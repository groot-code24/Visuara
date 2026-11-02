"""Placement quality evaluator: applies a versioned threshold policy to tracker measurements.

Thresholds are heuristic and were tuned only on the synthetic development split of
benchmark/. They are NOT calibrated probabilities,
so every PASSED result still requires human fit acceptance before proposal.
"""
import numpy as np

from .media import frame_ranges

POLICY_VERSION = 'planar-v0.1-heuristic'

POLICY = {
    'min_reference_features': 25,      # features detected on the surface in the reference frame
    'min_textured_cells': 6,           # of 16 rectified cells with usable texture
    'min_inliers': 12,                 # RANSAC inliers per frame
    'min_inlier_ratio': 0.5,
    'max_rmse_px': 2.0,                # inlier reprojection RMSE per frame (non-planarity / instability)
    'min_ncc': 0.70,                   # registered-surface photometric consistency (drift, deformation)
    'min_cell_ncc': 0.40,              # lowest cell NCC (localised occlusion)
    'occlusion_median_cell_ncc': 0.70, # if median cells stay above this while one cell fails -> occlusion
    'max_cut_score': 0.45,             # Bhattacharyya histogram distance between consecutive frames
    'min_product_visible': 0.85,       # fraction of the product quad inside the frame
    'min_sharpness_ratio': 0.30,       # renderer does not synthesise motion blur
    'luminance_ratio_range': [0.75, 1.33],  # renderer does not relight the product
    'area_ratio_range': [0.2, 5.0],    # surface scale change vs reference (degenerate homography guard)
    'min_product_side_px': 16,         # legibility floor
    'static_motion_px': 1.0,           # max corner motion for scene class A (static)
}

ACTIONS = {
    'LOW_TEXTURE': 'Choose a textured planar surface, or use a screen-space overlay placement.',
    'TRACKING_LOST': 'Shorten the interval, move the reference frame closer to the start, or pick a more stable surface.',
    'NON_PLANAR_OR_UNSTABLE': 'The surface does not move as a rigid plane. Pick a flat, rigid surface; non-planar insertion is unsupported.',
    'TRACKING_DRIFT': 'Correct the four corners on the reference frame or shorten the interval, then re-run the quality check.',
    'PARTIAL_OCCLUSION_UNSUPPORTED': 'Something passes in front of the surface. Occlusion-aware compositing is not available; choose an unobstructed interval.',
    'SCENE_CUT_IN_INTERVAL': 'Split the placement so the interval does not cross a scene cut.',
    'PRODUCT_OUT_OF_FRAME': 'Shorten the interval or choose a surface that stays in frame.',
    'MOTION_BLUR_UNSUPPORTED': 'The renderer does not synthesise motion blur; choose a steadier interval.',
    'LIGHTING_CHANGE_UNSUPPORTED': 'The renderer does not relight the product; choose an interval with stable lighting.',
    'DEGENERATE_GEOMETRY': 'Tracked geometry became implausible. Correct the corners or shorten the interval.',
    'PRODUCT_TOO_SMALL': 'Enlarge the surface or choose a closer shot so the product is legible.',
    'INCOMPLETE_VALIDATION': 'Some interval frames could not be measured; the placement cannot be approved.',
    'OVERLAY_OUT_OF_FRAME': 'Move or shrink the overlay so it is fully inside the frame.',
}


def _fail(code, message, frames, fps, metric=None, observed=None, threshold=None):
    return {'code': code, 'message': message, 'metric': metric, 'observed': observed, 'threshold': threshold,
            'timestamps': frame_ranges(frames, fps) if frames else [], 'frame_count': len(frames),
            'recommended_action': ACTIONS.get(code, 'Review the placement manually.')}


def _summ(values):
    v = [x for x in values if x is not None]
    if not v:
        return None
    return {'min': round(float(min(v)), 4), 'median': round(float(np.median(v)), 4), 'max': round(float(max(v)), 4)}


