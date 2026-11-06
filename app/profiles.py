"""Brand and publisher profiles; rights records with evidence and verification (Phase 6).

A rights record states who holds the rights to a video, for which channels, territories and dates, with an evidence
file (a licence or contract). It starts PENDING. A platform administrator verifies or rejects it, and that decision is
recorded with who made it. A VERIFIED record that is past its end date counts as expired. Listing inventory from a video
needs a verified, unexpired record (see `inventory.eligibility`).
"""
import hashlib, json, secrets, time
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from . import main as m, storage
from .orgs import require_platform_admin

router = APIRouter()
EVIDENCE_TYPES = {b'%PDF': 'application/pdf', b'\x89PNG': 'image/png', b'\xff\xd8\xff': 'image/jpeg'}
MAX_EVIDENCE_BYTES = 10 * 1048576


class ProfileIn(BaseModel):
    display_name: str = Field(min_length=2, max_length=80)
    website: str = Field(default='', max_length=200)
    description: str = Field(default='', max_length=2000)
    categories: list[str] = Field(default_factory=list, max_length=20)
    excluded_terms: list[str] = Field(default_factory=list, max_length=50)
    audience: dict = Field(default_factory=dict)


def _profile_table(kind):
    if kind in ('brand', 'agency'): return 'brand_profiles'
    if kind == 'publisher': return 'publisher_profiles'
    raise HTTPException(409, {'code': 'NO_PROFILE_FOR_KIND', 'message': 'Direct organizations have no marketplace profile; change the organization kind first'})


def _load(c, table, org_id):
    r = c.execute(f'SELECT * FROM {table} WHERE org_id=?', (org_id,)).fetchone()
    if not r: return None
    d = dict(r)
    for k in [k for k in d if k.endswith('_json')]: d[k[:-5]] = json.loads(d.pop(k) or 'null')
    return d


@router.get('/api/profile')
def get_profile(request: Request):
    u = m.current_user(request)
    with m.db() as c: return {'kind': u['org_kind'], 'profile': _load(c, _profile_table(u['org_kind']), u['org_id'])}


@router.put('/api/profile')
def put_profile(body: ProfileIn, request: Request):
    u = m.current_user(request); table = _profile_table(u['org_kind'])
    from .decisions import CATEGORIES
    bad = [x for x in body.categories if table == 'brand_profiles' and x not in CATEGORIES]
    if bad: raise HTTPException(422, {'code': 'INVALID_CATEGORY', 'message': f'Unknown product categories: {bad}'})
    if body.website and not body.website.startswith(('https://', 'http://')): raise HTTPException(422, {'code': 'INVALID_WEBSITE', 'message': 'The website must start with https://'})
    with m.db() as c:
        if table == 'brand_profiles':
            c.execute('INSERT INTO brand_profiles(org_id,display_name,website,description,categories_json,excluded_terms_json) VALUES(?,?,?,?,?,?) '
                      'ON CONFLICT(org_id) DO UPDATE SET display_name=excluded.display_name, website=excluded.website, description=excluded.description, '
                      'categories_json=excluded.categories_json, excluded_terms_json=excluded.excluded_terms_json, updated_at=CURRENT_TIMESTAMP',
                      (u['org_id'], body.display_name.strip(), body.website, body.description, json.dumps(body.categories), json.dumps([t.lower() for t in body.excluded_terms])))
        else:
            c.execute('INSERT INTO publisher_profiles(org_id,display_name,website,description,content_categories_json,audience_json) VALUES(?,?,?,?,?,?) '
                      'ON CONFLICT(org_id) DO UPDATE SET display_name=excluded.display_name, website=excluded.website, description=excluded.description, '
                      'content_categories_json=excluded.content_categories_json, audience_json=excluded.audience_json, updated_at=CURRENT_TIMESTAMP',
                      (u['org_id'], body.display_name.strip(), body.website, body.description, json.dumps(body.categories), json.dumps(body.audience)))
        m.event(c, u, 'profile.updated', 'organization', u['org_id'], {'table': table})
        return {'kind': u['org_kind'], 'profile': _load(c, table, u['org_id'])}


