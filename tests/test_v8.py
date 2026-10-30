"""V8 tests: 2D asset/compositing quality, 3D pipeline, cross-organization commerce, signed links, backup/restore."""
import hashlib, io, json, logging, os, struct, subprocess, sys, time
from pathlib import Path
import cv2
import numpy as np
import pytest
from PIL import Image

from app import main
from app.assets3d import load_glb, AssetError
from app.compositor import prepare_asset, Planar2DPlan, temporal_gains, composite_frame
from benchmark.glb_assets import bottle_glb, box_glb
from test_api import CSRSTestClient, register  # noqa: F401
from test_integrity import manual, login, PW, isolated_db   # noqa: F401  (shared fixtures)

needs_media = pytest.mark.skipif(not (main.tool_exists('ffmpeg') and main._has_cv2()), reason='FFmpeg/OpenCV required')
ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ 2D assets and compositing
def test_prepare_asset_orientation_icc_and_warnings(tmp_path):
    from PIL import ImageCms
    im = Image.new('RGB', (120, 60), (200, 30, 30))
    exif = Image.Exif(); exif[274] = 6                      # rotate 90 degrees on display
    im.save(tmp_path / 'rot.jpg', exif=exif.tobytes())
    rep = prepare_asset(tmp_path / 'rot.jpg', tmp_path / 'rot.png')
    assert rep['orientation_corrected'] and (rep['width'], rep['height']) == (60, 120)
    assert 'NO_TRANSPARENCY' in [w['code'] for w in rep['warnings']]
    src = Image.new('RGB', (100, 100), (90, 140, 200)); src.save(tmp_path / 'p.png', icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes())
    assert prepare_asset(tmp_path / 'p.png', tmp_path / 'p2.png')['icc_converted'] is True
    # Halo: opaque dark product with a bright semi-transparent fringe.
    a = np.zeros((100, 100, 4), np.uint8); cv2.circle(a, (50, 50), 40, (20, 20, 20, 255), -1)
    ring = (cv2.circle(np.zeros((100, 100), np.uint8), (50, 50), 42, 255, 3) > 0) & (a[:, :, 3] == 0)
    a[ring] = (250, 250, 250, 120); cv2.imwrite(str(tmp_path / 'halo.png'), a)
    assert 'EDGE_HALO' in [w['code'] for w in prepare_asset(tmp_path / 'halo.png', tmp_path / 'h2.png')['warnings']]
    (tmp_path / 'bad.png').write_bytes(b'\x89PNG\r\n\x1a\nnot really')
    with pytest.raises(Exception):
        prepare_asset(tmp_path / 'bad.png', tmp_path / 'bad2.png')


def test_premultiplied_compositing_has_no_dark_fringe(tmp_path):
    """Regression for straight-alpha warping: transparent pixels with black RGB used to darken downscaled edges."""
    from benchmark.run_2d_quality import legacy_composite
    prod = np.zeros((200, 200, 4), np.uint8); cv2.circle(prod, (100, 100), 80, (255, 255, 255, 255), -1, cv2.LINE_AA)
    prod[:, :, 3] = cv2.circle(np.zeros((200, 200), np.uint8), (100, 100), 80, 255, -1, cv2.LINE_AA)
    prod[:, :, :3] = np.where(prod[:, :, 3:] > 0, 255, 0)      # clean cut-out: white wherever visible, black where transparent
    cv2.imwrite(str(tmp_path / 'p.png'), prod)
    frame = np.full((120, 160, 3), 255, np.uint8); quad = [[40, 20], [100, 22], [98, 80], [42, 78]]
    v8 = Planar2DPlan(tmp_path / 'p.png', {0: quad}).apply(0, frame)
    v6 = legacy_composite(frame, prod, quad)
    assert v8.min() >= 250                      # white on white stays white
    assert v6.min() < 240                       # the old compositor darkens the edge (measured minimum 224)


