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
    ({'POST'}, r'^/api/members/\d+/transfer-ownership$', 'owner'),
    ({'GET'}, r'^/api/(members|invitations|usage)$', 'viewer'),
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
    row = c.execute('SELECT * FROM users WHERE id=? AND org_id=? AND removed_at IS NULL', (uid, org_id)).fetchone()
    if not row: raise HTTPException(404, 'Member not found')
    return dict(row)


def _end_sessions(c, uid): c.execute('DELETE FROM sessions WHERE user_id=?', (uid,))


def _audit(c, actor, typ, uid, details): m.event(c, actor, typ, 'user', uid, details)


@router.get('/api/members')
def list_members(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        rows = c.execute('SELECT id,email,name,member_role,suspended_at,created_at FROM users WHERE org_id=? AND removed_at IS NULL ORDER BY id', (u['org_id'],)).fetchall()
        org = c.execute('SELECT id,name FROM organizations WHERE id=?', (u['org_id'],)).fetchone()
    return {'organization': dict(org), 'you': {'id': u['id'], 'member_role': u['member_role']}, 'roles': list(INVITABLE),
            'members': [dict(r, suspended=bool(r['suspended_at'])) for r in rows]}


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
        c.execute('UPDATE users SET member_role=? WHERE id=?', (body.member_role, uid))
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
        c.execute('UPDATE users SET suspended_at=CURRENT_TIMESTAMP WHERE id=?', (uid,)); _end_sessions(c, uid)
        _audit(c, u, 'member.suspended', uid, {})
    return {'id': uid, 'suspended': True}


@router.post('/api/members/{uid}/reactivate')
def reactivate(uid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        t = _member(c, uid, u['org_id']); _guard_target(u, t)
        c.execute('UPDATE users SET suspended_at=NULL WHERE id=?', (uid,)); _audit(c, u, 'member.reactivated', uid, {})
    return {'id': uid, 'suspended': False}


@router.delete('/api/members/{uid}')
def remove(uid: int, request: Request):
    """Removal ends all sessions and frees the email address; the record stays (pseudonymised) for the audit trail."""
    u = m.current_user(request)
    with m.db() as c:
        t = _member(c, uid, u['org_id']); _guard_target(u, t)
        c.execute("UPDATE users SET removed_at=CURRENT_TIMESTAMP, suspended_at=COALESCE(suspended_at,CURRENT_TIMESTAMP), email=?, password_hash='' WHERE id=?",
                  (f'removed+{uid}.{secrets.token_hex(4)}@invalid', uid))
        c.execute('DELETE FROM user_identities WHERE user_id=?', (uid,)); _end_sessions(c, uid)
        _audit(c, u, 'member.removed', uid, {'email_sha256': hashlib.sha256(t['email'].encode()).hexdigest()[:16], 'role': t['member_role']})
    return {'id': uid, 'removed': True}


@router.post('/api/members/{uid}/transfer-ownership')
def transfer_ownership(uid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        c.lock(f'org-owner-{u["org_id"]}')
        t = _member(c, uid, u['org_id'])
        if t['id'] == u['id']: raise HTTPException(409, 'You already own this workspace')
        if t['member_role'] != 'admin' or t['suspended_at']: raise HTTPException(409, 'Ownership can only be transferred to an active admin')
        if c.execute("UPDATE users SET member_role='admin' WHERE id=? AND member_role='owner'", (u['id'],)).rowcount != 1: raise HTTPException(409, 'You are no longer the owner')
        c.execute("UPDATE users SET member_role='owner' WHERE id=?", (uid,))
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
        if c.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone(): raise HTTPException(409, 'This email already has an account')
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
