"""Direct-to-bucket video uploads.

Large videos never pass through the web tier (or a Vercel function): the browser asks for an upload authorisation,
PUTs the file straight into the private bucket with a short-lived presigned URL, then reports completion. The server
checks the object's size, downloads it, scans and probes it exactly like a server-side upload, and only then records
the video. Sessions that are never completed expire and their objects are deleted (see maintenance.expire_uploads).
Without object storage the API answers `method: server` and the browser uses POST /api/videos instead.
"""
import secrets, time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import main as m, blobstore, limits, storage

router = APIRouter()
DIRECT_TYPES = ('video/mp4', 'video/quicktime', 'video/webm')


class UploadInit(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    filename: str = Field(default='upload.mp4', max_length=200)
    content_type: str
    size_bytes: int = Field(gt=0)
    license_confirmed: bool
    license_expires: Optional[str] = None
    video_id: Optional[int] = None                 # a new version of this existing video, instead of a new video


@router.post('/api/uploads/video')
def init_video_upload(body: UploadInit, request: Request):
    u = m.current_user(request)
    if not body.license_confirmed: raise HTTPException(400, 'Confirm that you own or are authorized to license this video')
    if body.content_type not in DIRECT_TYPES: raise HTTPException(415, 'Supported formats: MP4, MOV, WebM')
    if body.size_bytes > limits.max_video_bytes(): raise HTTPException(413, f'Video upload limit is {limits.max_video_bytes() // 1048576} MB')
    with m.db() as c:
        limits.check_video_size(c, u['org_id'], body.size_bytes); max_bytes = limits.effective_video_bytes(c, u['org_id'])
        if body.video_id is not None:
            from .videos import _video, in_use
            _video(c, body.video_id, u['org_id'])
            why = in_use(c, body.video_id)
            if why: raise HTTPException(409, {'code': 'VIDEO_IN_USE', 'message': f'A new version cannot replace this video: {why}'})
    if not storage.enabled():
        return {'method': 'server', 'upload_url': f'/api/videos/{body.video_id}/versions' if body.video_id else '/api/videos', 'max_bytes': max_bytes}
    if not m.rate_limit(f'upload-init:{u["org_id"]}', 60, 3600): raise HTTPException(429, 'Too many uploads started this hour')
    ttl = limits.upload_session_ttl()
    path = m.UPLOADS / f'video_{secrets.token_hex(8)}_{m.safe_name(body.filename)}'
    with m.db() as c:
        limits.check_daily_uploads(c, u['org_id']); limits.check_storage(c, u['org_id'], body.size_bytes)
        sid = c.execute('INSERT INTO upload_sessions(org_id,user_id,kind,path,title,content_type,expected_bytes,license_expires,expires_at,target_video_id) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (u['org_id'], u['id'], 'video_version' if body.video_id else 'video', str(path), body.title.strip(), body.content_type, body.size_bytes,
                         body.license_expires, int(time.time()) + ttl, body.video_id)).lastrowid
        m.event(c, u, 'upload.started', 'upload', sid, {'bytes': body.size_bytes})
    if storage.uses_blob():
        key = storage.key_for(str(path))
        return {'method': 'blob', 'upload_id': sid, 'pathname': key, 'content_type': body.content_type, 'client_token': blobstore.client_token(key, body.content_type, body.size_bytes, min(ttl, 3600)),
                'expires_in_seconds': min(ttl, 3600), 'max_bytes': max_bytes}
    return {'method': 'direct', 'upload_id': sid, 'put_url': storage.presigned_put(str(path), body.content_type, ttl=min(ttl, 3600)),
            'headers': {'Content-Type': body.content_type}, 'expires_in_seconds': min(ttl, 3600), 'max_bytes': max_bytes}


def _session(c, sid, org_id):
    s = c.execute('SELECT * FROM upload_sessions WHERE id=? AND org_id=?', (sid, org_id)).fetchone()
    if not s: raise HTTPException(404, 'Upload not found')
    return dict(s)


