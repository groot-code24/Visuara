"""Platform: workspace roles and permissions, invitations, suspension and removal, quotas and plans, health split,
cross-origin protection, worker wake-up and fairness, job timeout and shutdown hand-back, direct-to-bucket uploads,
cache cleanup, operator metrics, Google ID-token signatures, log redaction, the production guard and the secret scanner."""
import base64, json, os, time
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from app import main, storage, obs, maintenance, limits
from app.auth import verify_rs256
from helpers import (CSRSTestClient, TestDB, make_user, login, owner, invite_and_join, member_id, email_signin, last_mail,
                     drive_to_approved, add_product, upload, tracked_body, needs_media)
from benchmark.fixtures import generate

BUCKET = 'cc-v10-private'


@pytest.fixture()
def team(manual, monkeypatch):
    """Owner plus one member of every invitable role, all joined through real invitations and email sign-in."""
    monkeypatch.setenv('CC_MAIL_BACKEND', 'outbox'); monkeypatch.setenv('CC_AUTH_RATE_PER_MINUTE', '100')
    owner(manual, 'owner@brand.io', 'Brand Co')
    for role in ('viewer', 'editor', 'manager', 'admin'):
        invite_and_join(manual, 'owner@brand.io', f'{role}@brand.io', role)
    return manual


def test_member_roles_gate_actions_server_side(team, tmp_path):
    c = team
    # viewer: read-only
    login(c, 'viewer@brand.io')
    assert c.get('/api/products').status_code == 200 and c.get('/api/members').status_code == 200
    r = c.post('/api/products', data={'name': 'x'}, files={'image': ('p.png', b'x', 'image/png')})
    assert r.status_code == 403 and r.json()['detail']['code'] == 'ROLE_FORBIDDEN'
    # editor: content yes; approvals, renders and team no
    login(c, 'editor@brand.io')
    assert add_product(c, tmp_path)
    assert c.post('/api/placements/999/approve').json()['detail']['code'] == 'ROLE_FORBIDDEN'
    assert c.post('/api/placements/999/render').json()['detail']['code'] == 'ROLE_FORBIDDEN'
    assert c.post('/api/invitations', json={'email': 'z@brand.io', 'member_role': 'viewer'}).status_code == 403
    # manager: approvals and renders pass the role check (then 404 for a missing placement); team no
    login(c, 'manager@brand.io')
    assert c.post('/api/placements/999/approve').status_code == 404
    assert c.post('/api/invitations', json={'email': 'z@brand.io', 'member_role': 'viewer'}).status_code == 403
    # admin: team yes, but cannot mint or promote admins
    login(c, 'admin@brand.io')
    assert c.post('/api/invitations', json={'email': 'z@brand.io', 'member_role': 'viewer'}).status_code == 200
    assert c.post('/api/invitations', json={'email': 'y@brand.io', 'member_role': 'admin'}).status_code == 403
    assert c.patch(f'/api/members/{member_id("viewer@brand.io")}', json={'member_role': 'editor'}).status_code == 200
    assert c.patch(f'/api/members/{member_id("viewer@brand.io")}', json={'member_role': 'admin'}).status_code == 403
    assert c.post(f'/api/members/{member_id("owner@brand.io")}/suspend').status_code == 409
    assert c.post(f'/api/members/{member_id("owner@brand.io")}/transfer-ownership').status_code == 403
    # another workspace's owner cannot touch these members
    owner(c, 'owner@other.io', 'Other Co')
    assert c.patch(f'/api/members/{member_id("editor@brand.io")}', json={'member_role': 'viewer'}).status_code == 404
    assert c.post(f'/api/members/{member_id("editor@brand.io")}/suspend').status_code == 404


