"""Shared test helpers: clients, workspace and session setup, media fixtures and the placement happy path.

`make_user` + `login` create accounts and sessions directly in the database, which keeps most tests fast and free of
sign-in rate limits. The real passwordless flows (email code, magic link, Google) are exercised in test_auth.py,
and `email_signin`/`signup` drive them end to end where a test needs the genuine path.
"""
import hashlib, json, os, re, secrets, time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, members
from benchmark.fixtures import generate, product_image

needs_media = pytest.mark.skipif(not (main.tool_exists('ffmpeg') and main.tool_exists('ffprobe') and main._has_cv2()),
                                 reason='FFmpeg, FFprobe and OpenCV are required')


def _model_installed():
    from app import detection
    return (detection.models_dir() / detection.MODELS[detection.DEFAULT_MODEL]['file']).exists()


# CI sets CC_REQUIRE_MODELS=1 after fetching the model, so there a missing model fails these tests instead of skipping them.
needs_model = pytest.mark.skipif(not _model_installed() and os.getenv('CC_REQUIRE_MODELS') != '1',
                                 reason='the object-detection model is not installed: python ops/fetch_models.py')


class CSRSTestClient(TestClient):
    """Mirrors the web app: sends the double-submit CSRF header on writes when signed in."""
    def request(self, method, url, **kwargs):
        if method.upper() in {'POST', 'PUT', 'PATCH', 'DELETE'} and self.cookies.get('cc_session'):
            headers = dict(kwargs.get('headers') or {})
            headers.setdefault('X-CSRF-Token', self.cookies.get('cc_csrf', ''))
            kwargs['headers'] = headers
        return super().request(method, url, **kwargs)


class _Res:
    def __init__(self, rows): self.rows = rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows
    def __iter__(self): return iter(self.rows)


class TestDB:
    """Autocommitting statement helper for tests that inspect or tamper with rows; works on SQLite and PostgreSQL."""
    __test__ = False
    def execute(self, sql, params=()):
        with main.db() as c: rows = c.execute(sql, params).fetchall()
        return _Res(rows)
    def commit(self): pass


# ---------------------------------------------------------------- accounts and sessions
def make_user(email, name='Owner', org='Acme', member_role='owner', org_id=None, plan='team'):
    """Create an account (and its workspace unless org_id is given) directly. Returns the user row as a dict."""
    with main.db() as c:
        if org_id is None:
            org_id = c.execute('INSERT INTO organizations(name,plan) VALUES(?,?)', (org, plan)).lastrowid
        uid = c.execute("INSERT INTO users(email,password_hash,name,role,org_id,member_role) VALUES(?,'',?,'brand',?,?)",
                        (email.lower(), name, org_id, member_role)).lastrowid
        members.sync_membership(c, uid)          # as sign-up does
        return dict(c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())


def login(c, email):
    """Sign `c` in as `email` with a fresh server-side session."""
    c.cookies.clear()
    token = secrets.token_urlsafe(32)
    with main.db() as con:
        uid = con.execute('SELECT id FROM users WHERE email=?', (email.lower(),)).fetchone()['id']
        con.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), uid, int(time.time()) + 3600))
    c.cookies.set('cc_session', token)
    c.get('/api/auth/csrf')
    return uid


def owner(c, email='owner@acme.io', org='Acme', plan='team'):
    u = make_user(email, 'Owner', org, plan=plan); login(c, email)
    return u


def last_mail(dirpath=None):
    d = json.loads(((dirpath or main.DATA) / 'outbox' / 'latest.json').read_text())
    code = re.search(r'enter this code: (\d{6})', d['text']).group(1)
    token = re.search(r'/verify#token=([A-Za-z0-9_-]+)', d['text']).group(1)
    return d, code, token


def email_signin(c, email):
    """Real email-code sign-in (needs CC_MAIL_BACKEND=outbox). Returns 'signed_in' or 'signup_required'."""
    c.cookies.clear()
    assert c.post('/api/auth/email/start', json={'email': email}).status_code == 200
    _, code, _ = last_mail()
    r = c.post('/api/auth/email/verify', json={'email': email, 'code': code})
    assert r.status_code == 200, r.text
    return r.json()['status']


def signup(c, email, name='Owner', org='Acme'):
    """Real passwordless sign-up: email code, then onboarding. Returns the new user."""
    assert email_signin(c, email) == 'signup_required'
    r = c.post('/api/auth/signup', json={'name': name, 'organization': org})
    assert r.status_code == 200, r.text
    return r.json()['user']


def invite_and_join(c, inviter_email, email, member_role):
    """Invite `email` as `inviter_email`, then accept through the real sign-in + onboarding path. Ends signed in as the invitee."""
    login(c, inviter_email)
    r = c.post('/api/invitations', json={'email': email, 'member_role': member_role})
    assert r.status_code == 200, r.text
    assert email_signin(c, email) == 'signup_required'
    inv = c.get('/api/auth/signup').json()['invitations'][0]
    r = c.post('/api/auth/signup', json={'name': member_role.title(), 'invitation_id': inv['id']})
    assert r.status_code == 200, r.text
    assert r.json()['user']['member_role'] == member_role
    return r.json()['user']['id']


def member_id(email): return TestDB().execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()['id']


# ---------------------------------------------------------------- media
def upload(c, path, title, analyze=True):
    r = c.post('/api/videos', data={'title': title, 'license_confirmed': 'true'},
               files={'video': (Path(path).name, Path(path).read_bytes(), 'video/mp4')})
    assert r.status_code == 200, r.text
    if analyze: assert c.post(f"/api/videos/{r.json()['id']}/analyze").status_code == 200
    return r.json()['id']


def add_product(c, tmp_path, name='Bottle', category='beverage', path=None):
    prod = path or product_image(tmp_path / f'{name.lower().replace(" ", "_")}.png')
    r = c.post('/api/products', data={'name': name, 'category': category}, files={'image': (Path(prod).name, Path(prod).read_bytes(), 'image/png')})
    assert r.status_code == 200, r.text
    return r.json()['id']


def tracked_body(vid, pid, meta):
    return {'video_id': vid, 'product_id': pid, 'frame_second': .4, 'x': .5, 'y': .5, 'width': .2,
            'start_second': .4, 'end_second': 2.8, 'tracking_quad': meta['quad_norm']}


def drive_to_approved(c, tmp_path, kind='pan_zoom', seed=5):
    """Full happy path up to APPROVED: owner signs in, uploads, adds a product, quality check passes, approval."""
    owner(c)
    meta = generate(kind, seed, tmp_path)
    vid = upload(c, meta['path'], kind)
    prod = add_product(c, tmp_path)
    pl = c.post('/api/placements', json=tracked_body(vid, prod, meta)).json()['id']
    q = c.post(f'/api/placements/{pl}/quality-check')
    assert q.status_code == 200 and q.json()['status'] == 'PASSED', q.text
    r = c.post(f'/api/placements/{pl}/approve', json={'fit_confidence': .9})
    assert r.status_code == 200 and r.json()['status'] == 'APPROVED', r.text
    return pl, meta


def wait_job(c, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = c.get('/api/jobs').json()
        if jobs and jobs[0]['status'] not in ('QUEUED', 'RUNNING'):
            return jobs[0]
        time.sleep(.2)
    raise AssertionError('render job did not finish')
