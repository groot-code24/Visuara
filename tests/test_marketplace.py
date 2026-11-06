"""Phases 6-12 end to end on real analysis and rendering: rights verification, listing, reservation races, campaigns,
two-party approval, production through the existing render engine, delivery evidence, invoicing, the ledger, payouts,
analytics from the outbox and the campaign report."""
import threading
from pathlib import Path

import pytest

from app import main, intelligence, outbox, commercial, db as dbm
from helpers import TestDB, owner, login, upload, add_product, needs_media
from benchmark import fixtures_scene as scene
from ops import platform_admin

PDF = b'%PDF-1.4\n% licence agreement between Pub Studio and the rights holder\n'


def _kind(c, kind): assert c.patch('/api/organizations/current', json={'kind': kind}).status_code == 200


def _render(c, pl):
    assert c.post(f'/api/placements/{pl}/render').status_code == 200
    jid, token = main.claim_next_job()
    main.render_job(jid, token)
    return jid


@pytest.fixture()
def market(manual, tmp_path):
    """A publisher with analysed, listed inventory; a brand with an active product; a platform administrator."""
    c = manual
    owner(c, 'ops@visuara.si', 'Visuara Ops'); platform_admin.set_flag('ops@visuara.si', True)
    owner(c, 'brand2@rival.io', 'Rival Brand'); _kind(c, 'brand')
    owner(c, 'brand@brand.io', 'Brand Co'); _kind(c, 'brand')
    prod = add_product(c, tmp_path, 'Sleeve', 'tech', scene.product(tmp_path / 's.png', 160, 100, (180, 80, 30)))
    owner(c, 'pub@pub.io', 'Pub Studio'); _kind(c, 'publisher')
    assert c.put('/api/profile', json={'display_name': 'Pub Studio', 'website': 'https://pub.example', 'categories': ['lifestyle']}).status_code == 200
    vid = upload(c, scene.generate('room', 1, tmp_path / 'clips')['path'], 'Living room', analyze=False)
    a = c.post(f'/api/videos/{vid}/analyses').json()
    while intelligence.run_next(): pass
    assert c.get(f'/api/analyses/{a["id"]}').json()['status'] == 'COMPLETED'
    items = c.get('/api/inventory').json()
    assert items and all(i['status'] == 'DRAFT' for i in items)
    item = max(items, key=lambda i: i['opportunity']['feasibility_score'])
    return {'c': c, 'vid': vid, 'item': item, 'product': prod, 'tmp': tmp_path}


@needs_media
def test_listing_needs_review_and_verified_rights(market):
    c, item, vid = market['c'], market['item'], market['vid']
    r = c.post(f'/api/inventory/{item["id"]}/list', json={'price_minor': 50000, 'currency': 'EUR'})
    assert r.status_code == 409 and r.json()['detail']['code'] == 'NOT_ELIGIBLE'
    assert any('brand safety' in x for x in r.json()['detail']['reasons']) and any('rights' in x for x in r.json()['detail']['reasons'])
    assert c.post(f'/api/opportunities/{item["opportunity_id"]}/review', json={'brand_safety': 'SAFE'}).json()['validation_status'] == 'VALID'
    c.post(f'/api/opportunities/{item["opportunity_id"]}/review', json={'brand_safety': 'SAFE'})                    # reviewed twice: said once
    expl = c.get(f'/api/opportunities/{item["opportunity_id"]}').json()['explanation']
    assert expl.endswith('Reviewed: SAFE by a person.') and expl.count('Reviewed:') == 1                      # debt 24
    bad =c.post(f'/api/videos/{vid}/rights', data={'holder': 'Pub Studio'}, files={'evidence': ('x.pdf', b'not a pdf', 'application/pdf')})
    assert bad.status_code == 415                                                         # checked from the contents
    rid = c.post(f'/api/videos/{vid}/rights', data={'holder': 'Pub Studio', 'expires_on': '2099-12-31'}, files={'evidence': ('licence.pdf', PDF, 'application/pdf')}).json()['id']
    assert c.post(f'/api/admin/rights/{rid}/decision', json={'decision': 'VERIFIED'}).json()['detail']['code'] == 'PLATFORM_ADMIN_REQUIRED'
    login(c, 'ops@visuara.si')
    assert [x['id'] for x in c.get('/api/admin/rights').json()] == [rid]
    assert c.get(f'/api/admin/rights/{rid}/evidence').content == PDF
    assert c.post(f'/api/admin/rights/{rid}/decision', json={'decision': 'VERIFIED', 'notes': 'contract checked'}).json()['status'] == 'VERIFIED'
    assert c.post(f'/api/admin/rights/{rid}/decision', json={'decision': 'REJECTED'}).status_code == 409
    login(c, 'pub@pub.io')
    assert c.post(f'/api/inventory/{item["id"]}/list', json={'price_minor': 50000, 'currency': 'XXX'}).status_code == 422
    assert c.post(f'/api/inventory/{item["id"]}/list', json={'price_minor': -5, 'currency': 'EUR'}).status_code == 422
    listed = c.post(f'/api/inventory/{item["id"]}/list', json={'price_minor': 50000, 'currency': 'eur'}).json()
    assert listed['status'] == 'AVAILABLE' and listed['currency'] == 'EUR'
    login(c, 'brand@brand.io')
    assert c.post(f'/api/inventory/{item["id"]}/list', json={'price_minor': 1, 'currency': 'EUR'}).status_code in (403, 404)   # a brand cannot list it


