"""Evaluate scene segmentation on clips with labelled transitions (`benchmark/fixtures_cuts.py`), specification §8.2.

    python -m benchmark.evaluate_segmentation [--seeds 1-12] [--out benchmark/results/segmentation.json]

A detected boundary matches a transition when it falls within `tolerance` frames of it: within 2 frames of a hard cut, or
anywhere from 2 frames before a gradual transition starts to 2 frames after it ends. Matching is one-to-one. Reported:
precision, recall and F1 overall; recall per transition type; false boundaries per minute of video, each attributed to the
distractor it fell in (flash, drift, fast pan, crossing object) or to none; and the median error of matched hard cuts, in
frames. Synthetic footage only (see the fixture module).
"""
import argparse, json, os, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))


def evaluate_clip(meta, found, tolerance=2):
    """found: boundary times in seconds. Returns (matches, false_positives, misses) with details."""
    fps = meta['fps']
    frames = sorted(int(round(t * fps)) for t in found)
    used, matches, fps_ = set(), [], []
    for f in frames:
        best = None
        for i, tr in enumerate(meta['transitions']):
            if i in used: continue
            lo, hi = tr['start_frame'] - tolerance, tr['end_frame'] + tolerance
            if lo <= f <= hi:
                err = 0 if tr['start_frame'] <= f <= tr['end_frame'] else min(abs(f - tr['start_frame']), abs(f - tr['end_frame']))
                if best is None or err < best[0]: best = (err, i)
        if best is None:
            where = next((d['type'] for d in meta['distractors'] if d['start_frame'] - 3 <= f <= d['end_frame'] + 3), None)
            fps_.append({'frame': f, 'distractor': where})
        else:
            used.add(best[1]); tr = meta['transitions'][best[1]]
            matches.append({'type': tr['type'], 'frame': f, 'error_frames': f - tr['start_frame'] if tr['start_frame'] == tr['end_frame'] else best[0]})
    misses = [tr for i, tr in enumerate(meta['transitions']) if i not in used]
    return matches, fps_, misses


def run(seeds, out=None, cfg_overrides=None):
    work = Path(tempfile.mkdtemp(prefix='visuara-seg-'))
    os.environ.update({'CC_DATA_DIR': str(work / 'data'), 'CC_DOTENV': '0', 'CC_INLINE_WORKER': '0'})
    from app import main, intelligence  # noqa: F401  (main first: it wires the routers)
    from benchmark import fixtures_cuts
    cfg = dict(intelligence.config(), **(cfg_overrides or {}))
    per_type = {t: {'n': 0, 'found': 0} for t in fixtures_cuts.TRANSITIONS}
    tp = fp = n_true = 0; seconds = 0.0; errors = []; attributed = {}; clips = []; spent = 0.0
    for seed in seeds:
        meta = fixtures_cuts.generate(seed, work)
        t0 = time.perf_counter()
        bounds, _ = intelligence.boundaries(meta['path'], meta['seconds'], cfg)
        spent += time.perf_counter() - t0
        found = [b['t'] for b in bounds]
        matches, false, misses = evaluate_clip(meta, found)
        tp += len(matches); fp += len(false); n_true += len(meta['transitions']); seconds += meta['seconds']
        for tr in meta['transitions']: per_type[tr['type']]['n'] += 1
        for mt in matches:
            per_type[mt['type']]['found'] += 1
            if mt['type'] in ('cut', 'cut_same_place'): errors.append(abs(mt['error_frames']))
        for f in false: attributed[f['distractor'] or 'none'] = attributed.get(f['distractor'] or 'none', 0) + 1
        clips.append({'seed': seed, 'transitions': [(tr['type'], tr['start_frame'], tr['end_frame']) for tr in meta['transitions']],
                      'found': [(b['t'], b['source']) for b in bounds], 'false': false, 'missed': [(m['type'], m['start_frame']) for m in misses]})
    p = tp / max(1, tp + fp); r = tp / max(1, n_true)
    res = {'measured_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'footage': 'synthetic (benchmark/fixtures_cuts.py)', 'seeds': list(seeds),
           'tolerance_frames': 2, 'config': {k: cfg[k] for k in sorted(cfg) if k.startswith(('cut', 'sample', 'min_scene', 'gradual'))},
           'transitions': n_true, 'precision': round(p, 3), 'recall': round(r, 3), 'f1': round(2 * p * r / max(p + r, 1e-9), 3),
           'false_per_minute': round(fp / max(seconds / 60, 1e-9), 2), 'false_by_distractor': attributed,
           'recall_by_type': {k: round(v['found'] / max(1, v['n']), 3) for k, v in per_type.items()}, 'count_by_type': {k: v['n'] for k, v in per_type.items()},
           'hard_cut_error_frames_median': sorted(errors)[len(errors) // 2] if errors else None,
           'seconds_per_video_second': round(spent / max(seconds, 1e-9), 3), 'clips': clips}
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_text(json.dumps(res, indent=1))
    return res


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--seeds', default='1-12'); ap.add_argument('--out', default='benchmark/results/segmentation.json')
    a = ap.parse_args()
    lo, hi = (int(x) for x in a.seeds.split('-'))
    r = run(range(lo, hi + 1), a.out)
    print(json.dumps({k: v for k, v in r.items() if k != 'clips'}, indent=1))
