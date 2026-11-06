"""Plan subscriptions through Lemon Squeezy: the checkout request, webhook verification, and the rules that decide a
workspace's plan (only a verified event for a checkout created here, priced by the variant actually paid for)."""
import io, json

import pytest

from app import billing, limits, main
from app.payment_settings import billing_mode, billing_problem
from helpers import TestDB, make_user, login, owner

SECRET = 'ls_whsec_' + 'k' * 24
VARIANTS = {'LEMONSQUEEZY_VARIANT_PRO_MONTHLY': '1001', 'LEMONSQUEEZY_VARIANT_PRO_ANNUAL': '1002',
            'LEMONSQUEEZY_VARIANT_AGENCY_MONTHLY': '2001', 'LEMONSQUEEZY_VARIANT_AGENCY_ANNUAL': '2002'}


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture()
def ls(client, monkeypatch):
    for k, v in {'LEMONSQUEEZY_API_KEY': 'test-api-key', 'LEMONSQUEEZY_STORE_ID': '77', 'LEMONSQUEEZY_WEBHOOK_SECRET': SECRET, **VARIANTS}.items():
        monkeypatch.setenv(k, v)
    sent = []

    def fake_call(method, path, body=None, opener=None):
        sent.append((method, path, body))
        if path == '/checkouts':
            return {'data': {'id': 'chk-uuid-1', 'attributes': {'url': 'https://visuara.lemonsqueezy.com/checkout/custom/chk-uuid-1?signature=x'}}}
        if method == 'PATCH':
            return {'data': {'id': path.rsplit('/', 1)[1], 'attributes': _sub(body['data']['attributes']['variant_id'], updated='2026-11-06T12:00:00.000000Z')}}
        return {'data': {'attributes': {'urls': {'customer_portal': 'https://visuara.lemonsqueezy.com/billing?signature=y'}}}}
    monkeypatch.setattr(billing, '_call', fake_call)
    return sent


def _sub(variant, status='active', updated='2026-11-06T10:00:00.000000Z', store=77, test_mode=True):
    return {'store_id': store, 'customer_id': 5, 'variant_id': int(variant), 'status': status, 'renews_at': '2026-12-06T10:00:00.000000Z',
            'ends_at': None, 'test_mode': test_mode, 'card_brand': 'visa', 'card_last_four': '4242', 'updated_at': updated}


def _deliver(c, event, sub_id, attrs, custom=None, secret=SECRET):
    body = json.dumps({'meta': {'event_name': event, **({'custom_data': custom} if custom else {})},
                       'data': {'type': 'subscriptions', 'id': str(sub_id), 'attributes': attrs}}).encode()
    return c.post('/api/billing/webhooks/lemonsqueezy', content=body, headers={'X-Signature': billing.sign(body, secret), 'Content-Type': 'application/json'})


def _plan(org_id): return TestDB().execute('SELECT plan FROM organizations WHERE id=?', (org_id,)).fetchone()['plan']


def _checkout(c, plan='pro', interval='monthly'):
    r = c.post('/api/billing/checkout', json={'plan': plan, 'interval': interval})
    assert r.status_code == 200, r.text
    return TestDB().execute('SELECT * FROM billing_checkouts ORDER BY id DESC').fetchone()


# ---------------------------------------------------------------- pure parts
def test_signatures_are_hex_hmac_of_the_raw_body_and_refuse_tampering():
    body = b'{"meta":{"event_name":"subscription_created"}}'
    sig = billing.sign(body, SECRET)
    assert len(sig) == 64 and billing.verify(sig, body, SECRET) and billing.verify(sig.upper(), body, SECRET)
    assert not billing.verify(sig, body + b' ', SECRET) and not billing.verify(sig, body, SECRET + 'x')
    assert not billing.verify('', body, SECRET) and not billing.verify(sig, body, '')


