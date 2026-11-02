"""Running jobs where code only runs during a request (CC_WORKER_MODE=http), such as Vercel Functions.

There is no worker process to poll the queue, so queueing a job sends one authenticated request to /api/internal/run-jobs
and does not wait for the answer. That request runs a single job within the function's time limit, then asks for another
if work remains. The database queue is unchanged: leases, retries and dead-lettering work as they do with worker.py.
A trigger can still be lost (a deploy, a crash), so the endpoints the dashboard polls also start the runner when they see
queued work or an expired lease. Nothing runs while nothing is queued, so an idle Neon database can scale to zero.
"""
import hashlib, hmac, os, threading, time

import httpx

from . import obs

_last_nudge = {}


def enabled(): return os.getenv('CC_WORKER_MODE') == 'http'
def budget_seconds(): return float(os.getenv('CC_RUNNER_BUDGET_SECONDS', '270'))     # under Vercel's default 300 s limit
def max_concurrent(): return int(os.getenv('CC_RUNNER_CONCURRENCY', '1'))     # jobs share the instance's CPU, and each must meet its deadline
def nudge_interval(): return float(os.getenv('CC_RUNNER_NUDGE_SECONDS', '20'))


def secret():
    return hmac.new(os.getenv('CC_SIGNING_KEY', '').encode(), b'context-commerce-job-runner', hashlib.sha256).hexdigest()


def authorised(header):
    return bool(os.getenv('CC_SIGNING_KEY')) and hmac.compare_digest(header or '', f'Bearer {secret()}')


def url():
    # CC_WORKER_URL is the private runner service's address (a Vercel service binding).
    base = os.getenv('CC_WORKER_URL') or os.getenv('CC_PUBLIC_URL') or ''
    return base.rstrip('/') + '/api/internal/run-jobs'


def kick():
    """Start the runner. The request keeps running after this side stops listening, so the wait only covers delivery."""
    try:
        httpx.post(url(), headers={'Authorization': f'Bearer {secret()}'}, timeout=httpx.Timeout(5.0, read=float(os.getenv('CC_RUNNER_KICK_WAIT', '1'))))
    except httpx.TimeoutException:
        pass
    except Exception as e:
        obs.warn('job runner could not be started', error=type(e).__name__)


def nudge(c, org_id):
    """Called by endpoints the dashboard polls: restarts the runner if this workspace has work nothing is running."""
    if not enabled(): return
    now = time.time()
    if now - _last_nudge.get(org_id, 0) < nudge_interval(): return
    stuck = "(status='QUEUED' OR (status='RUNNING' AND (lease_until IS NULL OR lease_until<?)))"
    if c.execute(f'SELECT 1 FROM jobs WHERE org_id=? AND {stuck} LIMIT 1', (org_id, now)).fetchone() or \
       c.execute(f'SELECT 1 FROM decisions WHERE org_id=? AND {stuck} LIMIT 1', (org_id, now)).fetchone():
        _last_nudge[org_id] = now
        kick()


def housekeeping():
    """Called on sign-in: where no job is running, nothing else would clear expired sign-ins and abandoned uploads."""
    if not enabled(): return
    from . import main, maintenance
    if main.rate_limit('housekeeping', 1, maintenance.db_interval()): kick()


class OutOfTime(threading.Event):
    """Set when this request's time is nearly up. A decision stops at its next checkpoint and continues in a new request."""


def _busy():
    from . import main
    now = time.time()
    with main.db() as c:
        return sum(c.execute(f"SELECT COUNT(*) FROM {t} WHERE status='RUNNING' AND lease_until>=?", (now,)).fetchone()[0] for t in ('jobs', 'decisions')) >= max_concurrent()


def _has_work():
    from . import main
    now = time.time()
    stuck = "status='QUEUED' OR (status='RUNNING' AND (lease_until IS NULL OR lease_until<?))"
    with main.db() as c:
        return any(c.execute(f'SELECT 1 FROM {t} WHERE {stuck} LIMIT 1', (now,)).fetchone() for t in ('jobs', 'decisions'))


def run_one():
    """Run at most one job, then ask for another request if more is waiting. A busy runner hands the queue on when it finishes."""
    from . import decisions, main, maintenance, storage
    if _busy(): return {'ran': None, 'reason': 'busy'}
    storage.cleanup_cache(max_age_hours=1, max_total_mb=float(os.getenv('CC_CACHE_MAX_MB', '256')), min_idle_seconds=60)
    stop = OutOfTime(); timer = threading.Timer(budget_seconds(), stop.set); timer.daemon = True; timer.start()
    ran = None
    try:
        job_id, token = main.claim_next_job()
        if job_id is not None:
            main.render_job(job_id, token); ran = 'render'
        elif decisions.run_next(stop=stop):
            ran = 'decision'
        if time.time() - maintenance_last.get('at', 0) > maintenance.db_interval():   # a job just woke the database anyway
            maintenance.run_db_maintenance(); maintenance_last['at'] = time.time()
    finally:
        timer.cancel()
    if _has_work(): kick()
    return {'ran': ran}


maintenance_last = {}
