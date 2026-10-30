"""Planar surface tracking (sparse optical flow + RANSAC homography) and per-frame measurements.

The user (or a reviewed surface proposal) supplies four normalized corners of a planar surface in a
reference frame. This module only *measures*; pass/fail decisions live in quality.py.
It is deterministic CV tracking, not a semantic object detector or generative model.
"""
import cv2
import numpy as np

from .media import interval_frames

PATCH = 96   # rectified surface patch size used for photometric checks
GRID = 4     # PATCH is split into GRID x GRID cells for occlusion localisation


def validate_quad(quad):
    if not isinstance(quad, (list, tuple)) or len(quad) != 4:
        raise ValueError('tracking_quad must contain exactly four normalized [x,y] points in TL,TR,BR,BL order')
    arr=np.asarray(quad,dtype=np.float32)
    if arr.shape != (4,2) or not np.isfinite(arr).all() or (arr < 0).any() or (arr > 1).any():
        raise ValueError('tracking_quad coordinates must be finite numbers between 0 and 1')
    if abs(cv2.contourArea(arr)) < 0.0001:
        raise ValueError('tracking_quad area is too small')
    if not cv2.isContourConvex(arr):
        raise ValueError('tracking_quad must describe a convex surface in TL,TR,BR,BL order')
    return arr


def _rectify(gray, quad):
    dst = np.array([[0, 0], [PATCH, 0], [PATCH, PATCH], [0, PATCH]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(np.asarray(quad, dtype=np.float32), dst)
    return cv2.warpPerspective(gray, M, (PATCH, PATCH), flags=cv2.INTER_LINEAR)


def _crop(img, centre, cw, ch, constant=False):
    """Fixed-size crop centred on centre, padded at the borders; returns (crop, origin_xy)."""
    x0, y0 = int(round(centre[0] - cw / 2)), int(round(centre[1] - ch / 2))
    pad = max(0, -x0, -y0, x0 + cw - img.shape[1], y0 + ch - img.shape[0])
    src = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT if constant else cv2.BORDER_REPLICATE, value=0) if pad else img
    return np.ascontiguousarray(src[y0 + pad:y0 + pad + ch, x0 + pad:x0 + pad + cw]), (x0, y0)


def _chroma(frame, quad):
    """Mean linear-light B,G,R of the rectified surface (for temporal colour matching)."""
    from .compositor import srgb_to_linear
    return srgb_to_linear(_rectify(frame, quad)).reshape(-1, 3).mean(axis=0).round(6).tolist()


def _ncc(a, b):
    a = a.astype(np.float64) - a.mean(); b = b.astype(np.float64) - b.mean()
    den = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / den) if den > 1e-6 else None


def _cells(patch):
    s = PATCH // GRID
    return [patch[r * s:(r + 1) * s, c * s:(c + 1) * s] for r in range(GRID) for c in range(GRID)]


