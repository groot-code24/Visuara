"""Payments through a provider (M15, specification Phase Seven): one interface, verified webhooks, the same ledger.

A brand pays an ISSUED invoice through the configured provider. The provider confirms by a signed webhook, and only the
webhook moves money in the ledger: the same balanced transaction as a payment recorded by an administrator
(`record_payment`). Manual payment by bank transfer stays available.

Providers (`CC_PAYMENT_PROVIDER`):
  sandbox  a built-in test-mode provider. Its "checkout" is a page in the web app where the payer chooses to pay or to
           decline; the outcome is delivered as a webhook signed exactly as Stripe signs its own. It never touches real
           money and is refused in production.
  stripe   Stripe Checkout through Stripe's REST API (no SDK). Test-mode keys (`sk_test_`) work everywhere; a live key
           (`sk_live_`) is refused unless the owner sets CC_PAYMENT_LIVE=1. Not verified against Stripe from this
           repository: no account exists (B-4). The request it sends and the webhook it accepts follow Stripe's
           documented formats and are tested against them.
  unset    no provider: invoices are paid by bank transfer and recorded by a platform administrator.

Webhook rules (`POST /api/payments/webhooks/{provider}`):
  - the signature (`t=<unix time>,v1=<hex HMAC-SHA256 of "t.body">`) must match CC_PAYMENT_WEBHOOK_SECRET and be at most
    `TOLERANCE_SECONDS` old; anything else is answered 400 and changes nothing;
  - each provider event is applied at most once (`payment_events`, unique per provider and event id); a repeat is
    answered 200 with the first result, so the provider stops retrying;
  - a payment is applied only when its amount, currency and invoice match the attempt the checkout was created for;
    a mismatch is recorded and not applied;
  - a second successful payment for an invoice that is already paid is recorded as ALREADY_PAID for a refund, and does
    not touch the ledger.
"""
import hashlib, hmac, json, os, secrets, time, urllib.error, urllib.parse, urllib.request
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import main as m, outbox
from .orgs import require_kind, BUY_SIDE
from .payment_settings import PROVIDERS, config_problem, mode  # noqa: F401  (settings checks import nothing from the app)

router = APIRouter()
TOLERANCE_SECONDS = 300


def _err(status, code, message, **extra): return HTTPException(status, {'code': code, 'message': message, **extra})


def provider_name():
    return (os.getenv('CC_PAYMENT_PROVIDER') or '').strip().lower() or None


def webhook_secret():
    return os.getenv('CC_PAYMENT_WEBHOOK_SECRET') or ''


# ---------------------------------------------------------------- signatures (Stripe's scheme, used by both providers)
def sign(body: bytes, secret: str, t: Optional[int] = None) -> str:
    t = int(time.time()) if t is None else int(t)
    mac = hmac.new(secret.encode(), f'{t}.'.encode() + body, hashlib.sha256).hexdigest()
    return f't={t},v1={mac}'


def verify(header: str, body: bytes, secret: str, now: Optional[float] = None, tolerance=TOLERANCE_SECONDS) -> bool:
    """True when one of the header's v1 signatures matches and its timestamp is fresh. Constant-time comparison."""
    if not header or not secret: return False
    parts = {}
    for item in header.split(','):
        k, _, v = item.strip().partition('=')
        parts.setdefault(k, []).append(v)
    try: t = int(parts.get('t', [''])[0])
    except ValueError: return False
    now = time.time() if now is None else now
    if abs(now - t) > tolerance: return False
    expected = hmac.new(secret.encode(), f'{t}.'.encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, v) for v in parts.get('v1', []))


# ---------------------------------------------------------------- providers
class Sandbox:
    name = 'sandbox'

    def create_checkout(self, attempt, invoice, app_url):
        ref = 'sbx_' + secrets.token_hex(12)
        return ref, f'/dashboard/finance/pay/{ref}'                     # a page of this web app, whatever its address

    def parse(self, payload):
        """Our own event format: {id, type: payment.succeeded|payment.failed, data: {ref, amount_minor, currency, invoice_id}}."""
        d = payload.get('data') or {}
        kind = {'payment.succeeded': 'SUCCEEDED', 'payment.failed': 'FAILED'}.get(payload.get('type'))
        return {'event_id': str(payload.get('id') or ''), 'outcome': kind, 'provider_ref': d.get('ref'), 'amount_minor': d.get('amount_minor'),
                'currency': (d.get('currency') or '').upper(), 'invoice_id': d.get('invoice_id'), 'type': payload.get('type') or ''}