def _fake_track(gains):
    frames = [{'idx': i, 'chroma': [0.2 * g for g in gains[i]]} for i in range(len(gains))]
    return {'ref_idx': 0, 'frames': frames}


def test_temporal_gains_deadband_tracking_and_clamp():
    flat = _fake_track([[1, 1, 1]] + [[1.005, .995, 1.0]] * 30)
    g, s = temporal_gains(flat, 1, 30); assert g == {} and s['applied'] is False
    ramp = _fake_track([[1, 1, 1]] + [[1 - .0066 * i] * 3 for i in range(1, 31)])
    g, s = temporal_gains(ramp, 1, 30)
    assert s['applied'] and abs(g[15][0] - (1 - .0066 * 15)) < .01 and abs(g[30][0] - .80) < .02
    dark = _fake_track([[1, 1, 1]] + [[.3, .3, .3]] * 10)
    g, _ = temporal_gains(dark, 1, 10); assert min(g[5]) == pytest.approx(.75)   # clamped, never amplified past policy


# ------------------------------------------------------------------ 3D assets
def test_glb_loader_accepts_valid_and_rejects_adversarial(tmp_path):
    mesh, rep = load_glb(Path(bottle_glb(tmp_path / 'b.glb')).read_bytes())
    assert rep['textured'] and rep['triangles'] == 256 and abs(rep['size_m'][1] - .2) < 1e-6
    good = Path(box_glb(tmp_path / 'x.glb')).read_bytes()
    with pytest.raises(AssetError): load_glb(b'NOPE' + good[4:])
    with pytest.raises(AssetError): load_glb(good[:-40])
    js_len = struct.unpack_from('<I', good, 12)[0]; doc = json.loads(good[20:20 + js_len])
    def rebuild(d):
        js = json.dumps(d).encode(); js += b' ' * (-len(js) % 4); rest = good[20 + js_len:]
        return b'glTF' + struct.pack('<II', 2, 12 + 8 + len(js) + len(rest)) + struct.pack('<II', len(js), 0x4E4F534A) + js + rest
    ext = json.loads(json.dumps(doc)); ext['buffers'][0]['uri'] = 'file:///etc/passwd'
    with pytest.raises(AssetError, match='External'): load_glb(rebuild(ext))
    oob = json.loads(json.dumps(doc)); oob['accessors'][0]['count'] = 10_000
    with pytest.raises(AssetError, match='outside'): load_glb(rebuild(oob))
    lines = json.loads(json.dumps(doc)); lines['meshes'][0]['primitives'][0]['mode'] = 1
    with pytest.raises(AssetError, match='triangle'): load_glb(rebuild(lines))


