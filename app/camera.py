"""Camera intrinsics and per-frame pose from a tracked planar surface of known physical size.

The tracked surface quad gives 4 image points per frame for 4 known plane points (the reviewer
supplies the surface's real width and height). Focal length is estimated by planar calibration over
many views (principal point at image centre, square pixels, no distortion). If the views do not
constrain the focal length (for example a fronto-parallel shot with no perspective change), the result
is reported as unobservable rather than guessed. A reviewer may supply a known horizontal field of view.
"""
import cv2
import numpy as np

MAX_VIEWS = 40
MAX_FOCAL_REL_STD = 0.10
# Observability: re-solving the views with the focal length 15% off must raise the reprojection RMS by at least
# this much. Dev split: observable shots rise >= 1.7 px; a fronto-parallel translating shot rises ~0.00 px.
# (The calibration's own standard deviation is not trustworthy here: near-exact homographies give near-zero
# residuals and therefore falsely small uncertainty.)
MIN_FOCAL_SENSITIVITY_PX = 0.5


def _rms_at(views, obj, f, w, h):
    K = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1.0]])
    err = []
    for c in views:
        ok, r, t = cv2.solvePnP(obj, c, K, None, flags=cv2.SOLVEPNP_IPPE)
        pr, _ = cv2.projectPoints(obj, r, t, K, None)
        err.append(((pr.reshape(-1, 2) - c) ** 2).sum(axis=1))
    return float(np.sqrt(np.mean(err)))


def plane_points(w_m, h_m):
    return np.array([[0, 0, 0], [w_m, 0, 0], [w_m, h_m, 0], [0, h_m, 0]], np.float32)


def surface_corners(track, start_idx, end_idx):
    q = np.asarray(track['quad_ref'], np.float64).reshape(-1, 1, 2)
    out = {}
    for f in track['frames']:
        if f.get('H') is not None and track['ref_idx'] <= f['idx'] <= end_idx:
            out[f['idx']] = cv2.perspectiveTransform(q, np.asarray(f['H']).reshape(3, 3)).reshape(4, 2).astype(np.float32)
    return out


def estimate_camera(track, surface_w_m, surface_h_m, start_idx, end_idx, hfov_deg=None):
    w, h = track['width'], track['height']
    corners = surface_corners(track, start_idx, end_idx)
    obj = plane_points(surface_w_m, surface_h_m)
    info = {'principal_point': [w / 2, h / 2], 'assumptions': 'square pixels, centred principal point, no lens distortion'}
    if hfov_deg:
        f = (w / 2) / np.tan(np.radians(hfov_deg) / 2)
        info.update(focal_px=round(float(f), 2), focal_source='user', focal_rel_std=None, observable=True)
    else:
        keys = sorted(corners)
        sel = [keys[int(i)] for i in np.linspace(0, len(keys) - 1, min(MAX_VIEWS, len(keys)))]
        f0 = (w / 2) / np.tan(np.radians(55) / 2)
        K0 = np.array([[f0, 0, w / 2], [0, f0, h / 2], [0, 0, 1]], np.float64)
        flags = (cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_FIX_PRINCIPAL_POINT | cv2.CALIB_FIX_ASPECT_RATIO | cv2.CALIB_ZERO_TANGENT_DIST
                 | cv2.CALIB_FIX_K1 | cv2.CALIB_FIX_K2 | cv2.CALIB_FIX_K3)
        try:
            rms, K, _d, _r, _t, std_i, _se, _pv = cv2.calibrateCameraExtended([obj] * len(sel), [corners[k] for k in sel], (w, h), K0, np.zeros(5), flags=flags)
            f = float(K[0, 0]); rel = float(std_i[0, 0]) / f if f > 0 else np.inf
        except cv2.error:
            f, rel, rms = float('nan'), float('inf'), float('nan')
        ok = bool(np.isfinite(f) and .3 * w < f < 10 * w and np.isfinite(rel) and rel <= MAX_FOCAL_REL_STD)
        if ok:
            views = [corners[k] for k in sel]; r0 = _rms_at(views, obj, f, w, h)
            sens = min(_rms_at(views, obj, f * .85, w, h), _rms_at(views, obj, f * 1.15, w, h)) - r0
            info['focal_sensitivity_px'] = round(sens, 4)
            ok = sens >= MIN_FOCAL_SENSITIVITY_PX
        info.update(focal_px=round(f, 2) if np.isfinite(f) else None, focal_rel_std=round(rel, 4) if np.isfinite(rel) else None,
                    calibration_rms_px=round(float(rms), 4) if np.isfinite(rms) else None, views=len(sel),
                    focal_source='estimated' if ok else 'unobservable', observable=bool(ok))
    if not info['observable']:
        return info, None
    f = info['focal_px']
    K = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]], np.float64)
    info['hfov_deg'] = round(float(np.degrees(2 * np.arctan(w / 2 / f))), 2)
    return info, K


def _rot_angle(Ra, Rb):
    c = (np.trace(Ra.T @ Rb) - 1) / 2
    return float(np.degrees(np.arccos(np.clip(c, -1, 1))))


def solve_poses(track, K, surface_w_m, surface_h_m, start_idx, end_idx):
    """Per-frame plane->camera pose via IPPE; the planar two-fold ambiguity is resolved by error and temporal continuity."""
    corners = surface_corners(track, start_idx, end_idx)
    obj = plane_points(surface_w_m, surface_h_m)
    poses, prev = {}, None
    for idx in sorted(corners):
        n, rvecs, tvecs, errs = cv2.solvePnPGeneric(obj, corners[idx], K, None, flags=cv2.SOLVEPNP_IPPE)
        cands = [(float(np.ravel(errs)[k]), cv2.Rodrigues(rvecs[k])[0], tvecs[k].ravel()) for k in range(n)]
        cands = [c for c in cands if c[2][2] > 0]            # plane in front of the camera
        if not cands:
            continue
        best = min(cands, key=lambda c: c[0])
        if prev is not None and len(cands) > 1:
            close = [c for c in cands if c[0] <= best[0] * 1.5 + 0.05]
            best = min(close, key=lambda c: _rot_angle(prev, c[1]))
        step = _rot_angle(prev, best[1]) if prev is not None else 0.0
        poses[idx] = {'R': best[1], 't': best[2], 'reproj_px': best[0], 'rot_step_deg': step}
        prev = best[1]
    return poses