def _hist(frame):
    small = cv2.resize(frame, (96, 54))
    h = cv2.calcHist([small], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
    return cv2.normalize(h, h)


def _visible_fraction(quad, w, h):
    q = np.asarray(quad, dtype=np.float32)
    area = abs(cv2.contourArea(q))
    if area < 1e-6:
        return 0.0
    frame = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    inter, _ = cv2.intersectConvexConvex(q, frame)
    return float(min(1.0, inter / area))


def _detect(gray, quad, max_corners=400):
    mask = np.zeros(gray.shape, dtype=np.uint8)
    cv2.fillConvexPoly(mask, np.round(quad).astype(np.int32), 255)
    pts = cv2.goodFeaturesToTrack(gray, maxCorners=max_corners, qualityLevel=.01, minDistance=5, mask=mask, blockSize=7)
    return np.empty((0, 2), np.float32) if pts is None else pts.reshape(-1, 2).astype(np.float32)


def track_surface(source, quad_norm, reference_second, start_second, end_second, progress=None, refine=True):
    """Track a planar quad from the reference frame through the end of the placement interval.

    Returns per-frame homographies (reference -> frame) and measurements for every frame in
    [reference, end]. Frames after a tracking loss are reported as lost, never extrapolated.
    """
    quad_norm = validate_quad(quad_norm)
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise RuntimeError('Could not open source video for tracking')
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    start_idx, end_idx = interval_frames(fps, total, start_second, end_second)
    ref_idx = max(0, min(total - 1, round(reference_second * fps)))
    if ref_idx > start_idx:
        cap.release(); raise ValueError('Reference frame must be at or before the placement start')
    if end_idx < start_idx:
        cap.release(); raise ValueError('Placement interval contains no frames')
    quad_ref = (quad_norm * np.array([w, h], dtype=np.float32)).astype(np.float32)

    # Sequential decode keeps frame indices identical to the renderer and validator.
    idx = 0; ref = None
    while idx <= ref_idx:
        ok, frame = cap.read()
        if not ok:
            cap.release(); raise RuntimeError('Could not read tracking reference frame')
        if idx == ref_idx: ref = frame
        idx += 1
    ref_gray = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
    ref_pts = _detect(ref_gray, quad_ref)
    ref_patch = _rectify(ref_gray, quad_ref)
    ref_cells = _cells(ref_patch)
    textured_cells = [i for i, c in enumerate(ref_cells) if c.std() >= 4.0]
    ref_sharp = float(cv2.Laplacian(ref_patch, cv2.CV_64F).var())
    ref_lum = float(ref_patch.mean())
    # Reference-anchored refinement: each frame's homography is re-registered to the reference surface (ECC),
    # so tracking error does not accumulate across frames.
    ref_mask = np.zeros(ref_gray.shape, np.uint8)
    cv2.fillConvexPoly(ref_mask, np.round(quad_ref).astype(np.int32), 255)
    ref_mask = cv2.erode(ref_mask, np.ones((5, 5), np.uint8))
    diag = float(np.hypot(w, h))
    # ECC cost scales with image size, so align equal-size crops around the surface (offsets folded into H).
    span = quad_ref.max(axis=0) - quad_ref.min(axis=0)
    cw, ch = int(span[0] * 1.4) + 8, int(span[1] * 1.4) + 8
    centre_ref = quad_ref.mean(axis=0)
    ecc_tpl, t_org = _crop(ref_gray, centre_ref, cw, ch)
    ecc_mask, _ = _crop(ref_mask, centre_ref, cw, ch, constant=True)
    T_tpl = np.array([[1, 0, t_org[0]], [0, 1, t_org[1]], [0, 0, 1]], np.float64)
    result = {'fps': fps, 'width': w, 'height': h, 'frame_count': total, 'ref_idx': ref_idx,
              'start_idx': start_idx, 'end_idx': end_idx, 'quad_ref': quad_ref.tolist(),
              'ref_feature_count': int(len(ref_pts)), 'textured_cells': len(textured_cells),
              'ref_sharpness': ref_sharp, 'frames': []}
    frames = result['frames']
    frames.append({'idx': ref_idx, 'H': np.eye(3).ravel().tolist(), 'quad': quad_ref.tolist(), 'points': int(len(ref_pts)),
                   'inliers': int(len(ref_pts)), 'inlier_ratio': 1.0, 'rmse': 0.0, 'ncc': 1.0, 'cell_ncc': [1.0] * len(textured_cells),
                   'sharpness_ratio': 1.0, 'luminance_ratio': 1.0, 'cut_score': 0.0, 'visible': _visible_fraction(quad_ref, w, h),
                   'lost': len(ref_pts) < 8, 'reseeded': False, 'chroma': _chroma(ref, quad_ref)})
    if len(ref_pts) < 8:
        cap.release(); result['lost_reason'] = 'insufficient_features'; return result

    initial = len(ref_pts)
    cur_ref = ref_pts.copy()            # reference-frame coordinates of tracked points
    cur_pts = ref_pts.copy()            # their positions in the previous frame
    prev_gray, prev_hist = ref_gray, _hist(ref)
    H = np.eye(3); lost = False
    while idx <= end_idx:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hist = _hist(frame)
        cut = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA))
        rec = {'idx': idx, 'cut_score': cut, 'lost': True, 'reseeded': False}
        if not lost and len(cur_pts) >= 8:
            lk = dict(winSize=(21, 21), maxLevel=3, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, .01))
            nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev_gray, gray, cur_pts.reshape(-1, 1, 2), None, **lk)
            back, st2, _ = cv2.calcOpticalFlowPyrLK(gray, prev_gray, nxt, None, **lk)
            fb = np.linalg.norm(back.reshape(-1, 2) - cur_pts, axis=1)
            keep = (st.ravel() == 1) & (st2.ravel() == 1) & (fb < 1.0)
            src, dst = cur_ref[keep], nxt.reshape(-1, 2)[keep]
            rec['points'] = int(keep.sum())
            Hn, inl = (cv2.findHomography(src, dst, cv2.RANSAC, 3.0) if len(src) >= 8 else (None, None))
            if Hn is not None and inl is not None and int(inl.sum()) >= 6:
                inl = inl.ravel() == 1
                proj = cv2.perspectiveTransform(src[inl].reshape(-1, 1, 2), Hn).reshape(-1, 2)
                H = Hn
                if refine:
                    try:
                        centre = cv2.perspectiveTransform(centre_ref.reshape(1, 1, 2).astype(np.float64), Hn).ravel()
                        inp, i_org = _crop(gray, centre, cw, ch)
                        T_in = np.array([[1, 0, i_org[0]], [0, 1, i_org[1]], [0, 0, 1]], np.float64)
                        Hr = (np.linalg.inv(T_in) @ Hn @ T_tpl).astype(np.float32)
                        cc, Hr = cv2.findTransformECC(ecc_tpl, inp, Hr, cv2.MOTION_HOMOGRAPHY,
                                                      (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 1e-5), ecc_mask, 5)
                        Hr = T_in @ Hr.astype(np.float64) @ np.linalg.inv(T_tpl); Hr /= Hr[2, 2]
                        q0 = cv2.perspectiveTransform(quad_ref.reshape(-1, 1, 2), Hn)
                        q1 = cv2.perspectiveTransform(quad_ref.reshape(-1, 1, 2), Hr.astype(np.float64))
                        if np.isfinite(Hr).all() and float(np.abs(q1 - q0).max()) < .03 * diag:
                            H = Hr.astype(np.float64); rec['refined'] = True; rec['ecc_cc'] = round(float(cc), 4)
                    except cv2.error:
                        rec['refined'] = False
                cur_ref, cur_pts = src[inl], dst[inl]
                rec.update(inliers=int(inl.sum()), inlier_ratio=float(inl.mean()),
                           rmse=float(np.sqrt(((proj - dst[inl]) ** 2).sum(axis=1).mean())), lost=False)
                quad = cv2.perspectiveTransform(quad_ref.reshape(-1, 1, 2), H).reshape(4, 2)
                # Re-seed from the current frame when too many points were dropped (reinitialisation).
                if len(cur_pts) < max(30, .4 * initial):
                    fresh = _detect(gray, quad)
                    if len(fresh):
                        d = np.min(np.linalg.norm(fresh[:, None, :] - cur_pts[None, :, :], axis=2), axis=1) if len(cur_pts) else np.full(len(fresh), 99.0)
                        fresh = fresh[d > 5]
                        if len(fresh):
                            back_ref = cv2.perspectiveTransform(fresh.reshape(-1, 1, 2), np.linalg.inv(H)).reshape(-1, 2)
                            cur_ref = np.vstack([cur_ref, back_ref]).astype(np.float32)
                            cur_pts = np.vstack([cur_pts, fresh]).astype(np.float32)
                            rec['reseeded'] = True
            else:
                rec.update(inliers=0 if inl is None else int(inl.sum()), inlier_ratio=0.0, rmse=None)
        if rec['lost']:
            lost = True
            rec.update(H=None, quad=None, ncc=None, cell_ncc=[], sharpness_ratio=None, luminance_ratio=None, visible=None)
        else:
            quad = cv2.perspectiveTransform(quad_ref.reshape(-1, 1, 2), H).reshape(4, 2)
            convex = bool(cv2.isContourConvex(quad.astype(np.float32)))
            patch = _rectify(gray, quad) if convex else np.zeros_like(ref_patch)
            cells = _cells(patch)
            rec.update(H=H.ravel().tolist(), quad=quad.tolist(), convex=convex,
                       area_ratio=float(abs(cv2.contourArea(quad.astype(np.float32))) / max(abs(cv2.contourArea(quad_ref)), 1e-6)),
                       ncc=_ncc(ref_patch, patch), cell_ncc=[_ncc(ref_cells[i], cells[i]) for i in textured_cells],
                       sharpness_ratio=float(cv2.Laplacian(patch, cv2.CV_64F).var()) / max(ref_sharp, 1e-6),
                       luminance_ratio=float(patch.mean()) / max(ref_lum, 1e-6), visible=_visible_fraction(quad, w, h),
                       chroma=_chroma(frame, quad) if convex else None)
        frames.append(rec)
        prev_gray, prev_hist = gray, hist
        idx += 1
        if progress and idx % 15 == 0:
            progress(idx)
    cap.release()
    return result