# ------------------------------------------------------------------ 3D API workflow
def _drive_3d(c, tmp_path, kind, expect):
    from benchmark.fixtures3d import generate3d
    register(c, 'Brand', 'b3@example.com', 'brand', 'Studio3D')
    tok = c.post('/api/invitations', json={'email': 'c3@example.com', 'role': 'creator'}).json()['invitation_token']
    c.post('/api/auth/logout')
    assert c.post('/api/auth/register', json={'name': 'C', 'email': 'c3@example.com', 'password': PW, 'role': 'creator', 'invitation_token': tok}).status_code == 200
    glb = bottle_glb(tmp_path / 'bottle.glb')
    meta = generate3d(kind, 7, tmp_path / 'clips', glb)
    r = c.post('/api/videos', data={'title': kind, 'license_confirmed': 'true'}, files={'video': ('v.mp4', Path(meta['path']).read_bytes(), 'video/mp4')})
    vid = r.json()['id']; c.post(f'/api/videos/{vid}/analyze')
    login(c, 'b3@example.com')
    img = io.BytesIO(); Image.new('RGBA', (60, 120), (200, 40, 40, 255)).save(img, 'PNG')
    pid = c.post('/api/products', data={'name': 'Bottle'}, files={'image': ('b.png', img.getvalue(), 'image/png')}).json()['id']
    no_model = c.post('/api/placements', json={'video_id': vid, 'product_id': pid, 'frame_second': .4, 'x': .5, 'y': .5, 'width': .2, 'start_second': .4, 'end_second': 2.8,
                                                'tracking_quad': meta['quad_norm'], 'model3d': meta['model3d']})
    assert no_model.json()['detail']['code'] == 'MODEL_REQUIRED'
    up = c.post(f'/api/products/{pid}/model', files={'model': ('b.glb', Path(glb).read_bytes(), 'model/gltf-binary')})
    assert up.status_code == 200 and up.json()['model_report']['textured']
    assert c.post(f'/api/products/{pid}/model', files={'model': ('x.glb', b'garbage', 'model/gltf-binary')}).json()['detail']['code'] == 'MODEL_INVALID'
    pl = c.post('/api/placements', json={'video_id': vid, 'product_id': pid, 'frame_second': .4, 'x': .5, 'y': .5, 'width': .2, 'start_second': .4, 'end_second': 2.8,
                                          'tracking_quad': meta['quad_norm'], 'model3d': meta['model3d']}).json()['id']
    q = c.post(f'/api/placements/{pl}/quality-check').json()
    assert q['status'] == expect, q.get('failures')
    return pl, q, meta


@needs_media
def test_3d_placement_renders_validates_and_previews(manual, tmp_path):
    pl, q, meta = _drive_3d(manual, tmp_path, 'orbit', 'PASSED')
    assert q['mode'] == 'TRACKED_3D' and q['scene_class'] == 'E_3D_ON_TRACKED_PLANE'
    assert q['camera']['focal_source'] == 'estimated' and abs(q['camera']['focal_px'] - 520) / 520 < .02
    assert q['lighting']['source'] == 'reviewer'
    pre = manual.get(f'/api/placements/{pl}/preview?t=1.0')
    assert pre.status_code == 200 and pre.headers['x-preview'] == 'unapproved' and pre.content[:2] == b'\xff\xd8'
    manual.post(f'/api/placements/{pl}/fit-review', data={'decision': 'accept', 'confidence': '0.9'})
    assert manual.post(f'/api/placements/{pl}/propose').status_code == 200
    login(manual, 'c3@example.com')
    manual.put(f'/api/placements/{pl}/rights', json={'permitted_channels': ['social'], 'creator_consent': True})
    assert manual.post(f'/api/placements/{pl}/approve').json()['status'] == 'CREATOR_APPROVED'
    login(manual, 'b3@example.com')
    assert manual.post(f'/api/placements/{pl}/approve').json()['status'] == 'READY_FOR_RENDER'
    job = manual.post(f'/api/placements/{pl}/render').json()
    assert job['renderer'] == 'tracked_3d'
    jid, token = main.claim_next_job(); main.render_job(jid, token)
    j = next(x for x in manual.get('/api/jobs').json() if x['id'] == jid)
    assert j['status'] == 'SUCCEEDED', j['error']
    assert j['validation']['status'] == 'PASSED' and j['validation']['interval_frames_checked'] == 61
    assert j['validation']['exposure']['seconds'] > 2 and j['validation']['plan']['camera']['focal_source'] == 'estimated'


@needs_media
def test_3d_refuses_unobservable_camera(manual, tmp_path):
    pl, q, _ = _drive_3d(manual, tmp_path, 'topdown', 'FAILED')
    f = next(x for x in q['failures'] if x['code'] == 'FOCAL_UNOBSERVABLE')
    assert 'field of view' in f['recommended_action']
    assert manual.post(f'/api/placements/{pl}/propose').json()['detail']['code'] == 'QUALITY_NOT_PASSED'


# ------------------------------------------------------------------ cross-organization commerce
def _overlay_clip(tmp_path):
    out = tmp_path / 'inv.mp4'
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=15:duration=3', '-f', 'lavfi', '-i', 'sine=duration=3',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(out)], check=True, timeout=60)
    return out


