"""Scene understanding from measurements (M14, specification §8.6). Every value here is measured from pixels or derived
from detected objects, and each says how; nothing is inferred that the evidence does not support.

  camera motion   global motion between sampled frames: corners over the whole frame, optical flow, and a RANSAC
                  similarity transform (translation, scale, rotation). Classified as static, pan, tilt, zoom in/out,
                  handheld (shake without a consistent direction) or mixed, with the measured rates
  lighting        luma level, contrast, dynamic range, a warm/neutral/cool cast from the linear-light blue/red balance,
                  and how much the level changed through the scene
  context         the setting suggested by the objects detected in the scene (COCO classes): a kitchen needs kitchen
                  objects, a street needs vehicles or traffic furniture. At least two supporting object kinds, or one
                  decisive one, are required; otherwise UNDETERMINED. The evidence lists the objects
  relations       geometric relations between tracked objects in the representative frame (on, next to, held by), from
                  their boxes only
  safety signals  detected objects that a brand-safety reviewer should look at (a blade, a wine glass); a signal is not a
                  verdict and never makes a scene SAFE
An optional vision model (`app/vision.py`) adds a description and its own classification on top; it never replaces these.
"""
import math

import numpy as np

CAMERA_ALGORITHM = 'global-similarity-flow-v1'
CONTEXT_ALGORITHM = 'coco-object-context-v1'

# setting -> {object label: weight}; a weight of 1.0 alone is decisive (one such object is enough)
CONTEXTS = {
    'kitchen': {'oven': 1.0, 'microwave': 1.0, 'refrigerator': 1.0, 'toaster': 1.0, 'sink': .6, 'bowl': .3, 'cup': .2, 'knife': .3,
                'spoon': .3, 'fork': .2, 'bottle': .2, 'banana': .2, 'apple': .2, 'orange': .2, 'broccoli': .3, 'carrot': .3},
    'dining': {'dining table': .7, 'wine glass': .5, 'fork': .4, 'knife': .3, 'spoon': .3, 'cup': .3, 'bowl': .3, 'pizza': .5,
               'cake': .5, 'sandwich': .5, 'hot dog': .5, 'donut': .4, 'chair': .2},
    'living room': {'couch': .8, 'tv': .6, 'remote': .5, 'potted plant': .3, 'vase': .3, 'book': .2, 'clock': .2, 'chair': .2},
    'workspace': {'laptop': .7, 'keyboard': .7, 'mouse': .6, 'tv': .2, 'cell phone': .2, 'book': .3, 'chair': .2, 'cup': .1},
    'bedroom': {'bed': 1.0, 'teddy bear': .3, 'clock': .2, 'book': .2},
    'bathroom': {'toilet': 1.0, 'toothbrush': .6, 'hair drier': .6, 'sink': .4},
    'street': {'car': .5, 'bus': .7, 'truck': .5, 'traffic light': .8, 'stop sign': .8, 'fire hydrant': .8, 'parking meter': .8,
               'bicycle': .4, 'motorcycle': .5, 'bench': .2},
    'sports': {'sports ball': .6, 'tennis racket': .8, 'baseball bat': .8, 'baseball glove': .8, 'skateboard': .7, 'surfboard': .7,
               'skis': .8, 'snowboard': .8, 'frisbee': .6, 'kite': .5},
    'outdoors with animals': {'horse': .6, 'sheep': .7, 'cow': .7, 'elephant': .9, 'bear': .9, 'zebra': .9, 'giraffe': .9, 'bird': .3},
    'travel': {'airplane': .8, 'train': .8, 'boat': .7, 'suitcase': .5, 'backpack': .2},
}
SAFETY_SIGNALS = {'knife': 'a blade is visible', 'scissors': 'a blade is visible', 'wine glass': 'possible alcohol (wine glass)'}


