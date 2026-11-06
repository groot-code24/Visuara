"""Phases 4 and 5: the staged analysis job (segmentation, frames per version, unavailable stages, resumption, cancellation,
duplicate prevention, failure) and deterministic scoring with brand safety."""
import json
import threading
from pathlib import Path

import pytest

from app import main, intelligence, scoring
from helpers import TestDB, owner, upload, login, make_user, needs_media, needs_model
from benchmark import fixtures_scene as scene
from benchmark.fixtures import generate


def _run_all():
    n = 0
    while intelligence.run_next(): n += 1
    return n


# ---------------------------------------------------------------- pure parts
def test_segmentation_finds_cuts_and_merges_short_scenes():
    s = [{'t': t / 4, 'dist': 0.9 if t in (8, 9, 20) else 0.05} for t in range(40)]           # cuts at 2.0, 2.25, 5.0 s
    assert intelligence.segment(s, 10.0, 0.35, 1.0) == [(0.0, 2.0), (2.0, 5.0), (5.0, 10.0)]
    assert intelligence.segment([{'t': 0, 'dist': 0}], 3.0, 0.35, 1.0) == [(0.0, 3.0)]
    assert intelligence.segment([{'t': 2.6, 'dist': 0.9}], 3.0, 0.35, 1.0) == [(0.0, 3.0)]          # a 0.4 s tail joins the scene


def test_a_brief_change_such_as_a_flash_is_not_a_cut():
    # a flash at 1.0 s: the picture changes and comes back. Neither its start nor its end is a boundary.
    s = [{'t': t / 4, 'dist': .05} for t in range(16)]
    s[4].update(dist=.8, across=.03); s[5].update(dist=.8, back=.03)
    assert intelligence.segment(s, 4.0, 0.35, 1.0) == [(0.0, 4.0)]
    s[4].update(across=.7); s[5].update(back=.7)                                        # a real change stays
    assert intelligence.segment(s, 4.0, 0.35, 1.0) == [(0.0, 1.0), (1.0, 4.0)]


@needs_media
def test_boundaries_are_placed_on_the_exact_frame_and_typed(tmp_path):
    from benchmark import fixtures_cuts
    from benchmark.evaluate_segmentation import evaluate_clip
    meta = fixtures_cuts.generate(4, tmp_path)                         # a cut, a cut within the same place, a dissolve, and a flash
    found, _ = intelligence.boundaries(meta['path'], meta['seconds'], intelligence.config())
    matches, false, misses = evaluate_clip(meta, [b['t'] for b in found])
    assert not false and not misses
    assert [m['error_frames'] for m in matches if m['type'].startswith('cut')] == [0, 0]
    assert [b['source'] for b in found] == ['HISTOGRAM_CUT', 'HISTOGRAM_CUT', 'GRADUAL_TRANSITION']


def test_an_object_that_is_the_surface_is_not_counted_as_covering_it():
    """Seen in the M14 walk: the detector read a framed poster as a clock and a patterned table as a keyboard, and the
    surfaces were reported 100% hidden. Something coinciding with a surface is the surface or lies on it, unless it is
    something that stands in front (a person)."""
    surface = (100, 100, 200, 180)
    y0 = 100 / 360; samples = [{'t': t, 'quad': [[.25, y0], [.5, y0], [.5, .5], [.25, .5]]} for t in (0.0, 0.5, 1.0)]   # the same box at 400 x 360
    def tr(label, box, tid=1):
        return {'track_id': tid, 'label': label, 'confidence': .5, 'observations': [{'t': t, 'bbox': list(box), 'confidence': .5} for t in (0.0, 0.5, 1.0)]}
    f = lambda tracks: intelligence._surface_objects(tracks, 0.5, surface, samples, 400, 360)
    assert f([tr('clock', (102, 101, 198, 179))]) == (None, 0.0, [], ['clock'])
    assert f([tr('tv', (101, 100, 199, 180))])[0] == 'screen'
    identified, covered, labels, _ = f([tr('person', (100, 95, 205, 185))])
    assert covered == 1.0 and labels == ['person']                                     # a person in front covers it
    identified, covered, labels, _ = f([tr('person', (150, 60, 260, 300))])
    assert covered == .5 and labels == ['person']                                      # half of it


