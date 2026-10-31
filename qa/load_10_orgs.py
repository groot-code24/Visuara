"""Ten-workspace concurrency and isolation test against the running docker compose stack, through the web app.

    docker compose -f docker-compose.yml -f deploy/local/compose.loadtest.yml up -d --build
    python qa/load_10_orgs.py [--app http://127.0.0.1:3000] [--orgs 10] [--database-url postgresql://cc:cc-local-only@127.0.0.1:5433/context_commerce]
                              [--kill-worker context-commerce-worker-1] [--out docs/v11/evidence/load_10_orgs.json]

Every request goes through the Next.js app's proxy to the API, as a browser's would. Simulates independent
workspaces working at the same time:
  * 10 owners sign up by emailed code (read from Mailpit); each invites an editor and a viewer, who join the same way;
  * editors add two products and upload two videos (direct to the S3 bucket by presigned PUT), then all 10 workspaces
    start a decision at once (2 products x 2 videos each) and the two workers run them;
  * editors turn each recommendation into a placement and run its quality check; owners approve and all start renders
    at once (optionally killing one worker mid-render); owners download the validated videos;
  * viewers and editors are refused actions above their role; every owner then probes every other workspace's
    products, videos, decisions, previews, placements and renders. Any 2xx there is a cross-tenant leak.
Reports per-endpoint latency, errors, decision and render timings and (with --database-url) PostgreSQL connections.
"""
import argparse, json, statistics, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'qa'))
from qa_session import Session          # noqa: E402
from benchmark import fixtures_scene as scene   # noqa: E402

SAMPLES, ERRORS, LOCK = [], [], threading.Lock()


def pmap(fn, items, workers):
    with ThreadPoolExecutor(workers) as ex: return list(ex.map(fn, items))