# ---------------------------------------------------------------- camera motion
def global_motion(frames):
    """frames: [(t, gray)] in time order. Returns per-step transforms [{t, dx, dy, scale, rotation, inliers}] in pixels."""
    import cv2
    out = []
    for (t0, a), (t1, b) in zip(frames, frames[1:]):
        pts = cv2.goodFeaturesToTrack(a, maxCorners=300, qualityLevel=.01, minDistance=8, blockSize=5)
        if pts is None or len(pts) < 12: out.append({'t': t1, 'dt': t1 - t0, 'ok': False}); continue
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(a, b, pts, None, winSize=(21, 21), maxLevel=3)
        good = st.ravel() == 1
        if good.sum() < 12: out.append({'t': t1, 'dt': t1 - t0, 'ok': False}); continue
        M, inl = cv2.estimateAffinePartial2D(pts[good], nxt[good], method=cv2.RANSAC, ransacReprojThreshold=2.0)
        if M is None: out.append({'t': t1, 'dt': t1 - t0, 'ok': False}); continue
        s = math.hypot(M[0, 0], M[1, 0]); rot = math.degrees(math.atan2(M[1, 0], M[0, 0]))
        h, w = a.shape
        # translation of the frame centre, so a zoom about the centre is not read as a pan
        cx, cy = w / 2, h / 2
        dx = M[0, 0] * cx + M[0, 1] * cy + M[0, 2] - cx; dy = M[1, 0] * cx + M[1, 1] * cy + M[1, 2] - cy
        out.append({'t': t1, 'dt': t1 - t0, 'ok': True, 'dx': dx / w, 'dy': dy / h, 'scale': s, 'rotation': rot,
                    'inliers': float(inl.mean()) if inl is not None else 0.0})
    return out


def classify_camera(steps):
    """Camera movement from per-step global motion. Rates are per second; translations as fractions of the frame."""
    ok = [s for s in steps if s.get('ok')]
    if len(ok) < 2:
        return {'status': 'UNAVAILABLE', 'reason': 'too few trackable frames to measure camera motion', 'algorithm': CAMERA_ALGORITHM}
    dt = np.array([max(s['dt'], 1e-3) for s in ok])
    vx = np.array([s['dx'] for s in ok]) / dt; vy = np.array([s['dy'] for s in ok]) / dt
    vz = np.array([math.log(max(s['scale'], 1e-3)) for s in ok]) / dt
    pan, tilt, zoom = float(np.median(vx)), float(np.median(vy)), float(np.median(vz))
    # shake: frame-to-frame motion that does not follow the scene's steady movement
    shake = float(np.sqrt(np.mean((vx - pan) ** 2 + (vy - tilt) ** 2)))
    moves = []
    if abs(pan) >= .01: moves.append('pan right' if pan < 0 else 'pan left')       # content moving left = camera panning right
    if abs(tilt) >= .01: moves.append('tilt down' if tilt < 0 else 'tilt up')
    if abs(zoom) >= .01: moves.append('zoom in' if zoom > 0 else 'zoom out')
    if shake >= .12 and not moves: kind = 'handheld'
    elif not moves: kind = 'static'
    elif len(moves) == 1: kind = moves[0]
    else: kind = 'mixed'
    return {'status': 'MEASURED', 'algorithm': CAMERA_ALGORITHM, 'movement': kind, 'components': moves,
            'pan_per_second': round(pan, 4), 'tilt_per_second': round(tilt, 4), 'zoom_per_second': round(zoom, 4),
            'shake': round(shake, 4), 'steps_measured': len(ok), 'steps_total': len(steps)}


# ---------------------------------------------------------------- lighting
def lighting(bgr_frames):
    """Lighting of a scene from a few full-colour frames (BGR)."""
    if not bgr_frames: return {'status': 'UNAVAILABLE', 'reason': 'no frames'}
    levels, contrasts, ranges, casts = [], [], [], []
    for f in bgr_frames:
        x = f.astype(np.float32) / 255.0
        lin = np.where(x <= .04045, x / 12.92, ((x + .055) / 1.055) ** 2.4)
        luma = .0722 * x[..., 0] + .7152 * x[..., 1] + .2126 * x[..., 2]
        levels.append(float(luma.mean())); contrasts.append(float(luma.std()))
        lo, hi = np.percentile(luma, [5, 95]); ranges.append(float(hi - lo))
        b, r = float(lin[..., 0].mean()), float(lin[..., 2].mean())
        casts.append(math.log((r + 1e-4) / (b + 1e-4)))
    level, cast = float(np.mean(levels)), float(np.mean(casts))
    exposure = 'low light' if level < .22 else 'bright' if level > .72 else 'normal'
    tone = 'warm' if cast > .25 else 'cool' if cast < -.25 else 'neutral'
    change = float(max(levels) - min(levels)) if len(levels) > 1 else 0.0
    return {'status': 'MEASURED', 'exposure': exposure, 'colour_cast': tone, 'level': round(level, 3), 'contrast': round(float(np.mean(contrasts)), 3),
            'dynamic_range': round(float(np.mean(ranges)), 3), 'warmth': round(cast, 3), 'level_change': round(change, 3),
            'stable': change < .1, 'frames': len(bgr_frames)}


