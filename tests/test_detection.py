"""M14 object detection: the COCO evaluator on cases with known answers, model loading and its refusals, real inference,
and the model fetcher's checksum check."""
import hashlib

import numpy as np
import pytest

from app import detection
from benchmark.coco_eval import evaluate
from helpers import needs_model


# ---------------------------------------------------------------- the evaluator
def test_perfect_detections_score_one_and_none_score_zero():
    gt = {'a': [('cat', [0, 0, 10, 10], False), ('dog', [20, 20, 40, 40], False)], 'b': [('cat', [5, 5, 15, 15], False)]}
    perfect = {k: [(c, b, .9) for c, b, _ in v] for k, v in gt.items()}
    assert evaluate(gt, perfect)['ap'] == 1.0
    assert evaluate(gt, {})['ap'] == 0.0


def test_a_lower_scored_false_positive_costs_nothing_but_a_higher_one_does():
    gt = {'a': [('cat', [0, 0, 10, 10], False)]}
    assert evaluate(gt, {'a': [('cat', [0, 0, 10, 10], .9), ('cat', [50, 50, 60, 60], .1)]})['ap'] == 1.0
    assert evaluate(gt, {'a': [('cat', [0, 0, 10, 10], .1), ('cat', [50, 50, 60, 60], .9)]})['ap50'] == pytest.approx(.5, abs=.01)


def test_a_detection_on_a_crowd_region_is_ignored_and_a_loose_box_fails_strict_thresholds():
    gt = {'a': [('person', [0, 0, 10, 10], False), ('person', [30, 0, 90, 60], True)]}
    assert evaluate(gt, {'a': [('person', [0, 0, 10, 10], .9), ('person', [40, 10, 50, 20], .95)]})['ap'] == 1.0
    loose = evaluate(gt, {'a': [('person', [0, 0, 10, 12.5], .9)]})       # IoU 0.8: passes 0.50 to 0.80, fails above
    assert loose['ap50'] == 1.0 and loose['ap'] == pytest.approx(.7, abs=.01)


# ---------------------------------------------------------------- loading
def test_detection_refuses_rather_than_guesses(tmp_path, monkeypatch):
    monkeypatch.setenv('CC_MODELS_DIR', str(tmp_path))
    with pytest.raises(detection.Unavailable, match='switched off'): detection.load('none')
    with pytest.raises(detection.Unavailable, match='unknown detection model'): detection.load('yolov8n')
    with pytest.raises(detection.Unavailable, match='not installed'): detection.load('yolox-nano')
    (tmp_path / detection.MODELS['yolox-nano']['file']).write_bytes(b'not the model')
    with pytest.raises(detection.Unavailable, match='does not match its published checksum'): detection.load('yolox-nano')
    s = detection.status()
    assert s['status'] == 'UNAVAILABLE' and s['model'] == detection.DEFAULT_MODEL


def test_health_reports_whether_the_model_is_installed(tmp_path, monkeypatch, manual):
    monkeypatch.setenv('CC_MODELS_DIR', str(tmp_path))
    assert manual.get('/api/health').json()['object_detection'] == {'model': detection.DEFAULT_MODEL, 'installed': False}
    (tmp_path / detection.MODELS[detection.DEFAULT_MODEL]['file']).write_bytes(b'x')
    assert detection.installed()['installed'] is True                 # presence only; the checksum is checked when it loads
    monkeypatch.setenv('CC_DETECTION_MODEL', 'none')
    assert detection.installed() == {'model': None, 'installed': False, 'switched_off': True}


def test_only_commercially_licensed_models_are_listed():
    assert {m['licence'] for m in detection.MODELS.values()} == {'Apache-2.0'}
    assert detection.DEFAULT_MODEL in detection.MODELS
    assert all(len(m['sha256']) == 64 and m['url'].startswith('https://github.com/') for m in detection.MODELS.values())


def _stop_sign():
    import cv2
    img = np.full((416, 640, 3), (170, 190, 200), np.uint8)
    cv2.rectangle(img, (0, 300), (640, 416), (90, 110, 120), -1)
    c, r = (320, 170), 95
    pts = np.array([[c[0] + r * np.cos(np.pi / 8 + k * np.pi / 4), c[1] + r * np.sin(np.pi / 8 + k * np.pi / 4)] for k in range(8)], np.int32)
    cv2.fillConvexPoly(img, pts, (30, 30, 200)); cv2.polylines(img, [pts], True, (250, 250, 250), 6)
    cv2.putText(img, 'STOP', (c[0] - 78, c[1] + 22), cv2.FONT_HERSHEY_DUPLEX, 2.0, (255, 255, 255), 6)
    cv2.rectangle(img, (312, 265), (328, 416), (120, 120, 120), -1)
    return img


@needs_model
def test_the_model_really_detects_and_finds_nothing_in_an_empty_frame():
    det = detection.load()
    found = det.detect(_stop_sign())
    assert found and found[0]['label'] == 'stop sign' and found[0]['confidence'] > .7
    x0, y0, x1, y1 = found[0]['bbox']
    assert 215 < x0 < 240 and 65 < y0 < 90 and 400 < x1 < 425 and 250 < y1 < 290        # the octagon spans 232-408 x 82-258
    assert det.detect(np.full((416, 640, 3), 128, np.uint8)) == []                      # nothing is invented
    assert det.detect(_stop_sign()) == found                                            # deterministic
    assert detection.status()['status'] == 'AVAILABLE' and detection.status()['licence'] == 'Apache-2.0'


@needs_model
def test_inference_does_not_change_the_rest_of_the_process_threads():
    import cv2
    before = cv2.getNumThreads()
    cv2.setNumThreads(3)
    try:
        detection.load().detect(_stop_sign())
        assert cv2.getNumThreads() == 3          # only the inference call runs on CC_DETECTION_THREADS
    finally:
        cv2.setNumThreads(before)


# ---------------------------------------------------------------- fetching
def test_the_fetcher_installs_only_a_file_with_the_pinned_checksum(tmp_path, monkeypatch):
    import io
    from ops import fetch_models
    payload = b'pretend model'
    good = dict(detection.MODELS['yolox-nano'], sha256=hashlib.sha256(payload).hexdigest())
    monkeypatch.setitem(fetch_models.MODELS, 'yolox-nano', good)

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(fetch_models.urllib.request, 'urlopen', lambda req, timeout: _Resp(payload))
    assert fetch_models.fetch('yolox-nano', tmp_path) == 'installed'
    assert fetch_models.fetch('yolox-nano', tmp_path) == 'present'
    monkeypatch.setattr(fetch_models.urllib.request, 'urlopen', lambda req, timeout: _Resp(b'tampered'))
    (tmp_path / good['file']).unlink()
    with pytest.raises(SystemExit, match='does not match'): fetch_models.fetch('yolox-nano', tmp_path)
    assert list(tmp_path.iterdir()) == []                                                # nothing left behind
