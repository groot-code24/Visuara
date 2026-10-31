"""Resource limits, per-organization quotas and plans.

Media and processing limits can be overridden with environment variables; they are read at call time so operators
(and tests) can change them without code edits. Each workspace is on a plan (see PLANS and the pricing page) whose
monthly allowances are enforced here. The environment quotas stay as hard platform caps above every plan.
"""
import os, time

from fastapi import HTTPException


def _num(name, default):
    try: return type(default)(os.getenv(name, default))
    except (TypeError, ValueError): return default


# Media limits (per file)
def max_video_bytes(): return _num('CC_MAX_VIDEO_MB', 2048) * 1024 * 1024
def max_video_seconds(): return _num('CC_MAX_VIDEO_SECONDS', 600.0)
def max_video_width(): return _num('CC_MAX_VIDEO_WIDTH', 3840)
def max_video_height(): return _num('CC_MAX_VIDEO_HEIGHT', 2160)
def max_video_fps(): return _num('CC_MAX_VIDEO_FPS', 120.0)
def max_image_bytes(): return _num('CC_MAX_IMAGE_MB', 10) * 1024 * 1024
# Processing limits
def job_max_seconds(): return _num('CC_JOB_MAX_SECONDS', 1800)
def upload_session_ttl(): return _num('CC_UPLOAD_SESSION_TTL', 3600)
# Per-organization quotas
# Platform ceilings above every plan. The plan's own value applies below them (the effective_* functions).
def org_storage_bytes(): return _num('CC_ORG_STORAGE_QUOTA_MB', 1048576) * 1024 * 1024
def org_daily_video_uploads(): return _num('CC_ORG_DAILY_VIDEO_UPLOADS', 1000)
def org_max_active_jobs(): return _num('CC_ORG_MAX_ACTIVE_JOBS', 50)
def org_quality_checks_per_hour(): return _num('CC_ORG_QUALITY_CHECKS_PER_HOUR', 40)
def org_analyses_per_hour(): return _num('CC_ORG_ANALYSES_PER_HOUR', 30)
def org_max_members(): return _num('CC_ORG_MAX_MEMBERS', 25)


# Plans (docs/v11/pricing.md). None = unlimited within the platform caps above. Prices are list prices in USD;
# billing is handled outside the app (invoices) until a payment provider is integrated.
PLANS = {
    'free':       {'name': 'Free', 'price_usd_month': 0, 'decisions_per_month': 5, 'renders_per_month': 3, 'storage_mb': 2048,
                   'members': 1, 'max_video_seconds': 60, 'max_products_per_decision': 4, 'max_videos_per_decision': 3,
                   'max_video_mb': 250, 'daily_video_uploads': 10, 'active_jobs': 2},
    'pro':        {'name': 'Pro', 'price_usd_month': 39, 'decisions_per_month': 60, 'renders_per_month': 40, 'storage_mb': 51200,
                   'members': 3, 'max_video_seconds': 300, 'max_products_per_decision': 4, 'max_videos_per_decision': 10,
                   'max_video_mb': 1024, 'daily_video_uploads': 50, 'active_jobs': 4},
    'team':       {'name': 'Agency', 'price_usd_month': 159, 'decisions_per_month': 300, 'renders_per_month': 200, 'storage_mb': 256000,
                   'members': 15, 'max_video_seconds': 600, 'max_products_per_decision': 4, 'max_videos_per_decision': 20,
                   'max_video_mb': 2048, 'daily_video_uploads': 200, 'active_jobs': 10},
    'enterprise': {'name': 'Enterprise', 'price_usd_month': None, 'decisions_per_month': None, 'renders_per_month': None, 'storage_mb': None,
                   'members': None, 'max_video_seconds': None, 'max_products_per_decision': 4, 'max_videos_per_decision': 50,
                   'max_video_mb': None, 'daily_video_uploads': None, 'active_jobs': None},
}


def default_plan():
    plan = os.getenv('CC_DEFAULT_PLAN', 'free')
    return plan if plan in PLANS else 'free'


def org_plan(c, org_id):
    row = c.execute('SELECT plan FROM organizations WHERE id=?', (org_id,)).fetchone()
    key = (row['plan'] if row else None) or default_plan()
    return key if key in PLANS else 'free'


def plan_limits(c, org_id):
    key = org_plan(c, org_id)
    return key, PLANS[key]