def test_suspension_removal_and_ownership_transfer(team):
    c = team
    other = CSRSTestClient(main.app)
    login(other, 'editor@brand.io'); assert other.get('/api/me').status_code == 200
    login(c, 'owner@brand.io')
    ed = member_id('editor@brand.io')
    assert c.post(f'/api/members/{ed}/suspend').status_code == 200
    assert other.get('/api/me').status_code == 401                      # sessions ended immediately
    other.cookies.clear(); other.post('/api/auth/email/start', json={'email': 'editor@brand.io'}); _, code, _ = last_mail()
    assert other.post('/api/auth/email/verify', json={'email': 'editor@brand.io', 'code': code}).json()['detail']['code'] == 'ACCOUNT_SUSPENDED'
    assert c.post(f'/api/members/{ed}/reactivate').status_code == 200
    assert email_signin(other, 'editor@brand.io') == 'signed_in'
    assert c.delete(f'/api/members/{ed}').status_code == 200
    assert other.get('/api/me').status_code == 401
    assert email_signin(other, 'editor@brand.io') == 'signup_required'   # the email is freed; the old account is gone
    assert 'editor@brand.io' not in json.dumps(c.get('/api/members').json())
    # ownership moves only to an active admin; the old owner becomes admin
    assert c.post(f'/api/members/{member_id("owner@brand.io")}/transfer-ownership').status_code == 409
    adm = member_id('admin@brand.io')
    assert c.post(f'/api/members/{adm}/transfer-ownership').json()['owner'] == adm
    assert c.get('/api/me').json()['member_role'] == 'admin'
    roles = {m['email']: m['member_role'] for m in c.get('/api/members').json()['members']}
    assert roles['owner@brand.io'] == 'admin' and roles['admin@brand.io'] == 'owner'
    events = [r['event_type'] for r in TestDB().execute("SELECT event_type FROM events WHERE event_type LIKE 'member.%' ORDER BY id").fetchall()]
    assert events == ['member.suspended', 'member.reactivated', 'member.removed', 'member.ownership_transferred']


def test_invitations_revoke_expire_single_use(manual, monkeypatch):
    monkeypatch.setenv('CC_MAIL_BACKEND', 'outbox')
    c = manual
    owner(c, 'owner@brand.io', 'Brand Co')
    inv = c.post('/api/invitations', json={'email': 'a@brand.io', 'member_role': 'viewer'}).json()
    assert inv['emailed'] is True and 'invitation_token' not in inv
    assert c.delete(f'/api/invitations/{inv["id"]}').status_code == 200
    assert c.delete(f'/api/invitations/{inv["id"]}').status_code == 404
    assert {i['email']: i['status'] for i in c.get('/api/invitations').json()}['a@brand.io'] == 'revoked'
    monkeypatch.setenv('CC_INVITATION_TTL', '-1')
    c.post('/api/invitations', json={'email': 'b@brand.io', 'member_role': 'viewer'})
    monkeypatch.delenv('CC_INVITATION_TTL')
    assert {i['email']: i['status'] for i in c.get('/api/invitations').json()}['b@brand.io'] == 'expired'
    fresh = c.post('/api/invitations', json={'email': 'c@brand.io', 'member_role': 'editor'}).json()
    # Revoked and expired invitations are never offered at sign-up.
    for email in ('a@brand.io', 'b@brand.io'):
        assert email_signin(c, email) == 'signup_required' and c.get('/api/auth/signup').json()['invitations'] == []
    assert email_signin(c, 'c@brand.io') == 'signup_required'
    offered = c.get('/api/auth/signup').json()['invitations']
    assert [i['id'] for i in offered] == [fresh['id']] and offered[0]['member_role'] == 'editor'
    assert c.post('/api/auth/signup', json={'name': 'C', 'invitation_id': fresh['id']}).status_code == 200
    login(c, 'owner@brand.io')
    assert c.post('/api/invitations', json={'email': 'c@brand.io'}).status_code == 409          # already a member


