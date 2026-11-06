"""Evaluate object tracking with the real detector in the loop (specification §8.4), on semi-synthetic sequences.

    python -m benchmark.evaluate_tracking --images DIR --annotations instances_val2017.json [--sequences 24] [--out FILE]

Each sequence moves two real objects, cut out of COCO val2017 photographs with their segmentation masks (people, cars,
dogs, bicycles, chairs, ...), across a real COCO photograph that has no labelled object of its own bigger than 1% of the
picture, one sample at a time as the analysis samples a video
(`detection_step_seconds`). Partway through, one object passes behind an occluder (a strip of another photograph) for 2 to
5 samples, the other keeps moving. The ground truth is known by construction. The detector (`app.detection`) runs on every
frame and the tracker (`app.objtrack`) links the detections, with the same appearance vector the pipeline uses.

Measured, per object: how many track identities it was given (1 is right; more is fragmentation), identity switches
(an object's matched track changing while both stay alive), whether the identity after the occlusion is the one before it
(recovery), and the fraction of its visible samples that a confirmed track covers (recall). Confirmed tracks that match
no inserted object are counted as background-object tracks when the photograph itself contains that object (the detector
is right about it), and as false tracks otherwise. The images are used for measurement only and are not stored in the repository.
This is real imagery with synthetic motion: it says how detector and tracker behave together, not how they do on real
video, which needs licensed, labelled footage (B-7).
"""
import argparse, json, os, random, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

CLASSES = ('person', 'car', 'dog', 'bicycle', 'chair', 'horse', 'motorcycle', 'bench', 'potted plant', 'cat')
W, H, SAMPLES = 640, 384, 16


def _cutouts(ann, image_dir, rng, n):
    import cv2, numpy as np
    d = json.loads(Path(ann).read_text(encoding='utf-8'))
    cats = {c['id']: c['name'] for c in d['categories']}
    files = {i['id']: (i['file_name'], i['width'], i['height']) for i in d['images'] if (Path(image_dir) / i['file_name']).exists()}
    pool = []
    for a in d['annotations']:
        if a['image_id'] not in files or a['iscrowd'] or cats[a['category_id']] not in CLASSES or not isinstance(a['segmentation'], list): continue
        fn, iw, ih = files[a['image_id']]; x, y, w, h = a['bbox']
        if w * h < 110 * 110 or x < 2 or y < 2 or x + w > iw - 2 or y + h > ih - 2: continue          # whole, not cut by the photo's edge
        pool.append((fn, cats[a['category_id']], a['segmentation'], a['bbox']))
    rng.shuffle(pool)
    out = []
    for fn, label, seg, (x, y, w, h) in pool[:n]:
        img = cv2.imread(str(Path(image_dir) / fn))
        mask = np.zeros(img.shape[:2], np.uint8)
        for poly in seg: cv2.fillPoly(mask, [np.asarray(poly, np.float32).reshape(-1, 2).astype(np.int32)], 255)
        x0, y0, x1, y1 = int(x), int(y), int(x + w), int(y + h)
        out.append({'label': label, 'rgb': img[y0:y1, x0:x1].copy(), 'alpha': mask[y0:y1, x0:x1].copy(), 'source': fn})
    # backgrounds and occluding strips: photographs with no labelled object bigger than 1% of the picture, so every person
    # or car the detector finds in a sequence is one that was put there
    busy = {a['image_id'] for a in d['annotations'] if a['area'] > .01 * files.get(a['image_id'], ('', 1, 1))[1] * files.get(a['image_id'], ('', 1, 1))[2]}
    backgrounds = sorted(f for i, (f, _, _) in files.items() if i not in busy)
    return out, backgrounds, files


