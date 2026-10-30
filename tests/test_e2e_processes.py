"""End-to-end test against real processes: uvicorn API + separate worker.py, over HTTP. Nothing is mocked.

Covers the release workflow (register, upload, analyse, reject unsuitable, correct, validate, approve,
verify rights, render, validate output, authorized/unauthorized download, edit -> invalidation)
and worker crash recovery (kill -> lease expiry -> another worker completes the job).
Skip with CC_SKIP_E2E=1.
"""
import json, os, socket, subprocess, sys, time
from pathlib import Path
import httpx
import pytest

from app import main
from benchmark.fixtures import generate, product_image

ROOT = Path(__file__).resolve().parent.parent
PW = 'strong-password-123'
pytestmark = pytest.mark.skipif(os.getenv('CC_SKIP_E2E') == '1' or not (main.tool_exists('ffmpeg') and main.tool_exists('ffprobe')),
                                reason='E2E disabled or FFmpeg missing')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


class Session:
    """HTTP session that mirrors the browser's double-submit CSRF behaviour."""
    def __init__(self, base):
        self.c = httpx.Client(base_url=base, timeout=120)

    def req(self, method, url, **kw):
        if method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            kw.setdefault('headers', {})['X-CSRF-Token'] = self.c.cookies.get('cc_csrf', '')
        return self.c.request(method, url, **kw)

    def login(self, email):
        self.c.cookies.clear(); self.c.get('/')
        r = self.req('POST', '/api/auth/login', json={'email': email, 'password': PW}); assert r.status_code == 200, r.text

    def register(self, name, email, role, org, token=None):
        self.c.cookies.clear(); self.c.get('/')
        r = self.req('POST', '/api/auth/register', json={'name': name, 'email': email, 'password': PW, 'role': role, 'organization': org, 'invitation_token': token})
        assert r.status_code == 200, r.text


@pytest.fixture()
def stack(tmp_path):
    env = dict(os.environ, CC_DATA_DIR=str(tmp_path / 'data'), CC_INLINE_WORKER='0', CC_JOB_LEASE_SECONDS='3', PYTHONPATH=str(ROOT))
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
    api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)],
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


def to_ready(s, pl, body_quality='PASSED'):
    q = s.req('POST', f'/api/placements/{pl}/quality-check').json()
    assert q['status'] == body_quality, q
    assert s.req('POST', f'/api/placements/{pl}/fit-review', data={'decision': 'accept', 'confidence': '0.9'}).status_code == 200
    assert s.req('POST', f'/api/placements/{pl}/propose').status_code == 200
    s.login('creator@example.com')
    assert s.req('PUT', f'/api/placements/{pl}/rights', json={'permitted_channels': ['social', 'web'], 'territories': ['WORLDWIDE'], 'creator_consent': True}).status_code == 200
    assert s.req('POST', f'/api/placements/{pl}/approve').json()['status'] == 'CREATOR_APPROVED'
    s.login('brand@example.com')
    r = s.req('POST', f'/api/placements/{pl}/approve').json()
    assert r['status'] == 'READY_FOR_RENDER', r
    return q