class Stripe:
    name = 'stripe'
    API = 'https://api.stripe.com/v1/checkout/sessions'

    def __init__(self, opener=None):
        self.opener = opener or urllib.request.urlopen

    def create_checkout(self, attempt, invoice, app_url):
        form = {
            'mode': 'payment', 'client_reference_id': str(invoice['id']),
            'line_items[0][quantity]': '1',
            'line_items[0][price_data][currency]': invoice['currency'].lower(),
            'line_items[0][price_data][unit_amount]': str(invoice['amount_minor']),
            'line_items[0][price_data][product_data][name]': f'Visuara invoice {invoice["number"]}',
            'metadata[invoice_id]': str(invoice['id']), 'metadata[attempt_id]': str(attempt),
            'success_url': f'{app_url}/dashboard/finance?paid={invoice["id"]}', 'cancel_url': f'{app_url}/dashboard/finance?cancelled={invoice["id"]}',
        }
        req = urllib.request.Request(self.API, data=urllib.parse.urlencode(form).encode(), method='POST', headers={
            'Authorization': f'Bearer {os.getenv("CC_STRIPE_SECRET_KEY", "")}', 'Content-Type': 'application/x-www-form-urlencoded',
            'Idempotency-Key': f'visuara-attempt-{attempt}'})
        try:
            with self.opener(req, timeout=20) as r: data = json.loads(r.read(1 << 20).decode())
        except urllib.error.HTTPError as e:
            raise _err(502, 'PAYMENT_PROVIDER_REFUSED', f'Stripe refused the checkout (HTTP {e.code})')
        except (urllib.error.URLError, TimeoutError, OSError):
            raise _err(503, 'PAYMENT_PROVIDER_UNAVAILABLE', 'Stripe could not be reached; try again shortly')
        if not str(data.get('id', '')).startswith('cs_') or not data.get('url'): raise _err(502, 'PAYMENT_PROVIDER_INVALID', 'Stripe did not return a checkout session')
        return data['id'], data['url']

    def parse(self, payload):
        obj = (payload.get('data') or {}).get('object') or {}
        t = payload.get('type') or ''
        if t in ('checkout.session.completed', 'checkout.session.async_payment_succeeded'):
            outcome = 'SUCCEEDED' if obj.get('payment_status') == 'paid' else None      # 'unpaid': an async method, wait for it
        elif t in ('checkout.session.async_payment_failed', 'checkout.session.expired'):
            outcome = 'FAILED' if t.endswith('failed') else 'EXPIRED'
        else:
            outcome = None
        meta = obj.get('metadata') or {}
        return {'event_id': str(payload.get('id') or ''), 'outcome': outcome, 'provider_ref': obj.get('id'), 'amount_minor': obj.get('amount_total'),
                'currency': (obj.get('currency') or '').upper(), 'invoice_id': int(meta['invoice_id']) if str(meta.get('invoice_id', '')).isdigit() else None, 'type': t}


def provider(name=None, opener=None):
    name = name or provider_name()
    if name == 'sandbox': return Sandbox()
    if name == 'stripe': return Stripe(opener)
    return None


# ---------------------------------------------------------------- applying a payment
def record_payment(c, inv, reference, user_id, source):
    """Mark an ISSUED invoice PAID and post the payment to the ledger. Returns the transaction id, or None when the
    invoice was no longer ISSUED (nothing changes then). Used by manual payments and by provider webhooks."""
    from .commercial import post_transaction
    from .inventory import notify
    if c.execute("UPDATE invoices SET status='PAID', paid_at=CURRENT_TIMESTAMP, payment_reference=? WHERE id=? AND status='ISSUED'", (reference, inv['id'])).rowcount != 1:
        return None
    txn = post_transaction(c, [('cash:platform', None, 'DEBIT', inv['amount_minor'], inv['currency']),
                               (f'receivable:brand:{inv["brand_org_id"]}', inv['brand_org_id'], 'CREDIT', inv['amount_minor'], inv['currency'])],
                           'invoice_payment', inv['id'], f'Payment {reference} for {inv["number"]} ({source})', user_id)
    c.execute("UPDATE revenue_allocations SET status='PAYABLE' WHERE invoice_id=? AND status='PENDING'", (inv['id'],))
    req = c.execute('SELECT placement_request_id FROM quotes WHERE id=?', (inv['quote_id'],)).fetchone()[0]
    outbox.emit(c, 'invoice.paid', 'invoice', inv['id'], {'invoice_id': inv['id'], 'amount_minor': inv['amount_minor'], 'currency': inv['currency'],
                                                         'placement_request_id': req, 'source': source}, org_id=inv['brand_org_id'])
    notify(c, inv['publisher_org_id'], 'invoice.paid', f'Invoice {inv["number"]} was paid; your share is payable', '', 'invoice', inv['id'])
    return txn