def _month_start(): return time.strftime('%Y-%m-01')


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
            'members': c.execute('SELECT COUNT(*) FROM users WHERE org_id=? AND removed_at IS NULL', (org_id,)).fetchone()[0],
            'decisions_this_month': c.execute('SELECT COUNT(*) FROM decisions WHERE org_id=? AND created_at>=?', (org_id, _month_start())).fetchone()[0],
            'renders_this_month': c.execute("SELECT COUNT(*) FROM jobs WHERE org_id=? AND created_at>=? AND status NOT IN ('CANCELLED')", (org_id, _month_start())).fetchone()[0]}


def _upgrade(code, message, plan, **extra):
    return HTTPException(402, {'code': code, 'message': message + ' Upgrade your plan to continue.', 'plan': plan, **extra})


def effective_storage_bytes(c, org_id):
    _, lim = plan_limits(c, org_id)
    return min(org_storage_bytes(), lim['storage_mb'] * 1048576) if lim['storage_mb'] else org_storage_bytes()


def effective_max_members(c, org_id):
    _, lim = plan_limits(c, org_id)
    return min(org_max_members(), lim['members']) if lim['members'] else org_max_members()


def effective_video_bytes(c, org_id):
    _, lim = plan_limits(c, org_id)
    return min(max_video_bytes(), lim['max_video_mb'] * 1048576) if lim['max_video_mb'] else max_video_bytes()


def effective_daily_uploads(c, org_id):
    _, lim = plan_limits(c, org_id)
    return min(org_daily_video_uploads(), lim['daily_video_uploads']) if lim['daily_video_uploads'] else org_daily_video_uploads()


def effective_active_jobs(c, org_id):
    _, lim = plan_limits(c, org_id)
    return min(org_max_active_jobs(), lim['active_jobs']) if lim['active_jobs'] else org_max_active_jobs()


def check_monthly(c, org_id, kind):
    """kind: 'decisions' or 'renders'. Raises 402 when the plan's monthly allowance is used up."""
    key, lim = plan_limits(c, org_id)
    allowance = lim[f'{kind}_per_month']
    if allowance is not None and org_usage(c, org_id)[f'{kind}_this_month'] >= allowance:
        raise _upgrade(f'PLAN_{kind.upper()}_LIMIT', f'Your {lim["name"]} plan includes {allowance} {kind} per month and they are used up for this month.', key)


def check_video_for_plan(c, org_id, duration):
    key, lim = plan_limits(c, org_id)
    if lim['max_video_seconds'] and duration > lim['max_video_seconds']:
        raise _upgrade('PLAN_VIDEO_TOO_LONG', f'Your {lim["name"]} plan accepts videos up to {lim["max_video_seconds"]} seconds.', key)


def check_video_size(c, org_id, size):
    key, lim = plan_limits(c, org_id); cap = effective_video_bytes(c, org_id)
    if size > cap:
        raise _upgrade('PLAN_VIDEO_TOO_LARGE', f'Your {lim["name"]} plan accepts video files up to {cap // 1048576} MB.', key)


def check_storage(c, org_id, incoming_bytes):
    used = org_usage(c, org_id)['storage_bytes']; cap = effective_storage_bytes(c, org_id)
    if used + incoming_bytes > cap:
        raise _quota('QUOTA_STORAGE', f'This workspace has reached its storage quota ({cap // 1048576} MB). Delete media or upgrade your plan.',
                     used_mb=round(used / 1048576, 1))


def check_daily_uploads(c, org_id):
    cap = effective_daily_uploads(c, org_id)
    if org_usage(c, org_id)['videos_uploaded_today'] >= cap:
        raise _quota('QUOTA_DAILY_UPLOADS', f'This workspace can upload {cap} videos per day. Try again tomorrow.')


def check_active_jobs(c, org_id):
    cap = effective_active_jobs(c, org_id)
    if org_usage(c, org_id)['active_jobs'] >= cap:
        raise _quota('QUOTA_ACTIVE_JOBS', f'This workspace already has {cap} renders queued or running. Wait for one to finish.')


def check_rate(org_id, kind, limit):
    from . import main
    if not main.rate_limit(f'org-{kind}:{org_id}', limit, 3600):
        raise _quota(f'QUOTA_{kind.upper()}_RATE', f'This workspace has reached its hourly limit for {kind.replace("_", " ")} ({limit} per hour).')


def check_video_metadata(duration, width, height, fps):
    if duration <= 0 or duration > max_video_seconds() or width > max_video_width() or height > max_video_height():
        raise HTTPException(413, f'Video must be between 0 and {int(max_video_seconds())} seconds and no larger than {max_video_width()}x{max_video_height()}')
    if fps > max_video_fps():
        raise HTTPException(413, f'Video frame rate must be at most {int(max_video_fps())} fps')
