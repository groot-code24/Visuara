"""Ten-organization concurrency and isolation test against a running deployment (API + separate workers).

    python qa/load_10_orgs.py --api http://localhost:8000 [--brands 10 --creators 5 --concurrency 10]
                               [--database-url postgresql://...] [--out docs/v10/evidence/load_10_orgs.json]

Simulates independent workspaces doing real work at the same time, through the public HTTP API only:
  * 10 brand workspaces (owner + manager + editor) and 5 creator workspaces (owner + manager): password sign-up,
    invitations, concurrent sign-ins;
  * creators upload and analyse two videos each and list them; brands create products and campaigns, search the
    marketplace and send offers; creators accept (one licensed video per brand);
  * every brand places its product, runs the quality check, gets creator consent and approval, then all brands
    queue renders at once; the workers render, validate and publish; brands request download links and download;
  * every brand then probes every other brand's private resources (products, placements, campaigns, jobs,
    downloads, usage) and the creators' unlicensed source videos. Any 2xx is a cross-tenant leak.
Measures API latency per endpoint, errors, render queue delay and duration, and (with --database-url) PostgreSQL
connection counts; `docker stats` samples container CPU and memory when Docker is available.
Expected refusals (403/404) during the isolation phase are counted separately from errors.
"""
import argparse, json, os, statistics, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PW = 'load-test-password-123'
LAT, ERRORS, LOCK = {}, [], threading.Lock()


class User:
    def __init__(self, api, email):
        self.email = email
        self.c = httpx.Client(base_url=api, timeout=600)

    def call(self, method, path, expect=(200,), label=None, **kw):
        if method in ('POST', 'PUT', 'PATCH', 'DELETE') and self.c.cookies.get('cc_csrf'):
            kw.setdefault('headers', {})['X-CSRF-Token'] = self.c.cookies.get('cc_csrf')
        t = time.perf_counter()
        try: r = self.c.request(method, path, **kw)
        except Exception as e:
            with LOCK: ERRORS.append({'call': f'{method} {label or path}', 'error': str(e)[:200]})
            raise
        ms = (time.perf_counter() - t) * 1000
        key = f'{method} {label or path}'
        with LOCK:
            LAT.setdefault(key, []).append(ms)
            if r.status_code not in expect: ERRORS.append({'call': key, 'status': r.status_code, 'body': r.text[:300]})
        return r


def register(api, name, email, role, org=None, token=None):
    u = User(api, email)
    u.c.get('/app')   # CSRF cookie
    body = {'name': name, 'email': email, 'password': PW, 'role': role}
    if token: body['invitation_token'] = token
    else: body['organization'] = org
    r = u.call('POST', '/api/auth/register', json=body, label='/api/auth/register')
    assert r.status_code == 200, r.text
    u.me = r.json()['user']
    return u


def login(u):
    u.c.cookies.clear(); u.c.get('/app')
    r = u.call('POST', '/api/auth/login', json={'email': u.email, 'password': PW}, label='/api/auth/login')
    assert r.status_code == 200, r.text


def pct(xs, q): xs = sorted(xs); return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def docker_sampler(stop, samples):
    while not stop.is_set():
        try:
            out = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{json .}}'], capture_output=True, text=True, timeout=30).stdout
            for line in out.splitlines():
                d = json.loads(line); samples.append({'t': round(time.time(), 1), 'name': d['Name'], 'cpu': d['CPUPerc'], 'mem': d['MemUsage']})
        except Exception:
            return
        stop.wait(5)