def apply_event(c, name, ev, payload_sha):
    """Apply a verified, parsed provider event once. Returns (result, detail)."""
    if not ev['event_id']: return 'IGNORED', 'no event id'
    prev = c.execute('SELECT result, detail FROM payment_events WHERE provider=? AND event_id=?', (name, ev['event_id'])).fetchone()
    if prev: return prev['result'], prev['detail']
    attempt = c.execute('SELECT * FROM payment_attempts WHERE provider=? AND provider_ref=?', (name, ev['provider_ref'])).fetchone() if ev['provider_ref'] else None
    result, detail = 'IGNORED', f'event type {ev["type"]} is not acted on'
    if ev['outcome'] and not attempt:
        result, detail = 'UNKNOWN_CHECKOUT', 'no checkout with this reference was created here'
    elif ev['outcome'] and attempt:
        inv = c.execute('SELECT * FROM invoices WHERE id=?', (attempt['invoice_id'],)).fetchone()
        if ev['outcome'] == 'SUCCEEDED':
            if (ev['amount_minor'], ev['currency']) != (attempt['amount_minor'], attempt['currency']) or (ev['invoice_id'] and ev['invoice_id'] != attempt['invoice_id']):
                result, detail = 'MISMATCH', f'paid {ev["amount_minor"]} {ev["currency"]} for invoice {ev["invoice_id"]}; expected {attempt["amount_minor"]} {attempt["currency"]} for {attempt["invoice_id"]}'
            else:
                c.execute("UPDATE payment_attempts SET status='SUCCEEDED', updated_at=CURRENT_TIMESTAMP WHERE id=?", (attempt['id'],))
                txn = record_payment(c, inv, ev['provider_ref'], None, name)
                result, detail = ('APPLIED', f'ledger transaction {txn}') if txn else ('ALREADY_PAID', 'the invoice was already paid: refund this payment')
        else:
            c.execute("UPDATE payment_attempts SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='CREATED'", (ev['outcome'], attempt['id']))
            result, detail = ev['outcome'], 'the payment did not complete'
    c.execute('INSERT INTO payment_events(provider,event_id,type,provider_ref,attempt_id,result,detail,payload_sha256) VALUES(?,?,?,?,?,?,?,?)',
              (name, ev['event_id'], ev['type'][:80], ev['provider_ref'], attempt['id'] if attempt else None, result, detail[:300], payload_sha))
    if result in ('MISMATCH', 'ALREADY_PAID', 'UNKNOWN_CHECKOUT'):
        from .obs import warn
        warn('payment event needs attention', provider=name, result=result, event=ev['event_id'])
    return result, detail


def handle_webhook(name, header, body: bytes):
    """Verify and apply one webhook. Returns (status_code, response)."""
    if name not in PROVIDERS or name != provider_name(): return 404, {'detail': 'No such payment provider'}
    if not verify(header, body, webhook_secret()): return 400, {'detail': 'Invalid or stale signature'}
    try: payload = json.loads(body.decode())
    except ValueError: return 400, {'detail': 'The body is not JSON'}
    ev = provider(name).parse(payload if isinstance(payload, dict) else {})
    with m.db() as c:
        c.lock(f'payment-event-{name}-{ev["event_id"]}')
        result, detail = apply_event(c, name, ev, hashlib.sha256(body).hexdigest())
    return 200, {'received': True, 'result': result}


# ---------------------------------------------------------------- API
@router.get('/api/payments/config')
def payments_config(request: Request):
    m.current_user(request)
    name = provider_name(); problem = config_problem()
    return {'provider': name, 'mode': mode() if name else None, 'ready': bool(name) and not problem, 'problem': problem or None,
            'manual_payments': True}


class CheckoutIn(BaseModel):
    pass


@router.post('/api/invoices/{iid}/checkout')
def create_checkout(iid: int, request: Request):
    u = m.current_user(request); require_kind(u, BUY_SIDE, 'Paying an invoice')
    from .auth import app_url
    from .commercial import _idempotent
    if u.get('member_role') not in ('owner', 'admin', 'manager'): raise _err(403, 'ROLE_FORBIDDEN', 'Paying invoices needs the manager role or higher')
    p = provider()
    if p is None or config_problem(): raise _err(409, 'NO_PAYMENT_PROVIDER', 'No payment provider is configured; pay by bank transfer', problem=config_problem() or None)

    def work(c):
        inv = c.execute('SELECT * FROM invoices WHERE id=? AND brand_org_id=?', (iid, u['org_id'])).fetchone()
        if not inv: raise HTTPException(404, 'Resource not found')
        if inv['status'] != 'ISSUED': raise _err(409, 'INVALID_TRANSITION', f'A {inv["status"]} invoice cannot be paid')
        aid = c.execute('INSERT INTO payment_attempts(invoice_id,org_id,provider,mode,amount_minor,currency,created_by) VALUES(?,?,?,?,?,?,?)',
                        (iid, u['org_id'], p.name, mode(), inv['amount_minor'], inv['currency'], u['id'])).lastrowid
        ref, url = p.create_checkout(aid, dict(inv), app_url())
        c.execute('UPDATE payment_attempts SET provider_ref=?, checkout_url=? WHERE id=?', (ref, url, aid))
        m.event(c, u, 'payment.checkout_created', 'invoice', iid, {'attempt_id': aid, 'provider': p.name, 'mode': mode()})
        return {'attempt_id': aid, 'provider': p.name, 'mode': mode(), 'provider_ref': ref, 'checkout_url': url}
    return _idempotent(request, u['org_id'], f'invoice-checkout:{iid}', {'invoice': iid}, work)