def _paste(frame, obj, cx, cy, height):
    import cv2, numpy as np
    s = height / obj['rgb'].shape[0]
    rgb = cv2.resize(obj['rgb'], None, fx=s, fy=s); a = cv2.resize(obj['alpha'], None, fx=s, fy=s).astype(np.float32)[..., None] / 255
    h, w = rgb.shape[:2]; x0, y0 = int(cx - w / 2), int(cy - h / 2)
    fx0, fy0, fx1, fy1 = max(0, x0), max(0, y0), min(frame.shape[1], x0 + w), min(frame.shape[0], y0 + h)
    if fx1 <= fx0 or fy1 <= fy0: return None
    roi = frame[fy0:fy1, fx0:fx1].astype(np.float32)
    sub_rgb, sub_a = rgb[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0], a[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0]
    frame[fy0:fy1, fx0:fx1] = (roi * (1 - sub_a) + sub_rgb * sub_a).astype(np.uint8)
    ys, xs = np.where(sub_a[..., 0] > .5)
    if not len(xs): return None
    return [fx0 + xs.min(), fy0 + ys.min(), fx0 + xs.max(), fy0 + ys.max()]


def _sequence(objs, background, strip, rng):
    """Frames and ground truth: [(frame, [(object index, visible box or None)])]."""
    import numpy as np
    out = []
    starts = [(rng.uniform(70, 160), rng.uniform(150, 260)), (rng.uniform(480, 570), rng.uniform(150, 260))]
    vel = [(rng.uniform(22, 34), rng.uniform(-4, 4)), (-rng.uniform(18, 30), rng.uniform(-4, 4))]
    heights = [rng.uniform(150, 230), rng.uniform(150, 230)]
    occ_at = int(rng.integers(5, 9)); occ_len = int(rng.integers(2, 6))
    for k in range(SAMPLES):
        frame = background.copy(); truth = []
        for i, o in enumerate(objs):
            cx, cy = starts[i][0] + vel[i][0] * k, starts[i][1] + vel[i][1] * k
            box = _paste(frame, o, cx, cy, heights[i])
            truth.append([i, box])
        if occ_at <= k < occ_at + occ_len:                              # object 0 goes behind a strip of another photo
            b = truth[0][1]
            if b is not None:
                x0, x1 = max(0, int(b[0]) - 20), min(W, int(b[2]) + 20)
                frame[:, x0:x1] = strip[:, x0:x1]; truth[0][1] = None
        out.append((frame, truth))
    return out, (occ_at, occ_len)


