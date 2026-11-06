"""M14 object tracking (app/objtrack.py), on detection sequences with known identities. No video is needed."""
import numpy as np
import pytest

from app import objtrack


def _det(label, x, y, w=40, h=80, conf=.8):
    return {'label': label, 'confidence': conf, 'bbox': [x, y, x + w, y + h]}


def test_two_people_crossing_keep_their_identities():
    frames = []
    for k in range(12):
        t = k * .5
        frames.append((t, [_det('person', 20 + 30 * k, 100), _det('person', 380 - 30 * k, 110)]))
    tracks = objtrack.track(frames)
    assert len(tracks) == 2
    a, b = tracks
    assert [o['bbox'][0] for o in a['observations']] == [20 + 30 * k for k in range(12)]       # never swapped
    assert [o['bbox'][0] for o in b['observations']] == [380 - 30 * k for k in range(12)]


def test_a_short_occlusion_keeps_the_track_and_is_reported_not_filled_in():
    frames = [(k * .5, [] if k in (4, 5) else [_det('car', 10 + 15 * k, 50, 60, 30)]) for k in range(10)]
    (tr,) = objtrack.track(frames)
    assert len(tr['observations']) == 8                                        # the two hidden samples are not invented
    assert tr['gaps'] == [[1.5, 3.0]]
    assert tr['observed_fraction'] == pytest.approx(.8) and tr['confidence'] == pytest.approx(.8 * .8)


def test_a_longer_occlusion_is_recovered_by_appearance_and_only_by_appearance():
    frames = [(k * .5, [] if 3 <= k <= 7 else [_det('dog', 100 + 5 * k, 60)]) for k in range(11)]
    red, blue = np.array([1.0, 0, 0, 0]), np.array([0, 0, 0, 1.0])
    same = objtrack.track(frames, appearance=lambda t, b: red)
    assert len(same) == 1 and same[0]['gaps'] == [[1.0, 4.0]]
    different = objtrack.track(frames, appearance=lambda t, b: red if t < 2 else blue)
    assert len(different) == 2                                                  # a different-looking dog is a new identity
    assert len(objtrack.track(frames)) == 2                                     # and without appearance it is not guessed


def test_classes_never_mix_and_single_sightings_are_discarded():
    frames = [(0.0, [_det('cup', 10, 10, 20, 20)]), (0.5, [_det('bowl', 10, 10, 20, 20)]), (1.0, [_det('bowl', 11, 10, 20, 20)])]
    tracks = objtrack.track(frames)
    assert [t['label'] for t in tracks] == ['bowl']                             # the one-frame cup is unconfirmed


def test_a_moving_object_is_followed_by_its_velocity():
    # 50 px per sample with a 40 px box: consecutive boxes do not overlap, so only the prediction can associate them.
    frames = [(k * .5, [_det('bicycle', 50 * k, 200, 40, 40)]) for k in range(8)]
    tracks = objtrack.track(frames, {'min_iou': 0.3})
    assert len(tracks) <= 2 and max(len(t['observations']) for t in tracks) >= 6