def test_quotas_and_limits(manual, monkeypatch):
    owner(manual, 'owner@brand.io', 'Brand Co')
    monkeypatch.setenv('CC_ORG_STORAGE_QUOTA_MB', '0')
    r = manual.post('/api/products', data={'name': 'x'}, files={'image': ('p.png', b'0', 'image/png')})
    assert r.status_code == 413 and r.json()['detail']['code'] == 'QUOTA_STORAGE'
    monkeypatch.delenv('CC_ORG_STORAGE_QUOTA_MB')
    u = manual.get('/api/usage').json()
    assert u['effective']['active_jobs'] == 10 and u['usage']['members'] == 1 and u['plan'] == 'team'
    monkeypatch.setenv('CC_ORG_DAILY_VIDEO_UPLOADS', '0')
    r = manual.post('/api/videos', data={'title': 't', 'license_confirmed': 'true'}, files={'video': ('v.mp4', b'00', 'video/mp4')})
    assert r.status_code == 413 and r.json()['detail']['code'] == 'QUOTA_DAILY_UPLOADS'


def test_every_plan_delivers_what_the_pricing_page_promises(manual):
    # Platform defaults must sit above every plan, or a customer silently gets less than they pay for.
    owner(manual, 'owner@brand.io', 'Brand Co')
    org = TestDB().execute("SELECT org_id FROM users WHERE email='owner@brand.io'").fetchone()['org_id']
    promised = {'free': (2048, 1, 250, 10, 2), 'pro': (51200, 3, 1024, 50, 4), 'team': (256000, 15, 2048, 200, 10)}
    for plan, (storage_mb, members, video_mb, daily, active) in promised.items():
        TestDB().execute('UPDATE organizations SET plan=? WHERE id=?', (plan, org))
        e = manual.get('/api/usage').json()['effective']
        assert (e['storage_mb'], e['members'], e['max_video_mb'], e['daily_video_uploads'], e['active_jobs']) == (storage_mb, members, video_mb, daily, active), plan
    TestDB().execute("UPDATE organizations SET plan='free' WHERE id=?", (org,))
    body = {'title': 'big', 'filename': 'big.mp4', 'content_type': 'video/mp4', 'size_bytes': 251 * 1048576, 'license_confirmed': True}
    r = manual.post('/api/uploads/video', json=body)
    assert r.status_code == 402 and r.json()['detail']['code'] == 'PLAN_VIDEO_TOO_LARGE'
    TestDB().execute("UPDATE organizations SET plan='pro' WHERE id=?", (org,))
    assert manual.post('/api/uploads/video', json=body).status_code == 200


@needs_media
def test_a_long_quality_check_answers_running_and_can_be_collected(manual, tmp_path, monkeypatch):
    # Vercel gives up after 120 s without a first byte, so a long check must answer before that and carry on.
    c = manual; owner(c, 'owner@acme.io', 'Acme')
    meta = generate('static', 3, tmp_path / 'clips'); vid = upload(c, meta['path'], 'clip'); prod = add_product(c, tmp_path)
    pl = c.post('/api/placements', json=tracked_body(vid, prod, meta)).json()['id']
    monkeypatch.setenv('CC_QUALITY_WAIT_SECONDS', '0')
    r = c.post(f'/api/placements/{pl}/quality-check')
    assert r.status_code == 202 and r.json()['status'] == 'RUNNING'
    monkeypatch.setenv('CC_QUALITY_WAIT_SECONDS', '120')
    r = c.post(f'/api/placements/{pl}/quality-check')
    assert r.status_code == 200 and r.json()['status'] == 'PASSED', r.text
    assert len(TestDB().execute('SELECT id FROM placement_analyses WHERE placement_id=?', (pl,)).fetchall()) == 1    # the same check, not a second one
    monkeypatch.setenv('CC_ORG_QUALITY_CHECKS_PER_HOUR', '1')                                                         # the hourly limit is enforced
    r = c.post(f'/api/placements/{pl}/quality-check')
    assert r.status_code == 429 and r.json()['detail']['code'] == 'QUOTA_QUALITY_RATE'