@needs_media
def test_marketplace_licence_workflow_conflicts_and_takedown(manual, tmp_path):
    c = manual
    # Creator organization lists a video.
    register(c, 'Cara', 'cara@studio.test', 'creator', 'Cara Studio')
    vid = c.post('/api/videos', data={'title': 'Kitchen vlog', 'license_confirmed': 'true'}, files={'video': ('v.mp4', _overlay_clip(tmp_path).read_bytes(), 'video/mp4')}).json()['id']
    assert c.put(f'/api/videos/{vid}/listing', json={'price_usd': 500, 'categories': ['food']}).json()['detail']['code'] == 'NOT_ANALYZED'
    c.post(f'/api/videos/{vid}/analyze')
    lst = c.put(f'/api/videos/{vid}/listing', json={'price_usd': 500, 'categories': ['Food', 'lifestyle'], 'excluded_categories': ['alcohol'], 'channels': ['social', 'web']}).json()
    assert lst['listed'] and lst['categories'] == ['food', 'lifestyle']
    # Brand organization (separate) cannot touch the footage before a licence.
    c.post('/api/auth/logout'); register(c, 'Bea', 'bea@brand.test', 'brand', 'Bea Foods')
    img = io.BytesIO(); Image.new('RGBA', (80, 80), (20, 160, 60, 255)).save(img, 'PNG')
    prod = c.post('/api/products', data={'name': 'Sauce'}, files={'image': ('s.png', img.getvalue(), 'image/png')}).json()['id']
    assert c.get(f'/api/videos/{vid}/stream').status_code == 404
    body = {'video_id': vid, 'product_id': prod, 'frame_second': .5, 'x': .6, 'y': .1, 'width': .25, 'start_second': .5, 'end_second': 2.5}
    assert c.post('/api/placements', json=body).status_code == 404
    camp = c.post('/api/campaigns', json={'name': 'Launch', 'category': 'food', 'budget_usd': 2000, 'channels': ['social'], 'product_id': prod}).json()['id']
    unsafe = c.post('/api/campaigns', json={'name': 'Avoid lifestyle', 'category': 'food', 'brand_safety_exclusions': ['lifestyle']}).json()['id']
    market = c.get(f'/api/marketplace?campaign_id={camp}').json()
    item = next(x for x in market if x['video_id'] == vid)
    assert item['match_score'] >= 80 and not item['blockers'] and item['preview_frames']
    assert c.get(item['preview_frames'][0]).status_code == 200            # keyframes only
    assert c.get(f'/api/marketplace?campaign_id={unsafe}').json() == []
    assert c.post('/api/offers', json={'campaign_id': unsafe, 'video_id': vid}).json()['detail']['code'] == 'LISTING_BLOCKED'
    offer = c.post('/api/offers', json={'campaign_id': camp, 'video_id': vid, 'deliverables': '1 placement, 2 s'}).json()
    assert offer['price_usd'] == 500
    # Creator accepts -> licence grant.
    login(c, 'cara@studio.test')
    acc = c.post(f"/api/offers/{offer['id']}/accept").json()
    assert acc['status'] == 'ACCEPTED' and acc['grant_id']
    # A competing brand cannot take the same category on this video.
    c.post('/api/auth/logout'); register(c, 'Rex', 'rex@rival.test', 'brand', 'Rival Foods')
    rc = c.post('/api/campaigns', json={'name': 'Rival', 'category': 'food'}).json()['id']
    assert c.post('/api/offers', json={'campaign_id': rc, 'video_id': vid}).json()['detail']['code'] == 'CATEGORY_CONFLICT'
    assert c.get(f'/api/videos/{vid}/stream').status_code == 404
    # Licensed brand places the product; creator consent is bounded by the licence.
    login(c, 'bea@brand.test')
    assert c.get(f'/api/videos/{vid}/stream').status_code == 200
    assert any(v['id'] == vid and v['licensed'] == 1 for v in c.get('/api/videos').json())   # licensed video appears in the brand's library
    pl = c.post('/api/placements', json=body).json()['id']
    assert c.post(f'/api/placements/{pl}/quality-check').json()['status'] == 'PASSED'
    c.post(f'/api/placements/{pl}/fit-review', data={'decision': 'accept', 'confidence': '0.9'}); c.post(f'/api/placements/{pl}/propose')
    login(c, 'cara@studio.test')
    assert any(p['id'] == pl for p in c.get('/api/placements').json())      # creator org sees placements on its video
    assert c.get(f'/api/products/{prod}/image').status_code == 200
    assert c.put(f'/api/placements/{pl}/rights', json={'permitted_channels': ['broadcast'], 'creator_consent': True}).json()['detail']['code'] == 'OUTSIDE_LICENCE'
    assert c.put(f'/api/placements/{pl}/rights', json={'permitted_channels': ['social'], 'creator_consent': True}).status_code == 200
    assert c.post(f'/api/placements/{pl}/approve').json()['status'] == 'CREATOR_APPROVED'
    login(c, 'bea@brand.test')
    assert c.post(f'/api/placements/{pl}/approve').json()['status'] == 'READY_FOR_RENDER'
    c.post(f'/api/placements/{pl}/render'); jid, tk = main.claim_next_job(); main.render_job(jid, tk)
    job = next(x for x in c.get('/api/jobs').json() if x['id'] == jid)
    assert job['status'] == 'SUCCEEDED' and c.get(job['download_url']).status_code == 200
    offers = {o['id']: o for o in c.get('/api/offers').json()}
    assert offers[offer['id']]['status'] == 'DELIVERED'
    assert c.post(f"/api/offers/{offer['id']}/complete").json()['payment'].startswith('NOT_PROCESSED')
    rep = c.get(f'/api/campaigns/{camp}/report').json()
    line = rep['lines'][0]
    assert line['render_validated'] and line['exposure_seconds'] > 1.5 and line['placement_status'] == 'DELIVERED'
    assert 'viewer impressions' in ' '.join(rep['methodology']['not_measured'])
    assert c.get(f'/api/campaigns/{camp}/report?format=csv').text.startswith('offer_id')
    login(c, 'cara@studio.test')
    assert c.get('/api/earnings').json()['completed_payable'] == {'count': 1, 'usd': 500}
    # Takedown: revoking the licence revokes the placement and blocks delivery and access.
    tk = c.post(f"/api/offers/{offer['id']}/revoke-grant?reason=Brand%20dispute").json()
    assert tk['placements_revoked'] == 1
    login(c, 'bea@brand.test')
    assert c.get(job['download_url']).status_code == 403
    assert c.get(f'/api/videos/{vid}/stream').status_code == 404
    assert not any(v['id'] == vid for v in c.get('/api/videos').json())