def evaluate_track(track, product_quads, policy=POLICY):
    """Decide PASSED/FAILED for a tracked planar placement. Every interval frame must pass."""
    fps = track['fps']; p = policy
    s, e = track['start_idx'], track['end_idx']
    by_idx = {f['idx']: f for f in track['frames']}
    span = [by_idx.get(i) for i in range(track['ref_idx'], e + 1)]   # tracking continuity from reference
    interval = [by_idx.get(i) for i in range(s, e + 1)]
    failures, warnings = [], []

    if track['ref_feature_count'] < p['min_reference_features'] or track['textured_cells'] < p['min_textured_cells']:
        failures.append(_fail('LOW_TEXTURE', 'The selected surface has too little visual texture to track or verify.',
                              [track['ref_idx']], fps, 'reference_features', track['ref_feature_count'], p['min_reference_features']))

    missing = [i for i, f in zip(range(track['ref_idx'], e + 1), span) if f is None]
    lost = [f['idx'] for f in span if f is not None and f['lost']]
    if lost:
        failures.append(_fail('TRACKING_LOST', 'The surface could not be tracked on these frames.', lost, fps, 'inliers', 0, p['min_inliers']))
    if missing:
        failures.append(_fail('INCOMPLETE_VALIDATION', 'Frames could not be decoded or measured.', missing, fps, 'frames_missing', len(missing), 0))

    ok = [f for f in span if f is not None and not f['lost']]
    cuts = [f['idx'] for f in ok if f['cut_score'] > p['max_cut_score']] + [f['idx'] for f in span if f is not None and f['lost'] and f['cut_score'] > p['max_cut_score']]
    if cuts:
        failures.append(_fail('SCENE_CUT_IN_INTERVAL', 'A scene cut occurs between the reference frame and the end of the placement.',
                              sorted(set(cuts)), fps, 'cut_score', round(max(by_idx[i]['cut_score'] for i in cuts), 3), p['max_cut_score']))

    weak = [f['idx'] for f in ok if f['inliers'] < p['min_inliers'] or f['inlier_ratio'] < p['min_inlier_ratio'] or (f['rmse'] or 0) > p['max_rmse_px']]
    if weak:
        failures.append(_fail('NON_PLANAR_OR_UNSTABLE', 'Tracked points do not fit a single rigid plane on these frames.', weak, fps,
                              'rmse_px', _summ([by_idx[i]['rmse'] for i in weak]), p['max_rmse_px']))

    occl, drift = [], []
    for f in ok:
        cells = [c for c in f['cell_ncc'] if c is not None]
        low_global = f['ncc'] is None or f['ncc'] < p['min_ncc']
        low_cell = bool(cells) and min(cells) < p['min_cell_ncc']
        if low_cell and np.median(cells) >= p['occlusion_median_cell_ncc']:
            occl.append(f['idx'])
        elif low_global or low_cell:
            drift.append(f['idx'])
    if occl:
        failures.append(_fail('PARTIAL_OCCLUSION_UNSUPPORTED', 'Part of the surface is covered on these frames.', occl, fps,
                              'min_cell_ncc', _summ([min(c for c in by_idx[i]['cell_ncc'] if c is not None) for i in occl]), p['min_cell_ncc']))
    if drift:
        failures.append(_fail('TRACKING_DRIFT', 'The registered surface no longer matches the reference appearance.', drift, fps,
                              'ncc', _summ([by_idx[i]['ncc'] for i in drift]), p['min_ncc']))

    geom = [f['idx'] for f in ok if not f.get('convex', True) or not (p['area_ratio_range'][0] <= f.get('area_ratio', 1) <= p['area_ratio_range'][1])]
    if geom:
        failures.append(_fail('DEGENERATE_GEOMETRY', 'Tracked surface geometry is implausible.', geom, fps,
                              'area_ratio', _summ([by_idx[i].get('area_ratio') for i in geom]), p['area_ratio_range']))

    in_ok = [f for f in interval if f is not None and not f['lost']]
    blur = [f['idx'] for f in in_ok if f['sharpness_ratio'] is not None and f['sharpness_ratio'] < p['min_sharpness_ratio']]
    if blur:
        failures.append(_fail('MOTION_BLUR_UNSUPPORTED', 'The surface is much blurrier than in the reference frame.', blur, fps,
                              'sharpness_ratio', _summ([by_idx[i]['sharpness_ratio'] for i in blur]), p['min_sharpness_ratio']))
    lo, hi = p['luminance_ratio_range']
    light = [f['idx'] for f in in_ok if f['luminance_ratio'] is not None and not lo <= f['luminance_ratio'] <= hi]
    if light:
        failures.append(_fail('LIGHTING_CHANGE_UNSUPPORTED', 'Surface brightness changes significantly during the interval.', light, fps,
                              'luminance_ratio', _summ([by_idx[i]['luminance_ratio'] for i in light]), p['luminance_ratio_range']))

    from .tracking import _visible_fraction
    vis, small, corners = [], [], []
    for i in range(s, e + 1):
        q = product_quads.get(i)
        if q is None:
            continue
        q = np.asarray(q)
        corners.append(q)
        if _visible_fraction(q, track['width'], track['height']) < p['min_product_visible']:
            vis.append(i)
        if min(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[3] - q[0])) < p['min_product_side_px']:
            small.append(i)
    if vis:
        failures.append(_fail('PRODUCT_OUT_OF_FRAME', 'The product would be partly outside the frame.', vis, fps, 'product_visible_fraction', None, p['min_product_visible']))
    if small:
        failures.append(_fail('PRODUCT_TOO_SMALL', 'The product would be too small to be legible.', small, fps, 'product_min_side_px', None, p['min_product_side_px']))

    motion = 0.0
    shake = None
    if corners:
        c = np.stack(corners)
        motion = float(np.max(np.linalg.norm(c - np.asarray(product_quads.get(s, corners[0])), axis=2)))
        if len(c) >= 5:
            k = np.ones(5) / 5
            smooth = np.stack([[np.convolve(c[:, j, d], k, mode='same') for d in range(2)] for j in range(4)]).transpose(2, 0, 1)
            shake = float(np.sqrt(((c[2:-2] - smooth[2:-2]) ** 2).sum(axis=2)).max()) if len(c) > 4 else None
    reseeds = sum(1 for f in ok if f.get('reseeded'))
    if reseeds:
        warnings.append({'code': 'TRACKER_REINITIALISED', 'message': f'Tracker re-seeded features on {reseeds} frame(s); verified by photometric checks.'})

    scene_class = 'A_STATIC_PLANAR' if motion <= p['static_motion_px'] else 'B_MOVING_PLANAR'
    codes = {f['code'] for f in failures}
    if 'PARTIAL_OCCLUSION_UNSUPPORTED' in codes:
        scene_class = 'C_PARTIAL_OCCLUSION'
    elif 'NON_PLANAR_OR_UNSTABLE' in codes:
        scene_class = 'D_NON_PLANAR_OR_UNSTABLE'
    validated = sum(1 for f in interval if f is not None and not f['lost'])
    status = 'PASSED' if not failures and validated == (e - s + 1) else 'FAILED'
    if status == 'FAILED' and not failures:
        failures.append(_fail('INCOMPLETE_VALIDATION', 'Not every interval frame was validated.', [], fps))
    return {
        'policy_version': POLICY_VERSION, 'thresholds_calibrated': False, 'mode': 'TRACKED_PLANAR',
        'status': status, 'scene_class': scene_class,
        'interval': {'start_frame': s, 'end_frame': e, 'reference_frame': track['ref_idx'], 'fps': fps},
        'frames_in_interval': e - s + 1, 'frames_validated': validated,
        'measurements': {
            'reference_features': track['ref_feature_count'], 'textured_cells': track['textured_cells'],
            'inliers': _summ([f['inliers'] for f in ok]), 'inlier_ratio': _summ([f['inlier_ratio'] for f in ok]),
            'rmse_px': _summ([f['rmse'] for f in ok]), 'ncc': _summ([f['ncc'] for f in ok]),
            'min_cell_ncc': _summ([min([c for c in f['cell_ncc'] if c is not None], default=None) for f in ok]),
            'cut_score': _summ([f['cut_score'] for f in span if f is not None]),
            'sharpness_ratio': _summ([f['sharpness_ratio'] for f in in_ok]), 'luminance_ratio': _summ([f['luminance_ratio'] for f in in_ok]),
            'max_product_motion_px': round(motion, 2), 'camera_shake_px': None if shake is None else round(shake, 3),
            'tracker_reseeds': reseeds,
        },
        'thresholds': dict(p), 'failures': failures, 'warnings': warnings,
        'recommended_action': failures[0]['recommended_action'] if failures else 'Quality checks passed. A person must still confirm the product fits the scene.',
    }


