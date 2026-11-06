"""Synthetic clips with labelled shot transitions, for evaluating scene segmentation (specification §8.2).

Each clip joins four shots. Every transition is labelled by construction, with its type and exact frame range:
  cut             a hard cut to a different place
  cut_same_place  a hard cut to another view of the same place (similar colours: the hard case for histograms)
  dissolve        a cross-fade over 0.6 or 1.0 s
  fade            a fade through black (0.4 s out, 0.4 s in)
Inside the shots there are distractors that are not transitions: a camera flash (three frames at +45% brightness), a
slow exposure drift of +-8%, a fast pan, and an object crossing the frame. They are labelled too, so false boundaries can
be attributed. Synthetic footage only: it measures the method on known input, not accuracy on real video (B-7).
"""
import subprocess
from pathlib import Path

import cv2
import numpy as np

from .fixtures import _texture

W, H, FPS = 480, 270, 25
SHOT_SECONDS = 2.6
TRANSITIONS = ('cut', 'cut_same_place', 'dissolve', 'fade')
DISTRACTORS = ('flash', 'drift', 'fast_pan', 'crossing', None)


def _world(seed):
    rng = np.random.default_rng(seed)
    world = _texture(rng, (1200, 700), 1.6)
    tint = rng.uniform(.55, 1.45, 3)                                   # each place has its own colour balance
    return (world.astype(np.float32) * tint).clip(0, 255).astype(np.uint8)


def _view(world, t, start, velocity, zoom):
    x, y = start[0] + velocity[0] * t, start[1] + velocity[1] * t
    s = 1.0 + zoom * t
    M = np.array([[s, 0, -x * s], [0, s, -y * s]], np.float32)
    return cv2.warpAffine(world, M, (W, H), borderMode=cv2.BORDER_REFLECT)


def _shots(seed):
    rng = np.random.default_rng(seed)
    places = [_world(seed * 10 + k) for k in range(4)]
    kinds = [TRANSITIONS[(seed + k) % len(TRANSITIONS)] for k in range(3)]
    distractors = [DISTRACTORS[(seed + k) % len(DISTRACTORS)] for k in range(4)]
    shots = []
    for k in range(4):
        place = places[k - 1] if k and kinds[k - 1] == 'cut_same_place' else places[k]
        start = (float(rng.uniform(40, 500)), float(rng.uniform(30, 300)))
        if k and kinds[k - 1] == 'cut_same_place':                     # same place, another part of it
            prev = shots[-1]['start']; start = ((prev[0] + 300) % 600 + 20, (prev[1] + 140) % 330 + 10)
        vel = (float(rng.uniform(-30, 30)), float(rng.uniform(-8, 8)))
        if distractors[k] == 'fast_pan': vel = (160.0, 0.0)
        shots.append({'place': place, 'start': start, 'velocity': vel, 'zoom': float(rng.uniform(-.03, .05)), 'distractor': distractors[k]})
    return shots, kinds


def generate(seed, out_dir):
    """Write cuts_s<seed>.mp4. Returns its labels: transitions [{type, start_frame, end_frame, boundary_second}] and
    distractors [{type, start_frame, end_frame}]."""
    shots, kinds = _shots(seed)
    rng = np.random.default_rng(seed + 99)
    per = int(SHOT_SECONDS * FPS)
    frames_per_shot = []
    for sh in shots:
        frames = []
        for i in range(per):
            t = i / FPS
            f = _view(sh['place'], t, sh['start'], sh['velocity'], sh['zoom']).astype(np.float32)
            d = sh['distractor']
            if d == 'flash' and 30 <= i < 33: f *= 1.45
            if d == 'drift': f *= 1 + .08 * np.sin(2 * np.pi * i / per)
            if d == 'crossing' and 20 <= i < 50:
                x = int(-60 + (i - 20) * 18); cv2.rectangle(f, (x, 60), (x + 70, 240), (30, 30, 30), -1)
            frames.append(f.clip(0, 255))
        frames_per_shot.append(frames)
    out, transitions, distractors = [], [], []
    for k, frames in enumerate(frames_per_shot):
        if k == 0:
            shot_start = 0; out.extend(frames)
        else:
            kind = kinds[k - 1]; prev = out
            if kind in ('cut', 'cut_same_place'):
                b = shot_start = len(out); out.extend(frames)
                transitions.append({'type': kind, 'start_frame': b, 'end_frame': b, 'boundary_second': round(b / FPS, 3)})
            elif kind == 'dissolve':
                n = int((0.6 if seed % 2 else 1.0) * FPS); b = shot_start = len(prev) - n
                for j in range(n):
                    a = (j + 1) / (n + 1); prev[b + j] = prev[b + j] * (1 - a) + frames[j] * a
                out.extend(frames[n:])
                transitions.append({'type': kind, 'start_frame': b, 'end_frame': b + n - 1, 'boundary_second': round((b + n / 2) / FPS, 3)})
            else:                                                       # fade through black
                n = int(0.4 * FPS); b = len(prev) - n; shot_start = len(prev)
                for j in range(n): prev[b + j] = prev[b + j] * (1 - (j + 1) / n)
                for j in range(n): frames[j] = frames[j] * ((j + 1) / n)
                out.extend(frames)
                transitions.append({'type': kind, 'start_frame': b, 'end_frame': b + 2 * n - 1, 'boundary_second': round((b + n) / FPS, 3)})
        d = shots[k]['distractor']
        if d:
            base = shot_start
            span = {'flash': (30, 32), 'crossing': (20, 49)}.get(d, (0, per - 1))
            distractors.append({'type': d, 'start_frame': base + span[0], 'end_frame': base + span[1]})
    path = Path(out_dir) / f'cuts_s{seed}.mp4'; path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                             '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p', str(path)], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    for f in out:
        noisy = f + rng.normal(0, 1.5, f.shape)
        proc.stdin.write(noisy.clip(0, 255).astype(np.uint8).tobytes())
    proc.stdin.close()
    if proc.wait(): raise RuntimeError('fixture encoding failed')
    return {'path': str(path), 'seed': seed, 'fps': FPS, 'frames': len(out), 'seconds': round(len(out) / FPS, 3),
            'transitions': transitions, 'distractors': distractors}