@router.get('/api/invoices/{iid}/payments')
def invoice_payments(iid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        inv = c.execute('SELECT id FROM invoices WHERE id=? AND (brand_org_id=? OR publisher_org_id=?)', (iid, u['org_id'], u['org_id'])).fetchone()
        if not inv: raise HTTPException(404, 'Resource not found')
        rows = c.execute('SELECT id, provider, mode, provider_ref, status, amount_minor, currency, created_at, updated_at FROM payment_attempts WHERE invoice_id=? ORDER BY id', (iid,)).fetchall()
    return [dict(r) for r in rows]


@router.post('/api/payments/webhooks/{name}')
async def payment_webhook(name: str, request: Request):
    from fastapi.responses import JSONResponse
    body = await request.body()
    if len(body) > 1 << 20: return JSONResponse({'detail': 'Body too large'}, status_code=413)
    header = request.headers.get('stripe-signature') if name == 'stripe' else request.headers.get('visuara-signature')
    status, out = handle_webhook(name, header or '', body)
    return JSONResponse(out, status_code=status)


class SandboxIn(BaseModel):
    outcome: str = 'succeeded'


@router.get('/api/payments/sandbox/{ref}')
def sandbox_session(ref: str, request: Request):
    u = m.current_user(request)
    if provider_name() != 'sandbox': raise HTTPException(404, 'Resource not found')
    with m.db() as c:
        a = c.execute("SELECT a.*, i.number FROM payment_attempts a JOIN invoices i ON i.id=a.invoice_id WHERE a.provider='sandbox' AND a.provider_ref=? AND a.org_id=?",
                      (ref, u['org_id'])).fetchone()
    if not a: raise HTTPException(404, 'Resource not found')
    return {'provider_ref': ref, 'invoice_id': a['invoice_id'], 'number': a['number'], 'amount_minor': a['amount_minor'], 'currency': a['currency'],
            'status': a['status'], 'mode': 'test'}


@router.post('/api/payments/sandbox/{ref}/complete')
def sandbox_complete(ref: str, body: SandboxIn, request: Request):
    """The sandbox's stand-in for the provider's checkout page: the payer chooses, and the sandbox delivers the outcome as
    a signed webhook through exactly the same verification and processing as a real provider's."""
    u = m.current_user(request)
    if provider_name() != 'sandbox': raise HTTPException(404, 'Resource not found')
    if body.outcome not in ('succeeded', 'failed'): raise _err(422, 'INVALID_OUTCOME', 'outcome is succeeded or failed')
    with m.db() as c:
        a = c.execute("SELECT * FROM payment_attempts WHERE provider='sandbox' AND provider_ref=? AND org_id=?", (ref, u['org_id'])).fetchone()
    if not a: raise HTTPException(404, 'Resource not found')
    event = {'id': 'evt_' + secrets.token_hex(10), 'type': f'payment.{body.outcome}',
             'data': {'ref': ref, 'amount_minor': a['amount_minor'], 'currency': a['currency'], 'invoice_id': a['invoice_id']}}
    raw = json.dumps(event, separators=(',', ':')).encode()
    status, out = handle_webhook('sandbox', sign(raw, webhook_secret()), raw)
    if status != 200: raise _err(500, 'SANDBOX_WEBHOOK_FAILED', str(out))
    return out


@router.get('/api/admin/payments/events')
def admin_payment_events(request: Request, result: Optional[str] = None):
    u = m.current_user(request)
    from .orgs import require_platform_admin
    require_platform_admin(u)
    with m.db() as c:
        q, args = 'SELECT * FROM payment_events', []
        if result: q += ' WHERE result=?'; args.append(result)
        return [dict(r) for r in c.execute(q + ' ORDER BY id DESC LIMIT 200', args).fetchall()]
