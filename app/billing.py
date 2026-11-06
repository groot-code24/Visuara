"""Plan subscriptions through Lemon Squeezy: a workspace buys Pro or Agency, monthly or annually, as on the pricing page.

The flow:
  1. An owner or admin asks for a checkout (`POST /api/billing/checkout {plan, interval}`). The API creates a Lemon Squeezy
     checkout for the variant configured for that plan and interval, with a one-time token in its custom data, and the
     browser goes to Lemon Squeezy's hosted checkout. Card details never reach Visuara.
  2. Lemon Squeezy calls `POST /api/billing/webhooks/lemonsqueezy`, signed with `X-Signature` (hex HMAC-SHA256 of the raw
     body with LEMONSQUEEZY_WEBHOOK_SECRET). Only a verified subscription event changes a workspace's plan, and
     the plan comes from the variant that was paid for, never from data the buyer could edit.
  3. Changing plan later (`POST /api/billing/change`) moves the existing subscription to another variant; cancelling,
     card changes and invoices are in Lemon Squeezy's customer portal (`POST /api/billing/portal`).

Settings (all on the API service):
  LEMONSQUEEZY_API_KEY, LEMONSQUEEZY_STORE_ID, LEMONSQUEEZY_WEBHOOK_SECRET
  LEMONSQUEEZY_VARIANT_PRO_MONTHLY, LEMONSQUEEZY_VARIANT_PRO_ANNUAL, LEMONSQUEEZY_VARIANT_AGENCY_MONTHLY, LEMONSQUEEZY_VARIANT_AGENCY_ANNUAL
  CC_PAYMENT_LIVE=1   the owner's authorisation for real money. Without it every checkout is created in test mode, and
                      with it test-mode events are ignored (a test card must never unlock a paid plan in production).

Webhook rules: a bad signature is answered 400 and changes nothing; each delivery is applied once (`payment_events`,
keyed by the body's SHA-256, since Lemon Squeezy sends no event id); an event older than the stored state of that
subscription is recorded as STALE and not applied; events for another store, an unknown variant or a checkout this
API did not create are recorded and not applied.
"""
import hashlib, hmac, json, os, secrets, urllib.error, urllib.request

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import main as m, limits
from .payment_settings import BILLING_VARIANTS, billing_mode, billing_problem

router = APIRouter()
PROVIDER = 'lemonsqueezy'
API = 'https://api.lemonsqueezy.com/v1'
# Statuses that keep the paid plan: `cancelled` runs until `ends_at` (then Lemon Squeezy sends subscription_expired);
# `past_due` is retried by Lemon Squeezy for a few days before it becomes `unpaid`.
ENTITLED = ('on_trial', 'active', 'past_due', 'cancelled')
PLAN_PRICES = {'pro': {'monthly': 39, 'annual': 348}, 'team': {'monthly': 159, 'annual': 1428}}   # USD, mirrors web/lib/pricing.ts


def _err(status, code, message, **extra): return HTTPException(status, {'code': code, 'message': message, **extra})


def enabled(): return bool(os.getenv('LEMONSQUEEZY_API_KEY'))


def variant_for(plan, interval):
    return (os.getenv(BILLING_VARIANTS.get((plan, interval), '')) or '').strip() if (plan, interval) in BILLING_VARIANTS else ''


def plan_for_variant(variant_id):
    v = str(variant_id or '')
    for (plan, interval), env in BILLING_VARIANTS.items():
        if v and (os.getenv(env) or '').strip() == v: return plan, interval
    return None, None


# ---------------------------------------------------------------- Lemon Squeezy API (no SDK)
def api_base():
    """Lemon Squeezy's API. CC_LEMONSQUEEZY_API_URL points it at a local stand-in for browser walks (refused in production)."""
    return (os.getenv('CC_LEMONSQUEEZY_API_URL') or API).rstrip('/')


def _provider_link(url):
    """Links from Lemon Squeezy are https; the local stand-in of a browser walk serves http on the loopback address."""
    return url.startswith('https://') or (bool(os.getenv('CC_LEMONSQUEEZY_API_URL')) and url.startswith('http://127.0.0.1:'))


