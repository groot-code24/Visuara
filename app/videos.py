"""Video assets (Phase 3): content hashes and duplicate detection, metadata read from the file, new versions of a video,
and deletion with storage clean-up.

Rules:
- A video that is in use cannot be replaced or deleted. In use means a placement on it is approved, rendering, rendered
  or delivered, or a marketplace record holds it (inventory that is reserved or sold).
- A new version keeps every earlier version and its analyses (AD-4). The video's current file and metadata move to the
  new version, the legacy keyframes are cleared, and the video must be analysed again.
- Deletion is soft: the row stays for the audit trail with `deleted_at`, and every stored file of the video (all
  versions, keyframes) is removed from storage.
"""
import json, secrets, subprocess
from fractions import Fraction
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from . import main as m, storage, limits, video_versions, outbox, obs

router = APIRouter()
IDLE_PLACEMENT_STATES = ('DRAFT', 'REJECTED', 'REVOKED', 'EXPIRED', 'CANCELLED')


def details(path):
    """Codec, container, audio and aspect ratio, from FFprobe reading the file itself (never the name or the browser's type)."""
    try:
        p = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=format_name:stream=codec_type,codec_name,width,height,sample_aspect_ratio',
                            '-of', 'json', str(path)], capture_output=True, text=True, timeout=20)
        info = json.loads(p.stdout or '{}')
    except Exception:
        return {}
    streams = info.get('streams', [])
    v = next((s for s in streams if s.get('codec_type') == 'video'), {})
    w, h = int(v.get('width') or 0), int(v.get('height') or 0)
    aspect = None
    if w and h:
        try:
            sar = Fraction(*map(int, (v.get('sample_aspect_ratio') or '1:1').split(':'))) if (v.get('sample_aspect_ratio') or '0:1') != '0:1' else Fraction(1)
        except (ValueError, ZeroDivisionError):
            sar = Fraction(1)
        r = (Fraction(w, h) * sar).limit_denominator(100); aspect = f'{r.numerator}:{r.denominator}'
    return {'codec': v.get('codec_name'), 'container': (info.get('format') or {}).get('format_name'), 'has_audio': any(s.get('codec_type') == 'audio' for s in streams),
            'aspect_ratio': aspect}


def duplicates(c, org_id, sha, exclude_video=None):
    if not sha: return []
    rows = c.execute('SELECT DISTINCT v.id FROM video_versions vv JOIN videos v ON v.id=vv.video_id WHERE vv.org_id=? AND vv.content_sha256=? AND v.deleted_at IS NULL ORDER BY v.id',
                     (org_id, sha)).fetchall()
    return [r[0] for r in rows if r[0] != exclude_video]


def in_use(c, video_id):
    """Why the video cannot change, or None."""
    marks = ','.join(f"'{s}'" for s in IDLE_PLACEMENT_STATES)
    n = c.execute(f'SELECT COUNT(*) FROM placements WHERE video_id=? AND status NOT IN ({marks})', (video_id,)).fetchone()[0]
    if n: return f'{n} placement(s) on this video are approved, rendering, rendered or delivered'
    if 'inventory_items' in c.tables():
        n = c.execute("SELECT COUNT(*) FROM inventory_items WHERE video_id=? AND status IN ('RESERVED','SOLD')", (video_id,)).fetchone()[0]
        if n: return f'{n} inventory item(s) from this video are reserved or sold'
    return None


def _video(c, vid, org_id):
    v = c.execute('SELECT * FROM videos WHERE id=? AND org_id=? AND deleted_at IS NULL', (vid, org_id)).fetchone()
    if not v: raise HTTPException(404, 'Resource not found')
    return dict(v)


