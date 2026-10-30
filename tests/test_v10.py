"""V10: workspace roles and permissions, invitations, suspension and removal, quotas, health split, cross-origin
protection, worker wake-up and fairness, job timeout and shutdown hand-back, direct-to-bucket uploads, cache
cleanup, supervised payments, Google ID-token signatures and log redaction."""
import base64, hashlib, json, os, time
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from app import main, storage, obs, maintenance, payments
from app.auth import verify_rs256
from test_api import CSRSTestClient, TestDB, register, client, isolated_db  # noqa: F401  (shared fixtures)
from test_integrity import manual, login, PW, setup_parties, drive_to_ready, needs_media, add_product  # noqa: F401

BUCKET = 'cc-v10-private'


def invite_and_join(c, inviter_email, email, member_role, kind='brand'):
    login(c, inviter_email)
    r = c.post('/api/invitations', json={'email': email, 'role': kind, 'member_role': member_role})
    assert r.status_code == 200, r.text
    c.post('/api/auth/logout')
    r = c.post('/api/auth/register', json={'name': member_role.title(), 'email': email, 'password': PW, 'role': kind, 'invitation_token': r.json()['invitation_token']})
    assert r.status_code == 200, r.text
    assert r.json()['user']['member_role'] == member_role
    return r.json()['user']['id']


def member_id(email): return TestDB().execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()['id']


def test_member_roles_gate_actions_server_side(manual, tmp_path, monkeypatch):
    monkeypatch.setenv('CC_AUTH_RATE_PER_MINUTE', '100')
    owner = register(manual, 'Owner', 'owner@brand.io', 'brand', 'Brand Co')
    assert owner['member_role'] == 'owner'
    for role in ('viewer', 'editor', 'manager', 'admin'):
        invite_and_join(manual, 'owner@brand.io', f'{role}@brand.io', role)
    campaign = {'name': 'Launch', 'category': 'beverage', 'budget_usd': 500}
    # viewer: read-only
    login(manual, 'viewer@brand.io')
    assert manual.get('/api/products').status_code == 200 and manual.get('/api/members').status_code == 200
    r = manual.post('/api/products', data={'name': 'x'}, files={'image': ('p.png', b'x', 'image/png')})
    assert r.status_code == 403 and r.json()['detail']['code'] == 'ROLE_FORBIDDEN'
    assert manual.post('/api/campaigns', json=campaign).status_code == 403
    # editor: content yes, commercial no, team no
    login(manual, 'editor@brand.io')
    assert add_product(manual, tmp_path)
    assert manual.post('/api/campaigns', json=campaign).status_code == 403
    assert manual.post('/api/invitations', json={'email': 'z@brand.io', 'member_role': 'viewer'}).status_code == 403
    # manager: commercial yes, team no
    login(manual, 'manager@brand.io')
    assert manual.post('/api/campaigns', json=campaign).status_code == 200
    assert manual.post('/api/invitations', json={'email': 'z@brand.io', 'member_role': 'viewer'}).status_code == 403
    # admin: team yes, but cannot mint or remove admins
    login(manual, 'admin@brand.io')
    assert manual.post('/api/invitations', json={'email': 'z@brand.io', 'member_role': 'viewer'}).status_code == 200
    assert manual.post('/api/invitations', json={'email': 'y@brand.io', 'member_role': 'admin'}).status_code == 403
    assert manual.patch(f'/api/members/{member_id("viewer@brand.io")}', json={'member_role': 'editor'}).status_code == 200
    assert manual.patch(f'/api/members/{member_id("viewer@brand.io")}', json={'member_role': 'admin'}).status_code == 403
    assert manual.post(f'/api/members/{member_id("owner@brand.io")}/suspend').status_code == 409
    assert manual.post(f'/api/members/{member_id("owner@brand.io")}/transfer-ownership').status_code == 403
    # another workspace's admin cannot touch these members
    manual.post('/api/auth/logout'); register(manual, 'Other', 'owner@other.io', 'brand', 'Other Co')
    assert manual.patch(f'/api/members/{member_id("editor@brand.io")}', json={'member_role': 'viewer'}).status_code == 404
    assert manual.post(f'/api/members/{member_id("editor@brand.io")}/suspend').status_code == 404


