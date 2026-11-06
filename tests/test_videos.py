"""Phase 3: video metadata from the file, duplicate detection, new versions, deletion with storage clean-up, hash backfill."""
import hashlib
from fractions import Fraction
from pathlib import Path

from app import main, videos
from helpers import TestDB, owner, upload, drive_to_approved, needs_media
from benchmark.fixtures import generate


def _post(c, path, title='Clip', **extra):
    return c.post('/api/videos', data={'title': title, 'license_confirmed': 'true', **extra},
                  files={'video': (Path(path).name, Path(path).read_bytes(), 'video/mp4')})


@needs_media
def test_metadata_comes_from_the_file_and_duplicates_are_detected(manual, tmp_path):
    owner(manual)
    meta = generate('static', 3, tmp_path)
    r = _post(manual, meta['path']).json()
    assert r['codec'] == 'h264' and 'mp4' in r['container'] and r['has_audio'] is True          # the fixtures carry an AAC track
    f = Fraction(r['width'], r['height']); assert r['aspect_ratio'] == f'{f.numerator}:{f.denominator}'
    assert r['content_sha256'] == hashlib.sha256(Path(meta['path']).read_bytes()).hexdigest() and r['duplicate_of'] == []
    again = _post(manual, meta['path'], 'Same again').json()
    assert again['duplicate_of'] == [r['id']]
    refused = _post(manual, meta['path'], 'Refuse', reject_duplicates='true')
    assert refused.status_code == 409 and refused.json()['detail'] == {'code': 'DUPLICATE_VIDEO', 'message': refused.json()['detail']['message'], 'duplicate_of': [r['id'], again['id']]}
    owner(manual, 'other@else.io', 'Else')                                           # another organization: not a duplicate
    assert _post(manual, meta['path']).json()['duplicate_of'] == []


@needs_media
def test_a_mislabelled_file_is_refused(manual, tmp_path):
    owner(manual)
    fake = tmp_path / 'not_a_video.mp4'; fake.write_bytes(b'\x00\x00\x00 ftypisom' + b'junk' * 500)
    assert _post(manual, fake).status_code == 400


@needs_media
def test_new_versions_keep_earlier_versions_and_analyses(manual, tmp_path):
    owner(manual)
    a = generate('static', 3, tmp_path); b = generate('pan_zoom', 4, tmp_path)
    vid = upload(manual, a['path'], 'Kitchen')                                      # analysed: version 1 has an analysis
    r = manual.post(f'/api/videos/{vid}/versions', data={'license_confirmed': 'true'}, files={'video': ('b.mp4', Path(b['path']).read_bytes(), 'video/mp4')})
    assert r.status_code == 200 and r.json()['version'] == 2
    vs = manual.get(f'/api/videos/{vid}/versions').json()
    assert vs['current_version'] == 2 and [v['version'] for v in vs['versions']] == [1, 2]
    assert vs['versions'][1]['content_sha256'] == hashlib.sha256(Path(b['path']).read_bytes()).hexdigest()
    v = next(x for x in manual.get('/api/videos').json() if x['id'] == vid)
    assert v['analyzed'] == 0 and v['current_version'] == 2 and manual.get(f'/api/videos/{vid}/scenes').json() == []
    db = TestDB()
    assert db.execute("SELECT COUNT(*) FROM analysis_versions a JOIN video_versions vv ON vv.id=a.video_version_id WHERE vv.video_id=? AND vv.version=1", (vid,)).fetchone()[0] == 1
    used = manual.get('/api/usage').json()['usage']['storage_bytes']
    assert used >= Path(a['path']).stat().st_size + Path(b['path']).stat().st_size        # both versions are stored


@needs_media
def test_a_video_in_use_cannot_be_replaced_or_deleted(manual, tmp_path):
    pl, meta = drive_to_approved(manual, tmp_path, 'static', 3)
    vid = TestDB().execute('SELECT video_id FROM placements WHERE id=?', (pl,)).fetchone()['video_id']
    r = manual.post(f'/api/videos/{vid}/versions', data={'license_confirmed': 'true'}, files={'video': ('x.mp4', Path(meta['path']).read_bytes(), 'video/mp4')})
    assert r.status_code == 409 and r.json()['detail']['code'] == 'VIDEO_IN_USE'
    assert manual.delete(f'/api/videos/{vid}').json()['detail']['code'] == 'VIDEO_IN_USE'


@needs_media
def test_deletion_removes_files_and_hides_the_video(manual, tmp_path):
    owner(manual)
    meta = generate('static', 3, tmp_path)
    vid = upload(manual, meta['path'], 'Gone')
    src = TestDB().execute('SELECT source_path FROM videos WHERE id=?', (vid,)).fetchone()['source_path']
    frames = [r['image_path'] for r in TestDB().execute('SELECT image_path FROM frames WHERE video_id=?', (vid,)).fetchall()]
    assert Path(src).exists() and frames and all(Path(f).exists() for f in frames)
    before = manual.get('/api/usage').json()['usage']['storage_bytes']
    r = manual.delete(f'/api/videos/{vid}').json()
    assert r['deleted'] is True and r['files_removed'] >= 1 + len(frames)
    assert not Path(src).exists() and not any(Path(f).exists() for f in frames)
    assert all(v['id'] != vid for v in manual.get('/api/videos').json())
    assert manual.get(f'/api/videos/{vid}/scenes').status_code == 404 and manual.delete(f'/api/videos/{vid}').status_code == 404
    assert manual.get('/api/usage').json()['usage']['storage_bytes'] < before
    assert TestDB().execute('SELECT deleted_at FROM videos WHERE id=?', (vid,)).fetchone()['deleted_at']        # kept for the audit trail


@needs_media
def test_older_versions_are_hashed_by_maintenance(manual, tmp_path):
    owner(manual)
    meta = generate('static', 3, tmp_path)
    vid = upload(manual, meta['path'], 'Old', analyze=False)
    with main.db() as c: c.execute('UPDATE video_versions SET content_sha256=NULL, codec=NULL WHERE video_id=?', (vid,))
    assert videos.backfill_hashes(5) == 1
    row = TestDB().execute('SELECT content_sha256, codec FROM video_versions WHERE video_id=?', (vid,)).fetchone()
    assert row['content_sha256'] == hashlib.sha256(Path(meta['path']).read_bytes()).hexdigest() and row['codec'] == 'h264'
    assert videos.backfill_hashes(5) == 0