@router.get('/api/publishers/{org_id}/profile')
def publisher_profile(org_id: int, request: Request):
    """Public to signed-in members of any organization: what a brand sees about a publisher."""
    m.current_user(request)
    with m.db() as c:
        p = _load(c, 'publisher_profiles', org_id)
        if not p: raise HTTPException(404, 'Resource not found')
        p['listed_items'] = c.execute("SELECT COUNT(*) FROM inventory_items WHERE org_id=? AND status='AVAILABLE'", (org_id,)).fetchone()[0]
    return p


# ---------------------------------------------------------------- rights records
def _sniff(head):
    return next((t for sig, t in EVIDENCE_TYPES.items() if head.startswith(sig)), None)


def rights_view(r):
    d = dict(r); d['channels'] = json.loads(d.pop('channels_json')); d['territories'] = json.loads(d.pop('territories_json')); d.pop('evidence_path', None)
    if d['status'] == 'VERIFIED' and d['expires_on'] and d['expires_on'] < time.strftime('%Y-%m-%d'): d['status'] = 'EXPIRED'
    return d


@router.post('/api/videos/{vid}/rights')
def add_rights(vid: int, request: Request, holder: str = Form(..., min_length=2, max_length=200), channels: str = Form('["social","web"]'),
               territories: str = Form('["WORLDWIDE"]'), starts_on: Optional[str] = Form(None), expires_on: Optional[str] = Form(None), evidence: UploadFile = File(...)):
    u = m.current_user(request)
    try:
        ch = json.loads(channels); te = json.loads(territories)
        assert isinstance(ch, list) and isinstance(te, list) and all(isinstance(x, str) for x in ch + te) and ch and te
    except Exception:
        raise HTTPException(422, {'code': 'INVALID_SCOPE', 'message': 'channels and territories must be non-empty JSON lists of strings'})
    for d in (starts_on, expires_on):
        if d and not (len(d) == 10 and d[4] == '-' and d[7] == '-'): raise HTTPException(422, {'code': 'INVALID_DATE', 'message': 'Dates are YYYY-MM-DD'})
    if starts_on and expires_on and expires_on < starts_on: raise HTTPException(422, {'code': 'INVALID_DATE', 'message': 'The end date is before the start date'})
    data = evidence.file.read(MAX_EVIDENCE_BYTES + 1)
    if len(data) > MAX_EVIDENCE_BYTES: raise HTTPException(413, 'Evidence files are limited to 10 MB')
    kind = _sniff(data[:8])
    if not kind: raise HTTPException(415, {'code': 'EVIDENCE_TYPE', 'message': 'Evidence must be a PDF, PNG or JPEG file (checked from its contents)'})
    with m.db() as c: m.owned(c, 'videos', vid, u['org_id'])
    path = m.DATA / 'rights' / f'r{u["org_id"]}_{secrets.token_hex(8)}{"." + kind.split("/")[1]}'
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); storage.persist(path)
    with m.db() as c:
        rid = c.execute('INSERT INTO rights_records(org_id,video_id,holder,channels_json,territories_json,starts_on,expires_on,evidence_path,evidence_sha256,evidence_name,evidence_type,created_by) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (u['org_id'], vid, holder.strip(), json.dumps(ch), json.dumps(te), starts_on, expires_on, str(path),
                                                            hashlib.sha256(data).hexdigest(), (evidence.filename or 'evidence')[:120], kind, u['id'])).lastrowid
        m.event(c, u, 'rights.submitted', 'rights_record', rid, {'video_id': vid})
        return rights_view(c.execute('SELECT * FROM rights_records WHERE id=?', (rid,)).fetchone())