def test_the_billing_settings_are_checked_and_live_money_needs_the_owner():
    ok = {'LEMONSQUEEZY_API_KEY': 'k', 'LEMONSQUEEZY_STORE_ID': '77', 'LEMONSQUEEZY_WEBHOOK_SECRET': SECRET, 'LEMONSQUEEZY_VARIANT_PRO_MONTHLY': '1001'}
    assert billing_problem({}) == ''                                                       # not configured: nothing to check
    assert billing_problem(ok) == ''
    assert 'STORE_ID' in billing_problem({**ok, 'LEMONSQUEEZY_STORE_ID': 'my-store'})
    assert 'WEBHOOK_SECRET' in billing_problem({**ok, 'LEMONSQUEEZY_WEBHOOK_SECRET': 'short'})
    assert 'numeric variant' in billing_problem({**ok, 'LEMONSQUEEZY_VARIANT_PRO_MONTHLY': 'pro'})
    assert 'at least one plan variant' in billing_problem({k: v for k, v in ok.items() if 'VARIANT' not in k})
    assert billing_mode({}) == 'test' and billing_mode({'CC_PAYMENT_LIVE': '1'}) == 'live'
    from app.config import production_problems
    assert any('CC_LEMONSQUEEZY_API_URL' in p for p in production_problems({'CC_LEMONSQUEEZY_API_URL': 'http://127.0.0.1:9'}))
    assert not any('LEMONSQUEEZY' in p for p in production_problems(ok))     # a billing setting never stops the API starting


# ---------------------------------------------------------------- the flow
def test_without_lemon_squeezy_the_page_says_so_and_checkout_is_refused(client):
    u = owner(client)
    b = client.get('/api/billing').json()
    assert b['provider'] is None and b['ready'] is False and b['plan'] == 'team' and b['can_manage'] is True
    r = client.post('/api/billing/checkout', json={'plan': 'pro', 'interval': 'monthly'})
    assert r.status_code == 409 and r.json()['detail']['code'] == 'BILLING_NOT_CONFIGURED'
    assert _plan(u['org_id']) == 'team'


def test_a_checkout_is_created_for_the_variant_of_the_chosen_plan_in_test_mode(client, ls):
    u = owner(client, plan='free')
    b = client.get('/api/billing').json()
    assert b['ready'] and b['mode'] == 'test' and {p['id']: p['available'] for p in b['plans']} == {'pro': {'monthly': True, 'annual': True}, 'team': {'monthly': True, 'annual': True}}
    r = client.post('/api/billing/checkout', json={'plan': 'team', 'interval': 'annual'})
    assert r.status_code == 200 and r.json()['checkout_url'].startswith('https://visuara.lemonsqueezy.com/checkout/')
    method, path, body = ls[-1]
    d = body['data']
    assert (method, path, d['type']) == ('POST', '/checkouts', 'checkouts')
    assert d['relationships']['variant']['data'] == {'type': 'variants', 'id': '2002'} and d['relationships']['store']['data'] == {'type': 'stores', 'id': '77'}
    assert d['attributes']['test_mode'] is True and d['attributes']['checkout_data']['email'] == 'owner@acme.io'
    ck = TestDB().execute('SELECT * FROM billing_checkouts').fetchone()
    assert d['attributes']['checkout_data']['custom'] == {'org_id': str(u['org_id']), 'checkout': ck['token']}
    assert (ck['plan'], ck['billing_interval'], ck['variant_id'], ck['provider_checkout_id']) == ('team', 'annual', '2002', 'chk-uuid-1')
    assert '/dashboard/settings?billing=success' in d['attributes']['product_options']['redirect_url']
    assert _plan(u['org_id']) == 'free'                                                     # nothing changes until Lemon Squeezy confirms


def test_a_verified_subscription_event_sets_the_plan_once_and_only_from_the_paid_variant(client, ls):
    u = owner(client, plan='free'); ck = _checkout(client)
    # custom data says Agency, the buyer could edit it: the plan comes from the variant that was paid for (Pro)
    r = _deliver(client, 'subscription_created', 9001, _sub(1001), {'org_id': str(u['org_id']), 'checkout': ck['token'], 'plan': 'team'})
    assert r.status_code == 200 and r.json()['result'] == 'APPLIED' and _plan(u['org_id']) == 'pro'
    s = client.get('/api/billing').json()['subscription']
    assert (s['plan'], s['billing_interval'], s['status'], s['card_last_four']) == ('pro', 'monthly', 'active', '4242')
    # the same delivery again (Lemon Squeezy retries): applied once
    again = _deliver(client, 'subscription_created', 9001, _sub(1001), {'org_id': str(u['org_id']), 'checkout': ck['token'], 'plan': 'team'})
    assert again.json()['result'] == 'APPLIED'
    assert TestDB().execute("SELECT COUNT(*) AS n FROM payment_events WHERE provider='lemonsqueezy'").fetchone()['n'] == 1
    assert TestDB().execute('SELECT COUNT(*) AS n FROM subscriptions').fetchone()['n'] == 1
    assert limits.PLANS[_plan(u['org_id'])]['renders_per_month'] == 40


