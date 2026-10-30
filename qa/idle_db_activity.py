"""Measure what an idle deployment does to its database (the local proxy for Neon's scale-to-zero).

    python qa/idle_db_activity.py --admin-url postgresql://cc:cc-local-only@127.0.0.1:5433/postgres --db context_commerce --minutes 20

Connects to a *different* database (`postgres`) so its own queries are not counted, and samples every 30 s:
pg_stat_database.sessions (client sessions ever opened to the application database; PostgreSQL 14+) and the
client backends currently connected. Transaction counters are recorded too but are NOT a measure of the app:
autovacuum visits every database each minute and counts as transactions even with the app stopped (measured).
Neon suspends compute after 5 minutes without connections; the report lists when the app connected.
"""
import argparse, json, time
from pathlib import Path

import psycopg

ap = argparse.ArgumentParser()
ap.add_argument('--admin-url', required=True); ap.add_argument('--db', default='context_commerce'); ap.add_argument('--minutes', type=float, default=20)
ap.add_argument('--out', default=str(Path(__file__).resolve().parent.parent / 'docs' / 'v10' / 'evidence' / 'idle_db_activity.json'))
a = ap.parse_args()
samples, t0 = [], time.time()
while time.time() - t0 < a.minutes * 60:
    with psycopg.connect(a.admin_url, autocommit=True) as con:
        x, sess = con.execute('SELECT xact_commit+xact_rollback, sessions FROM pg_stat_database WHERE datname=%s', (a.db,)).fetchone()
        conns = con.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=%s AND backend_type='client backend'", (a.db,)).fetchone()[0]
    samples.append({'t': round(time.time() - t0), 'xacts': int(x), 'sessions': int(sess), 'connections': int(conns)})
    print(samples[-1], flush=True)
    time.sleep(30)
active = [s['t'] for prev, s in zip(samples, samples[1:]) if s['sessions'] > prev['sessions'] or s['connections'] > 0]
gaps = [b - a2 for a2, b in zip(active, active[1:])]
report = {'minutes': a.minutes, 'samples': samples, 'app_sessions_opened': samples[-1]['sessions'] - samples[0]['sessions'],
          'transactions_total_including_autovacuum': samples[-1]['xacts'] - samples[0]['xacts'],
          'intervals_with_activity': len(active), 'activity_at_seconds': active, 'max_gap_seconds': max(gaps) if gaps else None,
          'connections_max': max(s['connections'] for s in samples), 'connections_last': samples[-1]['connections']}
Path(a.out).parent.mkdir(parents=True, exist_ok=True); Path(a.out).write_text(json.dumps(report, indent=1))
print(json.dumps({k: v for k, v in report.items() if k != 'samples'}, indent=1))