def test_scores_rescale_over_measured_factors():
    w = {'a': .5, 'b': .25, 'c': .25}
    score, br = scoring.combine(w, {'a': 1.0, 'b': 0.0, 'c': None})
    assert score == pytest.approx(2 / 3, abs=1e-4)
    assert br['c']['status'] == 'UNAVAILABLE' and br['a']['effective_weight'] == pytest.approx(.667, abs=1e-3)
    assert scoring.combine(w, {'a': None, 'b': None, 'c': None})[0] == 0.0


def test_brand_safety_rules(manual):
    u = make_user('pub@p.io', org='P'); login(manual, 'pub@p.io')
    with main.db() as c:
        assert scoring.brand_safety(c, u['org_id'], '', 'Kitchen') == ('NOT_EVALUATED', [])
        assert scoring.brand_safety(c, u['org_id'], 'a quiet kitchen', 'x') == ('SAFE', [])
        assert scoring.brand_safety(c, u['org_id'], 'a bar with alcohol bottles', 'x') == ('REVIEW', ['alcohol'])
        assert scoring.brand_safety(c, u['org_id'], 'a man holds a gun', 'x')[0] == 'UNSAFE'
        assert scoring.brand_safety(c, u['org_id'], 'shotgun wedding', 'x')[0] == 'SAFE'                     # whole words only
    assert manual.post('/api/brand-safety/rules', json={'term': 'Pizza', 'verdict': 'REVIEW'}).status_code == 200
    assert manual.post('/api/brand-safety/rules', json={'term': 'pizza', 'verdict': 'UNSAFE'}).status_code == 409
    with main.db() as c: assert scoring.brand_safety(c, u['org_id'], 'pizza night', 'x')[0] == 'REVIEW'
    other = make_user('o@o.io', org='O')
    with main.db() as c: assert scoring.brand_safety(c, other['org_id'], 'pizza night', 'x')[0] == 'SAFE'       # rules are per organization


def _candidate(**k):
    base = {'scene_id': 1, 'frame_second': 0.0, 'quad': [[.1, .1], [.4, .1], [.4, .4], [.1, .4]], 'orientation': 'vertical', 'method': 'lines',
            'bbox': [.1, .1, .3, .3], 'area_fraction': .09, 'texture': .6, 'detector_confidence': .8, 'stability': .9, 'planarity': .98,
            'jitter': .002, 'occlusion': 0.0, 'object_overlap': 0.0, 'overlapping_objects': [], 'identified_as': None, 'frontality': .95,
            'start_second': 0.0, 'end_second': 4.0, 'visible_seconds': 4.0, 'scene_motion': 1.0}
    return dict(base, **k)


def _scene(meta, description=''):
    return {'id': 1, 'description': description, 'metadata_json': json.dumps(meta)}


MEASURED = {'camera': {'status': 'MEASURED', 'movement': 'static', 'shake': .001}, 'objects_detected': True,
            'lighting': {'status': 'MEASURED', 'exposure': 'normal', 'colour_cast': 'neutral', 'level_change': .01, 'stable': True},
            'context': {'status': 'MEASURED', 'setting': 'kitchen', 'strength': .5, 'evidence': {'microwave': .8, 'cup': .7}},
            'objects': {'microwave': {}, 'cup': {}}, 'safety_signals': []}


