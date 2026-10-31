"""End-to-end test against real processes: uvicorn API + separate worker.py, over HTTP. Nothing is mocked.

Covers the release workflow (passwordless sign-up, upload, decision engine run on the worker, apply, reject
unsuitable, correct, validate, approve, render, validate output, authorized/unauthorized download,
edit -> invalidation) and worker crash recovery (kill -> lease expiry -> another worker completes the job).
Skip with CC_SKIP_E2E=1.
"""
import json, os, re, socket, subprocess, sys, time
from pathlib import Path
import httpx
import pytest

from app import main
from benchmark.fixtures import generate, product_image
from benchmark import fixtures_scene as scene

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(os.getenv('CC_SKIP_E2E') == '1' or not (main.tool_exists('ffmpeg') and main.tool_exists('ffprobe')),
                                reason='E2E disabled or FFmpeg missing')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


class Session:
    """HTTP session that mirrors the web app: double-submit CSRF header, email sign-in."""
    def __init__(self, base, outbox):
        self.c = httpx.Client(base_url=base, timeout=120); self.outbox = outbox

    def req(self, method, url, **kw):
        if method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            kw.setdefault('headers', {})['X-CSRF-Token'] = self.c.cookies.get('cc_csrf', '')
        return self.c.request(method, url, **kw)

    def login(self, email):
        """Request a code, read it from the outbox, verify: exactly what a person does with the email."""
        self.c.cookies.clear(); self.c.get('/api/auth/csrf')
        assert self.req('POST', '/api/auth/email/start', json={'email': email}).status_code == 200
        text = json.loads((self.outbox / 'latest.json').read_text())['text']
        code = re.search(r'enter this code: (\d{6})', text).group(1)
        r = self.req('POST', '/api/auth/email/verify', json={'email': email, 'code': code}); assert r.status_code == 200, r.text
        return r.json()['status']

    def signup(self, name, email, org):
        assert self.login(email) == 'signup_required'
        r = self.req('POST', '/api/auth/signup', json={'name': name, 'organization': org}); assert r.status_code == 200, r.text


@pytest.fixture()
def stack(tmp_path):
    env = dict(os.environ, CC_DATA_DIR=str(tmp_path / 'data'), CC_INLINE_WORKER='0', CC_JOB_LEASE_SECONDS='3', PYTHONPATH=str(ROOT),
               CC_MAIL_BACKEND='outbox', CC_AUTH_RATE_PER_MINUTE='100', CC_DECISION_WINDOW_SECONDS='2.5', CC_DECISION_CANDIDATES_PER_VIDEO='3')
    pg_url, schema = os.getenv('CC_TEST_DATABASE_URL'), None
    if pg_url:   # API and worker processes share one PostgreSQL schema
        import psycopg, secrets
        schema = 't_e2e_' + secrets.token_hex(4)
        with psycopg.connect(pg_url, autocommit=True) as con: con.execute(f'CREATE SCHEMA {schema}')
        env.update(DATABASE_URL=pg_url, CC_DATABASE_SCHEMA=schema)
    else:
        env.pop('DATABASE_URL', None); env.pop('CC_DATABASE_URL', None)
    port = free_port()
    logs = tmp_path / 'logs'; logs.mkdir()
    api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port), '--timeout-keep-alive', '75'],
                           cwd=ROOT, env=env, stdout=open(logs / 'api.log', 'wb'), stderr=subprocess.STDOUT)
    workers = []

    def start_worker():
        w = subprocess.Popen([sys.executable, 'worker.py'], cwd=ROOT, env=env, stdout=open(logs / f'worker{len(workers)}.log', 'wb'), stderr=subprocess.STDOUT)
        workers.append(w); return w
    base = f'http://127.0.0.1:{port}'
    for _ in range(100):
        try:
            if httpx.get(base + '/api/health', timeout=1).status_code == 200: break
        except httpx.HTTPError: time.sleep(.2)
    else:
        api.kill(); raise AssertionError('API did not start: ' + (logs / 'api.log').read_text(errors='replace'))
    yield base, start_worker, tmp_path
    for p in [api, *workers]:
        p.kill(); p.wait()
    if schema:
        with psycopg.connect(pg_url, autocommit=True) as con: con.execute(f'DROP SCHEMA {schema} CASCADE')


