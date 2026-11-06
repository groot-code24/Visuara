"""Load test the API (M16): many people using the dashboard at once, measured per endpoint.

    python qa/load_test.py [--users 20] [--seconds 60] [--docker] [--out FILE]

It seeds the demonstration data (`ops/demo.py`) into a temporary folder and starts its own API on it, either locally or,
with --docker, in the production image pinned to one vCPU and 2 GB (`--cpuset-cpus=0 --memory=2g`). It creates sessions
for the demo publisher and brand directly in that database, then runs `--users` concurrent clients for `--seconds`, each
repeating the requests the dashboard makes: mostly reads (the overview, library, inventory, marketplace, campaigns,
requests, finance, notifications), and writes (a brand creates a campaign, a publisher updates its profile).

Reported: requests per second, the error rate, and p50 / p95 / p99 latency overall and per endpoint. It fails (exit 1)
when the error rate is above 1% or the overall p95 is above --p95-ms (default 1500 ms). The database is SQLite in a
temporary folder: production uses PostgreSQL on Neon over the network, so the absolute numbers are a lower bound on
latency, not a forecast of production.
"""
import argparse, hashlib, json, os, random, secrets, shutil, socket, sqlite3, subprocess, sys, tempfile, threading, time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
READS = {
    'publisher': ['/api/me', '/api/videos', '/api/inventory', '/api/opportunities', '/api/placement-requests', '/api/invoices', '/api/earnings',
                  '/api/notifications', '/api/organizations', '/api/usage'],
    'brand': ['/api/me', '/api/marketplace/inventory', '/api/campaigns', '/api/placement-requests', '/api/invoices', '/api/ledger',
              '/api/notifications', '/api/products', '/api/payments/config'],
}


def _free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


def _sessions(db, n):
    """n sessions for each demo account, written straight into the seeded SQLite database."""
    con = sqlite3.connect(db); out = {'publisher': [], 'brand': []}
    for role, email in (('publisher', 'publisher@demo.visuara.si'), ('brand', 'brand@demo.visuara.si')):
        uid = con.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()[0]
        for _ in range(n):
            tok = secrets.token_urlsafe(32)
            con.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)', (hashlib.sha256(tok.encode()).hexdigest(), uid, int(time.time()) + 7200))
            out[role].append(tok)
    con.commit(); con.close()
    return out


def _client_loop(base, role, token, stop, record, seed):
    rnd = random.Random(seed)
    with httpx.Client(base_url=base, timeout=30, cookies={'cc_session': token}) as c:
        csrf = c.get('/api/auth/csrf').cookies.get('cc_csrf') or c.cookies.get('cc_csrf')
        while not stop.is_set():
            if rnd.random() < .08:
                if role == 'brand':
                    path, label = '/api/campaigns', 'POST /api/campaigns'
                    body = {'name': f'Load {rnd.randrange(10**6)}', 'budget_minor': 1000, 'currency': 'EUR'}
                    t = time.perf_counter(); r = c.post(path, json=body, headers={'x-csrf-token': csrf or ''})
                else:
                    path, label = '/api/profile', 'PUT /api/profile'
                    body = {'display_name': 'Demo Studio', 'website': 'https://studio.example', 'description': f'Updated {rnd.randrange(10**6)}'}
                    t = time.perf_counter(); r = c.put(path, json=body, headers={'x-csrf-token': csrf or ''})
            else:
                path = rnd.choice(READS[role]); label = f'GET {path}'
                t = time.perf_counter(); r = c.get(path)
            record(label, (time.perf_counter() - t) * 1000, r.status_code)