def _list(market):
    c, item, vid = market['c'], market['item'], market['vid']
    login(c, 'pub@pub.io')
    c.post(f'/api/opportunities/{item["opportunity_id"]}/review', json={'brand_safety': 'SAFE'})
    rid = c.post(f'/api/videos/{vid}/rights', data={'holder': 'Pub Studio'}, files={'evidence': ('licence.pdf', PDF, 'application/pdf')}).json()['id']
    login(c, 'ops@visuara.si'); c.post(f'/api/admin/rights/{rid}/decision', json={'decision': 'VERIFIED'})
    login(c, 'pub@pub.io')
    assert c.post(f'/api/inventory/{item["id"]}/list', json={'price_minor': 50000, 'currency': 'EUR'}).json()['status'] == 'AVAILABLE'
    return item['id']


@needs_media
def test_matching_uses_scene_context_and_leaves_out_what_it_must(market):
    import json
    c, prod = market['c'], market['product']; iid = _list(market)
    scene_id = market['item']['scene_id']

    def set_scene(meta):
        with main.db() as con:
            cur = json.loads(con.execute('SELECT metadata_json FROM scenes WHERE id=?', (scene_id,)).fetchone()['metadata_json'])
            con.execute('UPDATE scenes SET metadata_json=? WHERE id=?', (json.dumps(dict(cur, **meta)), scene_id))

    def recommend():
        r = c.post('/api/matching/recommendations', json={'product_id': prod}).json()
        return {x['inventory_id']: x for x in r['results']}, r['left_out']

    login(c, 'brand@brand.io')                                   # the product is a tech sleeve
    set_scene({'context': {'status': 'MEASURED', 'setting': 'workspace', 'evidence': {'laptop': .8}}, 'objects': {'laptop': {}}})
    suited, _ = recommend()
    assert suited[iid]['breakdown']['context_fit'] == 1.0 and any('workspace' in x for x in suited[iid]['reasons'])
    set_scene({'context': {'status': 'MEASURED', 'setting': 'kitchen', 'evidence': {'oven': .8}}, 'objects': {'oven': {}}})
    kitchen, _ = recommend()
    assert kitchen[iid]['breakdown']['context_fit'] == 0.0 and kitchen[iid]['score'] < suited[iid]['score']
    # the brand excludes knives: a scene where one was detected is left out, not ranked lower
    assert c.put('/api/profile', json={'display_name': 'Brand Co', 'website': 'https://brand.example', 'categories': ['tech'], 'excluded_terms': ['knife']}).status_code == 200
    set_scene({'objects': {'oven': {}, 'knife': {}}})
    found, left = recommend()
    assert iid not in found and left == {"matches the brand's excluded terms": 1}
    set_scene({'objects': {'oven': {}}})
    assert iid in recommend()[0]
    # rights revoked after listing: the item is no longer recommended
    with main.db() as con: con.execute("UPDATE rights_records SET status='REVOKED' WHERE video_id=?", (market['vid'],))
    found, left = recommend()
    assert iid not in found and left == {'no longer listable (licence, rights or review)': 1}