def evaluate_overlay(quad, width, height, fps, start_idx, end_idx, policy=POLICY):
    """Tier A screen-space overlay. Not scene-anchored, so only frame-bounds and legibility are checked."""
    from .tracking import _visible_fraction
    q = np.asarray(quad)
    failures = []
    frames = list(range(start_idx, end_idx + 1))
    if _visible_fraction(q, width, height) < 0.98:
        failures.append(_fail('OVERLAY_OUT_OF_FRAME', 'The overlay extends outside the frame.', frames, fps))
    if min(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[3] - q[0])) < policy['min_product_side_px']:
        failures.append(_fail('PRODUCT_TOO_SMALL', 'The overlay would be too small to be legible.', frames, fps))
    return {
        'policy_version': POLICY_VERSION, 'thresholds_calibrated': False, 'mode': 'OVERLAY_SCREEN_SPACE',
        'status': 'FAILED' if failures else 'PASSED', 'scene_class': 'OVERLAY_SCREEN_SPACE',
        'interval': {'start_frame': start_idx, 'end_frame': end_idx, 'reference_frame': None, 'fps': fps},
        'frames_in_interval': len(frames), 'frames_validated': len(frames),
        'measurements': {'overlay_quad_px': q.round(1).tolist()}, 'thresholds': {'min_visible': 0.98, 'min_product_side_px': policy['min_product_side_px']},
        'failures': failures,
        'warnings': [{'code': 'NOT_SCENE_ANCHORED', 'message': 'Screen-space overlay: the product does not follow camera motion and is not inserted into the scene.'}],
        'recommended_action': failures[0]['recommended_action'] if failures else 'Overlay checks passed. A person must still confirm the overlay is appropriate.',
    }


