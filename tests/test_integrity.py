"""Integrity tests for the v6 critical path: quality gate, lifecycle, rights holder, version binding, jobs."""
import json, time
from pathlib import Path
import pytest
from PIL import Image

from app import main, lifecycle, renderers
from benchmark.fixtures import generate, product_image
from test_api import CSRSTestClient, TestDB, register, client, isolated_db  # noqa: F401  (shared fixtures)

needs_media = pytest.mark.skipif(not (main.tool_exists('ffmpeg') and main.tool_exists('ffprobe') and main._has_cv2()),
                                 reason='FFmpeg, FFprobe and OpenCV are required')
PW = 'strong-password-123'


@pytest.fixture()
def manual(tmp_path, monkeypatch, isolated_db):
    """App client without the inline worker, so tests drive claim/render deterministically."""
    monkeypatch.setenv('CC_INLINE_WORKER', '0')
    for name, value in (('DATA', tmp_path), ('UPLOADS', tmp_path / 'uploads'),
                        ('OUTPUTS', tmp_path / 'outputs'), ('FRAMES', tmp_path / 'frames')):
        monkeypatch.setattr(main, name, value)
    for p in (main.UPLOADS, main.OUTPUTS, main.FRAMES): p.mkdir(parents=True, exist_ok=True)
    main.init_db()
    with CSRSTestClient(main.app) as c:
        yield c


def login(c, email):
    c.post('/api/auth/logout')
    assert c.post('/api/auth/login', json={'email': email, 'password': PW}).status_code == 200


def setup_parties(c):
    """Brand org with two invited creators. Ends logged in as creator@example.com."""
    register(c, 'Brand', 'brand@example.com', 'brand')
    tokens = {e: c.post('/api/invitations', json={'email': e, 'role': 'creator'}).json()['invitation_token']
              for e in ('creator@example.com', 'creator2@example.com')}
    for email in ('creator2@example.com', 'creator@example.com'):
        c.post('/api/auth/logout')
        r = c.post('/api/auth/register', json={'name': 'Creator', 'email': email, 'password': PW, 'role': 'creator',
                                               'organization': 'unused', 'invitation_token': tokens[email]})
        assert r.status_code == 200, r.text


def upload(c, path, title):
    r = c.post('/api/videos', data={'title': title, 'license_confirmed': 'true'},
               files={'video': (Path(path).name, Path(path).read_bytes(), 'video/mp4')})
    assert r.status_code == 200, r.text
    assert c.post(f"/api/videos/{r.json()['id']}/analyze").status_code == 200
    return r.json()['id']


def add_product(c, tmp_path):
    prod = product_image(tmp_path / 'product.png')
    r = c.post('/api/products', data={'name': 'Bottle'}, files={'image': ('product.png', Path(prod).read_bytes(), 'image/png')})
    assert r.status_code == 200, r.text
    return r.json()['id']


def tracked_body(vid, pid, meta):
    return {'video_id': vid, 'product_id': pid, 'frame_second': .4, 'x': .5, 'y': .5, 'width': .2,
            'start_second': .4, 'end_second': 2.8, 'tracking_quad': meta['quad_norm']}


def drive_to_ready(c, tmp_path, kind='pan_zoom', seed=5):
    """Full happy path up to READY_FOR_RENDER. Ends logged in as the brand."""
    setup_parties(c)
    meta = generate(kind, seed, tmp_path)
    vid = upload(c, meta['path'], kind)
    login(c, 'brand@example.com')
    prod = add_product(c, tmp_path)
    pl = c.post('/api/placements', json=tracked_body(vid, prod, meta)).json()['id']
    q = c.post(f'/api/placements/{pl}/quality-check')
    assert q.status_code == 200 and q.json()['status'] == 'PASSED', q.text
    assert c.post(f'/api/placements/{pl}/fit-review', data={'decision': 'accept', 'confidence': '0.9'}).status_code == 200
    assert c.post(f'/api/placements/{pl}/propose').status_code == 200
    login(c, 'creator@example.com')
    assert c.put(f'/api/placements/{pl}/rights', json={'permitted_channels': ['social', 'web'], 'territories': ['WORLDWIDE'], 'creator_consent': True}).status_code == 200
    assert c.post(f'/api/placements/{pl}/approve').json()['status'] == 'CREATOR_APPROVED'
    login(c, 'brand@example.com')
    r = c.post(f'/api/placements/{pl}/approve').json()
    assert r['status'] == 'READY_FOR_RENDER', r
    assert all(x['passed'] for x in r['verification'])
    return pl, meta


