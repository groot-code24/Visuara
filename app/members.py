"""Workspace membership: member roles, permissions, invitations, suspension, removal and ownership transfer.

Each account belongs to one workspace. What the person may do inside the workspace is their `member_role`:

    owner    everything, including transferring ownership
    admin    manage members and invitations, plus everything a manager can do
    manager  approvals, renders, downloads, takedowns
    editor   content work: uploads, products, analysis, decisions, placements, quality checks, revisions
    viewer   read-only reviewer

Permissions are enforced in one place, `authorize()`, which `main.current_user()` calls for every authenticated
request. GET requests need viewer; other methods need manager unless a rule below says otherwise.
"""
import json, re, secrets, time, hashlib, os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import main as m, mailer, limits

router = APIRouter()
ROLES = ('viewer', 'editor', 'manager', 'admin', 'owner')
RANK = {r: i for i, r in enumerate(ROLES)}
INVITABLE = ('viewer', 'editor', 'manager', 'admin')
_RULES = [  # (methods, path pattern, minimum member role); first match wins
    ({'POST'}, r'^/api/invitations/\d+/accept$', 'viewer'),
    ({'POST'}, r'^/api/notifications/\d+/read$', 'viewer'),
    ({'PATCH'}, r'^/api/products/\d+$', 'editor'),
    ({'POST'}, r'^/api/matching/recommendations$', 'editor'),
    ({'POST', 'PUT', 'PATCH'}, r'^/api/campaigns(/\d+(/(products|targeting))?)?$', 'editor'),
    ({'POST'}, r'^/api/analyses/\d+/cancel$', 'editor'),
    ({'POST'}, r'^/api/placement-requests/\d+/start-production$', 'editor'),
    ({'POST'}, r'^/api/organizations(/switch)?$', 'viewer'),
    ({'PATCH'}, r'^/api/organizations/current$', 'owner'),
    ({'GET', 'POST', 'PATCH', 'DELETE'}, r'^/api/admin/.*$', 'viewer'),          # platform administrators only, checked in the handler
    ({'POST'}, r'^/api/members/\d+/transfer-ownership$', 'owner'),
    ({'GET'}, r'^/api/(members|invitations|usage|billing)$', 'viewer'),
    ({'POST'}, r'^/api/billing/(checkout|change|portal)$', 'admin'),
    ({'POST', 'PATCH', 'DELETE'}, r'^/api/(members|invitations)(/.*)?$', 'admin'),
    ({'GET', 'POST'}, r'^/api/jobs/\d+/download(-link)?$', 'manager'),
    ({'POST'}, r'^/api/(products|videos|uploads)(/.*)?$', 'editor'),
    ({'POST'}, r'^/api/placements$', 'editor'),
    ({'POST'}, r'^/api/placements/\d+/quality-check$', 'editor'),
    ({'POST'}, r'^/api/decisions(/\d+/apply)?$', 'editor'),
    ({'POST'}, r'^/api/decisions/\d+/cancel$', 'editor'),
    ({'PATCH'}, r'^/api/placements/\d+/revise$', 'editor'),
]
_COMPILED = [(meths, re.compile(p), role) for meths, p, role in _RULES]


def required_role(method, path):
    for meths, rx, role in _COMPILED:
        if method in meths and rx.match(path): return role
    return 'viewer' if method in ('GET', 'HEAD', 'OPTIONS') else 'manager'


def authorize(user, request):
    if user.get('suspended_at') or user.get('removed_at'):
        raise HTTPException(403, {'code': 'ACCOUNT_SUSPENDED', 'message': 'This account is suspended. Contact your workspace owner.'})
    need = required_role(request.method, request.url.path)
    if RANK.get(user.get('member_role') or 'viewer', 0) < RANK[need]:
        raise HTTPException(403, {'code': 'ROLE_FORBIDDEN', 'message': f'Your workspace role ({user.get("member_role")}) cannot perform this action; it needs {need} or higher.'})


def can(user, role): return RANK.get(user.get('member_role') or 'viewer', 0) >= RANK[role]


