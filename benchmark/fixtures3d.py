"""Synthetic 3D fixtures: a real pinhole camera (known K, R, t) filming a textured tabletop.

Ground truth: the GLB product rendered with the TRUE camera and composited; the pipeline under test must
estimate the camera from the tracked surface alone. Labels are assigned by construction before evaluation.
These scenes share the renderer's shading model, so they measure geometry (camera, pose, placement and
temporal stability), not real-world lighting realism.
"""
import subprocess
from pathlib import Path
import cv2
import numpy as np

from benchmark.fixtures import _texture

W, H, FPS, SECONDS = 640, 360, 25, 3.2
F_TRUE = 520.0
TEX_M_PER_PX = 0.0008                         # table texture: 1200 x 900 px = 0.96 x 0.72 m
SURFACE = (0.30, 0.22, 0.36, 0.27)            # x, y, width, height (m) of the textured mat the product stands on
REF_S, START_S, END_S = 0.4, 0.4, 2.8

KINDS3D = {
    'orbit':         (True,  None, 'Camera sweeps sideways around the table'),
    'dolly':         (True,  None, 'Camera moves closer while panning'),
    'handheld3d':    (True,  None, 'Sideways sweep with handheld jitter'),
    'topdown_user_fov': (True, None, 'Top-down translating camera; reviewer supplies the field of view'),
    'topdown':       (False, 'FOCAL_UNOBSERVABLE', 'Top-down translating camera, no field of view supplied'),
    'occluder3d':    (False, 'FOREGROUND_MOTION_IN_OBJECT_REGION', 'An object passes through the space where the product stands'),
    'off_surface':   (False, 'OBJECT_OFF_SURFACE', 'Anchor places the product partly off the mat'),
    'cut3d':         (False, 'SCENE_CUT_IN_INTERVAL', 'Scene cut inside the interval'),
    # V9: scenes with real depth structure (a back wall behind the table) for the parallax/occlusion-aware check.
    'orbit_wall':    (True,  None, 'Sideways sweep; a textured wall stands behind the table (true parallax)'),
    'dolly_wall':    (True,  None, 'Dolly-in; a textured wall stands behind the table (true parallax)'),
    'static_occluder': (False, 'OBJECT_OCCLUDED', 'A static card stands on the table between the camera and the product'),
    'occluder_wall': (False, 'FOREGROUND_MOTION_IN_OBJECT_REGION', 'An object passes in front of the product; wall behind (motion plus parallax)'),
}
# Per-kind RNG streams are fixed so adding kinds never changes existing fixtures.
_RNG_INDEX = {k: i for i, k in enumerate(sorted(['orbit', 'dolly', 'handheld3d', 'topdown_user_fov', 'topdown', 'occluder3d', 'off_surface', 'cut3d']))}
_RNG_INDEX.update({'orbit_wall': 8, 'dolly_wall': 9, 'static_occluder': 10, 'occluder_wall': 11})
TABLE_M = (0.96, 0.72)                        # the textured table top (x, y extents, metres)
WALL_Y = -0.06                                # vertical back wall plane, just behind the table's far edge
CARD = (0.12, 0.08, 0.22)                     # static occluder: offset toward the camera (m), width, height (stands off the mat)
K_TRUE = np.array([[F_TRUE, 0, W / 2], [0, F_TRUE, H / 2], [0, 0, 1]])


def _look_at(C, target):
    up = np.array([0, 0, -1.0])
    f = target - C; f /= np.linalg.norm(f)
    x = np.cross(f, up); x /= np.linalg.norm(x)
    y = np.cross(f, x)
    R = np.stack([x, y, f])
    return R, -R @ C


def camera_pose(kind, t, jitter):
    if kind.startswith('topdown'):
        C = np.array([.48 + .06 * t - .1, .36 + .02 * np.sin(t), -.62])
        R = np.eye(3)                                            # looking straight down (+z is into the table)
        return R, -R @ C
    tgt = np.array([.48, .40, 0.0])
    if kind.startswith('dolly'):
        C = np.array([.48 + .12 * np.sin(.7 * t), 1.05 - .09 * t, -.45 + .03 * t])
    else:
        C = np.array([.48 + .22 * np.sin(.8 * t - .6), 1.0 + .05 * np.cos(.8 * t), -.45])
    R, tv = _look_at(C, tgt)
    if kind == 'handheld3d':
        a = np.radians(jitter)
        Rx = cv2.Rodrigues(np.array([a[0], a[1], 0.0]))[0]
        R = Rx @ R; tv = Rx @ tv
    return R, tv