def test_release_workflow_and_worker_crash_recovery(stack):
    base, start_worker, tmp = stack
    worker = start_worker()
    s = Session(base)
    # 1-2. Register a brand, invite and register a creator.
    s.register('Brand', 'brand@example.com', 'brand', 'Acme Brand')
    token = s.req('POST', '/api/invitations', json={'email': 'creator@example.com', 'role': 'creator'}).json()['invitation_token']
    s.register('Creator', 'creator@example.com', 'creator', 'unused', token)
    # 4-5. Upload real test videos and analyse scenes.
    good, bad = generate('handheld', 11, tmp / 'clips'), generate('occluder', 11, tmp / 'clips')
    vids = {}
    for name, meta in (('good', good), ('bad', bad)):
        r = s.req('POST', '/api/videos', data={'title': name, 'license_confirmed': 'true'}, files={'video': (f'{name}.mp4', Path(meta['path']).read_bytes(), 'video/mp4')})
        assert r.status_code == 200, r.text
        vids[name] = r.json()['id']
        assert len(s.req('POST', f'/api/videos/{vids[name]}/analyze').json()['scenes']) >= 1
    # 3. Create a product.
    s.login('brand@example.com')
    prod = product_image(tmp / 'product.png')
    pid = s.req('POST', '/api/products', data={'name': 'Bottle'}, files={'image': ('product.png', Path(prod).read_bytes(), 'image/png')}).json()['id']
    # 6-7. Candidate placements; the occluded one is rejected with evidence.
    body = lambda vid, quad: {'video_id': vid, 'product_id': pid, 'frame_second': .4, 'x': .5, 'y': .5, 'width': .2, 'start_second': .4, 'end_second': 2.8, 'tracking_quad': quad}
    rej = s.req('POST', '/api/placements', json=body(vids['bad'], bad['quad_norm'])).json()['id']
    q = s.req('POST', f'/api/placements/{rej}/quality-check').json()
    assert q['status'] == 'FAILED' and 'PARTIAL_OCCLUSION_UNSUPPORTED' in [f['code'] for f in q['failures']]
    # 8. A suitable candidate drawn slightly off, then corrected by a human (new version).
    pl = s.req('POST', '/api/placements', json=body(vids['good'], [[x + .01, y + .01] for x, y in good['quad_norm']])).json()['id']
    r = s.req('PATCH', f'/api/placements/{pl}/revise', data={'frame_second': .4, 'x': .5, 'y': .5, 'width': .2, 'start_second': .4, 'end_second': 2.8, 'tracking_quad': json.dumps(good['quad_norm'])})
    assert r.json()['version'] == 2
    # 9-11. Full-interval validation, version-bound approvals, rights verification.
    q = to_ready(s, pl)
    assert q['frames_validated'] == q['frames_in_interval']
    # 12-13. Real render by the separate worker process, output validated.
    assert s.req('POST', f'/api/placements/{pl}/render').status_code == 200
    job = wait_for(lambda: next((j for j in s.req('GET', '/api/jobs').json() if j['status'] not in ('QUEUED', 'RUNNING')), None))
    assert job['status'] == 'SUCCEEDED', job
    assert job['validation']['status'] == 'PASSED' and job['placement_version'] == 2
    # 14. Authorized download.
    dl = s.req('GET', job['download_url'])
    assert dl.status_code == 200 and dl.content[4:8] == b'ftyp' and len(dl.content) > 10000
    # 15. Unauthorized downloads fail.
    anon = httpx.get(base + job['download_url'])
    assert anon.status_code == 401
    other = Session(base); other.register('Rival', 'rival@example.com', 'brand', 'Rival Co')
    assert other.req('GET', job['download_url']).status_code == 404
    # 16. Editing invalidates approvals and the delivered render.
    s.req('PATCH', f'/api/placements/{pl}/revise', data={'frame_second': .4, 'x': .5, 'y': .5, 'width': .2, 'start_second': .4, 'end_second': 2.4, 'tracking_quad': json.dumps(good['quad_norm'])})
    assert s.req('GET', job['download_url']).json()['detail']['code'] == 'SUPERSEDED_VERSION'
    assert s.req('POST', f'/api/placements/{pl}/render').json()['detail']['code'] == 'NOT_READY_FOR_RENDER'

    # Crash recovery: a longer 1280x720 overlay render; kill the worker mid-job; a new worker finishes it.
    s.login('creator@example.com')
    long_clip = tmp / 'long.mp4'
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=25:duration=24', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=24', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(long_clip)], check=True, timeout=120)
    vid = s.req('POST', '/api/videos', data={'title': 'long', 'license_confirmed': 'true'}, files={'video': ('long.mp4', long_clip.read_bytes(), 'video/mp4')}).json()['id']
    s.login('brand@example.com')
    pl2 = s.req('POST', '/api/placements', json={'video_id': vid, 'product_id': pid, 'frame_second': 1, 'x': .7, 'y': .1, 'width': .2, 'start_second': 1, 'end_second': 22}).json()['id']
    assert to_ready(s, pl2)['scene_class'] == 'OVERLAY_SCREEN_SPACE'
    jid = s.req('POST', f'/api/placements/{pl2}/render').json()['id']
    wait_for(lambda: any(j['id'] == jid and j['status'] == 'RUNNING' for j in s.req('GET', '/api/jobs').json()), interval=.1)
    time.sleep(1.0)
    worker.kill(); worker.wait()
    assert next(j for j in s.req('GET', '/api/jobs').json() if j['id'] == jid)['status'] == 'RUNNING'
    start_worker()
    done = wait_for(lambda: next((j for j in s.req('GET', '/api/jobs').json() if j['id'] == jid and j['status'] not in ('QUEUED', 'RUNNING')), None), timeout=240)
    assert done['status'] == 'SUCCEEDED' and done['attempts'] == 2, done
    events = [e['event_type'] for e in s.req('GET', '/api/observability').json()['queue_events'] if e['job_id'] == jid]
    assert 'lease_recovered' in events
    assert s.req('GET', done['download_url']).status_code == 200