def test_suspension_removal_and_ownership_transfer(manual, monkeypatch):
    monkeypatch.setenv('CC_AUTH_RATE_PER_MINUTE', '100')
    register(manual, 'Owner', 'owner@brand.io', 'brand', 'Brand Co')
    invite_and_join(manual, 'owner@brand.io', 'ed@brand.io', 'editor')
    invite_and_join(manual, 'owner@brand.io', 'adm@brand.io', 'admin')
    # the editor holds a live session in a second client
    other = CSRSTestClient(main.app)
    assert other.post('/api/auth/login', json={'email': 'ed@brand.io', 'password': PW}).status_code == 200
    assert other.get('/api/me').status_code == 200
    login(manual, 'owner@brand.io')
    ed = member_id('ed@brand.io')
    assert manual.post(f'/api/members/{ed}/suspend').status_code == 200
    assert other.get('/api/me').status_code == 401                      # sessions ended immediately
    assert other.post('/api/auth/login', json={'email': 'ed@brand.io', 'password': PW}).status_code == 403
    assert manual.post(f'/api/members/{ed}/reactivate').status_code == 200
    assert other.post('/api/auth/login', json={'email': 'ed@brand.io', 'password': PW}).status_code == 200
    assert manual.delete(f'/api/members/{ed}').status_code == 200
    assert other.get('/api/me').status_code == 401
    assert other.post('/api/auth/login', json={'email': 'ed@brand.io', 'password': PW}).status_code == 401
    assert 'ed@brand.io' not in json.dumps(manual.get('/api/members').json())   # email freed and hidden
    # ownership moves only to an active admin; the old owner becomes admin
    assert manual.post(f'/api/members/{member_id("owner@brand.io")}/transfer-ownership').status_code == 409
    adm = member_id('adm@brand.io')
    assert manual.post(f'/api/members/{adm}/transfer-ownership').json()['owner'] == adm
    assert manual.get('/api/me').json()['member_role'] == 'admin'
    roles = {m['email']: m['member_role'] for m in manual.get('/api/members').json()['members']}
    assert roles == {'owner@brand.io': 'admin', 'adm@brand.io': 'owner'}
    events = [r['event_type'] for r in TestDB().execute("SELECT event_type FROM events WHERE event_type LIKE 'member.%' ORDER BY id").fetchall()]
    assert events == ['member.suspended', 'member.reactivated', 'member.removed', 'member.ownership_transferred']


def test_invitations_revoke_expire_single_use(manual, monkeypatch):
    register(manual, 'Owner', 'owner@brand.io', 'brand', 'Brand Co')
    tok = manual.post('/api/invitations', json={'email': 'a@brand.io', 'member_role': 'viewer'}).json()
    assert manual.delete(f'/api/invitations/{tok["id"]}').status_code == 200
    assert manual.delete(f'/api/invitations/{tok["id"]}').status_code == 404
    statuses = {i['email']: i['status'] for i in manual.get('/api/invitations').json()}
    assert statuses['a@brand.io'] == 'revoked'
    manual.post('/api/auth/logout')
    body = {'name': 'A', 'email': 'a@brand.io', 'password': PW, 'role': 'brand', 'invitation_token': tok['invitation_token']}
    assert manual.post('/api/auth/register', json=body).status_code == 400
    login(manual, 'owner@brand.io')
    monkeypatch.setenv('CC_INVITATION_TTL', '-1')
    expired = manual.post('/api/invitations', json={'email': 'b@brand.io', 'member_role': 'viewer'}).json()
    monkeypatch.delenv('CC_INVITATION_TTL')
    assert {i['email']: i['status'] for i in manual.get('/api/invitations').json()}['b@brand.io'] == 'expired'
    fresh = manual.post('/api/invitations', json={'email': 'c@brand.io', 'member_role': 'editor'}).json()
    manual.post('/api/auth/logout')
    assert manual.post('/api/auth/register', json={**body, 'email': 'b@brand.io', 'invitation_token': expired['invitation_token']}).status_code == 400
    assert manual.post('/api/auth/register', json={**body, 'email': 'c@brand.io', 'invitation_token': fresh['invitation_token']}).status_code == 200
    manual.post('/api/auth/logout')
    assert manual.post('/api/auth/register', json={**body, 'email': 'c@brand.io', 'invitation_token': fresh['invitation_token']}).status_code == 409


def test_quotas_and_limits(manual, tmp_path, monkeypatch):
    register(manual, 'Owner', 'owner@brand.io', 'brand', 'Brand Co')
    monkeypatch.setenv('CC_ORG_STORAGE_QUOTA_MB', '0')
    r = manual.post('/api/products', data={'name': 'x'}, files={'image': ('p.png', b'0', 'image/png')})
    assert r.status_code == 413 and r.json()['detail']['code'] == 'QUOTA_STORAGE'
    monkeypatch.delenv('CC_ORG_STORAGE_QUOTA_MB')
    u = manual.get('/api/usage').json()
    assert u['limits']['org_max_active_jobs'] == 2 and u['usage']['members'] == 1
    manual.post('/api/auth/logout'); register(manual, 'Creator', 'c@creator.io', 'creator', 'Creator Co')
    monkeypatch.setenv('CC_ORG_DAILY_VIDEO_UPLOADS', '0')
    r = manual.post('/api/videos', data={'title': 't', 'license_confirmed': 'true'}, files={'video': ('v.mp4', b'00', 'video/mp4')})
    assert r.status_code == 413 and r.json()['detail']['code'] == 'QUOTA_DAILY_UPLOADS'