def test_forged_unknown_and_foreign_events_change_nothing(client, ls):
    u = owner(client, plan='free'); ck = _checkout(client)
    custom = {'org_id': str(u['org_id']), 'checkout': ck['token']}
    assert _deliver(client, 'subscription_created', 1, _sub(1001), custom, secret='wrong' * 5).status_code == 400
    assert _deliver(client, 'subscription_created', 2, _sub(1001), {'org_id': str(u['org_id']), 'checkout': 'made-up'}).json()['result'] == 'UNKNOWN_CHECKOUT'
    assert _deliver(client, 'subscription_created', 3, _sub(1001)).json()['result'] == 'UNKNOWN_CHECKOUT'                 # no custom data
    assert _deliver(client, 'subscription_created', 4, _sub(5555), custom).json()['result'] == 'MISMATCH'                 # not a plan variant
    assert _deliver(client, 'subscription_created', 5, _sub(1001, store=78), custom).json()['result'] == 'IGNORED'      # another store
    other = make_user('b@other.io', org='Other', plan='free')
    assert _deliver(client, 'subscription_created', 6, _sub(1001), {'org_id': str(other['org_id']), 'checkout': ck['token']}).json()['result'] == 'MISMATCH'
    assert _plan(u['org_id']) == 'free' and _plan(other['org_id']) == 'free'
    assert TestDB().execute('SELECT COUNT(*) AS n FROM subscriptions').fetchone()['n'] == 0


def test_cancelled_keeps_the_plan_until_it_expires_and_old_events_are_not_replayed(client, ls):
    u = owner(client, plan='free'); ck = _checkout(client)
    custom = {'org_id': str(u['org_id']), 'checkout': ck['token']}
    _deliver(client, 'subscription_created', 42, _sub(1001, updated='2026-11-06T10:00:00Z'), custom)
    assert _deliver(client, 'subscription_cancelled', 42, _sub(1001, 'cancelled', updated='2026-11-07T10:00:00Z')).json()['result'] == 'APPLIED'
    assert _plan(u['org_id']) == 'pro'                                                   # paid until the end of the period
    assert _deliver(client, 'subscription_updated', 42, _sub(1001, 'active', updated='2026-11-06T11:00:00Z')).json()['result'] == 'STALE'
    assert _deliver(client, 'subscription_expired', 42, _sub(1001, 'expired', updated='2026-12-06T10:00:00Z')).json()['result'] == 'APPLIED'
    assert _plan(u['org_id']) == 'free'
    assert client.get('/api/billing').json()['subscription']['status'] == 'expired'


def test_unpaid_downgrades_and_a_recovered_payment_restores(client, ls):
    u = owner(client, plan='free'); ck = _checkout(client, 'team', 'monthly')
    _deliver(client, 'subscription_created', 7, _sub(2001, updated='2026-11-06T10:00:00Z'), {'org_id': str(u['org_id']), 'checkout': ck['token']})
    assert _plan(u['org_id']) == 'team'
    _deliver(client, 'subscription_updated', 7, _sub(2001, 'past_due', updated='2026-11-07T10:00:00Z')); assert _plan(u['org_id']) == 'team'
    _deliver(client, 'subscription_updated', 7, _sub(2001, 'unpaid', updated='2026-11-10T10:00:00Z')); assert _plan(u['org_id']) == 'free'
    _deliver(client, 'subscription_updated', 7, _sub(2001, 'active', updated='2026-11-11T10:00:00Z')); assert _plan(u['org_id']) == 'team'


