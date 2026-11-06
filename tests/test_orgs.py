"""Phase 2: several organizations per person, the active organization, roles per membership, organization kinds,
platform administration and isolation between organizations."""
import pytest
from fastapi import HTTPException

from app import main, members, orgs
from helpers import TestDB, make_user, login, owner, add_product
from ops import platform_admin


def _orgs(c): return c.get('/api/organizations').json()


def test_a_person_creates_and_switches_between_organizations(manual, tmp_path):
    c = manual
    owner(c, 'founder@acme.io', 'Acme')
    r = c.post('/api/organizations', json={'name': 'Acme Studios', 'kind': 'publisher'})
    assert r.status_code == 200 and r.json()['member_role'] == 'owner'
    studio = r.json()['id']
    data = _orgs(c)
    assert {o['name'] for o in data['organizations']} == {'Acme', 'Acme Studios'} and data['active_org_id'] != studio
    pid = add_product(c, tmp_path, 'Home product')                                      # created in the default organization
    assert c.post('/api/organizations/switch', json={'org_id': studio}).json()['kind'] == 'publisher'
    me = c.get('/api/me').json()
    assert me['org_id'] == studio and me['org_kind'] == 'publisher' and me['member_role'] == 'owner'
    assert c.get('/api/products').json() == []                                          # nothing leaks across organizations
    assert c.get(f'/api/products/{pid}/image').status_code == 404
    assert c.post('/api/organizations', json={'name': 'acme studios', 'kind': 'brand'}).status_code == 409
    assert c.post('/api/organizations', json={'name': 'Other', 'kind': 'empire'}).status_code == 422


def test_switching_needs_an_active_membership(manual):
    c = manual
    other = make_user('o@else.io', org='Elsewhere')
    owner(c, 'me@acme.io', 'Acme')
    assert c.post('/api/organizations/switch', json={'org_id': other['org_id']}).json()['detail']['code'] == 'NOT_A_MEMBER'


def test_existing_accounts_accept_invitations_and_get_that_role(manual, tmp_path):
    c = manual
    brand = make_user('boss@brand.io', org='Brand Co')
    pub = make_user('editor@pub.io', org='Pub Co')
    login(c, 'boss@brand.io')
    assert c.post('/api/invitations', json={'email': 'editor@pub.io', 'member_role': 'viewer'}).status_code == 200
    assert c.post('/api/invitations', json={'email': 'boss@brand.io', 'member_role': 'viewer'}).json()['detail']['code'] == 'ALREADY_MEMBER'
    login(c, 'editor@pub.io')
    inv = c.get('/api/invitations/mine').json()
    assert [i['organization'] for i in inv] == ['Brand Co']
    login(c, 'boss@brand.io'); assert c.post(f'/api/invitations/{inv[0]["id"]}/accept').status_code == 404       # someone else's invitation
    login(c, 'editor@pub.io')
    assert c.post(f'/api/invitations/{inv[0]["id"]}/accept').json() == {'org_id': brand['org_id'], 'member_role': 'viewer'}
    assert c.post(f'/api/invitations/{inv[0]["id"]}/accept').status_code == 404                                   # single use
    assert c.get('/api/me').json()['org_id'] == pub['org_id']                                                    # the default is unchanged
    assert add_product(c, tmp_path, 'Mine')
    c.post('/api/organizations/switch', json={'org_id': brand['org_id']})
    r = c.post('/api/products', data={'name': 'x'}, files={'image': ('p.png', b'x', 'image/png')})
    assert r.status_code == 403 and r.json()['detail']['code'] == 'ROLE_FORBIDDEN'                               # viewer there
    names = {m['email'] for m in c.get('/api/members').json()['members']}
    assert names == {'boss@brand.io', 'editor@pub.io'}


def test_suspension_and_removal_are_per_organization(manual):
    c = manual
    brand = make_user('boss@brand.io', org='Brand Co')
    pub = make_user('p@pub.io', org='Pub Co')
    with main.db() as con: members.add_membership(con, pub['id'], brand['org_id'], 'editor')
    login(c, 'boss@brand.io')
    assert c.post(f'/api/members/{pub["id"]}/suspend').status_code == 200
    login(c, 'p@pub.io')
    assert c.get('/api/me').json()['org_id'] == pub['org_id']                         # still works at home
    assert c.post('/api/organizations/switch', json={'org_id': brand['org_id']}).status_code == 404
    login(c, 'boss@brand.io')
    assert c.post(f'/api/members/{pub["id"]}/reactivate').status_code == 200
    assert c.delete(f'/api/members/{pub["id"]}').json()['removed'] is True
    row = TestDB().execute('SELECT email, removed_at, org_id FROM users WHERE id=?', (pub['id'],)).fetchone()
    assert row['email'] == 'p@pub.io' and row['removed_at'] is None and row['org_id'] == pub['org_id']        # account kept
    login(c, 'p@pub.io')
    assert {o['id'] for o in _orgs(c)['organizations']} == {pub['org_id']}
    with main.db() as con: assert members.membership_drift(con) == []


def test_removal_from_the_default_organization_moves_the_default(manual):
    c = manual
    a = make_user('a@one.io', org='One'); b = make_user('b@two.io', org='Two')
    with main.db() as con: members.add_membership(con, a['id'], b['org_id'], 'manager')
    # a second owner in One removes a
    make_user('boss@one.io', org_id=a['org_id'], member_role='owner')
    with main.db() as con: members.set_role(con, a['id'], a['org_id'], 'editor')
    login(c, 'boss@one.io')
    assert c.delete(f'/api/members/{a["id"]}').status_code == 200
    row = TestDB().execute('SELECT org_id, member_role, removed_at FROM users WHERE id=?', (a['id'],)).fetchone()
    assert (row['org_id'], row['member_role'], row['removed_at']) == (b['org_id'], 'manager', None)
    login(c, 'a@one.io'); assert c.get('/api/me').json()['org_id'] == b['org_id']
    with main.db() as con: assert members.membership_drift(con) == []


def test_only_the_owner_changes_the_organization_kind(manual):
    c = manual
    o = make_user('owner@acme.io', org='Acme')
    make_user('mgr@acme.io', org_id=o['org_id'], member_role='manager')
    login(c, 'mgr@acme.io')
    assert c.patch('/api/organizations/current', json={'kind': 'brand'}).status_code == 403
    login(c, 'owner@acme.io')
    assert c.patch('/api/organizations/current', json={'kind': 'brand'}).json()['kind'] == 'brand'
    assert c.get('/api/me').json()['org_kind'] == 'brand'


def test_platform_administration_is_granted_outside_the_api(manual):
    c = manual
    owner(c, 'ops@visuara.si', 'Visuara Ops')
    r = c.get('/api/admin/organizations')
    assert r.status_code == 403 and r.json()['detail']['code'] == 'PLATFORM_ADMIN_REQUIRED'
    assert platform_admin.set_flag('ops@visuara.si', True) == 1
    rows = c.get('/api/admin/organizations').json()
    assert any(r['name'] == 'Visuara Ops' and r['members'] == 1 for r in rows)
    assert c.get('/api/me').json()['platform_admin'] is True


def test_require_kind():
    orgs.require_kind({'org_kind': 'publisher'}, orgs.SELL_SIDE, 'Listing inventory')
    with pytest.raises(HTTPException) as e: orgs.require_kind({'org_kind': 'brand'}, orgs.SELL_SIDE, 'Listing inventory')
    assert e.value.status_code == 403 and e.value.detail['code'] == 'ORGANIZATION_KIND_FORBIDDEN'
