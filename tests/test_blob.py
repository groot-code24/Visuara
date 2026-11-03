"""Vercel Blob storage and the on-demand job runner: everything the platform does differently when it runs entirely on Vercel."""
import base64, json, os, shutil, sys, threading, time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'qa'))
from blob_stub import BlobStub  # noqa: E402

from app import blobstore, config, decisions, jobrunner, limits, main, storage  # noqa: E402
from helpers import TestDB, add_product, drive_to_approved, needs_media, owner, upload  # noqa: E402
from benchmark.fixtures import generate  # noqa: E402


@pytest.fixture(scope='module')
def blob_server():
    # One server per process: Vercel's SDK keeps its API address and credentials for the life of the process.
    stub = BlobStub()
    from vercel.blob._internal import service
    originals = blobstore.delivery_url, service.construct_delivery_url
    stub.patch_delivery()
    yield stub
    blobstore.delivery_url, service.construct_delivery_url = originals
    stub.close()


@pytest.fixture()
def blob(blob_server, monkeypatch):
    monkeypatch.setenv('BLOB_READ_WRITE_TOKEN', blob_server.rw_token); monkeypatch.setenv('VERCEL_BLOB_API_URL', blob_server.api_url)
    blob_server.objects.clear(); blob_server.calls.clear(); storage.reset()
    yield blob_server
    storage.reset()


# ---------------------------------------------------------------- compatibility with the official JavaScript SDK
def test_client_token_is_exactly_what_the_javascript_sdk_generates():
    # generateClientTokenFromReadWriteToken from @vercel/blob 2.8.2, same inputs.
    expected = ('vercel_blob_client_TESTSTORE_NTBkYTEzYjk3ZDhhYmFiMjE0ZDliNjIzZDFiYmFjODQ3MGUwOWUxMTllYmViNTg4NzJiOTM2ZDZlNzUwZjkxOS5leUp3WVhSb2JtRnRaU0k2SW5Wd2JHOWhaSE12ZG1sa1pXOWZlQzV0Y0RRaUxDSmhiR3h2ZDJWa1EyOXVkR1Z1'
                'ZEZSNWNHVnpJanBiSW5acFpHVnZMMjF3TkNKZExDSnRZWGhwYlhWdFUybDZaVWx1UW5sMFpYTWlPakV3TkRnMU56WXNJbUZrWkZKaGJtUnZiVk4xWm1acGVDSTZabUZzYzJVc0ltRnNiRzkzVDNabGNuZHlhWFJsSWpwbVlXeHpaU3dpZG1Gc2FXUlZiblJwYkNJNk1UZzVNelExTmpBd01EQXdNSDA9')
    assert blobstore.client_token('uploads/video_x.mp4', 'video/mp4', 1048576, 0, now_ms=1893456000000, rw_token='vercel_blob_rw_TESTSTORE_secretsecretsecret') == expected


def test_signed_read_is_exactly_what_the_javascript_sdk_generates():
    # presignUrl from @vercel/blob 2.8.2, same inputs.
    delegation = 'eyJzdG9yZUlkIjoiVEVTVFNUT1JFIiwicGF0aG5hbWUiOiIqIiwib3BlcmF0aW9ucyI6WyJnZXQiXSwidmFsaWRVbnRpbCI6MTc5MzQ3Mjg0MDQ0OH0.sig'
    params = blobstore.sign_read('uploads/video_a b.mp4', delegation, 'signing-key-123', 1793469540448)
    assert params == {'vercel-blob-valid-until': '1793469540448', 'vercel-blob-delegation': delegation, 'vercel-blob-signature': 'SqmASoZI7yYTA2lJe_2BVuInWMR-kH_q2jBIcKUhy9c'}


def test_store_id_comes_from_the_token(monkeypatch):
    assert blobstore.store_id('vercel_blob_rw_AbC123_secret') == 'AbC123'
    for bad in ('', 'vercel_blob_rw_nosecret', 'some-other-token'):
        with pytest.raises(blobstore.BlobConfigError): blobstore.store_id(bad)