@needs_media
def test_plans_limit_members_renders_and_video_length(manual, tmp_path, monkeypatch):
    c = manual
    pl, meta = drive_to_approved(c, tmp_path, 'static', 2)
    org = TestDB().execute("SELECT org_id FROM users WHERE email='owner@acme.io'").fetchone()['org_id']
    TestDB().execute("UPDATE organizations SET plan='free' WHERE id=?", (org,))
    assert c.get('/api/me').json()['plan_name'] == 'Free'
    r = c.post('/api/invitations', json={'email': 'second@acme.io'})               # Free: one member
    assert r.status_code == 402 and r.json()['detail']['code'] == 'PLAN_MEMBERS_LIMIT'
    with main.db() as con:                                                          # three renders already this month
        for _ in range(3): con.execute("INSERT INTO jobs(placement_id,org_id,status) VALUES(?,?,'SUCCEEDED')", (pl, org))
    r = c.post(f'/api/placements/{pl}/render')
    assert r.status_code == 402 and r.json()['detail']['code'] == 'PLAN_RENDERS_LIMIT'
    monkeypatch.setitem(limits.PLANS['free'], 'max_video_seconds', 2)
    r = c.post('/api/videos', data={'title': 'long', 'license_confirmed': 'true'}, files={'video': ('v.mp4', Path(meta['path']).read_bytes(), 'video/mp4')})
    assert r.status_code == 402 and r.json()['detail']['code'] == 'PLAN_VIDEO_TOO_LONG'
    TestDB().execute("UPDATE organizations SET plan='pro' WHERE id=?", (org,))
    assert c.post(f'/api/placements/{pl}/render').status_code == 200
    usage = c.get('/api/usage').json()
    assert usage['plan'] == 'pro' and usage['plan_limits']['renders_per_month'] == 40 and set(usage['plans']) == set(limits.PLANS)


def test_health_split_and_cross_origin_protection(manual, monkeypatch):
    assert manual.get('/api/health/live').json() == {'status': 'ok'}
    r = manual.get('/api/health/ready')
    assert r.status_code == 200 and r.json()['checks']['database'] == 'ok'
    assert 'host' not in json.dumps(r.json()) and 'bucket' not in json.dumps(r.json())
    owner(manual, 'owner@brand.io', 'Brand Co')
    evil = manual.post('/api/invitations', json={'email': 'x@brand.io'}, headers={'Origin': 'https://evil.example'})
    assert evil.status_code == 403
    monkeypatch.setenv('CC_CORS_ORIGINS', 'https://app.example.com')
    ok = manual.post('/api/invitations', json={'email': 'x@brand.io'}, headers={'Origin': 'https://app.example.com'})
    assert ok.status_code == 200, ok.text
    tok = manual.get('/api/auth/csrf').json()['csrf']
    assert tok == manual.cookies.get('cc_csrf')
    h = manual.get('/api/me').headers
    assert h['cache-control'] == 'no-store' and h['x-content-type-options'] == 'nosniff' and len(h['x-request-id']) >= 8
    assert h['x-frame-options'] == 'DENY'