@router.get('/api/videos/{vid}/versions')
def list_versions(vid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        v = _video(c, vid, u['org_id'])
        rows = c.execute('SELECT id,version,content_sha256,size_bytes,duration,width,height,fps,codec,container,has_audio,aspect_ratio,created_at FROM video_versions '
                         'WHERE video_id=? ORDER BY version', (vid,)).fetchall()
    return {'video_id': vid, 'current_version': v['current_version'], 'versions': [dict(r, has_audio=None if r['has_audio'] is None else bool(r['has_audio'])) for r in rows]}


@router.post('/api/videos/{vid}/versions')
def new_version(vid: int, request: Request, video: UploadFile = File(...), license_confirmed: bool = Form(...)):
    u = m.current_user(request)
    if not license_confirmed: raise HTTPException(400, 'Confirm that you own or are authorized to license this video')
    if video.content_type not in m.VIDEO_TYPES: raise HTTPException(415, 'Supported formats: MP4, MOV, WebM')
    with m.db() as c:
        _video(c, vid, u['org_id'])
        why = in_use(c, vid)
        if why: raise HTTPException(409, {'code': 'VIDEO_IN_USE', 'message': f'A new version cannot replace this video: {why}'})
    path = m.UPLOADS / f'video_{secrets.token_hex(8)}_{m.safe_name(video.filename or "upload.mp4")}'
    size = m.receive_upload(video, path)
    return add_version(u, vid, path, size)


def add_version(u, vid, path, size, persisted=False):
    """Validate a received file and record it as the video's next version. Used by the multipart route above and by
    direct-to-storage uploads that name an existing video (`app/uploads.py`)."""
    local = Path(storage.local(str(path)))
    try:
        duration, width, height, fps = m.validate_video_file(u, local, size)
    except HTTPException:
        if persisted: storage.delete(str(path))
        raise
    sha = video_versions.sha256_file(local); meta = details(local)
    if not persisted: storage.persist(path)
    with m.db() as c:
        c.lock(f'video-version-{vid}')
        _video(c, vid, u['org_id'])
        why = in_use(c, vid)
        if why: storage.delete(str(path)); raise HTTPException(409, {'code': 'VIDEO_IN_USE', 'message': f'A new version cannot replace this video: {why}'})
        version = c.execute('SELECT MAX(version) FROM video_versions WHERE video_id=?', (vid,)).fetchone()[0] + 1
        vvid = video_versions.record(c, vid, u['org_id'], u['id'], version, path, size, duration, width, height, fps, sha, meta)
        c.execute('UPDATE videos SET source_path=?, duration=?, width=?, height=?, fps=?, size_bytes=?, current_version=?, analyzed=0 WHERE id=?',
                  (str(path), duration, width, height, fps, size, version, vid))
        c.execute('DELETE FROM frames WHERE video_id=?', (vid,))          # the legacy current view; analyses of earlier versions stay
        dup = duplicates(c, u['org_id'], sha, exclude_video=vid)
        m.event(c, u, 'video.version_added', 'video', vid, {'version': version, 'video_version_id': vvid, 'duplicate_of': dup})
    return {'video_id': vid, 'version': version, 'video_version_id': vvid, 'content_sha256': sha, 'duplicate_of': dup, **meta}


@router.delete('/api/videos/{vid}')
def delete_video(vid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        c.lock(f'video-version-{vid}')
        _video(c, vid, u['org_id'])
        why = in_use(c, vid)
        if why: raise HTTPException(409, {'code': 'VIDEO_IN_USE', 'message': f'This video cannot be deleted: {why}'})
        paths = [r[0] for r in c.execute('SELECT source_path FROM video_versions WHERE video_id=?', (vid,)).fetchall()]
        paths += [r[0] for r in c.execute('SELECT image_path FROM frames WHERE video_id=?', (vid,)).fetchall()]
        paths += [r[0] for r in c.execute('SELECT f.image_path FROM scene_frames f JOIN scenes s ON s.id=f.scene_id WHERE s.video_id=?', (vid,)).fetchall()]
        c.execute('UPDATE videos SET deleted_at=CURRENT_TIMESTAMP, size_bytes=0 WHERE id=?', (vid,))
        c.execute('UPDATE video_versions SET size_bytes=0 WHERE video_id=?', (vid,))
        c.execute('DELETE FROM frames WHERE video_id=?', (vid,))
        if 'inventory_items' in c.tables():
            c.execute("UPDATE inventory_items SET status='WITHDRAWN', updated_at=CURRENT_TIMESTAMP WHERE video_id=? AND status IN ('DRAFT','AVAILABLE')", (vid,))
        outbox.emit(c, 'video.deleted', 'video', vid, {'video_id': vid}, org_id=u['org_id'])
        m.event(c, u, 'video.deleted', 'video', vid, {'files': len(set(paths))})
    removed = 0
    for p in set(paths):
        try: storage.delete(p); m.Path(p).unlink(missing_ok=True); removed += 1
        except Exception: obs.warn('a deleted video file could not be removed from storage', video_id=vid)
    return {'id': vid, 'deleted': True, 'files_removed': removed}


def backfill_hashes(limit=2):
    """Hash versions recorded without one (uploaded before Phase 1). Run by database maintenance, a few per run."""
    done = 0
    with m.db() as c:
        rows = c.execute('SELECT vv.id, vv.source_path FROM video_versions vv JOIN videos v ON v.id=vv.video_id WHERE vv.content_sha256 IS NULL AND v.deleted_at IS NULL ORDER BY vv.id LIMIT ?',
                         (limit,)).fetchall()
    for r in rows:
        try:
            local = storage.local(r['source_path'])
            sha = video_versions.sha256_file(local); meta = details(local)
        except Exception:
            obs.warn('video version could not be hashed', video_version_id=r['id']); continue
        with m.db() as c:
            c.execute('UPDATE video_versions SET content_sha256=?, codec=COALESCE(codec,?), container=COALESCE(container,?), has_audio=COALESCE(has_audio,?), '
                      'aspect_ratio=COALESCE(aspect_ratio,?) WHERE id=? AND content_sha256 IS NULL',
                      (sha, meta.get('codec'), meta.get('container'), None if 'has_audio' not in meta else int(meta['has_audio']), meta.get('aspect_ratio'), r['id']))
        done += 1
    return done


def reject_duplicate(c, org_id, sha, reject: Optional[bool]):
    dup = duplicates(c, org_id, sha)
    if dup and reject: raise HTTPException(409, {'code': 'DUPLICATE_VIDEO', 'message': 'This organization already has a video with exactly this content', 'duplicate_of': dup})
    return dup