@router.get('/api/videos/{vid}/rights')
def list_rights(vid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        m.owned(c, 'videos', vid, u['org_id'])
        return [rights_view(r) for r in c.execute('SELECT * FROM rights_records WHERE video_id=? ORDER BY id DESC', (vid,)).fetchall()]


@router.post('/api/rights/{rid}/revoke')
def revoke_rights(rid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        if c.execute("UPDATE rights_records SET status='REVOKED' WHERE id=? AND org_id=? AND status IN ('PENDING','VERIFIED')", (rid, u['org_id'])).rowcount != 1:
            raise HTTPException(404, 'No active rights record with this id')
        vid = c.execute('SELECT video_id FROM rights_records WHERE id=?', (rid,)).fetchone()[0]
        c.execute("UPDATE inventory_items SET status='WITHDRAWN', version=version+1, updated_at=CURRENT_TIMESTAMP WHERE video_id=? AND status='AVAILABLE' "
                  "AND NOT EXISTS (SELECT 1 FROM rights_records r WHERE r.video_id=? AND r.status='VERIFIED')", (vid, vid))
        m.event(c, u, 'rights.revoked', 'rights_record', rid, {})
    return {'id': rid, 'status': 'REVOKED'}


@router.get('/api/admin/rights')
def admin_rights(request: Request, status: str = 'PENDING'):
    u = m.current_user(request); require_platform_admin(u)
    with m.db() as c:
        return [dict(rights_view(r), organization=r['org_name']) for r in c.execute(
            'SELECT r.*, o.name AS org_name FROM rights_records r JOIN organizations o ON o.id=r.org_id WHERE r.status=? ORDER BY r.id', (status,)).fetchall()]


@router.get('/api/admin/rights/{rid}/evidence')
def admin_evidence(rid: int, request: Request):
    u = m.current_user(request); require_platform_admin(u)
    with m.db() as c: r = c.execute('SELECT evidence_path, evidence_type FROM rights_records WHERE id=?', (rid,)).fetchone()
    if not r: raise HTTPException(404, 'Resource not found')
    return storage.response(r['evidence_path'], media_type=r['evidence_type'])


class VerifyIn(BaseModel):
    decision: str
    notes: str = Field(default='', max_length=2000)


@router.post('/api/admin/rights/{rid}/decision')
def decide_rights(rid: int, body: VerifyIn, request: Request):
    u = m.current_user(request); require_platform_admin(u)
    if body.decision not in ('VERIFIED', 'REJECTED'): raise HTTPException(422, {'code': 'INVALID_DECISION', 'message': 'decision must be VERIFIED or REJECTED'})
    with m.db() as c:
        r = c.execute('SELECT * FROM rights_records WHERE id=?', (rid,)).fetchone()
        if not r: raise HTTPException(404, 'Resource not found')
        if r['org_id'] == u['org_id']: raise HTTPException(403, {'code': 'SELF_VERIFICATION', 'message': 'Rights submitted by your own organization are verified by another administrator'})
        if c.execute("UPDATE rights_records SET status=?, verified_by=?, verified_at=CURRENT_TIMESTAMP, review_notes=? WHERE id=? AND status='PENDING'",
                     (body.decision, u['id'], body.notes, rid)).rowcount != 1:
            raise HTTPException(409, {'code': 'ALREADY_DECIDED', 'message': f'This record is {r["status"]}'})
        c.execute("INSERT INTO events(org_id,user_id,event_type,entity_type,entity_id,details) VALUES(?,?,?,?,?,?)",
                  (r['org_id'], u['id'], f'rights.{body.decision.lower()}', 'rights_record', rid, json.dumps({'by_platform_admin': u['id']})))
        from .inventory import notify
        notify(c, r['org_id'], f'rights.{body.decision.lower()}', f'Rights record {rid} was {body.decision.lower()}', body.notes, 'rights_record', rid)
    return {'id': rid, 'status': body.decision}