# ------------------------------------------------------------------ signed links
@needs_media
def test_signed_download_links(manual, tmp_path, monkeypatch):
    from test_integrity import drive_to_ready
    pl, _ = drive_to_ready(manual, tmp_path, 'static', 6)
    manual.post(f'/api/placements/{pl}/render'); jid, tk = main.claim_next_job(); main.render_job(jid, tk)
    link = manual.post(f'/api/jobs/{jid}/download-link').json()
    assert link['ttl_seconds'] == 300
    anon = CSRSTestClient(main.app)
    r = anon.get(link['url']); assert r.status_code == 200 and r.content[4:8] == b'ftyp'
    assert anon.get(link['url'][:-3] + 'abc').status_code == 403
    monkeypatch.setattr(main, 'LINK_TTL_SECONDS', -1)
    assert anon.get(manual.post(f'/api/jobs/{jid}/download-link').json()['url']).json()['detail']['code'] == 'LINK_EXPIRED'
    monkeypatch.setattr(main, 'LINK_TTL_SECONDS', 300)
    fresh = manual.post(f'/api/jobs/{jid}/download-link').json()['url']
    login(manual, 'brand@example.com')
    manual.patch(f'/api/placements/{pl}/revise', data={'frame_second': .4, 'x': .5, 'y': .5, 'width': .2, 'start_second': .4, 'end_second': 2.0})
    assert anon.get(fresh).status_code == 403
    rec = logging.LogRecord('uvicorn.access', 20, '', 0, '%s - "%s %s"', ('1.2.3.4', 'GET', fresh), None)
    main._RedactSignedLinks().filter(rec)
    assert 'REDACTED' in rec.args[2] and fresh.split('/')[-1] not in rec.getMessage()


