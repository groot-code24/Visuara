"""M17 programmatic foundation: contracts, the four integration states, test adapters that are never reported as
external, contract verification, and the production refusal of test adapters."""
import pytest

from app import main, programmatic as p
from helpers import login, make_user


def test_contracts_accept_valid_shapes_and_name_every_violation():
    item = p._sample_feed_item()
    assert p.validate(p.FEED_SCHEMA, item) == []
    broken = p._sample_feed_item(); broken['imp'][0]['ext']['visuara']['brand_safety'] = 'MAYBE'; del broken['video']['w']; broken['extra'] = 1
    errs = p.validate(p.FEED_SCHEMA, broken)
    assert any('brand_safety' in e for e in errs) and any('video.w: missing' in e for e in errs) and any('unexpected fields' in e for e in errs)
    deal = {'deal_id': 'd', 'inventory_id': '1', 'buyer': {'id': 'b', 'name': 'B'}, 'price_minor': 100, 'currency': 'EUR', 'expires_at': 1}
    assert p.validate('visuara-deal/1', deal) == []
    assert p.validate('visuara-deal/1', dict(deal, price_minor=1.5)) and p.validate('visuara-deal/1', dict(deal, price_minor=True))   # money is an integer
    assert p.validate(p.FEED_SCHEMA, dict(item, imp=[])) == ['$.imp: needs at least 1 item(s)']


def _caps(c):
    return {x['name']: x for x in c.get('/api/programmatic/capabilities').json()['capabilities']}


def test_nothing_is_reported_connected_without_an_adapter(manual):
    make_user('p@p.io', org='P'); login(manual, 'p@p.io')
    caps = _caps(manual)
    assert caps['inventory_feed']['status'] == 'IMPLEMENTED' and caps['real_time_bidding']['status'] == 'DESIGNED_ONLY'
    assert {caps[k]['status'] for k in ('supply_side_platform', 'demand_side_platform', 'third_party_measurement')} == {'NOT_CONNECTED'}
    body = manual.get('/api/programmatic/capabilities').json()
    assert body['external_connections'] == 0 and body['states'] == list(p.STATES)
    assert set(manual.get('/api/programmatic/contracts').json()['contracts']) == set(p.CONTRACTS)
    with pytest.raises(NotImplementedError, match='DESIGNED_ONLY'): p.NotConnected().bid({})


def test_test_adapters_connect_and_verify_but_are_never_external(manual, monkeypatch):
    monkeypatch.setenv('CC_PROGRAMMATIC_TEST_ADAPTERS', '1'); p._TEST.clear()
    make_user('ops@visuara.si', org='Visuara Ops')
    with main.db() as c: c.execute("UPDATE users SET platform_admin=1 WHERE email='ops@visuara.si'")
    login(manual, 'ops@visuara.si')
    caps = _caps(manual)
    assert caps['supply_side_platform']['status'] == 'CONNECTED' and caps['supply_side_platform']['external'] is False
    assert caps['real_time_bidding']['status'] == 'DESIGNED_ONLY'                                  # no adapter can make it more
    res = manual.post('/api/admin/programmatic/verify').json()
    assert all(r['passed'] and r['external'] is False for r in res['results']) and len(res['results']) == 3
    caps = _caps(manual)
    assert {caps[k]['status'] for k in ('supply_side_platform', 'demand_side_platform', 'third_party_measurement')} == {'VERIFIED'}
    assert manual.get('/api/programmatic/capabilities').json()['external_connections'] == 0
    # an adapter that breaks its contract fails verification and stays CONNECTED
    monkeypatch.setattr(p._TEST['demand'], 'fetch_deals', lambda: [{'deal_id': 'x', 'price_minor': 'ten'}])
    res = {r['kind']: r for r in manual.post('/api/admin/programmatic/verify').json()['results']}
    assert res['demand']['passed'] is False and any('price_minor' in e for e in res['demand']['errors'])
    assert _caps(manual)['demand_side_platform']['status'] == 'CONNECTED'
    make_user('x@x.io', org='X'); login(manual, 'x@x.io')
    assert manual.post('/api/admin/programmatic/verify').status_code == 403                       # administrators only


def test_production_refuses_test_adapters(monkeypatch):
    from app.config import production_problems
    assert any('CC_PROGRAMMATIC_TEST_ADAPTERS' in x for x in production_problems({'CC_PROGRAMMATIC_TEST_ADAPTERS': '1'}))
    monkeypatch.setenv('CC_PROGRAMMATIC_TEST_ADAPTERS', '1'); monkeypatch.setenv('CC_ENV', 'production'); p._TEST.clear()
    assert p.adapters() == {}                                                                       # and never load them there