@needs_media
def test_inventory_whose_rights_lapse_cannot_be_bought(market):
    """Rights are checked when an item is listed, and again at every step of a sale: they can be revoked or expire later."""
    c, prod = market['c'], market['product']; iid = _list(market)

    def rights(status):
        with main.db() as con: con.execute('UPDATE rights_records SET status=? WHERE video_id=?', (status, market['vid']))

    login(c, 'brand@brand.io')
    camp = c.post('/api/campaigns', json={'name': 'Spring', 'budget_minor': 100000, 'currency': 'EUR'}).json()
    c.put(f'/api/campaigns/{camp["id"]}/products', json={'product_ids': [prod]})
    c.put(f'/api/campaigns/{camp["id"]}/targeting', json={'categories': ['tech'], 'min_score': 0.0})
    assert c.post(f'/api/campaigns/{camp["id"]}/transition', json={'to': 'SUBMITTED'}).json()['status'] == 'SUBMITTED'
    rights('REVOKED')
    assert iid not in [x['id'] for x in c.get('/api/marketplace/inventory').json()]
    r = c.post(f'/api/marketplace/inventory/{iid}/reserve', json={'campaign_id': camp['id']})
    assert r.status_code == 409 and r.json()['detail']['code'] == 'NOT_SELLABLE' and any('rights' in x for x in r.json()['detail']['reasons'])
    rights('VERIFIED')
    res = c.post(f'/api/marketplace/inventory/{iid}/reserve', json={'campaign_id': camp['id']}).json()
    req = c.post(f'/api/campaigns/{camp["id"]}/placement-requests', json={'reservation_id': res['id'], 'product_id': prod}).json()
    rights('EXPIRED')
    login(c, 'pub@pub.io')
    r = c.post(f'/api/placement-requests/{req["id"]}/approve')
    assert r.status_code == 409 and r.json()['detail']['code'] == 'NOT_SELLABLE'
    rights('VERIFIED')
    assert c.post(f'/api/placement-requests/{req["id"]}/approve').json()['status'] == 'APPROVED'
    rights('REVOKED')
    r = c.post(f'/api/placement-requests/{req["id"]}/start-production')
    assert r.status_code == 409 and r.json()['detail']['code'] == 'NOT_SELLABLE'


@needs_media
def test_only_one_brand_wins_a_reservation(market):
    c = market['c']; iid = _list(market)
    login(c, 'pub@pub.io')
    assert c.post(f'/api/marketplace/inventory/{iid}/reserve', json={}).json()['detail']['code'] == 'ORGANIZATION_KIND_FORBIDDEN'
    login(c, 'brand@brand.io')
    found = c.get('/api/marketplace/inventory?product_category=tech').json()
    assert [f['id'] for f in found] == [iid] and found[0]['publisher'] == 'Pub Studio'
    assert c.get(found[0]['preview_url']).headers['content-type'] == 'image/jpeg'
    # concurrent reservations from two brands: exactly one wins
    from helpers import CSRSTestClient
    other = CSRSTestClient(main.app); login(other, 'brand2@rival.io')
    results = []
    def go(client): results.append(client.post(f'/api/marketplace/inventory/{iid}/reserve', json={}))
    ts = [threading.Thread(target=go, args=(x,)) for x in (c, other)]
    [t.start() for t in ts]; [t.join() for t in ts]
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409] and next(r for r in results if r.status_code == 409).json()['detail']['code'] == 'INVENTORY_CONFLICT'
    assert TestDB().execute("SELECT COUNT(*) FROM reservations WHERE inventory_id=? AND status='HELD'", (iid,)).fetchone()[0] == 1
    winner = next(r for r in results if r.status_code == 200).json()
    # an expired hold frees the item for others
    with main.db() as con: con.execute('UPDATE reservations SET expires_at=0 WHERE id=?', (winner['id'],))
    login(other, 'brand2@rival.io')
    assert other.post(f'/api/marketplace/inventory/{iid}/reserve', json={}).status_code == 200
    assert TestDB().execute('SELECT status FROM reservations WHERE id=?', (winner['id'],)).fetchone()['status'] == 'EXPIRED'


