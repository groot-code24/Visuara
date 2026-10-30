"""Real-footage benchmark tooling, exercised end to end on synthetic clips that are explicitly marked synthetic."""
import hashlib, json, shutil

import pytest

from app import main
from benchmark import real_footage as rf
from benchmark.fixtures import generate, product_image

needs_media = pytest.mark.skipif(not (main.tool_exists('ffmpeg') and main._has_cv2()), reason='FFmpeg and OpenCV are required')
CONSENT = {'granted_by': 'Test Creator', 'date': '2026-10-30', 'reference': 'consent-form-001', 'scope': ['benchmark', 'product-placement']}


def _entry(tmp, meta, cid, creator, suitable, code=None):
    p = tmp / f'{cid}.mp4'; shutil.copy(meta['path'], p)
    return {'clip_id': cid, 'file': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'creator_id': creator, 'device': 'synthetic-camera',
            'consent': CONSENT, 'license': 'internal-benchmark', 'synthetic': True,
            'label': {'mode': '2d', 'suitable': suitable, 'expected_code': code, 'quad_norm': meta['quad_norm'], 'product_image': 'product.png',
                      'ref_second': .4, 'start_second': .4, 'end_second': 2.8}}


def test_manifest_validation_refuses_missing_consent_and_changed_files(tmp_path):
    meta = generate('static', 1, tmp_path / 'g')
    good = _entry(tmp_path, meta, 'c1', 'alice', True)
    no_consent = {**good, 'clip_id': 'c2', 'consent': {'granted_by': 'x', 'date': 'y', 'reference': 'z', 'scope': ['marketing']}}
    tampered = {**good, 'clip_id': 'c3', 'sha256': '0' * 64}
    (tmp_path / 'manifest.json').write_text(json.dumps({'split_salt': 's1', 'clips': [good, no_consent, tampered, {'clip_id': 'c4'}]}))
    clips, errors = rf.validate(tmp_path / 'manifest.json')
    assert [c['clip_id'] for c in clips] == ['c1']
    assert any('c2' in e and 'benchmark' in e for e in errors) and any('c3' in e and 'SHA-256' in e for e in errors) and any('c4' in e for e in errors)
    with pytest.raises(SystemExit):
        rf.run(tmp_path / 'manifest.json', 'dev', tmp_path / 'run')


def test_creator_level_split_is_deterministic():
    splits = {c: rf.split_of(c, 'salt-a') for c in (f'creator{i}' for i in range(200))}
    assert splits == {c: rf.split_of(c, 'salt-a') for c in splits}
    assert 0.25 < sum(v == 'dev' for v in splits.values()) / 200 < 0.55


def test_statistics():
    assert rf.wilson(0, 21) == (0.0, 0.1546)                       # 0 errors in 21 still allows ~15%
    assert rf.krippendorff_interval([[4, 4], [2, 2], [5, 5]]) == 1.0
    assert rf.krippendorff_interval([[1, 5], [5, 1], [3, 3]]) < 0


@needs_media
def test_run_sheet_and_report_end_to_end(tmp_path):
    product_image(tmp_path / 'product.png')
    creator = next(c for c in (f'creator{i}' for i in range(50)) if rf.split_of(c, 'salt-x') == 'dev')
    entries = [_entry(tmp_path, generate('static', 2, tmp_path / 'g1'), 'ok_static', creator, True),
               _entry(tmp_path, generate('occluder', 2, tmp_path / 'g2'), 'bad_occluded', creator, False, 'PARTIAL_OCCLUSION_UNSUPPORTED')]
    (tmp_path / 'manifest.json').write_text(json.dumps({'split_salt': 'salt-x', 'clips': entries}))
    res = rf.run(tmp_path / 'manifest.json', 'dev', tmp_path / 'run')
    by = {r['clip_id']: r for r in res['rows']}
    assert by['ok_static']['decision'] == 'PASSED' and by['ok_static']['output_validation'] == 'PASSED'
    assert by['bad_occluded']['decision'] == 'FAILED'
    info = rf.sheet(tmp_path / 'run')
    page = (tmp_path / 'run' / 'ratings.html').read_text(encoding='utf-8')
    assert info['items'] == 1 and 'ok_static' not in page and '<video' in page          # blind: no clip ids on the sheet
    for rater, score in (('r1', 4), ('r2', 5)):
        (tmp_path / f'{rater}.json').write_text(json.dumps({'rater': rater, 'ratings': {'item1': {c: score for c, _ in rf.CRITERIA}}}))
    rep = rf.report(tmp_path / 'run', [tmp_path / 'r1.json', tmp_path / 'r2.json'])
    assert rep['real_footage']['clips'] == 0                         # synthetic rows are never real-footage evidence
    assert rep['synthetic_smoke_rows_not_evidence']['false_accept'] == '0/1'
    assert rep['ratings']['raters'] == 2 and rep['ratings']['criteria']['overall']['n'] == 0   # synthetic items excluded from ratings too