# ------------------------------------------------------------------ TRACKED_3D
POLICY_3D_VERSION = 'tracked3d-v0.3-pose-aware-plane'   # V10: plane tolerance scales with pose error (render3d.plane_tolerance)
POLICY_3D = {
    'max_pose_reproj_px': 1.5,         # surface corners re-projected through the estimated pose
    'max_pose_step_deg': 15.0,         # larger frame-to-frame rotation = planar pose ambiguity flip
    'min_object_visible': 0.85,
    'min_object_height_px': 24,
    'max_foreground_fraction': 0.03,   # share of the object region not explained by the plane or the rigid scene (unchanged from v0.1)
    'max_occluded_fraction': 0.02,     # share of the object silhouette behind a static scene point nearer to the camera
}
ACTIONS.update({
    'FOCAL_UNOBSERVABLE': 'The shot does not reveal the camera focal length. Enter the camera field of view, or choose a shot with more perspective change.',
    'POSE_INCONSISTENT': 'The estimated camera does not explain the tracked surface. Check the real surface dimensions and corners.',
    'POSE_AMBIGUOUS': 'The surface pose flips between frames. Choose a shot with a more oblique view of the surface.',
    'OBJECT_OUT_OF_FRAME': 'Move the product anchor or shorten the interval so the object stays in frame.',
    'OBJECT_TOO_SMALL': 'Increase the object scale or choose a closer shot.',
    'OBJECT_OFF_SURFACE': 'Move the anchor so the object footprint lies inside the surface.',
    'FOREGROUND_MOTION_IN_OBJECT_REGION': 'Something moves where the object would stand. Occlusion of 3D objects is not supported; choose an unobstructed interval.',
    'OBJECT_OCCLUDED': 'Something in the scene stands between the camera and the object. Move the anchor to an unobstructed spot or choose another shot.',
})