# ------------------------------------------------------------------ backup and restore
@needs_media
def test_backup_restore_round_trip(manual, tmp_path):
    from test_integrity import drive_to_ready
    sys.path.insert(0, str(ROOT / 'ops'))
    import backup as ops
    pl, _ = drive_to_ready(manual, tmp_path, 'static', 8)
    manual.post(f'/api/placements/{pl}/render'); jid, tk = main.claim_next_job(); main.render_job(jid, tk)
    out_before = hashlib.sha256(manual.get(f'/api/jobs/{jid}/download').content).hexdigest()
    with pytest.raises(SystemExit): ops.backup(main.DATA, tmp_path / 'nodb', tmp_path / 'missing.sqlite3')
    pg_url = os.getenv('CC_TEST_DATABASE_URL')
    if pg_url:   # PostgreSQL: consistent logical export, restored into a fresh schema
        import psycopg, secrets
        from app import db as dbm
        man = ops.backup(main.DATA, tmp_path / 'bk', connect=main.db)
        assert man['database'] == 'logical' and 'db/users.jsonl' in man['files']
        target = 't_restore_' + secrets.token_hex(4)
        with psycopg.connect(pg_url, autocommit=True) as pc: pc.execute(f'CREATE SCHEMA {target}')
        connect = lambda: dbm.connect(None, url=pg_url, schema=target, id_tabs=main._ID_TABLES)
    else:
        man = ops.backup(main.DATA, tmp_path / 'bk', main.DB)
        assert man['database'] == 'sqlite' and 'context_commerce.sqlite3' in man['files']
        connect = lambda: main.dbm.connect(tmp_path / 'restored' / 'context_commerce.sqlite3', url='')
    assert any(k.startswith('outputs/') for k in man['files'])
    victim = next(k for k in man['files'] if k.startswith('uploads/'))
    good = (tmp_path / 'bk' / victim).read_bytes(); (tmp_path / 'bk' / victim).write_bytes(good + b'x')
    assert ops.verify(tmp_path / 'bk')[1] == [victim]
    with pytest.raises(SystemExit): ops.restore(tmp_path / 'bk', tmp_path / 'restored', connect=connect if pg_url else None)
    (tmp_path / 'bk' / victim).write_bytes(good)
    res = ops.restore(tmp_path / 'bk', tmp_path / 'restored', connect=connect if pg_url else None)
    assert res.get('integrity_check') == 'ok' or res.get('verified') is True
    with connect() as con:
        out_path = con.execute('SELECT output_path FROM jobs WHERE id=?', (jid,)).fetchone()[0]
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3
    assert out_path.startswith(str((tmp_path / 'restored').resolve()))
    assert hashlib.sha256(Path(out_path).read_bytes()).hexdigest() == out_before
    if pg_url:   # a populated target database is refused, even with an empty data directory
        with pytest.raises(SystemExit): ops.restore(tmp_path / 'bk', tmp_path / 'restored2', connect=connect)
        main.dbm.close_pools()
        with psycopg.connect(pg_url, autocommit=True) as pc: pc.execute(f'DROP SCHEMA {target} CASCADE')