def wait_job(c, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = c.get('/api/jobs').json()
        if jobs and jobs[0]['status'] not in ('QUEUED', 'RUNNING'):
            return jobs[0]
        time.sleep(.2)
    raise AssertionError('render job did not finish')


def test_lifecycle_rejects_invalid_transitions():
    assert set(lifecycle.TRANSITIONS) == set(lifecycle.STATES)
    assert all(t <= set(lifecycle.STATES) for t in lifecycle.TRANSITIONS.values())
    for cur, tgt in [('DRAFT', 'READY_FOR_RENDER'), ('PROPOSED', 'BRAND_APPROVED'), ('REVOKED', 'RENDERING'),
                     ('RENDERED', 'READY_FOR_RENDER'), ('CANCELLED', 'DRAFT'), ('BRAND_APPROVED', 'RENDERING')]:
        with pytest.raises(lifecycle.TransitionError):
            lifecycle.check(cur, tgt)
    lifecycle.check('CREATOR_APPROVED', 'BRAND_APPROVED')


def test_renderer_routing_refuses_unsupported_and_unverified(monkeypatch):
    assert renderers.route('B_MOVING_PLANAR', 10, 1920, 1080)['selected'] == 'planar_tracked'
    assert renderers.route('OVERLAY_SCREEN_SPACE', 10, 1920, 1080)['selected'] == 'overlay_2d'
    occl = renderers.route('C_PARTIAL_OCCLUSION', 10, 1920, 1080)
    assert occl['selected'] is None and occl['reason_code'] == 'NO_CAPABLE_RENDERER'
    for k, v in (('CC_REPLICATE_API_TOKEN', 't'), ('CC_REPLICATE_MODEL', 'm'), ('CC_REPLICATE_INPUT_TEMPLATE', '{}')):
        monkeypatch.setenv(k, v)
    gen = renderers.route('D_NON_PLANAR_OR_UNSTABLE', 10, 1920, 1080)
    assert gen['selected'] is None
    assert any(x['provider'] == 'replicate_generative' and 'not verified' in x['reason'] for x in gen['considered'])
    assert renderers.route('B_MOVING_PLANAR', 700, 1920, 1080)['selected'] is None   # duration limit


@needs_media
@pytest.mark.parametrize('kind,expected', [('static', None), ('handheld', None), ('cut_inside', 'SCENE_CUT_IN_INTERVAL'),
                                           ('occluder', 'PARTIAL_OCCLUSION_UNSUPPORTED'), ('low_texture', 'LOW_TEXTURE'),
                                           ('exits_frame', 'PRODUCT_OUT_OF_FRAME'), ('lighting_change', 'LIGHTING_CHANGE_UNSUPPORTED')])
def test_quality_gate_on_labelled_fixtures(tmp_path, kind, expected):
    from app.tracking import track_surface, homography_quads
    from app.compositor import product_quad_in_surface
    from app.quality import evaluate_track
    meta = generate(kind, 7, tmp_path)
    track = track_surface(meta['path'], meta['quad_norm'], .4, .4, 2.8)
    pq = product_quad_in_surface(track['quad_ref'], 120, 90)
    quads = {i: q for i, q in homography_quads(track, pq).items() if track['start_idx'] <= i <= track['end_idx']}
    report = evaluate_track(track, quads)
    assert report['thresholds_calibrated'] is False
    if expected is None:
        assert report['status'] == 'PASSED', report['failures']
        assert report['frames_validated'] == report['frames_in_interval'] == 61
    else:
        assert report['status'] == 'FAILED'
        assert expected in [f['code'] for f in report['failures']]
        assert all(f['recommended_action'] for f in report['failures'])
        assert any(f['timestamps'] for f in report['failures'])


@needs_media
def test_tracked_workflow_rejection_rights_holder_version_binding_and_delivery(client, tmp_path):
    setup_parties(client)
    good, bad = generate('pan_zoom', 5, tmp_path), generate('cut_inside', 5, tmp_path)
    vid, vbad = upload(client, good['path'], 'Desk pan'), upload(client, bad['path'], 'Cut clip')
    login(client, 'brand@example.com')
    prod = add_product(client, tmp_path)

    # 1. An unsuitable candidate is refused with structured evidence and cannot be proposed.
    rb = client.post('/api/placements', json=tracked_body(vbad, prod, bad)).json()['id']
    q = client.post(f'/api/placements/{rb}/quality-check').json()
    assert q['status'] == 'FAILED'
    cut = next(f for f in q['failures'] if f['code'] == 'SCENE_CUT_IN_INTERVAL')
    assert cut['timestamps'] and cut['recommended_action'] and cut['threshold'] is not None
    assert 'alternative_scenes' in q
    assert client.post(f'/api/placements/{rb}/fit-review', data={'decision': 'accept', 'confidence': '0.95'}).status_code == 200
    r = client.post(f'/api/placements/{rb}/propose')
    assert r.status_code == 409 and r.json()['detail']['code'] == 'QUALITY_NOT_PASSED'

    # 2. A suitable candidate passes every interval frame.
    pl = client.post('/api/placements', json=tracked_body(vid, prod, good)).json()['id']
    assert client.post(f'/api/placements/{pl}/propose').json()['detail']['code'] == 'QUALITY_NOT_PASSED'
    q = client.post(f'/api/placements/{pl}/quality-check').json()
    assert q['status'] == 'PASSED' and q['scene_class'] == 'B_MOVING_PLANAR'
    assert q['frames_validated'] == q['frames_in_interval']
    client.post(f'/api/placements/{pl}/fit-review', data={'decision': 'accept', 'confidence': '0.9'})
    assert client.post(f'/api/placements/{pl}/propose').status_code == 200
    r = client.post(f'/api/placements/{pl}/approve')
    assert r.status_code == 409 and r.json()['detail']['code'] == 'AWAITING_CREATOR_APPROVAL'

    # 3. Only the video's rights holder can consent or approve.
    login(client, 'creator2@example.com')
    assert client.put(f'/api/placements/{pl}/rights', json={'creator_consent': True}).json()['detail']['code'] == 'NOT_RIGHTS_HOLDER'
    assert client.post(f'/api/placements/{pl}/approve').json()['detail']['code'] == 'NOT_RIGHTS_HOLDER'
    login(client, 'creator@example.com')
    assert client.post(f'/api/placements/{pl}/approve').json()['detail']['code'] == 'CONSENT_REQUIRED'
    client.put(f'/api/placements/{pl}/rights', json={'permitted_channels': ['social'], 'territories': ['US'], 'creator_consent': True})
    assert client.post(f'/api/placements/{pl}/approve').json()['status'] == 'CREATOR_APPROVED'
    assert client.put(f'/api/placements/{pl}/rights', json={'permitted_channels': ['social', 'paid_ads'], 'creator_consent': True}).json()['detail']['code'] == 'RIGHTS_LOCKED'
    login(client, 'brand@example.com')
    assert client.post(f'/api/placements/{pl}/approve').json()['status'] == 'READY_FOR_RENDER'

    # 4. Real render, validated output, authorized delivery.
    assert client.post(f'/api/placements/{pl}/render').json()['renderer'] == 'planar_tracked'
    job = wait_job(client)
    assert job['status'] == 'SUCCEEDED', job
    assert job['validation']['status'] == 'PASSED'
    assert job['validation']['interval_frames_checked'] == job['validation']['interval_frames_expected'] == 61
    dl = client.get(job['download_url'])
    assert dl.status_code == 200 and dl.content[4:8] == b'ftyp'
    assert client.get(job['download_url'] + '?channel=paid_ads').json()['detail']['code'] == 'CHANNEL_NOT_PERMITTED'
    assert client.get(f'/api/placements/{pl}').json()['placement']['status'] == 'DELIVERED'

    # 5. Another organization cannot see or download it.
    client.post('/api/auth/logout')
    register(client, 'Other', 'other@example.com', 'brand', 'Other Org')
    assert client.get(job['download_url']).status_code == 404
    assert client.get(f'/api/placements/{pl}').status_code == 404

    # 6. A correction creates a new immutable version and invalidates every approval of the old one.
    login(client, 'brand@example.com')
    corrected = [[x + .005, y] for x, y in good['quad_norm']]
    r = client.patch(f'/api/placements/{pl}/revise', data={'frame_second': .4, 'x': .5, 'y': .5, 'width': .2, 'start_second': .4,
                                                           'end_second': 2.6, 'tracking_quad': json.dumps(corrected)})
    assert r.status_code == 200 and r.json()['version'] == 2
    assert client.get(job['download_url']).json()['detail']['code'] == 'SUPERSEDED_VERSION'
    detail = client.get(f'/api/placements/{pl}').json()
    assert detail['placement']['status'] == 'DRAFT' and detail['quality'] is None
    assert len({v['content_hash'] for v in detail['versions']}) == 2
    assert detail['rights']['creator_consent'] == 0
    assert client.post(f'/api/placements/{pl}/render').json()['detail']['code'] == 'NOT_READY_FOR_RENDER'
    assert client.post(f'/api/placements/{pl}/propose').json()['detail']['code'] == 'QUALITY_NOT_PASSED'
    events = [h['details'] for h in detail['history'] if h['event_type'] == 'placement.transition']
    assert ['PROPOSED', 'CREATOR_APPROVED', 'BRAND_APPROVED', 'RIGHTS_VERIFIED', 'QUALITY_VERIFIED', 'READY_FOR_RENDER',
            'RENDERING', 'RENDERED', 'DELIVERED', 'DRAFT'] == [e['to'] for e in events]


@needs_media
def test_tampered_track_and_revocation_block_render_and_delivery(manual, tmp_path):
    pl, _ = drive_to_ready(manual, tmp_path, 'static', 3)
    con = TestDB()
    track_path = con.execute('SELECT track_path FROM placement_analyses').fetchone()[0]
    original = Path(track_path).read_bytes()
    Path(track_path).write_bytes(original.replace(b'"idx": 20', b'"idx": 21', 1))
    assert manual.post(f'/api/placements/{pl}/render').json()['detail']['code'] == 'TRACK_INTEGRITY'
    Path(track_path).write_bytes(original)
    assert manual.post(f'/api/placements/{pl}/render').status_code == 200
    login(manual, 'creator@example.com')
    r = manual.post(f'/api/placements/{pl}/revoke-rights', data={'reason': 'Creator withdrew permission'})
    assert r.status_code == 200
    jid, token = main.claim_next_job()
    assert jid is None   # revocation cancelled the queued job
    assert manual.get(f'/api/placements/{pl}').json()['placement']['status'] == 'REVOKED'
    login(manual, 'brand@example.com')
    assert manual.post(f'/api/placements/{pl}/render').json()['detail']['code'] == 'NOT_READY_FOR_RENDER'


@needs_media
def test_cancel_mid_render_lease_recovery_and_dead_letter(manual, tmp_path, monkeypatch):
    pl, _ = drive_to_ready(manual, tmp_path, 'static', 4)
    con = TestDB()

    # Cancellation observed at the next heartbeat; success can never overwrite it.
    jid = manual.post(f'/api/placements/{pl}/render').json()['id']
    claimed, token = main.claim_next_job()
    assert claimed == jid
    con.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?', (jid,)); con.commit()
    main.render_job(jid, token)
    assert con.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()['status'] == 'CANCELLED'
    assert not list(main.OUTPUTS.glob('*.mp4'))
    assert manual.get(f'/api/placements/{pl}').json()['placement']['status'] == 'RENDER_FAILED'

    # A crashed worker's lease expires; the job is recovered and completes exactly once.
    v = manual.post(f'/api/placements/{pl}/verify').json()
    assert all(x['passed'] for x in v['verification'])
    jid = manual.post(f'/api/placements/{pl}/render').json()['id']
    claimed, stale_token = main.claim_next_job()
    con.execute('UPDATE jobs SET lease_until=? WHERE id=?', (time.time() - 1, jid)); con.commit()
    claimed2, token2 = main.claim_next_job()
    assert claimed2 == jid and token2 != stale_token
    main.render_job(jid, stale_token)   # the stale worker wakes up: it must not touch the job
    assert con.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()['status'] == 'RUNNING'
    main.render_job(jid, token2)
    row = con.execute('SELECT status,attempts FROM jobs WHERE id=?', (jid,)).fetchone()
    assert (row['status'], row['attempts']) == ('SUCCEEDED', 2)
    kinds = [r['event_type'] for r in con.execute('SELECT event_type FROM queue_events WHERE job_id=? ORDER BY id', (jid,))]
    assert kinds == ['claimed', 'lease_recovered', 'claimed', 'succeeded']

    # Exhausted retries go to the dead-letter state and fail the placement visibly.
    login(manual, 'brand@example.com')
    con.execute("INSERT INTO jobs(placement_id,org_id,status,attempts,max_attempts,lease_until,placement_version) VALUES(?,?,'RUNNING',3,3,?,1)", (pl, 1, time.time() - 1)); con.commit()
    main.claim_next_job()
    assert con.execute("SELECT status FROM jobs ORDER BY id DESC LIMIT 1").fetchone()['status'] == 'DEAD_LETTER'


def test_brand_term_change_resets_creator_consent(manual, tmp_path):
    setup_parties(manual)
    Image.new('RGB', (8, 8)).save(tmp_path / 'x.png')
    con = TestDB()
    # Minimal placement rows; this test exercises only the rights endpoint.
    uid = con.execute("SELECT id FROM users WHERE email='creator@example.com'").fetchone()[0]
    con.execute("INSERT INTO videos(id,org_id,title,source_path,duration,width,height,license_confirmed,created_by) VALUES(1,1,'v','x',10,320,240,1,?)", (uid,))
    con.execute("INSERT INTO products(id,org_id,name,image_path,created_by) VALUES(1,1,'p',?,1)", (str(tmp_path / 'x.png'),))
    con.execute("INSERT INTO placements(id,org_id,video_id,product_id,frame_second,x,y,width,start_second,end_second,created_by) VALUES(1,1,1,1,0,.1,.1,.2,0,1,1)")
    con.execute("INSERT INTO placement_rights(placement_id,placement_version,creator_consent,updated_by) VALUES(1,1,0,1)"); con.commit()
    assert manual.put('/api/placements/1/rights', json={'permitted_channels': ['social'], 'creator_consent': True}).json()['creator_consent'] is True
    login(manual, 'brand@example.com')
    r = manual.put('/api/placements/1/rights', json={'permitted_channels': ['social', 'paid_ads'], 'creator_consent': True}).json()
    assert r['creator_consent'] is False


def test_secure_cookie_flag_and_utf8_page(client, monkeypatch):
    monkeypatch.setenv('CC_SECURE_COOKIES', '1')
    r = client.post('/api/auth/register', json={'name': 'B', 'email': 's@example.com', 'password': PW, 'role': 'brand', 'organization': 'Sec'})
    assert 'Secure' in r.headers['set-cookie'] and 'HttpOnly' in r.headers['set-cookie']
    assert '✳' in client.get('/app').text and '✳' in client.get('/').text


def test_invited_user_needs_no_organization_but_new_workspace_does(client):
    register(client, 'Brand', 'owner@example.com', 'brand', 'Owner Org')
    token = client.post('/api/invitations', json={'email': 'joiner@example.com', 'role': 'creator'}).json()['invitation_token']
    client.post('/api/auth/logout')
    r = client.post('/api/auth/register', json={'name': 'J', 'email': 'joiner@example.com', 'password': PW, 'role': 'creator', 'organization': '', 'invitation_token': token})
    assert r.status_code == 200, r.text
    client.post('/api/auth/logout')
    r = client.post('/api/auth/register', json={'name': 'N', 'email': 'new@example.com', 'password': PW, 'role': 'brand', 'organization': '  '})
    assert r.status_code == 422
