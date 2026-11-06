"""Planar surface candidates from straight edges (M14, specification §8.5).

The contour detector (`decisions.detect_surfaces`) finds a surface when its outline is a closed edge contour. That fails on
the surfaces that matter most for placement, posters, screens, frames, signs, whose inside is textured or whose outline is
crossed by other edges: the outline never closes, or the texture breaks it into many small regions (3 of 42 textured
posters found, docs/qa/AI_EVALUATION_REPORT.md). This module builds quadrilateral hypotheses from line segments instead:

  1. segments   the LSD line-segment detector, then collinear pieces are merged (direction within 2.5 degrees, offset within
                2 px, gap within a quarter of the shorter piece), so an outline interrupted by crossing edges becomes one line
  2. pairs      two long, near-parallel segments (within 20 degrees) that overlap along their length are opposite sides
  3. closing    segments crossing both (at least 40 degrees off) close the shape; each pair of crossings gives a quadrilateral
  4. support    every side must lie on real edges: points sampled along each side are within 2 px of a detected segment for
                at least `MIN_SUPPORT` of the side. The weakest side decides
Only quadrilaterals that pass every check are returned, so a cluttered frame with no surface returns nothing.
`detect()` merges these with the contour detector's candidates.
"""
import math

import cv2
import numpy as np

ALGORITHM = 'lsd-quads-v1'
MIN_SUPPORT = 0.75
_PARALLEL_DEG, _CROSS_DEG = 20.0, 40.0


def merge_segments(segs, ang_tol=2.5, dist_tol=2.0, gap_frac=0.25, gap_min=6.0):
    """Join collinear segments (x0, y0, x1, y1) separated by small gaps. Pure; vectorised over candidates."""
    S = np.asarray(segs, np.float64).reshape(-1, 4)
    cos_tol = math.cos(math.radians(ang_tol))
    changed = True
    while changed and len(S) > 1:
        changed = False
        L = np.hypot(S[:, 2] - S[:, 0], S[:, 3] - S[:, 1]); order = np.argsort(-L, kind='stable')
        S, L = S[order], L[order]
        keep = L > 1e-6; S, L = S[keep], L[keep]
        U = (S[:, 2:] - S[:, :2]) / L[:, None]
        alive = np.ones(len(S), bool); out = []
        for i in range(len(S)):
            if not alive[i]: continue
            alive[i] = False
            p0, p1 = S[i, :2].copy(), S[i, 2:].copy()
            while True:
                idx = np.flatnonzero(alive)
                if not len(idx): break
                d = p1 - p0; Li = math.hypot(*d); u = d / Li; nrm = np.array([-u[1], u[0]])
                B = S[idx]; a0, a1 = B[:, :2] - p0, B[:, 2:] - p0
                t0, t1 = a0 @ u, a1 @ u; lo, hi = np.minimum(t0, t1), np.maximum(t0, t1)
                gap = np.maximum(np.maximum(lo - Li, -hi), 0.0)
                ok = ((np.abs(U[idx] @ u) >= cos_tol) & (np.abs(a0 @ nrm) <= dist_tol) & (np.abs(a1 @ nrm) <= dist_tol)
                      & (gap <= np.maximum(gap_min, gap_frac * L[idx])))
                if not ok.any(): break
                lo_all, hi_all = min(0.0, float(lo[ok].min())), max(Li, float(hi[ok].max()))
                p0, p1 = p0 + u * lo_all, p0 + u * hi_all
                alive[idx[ok]] = False; changed = True
            out.append(np.concatenate([p0, p1]))
        S = np.asarray(out).reshape(-1, 4)
    return [s for s in S]


def _segments(gray):
    raw = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(gray)[0]
    raw = [] if raw is None else [s for s in raw.reshape(-1, 4) if math.hypot(s[2] - s[0], s[3] - s[1]) >= 4]
    return raw, merge_segments([s for s in raw if math.hypot(s[2] - s[0], s[3] - s[1]) >= 6])


def _intersect_many(a, C):
    """Intersections of the line through segment a with the lines through each row of C: (points, parameter on a, valid)."""
    p, r = a[:2], a[2:] - a[:2]; q, sd = C[:, :2], C[:, 2:] - C[:, :2]
    den = r[0] * sd[:, 1] - r[1] * sd[:, 0]; valid = np.abs(den) > 1e-9; den = np.where(valid, den, 1.0)
    t = ((q[:, 0] - p[0]) * sd[:, 1] - (q[:, 1] - p[1]) * sd[:, 0]) / den
    return p + t[:, None] * r, t, valid