# ---------------------------------------------------------------- storage through Vercel's real Python SDK
def test_files_round_trip_through_the_sdk(blob, tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    small = tmp_path / 'uploads' / 'a b.mp4'; small.parent.mkdir(); small.write_bytes(os.urandom(4096))
    storage.persist(small)
    assert blob.objects['uploads/a b.mp4']['type'] == 'video/mp4' and storage.head(str(small)) == 4096 and storage.exists(str(small))
    data = small.read_bytes(); small.unlink()
    assert Path(storage.local(str(small))).read_bytes() == data              # a host without the file restores it
    assert storage.probe() == 'ok' and storage.describe()['backend'] == 'vercel_blob'
    storage.delete(str(small))
    assert not storage.exists(str(small)) and 'uploads/a b.mp4' not in blob.objects


def test_every_generated_file_reaches_object_storage():
    # Hosts share no disk: a file outside these folders exists only on the instance that wrote it (decision previews once did).
    for folder in ('uploads', 'outputs', 'frames', 'analyses', 'decisions'):
        assert storage.key_for(str(main.DATA / folder / 'x.bin')) == f'{folder}/x.bin'


def test_a_large_file_goes_in_parts(blob, tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path); monkeypatch.setattr(blobstore, 'MULTIPART_ABOVE', 1048576); monkeypatch.setattr(blobstore, 'PART_BYTES', 6 * 1048576)
    big = tmp_path / 'outputs' / 'render.mp4'; big.parent.mkdir(); body = os.urandom(13 * 1048576); big.write_bytes(body)
    storage.persist(big)
    assert blob.objects['outputs/render.mp4']['data'] == body
    assert sum(c[0] == 'MPU_PART' for c in blob.calls) == 3


def test_signed_links_expire_and_private_objects_need_them(blob, tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    f = tmp_path / 'outputs' / 'r.mp4'; f.parent.mkdir(); f.write_bytes(b'x' * 1000); storage.persist(f)
    url = storage.presigned_url(str(f))
    assert httpx.get(url).content == b'x' * 1000
    part = httpx.get(url, headers={'Range': 'bytes=0-9'})
    assert part.status_code == 206 and len(part.content) == 10
    assert httpx.get(blob.object_url('outputs/r.mp4')).status_code == 403     # no signature
    assert httpx.get(url.replace('outputs/r.mp4', 'outputs/other.mp4')).status_code == 403    # a signature covers one object
    assert httpx.get(url.replace('valid-until=', 'valid-until=1')).status_code == 403         # and one expiry
    expired = blobstore.signed_url('outputs/r.mp4', -5)
    assert httpx.get(expired).status_code == 403
    assert 'attachment' in httpx.get(storage.presigned_url(str(f), filename='r.mp4')).headers['content-disposition']
    assert sum(c[0] == 'SIGNED_TOKEN' for c in blob.calls) == 1               # the delegation is reused, not fetched per link


def test_a_client_token_allows_one_upload_of_what_was_declared(blob):
    def put(path, body, ctype='video/mp4', token=None):
        return httpx.put(blob.api_url, params={'pathname': path}, content=body, headers={'authorization': f'Bearer {token}', 'x-content-type': ctype, 'x-vercel-blob-access': 'private'})
    good = blobstore.client_token('uploads/v.mp4', 'video/mp4', 100, 60)
    assert put('uploads/other.mp4', b'x' * 10, token=good).status_code == 400
    assert put('uploads/v.mp4', b'x' * 10, 'text/html', token=good).status_code == 400
    assert put('uploads/v.mp4', b'x' * 101, token=good).status_code == 400
    assert put('uploads/v.mp4', b'x' * 10, token=blobstore.client_token('uploads/v.mp4', 'video/mp4', 100, -1)).status_code == 400
    assert put('uploads/v.mp4', b'x' * 10, token=good.replace('vercel_blob_client_TESTSTORE_', 'vercel_blob_client_TESTSTORE_A')).status_code == 403
    assert put('uploads/v.mp4', b'x' * 100, token=good).status_code == 200
    assert put('uploads/v.mp4', b'y' * 100, token=good).status_code == 400    # no overwriting


@needs_media
def test_direct_upload_to_blob_end_to_end(blob, manual, tmp_path):
    c = manual; owner(c)
    meta = generate('static', 3, tmp_path); raw = Path(meta['path']).read_bytes()
    r = c.post('/api/uploads/video', json={'title': 'Clip', 'filename': 'clip one.mp4', 'content_type': 'video/mp4', 'size_bytes': len(raw), 'license_confirmed': True}).json()
    assert r['method'] == 'blob' and r['client_token'].startswith('vercel_blob_client_') and 'put_url' not in r
    too_early = c.post(f"/api/uploads/{r['upload_id']}/complete")
    assert too_early.status_code == 409 and too_early.json()['detail']['code'] == 'UPLOAD_NOT_RECEIVED'
    assert httpx.put(blob.api_url, params={'pathname': r['pathname']}, content=raw,
                     headers={'authorization': f"Bearer {r['client_token']}", 'x-content-type': 'video/mp4', 'x-vercel-blob-access': 'private'}).status_code == 200
    done = c.post(f"/api/uploads/{r['upload_id']}/complete")
    assert done.status_code == 200, done.text
    assert any(k.startswith('uploads/video_') for k in blob.objects)
    assert c.get('/api/videos').json()[0]['id'] == done.json()['id']


@needs_media
def test_render_on_a_host_with_an_empty_disk_and_redirected_download(blob, manual, tmp_path):
    pl, _ = drive_to_approved(manual, tmp_path, 'static', 3)
    for d in ('uploads', 'frames', 'analyses'): shutil.rmtree(main.DATA / d, ignore_errors=True)
    jid = manual.post(f'/api/placements/{pl}/render').json()['id']
    claimed, token = main.claim_next_job(); assert claimed == jid
    main.render_job(jid, token)
    job = TestDB().execute('SELECT status,output_path,error FROM jobs WHERE id=?', (jid,)).fetchone()
    assert job['status'] == 'SUCCEEDED', job['error']
    assert storage.key_for(job['output_path']) in blob.objects
    r = manual.get(f'/api/jobs/{jid}/download', follow_redirects=False)
    assert r.status_code == 307 and 'vercel-blob-signature=' in r.headers['location'] and r.headers['cache-control'] == 'no-store'
    assert httpx.get(r.headers['location']).content == blob.objects[storage.key_for(job['output_path'])]['data']


# ---------------------------------------------------------------- production settings
BASE = {'CC_ENV': 'production', 'DATABASE_URL': 'postgresql://x', 'CC_SIGNING_KEY': 'k' * 40, 'CC_SECURE_COOKIES': '1', 'CC_INLINE_WORKER': '0',
        'CC_PUBLIC_URL': 'https://x.vercel.app', 'CC_MAIL_BACKEND': 'smtp', 'CC_SMTP_HOST': 'smtp.gmail.com', 'CC_SMTP_USER': 'a@gmail.com', 'CC_SMTP_PASSWORD': 'x'}


def test_production_accepts_blob_and_refuses_no_storage():
    assert config.production_problems({**BASE, 'BLOB_READ_WRITE_TOKEN': 'vercel_blob_rw_a_b'}) == []
    assert any('No media storage' in p for p in config.production_problems(BASE))
    assert config.production_problems({**BASE, 'CC_S3_BUCKET': 'b'}) == []


def test_the_public_address_defaults_to_the_vercel_production_domain(monkeypatch):
    monkeypatch.setenv('CC_WORKER_MODE', 'http'); monkeypatch.setenv('VERCEL_PROJECT_PRODUCTION_URL', 'cc.vercel.app'); monkeypatch.delenv('CC_PUBLIC_URL', raising=False)
    config.apply_platform_defaults()
    assert os.environ['CC_PUBLIC_URL'] == 'https://cc.vercel.app'
    monkeypatch.setenv('CC_PUBLIC_URL', 'https://mine.example')
    config.apply_platform_defaults()
    assert os.environ['CC_PUBLIC_URL'] == 'https://mine.example'


# ---------------------------------------------------------------- the on-demand job runner
@pytest.fixture()
def runner(monkeypatch):
    """Runner mode with the outgoing request recorded instead of sent."""
    kicks = []
    monkeypatch.setenv('CC_WORKER_MODE', 'http'); monkeypatch.setenv('CC_SIGNING_KEY', 's' * 40); monkeypatch.setattr(jobrunner, 'kick', lambda: kicks.append(1))
    monkeypatch.setattr(limits, 'check_storage_configured', lambda: None)     # these tests run the runner on local disk
    jobrunner._last_nudge.clear()
    return kicks


def _auth(): return {'Authorization': f'Bearer {jobrunner.secret()}'}


def test_runner_endpoint_requires_the_key(manual, runner):
    assert manual.post('/api/internal/run-jobs').status_code == 401
    assert manual.post('/api/internal/run-jobs', headers={'Authorization': 'Bearer nope'}).status_code == 401
    assert manual.post('/api/internal/run-jobs', headers=_auth()).json() == {'ran': None}


def test_runner_endpoint_does_not_exist_with_a_worker_process(manual, monkeypatch):
    monkeypatch.setenv('CC_SIGNING_KEY', 's' * 40); monkeypatch.delenv('CC_WORKER_MODE', raising=False)
    assert manual.post('/api/internal/run-jobs', headers=_auth()).status_code == 404


def test_runner_key_is_unusable_without_a_signing_key(monkeypatch):
    monkeypatch.delenv('CC_SIGNING_KEY', raising=False)
    assert not jobrunner.authorised(f'Bearer {jobrunner.secret()}')


@needs_media
def test_queueing_a_render_starts_the_runner_and_one_request_runs_one_job(manual, runner, tmp_path):
    pl, _ = drive_to_approved(manual, tmp_path, 'static', 3)
    runner.clear()
    jid = manual.post(f'/api/placements/{pl}/render').json()['id']
    assert runner == [1]
    assert manual.post('/api/internal/run-jobs', headers=_auth()).json() == {'ran': 'render'}
    assert TestDB().execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()['status'] == 'SUCCEEDED'
    assert runner == [1]                                                     # queue empty: no further request


@needs_media
def test_runner_asks_for_another_request_while_work_remains(manual, runner, tmp_path):
    pl, _ = drive_to_approved(manual, tmp_path, 'static', 3)
    manual.post(f'/api/placements/{pl}/render')
    with main.db() as c:      # a second queued job, as another workspace's would be
        c.execute("INSERT INTO jobs(placement_id,org_id,status,mode,placement_version,renderer) VALUES(?,1,'QUEUED','COMPOSITE',1,'x')", (pl,))
    runner.clear()
    assert manual.post('/api/internal/run-jobs', headers=_auth()).json()['ran'] == 'render'
    assert runner == [1]


def test_runner_respects_the_concurrency_limit(manual, runner, monkeypatch):
    monkeypatch.setenv('CC_RUNNER_CONCURRENCY', '1')
    with main.db() as c:
        c.execute("INSERT INTO jobs(placement_id,org_id,status,mode,lease_until) VALUES(1,1,'RUNNING','COMPOSITE',?)", (time.time() + 60,))
    assert manual.post('/api/internal/run-jobs', headers=_auth()).json() == {'ran': None, 'reason': 'busy'}


def test_polling_restarts_a_lost_runner_but_not_too_often(manual, runner, tmp_path):
    u = owner(manual)
    manual.get('/api/jobs'); assert runner == []                              # nothing queued: the database stays idle
    with main.db() as c:      # a job whose runner died: its lease has expired
        c.execute("INSERT INTO jobs(placement_id,org_id,status,mode,lease_until) VALUES(1,?,'RUNNING','COMPOSITE',?)", (u['org_id'], time.time() - 5))
    manual.get('/api/jobs'); assert runner == [1]
    manual.get('/api/jobs'); assert runner == [1]                             # rate limited
    jobrunner._last_nudge.clear()
    manual.get('/api/decisions'); assert runner == [1, 1]


def test_a_kick_does_not_wait_for_the_job_and_survives_failure(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    seen = []

    class Slow(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            seen.append(self.headers.get('authorization')); time.sleep(3); self.send_response(200); self.end_headers()
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Slow); srv.handle_error = lambda *a: None; threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv('CC_SIGNING_KEY', 's' * 40); monkeypatch.setenv('CC_RUNNER_KICK_WAIT', '.2'); monkeypatch.setenv('CC_WORKER_URL', f'http://127.0.0.1:{srv.server_address[1]}/')
    t = time.time(); jobrunner.kick()
    assert time.time() - t < 2 and seen == [f'Bearer {jobrunner.secret()}']
    srv.shutdown(); srv.server_close()
    jobrunner.kick()                                                          # nothing listening: logged, not raised


# ---------------------------------------------------------------- decisions that outlast one request
@pytest.fixture()
def two_videos(manual, runner, tmp_path, monkeypatch):
    from benchmark import fixtures_scene as scene
    monkeypatch.setenv('CC_DECISION_WINDOW_SECONDS', '2.5'); monkeypatch.setenv('CC_DECISION_CANDIDATES_PER_VIDEO', '3')
    clips = tmp_path / 'clips'
    owner(manual)
    p = add_product(manual, tmp_path, 'Sleeve', 'tech', scene.product(clips / 's.png', 160, 100, (180, 80, 30)))
    vids = [upload(manual, scene.generate('room', s, clips)['path'], f'v{s}', analyze=False) for s in (1, 2)]
    return manual, manual.post('/api/decisions', json={'product_ids': [p], 'video_ids': vids}).json()['id']


class _StopAfterFirstVideo(jobrunner.OutOfTime):
    """Time runs out as soon as a whole video has been checkpointed."""
    def is_set(self):
        with main.db() as c: row = c.execute('SELECT checkpoint_json FROM decisions WHERE checkpoint_json IS NOT NULL').fetchone()
        return bool(row and json.loads(row['checkpoint_json'])['videos'])


class _StopAfterFirstSurface(jobrunner.OutOfTime):
    """Time runs out as soon as one surface has been examined."""
    def is_set(self):
        with main.db() as c: return bool(c.execute('SELECT 1 FROM decisions WHERE checkpoint_json IS NOT NULL').fetchone())


@needs_media
def test_a_decision_resumes_after_running_out_of_time(two_videos):
    c, did = two_videos
    assert decisions.run_next(stop=_StopAfterFirstVideo()) is True
    row = TestDB().execute('SELECT status,attempts,checkpoint_json FROM decisions WHERE id=?', (did,)).fetchone()
    assert row['status'] == 'QUEUED' and row['attempts'] == 0                 # progress was made, so no attempt is used up
    assert len(json.loads(row['checkpoint_json'])['videos']) == 1
    first = json.loads(row['checkpoint_json'])['videos'][0]['video_id']
    assert decisions.run_next() is True
    d = c.get(f'/api/decisions/{did}').json()
    assert d['status'] == 'SUCCEEDED' and len(d['result']['videos']) == 2 and d['result']['videos'][0]['video_id'] == first


@needs_media
def test_a_decision_continues_from_the_surface_it_stopped_at(two_videos):
    c, did = two_videos
    stop = _StopAfterFirstSurface()
    assert decisions.run_next(stop=stop) is True
    row = TestDB().execute('SELECT status,attempts,checkpoint_json FROM decisions WHERE id=?', (did,)).fetchone()
    part = json.loads(row['checkpoint_json'])['partial']
    assert row['status'] == 'QUEUED' and row['attempts'] == 0 and part['next'] == 1 and len(part['examined']) == 1
    assert decisions.run_next() is True
    d = c.get(f'/api/decisions/{did}').json()
    assert d['status'] == 'SUCCEEDED' and len(d['result']['videos']) == 2
    assert d['result']['videos'][0]['candidates_examined'][0] == part['examined'][0]     # the first surface was not redone


def test_total_work_ceiling_names_a_length_that_would_fit(monkeypatch):
    from fastapi import HTTPException
    from app import limits
    monkeypatch.setenv('CC_MAX_VIDEO_GIGAPIXELS', '2.9')
    limits.check_video_metadata(45, 1920, 1080, 30)
    limits.check_video_metadata(100, 1280, 720, 30)
    with pytest.raises(HTTPException) as e: limits.check_video_metadata(60, 1920, 1080, 30)
    assert e.value.status_code == 413 and 'Keep it to 46 seconds' in e.value.detail
    with pytest.raises(HTTPException): limits.check_video_metadata(30, 1920, 1080, 60)


def test_budgets_follow_the_function_time_limit(monkeypatch):
    for k in ('CC_JOB_MAX_SECONDS', 'CC_RUNNER_BUDGET_SECONDS', 'CC_QUALITY_WAIT_SECONDS', 'CC_PUBLIC_URL'): monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('CC_WORKER_MODE', 'http'); monkeypatch.setenv('CC_FUNCTION_SECONDS', '800')
    config.apply_platform_defaults()
    assert os.environ['CC_JOB_MAX_SECONDS'] == os.environ['CC_RUNNER_BUDGET_SECONDS'] == os.environ['CC_QUALITY_WAIT_SECONDS'] == '770'
    for k in ('CC_JOB_MAX_SECONDS', 'CC_RUNNER_BUDGET_SECONDS', 'CC_QUALITY_WAIT_SECONDS'): del os.environ[k]


@needs_media
def test_a_decision_that_never_finishes_a_video_fails_instead_of_looping(two_videos):
    c, did = two_videos
    out = jobrunner.OutOfTime(); out.set()
    for _ in range(decisions.MAX_ATTEMPTS):
        assert decisions.run_next(stop=out) is True
    assert TestDB().execute('SELECT attempts FROM decisions WHERE id=?', (did,)).fetchone()['attempts'] == decisions.MAX_ATTEMPTS
    with main.db() as con: con.execute("UPDATE decisions SET status='RUNNING',lease_until=0 WHERE id=?", (did,))
    assert decisions.run_next() is False
    d = c.get(f'/api/decisions/{did}').json()
    assert d['status'] == 'FAILED' and 'too long' in d['error']


@needs_media
def test_a_worker_stopping_uses_no_attempt(two_videos):
    _, did = two_videos
    stop = threading.Event(); stop.set()
    assert decisions.run_next(stop=stop) is True
    row = TestDB().execute('SELECT status,attempts FROM decisions WHERE id=?', (did,)).fetchone()
    assert row['status'] == 'QUEUED' and row['attempts'] == 0


# ---------------------------------------------------------------- a new serverless instance starting
def test_schema_setup_is_skipped_when_already_applied(manual):
    with main.db() as c: kind = c.kind
    if kind != 'postgres': pytest.skip('PostgreSQL only: SQLite setup is local and free')
    with main.db() as c: c.execute('CREATE TABLE IF NOT EXISTS canary(id INTEGER)'); c.execute('DROP TABLE organizations CASCADE')
    main.init_db()                                                            # same schema: nothing re-created
    with main.db() as c: assert 'organizations' not in c.tables()
    with main.db() as c: c.execute("UPDATE schema_fingerprint SET value='older'")
    main.init_db()                                                            # different schema: applied again
    with main.db() as c: assert 'organizations' in c.tables()


def test_client_token_payload_is_readable_json():
    tok = blobstore.client_token('uploads/v.mp4', 'video/mp4', 5, 60, rw_token='vercel_blob_rw_S_k')
    sig, payload = base64.b64decode(tok.removeprefix('vercel_blob_client_S_')).decode().split('.', 1)
    assert json.loads(base64.b64decode(payload))['maximumSizeInBytes'] == 5 and len(sig) == 64


@needs_media
def test_quality_check_that_outlasts_its_request_says_so(manual, runner, tmp_path, monkeypatch):
    owner(manual)
    meta = generate('static', 3, tmp_path)
    vid = upload(manual, meta['path'], 'clip'); prod = add_product(manual, tmp_path)
    pl = manual.post('/api/placements', json={'video_id': vid, 'product_id': prod, 'frame_second': .4, 'x': .5, 'y': .5, 'width': .2, 'start_second': .4, 'end_second': 2.8, 'tracking_quad': meta['quad_norm']}).json()['id']
    monkeypatch.setenv('CC_QUALITY_WAIT_SECONDS', '0.001')
    r = manual.post(f'/api/placements/{pl}/quality-check')
    assert r.status_code == 422 and r.json()['detail']['code'] == 'QUALITY_CHECK_TOO_LONG'


def test_sign_in_housekeeping_starts_the_runner_once_per_interval(manual, runner):
    with main.db() as c: c.execute("DELETE FROM rate_limits WHERE bucket_key='housekeeping'")
    jobrunner.housekeeping(); jobrunner.housekeeping()
    assert runner == [1]


def test_platform_storage_ceiling_stops_uploads_and_renders_before_the_free_blob_limit(manual, monkeypatch):
    with main.db() as c:
        limits.check_platform_storage(c, 10 ** 12)                            # no ceiling set
        monkeypatch.setenv('CC_PLATFORM_STORAGE_MB', '1')
        limits.check_platform_storage(c, 1024)
        with pytest.raises(Exception) as e: limits.check_platform_storage(c, 2 * 1048576)
    assert e.value.status_code == 413 and e.value.detail['code'] == 'QUOTA_PLATFORM_STORAGE'


def test_unfinished_email_or_storage_turns_off_that_feature_and_not_the_whole_api(monkeypatch):
    from app import mailer
    gmail_only = {'CC_ENV': 'production', 'DATABASE_URL': 'postgresql://x', 'CC_SIGNING_KEY': 'k' * 40, 'CC_SECURE_COOKIES': '1', 'CC_INLINE_WORKER': '0', 'CC_WORKER_MODE': 'http',
                  'CC_PUBLIC_URL': 'https://www.example.com', 'CC_SMTP_HOST': 'smtp.gmail.com', 'CC_SMTP_USER': '', 'CC_SMTP_PASSWORD': '', 'CC_MAIL_FROM': 'Visuara <you@gmail.com>'}
    google = {'GOOGLE_CLIENT_ID': 'g', 'GOOGLE_CLIENT_SECRET': 's'}
    assert config.production_problems({**gmail_only, **google}) == []                                   # the API starts: Google works, so people can sign in
    assert any('Nobody could sign in' in p for p in config.production_problems(gmail_only))             # still refuses when no sign-in method works
    assert any('No media storage' in p for p in config.production_problems({**gmail_only, **google, 'CC_WORKER_MODE': ''}))   # a VM must not fall back to local disk
    warnings = ' '.join(config.setup_warnings({**gmail_only, **google}))
    assert 'Magic links are off' in warnings and 'Uploads are off' in warnings and 'Google' not in warnings
    for k, v in gmail_only.items(): monkeypatch.setenv(k, v)
    monkeypatch.delenv('CC_MAIL_BACKEND', raising=False)
    assert not mailer.enabled()
    monkeypatch.setenv('CC_SMTP_USER', 'a@gmail.com'); monkeypatch.setenv('CC_SMTP_PASSWORD', 'x')
    assert mailer.enabled() and mailer.sender() == 'a@gmail.com'                                        # the example's placeholder is never used as the sender


def test_uploads_say_storage_is_not_set_up_on_vercel(monkeypatch):
    monkeypatch.setenv('CC_WORKER_MODE', 'http')
    monkeypatch.setattr(storage, 'enabled', lambda: False)
    with pytest.raises(Exception) as e: limits.check_storage_configured()
    assert e.value.status_code == 503 and e.value.detail['code'] == 'STORAGE_NOT_CONFIGURED'
    monkeypatch.setattr(storage, 'enabled', lambda: True)
    limits.check_storage_configured()
