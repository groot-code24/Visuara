"""M14 scene understanding: camera motion, lighting, object context, relations and safety signals, and the vision-model
interface (validation, timeouts, retries, unavailable states). No model server is needed."""
import io, json, urllib.error

import cv2
import numpy as np
import pytest

from app import understanding as u, vision


# ---------------------------------------------------------------- camera
def _shifted(dx, dy=0, n=8, zoom=0.0):
    rng = np.random.default_rng(3)
    world = cv2.GaussianBlur((rng.random((400, 600)) * 255).astype(np.uint8), (0, 0), 1.5)
    out = []
    for k in range(n):
        s = 1 + zoom * k
        M = np.array([[s, 0, -dx * k + (1 - s) * 160], [0, s, -dy * k + (1 - s) * 90]], np.float32)
        out.append((k * .25, cv2.warpAffine(world, M, (320, 180), borderMode=cv2.BORDER_REFLECT)))
    return out


@pytest.mark.parametrize('dx, dy, zoom, expected', [(0, 0, 0, 'static'), (6, 0, 0, 'pan right'), (0, 5, 0, 'tilt down'), (0, 0, .02, 'zoom in')])
def test_camera_movement_is_measured(dx, dy, zoom, expected):
    cam = u.classify_camera(u.global_motion(_shifted(dx, dy, zoom=zoom)))
    assert cam['status'] == 'MEASURED' and cam['movement'] == expected, cam


def test_a_featureless_scene_does_not_get_a_camera_movement():
    flat = [(k * .25, np.full((180, 320), 128, np.uint8)) for k in range(5)]
    assert u.classify_camera(u.global_motion(flat))['status'] == 'UNAVAILABLE'


def test_lighting_is_measured_from_pixels():
    dark = [np.full((90, 160, 3), 25, np.uint8)] * 2
    warm = [np.dstack([np.full((90, 160), 90, np.uint8), np.full((90, 160), 130, np.uint8), np.full((90, 160), 190, np.uint8)])] * 2
    assert u.lighting(dark)['exposure'] == 'low light'
    li = u.lighting(warm)
    assert li['exposure'] == 'normal' and li['colour_cast'] == 'warm' and li['stable']
    drift = u.lighting([np.full((90, 160, 3), v, np.uint8) for v in (60, 120, 200)])
    assert not drift['stable'] and drift['level_change'] > .3


# ---------------------------------------------------------------- objects
def _track(label, conf, box=(0, 0, 10, 10), tid=1, t=1.0):
    return {'track_id': tid, 'label': label, 'confidence': conf, 'observations': [{'t': t, 'bbox': list(box), 'confidence': conf}]}


def test_a_setting_needs_supporting_objects():
    assert u.context([])['status'] == 'UNDETERMINED'
    assert u.context([_track('cup', .9)])['status'] == 'UNDETERMINED'                       # one weak clue is not a kitchen
    k = u.context([_track('cup', .8), _track('microwave', .7, tid=2), _track('bowl', .6, tid=3)])
    assert k['status'] == 'MEASURED' and k['setting'] == 'kitchen' and set(k['evidence']) == {'cup', 'microwave', 'bowl'}
    assert u.context([_track('bed', .8)])['setting'] == 'bedroom'                            # a decisive object alone
    assert u.context([_track('bed', .4)])['status'] == 'UNDETERMINED'                        # unless it is uncertain


def test_relations_come_from_box_geometry():
    tracks = [_track('person', .9, (100, 50, 200, 350), 1), _track('cell phone', .7, (150, 150, 170, 190), 2),
              _track('dining table', .8, (300, 250, 600, 400), 3), _track('cup', .8, (400, 215, 430, 255), 4)]
    rel = {(r['subject'], r['relation'], r['object']) for r in u.relations(tracks, 1.0)}
    assert ('cell phone', 'held by', 'person') in rel and ('cup', 'on', 'dining table') in rel
    assert not any(r[0] == 'person' and r[1] == 'on' for r in rel)