def homography_quads(track, local_quad_ref):
    """Map a quad given in reference-frame pixels through each tracked frame's homography."""
    q = np.asarray(local_quad_ref, dtype=np.float64).reshape(-1, 1, 2)
    out = {}
    for f in track['frames']:
        if f.get('H') is not None:
            out[f['idx']] = cv2.perspectiveTransform(q, np.asarray(f['H']).reshape(3, 3)).reshape(4, 2).tolist()
    return out


def render_tracked_video(source, product, output, quad_norm, reference_second, start_second, end_second):
    """Convenience: track then composite. Callers that need a quality gate must use quality.py first."""
    from .compositor import load_product, product_quad_in_surface, render_video
    track = track_surface(source, quad_norm, reference_second, start_second, end_second)
    if any(f['lost'] for f in track['frames']):
        raise RuntimeError('Surface tracking lost. Use a clearer reference frame or switch to static placement.')
    ph, pw = load_product(product).shape[:2]
    pq = product_quad_in_surface(track['quad_ref'], pw, ph)
    quads = {i: q for i, q in homography_quads(track, pq).items() if track['start_idx'] <= i <= track['end_idx']}
    info = render_video(source, product, output, quads)
    return {'mode': 'opencv_optical_flow_homography', 'frames': info['frames_written'], 'fps': info['fps'],
            'reference_second': reference_second, 'product_quad_ref': pq.tolist(), 'frame_quads': quads}
