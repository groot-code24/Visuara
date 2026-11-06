"""Organizations (Phase 2, AD-3): the organizations a person belongs to, creating one, switching the active one, and
platform administration.

A session has an active organization. Every request acts in it, with the person's role from `memberships`: see
`main.current_user()`. Organization kinds decide which side of the marketplace an organization may act on
(`require_kind`): publishers sell inventory, brands and agencies buy it, and `direct` organizations use Intelligence
and place their own products in their own videos.
"""
import hashlib

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import main as m, limits, members

router = APIRouter()
KINDS = ('direct', 'brand', 'publisher', 'agency')
SELL_SIDE = ('publisher',)
BUY_SIDE = ('brand', 'agency')


def require_kind(u, kinds, action):
    if u.get('org_kind', 'direct') not in kinds:
        raise HTTPException(403, {'code': 'ORGANIZATION_KIND_FORBIDDEN', 'message': f'{action} needs a {" or ".join(kinds)} organization; this one is {u.get("org_kind", "direct")}',
                                  'required_kinds': list(kinds)})


def require_platform_admin(u):
    if not u.get('platform_admin'):
        raise HTTPException(403, {'code': 'PLATFORM_ADMIN_REQUIRED', 'message': 'Only platform administrators can do this'})


def _session_hash(request): return hashlib.sha256((request.cookies.get('cc_session') or '').encode()).hexdigest()


@router.get('/api/organizations')
def my_organizations(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        rows = c.execute("SELECT o.id, o.name, o.kind, m.member_role, m.status FROM memberships m JOIN organizations o ON o.id=m.org_id "
                         "WHERE m.user_id=? AND m.status<>'REMOVED' ORDER BY o.name", (u['id'],)).fetchall()
        from .agencies import delegated_orgs
        clients = delegated_orgs(c, u['id'])
    own = {r['id'] for r in rows}
    return {'active_org_id': u['org_id'], 'default_org_id': u['default_org_id'], 'delegated_from': u.get('delegated_from'),
            'organizations': [dict(r, active=r['id'] == u['org_id']) for r in rows]
            + [{'id': d['org_id'], 'name': d['name'], 'kind': d['kind'], 'member_role': d['member_role'], 'status': 'DELEGATED', 'via_agency': d['via_agency'],
                'active': d['org_id'] == u['org_id']} for d in clients.values() if d['org_id'] not in own]}


class OrgIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    kind: str = 'brand'


@router.post('/api/organizations')
def create_organization(body: OrgIn, request: Request):
    u = m.current_user(request)
    name = body.name.strip()
    if body.kind not in KINDS: raise HTTPException(422, {'code': 'INVALID_KIND', 'message': f'kind must be one of {", ".join(KINDS)}'})
    if not m.rate_limit(f'org-create:{u["id"]}', 5, 3600): raise HTTPException(429, 'Too many organizations created this hour')
    with m.db() as c:
        if c.execute('SELECT 1 FROM organizations WHERE lower(name)=lower(?)', (name,)).fetchone():
            raise HTTPException(409, {'code': 'ORGANIZATION_EXISTS', 'message': 'An organization with this name exists'})
        oid = c.execute('INSERT INTO organizations(name,plan,kind) VALUES(?,?,?)', (name, limits.default_plan(), body.kind)).lastrowid
        members.add_membership(c, u['id'], oid, 'owner')
        m.event(c, dict(u, org_id=oid), 'organization.created', 'organization', oid, {'kind': body.kind})
    return {'id': oid, 'name': name, 'kind': body.kind, 'member_role': 'owner'}


class SwitchIn(BaseModel):
    org_id: int


@router.post('/api/organizations/switch')
def switch_organization(body: SwitchIn, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        r = c.execute("SELECT o.id, o.name, o.kind, m.member_role FROM memberships m JOIN organizations o ON o.id=m.org_id WHERE m.user_id=? AND m.org_id=? AND m.status='ACTIVE'",
                      (u['id'], body.org_id)).fetchone()
        via = None
        if not r:
            from .agencies import delegated_orgs
            d = delegated_orgs(c, u['id']).get(body.org_id)
            if not d: raise HTTPException(404, {'code': 'NOT_A_MEMBER', 'message': 'You are not an active member of that organization'})
            r = {'id': d['org_id'], 'name': d['name'], 'kind': d['kind'], 'member_role': d['member_role'], 'via_agency': d['via_agency']}; via = d['via_agency']
        c.execute('UPDATE sessions SET active_org_id=? WHERE token_hash=? AND user_id=?', (body.org_id, _session_hash(request), u['id']))
        m.event(c, dict(u, org_id=body.org_id), 'organization.switched', 'organization', body.org_id, {'from': u['org_id'], 'via_agency': via})
    return dict(r)


class OrgPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    kind: str | None = None


@router.patch('/api/organizations/current')
def update_organization(body: OrgPatch, request: Request):
    u = m.current_user(request)
    if body.kind is not None and body.kind not in KINDS: raise HTTPException(422, {'code': 'INVALID_KIND', 'message': f'kind must be one of {", ".join(KINDS)}'})
    with m.db() as c:
        if body.name:
            if c.execute('SELECT 1 FROM organizations WHERE lower(name)=lower(?) AND id<>?', (body.name.strip(), u['org_id'])).fetchone():
                raise HTTPException(409, {'code': 'ORGANIZATION_EXISTS', 'message': 'An organization with this name exists'})
            c.execute('UPDATE organizations SET name=? WHERE id=?', (body.name.strip(), u['org_id']))
        if body.kind:
            if body.kind != u['org_kind'] and _has_marketplace_records(c, u['org_id']):
                raise HTTPException(409, {'code': 'KIND_LOCKED', 'message': 'The kind cannot change once the organization has marketplace records'})
            c.execute('UPDATE organizations SET kind=? WHERE id=?', (body.kind, u['org_id']))
        m.event(c, u, 'organization.updated', 'organization', u['org_id'], body.model_dump(exclude_none=True))
        return dict(c.execute('SELECT id,name,kind FROM organizations WHERE id=?', (u['org_id'],)).fetchone())


def _has_marketplace_records(c, org_id):
    tables = set(c.tables())
    for t, col in (('inventory_items', 'org_id'), ('campaigns', 'org_id'), ('reservations', 'brand_org_id'), ('ledger_entries', 'org_id')):
        if t in tables and c.execute(f'SELECT 1 FROM {t} WHERE {col}=? LIMIT 1', (org_id,)).fetchone(): return True
    return False


@router.get('/api/admin/organizations')
def admin_organizations(request: Request):
    u = m.current_user(request); require_platform_admin(u)
    with m.db() as c:
        rows = c.execute("SELECT o.id, o.name, o.kind, o.plan, o.created_at, (SELECT COUNT(*) FROM memberships x WHERE x.org_id=o.id AND x.status='ACTIVE') AS members "
                         "FROM organizations o ORDER BY o.id").fetchall()
    return [dict(r) for r in rows]
