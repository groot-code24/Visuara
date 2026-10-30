import os, subprocess
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from PIL import Image
import io, time

from app import main, db as dbm


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """Fresh database per test: a temp SQLite file, or a new PostgreSQL schema when CC_TEST_DATABASE_URL is set."""
    monkeypatch.setattr(main, 'DB', tmp_path / 'test.sqlite3')
    url = os.getenv('CC_TEST_DATABASE_URL')
    if not url:
        monkeypatch.delenv('DATABASE_URL', raising=False); monkeypatch.delenv('CC_DATABASE_URL', raising=False)
        yield None; return
    import psycopg, secrets
    schema = 't_' + secrets.token_hex(6)
    with psycopg.connect(url, autocommit=True) as con: con.execute(f'CREATE SCHEMA {schema}')
    monkeypatch.setenv('DATABASE_URL', url); monkeypatch.setattr(main, 'DB_SCHEMA', schema)
    yield schema
    dbm.close_pools()
    with psycopg.connect(url, autocommit=True) as con: con.execute(f'DROP SCHEMA {schema} CASCADE')


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

class CSRSTestClient(TestClient):
    def request(self, method, url, **kwargs):
        if method.upper() in {'POST','PUT','PATCH','DELETE'} and self.cookies.get('cc_session'):
            headers=dict(kwargs.get('headers') or {})
            headers.setdefault('X-CSRF-Token', self.cookies.get('cc_csrf',''))
            kwargs['headers']=headers
        return super().request(method,url,**kwargs)

@pytest.fixture()
def client(tmp_path, monkeypatch, isolated_db):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'UPLOADS', tmp_path / 'uploads')
    monkeypatch.setattr(main, 'OUTPUTS', tmp_path / 'outputs')
    monkeypatch.setattr(main, 'FRAMES', tmp_path / 'frames')
    for p in (main.UPLOADS, main.OUTPUTS, main.FRAMES): p.mkdir(parents=True, exist_ok=True)
    main.init_db()
    with CSRSTestClient(main.app) as c:
        yield c

def register(c, name, email, role, org='Shared Studio'):
    r=c.post('/api/auth/register',json={'name':name,'email':email,'password':'strong-password-123','role':role,'organization':org})
    assert r.status_code==200, r.text
    return r.json()['user']

def test_register_login_and_health(client):
    u=register(client,'Brand User','brand@example.com','brand')
    assert u['role']=='brand'
    assert client.get('/api/me').json()['email']=='brand@example.com'
    client.post('/api/auth/logout')
    assert client.get('/api/me').status_code==401
    r=client.post('/api/auth/login',json={'email':'brand@example.com','password':'strong-password-123'})
    assert r.status_code==200
    assert client.get('/api/health').json()['status']=='ok'

def test_org_isolation(client):
    register(client,'Brand A','a@example.com','brand','Org A')
    image=Image.new('RGB',(100,100),'red'); b=io.BytesIO(); image.save(b,format='PNG')
    r=client.post('/api/products',data={'name':'Product A','description':'test'},files={'image':('product.png',b.getvalue(),'image/png')})
    assert r.status_code==200, r.text
    pid=r.json()['id']
    client.post('/api/auth/logout')
    register(client,'Brand B','b@example.com','brand','Org B')
    assert client.get('/api/products').json()==[]
    assert client.get(f'/api/products/{pid}/image').status_code==404

