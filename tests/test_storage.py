"""Private S3-compatible object storage: write-through, restore on a host without local files, presigned delivery."""
import hashlib, shutil, urllib.parse

import boto3
import pytest
from moto import mock_aws

from app import main, storage
from test_api import CSRSTestClient, TestDB, register, isolated_db  # noqa: F401  (shared fixtures)
from test_integrity import manual, login, needs_media, drive_to_ready  # noqa: F401

BUCKET = 'cc-test-private'


@pytest.fixture()
def s3(monkeypatch):
    with mock_aws():
        for k, v in (('AWS_ACCESS_KEY_ID', 'test'), ('AWS_SECRET_ACCESS_KEY', 'test'), ('AWS_DEFAULT_REGION', 'us-east-1'),
                     ('CC_S3_BUCKET', BUCKET), ('CC_S3_REGION', 'us-east-1')):
            monkeypatch.setenv(k, v)
        monkeypatch.delenv('CC_S3_ENDPOINT_URL', raising=False)
        storage.reset()
        client = boto3.client('s3', region_name='us-east-1'); client.create_bucket(Bucket=BUCKET)
        yield client
        storage.reset()


def keys(client): return {o['Key'] for o in client.list_objects_v2(Bucket=BUCKET).get('Contents', [])}


def test_object_keys_are_relative_to_media_directories(monkeypatch):
    assert storage.key_for('/srv/data/uploads/video_x.mp4') == 'uploads/video_x.mp4'
    assert storage.key_for(r'D:\other\data\outputs\placement_1.mp4') == 'outputs/placement_1.mp4'
    assert storage.key_for('/etc/passwd') is None
    monkeypatch.setenv('CC_S3_PREFIX', 'prod/')
    assert storage.key_for('/srv/data/frames/v1_0.jpg') == 'prod/frames/v1_0.jpg'


@needs_media
def test_write_through_restore_and_presigned_delivery(s3, manual, tmp_path):
    pl, _ = drive_to_ready(manual, tmp_path, 'static', 3)
    stored = keys(s3)
    for prefix in ('uploads/video_', 'uploads/product_', 'frames/', 'analyses/'):
        assert any(k.startswith(prefix) for k in stored), (prefix, stored)
    video_key = next(k for k in stored if k.startswith('uploads/video_'))
    assert s3.head_object(Bucket=BUCKET, Key=video_key)['ServerSideEncryption'] == 'AES256'
    assert manual.get('/api/health').json()['object_storage']['backend'] == 's3_compatible'

    # A worker host with an empty disk restores the source video, product and track from the bucket.
    for d in ('uploads', 'frames', 'analyses'):
        shutil.rmtree(main.DATA / d, ignore_errors=True)
    jid = manual.post(f'/api/placements/{pl}/render').json()['id']
    claimed, token = main.claim_next_job()
    assert claimed == jid
    main.render_job(jid, token)
    job = TestDB().execute('SELECT status,output_path,error FROM jobs WHERE id=?', (jid,)).fetchone()
    assert job['status'] == 'SUCCEEDED', job['error']
    out_key = storage.key_for(job['output_path'])
    assert out_key in keys(s3)
    expected = hashlib.sha256(open(job['output_path'], 'rb').read()).hexdigest()

    # Delivery: access checks first, then a short-lived presigned redirect; the API host needs no local copy.
    main.Path(job['output_path']).unlink()
    r = manual.get(f'/api/jobs/{jid}/download', follow_redirects=False)
    assert r.status_code == 307, r.text
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.headers['location']).query))
    assert q['X-Amz-Expires'] == '300' and 'X-Amz-Signature' in q and 'attachment' in q['response-content-disposition']
    assert hashlib.sha256(s3.get_object(Bucket=BUCKET, Key=out_key)['Body'].read()).hexdigest() == expected
    manual.post('/api/auth/logout')
    assert manual.get(f'/api/jobs/{jid}/download', follow_redirects=False).status_code == 401
    register(manual, 'Rival', 'rival@example.com', 'brand', 'Rival Org')      # another organization never gets a URL
    assert manual.get(f'/api/jobs/{jid}/download', follow_redirects=False).status_code == 404


def test_push_media_uploads_existing_local_files(s3, tmp_path, monkeypatch):
    from ops import dbtransfer
    monkeypatch.setattr(main, 'DATA', tmp_path)
    for rel in ('uploads/video_a.mp4', 'frames/v1_0.jpg', 'outputs/x.part.mp4', 'notes.txt'):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True); (tmp_path / rel).write_bytes(b'data')
    assert dbtransfer.push_media(tmp_path) == {'uploaded': 2, 'bucket': BUCKET}
    assert keys(s3) == {'uploads/video_a.mp4', 'frames/v1_0.jpg'}      # temp render parts and unknown files stay local
