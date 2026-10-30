"""Media storage: local working copy plus optional private S3-compatible object storage.

Without CC_S3_BUCKET the local data directory is the only store (single host). With it:
  * every durable file (uploads, keyframes, analyses, renders) is written through to the private bucket;
  * a host that lacks a file restores it from the bucket on first use, so API and worker hosts need no shared disk;
  * downloads and streams are served by redirecting to short-lived presigned URLs, after all access checks pass.
Works with AWS S3, Cloudflare R2, Backblaze B2, MinIO and other S3-compatible services (CC_S3_ENDPOINT_URL).
"""
import mimetypes, os, threading, time
from pathlib import Path

KNOWN_DIRS = ('uploads', 'outputs', 'frames', 'analyses')
_client = None
_public_client = None
_lock = threading.Lock()


def bucket(): return os.getenv('CC_S3_BUCKET', '')
def enabled(): return bool(bucket())
def presign_ttl(): return int(os.getenv('CC_S3_PRESIGN_TTL', '300'))


def _make_client(endpoint):
    import boto3
    from botocore.config import Config
    return boto3.client('s3', endpoint_url=endpoint or None, region_name=os.getenv('CC_S3_REGION') or None,
                        config=Config(signature_version='s3v4', retries={'max_attempts': 5, 'mode': 'standard'},
                                      connect_timeout=10, read_timeout=60, s3={'addressing_style': os.getenv('CC_S3_ADDRESSING', 'auto')}))


def client():
    global _client
    with _lock:
        if _client is None: _client = _make_client(os.getenv('CC_S3_ENDPOINT_URL'))
        return _client


def public_client():
    """Client used only to sign URLs the browser will open. Differs from client() when the server reaches storage
    on an internal address (e.g. http://minio:9000 in Docker) but browsers need a public one (CC_S3_PUBLIC_ENDPOINT_URL)."""
    global _public_client
    pub = os.getenv('CC_S3_PUBLIC_ENDPOINT_URL')
    if not pub: return client()
    with _lock:
        if _public_client is None: _public_client = _make_client(pub)
        return _public_client


def reset():
    global _client, _public_client
    with _lock: _client = None; _public_client = None


def _data():
    from . import main
    return main.DATA


def key_for(path):
    """Object key = the path from the first known media directory, so keys stay valid when the data directory moves."""
    parts = Path(path).parts
    for i, part in enumerate(parts):
        if part in KNOWN_DIRS:
            prefix = os.getenv('CC_S3_PREFIX', '').strip('/')
            return '/'.join(([prefix] if prefix else []) + list(parts[i:]))
    return None


def _local_for_key(path):
    parts = Path(path).parts
    for i, part in enumerate(parts):
        if part in KNOWN_DIRS: return _data().joinpath(*parts[i:])
    return Path(path)


def persist(path):
    """Write a finished local file through to object storage (no-op without a bucket)."""
    if not enabled() or not path: return
    key = key_for(path)
    if not key: return
    extra = {'ContentType': mimetypes.guess_type(str(path))[0] or 'application/octet-stream'}
    if os.getenv('CC_S3_SSE', 'AES256'): extra['ServerSideEncryption'] = os.getenv('CC_S3_SSE', 'AES256')
    client().upload_file(str(path), bucket(), key, ExtraArgs=extra)


def local(path):
    """Local filesystem path for a stored file, restoring it from object storage when this host does not have it."""
    if not path: return path
    p = Path(path)
    if p.exists(): _touch(p); return str(p)
    target = _local_for_key(path)
    if target.exists(): _touch(target); return str(target)
    if enabled() and key_for(path):
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + f'.{os.getpid()}.{threading.get_ident()}.dl')
        try:
            client().download_file(bucket(), key_for(path), str(tmp)); os.replace(tmp, target)
            return str(target)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
    return str(p)


def exists(path):
    """True if the file is available locally or in object storage (checked without downloading)."""
    if not path: return False
    if Path(path).exists() or _local_for_key(path).exists(): return True
    if enabled() and key_for(path):
        try: client().head_object(Bucket=bucket(), Key=key_for(path)); return True
        except Exception: return False
    return False


def delete(path):
    if not path: return
    Path(path).unlink(missing_ok=True)
    if enabled() and key_for(path):
        try: client().delete_object(Bucket=bucket(), Key=key_for(path))
        except Exception: pass