def _call(method, path, body=None, opener=None):
    req = urllib.request.Request(api_base() + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers={
        'Accept': 'application/vnd.api+json', 'Content-Type': 'application/vnd.api+json',
        'Authorization': f'Bearer {os.getenv("LEMONSQUEEZY_API_KEY", "")}'})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=20) as r: return json.loads(r.read(1 << 20).decode())
    except urllib.error.HTTPError as e:
        raise _err(502, 'PAYMENT_PROVIDER_REFUSED', f'Lemon Squeezy refused the request (HTTP {e.code}). Check the Lemon Squeezy settings.')
    except (urllib.error.URLError, TimeoutError, OSError):
        raise _err(503, 'PAYMENT_PROVIDER_UNAVAILABLE', 'Lemon Squeezy could not be reached; try again shortly')
    except ValueError:
        raise _err(502, 'PAYMENT_PROVIDER_INVALID', 'Lemon Squeezy sent an unreadable answer')


def create_checkout(variant_id, email, name, org_id, token, redirect_url, opener=None):
    body = {'data': {'type': 'checkouts',
                     'attributes': {'checkout_data': {'email': email, 'name': name, 'custom': {'org_id': str(org_id), 'checkout': token}},
                                    'product_options': {'redirect_url': redirect_url, 'enabled_variants': [int(variant_id)]},
                                    'test_mode': billing_mode() != 'live'},
                     'relationships': {'store': {'data': {'type': 'stores', 'id': str(os.getenv('LEMONSQUEEZY_STORE_ID', '')).strip()}},
                                       'variant': {'data': {'type': 'variants', 'id': str(variant_id)}}}}}
    data = (_call('POST', '/checkouts', body, opener).get('data') or {})
    url = (data.get('attributes') or {}).get('url') or ''
    if not _provider_link(url): raise _err(502, 'PAYMENT_PROVIDER_INVALID', 'Lemon Squeezy did not return a checkout')
    return str(data.get('id') or ''), url


# ---------------------------------------------------------------- webhooks
def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(signature: str, body: bytes, secret: str) -> bool:
    return bool(signature and secret) and hmac.compare_digest(sign(body, secret), signature.strip().lower())


def _subscription_row(c, ref):
    return c.execute('SELECT * FROM subscriptions WHERE provider=? AND provider_ref=?', (PROVIDER, str(ref))).fetchone()


def apply_subscription(c, sub_id, attrs, custom=None, source='webhook'):
    """Store a subscription's state and set the workspace's plan from it. Returns (result, detail, org_id)."""
    custom = custom or {}
    store = str(os.getenv('LEMONSQUEEZY_STORE_ID', '')).strip()
    if store and str(attrs.get('store_id', '')) != store: return 'IGNORED', 'an event for another Lemon Squeezy store', None
    if billing_mode() == 'live' and attrs.get('test_mode'): return 'IGNORED', 'a test-mode event while live payments are on', None
    plan, interval = plan_for_variant(attrs.get('variant_id'))
    if not plan: return 'MISMATCH', f'variant {attrs.get("variant_id")} is not one of the configured plan variants', None
    existing = _subscription_row(c, sub_id)
    if existing:
        org_id = existing['org_id']
        if existing['provider_updated_at'] and str(attrs.get('updated_at') or '') < existing['provider_updated_at']:
            return 'STALE', 'an older state of this subscription than the one stored', org_id
    else:
        ck = c.execute('SELECT * FROM billing_checkouts WHERE token=?', (str(custom.get('checkout') or ''),)).fetchone() if custom.get('checkout') else None
        if not ck: return 'UNKNOWN_CHECKOUT', 'no checkout with this token was created here', None
        if str(custom.get('org_id') or ck['org_id']) != str(ck['org_id']): return 'MISMATCH', 'the checkout token belongs to another workspace', None
        org_id = ck['org_id']
    status = str(attrs.get('status') or '')
    fields = (str(attrs.get('customer_id') or ''), str(attrs.get('variant_id') or ''), plan, interval, status, attrs.get('renews_at'), attrs.get('ends_at'),
              1 if attrs.get('test_mode') else 0, attrs.get('card_brand'), attrs.get('card_last_four'), str(attrs.get('updated_at') or ''))
    if existing:
        c.execute('UPDATE subscriptions SET customer_ref=?, variant_id=?, plan=?, billing_interval=?, status=?, renews_at=?, ends_at=?, test_mode=?, card_brand=?, '
                  'card_last_four=?, provider_updated_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?', fields + (existing['id'],))
    else:
        c.execute('INSERT INTO subscriptions(customer_ref,variant_id,plan,billing_interval,status,renews_at,ends_at,test_mode,card_brand,card_last_four,'
                  'provider_updated_at,org_id,provider,provider_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', fields + (org_id, PROVIDER, str(sub_id)))
    before = limits.org_plan(c, org_id)
    if status in ENTITLED: after = plan
    elif before == plan or (existing and before == existing['plan']): after = 'free'      # only undo what a subscription gave
    else: after = before
    if after != before:
        c.execute('UPDATE organizations SET plan=? WHERE id=?', (after, org_id))
        from .inventory import notify
        notify(c, org_id, 'billing.plan_changed', f'Your workspace is now on the {limits.PLANS[after]["name"]} plan', f'Subscription {status} ({source})', 'organization', org_id)
    return 'APPLIED', f'subscription {status}: plan {before} -> {after}', org_id