def test_video_upload_scene_render_approval_workflow(client, tmp_path):
    if not main.tool_exists('ffmpeg') or not main.tool_exists('ffprobe'):
        pytest.skip('FFmpeg/FFprobe not installed')
    # Shared organization permits this MVP's brand and creator accounts to collaborate.
    register(client,'Brand','brand@example.com','brand')
    brand_cookie=client.cookies.get('cc_session')
    invitation=client.post('/api/invitations',json={'email':'creator@example.com','role':'creator'})
    assert invitation.status_code==200, invitation.text
    invite_token=invitation.json()['invitation_token']
    client.post('/api/auth/logout')
    r=client.post('/api/auth/register',json={'name':'Creator','email':'creator@example.com','password':'strong-password-123','role':'creator','organization':'Ignored for invite','invitation_token':invite_token})
    assert r.status_code==200, r.text
    video=tmp_path/'sample.mp4'
    subprocess.run(['ffmpeg','-y','-f','lavfi','-i','color=c=blue:s=320x240:d=3:r=15','-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=44100','-shortest','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(video)],capture_output=True,timeout=60,check=True)
    r=client.post('/api/videos',data={'title':'Test clip','license_confirmed':'true'},files={'video':('sample.mp4',video.read_bytes(),'video/mp4')})
    assert r.status_code==200, r.text
    vid=r.json()['id']
    scenes=client.post(f'/api/videos/{vid}/analyze')
    assert scenes.status_code==200, scenes.text
    assert len(scenes.json()['scenes'])>=1
    client.post('/api/auth/logout')
    assert client.post('/api/auth/login',json={'email':'brand@example.com','password':'strong-password-123'}).status_code==200
    im=Image.new('RGBA',(100,100),(255,0,0,180)); b=io.BytesIO(); im.save(b,format='PNG')
    p=client.post('/api/products',data={'name':'Bottle','description':'demo'},files={'image':('bottle.png',b.getvalue(),'image/png')})
    assert p.status_code==200, p.text
    pid=p.json()['id']
    pl=client.post('/api/placements',json={'video_id':vid,'product_id':pid,'frame_second':1,'x':0.6,'y':0.1,'width':0.2,'start_second':0.5,'end_second':2.5})
    assert pl.status_code==200, pl.text
    placement_id=pl.json()['id']
    qc=client.post(f'/api/placements/{placement_id}/quality-check')
    assert qc.status_code==200 and qc.json()['status']=='PASSED', qc.text
    assert qc.json()['scene_class']=='OVERLAY_SCREEN_SPACE'
    fit=client.post(f'/api/placements/{placement_id}/fit-review',data={'decision':'accept','confidence':'0.9','notes':'Human verified the product fits this scene'})
    assert fit.status_code==200, fit.text
    assert client.post(f'/api/placements/{placement_id}/propose').status_code==200
    client.post('/api/auth/logout')
    assert client.post('/api/auth/login',json={'email':'creator@example.com','password':'strong-password-123'}).status_code==200
    rights=client.put(f'/api/placements/{placement_id}/rights',json={'permitted_channels':['social','web'],'territories':['WORLDWIDE'],'creator_consent':True})
    assert rights.status_code==200, rights.text
    assert client.post(f'/api/placements/{placement_id}/approve').status_code==200
    client.post('/api/auth/logout')
    assert client.post('/api/auth/login',json={'email':'brand@example.com','password':'strong-password-123'}).status_code==200
    approved=client.post(f'/api/placements/{placement_id}/approve')
    assert approved.status_code==200 and approved.json()['status']=='READY_FOR_RENDER', approved.text
    job=client.post(f'/api/placements/{placement_id}/render')
    assert job.status_code==200, job.text
    # Rendering is handled asynchronously by the persistent SQLite-backed worker.
    deadline=time.time()+30
    jobs=client.get('/api/jobs').json()
    while jobs and jobs[0]['status'] in ('QUEUED','RUNNING') and time.time()<deadline:
        time.sleep(.2); jobs=client.get('/api/jobs').json()
    assert jobs[0]['status']=='SUCCEEDED', jobs[0]
    dl=client.get(jobs[0]['download_url'])
    assert dl.status_code==200
    assert dl.content[:4]==b'\x00\x00\x00' or len(dl.content)>1024
    revoked=client.post(f'/api/placements/{placement_id}/revoke-rights',data={'reason':'Creator withdrew permission'})
    assert revoked.status_code==200, revoked.text
    blocked=client.get(jobs[0]['download_url'])
    assert blocked.status_code==403

def test_tracking_quad_validation_and_real_perspective_render(tmp_path):
    if not main.tool_exists('ffmpeg') or not main.tool_exists('ffprobe') or not main._has_cv2():
        pytest.skip('FFmpeg, FFprobe and OpenCV are required')
    from app.tracking import validate_quad, render_tracked_video
    from app.compositor import product_quad_in_surface
    from app.validation import validate_render
    from benchmark.fixtures import generate
    import numpy as np
    with pytest.raises(ValueError): validate_quad([[0,0],[1,0],[1,1]])
    quad=validate_quad([[.18,.18],[.82,.2],[.8,.82],[.2,.8]])
    assert quad.shape==(4,2)
    # A moving planar surface with known ground truth (the previous testsrc2 source is not a planar surface).
    meta=generate('pan_zoom',0,tmp_path); product=tmp_path/'product.png'; output=tmp_path/'tracked.mp4'
    Image.new('RGBA',(80,100),(230,30,50,220)).save(product)
    result=render_tracked_video(meta['path'],product,output,meta['quad_norm'],.4,.4,2.8)
    assert result['mode']=='opencv_optical_flow_homography'
    assert output.exists() and output.stat().st_size>1024
    assert main.probe(output)[0] > 2
    # Regression: the product must be rendered on the selected surface, in every interval frame.
    s,e=round(.4*meta['fps']),int(2.8*meta['fps'])
    gt={i:product_quad_in_surface(meta['gt_surface_quads'][i],80,100) for i in range(s,e+1)}
    assert set(result['frame_quads'])==set(gt)
    assert max(np.abs(np.asarray(result['frame_quads'][i])-gt[i]).max() for i in gt)<3
    assert validate_render(meta['path'],output,product,result['frame_quads'])['status']=='PASSED'

def test_csrf_rejects_missing_token(client):
    register(client,'Brand','csrf@example.com','brand')
    bad=client.post('/api/invitations',json={'email':'member@example.com','role':'creator'},headers={'X-CSRF-Token':''})
    assert bad.status_code==403
    good=client.post('/api/invitations',json={'email':'member@example.com','role':'creator'})
    assert good.status_code==200

def test_generative_adapter_fails_explicitly_when_unconfigured(monkeypatch, tmp_path):
    for key in ('CC_REPLICATE_API_TOKEN','CC_REPLICATE_MODEL','CC_REPLICATE_INPUT_TEMPLATE'):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match='not configured'):
        main._run_replicate_generation({'source_path':str(tmp_path/'video.mp4')},{'image_path':str(tmp_path/'product.png')},{'id':1,'start_second':0,'end_second':1},1)


def test_visual_fit_gate_blocks_low_confidence(client):
    register(client,'Brand','fit@example.com','brand')
    # Endpoint enforces a conservative threshold even when the user attempts acceptance.
    r=client.post('/api/placements/999/fit-review',data={'decision':'accept','confidence':'0.4'})
    assert r.status_code==404

def test_campaign_conversion_import_and_deduplication(client):
    register(client,'Brand','campaign@example.com','brand')
    payload={'event_name':'purchase','value':19.99,'currency':'USD','dedupe_key':'order-001'}
    first=client.post('/api/conversions/import',json=payload)
    assert first.status_code==200, first.text
    assert first.json()['causal_lift']=='NOT_MEASURED'
    second=client.post('/api/conversions/import',json=payload)
    assert second.status_code==409
    analytics=client.get('/api/campaign-analytics').json()
    assert analytics['imported_conversions']==1
    assert analytics['causal_incrementality']=='NOT_MEASURED'
