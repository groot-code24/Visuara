"""Object tracking across sampled frames (M14, specification §8.4).

Tracking by detection: each sampled frame's detections (`app/detection.py`) are associated with the existing tracks.
  prediction    every track carries a constant-velocity estimate of its box centre and size, updated with a fixed gain
                (an alpha-beta filter, the steady state of SORT's Kalman filter), so a moving object is looked for where it
                is going rather than where it was
  association   same class only; pairs are taken greedily, best first: IoU >= `min_iou` between the prediction and the
                detection, or, for fast motion between sparse samples, a box of similar size whose centre is within `gate`
                diagonals of the prediction (`first_gate` for a track seen once, whose velocity is not known yet)
  occlusion     a track with no detection is kept for `max_gap` samples. Its predicted boxes are NOT reported as
                observations; the gap is recorded so the caller can see the object was hidden
  recovery      a detection that matches no live track can still take a track that was lost for up to `reid_gap` samples,
                if the class is the same, its appearance (an HSV histogram of the box) correlates >= `min_appearance`,
                and it is within `reid_distance` box diagonals of where that track would be. That keeps one identity across a
                longer occlusion instead of starting a new one
  scenes        the caller runs one tracker per scene; nothing crosses a cut
  confidence    a track's confidence is the mean confidence of its observations times the fraction of its lifetime in which
                it was observed; a track observed fewer than `min_hits` times is discarded as unconfirmed
`track()` is pure apart from the optional appearance callback, so it is tested without video (`tests/test_tracking_objects.py`).
"""
from dataclasses import dataclass, field

import numpy as np

ALGORITHM = 'iou-alpha-beta-reid-v1'
DEFAULTS = {'min_iou': 0.3, 'gate': 0.5, 'first_gate': 1.0, 'max_gap': 2, 'reid_gap': 8, 'min_appearance': 0.6, 'reid_distance': 1.5, 'min_hits': 2,
            'alpha': 0.6, 'beta': 0.3}


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy; u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def _state(b):
    return np.array([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2, b[2] - b[0], b[3] - b[1]], dtype=np.float64)


def _box(s):
    cx, cy, w, h = s; w, h = max(w, 1.0), max(h, 1.0)
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]


@dataclass
class _Track:
    id: int
    label: str
    x: np.ndarray
    v: np.ndarray
    last_t: float
    appearance: object = None
    observations: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    missed: int = 0

    def predict(self, dt):
        return self.x + self.v * dt

    def update(self, box, conf, t, dt, alpha, beta, appearance):
        z = _state(box); pred = self.predict(dt); r = z - pred
        if len(self.observations) == 1 and dt > 0:
            # the second sighting: velocity was unknown, so it is taken from the two sightings in full (as a Kalman
            # filter with an uninformed velocity would), rather than eased in
            self.v = (z - self.x) / dt; self.x = z
        else:
            self.x = pred + alpha * r
            if dt > 0: self.v = self.v + beta * r / dt
        if self.missed: self.gaps.append([self.observations[-1]['t'], t])
        self.missed = 0; self.last_t = t
        self.observations.append({'t': t, 'bbox': [round(float(v), 1) for v in box], 'confidence': conf})
        if appearance is not None: self.appearance = appearance if self.appearance is None else 0.7 * self.appearance + 0.3 * appearance


def _match_score(pred, box, min_iou, gate):
    """How well a detection fits a track's predicted box, or None. Overlap first; when the boxes do not overlap enough
    (fast motion between sparse samples), a box of similar size whose centre is within `gate` diagonals of the prediction
    still matches, ranked below every overlap match."""
    o = iou(pred, box)
    if o >= min_iou: return 1.0 + o
    pw, ph = pred[2] - pred[0], pred[3] - pred[1]; bw, bh = box[2] - box[0], box[3] - box[1]
    if not .5 <= (bw * bh) / max(pw * ph, 1e-9) <= 2.0: return None
    dist = float(np.hypot((box[0] + box[2] - pred[0] - pred[2]) / 2, (box[1] + box[3] - pred[1] - pred[3]) / 2)) / max(float(np.hypot(pw, ph)), 1e-9)
    return 1.0 - dist / gate if dist <= gate else None