def plane_to_image(R, t):
    s = TEX_M_PER_PX
    return K_TRUE @ np.c_[R[:, 0] * s, R[:, 1] * s, t]


def generate3d(kind, seed, out_dir, model_path, object_opts=None):
    """Write <kind>_s<seed>.mp4 and its ground truth composite; returns metadata including true poses."""
    from app.assets3d import load_glb
    from app.render3d import Tracked3DPlan, place_object, DEFAULT_LIGHT
    rng = np.random.default_rng(seed * 100 + _RNG_INDEX[kind])
    tex = _texture(rng, (1200, 900), 2.5)
    mat = _texture(rng, (int(SURFACE[2] / TEX_M_PER_PX), int(SURFACE[3] / TEX_M_PER_PX)), 1.6)
    x0, y0 = int(SURFACE[0] / TEX_M_PER_PX), int(SURFACE[1] / TEX_M_PER_PX)
    tex[y0:y0 + mat.shape[0], x0:x0 + mat.shape[1]] = mat
    tex_b = _texture(np.random.default_rng(seed + 991), (1200, 900), 2.5)
    n = int(SECONDS * FPS)
    jitter = np.cumsum(rng.normal(0, .12, (n, 2)), axis=0); jitter -= cv2.blur(jitter.reshape(-1, 1, 2).astype(np.float32), (1, 9)).reshape(-1, 2)
    poses = [camera_pose(kind, i / FPS, jitter[i]) for i in range(n)]
    # Surface corners in the surface frame (origin at the mat's top-left), and in table coordinates.
    sx, sy, sw, sh = SURFACE
    corners_table = np.array([[sx, sy, 0], [sx + sw, sy, 0], [sx + sw, sy + sh, 0], [sx, sy + sh, 0]])
    def proj(P, R, tv):
        X = P @ R.T + tv; u = X @ K_TRUE.T; return u[:, :2] / u[:, 2:3]
    # Poses relative to the surface frame: X_table = X_surface + (sx, sy, 0).
    surf_poses = {i: {'R': R, 't': R @ np.array([sx, sy, 0]) + tv, 'reproj_px': 0.0, 'rot_step_deg': 0.0} for i, (R, tv) in enumerate(poses)}
    mesh, _ = load_glb(Path(model_path).read_bytes())
    oo = {'anchor_uv': [.5, .62], 'yaw_deg': 20.0, 'scale': 1.0, **(object_opts or {})}
    if kind == 'off_surface': oo['anchor_uv'] = [.97, .62]
    V, N = place_object(mesh, oo['anchor_uv'], (sw, sh), oo['yaw_deg'], oo['scale'])
    surf_quads = {i: proj(corners_table, R, tv).tolist() for i, (R, tv) in enumerate(poses)}
    gt_plan = Tracked3DPlan(mesh, V, N, K_TRUE, surf_poses, surf_quads, (0, n - 1), DEFAULT_LIGHT, 'reviewer')
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    paths = {'path': out / f'{kind}_s{seed}.mp4', 'gt_path': out / f'{kind}_s{seed}_gt.mp4'}
    procs = {k: subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                                  '-c:v', 'libx264', '-crf', '16', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', str(p)], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
             for k, p in paths.items()}
    occ = _texture(np.random.default_rng(seed + 5), (80, 220), 1.0)[:, :60]
    walled = kind in ('orbit_wall', 'dolly_wall', 'static_occluder', 'occluder_wall')
    if walled:
        wall_tex = _texture(np.random.default_rng(seed + 777), (1500, 1000), 2.0)       # 1.2 x 0.8 m at TEX_M_PER_PX
        card_tex = _texture(np.random.default_rng(seed + 31), (int(CARD[1] / TEX_M_PER_PX), int(CARD[2] / TEX_M_PER_PX)), 1.2)
        table_corners = np.array([[0, 0, 0], [TABLE_M[0], 0, 0], [TABLE_M[0], TABLE_M[1], 0], [0, TABLE_M[1], 0]], float)
        prod_c = (V + np.array([sx, sy, 0])).mean(axis=0)
        cy = prod_c[1] + np.ptp(V[:, 1]) / 2 + CARD[0]                                  # card plane y, between product and camera
        card_corners = np.array([[prod_c[0] - CARD[1] / 2, cy, -CARD[2]], [prod_c[0] + CARD[1] / 2, cy, -CARD[2]],
                                 [prod_c[0] + CARD[1] / 2, cy, 0], [prod_c[0] - CARD[1] / 2, cy, 0]])
    for i, (R, tv) in enumerate(poses):
        t = i / FPS
        src = tex_b if kind == 'cut3d' and t >= 1.6 else tex
        if walled:
            # Painter's order: wall (x, z plane at y = WALL_Y), then the finite table top, then the card.
            s_ = TEX_M_PER_PX
            Hw = K_TRUE @ np.c_[R[:, 0] * s_, R[:, 2] * s_, R[:, 0] * -0.12 + R[:, 2] * -0.70 + R[:, 1] * WALL_Y + tv]
            frame = cv2.warpPerspective(wall_tex, Hw, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            top = cv2.warpPerspective(src, plane_to_image(R, tv), (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            m = np.zeros((H, W), np.uint8); cv2.fillConvexPoly(m, np.round(proj(table_corners, R, tv) * 16).astype(np.int32), 255, cv2.LINE_AA, 4)
            a = (m.astype(np.float32) / 255)[:, :, None]
            frame = (top * a + frame * (1 - a)).round().astype(np.uint8)
            if kind == 'static_occluder':
                Hc = cv2.getPerspectiveTransform(np.float32([[0, 0], [card_tex.shape[1], 0], [card_tex.shape[1], card_tex.shape[0]], [0, card_tex.shape[0]]]),
                                                 proj(card_corners, R, tv).astype(np.float32))
                cw = cv2.warpPerspective(card_tex, Hc, (W, H), flags=cv2.INTER_LINEAR)
                cm = cv2.warpPerspective(np.full(card_tex.shape[:2], 255, np.uint8), Hc, (W, H), flags=cv2.INTER_LINEAR)
                ca = (cm.astype(np.float32) / 255)[:, :, None]
                frame = (cw * ca + frame * (1 - ca)).round().astype(np.uint8)
        else:
            frame = cv2.warpPerspective(src, plane_to_image(R, tv), (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        if kind in ('occluder3d', 'occluder_wall') and 1.0 <= t <= 2.2:
            # A tall object sweeps through the product's location, in front of where the product stands.
            uv = proj(V + np.array([sx, sy, 0]), R, tv); cx = int(uv[:, 0].min() - 60 + (t - 1.0) / 1.2 * (np.ptp(uv[:, 0]) + 120))
            ytop = int(uv[:, 1].min() - 30)
            xa, xb = max(cx, 0), min(cx + 60, W); ya, yb = max(ytop, 0), min(ytop + 220, H)
            if xb > xa and yb > ya: frame[ya:yb, xa:xb] = occ[ya - ytop:yb - ytop, xa - cx:xb - cx]
        procs['path'].stdin.write(frame.tobytes())
        procs['gt_path'].stdin.write(gt_plan.apply(i, frame).tobytes())
    for p in procs.values():
        p.stdin.close(); p.wait()
    ref = round(REF_S * FPS)
    quad_norm = (np.asarray(surf_quads[ref]) / [W, H]).clip(0, 1).tolist()
    suitable, code, desc = KINDS3D[kind]
    model3d = {'anchor_uv': oo['anchor_uv'], 'surface_size_m': [sw, sh], 'yaw_deg': oo['yaw_deg'], 'scale': oo['scale'], 'light': DEFAULT_LIGHT}
    if kind in ('topdown_user_fov', 'static_occluder'):   # static_occluder: FOV supplied so the occlusion check itself is exercised
        model3d['camera_hfov_deg'] = float(np.degrees(2 * np.arctan(W / 2 / F_TRUE)))
    return {'kind': kind, 'seed': seed, 'path': str(paths['path']), 'gt_path': str(paths['gt_path']), 'suitable': suitable, 'expected_code': code,
            'description': desc, 'quad_norm': quad_norm, 'model3d': model3d, 'true_poses': surf_poses, 'gt_plan': gt_plan, 'fps': FPS}
