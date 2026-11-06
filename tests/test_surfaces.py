"""M14 surface detection (straight-edge quadrilaterals) and plane following by homography, against the fixtures' per-frame
ground truth. Synthetic footage: these check the method does what it claims, not accuracy on real video."""
import numpy as np
import pytest
import cv2

from app import surfaces
from benchmark import fixtures, fixtures_scene
from benchmark.evaluate_intelligence import iou
from helpers import needs_media


def test_collinear_pieces_are_merged_and_others_are_not():
    pieces = [[0, 0, 40, 0], [45, 0.5, 90, 1], [100, 1, 140, 1], [0, 30, 40, 30], [0, 0, 0, 40]]
    merged = surfaces.merge_segments(pieces)
    lengths = sorted(round(float(np.hypot(s[2] - s[0], s[3] - s[1]))) for s in merged)
    assert lengths == [40, 40, 140]                     # the three pieces of one line become one; a parallel line and a crossing one stay
    assert surfaces.merge_segments([[0, 0, 40, 0], [70, 0, 110, 0]]) and len(surfaces.merge_segments([[0, 0, 40, 0], [70, 0, 110, 0]])) == 2   # too far apart


def _ref_frame(meta):
    cap = cv2.VideoCapture(meta['path']); cap.set(cv2.CAP_PROP_POS_MSEC, meta['reference_second'] * 1000); ok, frame = cap.read(); cap.release()
    assert ok
    return frame


def _gt_box(meta):
    q = np.asarray(meta['quad_norm']) * [meta['width'], meta['height']]
    return (*q.min(axis=0), *q.max(axis=0))


@needs_media
@pytest.mark.parametrize('kind', ['pan_zoom', 'perspective', 'sensor_noise'])
def test_a_textured_poster_is_found_where_contours_fail(tmp_path, kind):
    from app.decisions import detect_surfaces
    meta = fixtures.generate(kind, 1, tmp_path)
    frame = _ref_frame(meta)
    assert max((iou(d['bbox'], _gt_box(meta)) for d in detect_surfaces(frame)), default=0) < .5   # the old detector misses it
    best = max(surfaces.line_quads(frame), key=lambda d: iou(d['bbox'], _gt_box(meta)))
    assert iou(best['bbox'], _gt_box(meta)) > .85 and best['support'] >= surfaces.MIN_SUPPORT and best['method'] == 'lines'


@needs_media
def test_nothing_is_invented_on_a_bare_wall(tmp_path):
    meta = fixtures_scene.generate('bare', 1, tmp_path)
    cap = cv2.VideoCapture(meta['path']); ok, frame = cap.read(); cap.release()
    assert ok and surfaces.detect(frame) == []


def _follow(meta):
    frame = _ref_frame(meta)
    best = max(surfaces.detect(frame), key=lambda d: iou(d['bbox'], _gt_box(meta)))
    assert iou(best['bbox'], _gt_box(meta)) > .5
    return surfaces.follow(meta['path'], 0.0, fixtures.SECONDS, meta['reference_second'], [best['quad']])[0]


def _corner_error(meta, run):
    errs = []
    for s in run['samples']:
        g = np.asarray(meta['gt_surface_quads'][min(int(round(s['t'] * meta['fps'])), len(meta['gt_surface_quads']) - 1)])
        errs.append(float(np.linalg.norm(np.asarray(s['quad']) * [meta['width'], meta['height']] - g, axis=1).max()))
    return errs


@needs_media
def test_a_surface_is_followed_through_the_scene_within_a_few_pixels(tmp_path):
    meta = fixtures.generate('pan_zoom', 1, tmp_path)
    run = _follow(meta)
    assert run['status'] == 'TRACKED' and run['start'] == 0.0 and run['end'] >= 3.1
    assert run['stopped'] == {'before': 'scene_end', 'after': 'scene_end'}
    assert run['planarity'] > .95 and run['stability'] > .8 and run['occlusion'] < .1
    errs = _corner_error(meta, run)
    assert np.median(errs) < 5 and max(errs) < 8                  # pixels in a 480 x 270 frame


@needs_media
@pytest.mark.parametrize('kind, seed, reason, before', [('cut_inside', 2, 'lost', 1.75), ('exits_frame', 2, 'left_frame', 2.3), ('occluder', 2, 'occluded', 1.8)])
def test_following_stops_at_a_cut_an_exit_or_an_occluder_and_never_extrapolates(tmp_path, kind, seed, reason, before):
    meta = fixtures.generate(kind, seed, tmp_path)
    run = _follow(meta)
    assert run['stopped']['after'] == reason and run['end'] <= before
    assert max(_corner_error(meta, run)) < 8                      # every reported frame is still on the surface