def test_operator_metrics_need_a_token(manual, monkeypatch):
    owner(manual)
    assert manual.get('/api/ops/metrics').status_code == 404                           # operator API off without a token
    monkeypatch.setenv('CC_OPS_TOKEN', 'x' * 32)
    assert manual.get('/api/ops/metrics', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    m = manual.get('/api/ops/metrics', headers={'Authorization': 'Bearer ' + 'x' * 32}).json()
    assert m['organizations'] == 1 and 'queue' in m and 'disk' in m


def test_worker_wake_file_and_fair_claiming(manual):
    before = obs.wake_mtime(); time.sleep(0.02); obs.wake_workers()
    assert obs.wake_mtime() > before
    with main.db() as c:
        for org, n in ((1, 3), (2, 1)):
            for _ in range(n):
                c.execute("INSERT INTO jobs(placement_id,org_id,status) VALUES(?,?,'QUEUED')", (900 + org, org))
        c.execute("UPDATE jobs SET status='RUNNING',lease_until=? WHERE id=1", (time.time() + 600,))   # org 1 already busy
    jid, _ = main.claim_next_job()
    assert TestDB().execute('SELECT org_id FROM jobs WHERE id=?', (jid,)).fetchone()['org_id'] == 2
    obs.worker_heartbeat('idle')
    assert obs.worker_status()['alive'] >= 1


@needs_media
def test_job_timeout_and_shutdown_hand_back(manual, tmp_path, monkeypatch):
    pl, _ = drive_to_approved(manual, tmp_path, 'static', 3)
    jid = manual.post(f'/api/placements/{pl}/render').json()['id']
    # graceful shutdown: the running job goes straight back to the queue with its attempt refunded
    claimed, token = main.claim_next_job(); assert claimed == jid
    main._WORKER_STOP.set()
    try: main.render_job(jid, token)
    finally: main._WORKER_STOP.clear()
    job = TestDB().execute('SELECT status,attempts,lease_token FROM jobs WHERE id=?', (jid,)).fetchone()
    assert (job['status'], job['attempts'], job['lease_token']) == ('QUEUED', 0, None)
    # timeout: terminal failure with a clear code
    monkeypatch.setenv('CC_JOB_MAX_SECONDS', '0')
    claimed, token = main.claim_next_job(); main.render_job(claimed, token)
    job = TestDB().execute('SELECT status,error FROM jobs WHERE id=?', (jid,)).fetchone()
    assert job['status'] == 'FAILED' and job['error'].startswith('JOB_TIMEOUT')


@pytest.fixture()
def s3(monkeypatch):
    with mock_aws():
        for k, v in (('AWS_ACCESS_KEY_ID', 'test'), ('AWS_SECRET_ACCESS_KEY', 'test'), ('AWS_DEFAULT_REGION', 'us-east-1'),
                     ('CC_S3_BUCKET', BUCKET), ('CC_S3_REGION', 'us-east-1')):
            monkeypatch.setenv(k, v)
        for k in ('CC_S3_ENDPOINT_URL', 'CC_S3_PUBLIC_ENDPOINT_URL'): monkeypatch.delenv(k, raising=False)
        storage.reset()
        client = boto3.client('s3', region_name='us-east-1'); client.create_bucket(Bucket=BUCKET)
        yield client
        storage.reset()


def _init_upload(c, size, title='Direct'):
    return c.post('/api/uploads/video', json={'title': title, 'filename': 'clip.mp4', 'content_type': 'video/mp4', 'size_bytes': size, 'license_confirmed': True})


@needs_media
def test_direct_upload_complete_mismatch_cancel_and_expiry(manual, s3, tmp_path, monkeypatch):
    from benchmark.fixtures import generate
    owner(manual, 'c@creator.io', 'Creator Co')
    data = Path(generate('static', 3, tmp_path)['path']).read_bytes()
    init = _init_upload(manual, len(data)).json()
    assert init['method'] == 'direct' and 'X-Amz-Signature' in init['put_url']
    key = storage.key_for(TestDB().execute('SELECT path FROM upload_sessions WHERE id=?', (init['upload_id'],)).fetchone()['path'])
    early = manual.post(f'/api/uploads/{init["upload_id"]}/complete')
    assert early.status_code == 409 and early.json()['detail']['code'] == 'UPLOAD_NOT_RECEIVED'   # retryable
    s3.put_object(Bucket=BUCKET, Key=key, Body=data, ContentType='video/mp4')                      # the browser's PUT
    done = manual.post(f'/api/uploads/{init["upload_id"]}/complete')
    assert done.status_code == 200, done.text
    assert done.json()['duration'] > 0 and manual.get('/api/videos').json()[0]['id'] == done.json()['id']
    assert manual.post(f'/api/uploads/{init["upload_id"]}/complete').json()['status'] == 'COMPLETED'  # idempotent
    # declared size does not match what arrived: rejected and deleted
    bad = _init_upload(manual, len(data) + 5).json()
    bkey = storage.key_for(TestDB().execute('SELECT path FROM upload_sessions WHERE id=?', (bad['upload_id'],)).fetchone()['path'])
    s3.put_object(Bucket=BUCKET, Key=bkey, Body=data)
    assert manual.post(f'/api/uploads/{bad["upload_id"]}/complete').status_code == 400
    assert 'Contents' not in s3.list_objects_v2(Bucket=BUCKET, Prefix=bkey)
    # cancel deletes; abandoned sessions expire through maintenance
    cx = _init_upload(manual, 10).json(); ckey = storage.key_for(TestDB().execute('SELECT path FROM upload_sessions WHERE id=?', (cx['upload_id'],)).fetchone()['path'])
    s3.put_object(Bucket=BUCKET, Key=ckey, Body=b'0123456789')
    assert manual.post(f'/api/uploads/{cx["upload_id"]}/cancel').json()['status'] == 'CANCELLED'
    assert 'Contents' not in s3.list_objects_v2(Bucket=BUCKET, Prefix=ckey)
    ab = _init_upload(manual, 10).json(); akey = storage.key_for(TestDB().execute('SELECT path FROM upload_sessions WHERE id=?', (ab['upload_id'],)).fetchone()['path'])
    s3.put_object(Bucket=BUCKET, Key=akey, Body=b'0123456789')
    with main.db() as c: assert maintenance.expire_uploads(c, now=int(time.time()) + 10 ** 6) == 1
    assert 'Contents' not in s3.list_objects_v2(Bucket=BUCKET, Prefix=akey)
    # oversize declarations and other workspaces are refused
    assert _init_upload(manual, 10 ** 12).status_code == 413
    owner(manual, 'o@creator.io', 'Other Co')
    assert manual.post(f'/api/uploads/{init["upload_id"]}/complete').status_code == 404


def test_server_upload_fallback_without_object_storage(manual):
    owner(manual, 'c@creator.io', 'Creator Co')
    assert _init_upload(manual, 1000).json()['method'] == 'server'


def test_cache_cleanup_evicts_only_synced_idle_files(s3, tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    up = tmp_path / 'uploads'; up.mkdir()
    synced, unsynced, recent, temp = up / 'video_a.mp4', up / 'video_b.mp4', up / 'video_c.mp4', up / 'x.part.mp4'
    for f in (synced, unsynced, recent, temp): f.write_bytes(b'x' * 1000)
    for f in (synced, recent): storage.persist(f)
    old = time.time() - 10 * 86400
    for f in (synced, unsynced, temp): os.utime(f, (old, old))
    stats = storage.cleanup_cache(max_age_hours=24, max_total_mb=1000, min_idle_seconds=3600)
    assert not synced.exists() and unsynced.exists() and recent.exists() and not temp.exists()
    assert stats['evicted'] == 1 and stats['kept_unsynced'] == 1 and stats['temp_removed'] == 1
    assert Path(storage.local(str(synced))).read_bytes() == b'x' * 1000          # restored on demand


def test_google_id_token_signature_verification():
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives import hashes
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nums = key.public_key().public_numbers()
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip('=')
    jwk = {'kid': 'k1', 'kty': 'RSA', 'n': b64(nums.n.to_bytes(256, 'big')), 'e': b64(nums.e.to_bytes(3, 'big'))}
    keys = lambda force=False: {'k1': jwk}
    def token(header, claims, signer=key):
        signing = f'{b64(json.dumps(header).encode())}.{b64(json.dumps(claims).encode())}'
        return signing + '.' + b64(signer.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256()))
    good = token({'alg': 'RS256', 'kid': 'k1'}, {'sub': '42', 'email': 'a@b.io'})
    assert verify_rs256(good, keys)['sub'] == '42'
    h, p, s = good.split('.')
    tampered = f'{h}.{b64(json.dumps({"sub": "43"}).encode())}.{s}'
    for bad in (tampered, token({'alg': 'RS256', 'kid': 'k1'}, {'sub': '1'}, rsa.generate_private_key(public_exponent=65537, key_size=2048)),
                f'{b64(json.dumps({"alg": "none", "kid": "k1"}).encode())}.{p}.', token({'alg': 'RS256', 'kid': 'nope'}, {'sub': '1'})):
        with pytest.raises(Exception): verify_rs256(bad, keys)


def test_log_redaction():
    line = obs.redact('GET /api/media/abc.def?x=1 /auth/email/tok123 code=123456 for alice@example.com X-Amz-Signature=deadbeef')
    for secret in ('abc.def', 'tok123', '123456', 'alice@example.com', 'deadbeef'): assert secret not in line


def test_production_refuses_development_stand_ins():
    from app.config import production_problems
    dev = production_problems({'CC_ENV': 'production', 'CC_MAIL_BACKEND': 'outbox', 'CC_PUBLIC_URL': 'http://x', 'CC_CORS_ORIGINS': 'http://app.example.com'})
    joined = ' '.join(dev)
    for needle in ('DATABASE_URL', 'CC_S3_BUCKET', 'outbox', 'CC_SIGNING_KEY', 'CC_SECURE_COOKIES', 'CC_PUBLIC_URL', 'CC_CORS_ORIGINS', 'CC_INLINE_WORKER'):
        assert needle in joined, needle
    good = {'DATABASE_URL': 'postgresql://u:p@ep-x.neon.tech/db', 'CC_S3_BUCKET': 'b', 'CC_SMTP_HOST': 'smtp.resend.com', 'CC_SIGNING_KEY': 'k' * 40,
            'CC_SECURE_COOKIES': '1', 'CC_PUBLIC_URL': 'https://api.example.com', 'CC_APP_URL': 'https://app.example.com',
            'CC_CORS_ORIGINS': 'https://app.example.com', 'CC_INLINE_WORKER': '0'}
    assert production_problems(good) == []
    no_sign_in = {k: v for k, v in good.items() if k != 'CC_SMTP_HOST'}
    assert any('Nobody could sign in' in x for x in production_problems(no_sign_in))
    assert production_problems({**no_sign_in, 'GOOGLE_CLIENT_ID': 'g', 'GOOGLE_CLIENT_SECRET': 's'}) == []


def test_secret_scanner_detects_credentials_and_allows_placeholders(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('secret_scan', Path(__file__).resolve().parent.parent / 'qa' / 'secret_scan.py')
    scan = importlib.util.module_from_spec(spec); spec.loader.exec_module(scan)
    fake = ('AWS_ACCESS_KEY_ID=AKIA' + 'ABCDEFGHIJKLMNOP\n' + 'GOOGLE_CLIENT_SECRET=GOCSPX-' + 'a1B2c3D4e5F6g7H8i9J0k1L2\n'
            + 'DATABASE_URL=postgresql://neondb_owner:' + 'npg_X9y8Z7w6V5@ep-cool-1.eu-central-1.aws.neon.tech/neondb\n')
    kinds = {k for _, k, _ in scan.scan_text('x.env', fake)}
    assert {'aws_access_key', 'google_client_secret', 'postgres_url_with_password'} <= kinds
    ok = 'DATABASE_URL=postgresql://USER:PASSWORD@ep-xxxx-pooler.REGION.aws.neon.tech/neondb\nCC_SIGNING_KEY=change-me-to-a-long-random-secret\nDATABASE_URL: postgresql://cc:cc-local-only@postgres:5432/x'
    assert scan.scan_text('ok.env', ok) == []


def test_operator_plan_cli(manual, capsys):
    from ops import plans
    owner(manual, 'owner@acme.io', 'Acme', plan='free')
    plans.main(['set', '--org', 'acme', '--plan', 'pro'])
    assert 'now on Pro' in capsys.readouterr().out
    assert manual.get('/api/me').json()['plan'] == 'pro'
    with pytest.raises(SystemExit): plans.main(['set', '--org', 'Acme', '--plan', 'platinum'])
    with pytest.raises(SystemExit): plans.main(['set', '--org', 'Nobody', '--plan', 'pro'])
    plans.main(['list'])
    assert json.loads(capsys.readouterr().out.splitlines()[0])['plan'] == 'pro'