def _param(seg, pt):
    d = seg[2:] - seg[:2]; L2 = float(np.dot(d, d))
    return float(np.dot(pt - seg[:2], d) / L2) if L2 > 0 else 0.0


def _angle(s):
    return math.degrees(math.atan2(s[3] - s[1], s[2] - s[0])) % 180.0


def _adiff(a, b):
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _support(dist, quad, n=32, tol=2.0):
    """Per side, the fraction of sample points within `tol` px of a detected segment."""
    h, w = dist.shape; out = []
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        t = np.linspace(0.04, 0.96, n)[:, None]
        pts = a + (b - a) * t
        x = np.clip(np.round(pts[:, 0]).astype(int), 0, w - 1); y = np.clip(np.round(pts[:, 1]).astype(int), 0, h - 1)
        inside = (pts[:, 0] >= 0) & (pts[:, 0] < w) & (pts[:, 1] >= 0) & (pts[:, 1] < h)
        out.append(float(((dist[y, x] <= tol) & inside).mean()))
    return out


def line_quads(image, max_out=6, min_area=.02, max_area=.55):
    """Quadrilaterals whose four sides lie on straight edges. Same dict shape as `decisions.detect_surfaces`."""
    from .decisions import _order_quad, orientation, _iou
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    raw, segs = _segments(gray)
    diag = math.hypot(w, h); min_len = max(16.0, 0.05 * diag); min_side = 12.0
    long = [s for s in segs if math.hypot(s[2] - s[0], s[3] - s[1]) >= min_len]
    if len(long) < 4: return []
    raster = np.full((h, w), 255, np.uint8)
    for s in raw: cv2.line(raster, (int(round(s[0])), int(round(s[1]))), (int(round(s[2])), int(round(s[3]))), 0, 1)
    dist = cv2.distanceTransform(raster, cv2.DIST_L2, 3)
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    L4 = np.asarray(long, np.float64)
    ang = [_angle(s) for s in long]; length = [math.hypot(s[2] - s[0], s[3] - s[1]) for s in long]
    pairs = []
    for i in range(len(long)):
        for j in range(i + 1, len(long)):
            if _adiff(ang[i], ang[j]) > _PARALLEL_DEG: continue
            a, b = long[i], long[j]
            u = (a[2:] - a[:2]) / length[i]; nrm = np.array([-u[1], u[0]])
            sep = abs(float(np.dot((b[:2] + b[2:]) / 2 - a[:2], nrm)))
            if sep < min_side: continue
            t0, t1 = sorted([_param(a, b[:2]), _param(a, b[2:])])
            overlap = (min(1.0, t1) - max(0.0, t0)) * length[i]
            if overlap < 0.4 * min(length[i], length[j]): continue
            pairs.append((length[i] + length[j], i, j))
    pairs.sort(reverse=True)
    found = []
    for _, i, j in pairs[:400]:
        a, b = long[i], long[j]
        tol_a, tol_b = 0.1 + 4 / length[i], 0.1 + 4 / length[j]
        crosses = []
        ks = [k for k in range(len(long)) if k not in (i, j) and min(_adiff(ang[k], ang[i]), _adiff(ang[k], ang[j])) >= _CROSS_DEG]
        if ks:
            C = L4[ks]
            xa, ta, okA = _intersect_many(a, C); xb, tb, okB = _intersect_many(b, C)
            r = C[:, 2:] - C[:, :2]; rr = np.maximum((r * r).sum(axis=1), 1e-12)
            ca = ((xa - C[:, :2]) * r).sum(axis=1) / rr; cb = ((xb - C[:, :2]) * r).sum(axis=1) / rr
            lo, hi = np.minimum(ca, cb), np.maximum(ca, cb)
            cover = (np.minimum(1.0, hi) - np.maximum(0.0, lo)) / np.maximum(hi - lo, 1e-6)
            ok = okA & okB & (ta >= -tol_a) & (ta <= 1 + tol_a) & (tb >= -tol_b) & (tb <= 1 + tol_b) & (cover >= 0.4)
            crosses = [(float(ta[q]), xa[q], xb[q]) for q in np.flatnonzero(ok)]
        crosses.sort(key=lambda x: x[0])
        for m in range(len(crosses)):
            for n in range(m + 1, len(crosses)):
                (_, a1, b1), (_, a2, b2) = crosses[m], crosses[n]
                if math.hypot(*(a2 - a1)) < min_side or math.hypot(*(b2 - b1)) < min_side: continue
                quad = np.array([a1, a2, b2, b1], np.float64)
                if not cv2.isContourConvex(quad.astype(np.float32)): continue
                area = abs(cv2.contourArea(quad.astype(np.float32))) / (w * h)
                if not min_area <= area <= max_area: continue
                sup = _support(dist, quad)
                if min(sup) < MIN_SUPPORT: continue
                pts = _order_quad(quad)
                x0, y0 = pts.min(axis=0); x1, y1 = pts.max(axis=0)
                if x0 < 2 and y0 < 2 and x1 > w - 3 and y1 > h - 3: continue
                sides = [math.hypot(*(pts[(q + 1) % 4] - pts[q])) for q in range(4)]
                aspect = max(sides[0], 1) / max(sides[1], 1)
                if min(sides) < min_side or not .15 < aspect < 6.5: continue
                mask = np.zeros((h, w), np.uint8); cv2.fillConvexPoly(mask, pts.astype(np.int32), 255)
                mask = cv2.erode(mask, np.ones((5, 5), np.uint8))
                if not (mask > 0).any(): continue
                texture = min(1.0, float(lap[mask > 0].var()) / 250.0)
                area_score = area / .03 if area < .03 else 1.0 if area <= .3 else max(0.0, 1 - (area - .3) / .3)
                support = float(np.mean(sup))
                found.append({'quad_px': pts, 'area_fraction': area, 'texture': texture, 'aspect': aspect, 'support': round(support, 3),
                              'confidence': .35 * area_score + .25 * texture + .4 * support, 'bbox': (x0, y0, x1, y1),
                              'orientation': orientation(pts), 'mask': mask, 'method': 'lines'})
    found.sort(key=lambda c: (-c['support'], -c['confidence']))
    kept = []
    for cand in found:
        if all(_iou(cand['bbox'], k['bbox']) < .6 for k in kept): kept.append(cand)
        if len(kept) >= max_out: break
    for k in kept:
        k['quad'] = (k['quad_px'] / np.array([w, h])).clip(0, 1).tolist()
    return kept


