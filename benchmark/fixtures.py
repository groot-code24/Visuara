"""Synthetic, legally unencumbered video fixtures with per-frame ground-truth surface geometry.

Each clip shows a textured planar "poster" in a textured world, filmed by a virtual camera whose
world->image homography is known for every frame. Labels are assigned by construction BEFORE any
evaluation. Synthetic fixtures do not replace real-world validation (see docs/known-limitations.md).
"""
import copy, subprocess
from pathlib import Path
import cv2
import numpy as np

W, H, FPS, SECONDS = 480, 270, 25, 3.2
WORLD = (960, 540)
POSTER = np.array([[380, 170], [620, 170], [620, 350], [380, 350]], dtype=np.float32)  # world coords
REF_S, START_S, END_S = 0.4, 0.4, 2.8

# kind -> (suitable, expected primary rejection code or None, description)
KINDS = {
    'static':          (True,  None, 'Static camera, textured planar surface (class A)'),
    'pan_zoom':        (True,  None, 'Smooth pan and zoom (class B)'),
    'perspective':     (True,  None, 'Changing viewing angle on a planar surface (class B)'),
    'handheld':        (True,  None, 'Pan with handheld-style jitter (class B)'),
    'sensor_noise':    (True,  None, 'Pan with heavy sensor noise (class B)'),
    'mild_lighting':   (True,  None, '+/-8% global brightness drift (class B)'),
    'cut_after':       (True,  None, 'Scene cut after the placement interval ends'),
    'cut_inside':      (False, 'SCENE_CUT_IN_INTERVAL', 'Scene cut inside the placement interval'),
    'occluder':        (False, 'PARTIAL_OCCLUSION_UNSUPPORTED', 'Opaque object crosses the surface (class C)'),
    'low_texture':     (False, 'LOW_TEXTURE', 'Flat, untextured surface'),
    'deforming':       (False, 'NON_PLANAR_OR_UNSTABLE', 'Surface ripples non-rigidly (class D)'),
    'exits_frame':     (False, 'PRODUCT_OUT_OF_FRAME', 'Surface leaves the frame during the interval'),
    'motion_blur':     (False, 'MOTION_BLUR_UNSUPPORTED', 'Strong motion blur during the interval'),
    'lighting_change': (False, 'LIGHTING_CHANGE_UNSUPPORTED', 'Brightness falls to ~50% during the interval'),
}
# v8 colour-integration kinds: physically motivated linear-light changes (not part of the v6 gate benchmark).
COLOR_KINDS = {
    'exposure_drift': (True, None, 'Linear-light exposure drifts 1.0 -> 0.72 -> 1.0 (class B)'),
    'wb_drift':       (True, None, 'White balance drifts warm/cool by +-15% in linear light (class B)'),
}
ALL_KINDS = {**KINDS, **COLOR_KINDS}