def handle_webhook(signature, body: bytes):
    if not enabled(): return 404, {'detail': 'Billing is not configured'}
    if not verify(signature, body, os.getenv('LEMONSQUEEZY_WEBHOOK_SECRET') or ''): return 400, {'detail': 'Invalid signature'}
    try: payload = json.loads(body.decode())
    except ValueError: return 400, {'detail': 'The body is not JSON'}
    if not isinstance(payload, dict): return 400, {'detail': 'The body is not a JSON object'}
    meta = payload.get('meta') or {}; data = payload.get('data') or {}
    name = str(meta.get('event_name') or ''); key = hashlib.sha256(body).hexdigest()
    with m.db() as c:
        c.lock(f'billing-event-{key}')
        prev = c.execute('SELECT result FROM payment_events WHERE provider=? AND event_id=?', (PROVIDER, key)).fetchone()
        if prev: return 200, {'received': True, 'result': prev['result']}
        if data.get('type') == 'subscriptions' and name.startswith('subscription_'):
            c.lock(f'billing-subscription-{data.get("id")}')
            result, detail, _ = apply_subscription(c, data.get('id'), data.get('attributes') or {}, meta.get('custom_data') or {})
        else:
            result, detail = 'IGNORED', f'{name or "an event"} is recorded only; subscription events change plans'
        c.execute('INSERT INTO payment_events(provider,event_id,type,provider_ref,result,detail,payload_sha256) VALUES(?,?,?,?,?,?,?)',
                  (PROVIDER, key, name[:80], str(data.get('id') or ''), result, detail[:300], key))
    if result in ('MISMATCH', 'UNKNOWN_CHECKOUT'):
        from .obs import warn
        warn('billing event needs attention', provider=PROVIDER, result=result, event=name)
    return 200, {'received': True, 'result': result}


@router.post('/api/billing/webhooks/lemonsqueezy')
async def lemonsqueezy_webhook(request: Request):
    body = await request.body()
    if len(body) > 1 << 20: return JSONResponse({'detail': 'Body too large'}, status_code=413)
    status, out = handle_webhook(request.headers.get('x-signature') or '', body)
    return JSONResponse(out, status_code=status)


# ---------------------------------------------------------------- the workspace's billing
def _current(c, org_id):
    return c.execute("SELECT * FROM subscriptions WHERE org_id=? AND provider=? ORDER BY CASE WHEN status IN ('on_trial','active','past_due','cancelled') THEN 0 ELSE 1 END, id DESC",
                     (org_id, PROVIDER)).fetchone()


def _manager(u):
    if u.get('member_role') not in ('owner', 'admin'): raise _err(403, 'ROLE_FORBIDDEN', 'Only the workspace owner or an admin can change the plan')
    if u.get('delegated_from'): raise _err(403, 'ROLE_FORBIDDEN', 'An agency cannot change a client workspace\'s plan')


def _plans():
    return [{'id': p, 'name': limits.PLANS[p]['name'], 'monthly': PLAN_PRICES[p]['monthly'], 'annual': PLAN_PRICES[p]['annual'],
             'available': {i: bool(variant_for(p, i)) for i in ('monthly', 'annual')}} for p in PLAN_PRICES]


@router.get('/api/billing/plans')
def billing_plans():
    """Public: which plans can be bought online right now (the onboarding page asks before the workspace exists)."""
    ready = enabled() and not billing_problem()
    return {'ready': ready, 'mode': billing_mode() if enabled() else None, 'plans': _plans() if ready else []}