def test_health_split_and_cross_origin_protection(manual, monkeypatch):
    assert manual.get('/api/health/live').json() == {'status': 'ok'}
    r = manual.get('/api/health/ready')
    assert r.status_code == 200 and r.json()['checks']['database'] == 'ok'
    assert 'host' not in json.dumps(r.json()) and 'bucket' not in json.dumps(r.json())
    register(manual, 'Owner', 'owner@brand.io', 'brand', 'Brand Co')
    evil = manual.post('/api/campaigns', json={'name': 'x', 'category': 'beverage'}, headers={'Origin': 'https://evil.example'})
    assert evil.status_code == 403
    monkeypatch.setenv('CC_CORS_ORIGINS', 'https://app.example.com')
    ok = manual.post('/api/campaigns', json={'name': 'x', 'category': 'beverage', 'budget_usd': 1}, headers={'Origin': 'https://app.example.com'})
    assert ok.status_code == 200, ok.text
    tok = manual.get('/api/auth/csrf').json()['csrf']
    assert tok == manual.cookies.get('cc_csrf')
    h = manual.get('/api/me').headers
    assert h['cache-control'] == 'no-store' and h['x-content-type-options'] == 'nosniff' and len(h['x-request-id']) >= 8
    assert "frame-ancestors 'none'" in manual.get('/app').headers['content-security-policy']
    assert manual.get('/config.js').text.startswith('window.CC_CONFIG=')


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
    pl, _ = drive_to_ready(manual, tmp_path, 'static', 3)
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
    register(manual, 'Creator', 'c@creator.io', 'creator', 'Creator Co')
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
    manual.post('/api/auth/logout'); register(manual, 'Other', 'o@creator.io', 'creator', 'Other Co')
    assert manual.post(f'/api/uploads/{init["upload_id"]}/complete').status_code == 404


def test_server_upload_fallback_without_object_storage(manual):
    register(manual, 'Creator', 'c@creator.io', 'creator', 'Creator Co')
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


def test_supervised_payment_ledger(manual, monkeypatch):
    register(manual, 'Owner', 'owner@brand.io', 'brand', 'Brand Co')
    with main.db() as c:
        oid = c.execute("INSERT INTO offers(campaign_id,brand_org_id,video_id,creator_org_id,price_usd,category,channels,territories,status,created_by) VALUES(1,1,1,2,450,'beverage','[]','[]','ACCEPTED',1)").lastrowid
        with pytest.raises(payments.PaymentError): payments.record(c, oid, 'PAYMENT', 450, '', '', 'ops')        # not invoiced yet
        assert payments.record(c, oid, 'INVOICE', 450, 'INV-1', '', 'ops') == 'INVOICED'
        with pytest.raises(payments.PaymentError): payments.record(c, oid, 'PAYMENT', 400, '', '', 'ops')        # amount differs, no note
        assert payments.record(c, oid, 'PAYMENT', 450, 'bank-ref', '', 'ops') == 'PAID'
        assert payments.record(c, oid, 'CREATOR_SETTLEMENT', 405, 'payout-1', 'platform fee 10%', 'ops') == 'SETTLED'
    led = manual.get(f'/api/offers/{oid}/payments').json()
    assert led['payment_status'] == 'SETTLED' and [x['kind'] for x in led['ledger']] == ['INVOICE', 'PAYMENT', 'CREATOR_SETTLEMENT']
    assert manual.get('/api/ops/metrics').status_code == 404                           # operator API off without a token
    monkeypatch.setenv('CC_OPS_TOKEN', 'x' * 32)
    assert manual.get('/api/ops/metrics', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    m = manual.get('/api/ops/metrics', headers={'Authorization': 'Bearer ' + 'x' * 32}).json()
    assert m['organizations'] == 1 and 'queue' in m and 'disk' in m
    r = manual.post('/api/ops/payments', headers={'Authorization': 'Bearer ' + 'x' * 32}, json={'offer_id': oid, 'kind': 'INVOICE', 'amount_usd': 1, 'recorded_by': 'ops'})
    assert r.status_code == 409                                                       # SETTLED cannot be invoiced again


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
