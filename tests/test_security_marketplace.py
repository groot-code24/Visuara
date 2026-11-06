"""Phase 14: tenant isolation, role and organization-kind enforcement, and adversarial input across the Phase 2-13 APIs.
Phase 13: the programmatic capabilities are labelled honestly and the feed comes from canonical inventory."""
import pytest

from app import main, members
from helpers import TestDB, login, make_user, needs_media
from test_marketplace import market, _list  # noqa: F401  (fixture)


def _world(market):
    """Brand Co holds a campaign with a request on Pub Studio's item; strangers on both sides; a viewer and an editor at Brand Co."""
    c = market['c']; iid = _list(market)
    login(c, 'brand@brand.io')
    camp = c.post('/api/campaigns', json={'name': 'Secret campaign', 'currency': 'EUR'}).json()
    c.put(f'/api/campaigns/{camp["id"]}/products', json={'product_ids': [market['product']]})
    c.post(f'/api/campaigns/{camp["id"]}/transition', json={'to': 'SUBMITTED'})
    res = c.post(f'/api/marketplace/inventory/{iid}/reserve', json={'campaign_id': camp['id']}).json()
    req = c.post(f'/api/campaigns/{camp["id"]}/placement-requests', json={'reservation_id': res['id'], 'product_id': market['product']}).json()
    brand_org = TestDB().execute("SELECT org_id FROM users WHERE email='brand@brand.io'").fetchone()['org_id']
    for email, role in (('viewer@brand.io', 'viewer'), ('editor@brand.io', 'editor')):
        u = make_user(email, org_id=brand_org, member_role=role)
        with main.db() as con: members.sync_membership(con, u['id'])
    make_user('stranger@pub2.io', org='Other Publisher'); login(c, 'stranger@pub2.io'); c.patch('/api/organizations/current', json={'kind': 'publisher'})
    return {'iid': iid, 'camp': camp['id'], 'res': res['id'], 'req': req['id'], 'opp': market['item']['opportunity_id'], 'vid': market['vid']}


@needs_media
def test_other_organizations_see_nothing(market):
    c = market['c']; w = _world(market)
    a = TestDB().execute('SELECT id FROM analysis_versions WHERE video_id=?', (w['vid'],)).fetchone()['id']
    scene = TestDB().execute('SELECT scene_id FROM inventory_items WHERE id=?', (w['iid'],)).fetchone()['scene_id']
    login(c, 'stranger@pub2.io')                                    # another publisher
    for path in (f'/api/opportunities/{w["opp"]}', f'/api/analyses/{a}', f'/api/analyses/{a}/scenes', f'/api/scenes/{scene}', f'/api/videos/{w["vid"]}/rights',
                 f'/api/videos/{w["vid"]}/versions', f'/api/inventory/{w["iid"]}/preview', f'/api/campaigns/{w["camp"]}', f'/api/campaigns/{w["camp"]}/report',
                 f'/api/placement-requests/{w["req"]}'):
        assert c.get(path).status_code == 404, path
    for path, body in ((f'/api/opportunities/{w["opp"]}/review', {'brand_safety': 'UNSAFE'}), (f'/api/inventory/{w["iid"]}/withdraw', {}),
                       (f'/api/inventory/{w["iid"]}/list', {'price_minor': 1, 'currency': 'EUR'}), (f'/api/placement-requests/{w["req"]}/approve', {}),
                       (f'/api/placement-requests/{w["req"]}/reject', {}), (f'/api/reservations/{w["res"]}/release', {}), (f'/api/analyses/{a}/cancel', {})):
        assert c.post(path, json=body).status_code == 404, path
    assert c.get('/api/placement-requests').json() == [] and c.get('/api/reservations').json() == [] and c.get('/api/inventory').json() == []
    login(c, 'brand2@rival.io')                                     # another brand
    assert c.get(f'/api/marketplace/inventory/{w["iid"]}').status_code == 404               # reserved by someone else: hidden
    assert c.post(f'/api/reservations/{w["res"]}/release').status_code == 404
    assert c.post(f'/api/placement-requests/{w["req"]}/cancel', json={}).status_code == 404
    assert c.get(f'/api/campaigns/{w["camp"]}').status_code == 404
    login(c, 'pub@pub.io')                                          # the publisher sees the request but not the brand's campaign
    assert c.get(f'/api/campaigns/{w["camp"]}').status_code == 404
    assert c.post(f'/api/placement-requests/{w["req"]}/cancel', json={}).json()['detail']['code'] == 'WRONG_PARTY'
    assert c.post(f'/api/placement-requests/{w["req"]}/accept').json()['detail']['code'] == 'WRONG_PARTY'