def presigned_url(path, filename=None, media_type=None, ttl=None):
    params = {'Bucket': bucket(), 'Key': key_for(path)}
    if filename: params['ResponseContentDisposition'] = f'attachment; filename="{filename}"'
    if media_type: params['ResponseContentType'] = media_type
    return public_client().generate_presigned_url('get_object', Params=params, ExpiresIn=ttl or presign_ttl())


def presigned_put(path, content_type, ttl=None):
    """Short-lived URL that lets the browser PUT one object directly into the private bucket."""
    params = {'Bucket': bucket(), 'Key': key_for(path), 'ContentType': content_type}
    return public_client().generate_presigned_url('put_object', Params=params, ExpiresIn=ttl or presign_ttl(), HttpMethod='PUT')


def head(path):
    """Size in bytes of the stored object, or None when it does not exist."""
    try: return int(client().head_object(Bucket=bucket(), Key=key_for(path))['ContentLength'])
    except Exception: return None


def _touch(p):
    try: os.utime(p)        # marks the cached copy as recently used for cleanup_cache()
    except OSError: pass


def cleanup_cache(max_age_hours=None, max_total_mb=None, min_idle_seconds=None, dry_run=False):
    """Evict local copies of media that are safely stored in the bucket, least recently used first.

    A file is removed only when object storage is enabled, the bucket holds an object of the same size, and the
    file has not been used for `min_idle_seconds` (so files in use by a running job are kept). Abandoned temporary
    files (*.part.mp4, *.dl, *.tmp) older than 6 hours are removed in every mode. Never touches the database."""
    from . import main
    max_age = float(os.getenv('CC_CACHE_MAX_AGE_HOURS', '72') if max_age_hours is None else max_age_hours) * 3600
    max_total = float(os.getenv('CC_CACHE_MAX_MB', '20480') if max_total_mb is None else max_total_mb) * 1048576
    min_idle = float(os.getenv('CC_CACHE_MIN_IDLE_SECONDS', '3600') if min_idle_seconds is None else min_idle_seconds)
    now, stats = time.time(), {'temp_removed': 0, 'evicted': 0, 'freed_bytes': 0, 'kept_unsynced': 0, 'bytes_after': 0}
    files = []
    for d in KNOWN_DIRS:
        root = main.DATA / d
        if not root.exists(): continue
        for f in root.rglob('*'):
            try: st = f.stat()
            except OSError: continue
            if not f.is_file(): continue
            if (f.name.endswith(('.part.mp4', '.dl', '.tmp')) or '.part.' in f.name) and now - st.st_mtime > 6 * 3600:
                if not dry_run: f.unlink(missing_ok=True)
                stats['temp_removed'] += 1; continue
            files.append((st.st_mtime, st.st_size, f))
    total = sum(sz for _, sz, _f in files)
    if enabled():
        for mtime, size, f in sorted(files, key=lambda x: x[0]):
            idle = now - mtime
            if idle < min_idle or (idle < max_age and total <= max_total): continue
            if head(str(f)) != size: stats['kept_unsynced'] += 1; continue      # never drop the only copy
            if not dry_run: f.unlink(missing_ok=True)
            total -= size; stats['evicted'] += 1; stats['freed_bytes'] += size
    stats['bytes_after'] = int(total)
    return stats


def response(path, media_type=None, filename=None, headers=None):
    """Serve an authorised file: a presigned redirect when object storage is enabled, else the local file."""
    from fastapi.responses import FileResponse, RedirectResponse
    if enabled() and key_for(path) and os.getenv('CC_S3_REDIRECT_DOWNLOADS', '1') == '1':
        return RedirectResponse(presigned_url(path, filename, media_type), status_code=307, headers={'Cache-Control': 'no-store', **(headers or {})})
    return FileResponse(local(path), media_type=media_type, filename=filename, headers=headers)


def describe():
    if not enabled(): return {'backend': 'local_disk', 'private': True, 'note': 'single host; configure CC_S3_BUCKET for object storage'}
    return {'backend': 's3_compatible', 'bucket': bucket(), 'endpoint': os.getenv('CC_S3_ENDPOINT_URL') or 'aws', 'presigned_ttl_seconds': presign_ttl(),
            'server_side_encryption': os.getenv('CC_S3_SSE', 'AES256') or None}
