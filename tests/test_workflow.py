"""The placement critical path: upload, analysis, quality gate, version-bound approval, render, validation, delivery,
revision and takedown, plus lifecycle, renderer routing, isolation, CSRF and job-queue integrity."""
import io, json, subprocess, time
from pathlib import Path

import pytest
from PIL import Image

from app import main, lifecycle, renderers
from benchmark.fixtures import generate
from helpers import (TestDB, make_user, login, owner, upload, add_product, tracked_body, drive_to_approved, wait_job,
                     needs_media)


def _png(color=(255, 0, 0, 180), size=(100, 100)):
    b = io.BytesIO(); Image.new('RGBA', size, color).save(b, format='PNG'); return b.getvalue()


def test_session_logout_and_health(client):
    owner(client)
    assert client.get('/api/me').json()['email'] == 'owner@acme.io'
    client.post('/api/auth/logout')
    assert client.get('/api/me').status_code == 401
    assert client.get('/api/health').json()['status'] == 'ok'


def test_org_isolation(client):
    owner(client, 'a@example.com', 'Org A')
    r = client.post('/api/products', data={'name': 'Product A', 'category': 'food'}, files={'image': ('product.png', _png(), 'image/png')})
    assert r.status_code == 200, r.text
    pid = r.json()['id']
    assert client.post('/api/products', data={'name': 'X', 'category': 'weapons'}, files={'image': ('p.png', _png(), 'image/png')}).status_code == 422
    owner(client, 'b@example.com', 'Org B')
    assert client.get('/api/products').json() == []
    assert client.get(f'/api/products/{pid}/image').status_code == 404