def _fail(sid, u, code, message, delete_path=None):
    with m.db() as c:
        c.execute("UPDATE upload_sessions SET status='FAILED',error=?,finished_at=CURRENT_TIMESTAMP WHERE id=?", (f'{code}: {message}'[:500], sid))
        m.event(c, u, 'upload.failed', 'upload', sid, {'code': code})
    if delete_path: storage.delete(delete_path)


@router.post('/api/uploads/{sid}/complete')
def complete_upload(sid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        s = _session(c, sid, u['org_id'])
        if s['status'] == 'COMPLETED': return {'id': s['result_id'], 'upload_id': sid, 'status': 'COMPLETED'}
        if s['status'] != 'PENDING': raise HTTPException(409, f'Upload is {s["status"].lower()}; start a new upload')
        if s['expires_at'] < time.time(): raise HTTPException(410, 'Upload authorisation expired; start a new upload')
        if c.execute("UPDATE upload_sessions SET status='PROCESSING' WHERE id=? AND status='PENDING'", (sid,)).rowcount != 1:
            raise HTTPException(409, 'Upload is already being processed')
    size = storage.head(s['path'])
    if size is None:      # nothing in the bucket yet: let the browser retry the PUT, then complete again
        with m.db() as c: c.execute("UPDATE upload_sessions SET status='PENDING' WHERE id=? AND status='PROCESSING'", (sid,))
        raise HTTPException(409, {'code': 'UPLOAD_NOT_RECEIVED', 'message': 'The file has not reached storage yet. Retry the upload.'})
    if size != s['expected_bytes'] or size > limits.max_video_bytes():
        _fail(sid, u, 'SIZE_MISMATCH', f'expected {s["expected_bytes"]} bytes, received {size}', s['path'])
        raise HTTPException(400, {'code': 'SIZE_MISMATCH', 'message': 'The uploaded file does not match the declared size. Start a new upload.'})
    local = storage.local(s['path'])
    if not Path(local).exists():
        with m.db() as c: c.execute("UPDATE upload_sessions SET status='PENDING' WHERE id=? AND status='PROCESSING'", (sid,))
        raise HTTPException(503, {'code': 'STORAGE_UNAVAILABLE', 'message': 'Storage is temporarily unavailable. Try completing the upload again shortly.'})
    try:
        if s['kind'] == 'video_version':
            from .videos import add_version
            v = add_version(u, s['target_video_id'], Path(s['path']), size, persisted=True)
            out = dict(v, id=v['video_id'])
        else:
            out = m.ingest_video(u, Path(s['path']), s['title'], s['license_expires'], size, persisted=True)
    except HTTPException as e:
        _fail(sid, u, 'REJECTED', str(e.detail)); raise
    except Exception:
        _fail(sid, u, 'PROCESSING_ERROR', 'unexpected error', s['path']); raise
    with m.db() as c:
        c.execute("UPDATE upload_sessions SET status='COMPLETED',result_id=?,finished_at=CURRENT_TIMESTAMP WHERE id=?", (out['id'], sid))
    return {**out, 'upload_id': sid, 'status': 'COMPLETED'}


@router.post('/api/uploads/{sid}/cancel')
def cancel_upload(sid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        s = _session(c, sid, u['org_id'])
        if c.execute("UPDATE upload_sessions SET status='CANCELLED',finished_at=CURRENT_TIMESTAMP WHERE id=? AND status='PENDING'", (sid,)).rowcount != 1:
            raise HTTPException(409, f'Upload is {s["status"].lower()} and cannot be cancelled')
        m.event(c, u, 'upload.cancelled', 'upload', sid, {})
    storage.delete(s['path'])
    return {'upload_id': sid, 'status': 'CANCELLED'}


@router.get('/api/uploads')
def list_uploads(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        rows = c.execute('SELECT id,kind,title,expected_bytes,status,result_id,error,created_at,finished_at FROM upload_sessions WHERE org_id=? ORDER BY id DESC LIMIT 50', (u['org_id'],)).fetchall()
    return [dict(r) for r in rows]