def pg_sampler(url, stop, samples):
    try: import psycopg
    except ImportError: return
    while not stop.is_set():
        try:
            with psycopg.connect(url, autocommit=True) as con:
                n = con.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()").fetchone()[0]
            samples.append(n)
        except Exception:
            pass
        stop.wait(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--api', default='http://localhost:8000'); ap.add_argument('--brands', type=int, default=10)
    ap.add_argument('--creators', type=int, default=5); ap.add_argument('--concurrency', type=int, default=10)
    ap.add_argument('--database-url', default=''); ap.add_argument('--out', default=str(ROOT / 'docs' / 'v10' / 'evidence' / 'load_10_orgs.json'))
    ap.add_argument('--tag', default=str(int(time.time())))
    ap.add_argument('--kill-worker', default='', help='container to SIGKILL once renders are running (crash-recovery drill), e.g. context-commerce-worker-1')
    a = ap.parse_args(); api, tag, pool = a.api.rstrip('/'), a.tag, ThreadPoolExecutor(a.concurrency)
    from benchmark.fixtures import generate, product_image
    work = ROOT / 'data' / f'loadtest_{tag}'; work.mkdir(parents=True, exist_ok=True)
    stop, dsamples, psamples = threading.Event(), [], []
    threading.Thread(target=docker_sampler, args=(stop, dsamples), daemon=True).start()
    if a.database_url: threading.Thread(target=pg_sampler, args=(a.database_url, stop, psamples), daemon=True).start()
    phases, t0 = {}, time.time()

    def phase(name, fn, items):
        t = time.time(); out = list(pool.map(fn, items)); phases[name] = round(time.time() - t, 1); print(f'{name}: {phases[name]} s', flush=True); return out

    # 1. workspaces and teams
    def mk_brand(i):
        owner = register(api, f'Brand {i} owner', f'owner{i}.{tag}@brand{i}.test', 'brand', f'Brand {i} {tag}')
        team = [owner]
        for role in ('manager', 'editor'):
            tok = owner.call('POST', '/api/invitations', json={'email': f'{role}{i}.{tag}@brand{i}.test', 'member_role': role}, label='/api/invitations').json()['invitation_token']
            team.append(register(api, f'Brand {i} {role}', f'{role}{i}.{tag}@brand{i}.test', 'brand', token=tok))
        return team
    def mk_creator(i):
        owner = register(api, f'Creator {i}', f'owner{i}.{tag}@creator{i}.test', 'creator', f'Creator {i} {tag}')
        tok = owner.call('POST', '/api/invitations', json={'email': f'manager{i}.{tag}@creator{i}.test', 'member_role': 'manager'}, label='/api/invitations').json()['invitation_token']
        return [owner, register(api, f'Creator {i} manager', f'manager{i}.{tag}@creator{i}.test', 'creator', token=tok)]
    brands = phase('signup_brands', mk_brand, range(a.brands))
    creators = phase('signup_creators', mk_creator, range(a.creators))
    phase('concurrent_signin', login, [u for team in brands + creators for u in team])

    # 2. creators upload, analyse and list two videos each (the rights holder must be the uploader)
    clips = [generate('static', 100 + k, work / f'clip{k}') for k in range(2 * a.creators)]
    def upload(k):
        owner = creators[k // 2][0]; meta = clips[k]
        r = owner.call('POST', '/api/videos', data={'title': f'Clip {k}', 'license_confirmed': 'true'},
                       files={'video': (Path(meta['path']).name, Path(meta['path']).read_bytes(), 'video/mp4')}, label='/api/videos')
        vid = r.json()['id']
        owner.call('POST', f'/api/videos/{vid}/analyze', label='/api/videos/{id}/analyze')
        owner.call('PUT', f'/api/videos/{vid}/listing', json={'price_usd': 100 + k, 'categories': [f'cat{k}'], 'channels': ['social', 'web']}, label='/api/videos/{id}/listing')
        return vid
    vids = phase('creator_upload_analyse_list', upload, range(2 * a.creators))

    # 3. brands: product, campaign, marketplace search, offer; creators accept
    prod_png = product_image(work / 'product.png')
    def brand_setup(i):
        owner, manager, editor = brands[i]
        pid = editor.call('POST', '/api/products', data={'name': f'Product {i}'}, files={'image': ('p.png', Path(prod_png).read_bytes(), 'image/png')}, label='/api/products').json()['id']
        cid = manager.call('POST', '/api/campaigns', json={'name': f'Campaign {i}', 'category': f'cat{i}', 'budget_usd': 5000, 'channels': ['social']}, label='/api/campaigns').json()['id']
        manager.call('GET', f'/api/marketplace?campaign_id={cid}', label='/api/marketplace')
        k = i % len(vids)
        oid = manager.call('POST', '/api/offers', json={'campaign_id': cid, 'video_id': vids[k]}, label='/api/offers').json()['id']
        return {'pid': pid, 'cid': cid, 'vid': vids[k], 'clip': k, 'oid': oid}
    deals = phase('brand_product_campaign_search_offer', brand_setup, range(a.brands))
    def accept(i):
        d = deals[i]; creators[d['clip'] // 2][0].call('POST', f'/api/offers/{d["oid"]}/accept', label='/api/offers/{id}/accept')
    phase('creator_accept_offers', accept, range(a.brands))

    # 4. placement, quality check, consent, approvals; then all renders at once
    def place(i):
        owner, manager, editor = brands[i]; d = deals[i]; meta = clips[d['clip']]
        body = {'video_id': d['vid'], 'product_id': d['pid'], 'frame_second': .4, 'x': .5, 'y': .5, 'width': .2, 'start_second': .4, 'end_second': 2.8, 'tracking_quad': meta['quad_norm']}
        pl = editor.call('POST', '/api/placements', json=body, label='/api/placements').json()['id']
        q = editor.call('POST', f'/api/placements/{pl}/quality-check', label='/api/placements/{id}/quality-check').json()
        manager.call('POST', f'/api/placements/{pl}/fit-review', data={'decision': 'accept', 'confidence': '0.9'}, label='/api/placements/{id}/fit-review')
        manager.call('POST', f'/api/placements/{pl}/propose', label='/api/placements/{id}/propose')
        cr = creators[d['clip'] // 2][0]
        cr.call('PUT', f'/api/placements/{pl}/rights', json={'permitted_channels': ['social'], 'territories': ['WORLDWIDE'], 'creator_consent': True}, label='/api/placements/{id}/rights')
        cr.call('POST', f'/api/placements/{pl}/approve', label='/api/placements/{id}/approve')
        r = manager.call('POST', f'/api/placements/{pl}/approve', label='/api/placements/{id}/approve').json()
        d.update(pl=pl, quality=q.get('status'), ready=r.get('status'))
    phase('placement_quality_consent_approval', place, range(a.brands))
    t_queue = time.time()
    def render(i):
        d = deals[i]; r = brands[i][1].call('POST', f'/api/placements/{d["pl"]}/render', label='/api/placements/{id}/render')
        d['job'] = r.json().get('id')
    phase('queue_renders', render, range(a.brands))
    chaos = None
    if a.kill_worker:   # crash a worker mid-render (SIGKILL: no graceful hand-back); its lease must expire and be recovered
        t = time.time()
        while time.time() - t < 120:
            states = [next((j['status'] for j in brands[i][1].call('GET', '/api/jobs', label='/api/jobs').json() if j['id'] == deals[i]['job']), None) for i in range(a.brands)]
            if states.count('RUNNING') >= 2: break
            time.sleep(1)
        r = subprocess.run(['docker', 'kill', a.kill_worker], capture_output=True, text=True)
        chaos = {'killed': a.kill_worker, 'at_seconds_after_queue': round(time.time() - t_queue, 1), 'docker': (r.stdout or r.stderr).strip(), 'running_when_killed': states.count('RUNNING')}
        print('chaos:', chaos, flush=True)
    deadline = time.time() + 1800
    while time.time() < deadline:
        js = [next((j for j in brands[i][1].call('GET', '/api/jobs', label='/api/jobs').json() if j['id'] == deals[i]['job']), {}) for i in range(a.brands)]
        if all(j.get('status') not in ('QUEUED', 'RUNNING', None) for j in js): break
        time.sleep(3)
    phases['render_all_jobs'] = round(time.time() - t_queue, 1)
    jobs = [{'org': i, **{k: j.get(k) for k in ('id', 'status', 'created_at', 'started_at', 'finished_at', 'attempts', 'error')}} for i, j in enumerate(js)]
    def deliver(i):
        m = brands[i][1]; jid = deals[i]['job']
        link = m.call('POST', f'/api/jobs/{jid}/download-link', label='/api/jobs/{id}/download-link').json()
        r = m.call('GET', link['url'], label='/api/media/{token}', follow_redirects=True)
        deals[i]['download_bytes'] = len(r.content)
        r2 = brands[i][2].call('GET', f'/api/jobs/{jid}/download', expect=(403,), label='/api/jobs/{id}/download (editor, expect 403)')
    phase('download_links_and_downloads', deliver, range(a.brands))

    # 5. cross-tenant isolation probes
    leaks, refused = [], 0
    def probe(i):
        nonlocal refused
        attacker = brands[i][1]
        for j in range(a.brands):
            if j == i: continue
            d = deals[j]
            for path in (f'/api/products/{d["pid"]}/image', f'/api/placements/{d["pl"]}', f'/api/placements/{d["pl"]}/preview',
                         f'/api/campaigns/{d["cid"]}/report', f'/api/jobs/{d["job"]}/download', f'/api/offers/{d["oid"]}/payments'):
                r = attacker.call('GET', path, expect=(403, 404, 409), label='isolation probe GET')
                if r.status_code < 300 or r.status_code in (301, 302, 307): leaks.append({'attacker_org': i, 'path': path, 'status': r.status_code})
                else:
                    with LOCK: refused += 1
            r = attacker.call('POST', f'/api/jobs/{d["job"]}/download-link', expect=(403, 404, 409), label='isolation probe POST')
            if r.status_code < 300: leaks.append({'attacker_org': i, 'path': 'download-link', 'status': r.status_code})
            else:
                with LOCK: refused += 1
        for k, vid in enumerate(vids):   # unlicensed creator source video
            if k == deals[i]['clip']: continue
            r = attacker.call('GET', f'/api/videos/{vid}/stream', expect=(403, 404), label='isolation probe GET')
            if r.status_code < 300 or r.status_code == 307: leaks.append({'attacker_org': i, 'path': f'/api/videos/{vid}/stream', 'status': r.status_code})
            else:
                with LOCK: refused += 1
    phase('cross_tenant_probes', probe, range(a.brands))
    stop.set()

    def ts(s): return time.mktime(time.strptime(s, '%Y-%m-%d %H:%M:%S')) if s else None
    qd = [ts(j['started_at']) - ts(j['created_at']) for j in jobs if j['started_at'] and j['created_at']]
    rd = [ts(j['finished_at']) - ts(j['started_at']) for j in jobs if j['finished_at'] and j['started_at']]
    unexpected = [e for e in ERRORS]
    report = {
        'config': {'api': api, 'brands': a.brands, 'creators': a.creators, 'users': sum(len(t) for t in brands + creators), 'concurrency': a.concurrency},
        'phases_seconds': phases, 'total_seconds': round(time.time() - t0, 1),
        'latency_ms': {k: {'n': len(v), 'p50': pct(v, .5), 'p95': pct(v, .95), 'max': round(max(v), 1)} for k, v in sorted(LAT.items())},
        'requests': sum(len(v) for v in LAT.values()), 'unexpected_responses': len(unexpected), 'unexpected_detail': unexpected[:30],
        'quality_checks': {s: sum(1 for d in deals if d.get('quality') == s) for s in {d.get('quality') for d in deals}},
        'ready_for_render': sum(1 for d in deals if d.get('ready') == 'READY_FOR_RENDER'),
        'jobs': jobs, 'jobs_succeeded': sum(1 for j in jobs if j['status'] == 'SUCCEEDED'),
        'queue_delay_seconds': {'median': round(statistics.median(qd), 1) if qd else None, 'max': max(qd) if qd else None},
        'render_seconds': {'median': round(statistics.median(rd), 1) if rd else None, 'max': max(rd) if rd else None},
        'downloads_ok': sum(1 for d in deals if d.get('download_bytes', 0) > 1000),
        'isolation': {'probes_refused': refused, 'leaks': leaks},
        'worker_crash_drill': chaos, 'jobs_with_retries': [j for j in jobs if (j.get('attempts') or 0) > 1],
        'db_connections': {'samples': len(psamples), 'max': max(psamples) if psamples else None, 'median': statistics.median(psamples) if psamples else None},
        'containers': {name: {'cpu_max': max(float(s['cpu'].rstrip('%')) for s in dsamples if s['name'] == name),
                              'mem_last': [s['mem'] for s in dsamples if s['name'] == name][-1]} for name in {s['name'] for s in dsamples}},
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True); Path(a.out).write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report[k] for k in ('config', 'total_seconds', 'requests', 'unexpected_responses', 'jobs_succeeded', 'queue_delay_seconds', 'render_seconds', 'downloads_ok', 'isolation', 'db_connections')}, indent=1))
    ok = not leaks and not unexpected and report['jobs_succeeded'] == a.brands and report['downloads_ok'] == a.brands
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
