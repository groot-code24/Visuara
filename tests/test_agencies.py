"""M15 agency delegation: a brand grants an agency access; the agency's people act for the brand within the grant,
never above it, and lose access the moment it is revoked."""
from app import main
from helpers import TestDB, login, make_user


def _kind(org_id, kind):
    with main.db() as c: c.execute('UPDATE organizations SET kind=? WHERE id=?', (kind, org_id))


def _setup():
    brand = make_user('owner@brand.io', org='Brand Co'); _kind(brand['org_id'], 'brand')
    other = make_user('owner@other.io', org='Other Brand'); _kind(other['org_id'], 'brand')
    agency = make_user('boss@agency.io', org='Agency Ltd'); _kind(agency['org_id'], 'agency')
    make_user('planner@agency.io', member_role='manager', org_id=agency['org_id'])
    make_user('intern@agency.io', member_role='viewer', org_id=agency['org_id'])
    return brand, other, agency


def test_an_agency_acts_for_its_client_within_the_grant(manual):
    c = manual; brand, other, agency = _setup()
    login(c, 'planner@agency.io')
    assert c.post('/api/organizations/switch', json={'org_id': brand['org_id']}).status_code == 404        # no grant yet
    login(c, 'owner@brand.io')
    assert c.post('/api/agency/grants', json={'agency_org_id': other['org_id']}).json()['detail']['code'] == 'AGENCY_NOT_FOUND'   # not an agency
    g = c.post('/api/agency/grants', json={'agency_org_id': agency['org_id'], 'role': 'manager'}).json()
    assert g['status'] == 'ACTIVE' and c.post('/api/agency/grants', json={'agency_org_id': agency['org_id']}).json()['detail']['code'] == 'GRANT_EXISTS'
    # the agency sees its client and works in it
    login(c, 'planner@agency.io')
    assert [x['client'] for x in c.get('/api/agency/clients').json()] == ['Brand Co']
    s = c.post('/api/organizations/switch', json={'org_id': brand['org_id']}).json()
    assert s['member_role'] == 'manager' and s['via_agency'] == agency['org_id']
    orgs = c.get('/api/organizations').json()
    assert orgs['active_org_id'] == brand['org_id'] and orgs['delegated_from'] == agency['org_id']
    camp = c.post('/api/campaigns', json={'name': 'Agency launch', 'budget_minor': 1000, 'currency': 'EUR'}).json()
    assert TestDB().execute('SELECT org_id FROM campaigns WHERE id=?', (camp['id'],)).fetchone()['org_id'] == brand['org_id']
    # never above the grant: members, invitations, settings and agency access stay with the client
    assert c.post('/api/invitations', json={'email': 'x@agency.io', 'member_role': 'viewer'}).status_code == 403
    assert c.patch('/api/organizations/current', json={'name': 'Renamed'}).status_code == 403
    assert c.post('/api/agency/grants', json={'agency_org_id': agency['org_id']}).status_code == 403
    assert c.post('/api/organizations/switch', json={'org_id': other['org_id']}).status_code == 404      # only the client that granted
    ev = TestDB().execute("SELECT details FROM events WHERE event_type='organization.switched' ORDER BY id DESC LIMIT 1").fetchone()
    assert f'"via_agency": {agency["org_id"]}' in ev['details']
    # a viewer of the agency is a viewer in the client too
    login(c, 'intern@agency.io')
    assert c.post('/api/organizations/switch', json={'org_id': brand['org_id']}).json()['member_role'] == 'viewer'
    assert c.post('/api/campaigns', json={'name': 'No', 'budget_minor': 1000, 'currency': 'EUR'}).status_code == 403
    # the client revokes: the planner's very next request is back in the agency
    login(c, 'owner@brand.io')
    assert c.post(f'/api/agency/grants/{g["id"]}/revoke').json()['status'] == 'REVOKED'
    login(c, 'planner@agency.io')
    with main.db() as con:
        con.execute('UPDATE sessions SET active_org_id=? WHERE user_id=(SELECT id FROM users WHERE email=?)', (brand['org_id'], 'planner@agency.io'))
    assert c.get('/api/organizations').json()['active_org_id'] == agency['org_id']
    assert c.get(f'/api/campaigns/{camp["id"]}').status_code == 404
    assert c.post('/api/organizations/switch', json={'org_id': brand['org_id']}).status_code == 404


def test_the_campaign_list_costs_the_same_queries_for_3_or_30_campaigns(manual, monkeypatch):
    """M16: the list made four queries per campaign; under load at 100 campaigns it took 2.2 s at the median."""
    brand = make_user('owner@brand.io', org='Brand Co'); _kind(brand['org_id'], 'brand')
    login(manual, 'owner@brand.io')
    counts = []
    with main.db() as con: cls = type(con)                  # SQLite or PostgreSQL, whichever this run uses
    real = cls.execute

    def counting(self, sql, params=()):
        counts[-1] += 1
        return real(self, sql, params)

    for target in (3, 30):
        while len(manual.get('/api/campaigns').json()) < target:
            manual.post('/api/campaigns', json={'name': f'C {len(counts)} {target} {id(counts)}', 'budget_minor': 1, 'currency': 'EUR'})
        monkeypatch.setattr(cls, 'execute', counting); counts.append(0)
        listed = manual.get('/api/campaigns').json()
        monkeypatch.setattr(cls, 'execute', real)
        assert len(listed) >= target and all(x['transitions'][0]['to_status'] == 'DRAFT' for x in listed)
    assert counts[0] == counts[1] and counts[0] > 0


def test_only_an_owner_or_admin_manages_agency_access(manual):
    c = manual; brand, other, agency = _setup()
    make_user('editor@brand.io', member_role='editor', org_id=brand['org_id'])
    login(c, 'editor@brand.io')
    assert c.post('/api/agency/grants', json={'agency_org_id': agency['org_id']}).status_code == 403
    login(c, 'owner@brand.io')
    g = c.post('/api/agency/grants', json={'agency_org_id': agency['org_id'], 'role': 'viewer'}).json()
    login(c, 'planner@agency.io')                                 # a manager of the agency cannot end the relationship
    assert c.post(f'/api/agency/clients/{g["id"]}/leave').status_code == 403
    login(c, 'boss@agency.io')
    assert c.post(f'/api/agency/clients/{g["id"]}/leave').json()['status'] == 'REVOKED'
    login(c, 'owner@other.io')                                    # another client cannot revoke someone else's grant
    assert c.post(f'/api/agency/grants/{g["id"]}/revoke').status_code == 404