@needs_media
def test_every_id_route_refuses_an_organization_that_owns_nothing(market):
    """Sweep every API route that takes an identifier, with every identifier the other organizations use.

    A publisher that owns nothing must never get a 2xx: neither a read of another organization's record nor a write
    to it. The list of routes comes from the application itself, so a route added later is covered automatically.
    """
    import re
    c = market['c']; _world(market)
    db = TestDB()
    top = max(db.execute(f'SELECT COALESCE(MAX(id),0) FROM {t}').fetchone()[0] for t in
              ('videos', 'products', 'placements', 'jobs', 'campaigns', 'inventory_items', 'reservations', 'placement_requests',
               'analysis_versions', 'opportunities', 'delivery_records', 'invoices', 'organizations', 'users', 'decisions'))
    ids = range(1, top + 2)
    # Every endpoint, from the OpenAPI schema (FastAPI nests included routers, so app.routes does not list them).
    # Each write gets a minimal body that passes request validation, so the handler's own ownership check is what
    # answers. Routes that take a signed token instead of a session check it themselves: a wrong token is a 403.
    spec = main.app.openapi()
    routes = [(path, method.upper(), op) for path, ops in spec['paths'].items() if '{' in path for method, op in ops.items()]
    assert len(routes) > 60, f'the sweep found only {len(routes)} routes'
    login(c, 'stranger@pub2.io')
    leaks, unreached = [], set()
    for template, method, op in routes:
            statuses = set()
            for i in ids:
                path = re.sub(r'\{[^}]+\}', str(i), template)
                resp = c.request(method, path, params=_minimal_query(spec, op), **_minimal_body(spec, op))
                statuses.add(resp.status_code)
                if not 200 <= resp.status_code < 300: continue
                if (template, method) in PUBLIC_BY_DESIGN:
                    assert set(resp.json()) <= PUBLIC_BY_DESIGN[(template, method)], f'{path} exposes {set(resp.json()) - PUBLIC_BY_DESIGN[(template, method)]}'
                    continue
                leaks.append(f'{method} {path} -> {resp.status_code}')
            if statuses == {422}: unreached.add(f'{method} {template}')
    assert not leaks, 'another organization reached records it does not own:\n' + '\n'.join(leaks[:40])
    # A route that only ever answered 422 never reached its ownership check, so the sweep proved nothing about it.
    assert not unreached, 'request validation stopped the sweep before the ownership check:\n' + '\n'.join(sorted(unreached))


# Values that pass the handlers' own checks, which some routes run before looking up the record (a JSON body keeps
# only the keys its schema defines, so the extra keys are ignored elsewhere).
VALID_VALUES = {'brand_safety': 'SAFE', 'member_role': 'viewer', 'x': 0.5, 'y': 0.5, 'width': 0.3, 'frame_second': 0.0, 'start_second': 0.0, 'end_second': 1.0}


def _schema(spec, s):
    while '$ref' in s: s = spec['components']['schemas'][s['$ref'].rsplit('/', 1)[1]]
    return s


def _value(spec, s):
    """The smallest value that passes the schema: required fields only, lower bounds, the first enum member."""
    s = _schema(spec, s)
    if 'anyOf' in s: s = _schema(spec, next(x for x in s['anyOf'] if x.get('type') != 'null'))
    if 'enum' in s: return s['enum'][0]
    if 'const' in s: return s['const']
    t = s.get('type')
    if t == 'object': return {k: _value(spec, v) for k, v in s.get('properties', {}).items() if k in s.get('required', [])}
    if t == 'array': return [_value(spec, s['items'])] * s.get('minItems', 0)
    if t == 'integer': return max(1, s.get('minimum', 1))
    if t == 'number': return max(1.0, s.get('minimum', 1.0))
    if t == 'boolean': return True
    if s.get('format') == 'binary' or s.get('contentMediaType'): return b'not a real file'
    if s.get('format') == 'date': return '2030-01-01'
    return 'sweep'.ljust(s.get('minLength', 1), 'x')


def _minimal_body(spec, op):
    body = op.get('requestBody')
    if not body: return {}
    content = body['content']
    if 'application/json' in content: return {'json': {**_value(spec, content['application/json']['schema']), **VALID_VALUES}}
    form = _schema(spec, next(iter(content.values()))['schema'])
    fields = {k: VALID_VALUES.get(k, _value(spec, v)) for k, v in form.get('properties', {}).items() if k in form.get('required', [])}
    files = {k: ('upload.bin', v, 'application/octet-stream') for k, v in fields.items() if isinstance(v, bytes)}
    data = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in fields.items() if not isinstance(v, bytes)}
    return {'data': data, 'files': files} if files else {'data': data}


def _minimal_query(spec, op):
    return {p['name']: _value(spec, p['schema']) for p in op.get('parameters', []) if p['in'] == 'query' and p.get('required')}


# What a brand is meant to see about any publisher. Every field is public marketing information; a new column must
# be added here on purpose, or the sweep above fails.
PUBLIC_BY_DESIGN = {('/api/publishers/{org_id}/profile', 'GET'): {
    'id', 'org_id', 'display_name', 'website', 'description', 'content_categories', 'audience', 'created_at', 'updated_at', 'listed_items'}}


