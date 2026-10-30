"""Create or upgrade the database schema (idempotent) and report the result.

    python -m ops.migrate            # uses DATABASE_URL (Neon/PostgreSQL) or the local SQLite file
The API and workers also run this at start-up under an advisory lock; run it explicitly before a deployment
so a failed migration stops the rollout instead of the new containers.
"""
import sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

if __name__ == '__main__':
    t = time.time()
    from app import main, db as dbm   # importing app.main runs init_db()
    main.init_db()
    with main.db() as c:
        tables = c.tables()
        users = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        orgs = c.execute('SELECT COUNT(*) FROM organizations').fetchone()[0]
    d = dbm.describe()
    print(f"schema ok: engine={d['engine']} neon={d.get('neon', False)} tables={len(tables)} organizations={orgs} users={users} ({time.time() - t:.1f}s)")
