"""Infrastructure outage drill against the docker compose stack (scenario L).

    python qa/outage_drill.py [--api http://localhost:8000]

Stops PostgreSQL (`docker stop`: a crash or restart) and pauses the S3 gateway and the SMTP server (`docker pause`), one at a time, and checks that the API
answers with controlled errors (no stack traces, connection strings, hostnames or bucket names), that liveness stays
up, that readiness reports the failed dependency, and that everything recovers after `docker unpause`.
"""
import argparse, json, re, subprocess, sys, time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
LEAK = re.compile(r'Traceback|psycopg|postgresql://|cc-local|context-commerce-dev|seaweed|mailpit|botocore|File "/', re.I)
results = []


def dc(*args): return subprocess.run(['docker', 'compose', *args], cwd=ROOT, capture_output=True, text=True, timeout=120)


def check(name, ok, detail=''):
    results.append({'check': name, 'ok': bool(ok), 'detail': detail}); print(('PASS ' if ok else 'FAIL ') + name, detail, flush=True)


def wait_ready(api, want, timeout=90):
    t = time.time()
    while time.time() - t < timeout:
        try:
            r = httpx.get(api + '/api/health/ready', timeout=10)
            if (r.status_code == 200) == want: return r
        except Exception: pass
        time.sleep(2)
    return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--api', default='http://localhost:8000'); a = ap.parse_args(); api = a.api
    c = httpx.Client(base_url=api, timeout=60); c.get('/app'); tag = str(int(time.time()))
    r = c.post('/api/auth/register', json={'name': 'Drill', 'email': f'drill{tag}@qa.test', 'password': 'drill-password-123', 'role': 'creator', 'organization': f'Drill {tag}'})
    assert r.status_code == 200, r.text
    hdr = lambda: {'X-CSRF-Token': c.cookies.get('cc_csrf')}

    # 1. Database outage (crash/restart: connections reset, new connections refused)
    dc('stop', 'postgres'); time.sleep(1)
    try:
        live = httpx.get(api + '/api/health/live', timeout=10)
        check('db down: liveness still 200 (monitors do not need the database)', live.status_code == 200)
        time.sleep(31)   # readiness cache expiry
        rd = wait_ready(api, False, 120)
        check('db down: readiness 503 database=unavailable', rd is not None and rd.json()['checks']['database'] == 'unavailable', rd.text if rd else 'timeout')
        t = time.time()
        try: me = c.get('/api/me', timeout=180); took = time.time() - t
        except httpx.TimeoutException: me = None; took = time.time() - t
        if me is None: check('db down: authenticated request answers within 180 s', False, f'no answer after {took:.0f}s'); raise SystemExit(1)
        check('db down: authenticated request fails with a controlled error', me.status_code >= 500 and not LEAK.search(me.text), f'{me.status_code} {me.text[:120]} in {took:.0f}s')
    finally:
        dc('start', 'postgres')
    rd = wait_ready(api, True, 120)
    check('db back: readiness 200 again', rd is not None)
    check('db back: session still valid, requests succeed', c.get('/api/me').status_code == 200)

    # 2. Storage outage
    dc('pause', 's3'); time.sleep(31)
    try:
        rd = wait_ready(api, False, 120)
        check('storage down: readiness 503 object_storage=unavailable', rd is not None and rd.json()['checks']['object_storage'] == 'unavailable', rd.text if rd else 'timeout')
        init = c.post('/api/uploads/video', headers=hdr(), json={'title': 'x', 'filename': 'x.mp4', 'content_type': 'video/mp4', 'size_bytes': 1000, 'license_confirmed': True})
        check('storage down: upload authorisation still issued (signing is local)', init.status_code == 200 and init.json().get('method') == 'direct', init.text[:120])
        comp = c.post(f'/api/uploads/{init.json()["upload_id"]}/complete', headers=hdr())
        check('storage down: completion refused with a retryable, non-leaking error', comp.status_code in (409, 503) and not LEAK.search(comp.text), f'{comp.status_code} {comp.text[:160]}')
    finally:
        dc('unpause', 's3')
    rd = wait_ready(api, True, 120)
    check('storage back: readiness 200 again', rd is not None)

    # 3. SMTP outage
    dc('pause', 'mailpit')
    try:
        e = httpx.post(api + '/api/auth/email/start', json={'email': f'smtp{tag}@qa.test'}, timeout=90)
        check('smtp down: email sign-in fails with EMAIL_SEND_FAILED and no internals', e.status_code == 502 and 'EMAIL_SEND_FAILED' in e.text and not LEAK.search(e.text), f'{e.status_code} {e.text[:160]}')
    finally:
        dc('unpause', 'mailpit')
    time.sleep(2)
    e = httpx.post(api + '/api/auth/email/start', json={'email': f'smtp2{tag}@qa.test'}, timeout=60)
    check('smtp back: email sign-in works again', e.status_code == 200, e.text[:120])

    out = ROOT / 'docs' / 'v10' / 'evidence' / 'outage_drill.json'; out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'results': results}, indent=1))
    print(f"{sum(r['ok'] for r in results)}/{len(results)} checks passed")
    sys.exit(0 if all(r['ok'] for r in results) else 1)


if __name__ == '__main__':
    main()