def _appearance_score(a, b):
    if a is None or b is None: return 0.0
    a = a.ravel() - a.mean(); b = b.ravel() - b.mean()
    den = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / den) if den > 1e-9 else 0.0


def track(frames, params=None, appearance=None):
    """frames: [(t, [{'label', 'confidence', 'bbox': [x0,y0,x1,y1]}])] in time order, one scene.
    appearance: optional f(t, bbox) -> vector, used only for recovery after a longer occlusion.
    Returns confirmed tracks: [{'track_id', 'label', 'start', 'end', 'observations', 'gaps', 'confidence', 'observed_fraction'}]."""
    p = dict(DEFAULTS, **(params or {}))
    live, lost, done = [], [], []
    next_id = 1; ts = [t for t, _ in frames]
    step = float(np.median(np.diff(ts))) if len(ts) > 1 else 1.0
    for t, dets in frames:
        pairs = []
        for ti, tr in enumerate(live):
            pb = _box(tr.predict(t - tr.last_t))
            # a track seen once has no velocity yet, so it may look further for its next sighting
            gate = p['first_gate'] if len(tr.observations) < 2 else p['gate']
            for di, d in enumerate(dets):
                if d['label'] != tr.label: continue
                s = _match_score(pb, d['bbox'], p['min_iou'], gate)
                if s is not None: pairs.append((s, ti, di))
        used_t, used_d = set(), set()
        for o, ti, di in sorted(pairs, key=lambda x: -x[0]):
            if ti in used_t or di in used_d: continue
            used_t.add(ti); used_d.add(di)
            d = dets[di]; tr = live[ti]
            tr.update(d['bbox'], d['confidence'], t, t - tr.last_t, p['alpha'], p['beta'], appearance(t, d['bbox']) if appearance else None)
        for ti, tr in enumerate(live):
            if ti not in used_t: tr.missed += 1
        # recovery of tracks lost for longer than max_gap
        for di, d in enumerate(dets):
            if di in used_d: continue
            app = appearance(t, d['bbox']) if appearance else None
            best = None
            for tr in lost:
                if tr.label != d['label'] or (t - tr.last_t) > p['reid_gap'] * step: continue
                pb = _box(tr.predict(t - tr.last_t)); diag = float(np.hypot(pb[2] - pb[0], pb[3] - pb[1]))
                c = _state(d['bbox'])[:2]; dist = float(np.hypot(*(c - _state(pb)[:2]))) / max(diag, 1.0)
                sim = _appearance_score(tr.appearance, app)
                if dist <= p['reid_distance'] and sim >= p['min_appearance'] and (best is None or sim > best[0]): best = (sim, tr)
            if best:
                tr = best[1]; lost.remove(tr); live.append(tr)
                tr.missed = 1  # so that update() records the gap
                tr.update(d['bbox'], d['confidence'], t, t - tr.last_t, p['alpha'], 0.0, app)
                tr.v = np.zeros(4)
                used_d.add(di)
        for di, d in enumerate(dets):
            if di in used_d: continue
            tr = _Track(next_id, d['label'], _state(d['bbox']), np.zeros(4), t)
            tr.update(d['bbox'], d['confidence'], t, 0.0, 1.0, 0.0, appearance(t, d['bbox']) if appearance else None)
            next_id += 1; live.append(tr)
        still = []
        for tr in live:
            (lost if tr.missed > p['max_gap'] else still).append(tr)
        live = still
        keep = []
        for tr in lost:
            (keep if (t - tr.last_t) <= p['reid_gap'] * step else done).append(tr)
        lost = keep
    out = []
    for tr in sorted({id(x): x for x in live + lost + done}.values(), key=lambda x: x.id):
        if len(tr.observations) < p['min_hits']: continue
        span = tr.observations[-1]['t'] - tr.observations[0]['t']
        expected = max(1, int(round(span / step)) + 1) if step > 0 else len(tr.observations)
        observed = min(1.0, len(tr.observations) / expected)
        conf = float(np.mean([o['confidence'] for o in tr.observations])) * observed
        out.append({'track_id': tr.id, 'label': tr.label, 'start': tr.observations[0]['t'], 'end': tr.observations[-1]['t'],
                    'observations': tr.observations, 'gaps': tr.gaps, 'confidence': round(conf, 4), 'observed_fraction': round(observed, 3)})
    return out
