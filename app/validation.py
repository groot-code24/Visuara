"""Validate an actual rendered file against its source and the placement it was rendered for.

Checks container facts (decode, duration, frame rate, resolution, audio) and frame content:
every frame inside the placement interval must match the expected composite inside the product
region, and every checked frame must preserve the source scene outside it.
"""
import cv2
import numpy as np

from .compositor import Planar2DPlan
from .media import probe_info, frame_ranges, MediaError

VALIDATION_POLICY = {
    'duration_tolerance_s': 0.15,
    'fps_tolerance_ratio': 0.005,
    'frame_count_tolerance': 1,
    # Luma PSNR: 4:2:0 chroma subsampling lowers colour PSNR on saturated product edges without any
    # placement error. Measured on the dev split: correct renders 33.7-37.8 dB, product missing ~11 dB,
    # product shifted by 3 px ~10-11 dB.
    'min_product_psnr_db': 28.0,   # output vs expected composite inside the product region
    'min_scene_psnr_db': 28.0,     # output vs source outside the product region (scene preservation)
    'outside_interval_stride': 5,  # frames outside the interval are sampled at this stride
}


def _luma(frame):
    return cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)[:, :, 0]


def _psnr(a, b, mask=None):
    d = (_luma(a).astype(np.float64) - _luma(b).astype(np.float64)) ** 2
    if mask is not None:
        m = mask > 0
        if not m.any():
            return None
        mse = d[m].mean()
    else:
        mse = d.mean()
    return 99.0 if mse < 1e-10 else float(10 * np.log10(255.0 ** 2 / mse))


def validate_render(source, output, product_path, frame_quads, policy=VALIDATION_POLICY, progress=None):
    """Backward-compatible wrapper for a plain planar decal plan."""
    return validate_plan(source, output, Planar2DPlan(product_path, frame_quads), policy, progress)


def validate_plan(source, output, plan, policy=VALIDATION_POLICY, progress=None):
    p = policy
    failures = []
    try:
        src, out = probe_info(source), probe_info(output)
    except MediaError as e:
        return {'status': 'FAILED', 'failures': [{'code': 'OUTPUT_UNDECODABLE', 'message': str(e)[:300]}], 'policy': p}
    container = {'source': src, 'output': out}
    if abs(src['duration'] - out['duration']) > p['duration_tolerance_s']:
        failures.append({'code': 'DURATION_MISMATCH', 'observed': out['duration'], 'expected': src['duration']})
    if (src['width'], src['height']) != (out['width'], out['height']):
        failures.append({'code': 'RESOLUTION_MISMATCH', 'observed': [out['width'], out['height']], 'expected': [src['width'], src['height']]})
    if abs(src['fps'] - out['fps']) > src['fps'] * p['fps_tolerance_ratio']:
        failures.append({'code': 'FRAME_RATE_MISMATCH', 'observed': out['fps'], 'expected': src['fps']})
    if src['has_audio'] and not out['has_audio']:
        failures.append({'code': 'AUDIO_MISSING', 'message': 'Source audio was not preserved.'})
    if failures:
        return {'status': 'FAILED', 'failures': failures, 'container': container, 'policy': p}

    a, b = cv2.VideoCapture(str(source)), cv2.VideoCapture(str(output))
    idx = 0; checked = 0; interval_checked = 0
    bad_product, bad_scene, missing_product = [], [], []
    product_psnr, scene_psnr = [], []
    areas = []
    try:
        while True:
            if not a.grab() or not b.grab():
                break
            inside = idx in plan.indices
            if inside or idx % p['outside_interval_stride'] == 0:
                ok_a, fa = a.retrieve(); ok_b, fb = b.retrieve()
                if not ok_a or not ok_b:
                    break
                checked += 1
                if inside:
                    interval_checked += 1
                    expected = plan.apply(idx, fa)
                    m = plan.mask(idx, fa.shape)
                    areas.append(float((m > 0).mean()) if m is not None else 0.0)
                    pp = _psnr(fb, expected, m)
                    if pp is None:
                        missing_product.append(idx)
                    else:
                        product_psnr.append(pp)
                        if pp < p['min_product_psnr_db']:
                            bad_product.append(idx)
                    sp = _psnr(fb, fa, cv2.bitwise_not(cv2.dilate(m, np.ones((5, 5), np.uint8))))
                else:
                    sp = _psnr(fb, fa)
                if sp is not None:
                    scene_psnr.append(sp)
                    if sp < p['min_scene_psnr_db']:
                        bad_scene.append(idx)
            idx += 1
            if progress and idx % 15 == 0:
                progress(idx)
        rest_a = int(a.get(cv2.CAP_PROP_FRAME_COUNT)); rest_b = int(b.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        a.release(); b.release()
    fps = src['fps']
    expected_interval = len(plan.indices)
    if abs(rest_a - rest_b) > p['frame_count_tolerance']:
        failures.append({'code': 'FRAME_COUNT_MISMATCH', 'observed': rest_b, 'expected': rest_a})
    if interval_checked != expected_interval:
        failures.append({'code': 'INTERVAL_NOT_FULLY_VALIDATED', 'observed': interval_checked, 'expected': expected_interval})
    if bad_product:
        failures.append({'code': 'PRODUCT_MISMATCH', 'message': 'Rendered product region differs from the expected composite.',
                         'timestamps': frame_ranges(bad_product, fps), 'observed_min_db': round(min(product_psnr), 2), 'threshold_db': p['min_product_psnr_db']})
    if missing_product:
        failures.append({'code': 'PRODUCT_NOT_VISIBLE', 'timestamps': frame_ranges(missing_product, fps)})
    if bad_scene:
        failures.append({'code': 'SCENE_NOT_PRESERVED', 'message': 'Pixels outside the product region changed beyond encoding tolerance.',
                         'timestamps': frame_ranges(bad_scene, fps), 'observed_min_db': round(min(scene_psnr), 2), 'threshold_db': p['min_scene_psnr_db']})
    return {
        'status': 'FAILED' if failures else 'PASSED', 'failures': failures, 'container': container, 'policy': p,
        'frames_decoded': idx, 'frames_checked': checked, 'interval_frames_checked': interval_checked,
        'interval_frames_expected': expected_interval,
        # On-screen exposure in the delivered asset (not audience exposure): frames where the product covers >= 0.1% of the frame.
        'exposure': {'frames': sum(1 for a in areas if a >= .001), 'seconds': round(sum(1 for a in areas if a >= .001) / fps, 3),
                     'mean_area_pct': round(100 * float(np.mean(areas)), 3) if areas else 0.0},
        'product_psnr_db': None if not product_psnr else {'min': round(min(product_psnr), 2), 'median': round(float(np.median(product_psnr)), 2)},
        'scene_psnr_db': None if not scene_psnr else {'min': round(min(scene_psnr), 2), 'median': round(float(np.median(scene_psnr)), 2)},
    }