@needs_media
def test_the_whole_marketplace_flow(market):
    c, prod = market['c'], market['product']; iid = _list(market)
    login(c, 'brand@brand.io')
    camp = c.post('/api/campaigns', json={'name': 'Autumn launch', 'budget_minor': 100000, 'currency': 'EUR'}).json()
    assert c.post(f'/api/campaigns/{camp["id"]}/transition', json={'to': 'SUBMITTED'}).json()['detail']['code'] == 'CAMPAIGN_INCOMPLETE'
    c.put(f'/api/campaigns/{camp["id"]}/products', json={'product_ids': [prod]})
    c.put(f'/api/campaigns/{camp["id"]}/targeting', json={'categories': ['tech'], 'min_score': 0.0})
    assert c.post(f'/api/campaigns/{camp["id"]}/transition', json={'to': 'SUBMITTED'}).json()['status'] == 'SUBMITTED'
    recs = c.get(f'/api/campaigns/{camp["id"]}/recommendations').json()['products'][0]['results']
    assert recs[0]['inventory_id'] == iid and recs[0]['breakdown']['version'] == 'match-v2' and recs[0]['reasons']
    run = c.post('/api/matching/recommendations', json={'product_id': prod}).json()
    assert c.get(f'/api/matching/recommendations/{run["run_id"]}').json()[0]['inventory_id'] == iid
    res = c.post(f'/api/marketplace/inventory/{iid}/reserve', json={'campaign_id': camp['id']}).json()
    req = c.post(f'/api/campaigns/{camp["id"]}/placement-requests', json={'reservation_id': res['id'], 'product_id': prod}).json()
    assert req['status'] == 'REQUESTED' and req['brand_approved_by']
    assert c.post(f'/api/placement-requests/{req["id"]}/approve').json()['detail']['code'] in ('WRONG_PARTY', 'ORGANIZATION_KIND_FORBIDDEN')
    # the publisher approves: reservation confirmed, item sold, quote issued, campaign active
    login(c, 'pub@pub.io')
    assert c.get('/api/notifications').json()[0]['kind'] == 'placement_request.created'
    ap = c.post(f'/api/placement-requests/{req["id"]}/approve').json()
    assert ap['status'] == 'APPROVED' and ap['reservation']['status'] == 'CONFIRMED' and ap['quote']['amount_minor'] == 50000
    assert next(i for i in c.get('/api/inventory').json() if i['id'] == iid)['status'] == 'SOLD'
    # the brand accepts the quote: one invoice, whatever the retries
    login(c, 'brand@brand.io')
    assert c.get(f'/api/campaigns/{camp["id"]}').json()['status'] == 'ACTIVE'
    q = ap['quote']['id']
    assert c.post(f'/api/quotes/{q}/accept').status_code == 428
    inv1 = c.post(f'/api/quotes/{q}/accept', headers={'Idempotency-Key': 'k-1'}).json()
    inv2 = c.post(f'/api/quotes/{q}/accept', headers={'Idempotency-Key': 'k-1'}).json()
    assert inv1 == inv2 and inv1['publisher_share_minor'] + inv1['platform_fee_minor'] == 50000 and inv1['platform_fee_minor'] == 10000
    assert c.post(f'/api/quotes/{q}/accept', headers={'Idempotency-Key': 'k-2'}).json()['detail']['code'] == 'INVALID_TRANSITION'
    assert TestDB().execute('SELECT COUNT(*) FROM invoices').fetchone()[0] == 1
    # production through the existing engine
    login(c, 'pub@pub.io')
    prodn = c.post(f"/api/placement-requests/{req['id']}/start-production").json()
    assert "placement_id" in prodn, prodn
    pl = prodn['placement_id']
    qc = c.post(f'/api/placements/{pl}/quality-check')
    assert qc.status_code == 200, qc.text
    if qc.json()['status'] != 'PASSED':
        pytest.fail(f'the quality check of the produced placement did not pass: {qc.json()}')
    assert c.post(f'/api/placements/{pl}/approve', json={'fit_confidence': .9}).json()['status'] == 'APPROVED'
    jid = _render(c, pl)
    assert TestDB().execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()['status'] == 'SUCCEEDED'
    # delivery with evidence; the brand downloads and accepts
    login(c, 'brand@brand.io')
    r = c.get(f'/api/placement-requests/{req["id"]}').json()
    assert r['status'] == 'DELIVERED' and r['delivery']['status'] == 'DELIVERED' and 'shadow_product_id' not in r
    d = c.get(f'/api/deliveries/{r["delivery"]["id"]}').json()
    assert [e['kind'] for e in d['evidence']] == ['OUTPUT', 'VALIDATION', 'APPROVALS', 'RIGHTS', 'PROVENANCE']
    prov = next(e for e in d['evidence'] if e['kind'] == 'PROVENANCE')['content']
    assert prov['video_id'] == market['vid'] and prov['brand_product_id'] == prod and prov['placement_id'] == pl
    assert next(e for e in d['evidence'] if e['kind'] == 'VALIDATION')['content']['status'] == 'PASSED'
    link = c.post(f'/api/deliveries/{d["id"]}/download-link').json()
    media = c.get(link['url'])
    assert media.status_code == 200 and media.headers['content-type'] == 'video/mp4'
    # D-SEC-02: a delivery link is not a render-download link (it used to fail with a 500 there).
    assert c.get(link['url'].replace('/api/delivery-media/', '/api/media/')).status_code == 403
    import hashlib
    assert hashlib.sha256(media.content).hexdigest() == d['output_sha256']
    assert c.post(f'/api/placement-requests/{req["id"]}/accept').json()['status'] == 'ACCEPTED'
    # payment and payout by the platform administrator, then the books balance
    login(c, 'ops@visuara.si')
    invoice = TestDB().execute('SELECT id FROM invoices').fetchone()['id']
    assert c.post(f'/api/admin/invoices/{invoice}/mark-paid', json={'reference': 'BANK-123'}, headers={'Idempotency-Key': 'p-1'}).json()['status'] == 'PAID'
    assert c.post(f'/api/admin/invoices/{invoice}/mark-paid', json={'reference': 'BANK-999'}, headers={'Idempotency-Key': 'p-1'}).json()['detail']['code'] == 'IDEMPOTENCY_KEY_REUSED'
    alloc = TestDB().execute("SELECT id FROM revenue_allocations WHERE beneficiary='PUBLISHER'").fetchone()['id']
    assert c.post(f'/api/admin/allocations/{alloc}/payout', json={'reference': 'PAYOUT-1'}, headers={'Idempotency-Key': 'o-1'}).json()['status'] == 'PAID'
    tb = c.get('/api/admin/ledger/trial-balance').json()
    assert tb['balanced'] is True
    bal = {a['account']: a['balance'] for a in tb['accounts']}
    assert bal['revenue:platform'] == -10000 and bal['cash:platform'] == 10000 and all(v == 0 for k, v in bal.items() if k.startswith(('receivable', 'payable')))
    login(c, 'pub@pub.io')
    assert c.get('/api/earnings').json()['totals'] == {'EUR': {'PAID': 40000}}
    # analytics from the outbox, and the campaign report from persisted records. Nothing has delivered the events yet (no
    # runner here, as on Vercel between runner requests): reading the report delivers them first.
    with main.db() as d: assert d.execute("SELECT COUNT(*) FROM outbox_events WHERE status='PENDING'").fetchone()[0] > 0
    login(c, 'brand@brand.io')
    rep = c.get(f'/api/campaigns/{camp["id"]}/report').json()
    assert rep['committed_minor'] == 50000 and rep['invoiced_minor'] == 50000 and rep['paid_minor'] == 50000
    assert rep['requests_by_status'] == {'ACCEPTED': 1} and rep['deliveries'][0]['status'] == 'ACCEPTED'
    assert {'reservation.created', 'placement_request.approved', 'delivery.recorded', 'delivery.accepted', 'invoice.issued', 'invoice.paid'} <= set(rep['events'])
    outbox.deliver(limit=500)
    assert c.get(f'/api/campaigns/{camp["id"]}/report').json()['events'] == rep['events']          # counted once


def test_the_ledger_is_append_only_and_balanced(manual):
    with main.db() as c:
        commercial.post_transaction(c, [('a', None, 'DEBIT', 5, 'EUR'), ('b', None, 'CREDIT', 5, 'EUR')], 'test', 1, 'x', None)
        with pytest.raises(ValueError, match='unbalanced'):
            commercial.post_transaction(c, [('a', None, 'DEBIT', 5, 'EUR'), ('b', None, 'CREDIT', 4, 'EUR')], 'test', 1, 'x', None)
        with pytest.raises(ValueError): commercial.post_transaction(c, [('a', None, 'DEBIT', 0, 'EUR'), ('b', None, 'CREDIT', 0, 'EUR')], 'test', 1, 'x', None)
    for sql in ('UPDATE ledger_entries SET amount_minor=1', 'DELETE FROM ledger_entries'):
        with pytest.raises(Exception) as e:                                   # sqlite3.IntegrityError or psycopg CheckViolation
            with main.db() as c: c.execute(sql)
        assert 'append-only' in str(e.value)
    assert commercial.split(999, 2000) == (800, 199) and sum(commercial.split(1, 2000)) == 1