def _pct(xs, q): return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def run(users, seconds, docker, p95_ms):
    tmp = Path(tempfile.mkdtemp(prefix='visuara-load-')); data = tmp / 'data'
    env = dict(os.environ, CC_DOTENV='0', CC_MAIL_BACKEND='outbox', PYTHONPATH=str(ROOT), PYTHONIOENCODING='utf-8', CC_DEFAULT_PLAN='team')
    for k in ('DATABASE_URL', 'CC_DATABASE_URL', 'CC_S3_BUCKET', 'BLOB_READ_WRITE_TOKEN', 'CC_VISION_MODEL'): env.pop(k, None)
    seeded = subprocess.run([sys.executable, '-m', 'ops.demo', 'seed', '--data', str(data)], cwd=ROOT, env=env, capture_output=True, text=True)
    if seeded.returncode: raise SystemExit('Demo seeding failed:\n' + seeded.stderr[-2000:])
    tokens = _sessions(data / 'context_commerce.sqlite3', max(1, users // 2 + 1))
    port = _free_port(); base = f'http://127.0.0.1:{port}'
    run_env = dict(env, CC_DATA_DIR=str(data), CC_INLINE_WORKER='0')
    if docker:
        name = f'visuara-load-{secrets.token_hex(3)}'
        cmd = ['docker', 'run', '--rm', '-d', '--name', name, '--cpuset-cpus=0', '--memory=2g', '-p', f'127.0.0.1:{port}:8080',
               '-v', f'{data}:/data', '-e', 'CC_ENV=development', '-e', 'CC_DATA_DIR=/data', '-e', 'CC_INLINE_WORKER=0', '-e', 'CC_DOTENV=0',
               '-e', 'CC_MAIL_BACKEND=outbox', '-e', 'CC_SECURE_COOKIES=0', '--entrypoint', 'uvicorn', 'context-commerce-vercel:local',
               'app.main:app', '--host', '0.0.0.0', '--port', '8080', '--no-server-header']
        subprocess.run(cmd, check=True, capture_output=True, env=dict(os.environ, MSYS_NO_PATHCONV='1'))
        stop_server = lambda: subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
        where = 'production image, 1 pinned vCPU, 2 GB (docker --cpuset-cpus=0 --memory=2g), uvicorn 1 process'
    else:
        api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port), '--no-server-header'],
                               cwd=ROOT, env=run_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stop_server = lambda: (api.terminate(), api.wait(10))
        where = f'this machine ({os.cpu_count()} logical CPUs), uvicorn 1 process'
    try:
        for _ in range(120):
            try:
                if httpx.get(base + '/api/health/live', timeout=2).status_code == 200: break
            except httpx.HTTPError: pass
            time.sleep(.5)
        else:
            raise SystemExit('The API did not start')
        lat, lock = {}, threading.Lock()
        def record(label, ms, status):
            with lock: lat.setdefault(label, []).append((ms, status))
        stop = threading.Event(); threads = []
        for i in range(users):
            role = 'brand' if i % 2 else 'publisher'
            t = threading.Thread(target=_client_loop, args=(base, role, tokens[role][i // 2 % len(tokens[role])], stop, record, i), daemon=True)
            t.start(); threads.append(t)
        started = time.time(); time.sleep(seconds); stop.set()
        for t in threads: t.join(30)
        elapsed = time.time() - started
    finally:
        stop_server(); shutil.rmtree(tmp, ignore_errors=True)
    allv = sorted(ms for v in lat.values() for ms, _ in v)
    errors = sum(1 for v in lat.values() for _, st in v if st >= 500 or st == 0)
    unexpected = {k: sorted({st for _, st in v if st >= 400}) for k, v in lat.items() if any(st >= 400 for _, st in v)}
    total = len(allv)
    res = {'measured_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'where': where, 'database': 'SQLite (temporary)', 'users': users, 'seconds': round(elapsed, 1),
           'requests': total, 'requests_per_second': round(total / max(elapsed, 1e-9), 1), 'server_errors': errors,
           'error_rate': round(errors / max(1, total), 4), 'client_errors_by_endpoint': unexpected,
           'latency_ms': {'p50': _pct(allv, .5), 'p95': _pct(allv, .95), 'p99': _pct(allv, .99), 'max': round(allv[-1], 1) if allv else None},
           'per_endpoint': {k: {'n': len(v), 'p50': _pct(sorted(ms for ms, _ in v), .5), 'p95': _pct(sorted(ms for ms, _ in v), .95)} for k, v in sorted(lat.items())},
           'thresholds': {'max_error_rate': 0.01, 'max_p95_ms': p95_ms}}
    res['passed'] = res['error_rate'] <= 0.01 and (res['latency_ms']['p95'] or 0) <= p95_ms and not unexpected
    return res


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--users', type=int, default=20); ap.add_argument('--seconds', type=int, default=60)
    ap.add_argument('--docker', action='store_true'); ap.add_argument('--p95-ms', type=float, default=1500); ap.add_argument('--out')
    a = ap.parse_args()
    r = run(a.users, a.seconds, a.docker, a.p95_ms)
    if a.out: Path(a.out).write_text(json.dumps(r, indent=1))
    print(json.dumps({k: v for k, v in r.items() if k != 'per_endpoint'}, indent=1))
    sys.exit(0 if r['passed'] else 1)
