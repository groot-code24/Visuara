"""TRACKED_3D: render a validated GLB product standing on a tracked planar surface.

Pipeline: tracked surface (tracking.py) -> camera intrinsics and per-frame pose (camera.py) -> object placed
in surface coordinates -> software rasterisation (2x supersampled; faces ordered far-to-near after back-face
culling, adequate for closed product meshes) -> linear-light Lambert + Blinn shading -> cast and contact
shadows projected onto the surface and clipped to it -> composite.

Plane frame: origin at the surface's top-left corner, +x along TL->TR, +y along TL->BL, +z = x * y,
which points *into* the surface; "up" (out of the surface, towards the camera side) is -z.
glTF (+Y up, +Z front) maps to plane (x, y, z) = (gx, gz, -gy).
"""
import json
from pathlib import Path
import cv2
import numpy as np

from .assets3d import load_glb
from .camera import estimate_camera, solve_poses, surface_corners
from .compositor import srgb_to_linear, linear_to_srgb, temporal_gains, product_mask

SS = 2
DEFAULT_LIGHT = {'azimuth_deg': 135.0, 'elevation_deg': 55.0}


def place_object(mesh, anchor_uv, surface_wh, yaw_deg=0.0, scale=1.0):
    """Object vertices and normals in plane coordinates, base on the surface at the anchor."""
    G2P = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], np.float64)
    V = mesh['positions'].astype(np.float64) @ G2P.T * scale
    N = mesh['normals'].astype(np.float64) @ G2P.T
    V[:, 0] -= (V[:, 0].min() + V[:, 0].max()) / 2
    V[:, 1] -= (V[:, 1].min() + V[:, 1].max()) / 2
    V[:, 2] -= V[:, 2].max()                     # lowest point (largest z) touches the surface
    a = np.radians(yaw_deg); Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    V = V @ Rz.T; N = N @ Rz.T
    V[:, 0] += anchor_uv[0] * surface_wh[0]; V[:, 1] += anchor_uv[1] * surface_wh[1]
    return V, N


def light_dir(light):
    az, el = np.radians(light['azimuth_deg']), np.radians(light['elevation_deg'])
    return np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), -np.sin(el)])   # towards the light


def footprint(V, height):
    base = V[V[:, 2] > -0.02 * height][:, :2]
    hull = cv2.convexHull(base.astype(np.float32)).reshape(-1, 2)
    c = hull.mean(axis=0)
    return c + (hull - c) * 1.15


def _project(P, R, t, K):
    Xc = P @ R.T + t
    uv = Xc @ K.T
    return uv[:, :2] / uv[:, 2:3], Xc


def _bilinear(img, x, y):
    x0 = np.clip(np.floor(x).astype(np.int64), 0, img.shape[1] - 2); y0 = np.clip(np.floor(y).astype(np.int64), 0, img.shape[0] - 2)
    fx = (x - x0)[:, None]; fy = (y - y0)[:, None]
    return (img[y0, x0] * (1 - fx) * (1 - fy) + img[y0, x0 + 1] * fx * (1 - fy) + img[y0 + 1, x0] * (1 - fx) * fy + img[y0 + 1, x0 + 1] * fx * fy).astype(np.float32)


def _fill(shape, tris, values=None):
    """Rasterise triangles (pixel coords, float) into a float32 buffer; later triangles overwrite earlier ones."""
    buf = np.zeros(shape, np.float32)
    pts = np.round(tris * 16).astype(np.int32)
    for k, tri in enumerate(pts):
        cv2.fillConvexPoly(buf, tri, float(values[k]) if values is not None else 1.0, cv2.LINE_8, 4)
    return buf