def detect(image, max_out=6):
    """Surface candidates from both detectors, best first, overlapping duplicates removed."""
    from .decisions import detect_surfaces, _iou
    cands = [dict(c, method='contours') for c in detect_surfaces(image, max_out=max_out)] + line_quads(image, max_out=max_out)
    cands.sort(key=lambda c: -c['confidence'])
    kept = []
    for c in cands:
        if all(_iou(c['bbox'], k['bbox']) < .6 for k in kept): kept.append(c)
        if len(kept) >= max_out: break
    return kept


# ---------------------------------------------------------------- following a plane through a scene
TRACK_ALGORITHM = 'klt-ransac-homography-v1'
_PATCH, _GRID = 64, 4


def _scene_frames(source, start, end, step_seconds=0.12, max_samples=300, max_width=512):
    """Grey, downscaled frames sampled through [start, end): [(t, gray)], and the scale factor applied."""
    cap = cv2.VideoCapture(str(source))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    span = max(0.0, end - start)
    stride = max(1, int(round(fps * step_seconds)), int(math.ceil(span * fps / max_samples)))
    first = int(math.floor(start * fps + 1e-6)); last = int(math.ceil(end * fps - 1e-6))
    cap.set(cv2.CAP_PROP_POS_FRAMES, first)
    out, scale, idx = [], 1.0, first
    while idx < last:
        if not cap.grab(): break
        if (idx - first) % stride == 0:
            ok, frame = cap.retrieve()
            if not ok: break
            h, w = frame.shape[:2]; scale = min(1.0, max_width / w)
            g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if scale < 1.0: g = cv2.resize(g, (int(round(w * scale)), int(round(h * scale))), interpolation=cv2.INTER_AREA)
            out.append((round(idx / fps, 3), g))
        idx += 1
    cap.release()
    return out, scale


def _rectify(gray, quad):
    dst = np.array([[0, 0], [_PATCH, 0], [_PATCH, _PATCH], [0, _PATCH]], np.float32)
    return cv2.warpPerspective(gray, cv2.getPerspectiveTransform(np.asarray(quad, np.float32), dst), (_PATCH, _PATCH))


