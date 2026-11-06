"""Background housekeeping run by the worker.

* Cache maintenance (local files only, no database traffic): evicts local copies of media already safe in the bucket
  and removes abandoned temporary files. Runs every CC_CACHE_INTERVAL_SECONDS (default 1 hour).
* Database maintenance: expires abandoned direct uploads (deleting their objects), and prunes expired sessions,
  sign-in challenges, pending sign-ups and old rate-limit rows. Runs at most every CC_MAINTENANCE_INTERVAL_SECONDS
  (default 6 hours) and only right after a queue poll, so it never wakes an idle database by itself.
Both can also be run by hand: `python -m ops.maintenance`.
"""
import os, time

from . import obs, storage


def cache_interval(): return float(os.getenv('CC_CACHE_INTERVAL_SECONDS', '3600'))
def db_interval(): return float(os.getenv('CC_MAINTENANCE_INTERVAL_SECONDS', '21600'))


def run_cache_maintenance(dry_run=False):
    try:
        stats = storage.cleanup_cache(dry_run=dry_run)
        if stats['evicted'] or stats['temp_removed']: obs.info('cache maintenance', **stats)
        return stats
    except Exception:
        obs.error('cache maintenance failed', exc_info=True); return None


def expire_uploads(c, now=None):
    """Mark direct uploads that were never completed as EXPIRED and delete whatever reached the bucket."""
    now = now or int(time.time())
    rows = c.execute("SELECT id,path FROM upload_sessions WHERE status='PENDING' AND expires_at<?", (now,)).fetchall()
    for r in rows:
        if c.execute("UPDATE upload_sessions SET status='EXPIRED',finished_at=CURRENT_TIMESTAMP WHERE id=? AND status='PENDING'", (r['id'],)).rowcount:
            storage.delete(r['path'])
    return len(rows)


def run_db_maintenance():
    from . import main
    try:
        now = int(time.time())
        with main.db() as c:
            expired = expire_uploads(c, now)
            sessions = c.execute('DELETE FROM sessions WHERE expires_at<?', (now,)).rowcount
            c.execute('DELETE FROM auth_challenges WHERE expires_at<?', (now - 86400,))
            c.execute('DELETE FROM pending_signups WHERE expires_at<?', (now,))
            c.execute('DELETE FROM rate_limits WHERE hit_at<?', (time.time() - 86400,))
        from . import videos, inventory
        hashed = videos.backfill_hashes(int(os.getenv('CC_HASH_BACKFILL_PER_RUN', '2')))
        inventory.run_expiry()
        obs.info('database maintenance', expired_uploads=expired, expired_sessions=sessions, hashed_videos=hashed)
        return {'expired_uploads': expired, 'expired_sessions': sessions}
    except Exception:
        obs.error('database maintenance failed', exc_info=True); return None