def run(images, annotations, n, out, seed=7, first=0, params=None):
    import cv2, numpy as np
    from app import detection, objtrack
    from app.intelligence import _appearance
    rng = np.random.default_rng(seed); prng = random.Random(seed)
    cut, backgrounds, files = _cutouts(annotations, images, prng, (first + n) * 2)
    det = detection.load()
    per_obj, false_tracks, background_tracks, seqs = [], 0, 0, []
    started = time.time()
    for q in range(first, first + n):
        objs = cut[2 * q:2 * q + 2]
        if len(objs) < 2: break
        bg = cv2.resize(cv2.imread(str(Path(images) / backgrounds[(q * 7) % len(backgrounds)])), (W, H))
        strip = cv2.resize(cv2.imread(str(Path(images) / backgrounds[(q * 7 + 3) % len(backgrounds)])), (W, H))
        frames, (occ_at, occ_len) = _sequence(objs, bg, strip, rng)
        detections, app = [], {}
        for k, (f, _) in enumerate(frames):
            ds = det.detect(f)
            for d in ds: app[(k * .5, tuple(d['bbox']))] = np.asarray(_appearance(f, d['bbox']) or [0.0])
            detections.append((k * .5, ds))
        tracks = objtrack.track(detections, params, appearance=lambda t, b: app.get((t, tuple(b))))
        scenery = det.detect(bg) + det.detect(strip)          # objects the photographs themselves contain (unlabelled ones too)
        # match each confirmed track observation to the ground-truth object it overlaps most (IoU >= 0.5, same class)
        matched_ids = {0: {}, 1: {}}; used_tracks = set()
        for tr in tracks:
            for o in tr['observations']:
                k = int(round(o['t'] / .5))
                for i, box in frames[k][1]:
                    if box is not None and objs[i]['label'] == tr['label'] and objtrack.iou(o['bbox'], box) >= .5:
                        matched_ids[i][k] = tr['track_id']; used_tracks.add(tr['track_id'])
        for tr in tracks:
            if tr['track_id'] in used_tracks: continue
            if any(objtrack.iou(o['bbox'], d['bbox']) >= .5 and d['label'] == tr['label'] for o in tr['observations'] for d in scenery): background_tracks += 1
            else: false_tracks += 1
        for i in (0, 1):
            visible = [k for k, (_, truth) in enumerate(frames) if truth[i][1] is not None]
            ids = [matched_ids[i][k] for k in sorted(matched_ids[i])]
            switches = sum(1 for a, b in zip(ids, ids[1:]) if a != b)
            rec = None
            if i == 0:
                before = [matched_ids[0][k] for k in sorted(matched_ids[0]) if k < occ_at]
                after = [matched_ids[0][k] for k in sorted(matched_ids[0]) if k >= occ_at + occ_len]
                rec = bool(before and after and before[-1] == after[0]) if (before and after) else None
            per_obj.append({'sequence': q, 'object': i, 'label': objs[i]['label'], 'identities': len(set(ids)), 'switches': switches,
                            'recall': round(len(matched_ids[i]) / max(1, len(visible)), 3), 'recovered_after_occlusion': rec,
                            'occlusion_samples': occ_len if i == 0 else 0})
        seqs.append({'sequence': q, 'labels': [o['label'] for o in objs], 'occlusion': [occ_at, occ_len], 'tracks': len(tracks)})
    tracked = [p for p in per_obj if p['identities'] > 0]
    occl = [p for p in per_obj if p['recovered_after_occlusion'] is not None]
    res = {'measured_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'model': det.version, 'tracker': objtrack.ALGORITHM, 'sequences': len(seqs),
           'objects': len(per_obj), 'imagery': 'COCO val2017 cut-outs on COCO backgrounds, synthetic motion (labels CC BY 4.0)',
           'objects_detected_and_tracked': len(tracked),
           'single_identity_rate': round(sum(1 for p in tracked if p['identities'] == 1) / max(1, len(tracked)), 3),
           'identity_switches': sum(p['switches'] for p in per_obj),
           'recovered_after_occlusion': f"{sum(1 for p in occl if p['recovered_after_occlusion'])}/{len(occl)}",
           'mean_recall_of_tracked_objects': round(sum(p['recall'] for p in tracked) / max(1, len(tracked)), 3),
           'false_tracks': false_tracks, 'background_object_tracks': background_tracks, 'tracker_params': params or {}, 'seconds': round(time.time() - started, 1), 'per_object': per_obj, 'per_sequence': seqs}
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_text(json.dumps(res, indent=1))
    return res


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--images', required=True); ap.add_argument('--annotations', required=True)
    ap.add_argument('--sequences', type=int, default=24); ap.add_argument('--first', type=int, default=0)
    ap.add_argument('--params', default='', help='tracker parameter overrides as JSON'); ap.add_argument('--out', default='benchmark/results/tracking.json')
    a = ap.parse_args()
    os.environ.setdefault('CC_DATA_DIR', str(Path(os.getenv('TEMP', '/tmp')) / 'visuara-track-eval'))
    os.environ.setdefault('CC_DOTENV', '0'); os.environ.setdefault('CC_INLINE_WORKER', '0')
    from app import main  # noqa: F401  (wires the application before app.intelligence is imported)
    r = run(a.images, a.annotations, a.sequences, a.out, first=a.first, params=json.loads(a.params) if a.params else None)
    print(json.dumps({k: v for k, v in r.items() if k not in ('per_object', 'per_sequence')}, indent=1))