def _ncc(a, b):
    a = a.astype(np.float64) - a.mean(); b = b.astype(np.float64) - b.mean()
    den = math.sqrt(float((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / den) if den > 1e-6 else None


def _visible(quad, w, h):
    q = np.asarray(quad, np.float32); area = abs(cv2.contourArea(q))
    if area < 1e-6: return 0.0
    inter, _ = cv2.intersectConvexConvex(q, np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32))
    return float(min(1.0, inter / area))


def _features(gray, quad, grow=6, n=200):
    """Corners on the surface and its outline (grown by `grow` px, so a plain surface's border is used too)."""
    mask = np.zeros(gray.shape, np.uint8)
    c = quad.mean(axis=0); q = c + (quad - c) * (1 + grow / max(1.0, float(np.linalg.norm(quad - c, axis=1).mean())))
    cv2.fillConvexPoly(mask, np.round(q).astype(np.int32), 255)
    pts = cv2.goodFeaturesToTrack(gray, maxCorners=n, qualityLevel=.01, minDistance=4, mask=mask, blockSize=5)
    return np.empty((0, 2), np.float32) if pts is None else pts.reshape(-1, 2).astype(np.float32)


def _follow_one(frames, ref_k, quad_ref, direction):
    """Track quad_ref (pixels in the downscaled reference frame) from frame ref_k in one direction. Returns (records, reason)."""
    h, w = frames[ref_k][1].shape
    ref_gray = frames[ref_k][1]
    ref_pts = _features(ref_gray, quad_ref)
    if len(ref_pts) < 10: return [], 'insufficient_features'
    ref_patch = _rectify(ref_gray, quad_ref); s = _PATCH // _GRID
    ref_cells = [ref_patch[r * s:(r + 1) * s, c * s:(c + 1) * s] for r in range(_GRID) for c in range(_GRID)]
    textured = [i for i, c in enumerate(ref_cells) if c.std() >= 4.0]
    area_ref = abs(cv2.contourArea(quad_ref.astype(np.float32)))
    lk = dict(winSize=(21, 21), maxLevel=3, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, .01))
    src_ref, cur = ref_pts.copy(), ref_pts.copy(); initial = len(ref_pts)
    prev = ref_gray; out = []; k = ref_k + direction
    while 0 <= k < len(frames):
        t, gray = frames[k]
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, gray, cur.reshape(-1, 1, 2), None, **lk)
        back, st2, _ = cv2.calcOpticalFlowPyrLK(gray, prev, nxt, None, **lk)
        keep = (st.ravel() == 1) & (st2.ravel() == 1) & (np.linalg.norm(back.reshape(-1, 2) - cur, axis=1) < 1.0)
        src, dst = src_ref[keep], nxt.reshape(-1, 2)[keep]
        if len(src) < 8: return out, 'lost'
        H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 2.0)
        if H is None or inl is None or int(inl.sum()) < 8: return out, 'lost'
        inl = inl.ravel() == 1; ratio = float(inl.mean())
        quad = cv2.perspectiveTransform(quad_ref.reshape(-1, 1, 2).astype(np.float64), H).reshape(4, 2)
        if not cv2.isContourConvex(quad.astype(np.float32)): return out, 'deformed'
        area = abs(cv2.contourArea(quad.astype(np.float32)))
        if not 0.25 <= area / max(area_ref, 1e-6) <= 4.0: return out, 'deformed'
        vis = _visible(quad, w, h)
        if vis < 0.6: return out, 'left_frame'
        if ratio < 0.5: return out, 'not_planar'
        proj = cv2.perspectiveTransform(src[inl].reshape(-1, 1, 2), H).reshape(-1, 2)
        rmse = float(np.sqrt(((proj - dst[inl]) ** 2).sum(axis=1).mean()))
        patch = _rectify(gray, quad)
        cells = [patch[r * s:(r + 1) * s, c * s:(c + 1) * s] for r in range(_GRID) for c in range(_GRID)]
        cn = [_ncc(ref_cells[i], cells[i]) for i in textured]
        occluded = (sum(1 for v in cn if v is not None and v < 0.4) / len(cn)) if cn else None
        # Something in front of the surface drags the homography with it; the placement cannot be checked while it is there.
        if occluded is not None and occluded > 0.5: return out, 'occluded'
        last = out[-1]['quad'] if out else quad_ref
        if float(np.linalg.norm(quad - last, axis=1).max()) > 0.25 * float(np.linalg.norm(quad_ref[2] - quad_ref[0])): return out, 'jump'
        out.append({'t': t, 'quad': quad, 'inlier_ratio': round(ratio, 3), 'rmse': round(rmse, 3), 'ncc': _ncc(ref_patch, patch),
                    'occluded': None if occluded is None else round(occluded, 3), 'visible': round(vis, 3)})
        src_ref, cur = src[inl], dst[inl]
        if len(cur) < max(20, 0.4 * initial):
            fresh = _features(gray, quad)
            if len(fresh):
                d = np.min(np.linalg.norm(fresh[:, None] - cur[None], axis=2), axis=1) if len(cur) else np.full(len(fresh), 99.0)
                fresh = fresh[d > 4]
                if len(fresh):
                    back_ref = cv2.perspectiveTransform(fresh.reshape(-1, 1, 2), np.linalg.inv(H)).reshape(-1, 2)
                    src_ref = np.vstack([src_ref, back_ref]).astype(np.float32); cur = np.vstack([cur, fresh]).astype(np.float32)
        prev = gray; k += direction
    return out, 'scene_end'


