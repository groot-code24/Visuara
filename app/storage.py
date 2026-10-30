"""Media storage: local working copy plus optional private S3-compatible object storage.

Without CC_S3_BUCKET the local data directory is the only store (single host). With it:
  * every durable file (uploads, keyframes, analyses, renders) is written through to the private bucket;
  * a host that lacks a file restores it from the bucket on first use, so API and worker hosts need no shared disk;
  * downloads and streams are served by redirecting to short-lived presigned URLs, after all access checks pass.
Works with AWS S3, Cloudflare R2, Backblaze B2, MinIO and other S3-compatible services (CC_S3_ENDPOINT_URL).
"""
import mimetypes, os, threading
from pathlib import Path

KNOWN_DIRS = ('uploads', 'outputs', 'frames', 'analyses')
_client = None
_lock = threading.Lock()


def bucket(): return os.getenv('CC_S3_BUCKET', '')
def enabled(): return bool(bucket())
def presign_ttl(): return int(os.getenv('CC_S3_PRESIGN_TTL', '300'))


def client():
    global _client
    with _lock:
        if _client is None:
            import boto3
            from botocore.config import Config
            _client = boto3.client('s3', endpoint_url=os.getenv('CC_S3_ENDPOINT_URL') or None, region_name=os.getenv('CC_S3_REGION') or None,
                                   config=Config(signature_version='s3v4', retries={'max_attempts': 5, 'mode': 'standard'}))
        return _client


def reset():
    global _client
    with _lock: _client = None


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
    if p.exists(): return str(p)
    target = _local_for_key(path)
    if target.exists(): return str(target)
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
    return client().generate_presigned_url('get_object', Params=params, ExpiresIn=ttl or presign_ttl())


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