def _member(c, uid, org_id):
    """A member of `org_id` (not removed), as a dict with the user's fields and this membership's role and status."""
    row = c.execute("SELECT u.id, u.email, u.name, u.org_id AS default_org_id, u.created_at, m.member_role, m.status FROM memberships m JOIN users u ON u.id=m.user_id "
                    "WHERE m.user_id=? AND m.org_id=? AND m.status<>'REMOVED' AND u.removed_at IS NULL", (uid, org_id)).fetchone()
    if not row: raise HTTPException(404, 'Member not found')
    d = dict(row); d['suspended_at'] = 'yes' if d['status'] == 'SUSPENDED' else None
    return d


def _end_sessions(c, uid): c.execute('DELETE FROM sessions WHERE user_id=?', (uid,))


def _audit(c, actor, typ, uid, details): m.event(c, actor, typ, 'user', uid, details)


def _status(t=''):
    return f"CASE WHEN {t}removed_at IS NOT NULL THEN 'REMOVED' WHEN {t}suspended_at IS NOT NULL THEN 'SUSPENDED' ELSE 'ACTIVE' END"


def sync_membership(c, uid):
    """Copy a user's default organization, role and status into `memberships`. `users` mirrors the default membership."""
    c.execute(f"INSERT INTO memberships(org_id,user_id,member_role,status,updated_at) SELECT org_id,id,member_role,{_status()},CURRENT_TIMESTAMP FROM users WHERE id=? "
              "ON CONFLICT(org_id,user_id) DO UPDATE SET member_role=excluded.member_role, status=excluded.status, updated_at=excluded.updated_at", (uid,))


def membership_drift(c):
    """Users whose default-organization membership disagrees with `users`. Always empty unless a write path skipped the mirror."""
    return [dict(r) for r in c.execute(f"SELECT u.id, u.org_id, u.member_role, m.member_role AS m_role, m.status AS m_status FROM users u "
                                       f"LEFT JOIN memberships m ON m.user_id=u.id AND m.org_id=u.org_id "
                                       f"WHERE m.id IS NULL OR m.member_role<>u.member_role OR m.status<>{_status('u.')}").fetchall()]


def _default_org(c, uid): return c.execute('SELECT org_id FROM users WHERE id=?', (uid,)).fetchone()[0]


def set_role(c, uid, org_id, role):
    c.execute('UPDATE memberships SET member_role=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND org_id=?', (role, uid, org_id))
    if _default_org(c, uid) == org_id: c.execute('UPDATE users SET member_role=? WHERE id=?', (role, uid))


def set_status(c, uid, org_id, status):
    c.execute('UPDATE memberships SET status=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND org_id=?', (status, uid, org_id))
    if _default_org(c, uid) == org_id:
        c.execute('UPDATE users SET suspended_at=' + ('CURRENT_TIMESTAMP' if status == 'SUSPENDED' else 'NULL') + ' WHERE id=?', (uid,))


def add_membership(c, uid, org_id, role):
    """Join `org_id` (or rejoin after removal). The user's default organization is unchanged."""
    c.execute("INSERT INTO memberships(org_id,user_id,member_role,status) VALUES(?,?,?,'ACTIVE') "
              "ON CONFLICT(org_id,user_id) DO UPDATE SET member_role=excluded.member_role, status='ACTIVE', updated_at=CURRENT_TIMESTAMP", (org_id, uid, role))


