"""Resource limits and per-organization quotas for the pilot.

Every value can be overridden with an environment variable; they are read at call time so operators (and tests)
can change them without code edits. The defaults are sized for a single 2-4 vCPU host serving about 10 organizations.
"""
import os, time

from fastapi import HTTPException


def _num(name, default):
    try: return type(default)(os.getenv(name, default))
    except (TypeError, ValueError): return default


# Media limits (per file)
def max_video_bytes(): return _num('CC_MAX_VIDEO_MB', 500) * 1024 * 1024
def max_video_seconds(): return _num('CC_MAX_VIDEO_SECONDS', 600.0)
def max_video_width(): return _num('CC_MAX_VIDEO_WIDTH', 3840)
def max_video_height(): return _num('CC_MAX_VIDEO_HEIGHT', 2160)
def max_video_fps(): return _num('CC_MAX_VIDEO_FPS', 120.0)
def max_image_bytes(): return _num('CC_MAX_IMAGE_MB', 10) * 1024 * 1024
# Processing limits
def job_max_seconds(): return _num('CC_JOB_MAX_SECONDS', 1800)
def upload_session_ttl(): return _num('CC_UPLOAD_SESSION_TTL', 3600)
# Per-organization quotas
def org_storage_bytes(): return _num('CC_ORG_STORAGE_QUOTA_MB', 10240) * 1024 * 1024
def org_daily_video_uploads(): return _num('CC_ORG_DAILY_VIDEO_UPLOADS', 25)
def org_max_active_jobs(): return _num('CC_ORG_MAX_ACTIVE_JOBS', 2)
def org_quality_checks_per_hour(): return _num('CC_ORG_QUALITY_CHECKS_PER_HOUR', 40)
def org_analyses_per_hour(): return _num('CC_ORG_ANALYSES_PER_HOUR', 30)
def org_max_members(): return _num('CC_ORG_MAX_MEMBERS', 25)


def snapshot():
    return {'max_video_mb': max_video_bytes() // 1048576, 'max_video_seconds': max_video_seconds(), 'max_video_resolution': [max_video_width(), max_video_height()],
            'max_video_fps': max_video_fps(), 'max_image_mb': max_image_bytes() // 1048576, 'job_max_seconds': job_max_seconds(),
            'org_storage_quota_mb': org_storage_bytes() // 1048576, 'org_daily_video_uploads': org_daily_video_uploads(), 'org_max_active_jobs': org_max_active_jobs(),
            'org_quality_checks_per_hour': org_quality_checks_per_hour(), 'org_analyses_per_hour': org_analyses_per_hour(), 'org_max_members': org_max_members()}


def _quota(code, message, **extra):
    return HTTPException(429 if code.endswith('RATE') or code == 'QUOTA_ACTIVE_JOBS' else 413, {'code': code, 'message': message, **extra})


def org_usage(c, org_id):
    day = time.strftime('%Y-%m-%d')
    storage = (c.execute('SELECT COALESCE(SUM(size_bytes),0) FROM videos WHERE org_id=?', (org_id,)).fetchone()[0]
               + c.execute('SELECT COALESCE(SUM(size_bytes),0) FROM products WHERE org_id=?', (org_id,)).fetchone()[0]
               + c.execute('SELECT COALESCE(SUM(output_bytes),0) FROM jobs WHERE org_id=?', (org_id,)).fetchone()[0]
               + c.execute("SELECT COALESCE(SUM(expected_bytes),0) FROM upload_sessions WHERE org_id=? AND status='PENDING'", (org_id,)).fetchone()[0])
    return {'storage_bytes': int(storage),
            'videos_uploaded_today': c.execute('SELECT COUNT(*) FROM videos WHERE org_id=? AND created_at>=?', (org_id, day)).fetchone()[0],
            'active_jobs': c.execute("SELECT COUNT(*) FROM jobs WHERE org_id=? AND status IN ('QUEUED','RUNNING')", (org_id,)).fetchone()[0],
            'members': c.execute('SELECT COUNT(*) FROM users WHERE org_id=? AND removed_at IS NULL', (org_id,)).fetchone()[0]}


def check_storage(c, org_id, incoming_bytes):
    used = org_usage(c, org_id)['storage_bytes']
    if used + incoming_bytes > org_storage_bytes():
        raise _quota('QUOTA_STORAGE', f'This workspace has reached its storage quota ({org_storage_bytes() // 1048576} MB). Delete media or contact support.',
                     used_mb=round(used / 1048576, 1))


def check_daily_uploads(c, org_id):
    if org_usage(c, org_id)['videos_uploaded_today'] >= org_daily_video_uploads():
        raise _quota('QUOTA_DAILY_UPLOADS', f'This workspace can upload {org_daily_video_uploads()} videos per day. Try again tomorrow.')


def check_active_jobs(c, org_id):
    if org_usage(c, org_id)['active_jobs'] >= org_max_active_jobs():
        raise _quota('QUOTA_ACTIVE_JOBS', f'This workspace already has {org_max_active_jobs()} renders queued or running. Wait for one to finish.')


def check_rate(org_id, kind, limit):
    from . import main
    if not main.rate_limit(f'org-{kind}:{org_id}', limit, 3600):
        raise _quota(f'QUOTA_{kind.upper()}_RATE', f'This workspace has reached its hourly limit for {kind.replace("_", " ")} ({limit} per hour).')


def check_video_metadata(duration, width, height, fps):
    if duration <= 0 or duration > max_video_seconds() or width > max_video_width() or height > max_video_height():
        raise HTTPException(413, f'Video must be between 0 and {int(max_video_seconds())} seconds and no larger than {max_video_width()}x{max_video_height()}')
    if fps > max_video_fps():
        raise HTTPException(413, f'Video frame rate must be at most {int(max_video_fps())} fps')