def test_scoring_v2_is_reproducible_explained_and_leaves_v1_unchanged(manual):
    u = make_user('pub@p.io', org='P')
    video = {'id': 0, 'org_id': u['org_id'], 'title': 'Morning', 'license_confirmed': 1, 'license_expires': None}
    with main.db() as c:
        v2 = scoring.by_version(c, 'v2'); v1 = scoring.by_version(c, 'v1')
        assert scoring.current(c)['version'] == 'v2' and abs(sum(v2['weights'].values()) - 1) < 1e-9
        a = scoring.build(c, v2, _candidate(), _scene(MEASURED), video)
        assert a == scoring.build(c, v2, _candidate(), _scene(MEASURED), video)                     # reproducible
        opp, br = a
        assert all(b['status'] == 'MEASURED' and b['evidence'] for k, b in br.items() if k != 'brand_safety')
        no_context = scoring.build(c, v2, _candidate(), _scene(dict(MEASURED, context={'status': 'UNDETERMINED'})), video)[0]
        assert 'kitchen' in br['contextual_relevance']['evidence']
        assert opp['product_compatibility']['food'] == round(no_context['product_compatibility']['food'] + .1, 3)       # a kitchen favours food
        assert opp['product_compatibility']['fashion'] == no_context['product_compatibility']['fashion']
        hidden = scoring.build(c, v2, _candidate(occlusion=.4, overlapping_objects=['person'], object_overlap=.4), _scene(MEASURED), video)
        assert hidden[0]['feasibility_score'] < opp['feasibility_score'] and 'person' in hidden[1]['occlusion']['evidence']
        assert hidden[0]['rendering_complexity'] == 'HIGH'
        # v1 ignores the new evidence: an opportunity scored under v1 scores the same as it did before v2 existed
        old = scoring.build(c, v1, _candidate(), _scene(MEASURED), video)
        assert set(old[1]) == set(v1['weights']) and old == scoring._build_v1(c, v1, _candidate(), _scene(MEASURED), video)


def test_detected_objects_can_raise_brand_safety_but_never_lower_it(manual):
    u = make_user('pub@p.io', org='P')
    video = {'id': 0, 'org_id': u['org_id'], 'title': 'Dinner', 'license_confirmed': 1, 'license_expires': None}
    knife = dict(MEASURED, safety_signals=[{'object': 'knife', 'confidence': .7, 'note': 'a blade is visible'}], objects={'knife': {}})
    with main.db() as c:
        v2 = scoring.by_version(c, 'v2')
        opp, br = scoring.build(c, v2, _candidate(), _scene(knife, 'a quiet kitchen'), video)
        assert opp['brand_safety'] == 'REVIEW' and 'object:knife' in br['brand_safety']['evidence']
        assert scoring.build(c, v2, _candidate(), _scene(MEASURED), video)[0]['brand_safety'] == 'NOT_EVALUATED'   # objects alone never make it SAFE
        assert scoring.brand_safety(c, u['org_id'], '', 'x', ['gun'])[0] == 'UNSAFE'                               # rules see detected objects


# ---------------------------------------------------------------- the job
@pytest.fixture()
def room(manual, tmp_path):
    owner(manual, 'pub@pub.io', 'Pub Studio')
    meta = scene.generate('room', 1, tmp_path / 'clips')
    vid = upload(manual, meta['path'], 'Room', analyze=False)
    return manual, vid