def _texture(rng, size, contrast=1.0, flat=False):
    w, h = size
    if flat:
        base = np.full((h, w, 3), rng.integers(90, 170, 3), dtype=np.float32)
        return (base + rng.normal(0, 1.5, base.shape)).clip(0, 255).astype(np.uint8)
    img = cv2.GaussianBlur(rng.normal(128, 40, (h, w, 3)).astype(np.float32), (0, 0), 6)
    img = img.clip(0, 255).astype(np.uint8)
    for _ in range(int(60 * contrast)):
        color = tuple(int(c) for c in rng.integers(0, 255, 3))
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        r = int(rng.integers(4, max(6, w // 12)))
        shape = rng.integers(0, 3)
        if shape == 0: cv2.circle(img, (x, y), r, color, -1)
        elif shape == 1: cv2.rectangle(img, (x, y), (x + r, y + int(r * 1.4)), color, -1)
        else: cv2.line(img, (x, y), (int(rng.integers(0, w)), int(rng.integers(0, h))), color, 2)
    for _ in range(int(6 * contrast)):
        cv2.putText(img, ''.join(chr(int(c)) for c in rng.integers(65, 90, 5)), (int(rng.integers(0, w - 60)), int(rng.integers(20, h))),
                    cv2.FONT_HERSHEY_SIMPLEX, .8, tuple(int(c) for c in rng.integers(0, 255, 3)), 2)
    return img


def _world(rng, flat_poster=False):
    world = _texture(rng, WORLD, 2.0)
    pw, ph = int(POSTER[1, 0] - POSTER[0, 0]), int(POSTER[2, 1] - POSTER[1, 1])
    poster = _texture(rng, (pw, ph), 1.5, flat=flat_poster)
    cv2.rectangle(poster, (0, 0), (pw - 1, ph - 1), (20, 20, 20), 3)
    x0, y0 = int(POSTER[0, 0]), int(POSTER[0, 1])
    world[y0:y0 + ph, x0:x0 + pw] = poster
    return world


def _camera(kind, t, rng_jitter):
    """Image positions of the world corners at time t (seconds) -> world->image homography."""
    wc = np.array([[0, 0], [WORLD[0], 0], [WORLD[0], WORLD[1]], [0, WORLD[1]]], dtype=np.float32)
    s = 0.5   # base scale world->image
    tx, ty, zoom, skew = 0.0, 0.0, 1.0, 0.0
    if kind in ('pan_zoom', 'handheld', 'sensor_noise', 'mild_lighting', 'cut_after', 'cut_inside', 'occluder',
                'low_texture', 'deforming', 'motion_blur', 'lighting_change'):
        tx, ty, zoom = -25 * t, 6 * np.sin(t), 1 + .06 * t
    if kind == 'perspective':
        skew = .10 * np.sin(t * 0.9)
        tx = -10 * t
    if kind == 'exits_frame':
        tx = -110 * t
    if kind == 'motion_blur':
        tx = -25 * t if t < 1.6 else -25 * 1.6 - 120 * (t - 1.6)
    img = (wc * s - np.array([WORLD[0] * s / 2, WORLD[1] * s / 2])) * zoom + np.array([W / 2, H / 2]) + np.array([tx, ty])
    if skew:
        img[[1, 2], 1] += np.array([-1, 1]) * skew * H   # right edge shrinks/grows -> perspective change
    if kind == 'handheld':
        img += rng_jitter
    return cv2.getPerspectiveTransform(wc, img.astype(np.float32))


def _linear(img):
    x = img.astype(np.float32) / 255
    return np.where(x <= .04045, x / 12.92, ((x + .055) / 1.055) ** 2.4)


def _srgb(lin):
    lin = np.clip(lin, 0, 1)
    return (np.where(lin <= .0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - .055) * 255).round().astype(np.uint8)


def world_product_quad(pw, ph):
    """Where the product sits on the poster in world coordinates (aspect-fit, centred)."""
    qw, qh = POSTER[1, 0] - POSTER[0, 0], POSTER[2, 1] - POSTER[1, 1]
    fw, fh = (qw, qw * ph / pw) if pw / ph >= qw / qh else (qh * pw / ph, qh)
    x0, y0 = POSTER[0, 0] + (qw - fw) / 2, POSTER[0, 1] + (qh - fh) / 2
    return np.array([[x0, y0], [x0 + fw, y0], [x0 + fw, y0 + fh], [x0, y0 + fh]], np.float32)


def _with_product(world, product_bgra):
    """Ground truth: composite the product into the world plane in linear light (premultiplied)."""
    ph, pw = product_bgra.shape[:2]
    a = product_bgra[:, :, 3:4].astype(np.float32) / 255
    pre = np.concatenate([_linear(product_bgra[:, :, :3]) * a, a], axis=2)
    P = cv2.getPerspectiveTransform(np.array([[0, 0], [pw, 0], [pw, ph], [0, ph]], np.float32), world_product_quad(pw, ph))
    warped = cv2.warpPerspective(pre, P, (world.shape[1], world.shape[0]), flags=cv2.INTER_AREA if pw > 240 else cv2.INTER_LINEAR)
    return _srgb(_linear(world) * (1 - warped[:, :, 3:4]) + warped[:, :, :3])


def generate(kind, seed, out_dir, product=None):
    """Write <kind>_s<seed>.mp4 (and <kind>_s<seed>_gt.mp4 with the product inserted in the world when product is given)."""
    if kind not in ALL_KINDS:
        raise ValueError(kind)
    rng = np.random.default_rng(seed * 1000 + sorted(ALL_KINDS).index(kind)) if kind in COLOR_KINDS else np.random.default_rng(seed * 1000 + sorted(KINDS).index(kind))
    world = _world(rng, flat_poster=(kind == 'low_texture'))
    gt_rng = copy.deepcopy(rng)          # identical camera jitter and noise for the ground-truth clip
    meta = _render(kind, seed, out_dir, world, False, rng)
    if product:
        meta['gt_path'] = _render(kind, seed, out_dir, _with_product(world, cv2.imread(str(product), cv2.IMREAD_UNCHANGED)), True, gt_rng)['path']
    return meta


def _render(kind, seed, out_dir, world, is_gt, rng):
    world_b = _world(np.random.default_rng(seed + 777))
    n = int(SECONDS * FPS)
    jitter = np.cumsum(rng.normal(0, 1.2, (n, 2)), axis=0)
    jitter = jitter - cv2.blur(jitter.reshape(-1, 1, 2).astype(np.float32), (1, 9)).reshape(-1, 2)
    out = Path(out_dir) / f'{kind}_s{seed}{"_gt" if is_gt else ""}.mp4'
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
           '-f', 'lavfi', '-i', f'sine=frequency=330:duration={SECONDS}', '-c:v', 'libx264', '-crf', '16', '-preset', 'veryfast',
           '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    gt = []
    cut_t = 1.6 if kind == 'cut_inside' else 3.0 if kind == 'cut_after' else None
    occ_rng = np.random.default_rng(seed)
    occ_tex = _texture(occ_rng, (70, 160), 1.0)
    for i in range(n):
        t = i / FPS
        Hm = _camera(kind, t, jitter[i])
        src = world_b if cut_t is not None and t >= cut_t else world
        frame = cv2.warpPerspective(src, Hm, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        quad = cv2.perspectiveTransform(POSTER.reshape(-1, 1, 2), Hm).reshape(4, 2)
        if kind == 'deforming':
            yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
            inside = np.zeros((H, W), np.float32); cv2.fillConvexPoly(inside, quad.astype(np.int32), 1.0)
            inside = cv2.GaussianBlur(inside, (0, 0), 4)
            amp = 7.0 * min(1.0, t / 0.8)
            dx = amp * np.sin(yy / 11.0 + t * 6) * inside; dy = amp * np.cos(xx / 13.0 + t * 5) * inside
            frame = cv2.remap(frame, xx + dx, yy + dy, cv2.INTER_LINEAR)
        if kind == 'occluder' and 1.0 <= t <= 2.4:
            cx = int(quad[:, 0].min() - 70 + (t - 1.0) / 1.4 * (quad[:, 0].max() - quad[:, 0].min() + 70))
            y0 = int(quad[:, 1].min() - 20)
            x0, x1 = max(cx, 0), min(cx + 70, W); ys = slice(max(y0, 0), min(y0 + 160, H))
            if x1 > x0:
                frame[ys, x0:x1] = occ_tex[:ys.stop - ys.start, x0 - cx:x1 - cx]
        if kind == 'motion_blur' and t >= 1.6:
            k = np.zeros((1, 21), np.float32); k[0, :] = 1 / 21
            frame = cv2.filter2D(frame, -1, k)
        gain = 1.0
        if kind == 'mild_lighting': gain = 1 + .08 * np.sin(t * 2.5)
        if kind == 'lighting_change': gain = 1 - .5 * min(1.0, max(0.0, (t - 0.8) / 1.5))
        if gain != 1.0:
            frame = (frame.astype(np.float32) * gain).clip(0, 255).astype(np.uint8)
        if kind in ('exposure_drift', 'wb_drift'):
            if kind == 'exposure_drift':
                g = np.full(3, 1 - .28 * np.sin(np.pi * min(1.0, t / SECONDS)))
            else:
                g = np.array([1 - .15 * np.sin(t * 2), 1.0, 1 + .15 * np.sin(t * 2)])   # B,G,R
            frame = _srgb(_linear(frame) * g.reshape(1, 1, 3).astype(np.float32))
        if kind == 'sensor_noise':
            frame = (frame.astype(np.float32) + rng.normal(0, 9, frame.shape)).clip(0, 255).astype(np.uint8)
        proc.stdin.write(frame.tobytes())
        gt.append(quad.tolist())
    proc.stdin.close()
    if proc.wait():
        raise RuntimeError('fixture encoding failed')
    suitable, code, desc = ALL_KINDS[kind]
    ref_idx = round(REF_S * FPS)
    quad_norm = (np.asarray(gt[ref_idx]) / np.array([W, H])).clip(0, 1).tolist()
    return {'kind': kind, 'seed': seed, 'path': str(out), 'suitable': suitable, 'expected_code': code, 'description': desc,
            'quad_norm': quad_norm, 'reference_second': REF_S, 'start_second': START_S, 'end_second': END_S,
            'fps': FPS, 'width': W, 'height': H, 'gt_surface_quads': gt}


def product_image(path, w=120, h=90):
    """A labelled, legible product image with transparency."""
    img = np.zeros((h, w, 4), np.uint8)
    cv2.rectangle(img, (0, 0), (w - 1, h - 1), (40, 40, 200, 255), -1)
    cv2.rectangle(img, (6, 6), (w - 7, h - 7), (240, 240, 240, 255), 3)
    cv2.putText(img, 'CC', (w // 2 - 26, h // 2 + 14), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255, 255), 3)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)
    return str(path)