def evaluate_3d(track, plan, cam, poses, mo, foreground, policy=POLICY_3D):
    """3D checks on top of the planar-surface checks. plan is None when the camera is unobservable.

    foreground: render3d.scene_consistency() output ({'moving': {...}, 'occluded': {...}}), or a legacy {idx: fraction}."""
    fps = track['fps']; s, e = track['start_idx'], track['end_idx']; p = policy
    scene = foreground if isinstance(foreground, dict) and 'moving' in foreground else {'moving': foreground or {}, 'occluded': {}}
    foreground, occluded = scene['moving'], scene['occluded']
    quads = plan.object_quads() if plan else {}
    base = evaluate_track(track, {i: q for i, q in quads.items() if s <= i <= e})
    failures = list(base['failures'])
    if plan is None:
        failures.append(_fail('FOCAL_UNOBSERVABLE', 'Camera focal length could not be estimated from these views.', [], fps,
                              'focal_sensitivity_px', cam.get('focal_sensitivity_px'), 0.5))
    else:
        frames = list(range(track['ref_idx'], e + 1))
        missing = [i for i in frames if i not in poses]
        if missing:
            failures.append(_fail('POSE_INCONSISTENT', 'No valid camera pose on these frames.', missing, fps))
        bad = [i for i in frames if i in poses and poses[i]['reproj_px'] > p['max_pose_reproj_px']]
        if bad:
            failures.append(_fail('POSE_INCONSISTENT', 'Estimated pose does not reproject the surface corners.', bad, fps, 'reproj_px',
                                  round(max(poses[i]['reproj_px'] for i in bad), 3), p['max_pose_reproj_px']))
        flips = [i for i in frames if i in poses and poses[i]['rot_step_deg'] > p['max_pose_step_deg']]
        if flips:
            failures.append(_fail('POSE_AMBIGUOUS', 'Camera rotation jumps between frames.', flips, fps, 'rot_step_deg',
                                  round(max(poses[i]['rot_step_deg'] for i in flips), 2), p['max_pose_step_deg']))
        vis = plan.object_visibility(track['width'], track['height'])
        out = [i for i in range(s, e + 1) if i in vis and vis[i][0] < p['min_object_visible']]
        small = [i for i in range(s, e + 1) if i in vis and vis[i][1] < p['min_object_height_px']]
        if out: failures.append(_fail('OBJECT_OUT_OF_FRAME', 'The object would leave the frame.', out, fps, 'visible_fraction', None, p['min_object_visible']))
        if small: failures.append(_fail('OBJECT_TOO_SMALL', 'The object would be too small to see.', small, fps, 'height_px', None, p['min_object_height_px']))
        W, H = mo['surface_size_m']
        if (plan.foot.min(axis=0) < 0).any() or plan.foot[:, 0].max() > W or plan.foot[:, 1].max() > H:
            failures.append(_fail('OBJECT_OFF_SURFACE', 'The object footprint extends beyond the supporting surface.', [s], fps))
        fg = [i for i, v in foreground.items() if v > p['max_foreground_fraction']]
        if fg:
            failures.append(_fail('FOREGROUND_MOTION_IN_OBJECT_REGION', 'Motion in the object region that neither the surface nor the static scene explains.', fg, fps,
                                  'moving_fraction', round(max(foreground[i] for i in fg), 4), p['max_foreground_fraction']))
        oc = [i for i, v in occluded.items() if v > p['max_occluded_fraction']]
        if oc:
            failures.append(_fail('OBJECT_OCCLUDED', 'A static scene element is nearer to the camera than the object, inside its outline.', oc, fps,
                                  'occluded_fraction', round(max(occluded[i] for i in oc), 4), p['max_occluded_fraction']))
    report = dict(base)
    passed = not failures and base['frames_validated'] == base['frames_in_interval']
    report.update({
        'policy_version': f"{POLICY_VERSION}+{POLICY_3D_VERSION}", 'mode': 'TRACKED_3D', 'failures': failures,
        'status': 'PASSED' if passed else 'FAILED',
        'scene_class': 'E_3D_ON_TRACKED_PLANE' if passed else (base['scene_class'] if base['status'] == 'FAILED' else 'E_3D_REJECTED'),
        'camera': {k: v for k, v in cam.items() if k != 'color_match'},
        'measurements': {**base['measurements'],
                         'pose_reproj_px': _summ([v['reproj_px'] for v in poses.values()]),
                         'pose_rot_step_deg': _summ([v['rot_step_deg'] for v in poses.values()]),
                         'foreground_moving_fraction': _summ(list(foreground.values())),
                         'occluded_fraction': _summ(list(occluded.values()))},
        'scene_check': {k: v for k, v in scene.items() if k in ('method', 'params', 'frames_analysed')},
        'thresholds': {**base['thresholds'], **p},
        'lighting': {'light': mo['light'], 'source': mo['light_source'],
                     'note': 'Light direction is reviewer-set or a default; it is not estimated from the footage.'},
    })
    report['recommended_action'] = failures[0]['recommended_action'] if failures else 'Quality checks passed. A person must still review the 3D integration.'
    return report