def follow(source, start, end, ref_second, quads_norm, step_seconds=0.12):
    """Follow planar quads (normalised corners at `ref_second`) through the scene [start, end) by homography.

    Returns, per quad, the contiguous run in which it was tracked, with evidence:
      samples      [{t, quad (normalised), inlier_ratio, rmse, ncc, occluded, visible}]
      planarity    median RANSAC inlier ratio of the points on the surface. A deforming surface, or one that is not a plane,
                   scores low, and below 0.5 the run stops
      jitter       RMS second difference of the corner paths relative to the quad's diagonal: how smoothly the surface moves
      stability    planarity x (1 - jitter / 0.1), clipped to [0, 1] (0.1: a tenth of the diagonal per sample, a violent shake)
      occlusion    the largest fraction of the surface's textured cells that stopped matching the reference (0 when none)
      stopped      why the run ended before and after the reference: scene_end, lost, left_frame, deformed, not_planar,
                   occluded (more than half the surface hidden), jump (corners moved over a quarter of the diagonal in one
                   step), insufficient_features
    Nothing is extrapolated: a frame where tracking failed ends the run."""
    frames, _ = _scene_frames(source, start, end, step_seconds)
    if not frames: return [{'status': 'NO_FRAMES'} for _ in quads_norm]
    h, w = frames[0][1].shape
    ref_k = min(range(len(frames)), key=lambda i: abs(frames[i][0] - ref_second))
    results = []
    for qn in quads_norm:
        quad_ref = np.asarray(qn, np.float64) * np.array([w, h])
        back, why_back = _follow_one(frames, ref_k, quad_ref, -1)
        fwd, why_fwd = _follow_one(frames, ref_k, quad_ref, +1)
        if why_back == 'insufficient_features':
            results.append({'status': 'INSUFFICIENT_FEATURES', 'stopped': {'before': why_back, 'after': why_fwd}}); continue
        ref_rec = {'t': frames[ref_k][0], 'quad': quad_ref, 'inlier_ratio': 1.0, 'rmse': 0.0, 'ncc': 1.0, 'occluded': 0.0,
                   'visible': round(_visible(quad_ref, w, h), 3)}
        run = list(reversed(back)) + [ref_rec] + fwd
        corners = np.array([r['quad'] for r in run]) / math.hypot(w, h)
        jitter = float(np.sqrt((np.diff(corners, n=2, axis=0) ** 2).sum(axis=2).mean())) if len(run) >= 3 else 0.0
        diag_q = float(np.linalg.norm(quad_ref[2] - quad_ref[0])) / math.hypot(w, h)
        jitter_rel = jitter / max(diag_q, 1e-3)
        planarity = float(np.median([r['inlier_ratio'] for r in run if r is not ref_rec] or [1.0]))
        occ = [r['occluded'] for r in run if r['occluded'] is not None]
        step = float(np.median(np.diff([r['t'] for r in run]))) if len(run) > 1 else step_seconds
        results.append({
            'status': 'TRACKED', 'algorithm': TRACK_ALGORITHM, 'start': run[0]['t'], 'end': round(min(end, run[-1]['t'] + step), 3),
            'samples': [dict(r, quad=(np.asarray(r['quad']) / np.array([w, h])).round(5).tolist()) for r in run],
            'planarity': round(planarity, 3), 'jitter': round(jitter_rel, 4),
            'stability': round(max(0.0, min(1.0, planarity * (1 - min(1.0, jitter_rel / 0.1)))), 3),
            'occlusion': round(max(occ), 3) if occ else 0.0, 'stopped': {'before': why_back, 'after': why_fwd},
            'tracked_samples': len(run)})
    return results