class Tracked3DPlan:
    def __init__(self, mesh, V, N, K, poses, surface_quads, interval, light, light_source, gains=None, adjust=None):
        self.mesh, self.V, self.N, self.K, self.poses = mesh, V, N, K, poses
        self.surface_quads, self.gains = surface_quads, gains or {}
        s, e = interval
        self.indices = {i for i in poses if s <= i <= e}
        self.L = light_dir(light)
        self.height = float(-V[:, 2].min())
        self.cast_opacity = .45 if light_source == 'reviewer' else .25
        self.contact_opacity = .55
        g = 2.0 ** float((adjust or {}).get('exposure_ev', 0)); tmp = float((adjust or {}).get('temperature', 0))
        self.adjust = np.array([1 - .2 * tmp, 1.0, 1 + .2 * tmp]) * g
        self.foot = footprint(V, self.height)
        self._cache = {}
        fn = np.cross(V[mesh['faces'][:, 1]] - V[mesh['faces'][:, 0]], V[mesh['faces'][:, 2]] - V[mesh['faces'][:, 0]])
        self.face_n = fn / np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)

    # ---------------------------------------------------------------- layers
    def _layers(self, idx, shape):
        if idx in self._cache:
            return self._cache[idx]
        h, w = shape[:2]
        pose = self.poses[idx]; R, t, K = pose['R'], pose['t'], self.K
        uv, Xc = _project(self.V, R, t, K)
        if (Xc[:, 2] <= 1e-3).any():
            raise RuntimeError('Object is behind the camera on frame %d' % idx)
        # Shadow geometry on the surface (z = 0).
        tcoef = self.V[:, 2] / self.L[2]
        S = self.V - tcoef[:, None] * self.L[None, :]
        suv, _ = _project(S, R, t, K)
        fuv, _ = _project(np.c_[self.foot, np.zeros(len(self.foot))], R, t, K)
        size = max(np.ptp(uv[:, 1]), 4.0)
        pad = int(size * .25) + 4
        allp = np.vstack([uv, suv, fuv])
        x0, y0 = np.floor(allp.min(axis=0)).astype(int) - pad; x1, y1 = np.ceil(allp.max(axis=0)).astype(int) + pad
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
        if x1 <= x0 or y1 <= y0:
            self._cache[idx] = None; return None
        W, H = (x1 - x0) * SS, (y1 - y0) * SS
        off = np.array([x0, y0], np.float64)
        F = self.mesh['faces']
        # Object: back-face culling, then far-to-near order.
        cen = Xc[F].mean(axis=1)
        nc = self.face_n @ R.T
        front = (nc * cen).sum(axis=1) < 0
        order = np.where(front)[0]; order = order[np.argsort(-cen[order, 2])]
        tris = (uv[F[order]] - off) * SS
        fid = _fill((H, W), tris, order + 1.0)
        cov = fid > 0
        color = np.zeros((H, W, 3), np.float32)
        if cov.any():
            ys, xs = np.nonzero(cov); f = fid[ys, xs].astype(np.int64) - 1
            p = (np.c_[xs, ys] + .5) / SS + off
            a, b, c = (uv[F[f, k]] for k in range(3))
            den = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
            den = np.where(np.abs(den) < 1e-12, 1e-12, den)
            l0 = ((b[:, 1] - c[:, 1]) * (p[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (p[:, 1] - c[:, 1])) / den
            l1 = ((c[:, 1] - a[:, 1]) * (p[:, 0] - c[:, 0]) + (a[:, 0] - c[:, 0]) * (p[:, 1] - c[:, 1])) / den
            lam = np.clip(np.c_[l0, l1, 1 - l0 - l1], 0, 1)
            iz = lam / Xc[F[f], 2]                                  # perspective-correct weights
            wgt = iz / iz.sum(axis=1, keepdims=True)
            nrm = (wgt[:, :, None] * (self.N[F[f]] @ R.T)).sum(axis=1)
            nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
            pos = (wgt[:, :, None] * Xc[F[f]]).sum(axis=1)
            tex_uv = (wgt[:, :, None] * self.mesh['uv'][F[f]]).sum(axis=1)
            albedo = np.zeros((len(f), 3), np.float32); rough = np.ones(len(f), np.float32)
            fm = self.mesh['face_material'][f]
            for mi, mat in enumerate(self.mesh['materials']):
                sel = fm == mi
                if not sel.any(): continue
                base = np.asarray(mat['base_color'][:3], np.float32)[::-1]       # RGB -> BGR
                if mat['texture'] is not None and self.mesh['has_uv']:
                    tex = mat['texture']; th, tw = tex.shape[:2]
                    tu = (tex_uv[sel, 0] % 1.0) * (tw - 1); tv = (tex_uv[sel, 1] % 1.0) * (th - 1)
                    albedo[sel] = _bilinear(srgb_to_linear(tex[:, :, :3]), tu, tv) * base
                else:
                    albedo[sel] = base
                rough[sel] = mat['roughness']
            Lc = self.L @ R.T
            ndl = np.clip(nrm @ Lc, 0, None)
            view = -pos / np.maximum(np.linalg.norm(pos, axis=1, keepdims=True), 1e-9)
            hv = view + Lc; hv /= np.maximum(np.linalg.norm(hv, axis=1, keepdims=True), 1e-9)
            shin = np.clip(2 / np.maximum(rough, .05) ** 2 - 2, 1, 512)
            spec = .04 * np.clip((nrm * hv).sum(axis=1), 0, None) ** shin * (ndl > 0)
            shade = albedo * (.35 + .75 * ndl)[:, None] + spec[:, None]
            color[ys, xs] = shade
        # Shadows (cast: projected silhouette; contact: footprint), blurred and clipped to the surface.
        cast = _fill((H, W), (suv[F] - off) * SS)
        contact = np.zeros((H, W), np.float32)
        cv2.fillConvexPoly(contact, np.round((fuv - off) * SS * 16).astype(np.int32), 1.0, cv2.LINE_8, 4)
        sig = max(1.0, size * SS * .04)
        cast = cv2.GaussianBlur(cast, (0, 0), sig * 1.5) * self.cast_opacity
        contact = cv2.GaussianBlur(contact, (0, 0), sig) * self.contact_opacity
        shadow = 1 - (1 - cast) * (1 - contact)
        surf = np.zeros((H, W), np.float32)
        cv2.fillConvexPoly(surf, np.round((np.asarray(self.surface_quads[idx]) - off) * SS * 16).astype(np.int32), 1.0, cv2.LINE_8, 4)
        shadow *= surf
        # Downsample (area) = anti-aliased coverage.
        ds = lambda img: cv2.resize(img, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
        alpha = ds(cov.astype(np.float32))
        gain = np.asarray(self.gains.get(idx, (1, 1, 1)), np.float32) * self.adjust.astype(np.float32)
        layer = (x0, y0, x1, y1, ds(color) * gain, alpha[:, :, None], ds(shadow)[:, :, None])
        self._cache = {idx: layer}            # keep only the current frame (render and validation visit frames in order)
        return layer

    def apply(self, idx, frame):
        if idx not in self.indices: return frame
        L = self._layers(idx, frame.shape)
        if L is None: return frame
        x0, y0, x1, y1, col, a, sh = L
        out = frame.copy()
        lin = srgb_to_linear(out[y0:y1, x0:x1]) * (1 - sh)
        out[y0:y1, x0:x1] = linear_to_srgb(lin * (1 - a) + col).round().clip(0, 255).astype(np.uint8)
        return out

    def mask(self, idx, shape):
        L = self._layers(idx, shape) if idx in self.indices else None
        m = np.zeros(shape[:2], np.uint8)
        if L is not None:
            x0, y0, x1, y1, _c, a, sh = L
            m[y0:y1, x0:x1] = (((a[:, :, 0] > .02) | (sh[:, :, 0] > .02)) * 255).astype(np.uint8)
        return m

    def object_rois(self, w, h):
        """Per-frame pixel bounding box of the projected object, padded by 10% (for the foreground-motion check)."""
        out = {}
        for i, p in self.poses.items():
            uv, _ = _project(self.V, p['R'], p['t'], self.K)
            lo, hi = uv.min(axis=0), uv.max(axis=0); pad = (hi - lo) * .1
            out[i] = [int(max(0, lo[0] - pad[0])), int(max(0, lo[1] - pad[1])), int(min(w, hi[0] + pad[0])), int(min(h, hi[1] + pad[1]))]
        return out

    def object_visibility(self, w, h):
        """Per-frame fraction of the object's projected bounding box inside the frame, and its pixel height."""
        out = {}
        for i, p in self.poses.items():
            uv, _ = _project(self.V, p['R'], p['t'], self.K)
            lo, hi = uv.min(axis=0), uv.max(axis=0)
            area = max(float((hi - lo).prod()), 1e-9)
            ilo, ihi = np.maximum(lo, 0), np.minimum(hi, [w, h])
            out[i] = (float((ihi - ilo).clip(0).prod()) / area, float(hi[1] - lo[1]))
        return out

    def object_quads(self):
        """Per-frame image quad of the object's footprint bounding box (for visibility checks)."""
        lo, hi = self.foot.min(axis=0), self.foot.max(axis=0)
        fb = np.array([[lo[0], lo[1], 0], [hi[0], lo[1], 0], [hi[0], hi[1], 0], [lo[0], hi[1], 0]])
        return {i: _project(fb, p['R'], p['t'], self.K)[0].tolist() for i, p in self.poses.items()}


def model_options(opts):
    m = opts.get('model3d') or {}
    return {'anchor_uv': m.get('anchor_uv', [.5, .5]), 'surface_size_m': m['surface_size_m'], 'yaw_deg': float(m.get('yaw_deg', 0)),
            'scale': float(m.get('scale', 1.0)), 'light': m.get('light') or DEFAULT_LIGHT,
            'light_source': 'reviewer' if m.get('light') else 'default', 'camera_hfov_deg': m.get('camera_hfov_deg')}


def prepare_3d(track, model_path, opts, start_idx, end_idx):
    """Shared by the quality check and the render: camera, poses, placed geometry. Returns (plan or None, camera_info, poses)."""
    mo = model_options(opts)
    mesh, _ = load_glb(Path(model_path).read_bytes())
    W, H = mo['surface_size_m']
    cam, K = estimate_camera(track, W, H, start_idx, end_idx, mo['camera_hfov_deg'])
    if K is None:
        return None, cam, {}, mo
    poses = solve_poses(track, K, W, H, start_idx, end_idx)
    V, N = place_object(mesh, mo['anchor_uv'], (W, H), mo['yaw_deg'], mo['scale'])
    gains, gsum = temporal_gains(track, start_idx, end_idx) if opts.get('color_match', True) else ({}, {'applied': False})
    cam['color_match'] = gsum
    plan = Tracked3DPlan(mesh, V, N, K, poses, surface_corners(track, start_idx, end_idx), (start_idx, end_idx),
                         mo['light'], mo['light_source'], gains, opts.get('adjust'))
    return plan, cam, poses, mo


def build_3d_plan(track, report, opts, prod, summary):
    s, e = report['interval']['start_frame'], report['interval']['end_frame']
    plan, cam, _p, mo = prepare_3d(track, prod['model_path'], opts, s, e)
    if plan is None:
        raise RuntimeError('Camera could not be estimated for this placement')
    summary.update(camera={k: cam.get(k) for k in ('focal_px', 'focal_source', 'focal_rel_std', 'hfov_deg')}, light=mo['light'],
                   light_source=mo['light_source'], color_match=cam.get('color_match'))
    return plan, summary


def foreground_motion(source, rois, start_idx, end_idx, diff_threshold=30):
    """Per interval frame: fraction of the object ROI that changes after compensating the dominant camera motion.

    Dominant motion is a full-frame homography: exact for planar scenes; strong parallax raises the value,
    which errs towards rejection."""
    cap = cv2.VideoCapture(str(source)); prev = None; idx = 0; out = {}
    while idx <= end_idx:
        ok, frame = cap.read()
        if not ok: break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if prev is not None and idx >= start_idx and idx in rois:
            val = 1.0
            pts = cv2.goodFeaturesToTrack(prev, 600, .01, 6)
            if pts is not None and len(pts) >= 12:
                nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, gray, pts, None)
                good = st.ravel() == 1
                if good.sum() >= 12:
                    Hm, _ = cv2.findHomography(pts[good], nxt[good], cv2.RANSAC, 3.0)
                    if Hm is not None:
                        warped = cv2.warpPerspective(prev, Hm, (gray.shape[1], gray.shape[0]))
                        x0, y0, x1, y1 = rois[idx]
                        d = cv2.GaussianBlur(cv2.absdiff(gray, warped), (5, 5), 0)[y0:y1, x0:x1]
                        val = float((d > diff_threshold).mean()) if d.size else 0.0
            out[idx] = round(val, 5)
        prev = gray; idx += 1
    cap.release()
    return out


def _object_depth(plan, idx, shape):
    """Per-pixel camera depth of the object's nearest visible surface (0 where the object is absent)."""
    p = plan.poses[idx]
    uv, Xc = _project(plan.V, p['R'], p['t'], plan.K)
    F = plan.mesh['faces']
    cen = Xc[F].mean(axis=1)
    front = ((plan.face_n @ p['R'].T) * cen).sum(axis=1) < 0
    order = np.where(front)[0]; order = order[np.argsort(-cen[order, 2])]     # far to near: nearer faces overwrite
    return _fill(shape, uv[F[order]], Xc[F[order]][:, :, 2].min(axis=1))


def plane_tolerance(reproj_i, reproj_j, plane_px=1.0, cap_px=2.0):
    """Pixel tolerance for 'flow agrees with the pose-derived plane homography' between two frames.

    The prediction carries the error of both solved poses (their surface-corner reprojection RMS) on top of the
    flow noise allowed by plane_px. Independent errors add in quadrature. The cap equals the threshold above which a
    point counts as clearly off the plane, so the tolerance can never absorb a candidate occluder. V9 used plane_px alone."""
    return float(min(cap_px, np.sqrt(plane_px ** 2 + reproj_i ** 2 + reproj_j ** 2)))


def scene_consistency(source, plan, start_idx, end_idx, gap=3, diff_threshold=30, plane_px=1.0, fb_px=1.0, ep_px=1.5,
                      min_parallax_deg=0.25, depth_margin=0.03, occluder_plane_px=2.0, occluder_height_m=0.01, pose_aware_plane=True):
    """Parallax- and occlusion-aware check of the space around the 3D object, using the solved camera.

    For frame i and a partner frame j (i + gap, else i - gap), dense forward/backward flow gives correspondences
    around the object. Each pixel is explained, in order, as: on the tracked plane (flow agrees with the pose-derived
    plane homography); or rigid scene off the plane (flow satisfies the epipolar constraint of the known camera
    motion), triangulated to a depth. Anything else is unexplained. Unexplained pixels that also change
    photometrically count as foreground motion. Rigid pixels nearer to the camera than the object, inside its
    silhouette, count as occluders. Rigid points behind the object (walls, background parallax) are explained and do
    not count. Unreliable flow is never explained, so the check errs towards rejection.
    Limits: an object moving along the epipolar lines at a rate that mimics a static point cannot be told apart from
    one (a fundamental monocular ambiguity); untextured occluders give no flow and are not detected.

    Returns {'moving': {idx: fraction of the object ROI}, 'occluded': {idx: fraction of the object silhouette}, ...}.
    """
    from collections import deque
    poses, K = plan.poses, plan.K
    Kinv = np.linalg.inv(K)
    h_of = lambda p: K @ np.c_[p['R'][:, 0], p['R'][:, 1], p['t']]
    cap = cv2.VideoCapture(str(source))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    rois = plan.object_rois(w, h)
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    moving, occluded, breakdown, done, tolerances = {}, {}, {}, set(), []

    def analyse(i, gi, j, gj, k=None, gk=None):
        pi, pj = poses[i], poses[j]
        x0, y0, x1, y1 = rois[i]
        if x1 - x0 < 4 or y1 - y0 < 4: return
        m = 24 + gap * 6                                            # crop margin for the flow (covers inter-frame motion)
        cx0, cy0, cx1, cy1 = max(0, x0 - m), max(0, y0 - m), min(w, x1 + m), min(h, y1 + m)
        a, b = np.ascontiguousarray(gi[cy0:cy1, cx0:cx1]), np.ascontiguousarray(gj[cy0:cy1, cx0:cx1])
        fwd = dis.calc(a, b, None); bwd = dis.calc(b, a, None)
        ys, xs = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        px = np.stack([xs.ravel(), ys.ravel()], 1)                 # pixels of the ROI in frame i
        lx, ly = (px[:, 0] - cx0).astype(int), (px[:, 1] - cy0).astype(int)
        q = px + fwd[ly, lx]                                        # correspondence in frame j
        qx = np.clip(np.round(q[:, 0] - cx0).astype(int), 0, cx1 - cx0 - 1); qy = np.clip(np.round(q[:, 1] - cy0).astype(int), 0, cy1 - cy0 - 1)
        back = q + bwd[qy, qx]
        inside = (q[:, 0] >= cx0) & (q[:, 0] < cx1 - 1) & (q[:, 1] >= cy0) & (q[:, 1] < cy1 - 1)
        reliable = inside & (np.linalg.norm(back - px, axis=1) < fb_px)
        # Plane: pose-derived homography i -> j (exact for the tracked surface, whatever else dominates the view).
        Hij = h_of(pj) @ np.linalg.inv(h_of(pi))
        hp = cv2.perspectiveTransform(px.reshape(-1, 1, 2).astype(np.float64), Hij).reshape(-1, 2)
        tol = plane_tolerance(pi.get('reproj_px', 0.0), pj.get('reproj_px', 0.0), plane_px, occluder_plane_px) if pose_aware_plane else plane_px
        tolerances.append(tol)
        on_plane = reliable & (np.linalg.norm(q - hp, axis=1) < tol)
        # Photometric change after compensating the plane (frame j sampled at H(p)).
        warped = cv2.warpPerspective(gj, Hij, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
        resid = cv2.GaussianBlur(cv2.absdiff(gi, warped), (5, 5), 0)[y0:y1, x0:x1].ravel() > diff_threshold
        # Rigid off-plane points satisfy the epipolar constraint of the known relative motion.
        Rij = pj['R'] @ pi['R'].T; tij = pj['t'] - Rij @ pi['t']
        tx = np.array([[0, -tij[2], tij[1]], [tij[2], 0, -tij[0]], [-tij[1], tij[0], 0]])
        Fm = Kinv.T @ tx @ Rij @ Kinv
        ph = np.c_[px, np.ones(len(px))]; qh = np.c_[q, np.ones(len(q))]
        lines = ph @ Fm.T
        ep = np.abs((qh * lines).sum(1)) / np.maximum(np.linalg.norm(lines[:, :2], axis=1), 1e-9)
        rigid = reliable & ~on_plane & (ep < ep_px)
        depth = np.full(len(px), np.nan); height = np.full(len(px), np.nan); Xall = np.full((len(px), 3), np.nan)
        if rigid.any():
            Pi = K @ np.c_[pi['R'], pi['t']]; Pj = K @ np.c_[pj['R'], pj['t']]
            X = cv2.triangulatePoints(Pi, Pj, px[rigid].T.astype(np.float64), q[rigid].T.astype(np.float64))
            X = (X[:3] / X[3]).T
            ci = X @ pi['R'].T + pi['t']; cj = X @ pj['R'].T + pj['t']
            cosang = (ci * cj).sum(1) / np.maximum(np.linalg.norm(ci, axis=1) * np.linalg.norm(cj, axis=1), 1e-12)
            ok = (ci[:, 2] > 0) & (cj[:, 2] > 0) & (np.degrees(np.arccos(np.clip(cosang, -1, 1))) >= min_parallax_deg)
            # A point seen through the tracked surface cannot lie beneath it (z > 0 is behind the surface).
            sm = np.zeros((h, w), np.uint8)
            cv2.fillConvexPoly(sm, np.round(np.asarray(plan.surface_quads[i], np.float64) * 16).astype(np.int32), 1, cv2.LINE_8, 4)
            through = sm[px[rigid, 1].astype(int), px[rigid, 0].astype(int)] > 0
            ok &= ~(through & (X[:, 2] > .005))
            d = np.full(int(rigid.sum()), np.nan); d[ok] = ci[ok, 2]; depth[rigid] = d
            hh = np.full(int(rigid.sum()), np.nan); hh[ok] = -X[ok, 2]; height[rigid] = hh     # height above the surface (m)
            Xall[rigid] = X
        explained = on_plane | ~np.isnan(depth)
        mv = resid & ~explained
        moving[i] = round(float(mv.mean()), 5)
        breakdown[i] = {'unreliable_flow': round(float((mv & ~reliable).mean()), 5),
                        'independent_motion': round(float((mv & reliable & ~on_plane & (ep >= ep_px)).mean()), 5),
                        'unresolved_depth': round(float((mv & rigid).mean()), 5)}
        zobj = _object_depth(plan, i, (h, w))[y0:y1, x0:x1].ravel()
        sil = zobj > 0
        # Occluder: clearly off the plane, physically above the surface, and nearer than the object along the ray.
        clear = np.linalg.norm(q - hp, axis=1) > occluder_plane_px
        occ = sil & clear & ~np.isnan(depth) & (height > occluder_height_m) & (depth < zobj * (1 - depth_margin))
        if occ.any() and k is not None:
            # Third view: a static point triangulated from (i, j) must reproject onto its flow match in frame k.
            c = np.ascontiguousarray(gk[cy0:cy1, cx0:cx1]); fk = dis.calc(a, c, None)
            qk = px[occ] + fk[ly[occ], lx[occ]]
            pk = poses[k]; uk, _ = _project(Xall[occ], pk['R'], pk['t'], K)
            agree = np.linalg.norm(uk - qk, axis=1) < ep_px
            occ[np.where(occ)[0][~agree]] = False
        occluded[i] = round(float(occ.sum() / max(int(sil.sum()), 1)), 5)

    buf, idx = deque(maxlen=2 * gap + 1), 0
    last = end_idx + 2 * gap
    while idx <= last:
        ok, frame = cap.read()
        if not ok: break
        buf.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        i = idx - 2 * gap                                            # partners ahead: (i, i + gap, i + 2 gap)
        if len(buf) == 2 * gap + 1 and start_idx <= i <= end_idx and all(x in poses for x in (i, i + gap, idx)) and i in rois:
            analyse(i, buf[0], i + gap, buf[gap], idx, buf[-1]); done.add(i)
        idx += 1
    cap.release()
    # Frames without partners ahead (end of the video, or untracked): use partners behind.
    need = sorted(i for i in range(start_idx, end_idx + 1) if i not in done and i in poses and i in rois and (i - gap) in poses)
    if need:
        want = set(need) | {i - gap for i in need} | {i - 2 * gap for i in need}; cache = {}
        cap = cv2.VideoCapture(str(source)); f = 0
        while f <= max(want):
            ok, frame = cap.read()
            if not ok: break
            if f in want: cache[f] = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            f += 1
        cap.release()
        for i in need:
            if i in cache and i - gap in cache:
                third = i - 2 * gap if (i - 2 * gap) in cache and (i - 2 * gap) in poses else None
                analyse(i, cache[i], i - gap, cache[i - gap], third, cache.get(third))
    return {'moving': moving, 'occluded': occluded, 'breakdown': breakdown, 'frames_analysed': len(moving), 'method': 'pose-epipolar-depth-3view-v2' + ('-pose-aware-plane' if pose_aware_plane else '-fixed-plane'),
            'plane_tolerance_px': {'min': round(min(tolerances), 3), 'max': round(max(tolerances), 3)} if tolerances else None,
            'params': {'gap': gap, 'diff_threshold': diff_threshold, 'plane_px': plane_px, 'pose_aware_plane': pose_aware_plane, 'fb_px': fb_px, 'ep_px': ep_px,
                       'min_parallax_deg': min_parallax_deg, 'depth_margin': depth_margin, 'occluder_plane_px': occluder_plane_px,
                       'occluder_height_m': occluder_height_m}}