def test_live_mode_ignores_test_mode_events(client, ls, monkeypatch):
    monkeypatch.setenv('CC_PAYMENT_LIVE', '1')
    u = owner(client, plan='free'); ck = _checkout(client)
    assert ls[-1][2]['data']['attributes']['test_mode'] is False
    custom = {'org_id': str(u['org_id']), 'checkout': ck['token']}
    assert _deliver(client, 'subscription_created', 11, _sub(1001, test_mode=True), custom).json()['result'] == 'IGNORED'
    assert _plan(u['org_id']) == 'free'
    assert _deliver(client, 'subscription_created', 12, _sub(1001, test_mode=False), custom).json()['result'] == 'APPLIED'
    assert _plan(u['org_id']) == 'pro'


def test_only_the_owner_or_an_admin_changes_the_plan(client, ls):
    u = owner(client, plan='free')
    for role in ('viewer', 'editor', 'manager'):
        make_user(f'{role}@acme.io', org_id=u['org_id'], member_role=role); login(client, f'{role}@acme.io')
        assert client.get('/api/billing').json()['can_manage'] is False
        assert client.post('/api/billing/checkout', json={'plan': 'pro', 'interval': 'monthly'}).status_code == 403
    make_user('admin@acme.io', org_id=u['org_id'], member_role='admin'); login(client, 'admin@acme.io')
    assert client.post('/api/billing/checkout', json={'plan': 'pro', 'interval': 'monthly'}).status_code == 200
    assert client.post('/api/billing/checkout', json={'plan': 'enterprise', 'interval': 'monthly'}).status_code == 422


def test_changing_plan_moves_the_subscription_and_the_portal_link_is_fresh(client, ls, monkeypatch):
    u = owner(client, plan='free'); ck = _checkout(client)
    _deliver(client, 'subscription_created', 300, _sub(1001), {'org_id': str(u['org_id']), 'checkout': ck['token']})
    r = client.post('/api/billing/checkout', json={'plan': 'team', 'interval': 'monthly'})
    assert r.status_code == 409 and r.json()['detail']['code'] == 'ALREADY_SUBSCRIBED'
    r = client.post('/api/billing/change', json={'plan': 'team', 'interval': 'annual'})
    assert r.status_code == 200, r.text
    assert ls[-1][:2] == ('PATCH', '/subscriptions/300') and ls[-1][2]['data']['attributes'] == {'variant_id': 2002}
    assert _plan(u['org_id']) == 'team'
    assert client.post('/api/billing/portal').json()['portal_url'].startswith('https://visuara.lemonsqueezy.com/billing')
    monkeypatch.delenv('LEMONSQUEEZY_VARIANT_PRO_ANNUAL')
    r = client.post('/api/billing/change', json={'plan': 'pro', 'interval': 'annual'})
    assert r.status_code == 409 and r.json()['detail']['code'] == 'PLAN_NOT_FOR_SALE'


def test_the_lemon_squeezy_request_has_the_documented_headers(monkeypatch):
    monkeypatch.setenv('LEMONSQUEEZY_API_KEY', 'abc')
    seen = {}

    def opener(req, timeout):
        seen.update(url=req.full_url, method=req.get_method(), headers={k.lower(): v for k, v in req.header_items()}, body=json.loads(req.data))
        return _Resp(b'{"data":{"id":"1","attributes":{"url":"https://x.lemonsqueezy.com/checkout/custom/1"}}}')
    ref, url = billing.create_checkout('1001', 'a@b.io', 'A', 3, 'tok', 'https://app/x', opener=opener)
    assert (ref, url) == ('1', 'https://x.lemonsqueezy.com/checkout/custom/1')
    assert seen['url'] == 'https://api.lemonsqueezy.com/v1/checkouts' and seen['method'] == 'POST'
    assert seen['headers']['accept'] == 'application/vnd.api+json' and seen['headers']['content-type'] == 'application/vnd.api+json'
    assert seen['headers']['authorization'] == 'Bearer abc'
    assert seen['body']['data']['attributes']['product_options']['enabled_variants'] == [1001]
    assert main.app.url_path_for('lemonsqueezy_webhook') == '/api/billing/webhooks/lemonsqueezy'