def test_csrf_and_origin_protection(client):
    owner(client)
    assert client.post('/api/invitations', json={'email': 'member@example.com'}, headers={'X-CSRF-Token': ''}).status_code == 403
    assert client.post('/api/invitations', json={'email': 'evil@example.com'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/invitations', json={'email': 'member@example.com'}).status_code == 200


@needs_media
def test_overlay_workflow_render_download_and_takedown(client, tmp_path):
    owner(client)
    video = tmp_path / 'sample.mp4'
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=320x240:d=3:r=15', '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
                    '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(video)], capture_output=True, timeout=60, check=True)
    r = client.post('/api/videos', data={'title': 'Test clip', 'license_confirmed': 'false'}, files={'video': ('sample.mp4', video.read_bytes(), 'video/mp4')})
    assert r.status_code == 400                                          # the uploader must confirm the rights
    vid = upload(client, video, 'Test clip')
    assert client.get('/api/videos').json()[0]['thumbnail_url']
    pid = client.post('/api/products', data={'name': 'Bottle', 'category': 'beverage'}, files={'image': ('bottle.png', _png(), 'image/png')}).json()['id']
    pl = client.post('/api/placements', json={'video_id': vid, 'product_id': pid, 'frame_second': 1, 'x': .6, 'y': .1, 'width': .2, 'start_second': .5, 'end_second': 2.5}).json()['id']
    assert client.post(f'/api/placements/{pl}/approve').json()['detail']['code'] == 'QUALITY_NOT_PASSED'
    qc = client.post(f'/api/placements/{pl}/quality-check').json()
    assert qc['status'] == 'PASSED' and qc['scene_class'] == 'OVERLAY_SCREEN_SPACE'
    assert client.post(f'/api/placements/{pl}/render').json()['detail']['code'] == 'NOT_APPROVED'
    low = client.post(f'/api/placements/{pl}/approve', json={'fit_confidence': .5})
    assert low.status_code == 422 and low.json()['detail']['code'] == 'FIT_CONFIDENCE_TOO_LOW'
    assert client.post(f'/api/placements/{pl}/approve').json()['status'] == 'APPROVED'
    assert client.post(f'/api/placements/{pl}/approve').json()['detail']['code'] == 'NOT_DRAFT'
    assert client.post(f'/api/placements/{pl}/render').status_code == 200
    job = wait_job(client, 60)
    assert job['status'] == 'SUCCEEDED', job
    dl = client.get(job['download_url'])
    assert dl.status_code == 200 and dl.content[4:8] == b'ftyp'
    assert client.get(f'/api/placements/{pl}').json()['placement']['status'] == 'DELIVERED'
    r = client.post(f'/api/placements/{pl}/revoke-rights', data={'reason': 'Licence withdrawn'})
    assert r.status_code == 200
    assert client.get(job['download_url']).status_code == 403


def test_tracking_quad_validation_and_real_perspective_render(tmp_path):
    if not main.tool_exists('ffmpeg') or not main.tool_exists('ffprobe') or not main._has_cv2():
        pytest.skip('FFmpeg, FFprobe and OpenCV are required')
    from app.tracking import validate_quad, render_tracked_video
    from app.compositor import product_quad_in_surface
    from app.validation import validate_render
    import numpy as np
    with pytest.raises(ValueError): validate_quad([[0, 0], [1, 0], [1, 1]])
    assert validate_quad([[.18, .18], [.82, .2], [.8, .82], [.2, .8]]).shape == (4, 2)
    meta = generate('pan_zoom', 0, tmp_path); product = tmp_path / 'product.png'; output = tmp_path / 'tracked.mp4'
    Image.new('RGBA', (80, 100), (230, 30, 50, 220)).save(product)
    result = render_tracked_video(meta['path'], product, output, meta['quad_norm'], .4, .4, 2.8)
    assert result['mode'] == 'opencv_optical_flow_homography' and output.stat().st_size > 1024
    s, e = round(.4 * meta['fps']), int(2.8 * meta['fps'])
    gt = {i: product_quad_in_surface(meta['gt_surface_quads'][i], 80, 100) for i in range(s, e + 1)}
    assert set(result['frame_quads']) == set(gt)
    assert max(np.abs(np.asarray(result['frame_quads'][i]) - gt[i]).max() for i in gt) < 3
    assert validate_render(meta['path'], output, product, result['frame_quads'])['status'] == 'PASSED'


def test_lifecycle_rejects_invalid_transitions():
    assert set(lifecycle.TRANSITIONS) == set(lifecycle.STATES)
    assert all(t <= set(lifecycle.STATES) for t in lifecycle.TRANSITIONS.values())
    for cur, tgt in [('DRAFT', 'RENDERING'), ('DRAFT', 'RENDERED'), ('REVOKED', 'RENDERING'), ('RENDERED', 'APPROVED'),
                     ('CANCELLED', 'DRAFT'), ('REJECTED', 'APPROVED'), ('DELIVERED', 'RENDERING')]:
        with pytest.raises(lifecycle.TransitionError):
            lifecycle.check(cur, tgt)
    lifecycle.check('DRAFT', 'APPROVED'); lifecycle.check('RENDER_FAILED', 'RENDERING')


def test_renderer_routing_refuses_unsupported():
    assert renderers.route('B_MOVING_PLANAR', 10, 1920, 1080)['selected'] == 'planar_tracked'
    assert renderers.route('OVERLAY_SCREEN_SPACE', 10, 1920, 1080)['selected'] == 'overlay_2d'
    for cls in ('C_PARTIAL_OCCLUSION', 'D_NON_PLANAR_OR_UNSTABLE'):
        r = renderers.route(cls, 10, 1920, 1080)
        assert r['selected'] is None and r['reason_code'] == 'NO_CAPABLE_RENDERER'
    assert renderers.route('B_MOVING_PLANAR', 700, 1920, 1080)['selected'] is None   # duration limit
    assert not any(p['id'] == 'replicate_generative' for p in renderers.capabilities())


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
def test_tracked_workflow_rejection_version_binding_delivery_and_roles(client, tmp_path):
    owner(client)
    good, bad = generate('pan_zoom', 5, tmp_path), generate('cut_inside', 5, tmp_path)
    vid, vbad = upload(client, good['path'], 'Desk pan'), upload(client, bad['path'], 'Cut clip')
    prod = add_product(client, tmp_path)

    # 1. An unsuitable candidate is refused with structured evidence and cannot be approved.
    rb = client.post('/api/placements', json=tracked_body(vbad, prod, bad)).json()['id']
    q = client.post(f'/api/placements/{rb}/quality-check').json()
    assert q['status'] == 'FAILED'
    cut = next(f for f in q['failures'] if f['code'] == 'SCENE_CUT_IN_INTERVAL')
    assert cut['timestamps'] and cut['recommended_action'] and cut['threshold'] is not None and 'alternative_scenes' in q
    r = client.post(f'/api/placements/{rb}/approve')
    assert r.status_code == 409 and r.json()['detail']['code'] == 'QUALITY_NOT_PASSED'
    assert client.post(f'/api/placements/{rb}/reject', data={'reason': 'Scene cut'}).json()['status'] == 'REJECTED'

    # 2. A suitable candidate passes every interval frame; an editor can prepare it but not approve or render it.
    pl = client.post('/api/placements', json=tracked_body(vid, prod, good)).json()['id']
    q = client.post(f'/api/placements/{pl}/quality-check').json()
    assert q['status'] == 'PASSED' and q['scene_class'] == 'B_MOVING_PLANAR' and q['frames_validated'] == q['frames_in_interval']
    org = TestDB().execute("SELECT org_id FROM users WHERE email='owner@acme.io'").fetchone()['org_id']
    make_user('ed@acme.io', 'Ed', member_role='editor', org_id=org); login(client, 'ed@acme.io')
    assert client.post(f'/api/placements/{pl}/approve').json()['detail']['code'] == 'ROLE_FORBIDDEN'
    login(client, 'owner@acme.io')
    assert client.post(f'/api/placements/{pl}/approve', json={'fit_confidence': .9, 'notes': 'Looks native'}).json()['status'] == 'APPROVED'

    # 3. Real render, validated output, authorized delivery.
    assert client.post(f'/api/placements/{pl}/render').json()['renderer'] == 'planar_tracked'
    job = wait_job(client)
    assert job['status'] == 'SUCCEEDED', job
    assert job['validation']['status'] == 'PASSED'
    assert job['validation']['interval_frames_checked'] == job['validation']['interval_frames_expected'] == 61
    dl = client.get(job['download_url'])
    assert dl.status_code == 200 and dl.content[4:8] == b'ftyp'
    assert client.get(f'/api/placements/{pl}').json()['placement']['status'] == 'DELIVERED'

    # 4. Another organization cannot see or download it.
    owner(client, 'other@example.com', 'Other Org')
    assert client.get(job['download_url']).status_code == 404
    assert client.get(f'/api/placements/{pl}').status_code == 404

    # 5. A correction creates a new immutable version and invalidates the approval of the old one.
    login(client, 'owner@acme.io')
    corrected = [[x + .005, y] for x, y in good['quad_norm']]
    r = client.patch(f'/api/placements/{pl}/revise', data={'frame_second': .4, 'x': .5, 'y': .5, 'width': .2, 'start_second': .4,
                                                           'end_second': 2.6, 'tracking_quad': json.dumps(corrected)})
    assert r.status_code == 200 and r.json()['version'] == 2
    assert client.get(job['download_url']).json()['detail']['code'] == 'SUPERSEDED_VERSION'
    detail = client.get(f'/api/placements/{pl}').json()
    assert detail['placement']['status'] == 'DRAFT' and detail['quality'] is None and detail['product']['name'] == 'Bottle'
    assert len({v['content_hash'] for v in detail['versions']}) == 2
    assert detail['rights']['creator_consent'] == 0
    assert client.post(f'/api/placements/{pl}/render').json()['detail']['code'] == 'NOT_APPROVED'
    assert client.post(f'/api/placements/{pl}/approve').json()['detail']['code'] == 'QUALITY_NOT_PASSED'
    events = [h['details'] for h in detail['history'] if h['event_type'] == 'placement.transition']
    assert ['APPROVED', 'RENDERING', 'RENDERED', 'DELIVERED', 'DRAFT'] == [e['to'] for e in events]


@needs_media
def test_tampered_track_and_takedown_block_render_and_delivery(manual, tmp_path):
    pl, _ = drive_to_approved(manual, tmp_path, 'static', 3)
    con = TestDB()
    track_path = con.execute('SELECT track_path FROM placement_analyses').fetchone()[0]
    original = Path(track_path).read_bytes()
    Path(track_path).write_bytes(original.replace(b'"idx": 20', b'"idx": 21', 1))
    assert manual.post(f'/api/placements/{pl}/render').json()['detail']['code'] == 'TRACK_INTEGRITY'
    Path(track_path).write_bytes(original)
    assert manual.post(f'/api/placements/{pl}/render').status_code == 200
    assert manual.post(f'/api/placements/{pl}/revoke-rights', data={'reason': 'Licence withdrawn'}).status_code == 200
    jid, token = main.claim_next_job()
    assert jid is None   # the takedown cancelled the queued job
    assert manual.get(f'/api/placements/{pl}').json()['placement']['status'] == 'REVOKED'
    assert manual.post(f'/api/placements/{pl}/render').json()['detail']['code'] == 'NOT_APPROVED'


@needs_media
def test_cancel_mid_render_lease_recovery_and_dead_letter(manual, tmp_path):
    pl, _ = drive_to_approved(manual, tmp_path, 'static', 4)
    con = TestDB()
    # Cancellation is observed at the next heartbeat; success can never overwrite it.
    jid = manual.post(f'/api/placements/{pl}/render').json()['id']
    claimed, token = main.claim_next_job()
    assert claimed == jid
    con.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?', (jid,))
    main.render_job(jid, token)
    assert con.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()['status'] == 'CANCELLED'
    assert not list(main.OUTPUTS.glob('*.mp4'))
    assert manual.get(f'/api/placements/{pl}').json()['placement']['status'] == 'RENDER_FAILED'
    # A crashed worker's lease expires; the job is recovered and completes exactly once.
    jid = manual.post(f'/api/placements/{pl}/render').json()['id']
    claimed, stale_token = main.claim_next_job()
    con.execute('UPDATE jobs SET lease_until=? WHERE id=?', (time.time() - 1, jid))
    claimed2, token2 = main.claim_next_job()
    assert claimed2 == jid and token2 != stale_token
    main.render_job(jid, stale_token)   # the stale worker wakes up: it must not touch the job
    assert con.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()['status'] == 'RUNNING'
    main.render_job(jid, token2)
    row = con.execute('SELECT status,attempts FROM jobs WHERE id=?', (jid,)).fetchone()
    assert (row['status'], row['attempts']) == ('SUCCEEDED', 2)
    kinds = [r['event_type'] for r in con.execute('SELECT event_type FROM queue_events WHERE job_id=? ORDER BY id', (jid,))]
    assert kinds == ['claimed', 'lease_recovered', 'claimed', 'succeeded']
    # Exhausted retries go to the dead-letter state.
    con.execute("INSERT INTO jobs(placement_id,org_id,status,attempts,max_attempts,lease_until,placement_version) VALUES(?,?,'RUNNING',3,3,?,1)", (pl, 1, time.time() - 1))
    main.claim_next_job()
    assert con.execute("SELECT status FROM jobs ORDER BY id DESC LIMIT 1").fetchone()['status'] == 'DEAD_LETTER'