@router.get('/api/billing')
def billing(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        plan = limits.org_plan(c, u['org_id']); sub = _current(c, u['org_id'])
    problem = billing_problem() if enabled() else ''
    plans = _plans()
    return {'plan': plan, 'plan_name': limits.PLANS[plan]['name'], 'provider': PROVIDER if enabled() else None,
            'ready': enabled() and not problem, 'problem': problem or None, 'mode': billing_mode() if enabled() else None,
            'can_manage': u.get('member_role') in ('owner', 'admin') and not u.get('delegated_from'), 'plans': plans,
            'subscription': {k: sub[k] for k in ('plan', 'billing_interval', 'status', 'renews_at', 'ends_at', 'test_mode', 'card_brand', 'card_last_four')} if sub else None}


class PlanIn(BaseModel):
    plan: str
    interval: str = 'monthly'


def _ready():
    if not enabled(): raise _err(409, 'BILLING_NOT_CONFIGURED', 'Online payment is not set up yet. Contact us to upgrade.')
    problem = billing_problem()
    if problem: raise _err(409, 'BILLING_NOT_CONFIGURED', 'Online payment is not set up correctly yet. Contact us to upgrade.', problem=problem)


def _variant(body):
    if body.plan not in PLAN_PRICES or body.interval not in ('monthly', 'annual'): raise _err(422, 'INVALID_PLAN', 'Choose Pro or Agency, monthly or annual')
    v = variant_for(body.plan, body.interval)
    if not v: raise _err(409, 'PLAN_NOT_FOR_SALE', f'{limits.PLANS[body.plan]["name"]} ({body.interval}) cannot be bought online yet')
    return v


@router.post('/api/billing/checkout')
def checkout(body: PlanIn, request: Request):
    u = m.current_user(request); _manager(u); _ready(); v = _variant(body)
    from .auth import app_url
    with m.db() as c:
        sub = _current(c, u['org_id'])
        if sub and sub['status'] in ENTITLED and sub['status'] != 'cancelled':
            raise _err(409, 'ALREADY_SUBSCRIBED', 'This workspace already has a subscription: change its plan instead', subscription_plan=sub['plan'])
        token = secrets.token_urlsafe(24)
        org = c.execute('SELECT name FROM organizations WHERE id=?', (u['org_id'],)).fetchone()
        c.execute('INSERT INTO billing_checkouts(org_id,token,plan,billing_interval,variant_id,mode,created_by) VALUES(?,?,?,?,?,?,?)',
                  (u['org_id'], token, body.plan, body.interval, v, billing_mode(), u['id']))
    ref, url = create_checkout(v, u['email'], u.get('name') or (org['name'] if org else ''), u['org_id'], token,
                               f'{app_url()}/dashboard/settings?billing=success#plan')
    with m.db() as c:
        c.execute('UPDATE billing_checkouts SET provider_checkout_id=? WHERE token=?', (ref, token))
        m.event(c, u, 'billing.checkout_created', 'organization', u['org_id'], {'plan': body.plan, 'interval': body.interval, 'mode': billing_mode()})
    return {'checkout_url': url, 'mode': billing_mode()}


@router.post('/api/billing/change')
def change_plan(body: PlanIn, request: Request):
    """Move the existing subscription to another plan or interval. Lemon Squeezy prorates the difference."""
    u = m.current_user(request); _manager(u); _ready(); v = _variant(body)
    with m.db() as c: sub = _current(c, u['org_id'])
    if not sub or sub['status'] not in ENTITLED: raise _err(409, 'NO_SUBSCRIPTION', 'This workspace has no subscription to change: start a checkout')
    out = _call('PATCH', f'/subscriptions/{sub["provider_ref"]}', {'data': {'type': 'subscriptions', 'id': str(sub['provider_ref']), 'attributes': {'variant_id': int(v)}}})
    data = out.get('data') or {}
    with m.db() as c:
        result, detail, _ = apply_subscription(c, data.get('id') or sub['provider_ref'], data.get('attributes') or {}, source='plan change')
        m.event(c, u, 'billing.plan_change', 'organization', u['org_id'], {'plan': body.plan, 'interval': body.interval, 'result': result})
    if result != 'APPLIED': raise _err(502, 'PAYMENT_PROVIDER_INVALID', f'The plan change was not applied: {detail}')
    return {'changed': True, 'plan': body.plan, 'interval': body.interval}


@router.post('/api/billing/portal')
def portal(request: Request):
    """A fresh link to Lemon Squeezy's customer portal (cancel, change card, invoices). Links are signed for 24 hours."""
    u = m.current_user(request); _manager(u); _ready()
    with m.db() as c: sub = _current(c, u['org_id'])
    if not sub: raise _err(409, 'NO_SUBSCRIPTION', 'This workspace has no subscription yet')
    urls = ((_call('GET', f'/subscriptions/{sub["provider_ref"]}').get('data') or {}).get('attributes') or {}).get('urls') or {}
    url = urls.get('customer_portal') or ''
    if not _provider_link(url): raise _err(502, 'PAYMENT_PROVIDER_INVALID', 'Lemon Squeezy did not return a portal link')
    return {'portal_url': url}
