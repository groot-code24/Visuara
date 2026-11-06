"""M15 payments: the provider interface in test mode, webhook verification and idempotency, and the financial invariants
that must hold whatever the provider sends."""
import io, json, time, urllib.parse

import pytest

from app import commercial, main, payments
from helpers import TestDB, login, needs_media
from test_marketplace import _list, market  # noqa: F401  (pytest fixture)

SECRET = 'whsec_test_' + 'x' * 24


# ---------------------------------------------------------------- pure parts
def test_signatures_follow_the_stripe_scheme_and_refuse_tampering():
    body = b'{"id":"evt_1"}'
    h = payments.sign(body, SECRET, t=1_700_000_000)
    assert h.startswith('t=1700000000,v1=') and payments.verify(h, body, SECRET, now=1_700_000_100)
    assert not payments.verify(h, body + b' ', SECRET, now=1_700_000_100)                  # body changed
    assert not payments.verify(h, body, SECRET + 'y', now=1_700_000_100)                   # wrong secret
    assert not payments.verify(h, body, SECRET, now=1_700_000_000 + 301)                   # too old: a replay
    assert not payments.verify('', body, SECRET) and not payments.verify('t=x,v1=00', body, SECRET)
    assert payments.verify(f'{h},v1=deadbeef', body, SECRET, now=1_700_000_000)            # several v1 values, one good


def test_the_payment_configuration_is_checked_and_live_money_needs_the_owner():
    ok = {'CC_PAYMENT_WEBHOOK_SECRET': SECRET}
    assert payments.config_problem({}) == ''
    assert 'must be one of' in payments.config_problem({'CC_PAYMENT_PROVIDER': 'paypal', **ok})
    assert 'WEBHOOK_SECRET' in payments.config_problem({'CC_PAYMENT_PROVIDER': 'sandbox'})
    test_key = 'sk_' + 'test_' + 'a' * 20
    live_key = 'sk_' + 'live_' + 'a' * 20
    assert payments.config_problem({'CC_PAYMENT_PROVIDER': 'stripe', 'CC_STRIPE_SECRET_KEY': test_key, **ok}) == ''
    assert 'CC_PAYMENT_LIVE=1' in payments.config_problem({'CC_PAYMENT_PROVIDER': 'stripe', 'CC_STRIPE_SECRET_KEY': live_key, **ok})
    assert payments.config_problem({'CC_PAYMENT_PROVIDER': 'stripe', 'CC_STRIPE_SECRET_KEY': live_key, 'CC_PAYMENT_LIVE': '1', **ok}) == ''
    assert payments.mode({'CC_PAYMENT_PROVIDER': 'stripe', 'CC_STRIPE_SECRET_KEY': test_key}) == 'test'
    from app.config import production_problems
    base = {'CC_PAYMENT_PROVIDER': 'sandbox', **ok}
    assert any('simulator' in p for p in production_problems(base))                        # never the simulator in production
    assert not any('PAYMENT' in p or 'Stripe' in p for p in production_problems({'CC_PAYMENT_PROVIDER': 'stripe', 'CC_STRIPE_SECRET_KEY': test_key, **ok}))


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_the_stripe_adapter_sends_a_checkout_session_and_reads_stripe_events(monkeypatch):
    monkeypatch.setenv('CC_STRIPE_SECRET_KEY', 'sk_' + 'test_' + 'b' * 20)
    sent = {}
    def opener(req, timeout):
        sent.update(url=req.full_url, form=dict(urllib.parse.parse_qsl(req.data.decode())), headers={k.lower(): v for k, v in req.header_items()})
        return _Resp(json.dumps({'id': 'cs_test_123', 'url': 'https://checkout.stripe.com/c/pay/cs_test_123'}).encode())
    ref, url = payments.Stripe(opener).create_checkout(7, {'id': 3, 'number': 'VIS-2026-000003', 'amount_minor': 50000, 'currency': 'EUR'}, 'https://app.example')
    assert (ref, url) == ('cs_test_123', 'https://checkout.stripe.com/c/pay/cs_test_123')
    f = sent['form']
    assert sent['url'] == 'https://api.stripe.com/v1/checkout/sessions' and f['mode'] == 'payment'
    assert f['line_items[0][price_data][unit_amount]'] == '50000' and f['line_items[0][price_data][currency]'] == 'eur'
    assert f['metadata[invoice_id]'] == '3' and f['metadata[attempt_id]'] == '7' and f['success_url'].startswith('https://app.example/dashboard/finance')
    assert sent['headers']['idempotency-key'] == 'visuara-attempt-7' and sent['headers']['authorization'].startswith('Bearer sk_test_')
    completed = {'id': 'evt_9', 'type': 'checkout.session.completed', 'data': {'object': {'id': 'cs_test_123', 'payment_status': 'paid', 'amount_total': 50000,
                                                                                            'currency': 'eur', 'metadata': {'invoice_id': '3'}}}}
    assert payments.Stripe().parse(completed) == {'event_id': 'evt_9', 'outcome': 'SUCCEEDED', 'provider_ref': 'cs_test_123', 'amount_minor': 50000,
                                                   'currency': 'EUR', 'invoice_id': 3, 'type': 'checkout.session.completed'}
    unpaid = json.loads(json.dumps(completed)); unpaid['data']['object']['payment_status'] = 'unpaid'
    assert payments.Stripe().parse(unpaid)['outcome'] is None                              # a bank debit still pending: wait
    assert payments.Stripe().parse({'id': 'e', 'type': 'checkout.session.expired', 'data': {'object': {'id': 'cs_x'}}})['outcome'] == 'EXPIRED'


