"""Synthetic "room" clips for the decision engine: a textured poster on a wall (vertical surface) and a textured
tabletop seen at an angle (horizontal surface) under a slow camera pan, or a bare room with nothing trackable.

Like benchmark/fixtures.py these are synthetic and only exercise the engine's logic (detection, tracking, scoring and
assignment); they say nothing about real-footage quality.
"""
import shutil, subprocess
from pathlib import Path

import cv2
import numpy as np

W, H, FPS, SECONDS = 640, 360, 25, 4.0
WORLD = (900, 420)
POSTER = np.array([[520, 70], [700, 70], [700, 205], [520, 205]], np.float32)            # on the wall
TABLE = np.array([[140, 255], [430, 255], [480, 375], [90, 375]], np.float32)            # receding tabletop


def _pattern(rng, w, h, base):
    img = np.full((h, w, 3), base, np.uint8)
    for _ in range(int(w * h / 260)):
        c = tuple(int(x) for x in rng.integers(0, 255, 3))
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        if rng.random() < .5: cv2.circle(img, (x, y), int(rng.integers(2, 7)), c, -1)
        else: cv2.rectangle(img, (x, y), (x + int(rng.integers(3, 12)), y + int(rng.integers(3, 12))), c, -1)
    return cv2.GaussianBlur(img, (3, 3), 0)


def _world(rng, furnished=True):
    ww, wh = WORLD
    world = np.zeros((wh, ww, 3), np.uint8)
    world[:] = (196, 206, 214)                                                           # wall
    world[250:] = (120, 132, 150)                                                        # floor
    world = (world.astype(np.float32) + rng.normal(0, 2.0, world.shape)).clip(0, 255).astype(np.uint8)
    if furnished:
        for quad, base in ((POSTER, (60, 90, 170)), (TABLE, (40, 80, 120))):
            x0, y0 = quad.min(axis=0).astype(int); x1, y1 = quad.max(axis=0).astype(int)
            tex = _pattern(rng, x1 - x0, y1 - y0, base)
            src = np.array([[0, 0], [x1 - x0, 0], [x1 - x0, y1 - y0], [0, y1 - y0]], np.float32)
            warped = cv2.warpPerspective(tex, cv2.getPerspectiveTransform(src, quad), (ww, wh))
            mask = np.zeros((wh, ww), np.uint8); cv2.fillConvexPoly(mask, quad.astype(np.int32), 255)
            world[mask > 0] = warped[mask > 0]
            cv2.polylines(world, [quad.astype(np.int32)], True, (25, 25, 25), 3)
    return world


def generate(kind, seed, out_dir):
    """kind: 'room' (poster + table) or 'bare' (no trackable surface). Writes <kind>_s<seed>.mp4."""
    if not shutil.which('ffmpeg'): raise RuntimeError('ffmpeg is required')
    rng = np.random.default_rng(seed)
    world = _world(rng, furnished=(kind == 'room'))
    out = Path(out_dir) / f'scene_{kind}_s{seed}.mp4'; out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', str(FPS),
                             '-i', '-', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '16', '-pix_fmt', 'yuv420p', str(out)], stdin=subprocess.PIPE)
    n = int(SECONDS * FPS)
    for i in range(n):
        t = i / FPS
        # slow pan right-to-left with a slight zoom: the world plane maps to the image by a homography
        s = .74 * (1 + .015 * t); tx = -40 - 9 * t; ty = -8 + 2 * np.sin(t)
        M = np.array([[s, 0, tx], [0, s, ty], [0, 0, 1]], np.float64)
        frame = cv2.warpPerspective(world, M, (W, H), borderMode=cv2.BORDER_REPLICATE)
        frame = (frame.astype(np.float32) + rng.normal(0, 1.5, frame.shape)).clip(0, 255).astype(np.uint8)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait(): raise RuntimeError('fixture encoding failed')
    return {'kind': kind, 'seed': seed, 'path': str(out), 'fps': FPS, 'width': W, 'height': H, 'seconds': SECONDS}


def product(path, w, h, color):
    """A cut-out product image (opaque label on a transparent background)."""
    img = np.zeros((h, w, 4), np.uint8)
    cv2.rectangle(img, (2, 2), (w - 3, h - 3), (*color, 255), -1)
    cv2.rectangle(img, (8, 8), (w - 9, h - 9), (245, 245, 245, 255), 2)
    cv2.putText(img, 'CC', (w // 2 - 22, h // 2 + 10), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255, 255), 2)
    Path(path).parent.mkdir(parents=True, exist_ok=True); cv2.imwrite(str(path), img)
    return str(path)