def test_safety_signals_are_signals_not_verdicts():
    sig = u.safety_signals([_track('knife', .6), _track('cup', .9, tid=2), _track('wine glass', .3, tid=3)])
    assert [s['object'] for s in sig] == ['knife']                                          # the uncertain wine glass is left out


# ---------------------------------------------------------------- vision model interface
def test_without_a_model_vision_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.delenv('CC_VISION_MODEL', raising=False)
    r = vision.describe(tmp_path / 'x.jpg')
    assert r.status == 'UNAVAILABLE' and 'CC_VISION_MODEL' in r.reason and r.description is None


def test_model_answers_are_validated_not_trusted():
    ok = vision.parse(json.dumps({'description': 'A kitchen counter with a kettle.', 'scene_class': 'Kitchen', 'lighting': 'daylight',
                                  'objects': ['kettle', 'mug', 3], 'placement_areas': ['counter left of the kettle'],
                                  'sensitive_content': [], 'extra': 'ignored'}), 'm')
    assert ok.status == 'AVAILABLE' and ok.scene_class == 'kitchen' and ok.objects == ['kettle', 'mug'] and ok.lighting == 'daylight'
    assert vision.parse(json.dumps({'description': 'x', 'scene_class': 'castle'}), 'm').status == 'FAILED'      # too short to be a description
    odd = vision.parse(json.dumps({'description': 'A castle courtyard.', 'scene_class': 'castle', 'lighting': 'moody'}), 'm')
    assert odd.status == 'AVAILABLE' and odd.scene_class is None and odd.lighting is None                      # unknown values dropped
    assert vision.parse('the scene shows a kitchen', 'm').status == 'FAILED'
    assert vision.parse('[1, 2]', 'm').status == 'FAILED'


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture()
def frame(tmp_path, monkeypatch):
    monkeypatch.setenv('CC_VISION_MODEL', 'test-model'); monkeypatch.setenv('CC_VISION_RETRIES', '1')
    monkeypatch.setattr(vision.time, 'sleep', lambda s: None)
    p = tmp_path / 'f.jpg'; cv2.imwrite(str(p), np.full((1080, 1920, 3), 120, np.uint8))
    return p


def test_a_valid_answer_is_used_and_the_image_is_scaled_down(frame):
    seen = {}
    def opener(req, timeout):
        body = json.loads(req.data); seen.update(body, timeout=timeout)
        return _Resp(json.dumps({'response': json.dumps({'description': 'A grey wall.', 'scene_class': 'other'})}).encode())
    r = vision.describe(frame, opener=opener)
    assert r.status == 'AVAILABLE' and r.description == 'A grey wall.' and r.attempts == 1
    img = cv2.imdecode(np.frombuffer(__import__('base64').b64decode(seen['images'][0]), np.uint8), cv2.IMREAD_COLOR)
    assert max(img.shape[:2]) == 768 and seen['format'] == 'json' and seen['timeout'] == 20.0


def test_timeouts_are_retried_then_reported(frame):
    calls = []
    def opener(req, timeout):
        calls.append(1); raise TimeoutError('timed out')
    r = vision.describe(frame, opener=opener)
    assert r.status == 'UNAVAILABLE' and 'timed out' in r.reason and len(calls) == 2 and r.description is None


def test_a_refusal_is_not_retried_and_garbage_is_not_a_description(frame):
    calls = []
    def refuse(req, timeout):
        calls.append(1); raise urllib.error.HTTPError(req.full_url, 404, 'model not found', {}, None)
    assert vision.describe(frame, opener=refuse).status == 'UNAVAILABLE' and len(calls) == 1
    assert vision.describe(frame, opener=lambda req, timeout: _Resp(b'<html>')).status == 'FAILED'
    assert vision.describe(frame, opener=lambda req, timeout: _Resp(json.dumps({'response': 'I think a kitchen'}).encode())).status == 'FAILED'
