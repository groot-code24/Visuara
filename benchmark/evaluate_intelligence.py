"""Measure the Intelligence pipeline against footage with known ground truth (the project's synthetic fixtures).

    python -m benchmark.evaluate_intelligence --out benchmark/results/intelligence.json

What is measured, and on what:
  scene cuts      `benchmark.fixtures` kinds with a known cut (cut_inside at 1.6 s, cut_after at 3.0 s) and every other kind
                  (no cut). A detected cut within 0.25 s of a true one is a true positive.
  surfaces        each fixture has a ground-truth planar poster (`quad_norm` at the reference frame). A detected surface whose
                  bounding box overlaps the poster's by IoU >= 0.5 is a hit. Measured for the contour detector alone (v1) and
                  for the pipeline's detector (contours and straight edges, `app/surfaces.py`).
  plane tracking  for each hit, the surface followed through the clip by homography (`surfaces.follow`): the largest corner
                  error against the fixture's per-frame ground truth, in pixels, and why the run stopped.
  rooms           `benchmark.fixtures_scene` room clips (poster and table) and bare clips (no trackable surface): opportunities
                  produced per clip, which should be zero for bare clips.
  speed           wall-clock seconds of the whole pipeline per second of video on this machine.
This is synthetic footage only. It shows the pipeline measures what it claims on controlled input; it says nothing about
accuracy on real footage, for which no licensed, labelled set exists yet (blocker B-7).
"""
import argparse, json, os, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy; u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def _plain(o):
    """NumPy scalars from OpenCV and the detectors, as plain numbers."""
    if hasattr(o, 'item'): return o.item()
    raise TypeError(f'{type(o).__name__} is not JSON serializable')