@router.get('/api/members')
def list_members(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        rows = c.execute("SELECT u.id, u.email, u.name, m.member_role, m.status, m.created_at FROM memberships m JOIN users u ON u.id=m.user_id "
                         "WHERE m.org_id=? AND m.status<>'REMOVED' ORDER BY u.id", (u['org_id'],)).fetchall()
        org = c.execute('SELECT id,name,kind FROM organizations WHERE id=?', (u['org_id'],)).fetchone()
    return {'organization': dict(org), 'you': {'id': u['id'], 'member_role': u['member_role']}, 'roles': list(INVITABLE),
            'members': [dict({k: r[k] for k in ('id', 'email', 'name', 'member_role', 'created_at')}, suspended=r['status'] == 'SUSPENDED',
                             suspended_at=None if r['status'] != 'SUSPENDED' else 'suspended') for r in rows]}


class RoleIn(BaseModel):
    member_role: str


@router.patch('/api/members/{uid}')
def change_role(uid: int, body: RoleIn, request: Request):
    u = m.current_user(request)
    if body.member_role not in INVITABLE: raise HTTPException(422, f'Role must be one of {", ".join(INVITABLE)} (use ownership transfer for owner)')
    with m.db() as c:
        t = _member(c, uid, u['org_id'])
        if t['id'] == u['id']: raise HTTPException(409, 'You cannot change your own role')
        if t['member_role'] == 'owner': raise HTTPException(409, 'The owner role changes only through ownership transfer')
        if 'admin' in (t['member_role'], body.member_role) and u['member_role'] != 'owner': raise HTTPException(403, 'Only the owner can grant or remove the admin role')
        set_role(c, uid, u['org_id'], body.member_role)
        _audit(c, u, 'member.role_changed', uid, {'from': t['member_role'], 'to': body.member_role})
    return {'id': uid, 'member_role': body.member_role}


def _guard_target(u, t):
    if t['id'] == u['id']: raise HTTPException(409, 'You cannot do this to your own account')
    if t['member_role'] == 'owner': raise HTTPException(409, 'The owner cannot be suspended or removed; transfer ownership first')
    if t['member_role'] == 'admin' and u['member_role'] != 'owner': raise HTTPException(403, 'Only the owner can suspend or remove an admin')


@router.post('/api/members/{uid}/suspend')
def suspend(uid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        t = _member(c, uid, u['org_id']); _guard_target(u, t)
        set_status(c, uid, u['org_id'], 'SUSPENDED'); _end_sessions(c, uid)
        _audit(c, u, 'member.suspended', uid, {})
    return {'id': uid, 'suspended': True}


@router.post('/api/members/{uid}/reactivate')
def reactivate(uid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        t = _member(c, uid, u['org_id']); _guard_target(u, t)
        set_status(c, uid, u['org_id'], 'ACTIVE'); _audit(c, u, 'member.reactivated', uid, {})
    return {'id': uid, 'suspended': False}


@router.delete('/api/members/{uid}')
def remove(uid: int, request: Request):
    """Removal from an organization. When it was the person's only organization, the account is closed: sessions end, the
    email is freed and the record stays pseudonymised for the audit trail. Otherwise their default moves to another one."""
    u = m.current_user(request)
    with m.db() as c:
        t = _member(c, uid, u['org_id']); _guard_target(u, t)
        c.execute("UPDATE memberships SET status='REMOVED', updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND org_id=?", (uid, u['org_id']))
        others = c.execute("SELECT org_id, member_role FROM memberships WHERE user_id=? AND status='ACTIVE' ORDER BY org_id", (uid,)).fetchall()
        if others:
            if t['default_org_id'] == u['org_id']:
                c.execute('UPDATE users SET org_id=?, member_role=?, suspended_at=NULL WHERE id=?', (others[0]['org_id'], others[0]['member_role'], uid))
            c.execute('UPDATE sessions SET active_org_id=NULL WHERE user_id=? AND active_org_id=?', (uid, u['org_id']))
        else:
            if t['default_org_id'] != u['org_id']:
                c.execute('UPDATE users SET org_id=? WHERE id=?', (u['org_id'], uid))
            c.execute("UPDATE users SET removed_at=CURRENT_TIMESTAMP, suspended_at=COALESCE(suspended_at,CURRENT_TIMESTAMP), member_role=?, email=?, password_hash='' WHERE id=?",
                      (t['member_role'], f'removed+{uid}.{secrets.token_hex(4)}@invalid', uid))
            c.execute('DELETE FROM user_identities WHERE user_id=?', (uid,)); _end_sessions(c, uid)
        _audit(c, u, 'member.removed', uid, {'email_sha256': hashlib.sha256(t['email'].encode()).hexdigest()[:16], 'role': t['member_role'],
                                             'account_closed': not others})
    return {'id': uid, 'removed': True}


@router.post('/api/members/{uid}/transfer-ownership')
def transfer_ownership(uid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        c.lock(f'org-owner-{u["org_id"]}')
        t = _member(c, uid, u['org_id'])
        if t['id'] == u['id']: raise HTTPException(409, 'You already own this workspace')
        if t['member_role'] != 'admin' or t['suspended_at']: raise HTTPException(409, 'Ownership can only be transferred to an active admin')
        if c.execute("SELECT 1 FROM memberships WHERE user_id=? AND org_id=? AND member_role='owner' AND status='ACTIVE'", (u['id'], u['org_id'])).fetchone() is None:
            raise HTTPException(409, 'You are no longer the owner')
        set_role(c, u['id'], u['org_id'], 'admin'); set_role(c, uid, u['org_id'], 'owner')
        _audit(c, u, 'member.ownership_transferred', uid, {'from_user': u['id']})
    return {'owner': uid, 'previous_owner_role': 'admin'}


# ---------------------------------------------------------------- invitations
class InvitationIn(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    member_role: str = 'editor'


def invitation_ttl(): return int(os.getenv('CC_INVITATION_TTL', str(7 * 86400)))


@router.post('/api/invitations')
def create_invitation(body: InvitationIn, request: Request):
    u = m.current_user(request)
    email = body.email.strip().lower()
    if not re.match(r'^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$', email): raise HTTPException(422, 'Enter a valid email address')
    if body.member_role not in INVITABLE: raise HTTPException(422, f'Member role must be one of {", ".join(INVITABLE)}')
    if body.member_role == 'admin' and u['member_role'] != 'owner': raise HTTPException(403, 'Only the owner can invite admins')
    if not m.rate_limit(f'invite:{u["org_id"]}', 30, 3600): raise HTTPException(429, 'Too many invitations this hour')
    with m.db() as c:
        if c.execute("SELECT 1 FROM memberships m JOIN users x ON x.id=m.user_id WHERE x.email=? AND m.org_id=? AND m.status<>'REMOVED'", (email, u['org_id'])).fetchone():
            raise HTTPException(409, {'code': 'ALREADY_MEMBER', 'message': 'This person is already a member of this organization'})
        cap = limits.effective_max_members(c, u['org_id'])
        if limits.org_usage(c, u['org_id'])['members'] >= cap: raise HTTPException(402, {'code': 'PLAN_MEMBERS_LIMIT', 'message': f'Your plan includes {cap} member(s). Upgrade your plan to invite more people.'})
        c.execute("UPDATE invitations SET revoked_at=CURRENT_TIMESTAMP WHERE org_id=? AND lower(email)=? AND accepted_at IS NULL AND revoked_at IS NULL", (u['org_id'], email))
        raw = secrets.token_urlsafe(32)
        inv_id = c.execute('INSERT INTO invitations(org_id,email,role,member_role,token_hash,expires_at,created_by) VALUES(?,?,?,?,?,?,?)',
                           (u['org_id'], email, 'brand', body.member_role, hashlib.sha256(raw.encode()).hexdigest(), int(time.time()) + invitation_ttl(), u['id'])).lastrowid
        org = c.execute('SELECT name FROM organizations WHERE id=?', (u['org_id'],)).fetchone()['name']
        m.event(c, u, 'invitation.created', 'invitation', inv_id, {'member_role': body.member_role})
    emailed = False
    if mailer.enabled():
        from .auth import app_url
        link = f'{app_url()}/login?invited=1'
        try:
            mailer.send(email, f'{u["name"]} invited you to {org} on Visuara',
                        f'{u["name"]} invited you to join the {org} workspace on Visuara as {body.member_role}.\n\nSign in with this email address to accept: {link}\n\nThe invitation expires in {invitation_ttl() // 86400} days.',
                        None, outbox_dir=m.DATA / 'outbox')
            emailed = True
        except mailer.MailError:
            emailed = False
    return {'id': inv_id, 'email': email, 'member_role': body.member_role, 'emailed': emailed,
            'expires_in_seconds': invitation_ttl(),
            'instructions': 'The invitee signs in with this email address (Google, magic link or code) and accepts the invitation.'}


@router.get('/api/invitations/mine')
def my_invitations(request: Request):
    """Pending invitations to other organizations for the signed-in person's email."""
    u = m.current_user(request)
    with m.db() as c:
        rows = c.execute("SELECT i.id, i.member_role, i.expires_at, o.id AS org_id, o.name AS organization, o.kind FROM invitations i JOIN organizations o ON o.id=i.org_id "
                         "WHERE lower(i.email)=? AND i.accepted_at IS NULL AND i.revoked_at IS NULL AND i.expires_at>? ORDER BY i.id DESC", (u['email'].lower(), int(time.time()))).fetchall()
    return [dict(r) for r in rows]


@router.post('/api/invitations/{inv_id}/accept')
def accept_invitation(inv_id: int, request: Request):
    """A signed-in person joins another organization. The invitation must be pending and addressed to their email."""
    u = m.current_user(request)
    with m.db() as c:
        inv = c.execute('SELECT * FROM invitations WHERE id=? AND lower(email)=? AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>?',
                        (inv_id, u['email'].lower(), int(time.time()))).fetchone()
        if not inv: raise HTTPException(404, {'code': 'INVITATION_INVALID', 'message': 'No pending invitation with this id for your email'})
        cap = limits.effective_max_members(c, inv['org_id'])
        if limits.org_usage(c, inv['org_id'])['members'] >= cap: raise HTTPException(402, {'code': 'PLAN_MEMBERS_LIMIT', 'message': 'That organization has no free member places'})
        if c.execute('UPDATE invitations SET accepted_at=CURRENT_TIMESTAMP WHERE id=? AND accepted_at IS NULL', (inv_id,)).rowcount != 1:
            raise HTTPException(409, {'code': 'INVITATION_INVALID', 'message': 'That invitation was already used'})
        add_membership(c, u['id'], inv['org_id'], inv['member_role'])
        m.event(c, dict(u, org_id=inv['org_id']), 'member.joined', 'user', u['id'], {'invitation_id': inv_id, 'member_role': inv['member_role']})
    return {'org_id': inv['org_id'], 'member_role': inv['member_role']}


@router.get('/api/invitations')
def list_invitations(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        rows = c.execute('SELECT id,email,member_role,expires_at,accepted_at,revoked_at,created_at FROM invitations WHERE org_id=? ORDER BY id DESC', (u['org_id'],)).fetchall()
    now = int(time.time())
    return [dict(r, status='accepted' if r['accepted_at'] else 'revoked' if r['revoked_at'] else 'expired' if r['expires_at'] < now else 'pending') for r in rows]


@router.delete('/api/invitations/{inv_id}')
def revoke_invitation(inv_id: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        if c.execute('UPDATE invitations SET revoked_at=CURRENT_TIMESTAMP WHERE id=? AND org_id=? AND accepted_at IS NULL AND revoked_at IS NULL', (inv_id, u['org_id'])).rowcount != 1:
            raise HTTPException(404, 'No pending invitation with this id')
        m.event(c, u, 'invitation.revoked', 'invitation', inv_id, {})
    return {'id': inv_id, 'revoked': True}


@router.get('/api/usage')
def usage(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        used = limits.org_usage(c, u['org_id']); plan, lim = limits.plan_limits(c, u['org_id'])
        caps = {'storage_mb': limits.effective_storage_bytes(c, u['org_id']) // 1048576, 'members': limits.effective_max_members(c, u['org_id']),
                'max_video_mb': limits.effective_video_bytes(c, u['org_id']) // 1048576, 'daily_video_uploads': limits.effective_daily_uploads(c, u['org_id']), 'active_jobs': limits.effective_active_jobs(c, u['org_id'])}
    return {'plan': plan, 'plan_limits': lim, 'effective': caps, 'usage': used, 'limits': limits.snapshot(), 'plans': limits.PLANS}