@needs_media
@needs_model
def test_a_staged_analysis_records_every_stage_and_owns_its_outputs(room):
    c, vid = room
    a = c.post(f'/api/videos/{vid}/analyses').json()
    assert a['status'] == 'QUEUED' and a['created'] is True
    again = c.post(f'/api/videos/{vid}/analyses').json()
    assert again['id'] == a['id'] and again['created'] is False                       # no duplicate job
    assert _run_all() == 1
    a = c.get(f'/api/analyses/{a["id"]}').json()
    assert a['status'] == 'COMPLETED' and a['progress'] == 1
    assert list(a['stages']) == list(intelligence.STAGES)
    det = a['stages']['DETECTING_OBJECTS']
    assert det['status'] == 'DONE' and det['detail']['model'].startswith('yolox-tiny@') and det['detail']['frames'] == 8   # every 0.5 s of 4 s
    assert a['model_versions']['object_detection'] == det['detail']['model'] and a['model_versions']['object_detection_status']['licence'] == 'Apache-2.0'
    assert a['stages']['TRACKING_OBJECTS']['status'] == 'DONE'
    assert a['stages']['UNDERSTANDING_SCENES']['status'] == 'PARTIAL'                  # no vision model in tests
    assert a['outputs']['unavailable_stages'] == [] and a['outputs']['opportunities'] >= 1
    stored = TestDB().execute('SELECT label, confidence, model FROM detected_objects WHERE analysis_version_id=?', (a['id'],)).fetchall()
    assert all(r['confidence'] >= .35 and r['model'] == det['detail']['model'] for r in stored) and len(stored) == det['detail']['detections']
    scenes = c.get(f'/api/analyses/{a["id"]}/scenes').json()
    assert scenes and scenes[0]['boundary_source'] == 'HISTOGRAM_CUT' and {'motion', 'brightness', 'colourfulness'} <= set(scenes[0]['metadata'])
    meta = scenes[0]['metadata']
    # the room clip zooms in at 1.5% a second about the frame's corner while panning; at the frame centre the two nearly cancel
    assert meta['camera']['status'] == 'MEASURED' and meta['camera']['movement'] == 'zoom in' and abs(meta['camera']['zoom_per_second'] - .015) < .004
    assert meta['lighting']['exposure'] == 'normal' and meta['lighting']['stable']
    assert meta['context']['status'] == 'UNDETERMINED'                                  # a synthetic room has no recognisable objects
    placements = TestDB().execute('SELECT stages_json FROM analysis_versions WHERE id=?', (a['id'],)).fetchone()['stages_json']
    cands = json.loads(placements)['DETECTING_PLACEMENTS']['detail']['candidates']
    assert cands and all(x['planarity'] > .9 and x['stopped'] == {'before': 'scene_end', 'after': 'scene_end'} for x in cands)
    frame = scenes[0]['frames'][0]
    assert f'a{a["id"]}' in TestDB().execute('SELECT image_path FROM scene_frames WHERE id=?', (frame['id'],)).fetchone()['image_path']   # owned by this version
    assert c.get(frame['image_url']).headers['content-type'] == 'image/jpeg'
    opp = c.get(f'/api/opportunities?video_id={vid}').json()[0]
    assert opp['validation_status'] == 'NEEDS_REVIEW' and opp['brand_safety'] == 'NOT_EVALUATED'
    assert 'not a validated prediction' in opp['explanation']
    detail = c.get(f'/api/opportunities/{opp["id"]}').json()
    assert detail['inventory']['status'] == 'DRAFT' and 'scoring v2' in detail['explanation']
    br = detail['score_breakdown']
    assert {'perspective', 'occlusion', 'lighting', 'camera_motion'} <= set(br) and all(b['evidence'] for b in br.values())
    assert br['brand_safety']['status'] == 'UNAVAILABLE'                                 # no description: not evaluated, not guessed
    assert c.get(f'/api/videos/{vid}/scenes').json()                                    # the legacy view shows the new analysis
    # a second analysis is a new version; the first is untouched
    first_scenes = [dict(r) for r in TestDB().execute('SELECT * FROM scenes WHERE analysis_version_id=?', (a['id'],)).fetchall()]
    b = c.post(f'/api/videos/{vid}/analyses').json(); _run_all()
    assert b['version'] == a['version'] + 1
    assert [dict(r) for r in TestDB().execute('SELECT * FROM scenes WHERE analysis_version_id=?', (a['id'],)).fetchall()] == first_scenes
    types = [r['event_type'] for r in TestDB().execute("SELECT event_type FROM outbox_events WHERE aggregate_id=? ORDER BY id", (str(vid),)).fetchall()]
    assert types.count('analysis.completed') == 2