# ---------------------------------------------------------------- context, relations and signals from objects
def _object_strength(tracks):
    """label -> best confidence among confirmed tracks."""
    best = {}
    for t in tracks: best[t['label']] = max(best.get(t['label'], 0.0), float(t['confidence']))
    return best


def context(tracks):
    objs = _object_strength(tracks)
    if not objs: return {'status': 'UNDETERMINED', 'reason': 'no objects were detected', 'algorithm': CONTEXT_ALGORITHM}
    scored = []
    for name, weights in CONTEXTS.items():
        support = {k: objs[k] for k in weights if k in objs and objs[k] >= .3}
        if not support: continue
        score = sum(weights[k] * v for k, v in support.items())
        decisive = any(weights[k] >= 1.0 and v >= .5 for k, v in support.items())
        if len(support) >= 2 or decisive: scored.append((score, name, support))
    if not scored:
        return {'status': 'UNDETERMINED', 'reason': 'the detected objects do not indicate a setting', 'objects': sorted(objs), 'algorithm': CONTEXT_ALGORITHM}
    scored.sort(key=lambda x: -x[0])
    score, name, support = scored[0]
    return {'status': 'MEASURED', 'setting': name, 'strength': round(min(1.0, score / 2.0), 3), 'algorithm': CONTEXT_ALGORITHM,
            'evidence': {k: round(v, 3) for k, v in sorted(support.items(), key=lambda kv: -kv[1])},
            'alternatives': [n for _, n, _ in scored[1:3]]}


def _box_at(track, t):
    obs = min(track['observations'], key=lambda o: abs(o['t'] - t))
    return obs['bbox'] if abs(obs['t'] - t) <= 1.0 else None


def relations(tracks, t, limit=12):
    """Geometric relations between objects visible near time t: 'on' (resting on top of), 'held by' (a small object
    inside a person's box), 'next to' (similar height, side by side). Boxes only; no semantics are claimed."""
    boxes = [(tr['label'], tr['track_id'], b) for tr in tracks if (b := _box_at(tr, t)) is not None]
    out = []
    for la, ia, a in boxes:
        for lb, ib, b in boxes:
            if ia == ib: continue
            aw, ah, bw, bh = a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1]
            cx = (a[0] + a[2]) / 2
            if lb == 'person' and la != 'person' and aw * ah < .25 * bw * bh and b[0] <= cx <= b[2] and b[1] <= (a[1] + a[3]) / 2 <= b[3]:
                out.append({'subject': la, 'relation': 'held by', 'object': lb, 'subject_track': ia, 'object_track': ib}); continue
            if lb != 'person' and b[0] <= cx <= b[2] and b[1] - .15 * bh <= a[3] <= b[1] + .45 * bh and aw < bw:
                out.append({'subject': la, 'relation': 'on', 'object': lb, 'subject_track': ia, 'object_track': ib}); continue
            if ia < ib and .6 < ah / max(bh, 1) < 1.6 and abs((a[1] + a[3]) - (b[1] + b[3])) / 2 < .35 * max(ah, bh):
                gap = max(b[0] - a[2], a[0] - b[2])
                if gap < .5 * max(aw, bw): out.append({'subject': la, 'relation': 'next to', 'object': lb, 'subject_track': ia, 'object_track': ib})
    return out[:limit]


def safety_signals(tracks):
    objs = _object_strength(tracks)
    return [{'object': k, 'confidence': round(v, 3), 'note': SAFETY_SIGNALS[k]} for k, v in sorted(objs.items()) if k in SAFETY_SIGNALS and v >= .4]


def people(tracks, sampled_times):
    """How much of the scene has people in it: the fraction of sampled times at which a person was observed."""
    ps = [t for t in tracks if t['label'] == 'person']
    if not ps or not sampled_times: return {'tracks': 0, 'present_fraction': 0.0}
    seen = {o['t'] for t in ps for o in t['observations']}
    return {'tracks': len(ps), 'max_confidence': round(max(t['confidence'] for t in ps), 3),
            'present_fraction': round(len(seen & set(sampled_times)) / len(set(sampled_times)), 3)}
