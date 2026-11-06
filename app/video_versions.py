"""Video versions (Phase 1): each upload is version 1 of its video, with the SHA-256 of the file's content.

Videos uploaded before this table existed were backfilled as version 1 without a hash, because their files may be
in object storage. Phase 3 hashes those, adds duplicate detection and new versions of an existing video.
"""
import hashlib

from . import outbox


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(chunk), b''): h.update(block)
    return h.hexdigest()


def record(c, video_id, org_id, user_id, version, source_path, size_bytes, duration, width, height, fps, content_sha256, meta=None):
    meta = meta or {}
    vvid = c.execute('INSERT INTO video_versions(org_id,video_id,version,source_path,content_sha256,size_bytes,duration,width,height,fps,codec,container,has_audio,aspect_ratio,created_by) '
                     'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (org_id, video_id, version, str(source_path), content_sha256, size_bytes, duration, width, height, fps or 0, meta.get('codec'), meta.get('container'),
                      None if meta.get('has_audio') is None else int(meta['has_audio']), meta.get('aspect_ratio'), user_id)).lastrowid
    outbox.emit(c, 'video.version.created', 'video', video_id, {'video_id': video_id, 'video_version_id': vvid, 'version': version,
                                                                 'content_sha256': content_sha256, 'size_bytes': size_bytes}, org_id=org_id)
    return vvid


def record_upload(c, video_id, org_id, user_id, source_path, size_bytes, duration, width, height, fps, content_sha256, meta=None):
    return record(c, video_id, org_id, user_id, 1, source_path, size_bytes, duration, width, height, fps, content_sha256, meta)