@needs_media
def test_without_a_detection_model_the_stages_say_so_and_nothing_is_invented(room, monkeypatch):
    monkeypatch.setenv('CC_DETECTION_MODEL', 'none')
    c, vid = room
    a = c.post(f'/api/videos/{vid}/analyses').json(); _run_all()
    a = c.get(f'/api/analyses/{a["id"]}').json()
    assert a['status'] == 'COMPLETED' and a['model_versions']['object_detection'] is None
    assert a['stages']['DETECTING_OBJECTS']['status'] == 'UNAVAILABLE' and 'switched off' in a['stages']['DETECTING_OBJECTS']['detail']['reason']
    assert a['stages']['TRACKING_OBJECTS']['status'] == 'UNAVAILABLE' and 'needs object detection' in a['stages']['TRACKING_OBJECTS']['detail']['reason']
    assert a['outputs']['unavailable_stages'] == ['DETECTING_OBJECTS', 'TRACKING_OBJECTS'] and a['outputs']['object_tracks'] == 0
    assert TestDB().execute('SELECT COUNT(*) FROM detected_objects').fetchone()[0] == 0
    scene = c.get(f'/api/analyses/{a["id"]}/scenes').json()[0]
    assert scene['metadata']['context'] == {'status': 'UNAVAILABLE', 'reason': 'object detection did not run'}
    opp = c.get(f'/api/opportunities?video_id={vid}').json()[0]                              # surfaces do not need object detection
    assert c.get(f'/api/opportunities/{opp["id"]}').json()['score_breakdown']['contextual_relevance']['status'] == 'UNAVAILABLE'


@needs_media
def test_a_long_detection_stage_renews_its_lease_and_can_be_cancelled(room, monkeypatch):
    from app import detection
    c, vid = room
    monkeypatch.setenv('CC_ANALYSIS_DETECTION_STEP_SECONDS', '0.1')                      # 40 frames in the 4 s clip
    a = c.post(f'/api/videos/{vid}/analyses').json()
    calls = []

    class Slow:
        version, spec = 'fake@0', {'licence': 'test'}
        def detect(self, frame):
            calls.append(1)
            if len(calls) == 10:
                with main.db() as con: con.execute('UPDATE analysis_versions SET cancel_requested=1 WHERE id=?', (a['id'],))
            return []
    monkeypatch.setattr(detection, 'load', lambda name=None: Slow())
    _run_all()
    row = TestDB().execute('SELECT status, stage FROM analysis_versions WHERE id=?', (a['id'],)).fetchone()
    assert row['status'] == 'CANCELLED' and row['stage'] == 'DETECTING_OBJECTS'
    assert len(calls) == 20                                                               # stopped at the next lease renewal


@needs_media
@pytest.mark.parametrize('kind, seed, cuts', [('mild_lighting', 1, 0), ('lighting_change', 1, 0), ('cut_inside', 1, 1), ('bare', 2, 0), ('bare', 3, 0)])
def test_a_lighting_drift_is_not_a_cut_but_a_real_cut_is(manual, tmp_path, kind, seed, cuts):
    """A cut needs the histogram to change both as recorded and gain-normalised (cut_gain_normalise). The bare clips are
    plain walls, where normalising alone produced false cuts: measured in docs/qa/AI_EVALUATION_REPORT.md."""
    owner(manual, 'pub@pub.io', 'Pub Studio')
    clip = scene.generate(kind, seed, tmp_path / 'clips') if kind == 'bare' else generate(kind, seed, tmp_path / 'clips')
    vid = upload(manual, clip['path'], kind, analyze=False)
    a = manual.post(f'/api/videos/{vid}/analyses').json(); _run_all()
    a = manual.get(f'/api/analyses/{a["id"]}').json()
    assert a['status'] == 'COMPLETED' and a['config']['cut_gain_normalise'] == 1
    assert a['stages']['SEGMENTING']['detail']['cuts'] == cuts


