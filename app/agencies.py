"""Agency delegation (M15): an agency acts for a client brand without joining it.

The client decides. A brand organization's owner or admin grants an agency organization access with a role (viewer,
editor or manager), and can revoke it at any time; the agency can leave. Members of the agency can then switch into the
client (`POST /api/organizations/switch`) and act there with the lower of the grant's role and their own role in the
agency, never more than manager: members, invitations, ownership and the organization's settings stay with the client.
Grants are checked on every request (`main.current_user` → `delegated_orgs`), so a revocation takes effect immediately,
and every action is recorded under the person who did it, with the agency it came through.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import main as m
from .members import RANK, ROLES

router = APIRouter()
GRANT_ROLES = ('viewer', 'editor', 'manager')


def _err(status, code, message): return HTTPException(status, {'code': code, 'message': message})


def delegated_orgs(c, user_id):
    """{client_org_id: row} for the ACTIVE grants reachable through the person's ACTIVE agency memberships, with the role
    capped at the person's own agency role."""
    rows = c.execute("SELECT g.id, g.client_org_id, g.agency_org_id, g.role AS grant_role, mm.member_role AS agency_role, o.kind, o.name "
                     "FROM agency_clients g JOIN memberships mm ON mm.org_id=g.agency_org_id AND mm.user_id=? AND mm.status='ACTIVE' "
                     "JOIN organizations a ON a.id=g.agency_org_id AND a.kind='agency' JOIN organizations o ON o.id=g.client_org_id "
                     "WHERE g.status='ACTIVE' ORDER BY g.id", (user_id,)).fetchall()
    out = {}
    for r in rows:
        role = ROLES[min(RANK[r['grant_role']], RANK.get(r['agency_role'], 0), RANK['manager'])]
        if r['client_org_id'] not in out or RANK[role] > RANK[out[r['client_org_id']]['member_role']]:
            out[r['client_org_id']] = {'org_id': r['client_org_id'], 'member_role': role, 'kind': r['kind'], 'name': r['name'],
                                       'via_agency': r['agency_org_id'], 'grant_id': r['id']}
    return out


def _require_admin(u):
    if u.get('delegated_from') or RANK.get(u.get('member_role'), 0) < RANK['admin']:
        raise _err(403, 'ROLE_FORBIDDEN', 'Only an owner or admin of this organization can manage agency access')


class GrantIn(BaseModel):
    agency_org_id: int
    role: str = 'manager'


@router.post('/api/agency/grants')
def grant(body: GrantIn, request: Request):
    """The client gives an agency access. The agency's id is on its organization page."""
    u = m.current_user(request); _require_admin(u)
    if u['org_kind'] not in ('brand', 'direct'): raise _err(403, 'ORGANIZATION_KIND_FORBIDDEN', 'Only a brand (or direct) organization can give an agency access')
    if body.role not in GRANT_ROLES: raise _err(422, 'INVALID_ROLE', f'role must be one of {", ".join(GRANT_ROLES)}')
    with m.db() as c:
        a = c.execute("SELECT id, name, kind FROM organizations WHERE id=?", (body.agency_org_id,)).fetchone()
        if not a or a['kind'] != 'agency': raise _err(404, 'AGENCY_NOT_FOUND', 'No agency organization has this id')
        if a['id'] == u['org_id']: raise _err(409, 'SELF_GRANT', 'An organization cannot be its own agency')
        if c.execute("SELECT 1 FROM agency_clients WHERE agency_org_id=? AND client_org_id=? AND status IN ('PENDING','ACTIVE')", (a['id'], u['org_id'])).fetchone():
            raise _err(409, 'GRANT_EXISTS', 'This agency already has access; revoke it first to change the role')
        gid = c.execute("INSERT INTO agency_clients(agency_org_id,client_org_id,status,role,requested_by,decided_by,decided_at) VALUES(?,?,'ACTIVE',?,?,?,CURRENT_TIMESTAMP)",
                        (a['id'], u['org_id'], body.role, u['id'], u['id'])).lastrowid
        m.event(c, u, 'agency.granted', 'agency_grant', gid, {'agency_org_id': a['id'], 'role': body.role})
        from .inventory import notify
        notify(c, a['id'], 'agency.granted', 'A client gave your agency access', f'Role: {body.role}', 'agency_grant', gid)
    return {'id': gid, 'agency_org_id': a['id'], 'agency': a['name'], 'role': body.role, 'status': 'ACTIVE'}


@router.get('/api/agency/grants')
def list_grants(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        rows = c.execute("SELECT g.id, g.agency_org_id, o.name AS agency, g.role, g.status, g.created_at, g.decided_at FROM agency_clients g "
                         "JOIN organizations o ON o.id=g.agency_org_id WHERE g.client_org_id=? ORDER BY g.id DESC", (u['org_id'],)).fetchall()
    return [dict(r) for r in rows]


@router.post('/api/agency/grants/{gid}/revoke')
def revoke(gid: int, request: Request):
    u = m.current_user(request); _require_admin(u)
    with m.db() as c:
        if c.execute("UPDATE agency_clients SET status='REVOKED', decided_by=?, decided_at=CURRENT_TIMESTAMP WHERE id=? AND client_org_id=? AND status='ACTIVE'",
                     (u['id'], gid, u['org_id'])).rowcount != 1:
            raise HTTPException(404, 'Resource not found')
        m.event(c, u, 'agency.revoked', 'agency_grant', gid, {})
    return {'id': gid, 'status': 'REVOKED'}


@router.get('/api/agency/clients')
def list_clients(request: Request):
    """The agency side: the clients that gave this agency access."""
    u = m.current_user(request)
    if u['org_kind'] != 'agency': raise _err(403, 'ORGANIZATION_KIND_FORBIDDEN', 'Only an agency organization has clients')
    with m.db() as c:
        rows = c.execute("SELECT g.id, g.client_org_id, o.name AS client, o.kind, g.role, g.status, g.decided_at FROM agency_clients g "
                         "JOIN organizations o ON o.id=g.client_org_id WHERE g.agency_org_id=? AND g.status='ACTIVE' ORDER BY o.name", (u['org_id'],)).fetchall()
    return [dict(r) for r in rows]


@router.post('/api/agency/clients/{gid}/leave')
def leave(gid: int, request: Request):
    u = m.current_user(request); _require_admin(u)
    with m.db() as c:
        if c.execute("UPDATE agency_clients SET status='REVOKED', decided_by=?, decided_at=CURRENT_TIMESTAMP WHERE id=? AND agency_org_id=? AND status='ACTIVE'",
                     (u['id'], gid, u['org_id'])).rowcount != 1:
            raise HTTPException(404, 'Resource not found')
        m.event(c, u, 'agency.left', 'agency_grant', gid, {})
    return {'id': gid, 'status': 'REVOKED'}