# ---------------------------------------------------------------- the flow, in test mode
def _invoice(market):
    """List the item, reserve it, request, approve and accept the quote. Returns the ISSUED invoice."""
    c, prod = market['c'], market['product']; iid = _list(market)
    login(c, 'brand@brand.io')
    camp = c.post('/api/campaigns', json={'name': 'Pay test', 'budget_minor': 100000, 'currency': 'EUR'}).json()
    c.put(f'/api/campaigns/{camp["id"]}/products', json={'product_ids': [prod]})
    c.put(f'/api/campaigns/{camp["id"]}/targeting', json={'categories': ['tech'], 'min_score': 0.0})
    c.post(f'/api/campaigns/{camp["id"]}/transition', json={'to': 'SUBMITTED'})
    res = c.post(f'/api/marketplace/inventory/{iid}/reserve', json={'campaign_id': camp['id']}).json()
    req = c.post(f'/api/campaigns/{camp["id"]}/placement-requests', json={'reservation_id': res['id'], 'product_id': prod}).json()
    login(c, 'pub@pub.io')
    q = c.post(f'/api/placement-requests/{req["id"]}/approve').json()['quote']['id']
    login(c, 'brand@brand.io')
    return c.post(f'/api/quotes/{q}/accept', headers={'Idempotency-Key': 'accept-1'}).json()


def _webhook(c, event, secret=SECRET, t=None):
    raw = json.dumps(event).encode()
    return c.post('/api/payments/webhooks/sandbox', content=raw, headers={'Visuara-Signature': payments.sign(raw, secret, t), 'Content-Type': 'application/json'})


def _txns(invoice_id):
    return TestDB().execute("SELECT COUNT(DISTINCT txn_id) FROM ledger_entries WHERE reference_type='invoice_payment' AND reference_id=?", (invoice_id,)).fetchone()[0]


@pytest.fixture()
def sandbox(monkeypatch):
    monkeypatch.setenv('CC_PAYMENT_PROVIDER', 'sandbox'); monkeypatch.setenv('CC_PAYMENT_WEBHOOK_SECRET', SECRET)


@needs_media
def test_a_test_mode_payment_settles_the_invoice_exactly_once(market, sandbox):
    c = market['c']; inv = _invoice(market)
    assert c.get('/api/payments/config').json() == {'provider': 'sandbox', 'mode': 'test', 'ready': True, 'problem': None, 'manual_payments': True}
    assert c.post(f'/api/invoices/{inv["invoice_id"]}/checkout').status_code == 428                 # money endpoints need an Idempotency-Key
    a1 = c.post(f'/api/invoices/{inv["invoice_id"]}/checkout', headers={'Idempotency-Key': 'pay-1'}).json()
    assert a1 == c.post(f'/api/invoices/{inv["invoice_id"]}/checkout', headers={'Idempotency-Key': 'pay-1'}).json()   # retried: one attempt
    assert a1['mode'] == 'test' and a1['checkout_url'].endswith(f'/dashboard/finance/pay/{a1["provider_ref"]}')
    assert c.get(f'/api/payments/sandbox/{a1["provider_ref"]}').json()['amount_minor'] == 50000
    assert c.post(f'/api/payments/sandbox/{a1["provider_ref"]}/complete', json={'outcome': 'succeeded'}).json()['result'] == 'APPLIED'
    paid = c.get(f'/api/invoices/{inv["invoice_id"]}').json()
    assert paid['status'] == 'PAID' and paid['payment_reference'] == a1['provider_ref'] and _txns(inv['invoice_id']) == 1
    assert [p['status'] for p in c.get(f'/api/invoices/{inv["invoice_id"]}/payments').json()] == ['SUCCEEDED']
    # the provider delivers the same event again: the first answer, and nothing moves
    ev = {'id': 'evt_repeat', 'type': 'payment.succeeded', 'data': {'ref': a1['provider_ref'], 'amount_minor': 50000, 'currency': 'EUR', 'invoice_id': inv['invoice_id']}}
    assert _webhook(c, ev).json()['result'] == 'ALREADY_PAID' and _webhook(c, ev).json()['result'] == 'ALREADY_PAID'
    assert _txns(inv['invoice_id']) == 1
    assert c.post(f'/api/invoices/{inv["invoice_id"]}/checkout', headers={'Idempotency-Key': 'pay-2'}).json()['detail']['code'] == 'INVALID_TRANSITION'
    # financial invariants
    with main.db() as con:
        tb = commercial.trial_balance(con)
        allocs = con.execute('SELECT SUM(amount_minor) FROM revenue_allocations WHERE invoice_id=?', (inv['invoice_id'],)).fetchone()[0]
        statuses = {r[0] for r in con.execute('SELECT status FROM revenue_allocations WHERE invoice_id=?', (inv['invoice_id'],)).fetchall()}
    assert tb['balanced'] and allocs == 50000 and statuses == {'PAYABLE'}
    receivable = next(a for a in tb['accounts'] if a['account'] == f'receivable:brand:{paid["brand_org_id"]}')
    assert receivable['balance'] == 0                                                            # issued then paid: nothing owed