@needs_media
def test_an_analysis_resumes_after_running_out_of_time(room):
    c, vid = room
    a = c.post(f'/api/videos/{vid}/analyses').json()
    stop = threading.Event()
    real = intelligence.RUNNERS['SEGMENTING']
    def segment_then_stop(r):
        out = real(r); stop.set(); return out
    intelligence.RUNNERS['SEGMENTING'] = segment_then_stop
    try:
        assert intelligence.run_next(stop=stop) is True
    finally:
        intelligence.RUNNERS['SEGMENTING'] = real
    row = TestDB().execute('SELECT status, attempts, stages_json FROM analysis_versions WHERE id=?', (a['id'],)).fetchone()
    assert row['status'] == 'QUEUED' and row['attempts'] == 0 and json.loads(row['stages_json'])['SEGMENTING']['status'] == 'DONE'
    calls = []
    intelligence.RUNNERS['SEGMENTING'] = lambda r: calls.append(1) or real(r)
    try: _run_all()
    finally: intelligence.RUNNERS['SEGMENTING'] = real
    assert calls == [] and c.get(f'/api/analyses/{a["id"]}').json()['status'] == 'COMPLETED'          # the finished stage was not repeated


@needs_media
def test_failures_retry_then_fail_with_the_error(room, monkeypatch):
    c, vid = room
    a = c.post(f'/api/videos/{vid}/analyses').json()
    monkeypatch.setitem(intelligence.RUNNERS, 'EXTRACTING_FRAMES', lambda r: (_ for _ in ()).throw(RuntimeError('disk full')))
    assert intelligence.run_next() is True
    assert TestDB().execute('SELECT status FROM analysis_versions WHERE id=?', (a['id'],)).fetchone()['status'] == 'QUEUED'   # one retry
    _run_all()
    a = c.get(f'/api/analyses/{a["id"]}').json()
    assert a['status'] == 'FAILED' and a['error'] == {'stage': 'EXTRACTING_FRAMES', 'type': 'RuntimeError', 'message': 'disk full'}
    assert a['stages']['EXTRACTING_FRAMES']['status'] == 'FAILED' and a['stages']['SEGMENTING']['status'] == 'DONE'


@needs_media
def test_a_queued_analysis_can_be_cancelled(room):
    c, vid = room
    a = c.post(f'/api/videos/{vid}/analyses').json()
    assert c.post(f'/api/analyses/{a["id"]}/cancel').json()['status'] == 'CANCELLED'
    assert _run_all() == 0 and c.post(f'/api/analyses/{a["id"]}/cancel').status_code == 409
    assert c.post(f'/api/videos/{vid}/analyses').json()['created'] is True                      # a new one may start


@needs_media
def test_validation_refuses_a_deleted_or_unlicensed_video(manual, tmp_path):
    owner(manual)
    vid = upload(manual, generate('static', 3, tmp_path)['path'], 'S', analyze=False)
    a = manual.post(f'/api/videos/{vid}/analyses').json()
    with main.db() as c: c.execute("UPDATE videos SET license_expires='2000-01-01' WHERE id=?", (vid,))
    _run_all()
    a = manual.get(f'/api/analyses/{a["id"]}').json()
    assert a['status'] == 'FAILED' and 'licence' in a['error']['message'] and a['attempts'] == 1          # not retried: it would fail again
    assert manual.post(f'/api/videos/{vid}/analyses').json()['detail']['code'] == 'LICENCE_INVALID'


def test_analyses_are_private_to_their_organization(room):
    c, vid = room
    a = c.post(f'/api/videos/{vid}/analyses').json()
    owner(c, 'other@o.io', 'Other')
    assert c.get(f'/api/analyses/{a["id"]}').status_code == 404 and c.get(f'/api/videos/{vid}/analyses').status_code == 404
    assert c.post(f'/api/analyses/{a["id"]}/cancel').status_code == 404