def guarded(name, fn):
    def run(x):
        try: return fn(x)
        except Exception as e:
            with LOCK: ERRORS.append({'phase': name, 'error': str(e)[:300]})
            raise
    return run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', default='http://127.0.0.1:3000'); ap.add_argument('--orgs', type=int, default=10)
    ap.add_argument('--database-url'); ap.add_argument('--kill-worker'); ap.add_argument('--out', default=str(ROOT / 'docs' / 'v11' / 'evidence' / 'load_10_orgs.json'))
    a = ap.parse_args()
    tag = str(int(time.time()))[-6:]; t0 = time.time(); phases = {}
    clips = ROOT / 'data' / f'loadtest_{tag}'
    rooms = [scene.generate('room', 20 + i, clips)['path'] for i in range(4)]
    prods = [scene.product(clips / 'tech.png', 160, 100, (180, 80, 30)), scene.product(clips / 'food.png', 120, 90, (40, 160, 220))]

    pg = {'max': 0, 'samples': []}; stop = threading.Event()
    if a.database_url:
        import psycopg
        def sample():
            while not stop.is_set():
                try:
                    with psycopg.connect(a.database_url, connect_timeout=5) as con:
                        n = con.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()").fetchone()[0]
                    pg['samples'].append(n); pg['max'] = max(pg['max'], n)
                except Exception: pass
                stop.wait(2)
        threading.Thread(target=sample, daemon=True).start()

    def phase(name, fn, items, workers=10):
        t = time.time(); out = pmap(guarded(name, fn), items, workers); phases[name] = round(time.time() - t, 1)
        print(f'{name}: {phases[name]} s', flush=True); return out

    S = lambda email: Session(a.app, email, SAMPLES)
    # 1. owners sign up; 2. each invites an editor and a viewer, who join by signing in with the invited address
    def signup(i):
        o = S(f'owner{i}.{tag}@org{i}.test'); o.sign_up(f'Owner {i}', f'Load Org {i} {tag}'); return o
    owners = phase('owner_sign_up', signup, range(a.orgs))

    def team(i):
        o = owners[i]; out = {}
        for role in ('editor', 'viewer'):
            o.call('POST', '/api/invitations', json={'email': f'{role}{i}.{tag}@org{i}.test', 'member_role': role}, label='/api/invitations', expect=200)
            m = S(f'{role}{i}.{tag}@org{i}.test'); u = m.sign_up(role.title()); assert u['member_role'] == role; out[role] = m
        return out
    teams = phase('invite_and_join', team, range(a.orgs))
    editors, viewers = [t['editor'] for t in teams], [t['viewer'] for t in teams]

    # 3. content: two products (multipart through the proxy) and two videos (presigned PUT straight to the bucket)
    def content(i):
        e = editors[i]; pids, vids = [], []
        for path, name, cat in ((prods[0], 'Sleeve', 'tech'), (prods[1], 'Granola', 'food')):
            pids.append(e.call('POST', '/api/products', data={'name': name, 'category': cat}, files={'image': (Path(path).name, Path(path).read_bytes(), 'image/png')},
                               label='/api/products', expect=200).json()['id'])
        for k in range(2):
            data = Path(rooms[(i + k) % len(rooms)]).read_bytes()
            init = e.call('POST', '/api/uploads/video', json={'title': f'Clip {k}', 'filename': 'clip.mp4', 'content_type': 'video/mp4', 'size_bytes': len(data), 'license_confirmed': True},
                          label='/api/uploads/video', expect=200).json()
            assert init['method'] == 'direct', init
            t = time.perf_counter(); put = httpx.put(init['put_url'], content=data, headers=init['headers'], timeout=120)
            with LOCK: SAMPLES.append(('PUT presigned (bucket)', (time.perf_counter() - t) * 1000, put.status_code))
            assert put.status_code == 200, put.text[:200]
            vids.append(e.call('POST', f'/api/uploads/{init["upload_id"]}/complete', label='/api/uploads/{id}/complete', expect=200).json()['id'])
        return pids, vids
    content_ids = phase('products_and_direct_uploads', content, range(a.orgs))

    # 4. all workspaces start a decision at once; the two workers run them
    def start_decision(i):
        pids, vids = content_ids[i]
        return editors[i].call('POST', '/api/decisions', json={'name': 'Load', 'product_ids': pids, 'video_ids': vids}, label='/api/decisions', expect=200).json()['id']
    dids = phase('start_decisions', start_decision, range(a.orgs))

    def wait_decision(i):
        t = time.time()
        while time.time() - t < 1800:
            d = editors[i].call('GET', f'/api/decisions/{dids[i]}', label='/api/decisions/{id}', expect=200).json()
            if d['status'] not in ('QUEUED', 'RUNNING'): return d, time.time() - t
            time.sleep(3)
        raise AssertionError('decision timed out')
    decisions = phase('decisions_complete', wait_decision, range(a.orgs))
    assigned = sum(d['result']['summary']['assigned'] for d, _ in decisions if d['status'] == 'SUCCEEDED')

    # 5. apply the first assigned recommendation, quality check, approve, render
    def prepare(i):
        d, _ = decisions[i]; assert d['status'] == 'SUCCEEDED', d.get('error')
        v = next(v for v in d['result']['videos'] if v['recommendation'])
        pl = editors[i].call('POST', f'/api/decisions/{dids[i]}/apply', json={'video_id': v['video_id']}, label='/api/decisions/{id}/apply', expect=200).json()['placement_id']
        q = editors[i].call('POST', f'/api/placements/{pl}/quality-check', label='/api/placements/{id}/quality-check', expect=200).json()
        assert q['status'] == 'PASSED', q['failures']
        r = editors[i].call('POST', f'/api/placements/{pl}/approve', label='/api/placements/{id}/approve (editor, refused)')
        assert r.status_code == 403
        owners[i].call('POST', f'/api/placements/{pl}/approve', json={'fit_confidence': .9}, label='/api/placements/{id}/approve', expect=200)
        return pl
    placements = phase('apply_check_approve', prepare, range(a.orgs))

    queued_at = time.time()
    jobs = phase('start_renders', lambda i: owners[i].call('POST', f'/api/placements/{placements[i]}/render', label='/api/placements/{id}/render', expect=200).json()['id'], range(a.orgs))
    killed = None
    if a.kill_worker:
        time.sleep(3); subprocess.run(['docker', 'kill', a.kill_worker], capture_output=True); killed = round(time.time() - queued_at, 1)
        subprocess.run(['docker', 'start', a.kill_worker], capture_output=True)

    def finish(i):
        while time.time() - queued_at < 1800:
            j = next(j for j in owners[i].call('GET', '/api/jobs', label='/api/jobs', expect=200).json() if j['id'] == jobs[i])
            if j['status'] not in ('QUEUED', 'RUNNING'): break
            time.sleep(2)
        assert j['status'] == 'SUCCEEDED', j
        link = owners[i].call('POST', f'/api/jobs/{jobs[i]}/download-link', label='/api/jobs/{id}/download-link', expect=200).json()
        dl = owners[i].call('GET', link['url'], label='/api/media/{token}', follow_redirects=True)
        assert dl.status_code == 200 and dl.content[4:8] == b'ftyp', dl.status_code
        return {'attempts': j['attempts'], 'seconds_after_queue': round(time.time() - queued_at, 1)}
    renders = phase('renders_and_downloads', finish, range(a.orgs))

    # 6. roles and isolation
    def roles(i):
        r = viewers[i].call('POST', '/api/products', data={'name': 'x'}, files={'image': ('p.png', b'x', 'image/png')}, label='/api/products (viewer, refused)')
        assert r.status_code == 403
        assert viewers[i].call('GET', f'/api/decisions/{dids[i]}', label='/api/decisions/{id}').status_code == 200
        r = editors[i].call('POST', f'/api/jobs/{jobs[i]}/download-link', label='/api/jobs/{id}/download-link (editor, refused)')
        assert r.status_code == 403
    phase('role_checks', roles, range(a.orgs))

    def probe(i):
        leaks, refused = [], 0
        for k in range(a.orgs):
            if k == i: continue
            vid = content_ids[k][1][0]; pid = content_ids[k][0][0]
            for method, url in (('GET', f'/api/products/{pid}/image'), ('GET', f'/api/videos/{vid}/stream'), ('GET', f'/api/decisions/{dids[k]}'),
                                ('GET', f'/api/decisions/{dids[k]}/preview?video_id={vid}&product_id={pid}'), ('GET', f'/api/placements/{placements[k]}'),
                                ('GET', f'/api/placements/{placements[k]}/preview?t=1'), ('GET', f'/api/jobs/{jobs[k]}/download'),
                                ('POST', f'/api/jobs/{jobs[k]}/download-link'), ('POST', f'/api/decisions/{dids[k]}/apply'), ('POST', f'/api/placements/{placements[k]}/render')):
                kw = {'json': {'video_id': vid}} if url.endswith('/apply') else {}
                r = owners[i].call(method, url, label='isolation probe', follow_redirects=False, **kw)
                if 200 <= r.status_code < 400: leaks.append(f'{method} {url} -> {r.status_code}')
                else: refused += 1
        return leaks, refused
    probes = phase('isolation_probes', probe, range(a.orgs))
    stop.set()

    lat = {}
    for key in sorted({k for k, _, _ in SAMPLES}):
        xs = sorted(ms for k, ms, _ in SAMPLES if k == key)
        lat[key] = {'n': len(xs), 'p50_ms': round(statistics.median(xs)), 'p95_ms': round(xs[min(len(xs) - 1, int(len(xs) * .95))])}
    report = {
        'app': a.app, 'workspaces': a.orgs, 'users': a.orgs * 3, 'requests': len(SAMPLES), 'errors': ERRORS,
        'unexpected_5xx': sum(1 for _, _, s in SAMPLES if s >= 500),
        'decisions': {'succeeded': sum(d['status'] == 'SUCCEEDED' for d, _ in decisions), 'videos_assigned': assigned, 'videos_total': a.orgs * 2,
                      'seconds_median': round(statistics.median(s for _, s in decisions), 1), 'seconds_max': round(max(s for _, s in decisions), 1)},
        'renders': {'succeeded': len(renders), 'retried_after_worker_kill': sum(r['attempts'] > 1 for r in renders), 'worker_killed_after_s': killed,
                    'all_done_after_s': max(r['seconds_after_queue'] for r in renders)},
        'isolation': {'probes_refused': sum(r for _, r in probes), 'leaks': [x for lk, _ in probes for x in lk]},
        'postgres_connections_max': pg['max'] if a.database_url else None, 'phases_s': phases, 'total_s': round(time.time() - t0, 1), 'latency': lat,
    }
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != 'latency'}, indent=1))
    ok = not ERRORS and not report['isolation']['leaks'] and report['unexpected_5xx'] == 0
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