def run(out_path, seeds=(1, 2, 3)):
    work = Path(tempfile.mkdtemp(prefix='visuara-eval-'))
    os.environ.update({'CC_DATA_DIR': str(work / 'data'), 'CC_DOTENV': '0', 'CC_INLINE_WORKER': '0', 'CC_DEFAULT_PLAN': 'enterprise'})
    for k in ('DATABASE_URL', 'CC_DATABASE_URL', 'CC_VISION_MODEL'): os.environ.pop(k, None)
    from app import main, intelligence, video_versions
    from app.decisions import detect_surfaces
    from app import surfaces as surface_detector
    import numpy as np
    from benchmark import fixtures, fixtures_scene
    import cv2
    with main.db() as c:
        org = c.execute("INSERT INTO organizations(name,plan,kind) VALUES('Eval','enterprise','publisher')").lastrowid
        uid = c.execute("INSERT INTO users(email,password_hash,name,role,org_id,member_role) VALUES('eval@x','','Eval','brand',?,'owner')", (org,)).lastrowid

    def analyse(path, title):
        dur, w, h, fps = main.probe(path)
        with main.db() as c:
            vid = c.execute('INSERT INTO videos(org_id,title,source_path,duration,width,height,fps,license_confirmed,created_by,size_bytes) VALUES(?,?,?,?,?,?,?,1,?,?)',
                            (org, title, str(path), dur, w, h, fps, uid, Path(path).stat().st_size)).lastrowid
            video_versions.record_upload(c, vid, org, uid, path, Path(path).stat().st_size, dur, w, h, fps, None)
            v = dict(c.execute('SELECT * FROM videos WHERE id=?', (vid,)).fetchone())
            aid = intelligence.queue(c, v, uid)[0]['id']
        t = time.time()
        while intelligence.run_next(): pass
        took = time.time() - t
        with main.db() as c:
            a = dict(c.execute('SELECT * FROM analysis_versions WHERE id=?', (aid,)).fetchone())
            scenes = [dict(s) for s in c.execute('SELECT start_second FROM scenes WHERE analysis_version_id=? ORDER BY scene_index', (aid,)).fetchall()]
            opps = c.execute('SELECT COUNT(*) FROM opportunities WHERE analysis_version_id=?', (aid,)).fetchone()[0]
        return a, [s['start_second'] for s in scenes[1:]], opps, took, dur

    cuts = {'tp': 0, 'fp': 0, 'fn': 0}; surfaces = {'hits': 0, 'clips': 0, 'best_ious': []}; speed = []; per_clip = []
    surfaces_v2 = {'hits': 0, 'best_ious': []}; tracking = {}
    for kind in fixtures.KINDS:
        for seed in seeds:
            meta = fixtures.generate(kind, seed, work / 'clips')
            a, found, opps, took, dur = analyse(meta['path'], f'{kind}-{seed}')
            truth = [1.6] if kind == 'cut_inside' else [3.0] if kind == 'cut_after' else []
            matched = set()
            for f in found:
                hit = next((i for i, t in enumerate(truth) if abs(f - t) <= 0.25 and i not in matched), None)
                if hit is None: cuts['fp'] += 1
                else: matched.add(hit); cuts['tp'] += 1
            cuts['fn'] += len(truth) - len(matched)
            cap = cv2.VideoCapture(meta['path']); cap.set(cv2.CAP_PROP_POS_MSEC, meta['reference_second'] * 1000); ok, frame = cap.read(); cap.release()
            q = meta['quad_norm']; gt = (min(p[0] for p in q) * meta['width'], min(p[1] for p in q) * meta['height'], max(p[0] for p in q) * meta['width'], max(p[1] for p in q) * meta['height'])
            det = detect_surfaces(frame) if ok else []
            best = float(max((iou(d['bbox'], gt) for d in det), default=0.0))
            surfaces['clips'] += 1; surfaces['hits'] += int(best >= 0.5); surfaces['best_ious'].append(round(best, 3))
            det2 = surface_detector.detect(frame) if ok else []
            pick = max(det2, key=lambda d: iou(d['bbox'], gt), default=None)
            best2 = float(iou(pick['bbox'], gt)) if pick else 0.0
            surfaces_v2['hits'] += int(best2 >= 0.5); surfaces_v2['best_ious'].append(round(best2, 3))
            if best2 >= 0.5:
                run_ = surface_detector.follow(meta['path'], 0.0, fixtures.SECONDS, meta['reference_second'], [pick['quad']])[0]
                if run_['status'] == 'TRACKED':
                    errs = [float(np.linalg.norm(np.asarray(x['quad']) * [meta['width'], meta['height']]
                                                 - np.asarray(meta['gt_surface_quads'][min(int(round(x['t'] * meta['fps'])), len(meta['gt_surface_quads']) - 1)]), axis=1).max())
                            for x in run_['samples']]
                    tracking.setdefault(kind, []).append({'seed': seed, 'max_corner_error_px': round(max(errs), 2), 'median_corner_error_px': round(sorted(errs)[len(errs) // 2], 2),
                                                          'tracked_seconds': round(run_['end'] - run_['start'], 2), 'stopped_after': run_['stopped']['after'],
                                                          'planarity': run_['planarity'], 'occlusion': run_['occlusion']})
                else:
                    tracking.setdefault(kind, []).append({'seed': seed, 'status': run_['status']})
            speed.append(took / max(dur, 0.1))
            per_clip.append({'clip': f'{kind}-{seed}', 'status': a['status'], 'cuts_found': found, 'cuts_true': truth, 'poster_best_iou': round(best, 3), 'opportunities': opps,
                             'seconds': round(took, 2)})
    rooms = {}
    for kind in ('room', 'bare'):
        rooms[kind] = []
        for seed in seeds:
            meta = fixtures_scene.generate(kind, seed, work / 'rooms')
            a, found, opps, took, dur = analyse(meta['path'], f'{kind}-{seed}')
            rooms[kind].append({'clip': f'{kind}-{seed}', 'status': a['status'], 'opportunities': opps, 'scenes': len(found) + 1, 'seconds': round(took, 2)})
            speed.append(took / max(dur, 0.1))
    p = cuts['tp'] / max(1, cuts['tp'] + cuts['fp']); r = cuts['tp'] / max(1, cuts['tp'] + cuts['fn'])
    result = {
        'measured_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'footage': 'synthetic (benchmark/fixtures.py, benchmark/fixtures_scene.py)', 'seeds': list(seeds),
        'scene_cuts': dict(cuts, precision=round(p, 3), recall=round(r, 3), tolerance_seconds=0.25),
        'surface_detection': {'clips': surfaces['clips'], 'poster_found_iou_0_5': surfaces['hits'], 'rate': round(surfaces['hits'] / max(1, surfaces['clips']), 3),
                              'median_best_iou': sorted(surfaces['best_ious'])[len(surfaces['best_ious']) // 2]},
        'surface_detection_v2': {'detector': f'contours + {surface_detector.ALGORITHM}', 'clips': surfaces['clips'], 'poster_found_iou_0_5': surfaces_v2['hits'],
                                 'rate': round(surfaces_v2['hits'] / max(1, surfaces['clips']), 3),
                                 'median_best_iou': sorted(surfaces_v2['best_ious'])[len(surfaces_v2['best_ious']) // 2]},
        'plane_tracking': {'algorithm': surface_detector.TRACK_ALGORITHM, 'by_kind': tracking,
                           'max_corner_error_px_median': (lambda v: sorted(v)[len(v) // 2] if v else None)([x['max_corner_error_px'] for xs in tracking.values() for x in xs if 'max_corner_error_px' in x])},
        'rooms': {k: {'clips': len(v), 'with_opportunities': sum(1 for x in v if x['opportunities']), 'opportunities': sum(x['opportunities'] for x in v), 'all_completed': all(x['status'] == 'COMPLETED' for x in v)}
                  for k, v in rooms.items()},
        'speed_seconds_per_video_second': {'median': round(sorted(speed)[len(speed) // 2], 3), 'max': round(max(speed), 3)},
        'per_clip': per_clip, 'room_clips': rooms,
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True); Path(out_path).write_text(json.dumps(result, indent=1, default=_plain))
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--out', default='benchmark/results/intelligence.json'); a = ap.parse_args()
    r = run(a.out)
    print(json.dumps({k: v for k, v in r.items() if k not in ('per_clip', 'room_clips')}, indent=1, default=_plain))