@needs_media
def test_webhooks_refuse_forgery_staleness_mismatch_and_strangers(market, sandbox):
    c = market['c']; inv = _invoice(market); iid = inv['invoice_id']
    a = c.post(f'/api/invoices/{iid}/checkout', headers={'Idempotency-Key': 'pay-a'}).json()
    good = {'id': 'evt_ok', 'type': 'payment.succeeded', 'data': {'ref': a['provider_ref'], 'amount_minor': 50000, 'currency': 'EUR', 'invoice_id': iid}}
    assert _webhook(c, good, secret='whsec_wrong_' + 'z' * 20).status_code == 400                 # forged
    assert _webhook(c, good, t=int(time.time()) - 3600).status_code == 400                         # replayed an hour later
    cheap = dict(good, id='evt_cheap', data=dict(good['data'], amount_minor=100))
    assert _webhook(c, cheap).json()['result'] == 'MISMATCH'
    assert _webhook(c, dict(good, id='evt_other', data=dict(good['data'], ref='sbx_unknown'))).json()['result'] == 'UNKNOWN_CHECKOUT'
    assert _webhook(c, dict(good, id='evt_fail', type='payment.failed')).json()['result'] == 'FAILED'
    assert c.get(f'/api/invoices/{iid}').json()['status'] == 'ISSUED' and _txns(iid) == 0          # nothing of that moved money
    assert c.post('/api/payments/webhooks/stripe', content=b'{}', headers={'Stripe-Signature': 'x'}).status_code == 404   # not the configured provider
    # another brand can neither pay this invoice nor see its checkout
    login(c, 'brand2@rival.io')
    assert c.post(f'/api/invoices/{iid}/checkout', headers={'Idempotency-Key': 'pay-x'}).status_code == 404
    assert c.get(f'/api/payments/sandbox/{a["provider_ref"]}').status_code == 404
    assert c.post(f'/api/payments/sandbox/{a["provider_ref"]}/complete', json={'outcome': 'succeeded'}).status_code == 404
    # a publisher cannot pay; then the brand pays after the failure
    login(c, 'pub@pub.io')
    assert c.post(f'/api/invoices/{iid}/checkout', headers={'Idempotency-Key': 'pay-p'}).status_code == 403
    login(c, 'brand@brand.io')
    b = c.post(f'/api/invoices/{iid}/checkout', headers={'Idempotency-Key': 'pay-b'}).json()
    assert c.post(f'/api/payments/sandbox/{b["provider_ref"]}/complete', json={'outcome': 'succeeded'}).json()['result'] == 'APPLIED'
    # the first checkout's payment arrives late: recorded for a refund, the ledger unchanged
    assert _webhook(c, good).json()['result'] == 'ALREADY_PAID' and _txns(iid) == 1
    login(c, 'ops@visuara.si')
    results = {e['result'] for e in c.get('/api/admin/payments/events').json()}
    assert {'MISMATCH', 'UNKNOWN_CHECKOUT', 'FAILED', 'APPLIED', 'ALREADY_PAID'} <= results
    with main.db() as con: assert commercial.trial_balance(con)['balanced']


@needs_media
def test_without_a_provider_invoices_are_paid_by_bank_transfer(market):
    c = market['c']; inv = _invoice(market)
    assert c.get('/api/payments/config').json()['provider'] is None
    assert c.post(f'/api/invoices/{inv["invoice_id"]}/checkout', headers={'Idempotency-Key': 'p'}).json()['detail']['code'] == 'NO_PAYMENT_PROVIDER'
    login(c, 'ops@visuara.si')
    assert c.post(f'/api/admin/invoices/{inv["invoice_id"]}/mark-paid', json={'reference': 'BANK-123'}, headers={'Idempotency-Key': 'm'}).json()['status'] == 'PAID'
    assert _txns(inv['invoice_id']) == 1