@needs_media
def test_roles_and_kinds_gate_marketplace_actions(market):
    c = market['c']; w = _world(market)
    login(c, 'viewer@brand.io')
    assert c.get(f'/api/campaigns/{w["camp"]}').status_code == 200
    for path, body in (('/api/campaigns', {'name': 'Viewer campaign'}), (f'/api/marketplace/inventory/{w["iid"]}/reserve', {}), (f'/api/placement-requests/{w["req"]}/cancel', {}),
                       (f'/api/campaigns/{w["camp"]}/transition', {'to': 'PAUSED'})):
        r = c.post(path, json=body); assert r.status_code == 403 and r.json()['detail']['code'] == 'ROLE_FORBIDDEN', path
    login(c, 'editor@brand.io')
    assert c.post('/api/campaigns', json={'name': 'Editors may draft'}).status_code == 200
    assert c.post(f'/api/campaigns/{w["camp"]}/transition', json={'to': 'PAUSED'}).json()['detail']['code'] == 'ROLE_FORBIDDEN'      # managers decide
    login(c, 'ops@visuara.si')                                       # a direct organization is on neither side
    assert c.get('/api/marketplace/inventory').json()['detail']['code'] == 'ORGANIZATION_KIND_FORBIDDEN'
    assert c.post('/api/campaigns', json={'name': 'Ops campaign'}).json()['detail']['code'] == 'ORGANIZATION_KIND_FORBIDDEN'
    login(c, 'pub@pub.io')
    assert c.patch('/api/organizations/current', json={'kind': 'brand'}).json()['detail']['code'] == 'KIND_LOCKED'   # has marketplace records


@needs_media
def test_adversarial_input_is_refused_or_stored_inert(market):
    c = market['c']; w = _world(market)
    login(c, 'brand@brand.io')
    evil = "Robert'); DROP TABLE campaigns;--"
    cid = c.post('/api/campaigns', json={'name': evil}).json()['id']
    assert c.get(f'/api/campaigns/{cid}').json()['name'] == evil and 'campaigns' in TestDB().execute("SELECT 'campaigns' AS t").fetchone()['t']
    assert c.post('/api/campaigns', json={'name': 'Bad input', 'budget_minor': -1}).status_code == 422
    assert c.post('/api/campaigns', json={'name': 'Bad input', 'budget_minor': 10 ** 30}).status_code == 422
    assert c.post('/api/campaigns', json={'name': 'Bad input', 'currency': 'BTC'}).json()['detail']['code'] == 'INVALID_CURRENCY'
    assert c.post('/api/campaigns', json={'name': 'Bad input', 'starts_on': '2026-12-01', 'ends_on': '2026-11-01'}).json()['detail']['code'] == 'INVALID_DATE'
    assert c.get('/api/marketplace/inventory?min_score=7').status_code == 422
    assert c.get(f'/api/delivery-media/{"a" * 40}').status_code == 403                      # a forged link
    assert c.get('/api/media/not.a.token').status_code == 403
    # a request on a reservation that has expired is refused
    with main.db() as con: con.execute("UPDATE reservations SET status='HELD', expires_at=0 WHERE id=?", (w['res'],))
    r = c.post(f'/api/campaigns/{w["camp"]}/placement-requests', json={'reservation_id': w['res'], 'product_id': market['product']})
    assert r.status_code == 409


def test_programmatic_capabilities_are_labelled(manual):
    make_user('p@p.io', org='P'); login(manual, 'p@p.io')
    caps = {x['name']: x['status'] for x in manual.get('/api/programmatic/capabilities').json()['capabilities']}
    # M17: measurement now has an interface, a contract and a test adapter, so without a partner it is NOT_CONNECTED
    assert caps['inventory_feed'] == 'IMPLEMENTED' and caps['real_time_bidding'] == 'DESIGNED_ONLY' and caps['third_party_measurement'] == 'NOT_CONNECTED'
    assert manual.get('/api/programmatic/inventory-feed').json()['detail']['code'] == 'ORGANIZATION_KIND_FORBIDDEN'
    manual.patch('/api/organizations/current', json={'kind': 'publisher'})
    feed = manual.get('/api/programmatic/inventory-feed').json()
    assert feed['schema'] == 'visuara-inventory-feed/1' and feed['items'] == []
    from app.programmatic import NotConnected
    with pytest.raises(NotImplementedError, match='DESIGNED_ONLY'): NotConnected().bid({})


@needs_media
def test_the_feed_shows_only_available_inventory(market):
    c = market['c']; iid = _list(market)
    login(c, 'pub@pub.io')
    feed = c.get('/api/programmatic/inventory-feed').json()
    assert [i['imp'][0]['id'] for i in feed['items']] == [str(iid)]
    assert feed['items'][0]['imp'][0]['bidfloor'] == 500.0 and feed['items'][0]['imp'][0]['ext']['visuara']['score_is_heuristic'] is True