def wait_for(fn, timeout=120, interval=.25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = fn()
        if out: return out
        time.sleep(interval)
    raise AssertionError('timed out')


def approve(s, pl, body_quality='PASSED'):
    q = s.req('POST', f'/api/placements/{pl}/quality-check').json()
    assert q['status'] == body_quality, q
    r = s.req('POST', f'/api/placements/{pl}/approve', json={'fit_confidence': .9}).json()
    assert r['status'] == 'APPROVED', r
    return q


def finished_job(s, jid=None):
    return next((j for j in s.req('GET', '/api/jobs').json() if (jid is None or j['id'] == jid) and j['status'] not in ('QUEUED', 'RUNNING')), None)


def finished_decision(s, did):
    d = s.req('GET', f'/api/decisions/{did}').json()
    return d if d['status'] not in ('QUEUED', 'RUNNING') else None


def test_release_workflow_and_worker_crash_recovery(stack):
    base, start_worker, tmp = stack
    worker = start_worker()
    s = Session(base, tmp / 'data' / 'outbox')
    # 1. Passwordless sign-up creates the workspace.
    s.signup('Owner', 'owner@acme.io', 'Acme')
    # 2. Videos and products; the decision engine runs on the separate worker process.
    good, bad = generate('handheld', 11, tmp / 'clips'), generate('occluder', 11, tmp / 'clips')
    room = scene.generate('room', 4, tmp / 'clips')['path']
    vids = {}
    for name, path in (('good', good['path']), ('bad', bad['path']), ('room', room)):
        r = s.req('POST', '/api/videos', data={'title': name, 'license_confirmed': 'true'}, files={'video': (f'{name}.mp4', Path(path).read_bytes(), 'video/mp4')})
        assert r.status_code == 200, r.text
        vids[name] = r.json()['id']
    prod = product_image(tmp / 'product.png')
    pid = s.req('POST', '/api/products', data={'name': 'Bottle', 'category': 'beverage'}, files={'image': ('product.png', Path(prod).read_bytes(), 'image/png')}).json()['id']
    sleeve = scene.product(tmp / 'sleeve.png', 160, 100, (180, 80, 30))
    pid2 = s.req('POST', '/api/products', data={'name': 'Sleeve', 'category': 'tech'}, files={'image': ('sleeve.png', Path(sleeve).read_bytes(), 'image/png')}).json()['id']
    did = s.req('POST', '/api/decisions', json={'product_ids': [pid, pid2], 'video_ids': [vids['room']]}).json()['id']
    d = wait_for(lambda: finished_decision(s, did), timeout=300)
    assert d['status'] == 'SUCCEEDED' and d['result']['videos'][0]['status'] == 'assigned', d
    applied = s.req('POST', f'/api/decisions/{did}/apply', json={'video_id': vids['room']}).json()['placement_id']
    assert s.req('POST', f'/api/placements/{applied}/quality-check').json()['status'] == 'PASSED'
    for name in ('good', 'bad'):
        assert len(s.req('POST', f'/api/videos/{vids[name]}/analyze').json()['scenes']) >= 1
    # 3. Candidate placements; the occluded one is rejected with evidence.
    body = lambda vid, quad: {'video_id': vid, 'product_id': pid, 'frame_second': .4, 'x': .5, 'y': .5, 'width': .2, 'start_second': .4, 'end_second': 2.8, 'tracking_quad': quad}
    rej = s.req('POST', '/api/placements', json=body(vids['bad'], bad['quad_norm'])).json()['id']
    q = s.req('POST', f'/api/placements/{rej}/quality-check').json()
    assert q['status'] == 'FAILED' and 'PARTIAL_OCCLUSION_UNSUPPORTED' in [f['code'] for f in q['failures']]
    # 4. A suitable candidate drawn slightly off, then corrected by a person (new version).
    pl = s.req('POST', '/api/placements', json=body(vids['good'], [[x + .01, y + .01] for x, y in good['quad_norm']])).json()['id']
    r = s.req('PATCH', f'/api/placements/{pl}/revise', data={'frame_second': .4, 'x': .5, 'y': .5, 'width': .2, 'start_second': .4, 'end_second': 2.8, 'tracking_quad': json.dumps(good['quad_norm'])})
    assert r.json()['version'] == 2
    # 5. Full-interval validation and a version-bound approval.
    q = approve(s, pl)
    assert q['frames_validated'] == q['frames_in_interval']
    # 6. Real render by the separate worker process, output validated.
    assert s.req('POST', f'/api/placements/{pl}/render').status_code == 200
    job = wait_for(lambda: finished_job(s))
    assert job['status'] == 'SUCCEEDED', job
    assert job['validation']['status'] == 'PASSED' and job['placement_version'] == 2
    # 7. Authorized download; unauthorized downloads fail.
    dl = s.req('GET', job['download_url'])
    assert dl.status_code == 200 and dl.content[4:8] == b'ftyp' and len(dl.content) > 10000
    assert httpx.get(base + job['download_url']).status_code == 401
    other = Session(base, tmp / 'data' / 'outbox'); other.signup('Rival', 'rival@example.com', 'Rival Co')
    assert other.req('GET', job['download_url']).status_code == 404
    # 8. Editing invalidates the approval and the delivered render.
    s.req('PATCH', f'/api/placements/{pl}/revise', data={'frame_second': .4, 'x': .5, 'y': .5, 'width': .2, 'start_second': .4, 'end_second': 2.4, 'tracking_quad': json.dumps(good['quad_norm'])})
    assert s.req('GET', job['download_url']).json()['detail']['code'] == 'SUPERSEDED_VERSION'
    assert s.req('POST', f'/api/placements/{pl}/render').json()['detail']['code'] == 'NOT_APPROVED'

    # Crash recovery: a longer 1280x720 overlay render; kill the worker mid-job; a new worker finishes it.
    long_clip = tmp / 'long.mp4'
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=25:duration=24', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=24', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(long_clip)], check=True, timeout=120)
    vid = s.req('POST', '/api/videos', data={'title': 'long', 'license_confirmed': 'true'}, files={'video': ('long.mp4', long_clip.read_bytes(), 'video/mp4')}).json()['id']
    pl2 = s.req('POST', '/api/placements', json={'video_id': vid, 'product_id': pid, 'frame_second': 1, 'x': .7, 'y': .1, 'width': .2, 'start_second': 1, 'end_second': 22}).json()['id']
    assert approve(s, pl2)['scene_class'] == 'OVERLAY_SCREEN_SPACE'
    jid = s.req('POST', f'/api/placements/{pl2}/render').json()['id']
    wait_for(lambda: any(j['id'] == jid and j['status'] == 'RUNNING' for j in s.req('GET', '/api/jobs').json()), interval=.1)
    time.sleep(1.0)
    worker.kill(); worker.wait()
    assert next(j for j in s.req('GET', '/api/jobs').json() if j['id'] == jid)['status'] == 'RUNNING'
    start_worker()
    done = wait_for(lambda: finished_job(s, jid), timeout=240)
    assert done['status'] == 'SUCCEEDED' and done['attempts'] == 2, done
    events = [e['event_type'] for e in s.req('GET', '/api/observability').json()['queue_events'] if e['job_id'] == jid]
    assert 'lease_recovered' in events
    assert s.req('GET', done['download_url']).status_code == 200
