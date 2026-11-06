"""Benchmark the candidate object detectors before one is chosen (specification §8.3).

    python -m benchmark.evaluate_detection --images DIR --annotations instances_val2017.json [--models a,b] [--out FILE]

For each model in `app.detection.MODELS` whose file is installed (see `ops/fetch_models.py`) it measures:
  accuracy   COCO-style AP (0.5:0.95), AP50 and AP75 on the given labelled images (`benchmark/coco_eval.py`). The images are
             a fixed, seeded subset of COCO val2017 chosen by `--subset-seed` and `--subset-size`; their labels are CC BY 4.0.
             The images are used for measurement only and are not stored in the repository.
  latency    wall-clock milliseconds per image for pre-processing, inference and post-processing, with OpenCV limited to
             `--threads` threads (1 matches the deployment's single vCPU); median and 95th percentile.
  memory     resident memory of this process after loading the model and the peak during the run, in MB.
Detections are scored at confidence >= 0.001 for AP (as the authors evaluate) and the deployment threshold is reported
separately with its precision and recall at IoU 0.5.
"""
import argparse, json, os, platform, random, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))


def _rss_mb():
    try:
        import psutil
        return psutil.Process().memory_info().rss / 2**20
    except ImportError:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def load_coco(path, image_dir, size, seed):
    d = json.loads(Path(path).read_text(encoding='utf-8'))
    cats = {c['id']: c['name'] for c in d['categories']}
    ids = sorted(i['id'] for i in d['images']); random.Random(seed).shuffle(ids); chosen = set(ids[:size])
    names = {i['id']: i['file_name'] for i in d['images'] if i['id'] in chosen}
    gt = {names[i]: [] for i in chosen}
    for a in d['annotations']:
        if a['image_id'] in chosen:
            x, y, w, h = a['bbox']
            gt[names[a['image_id']]].append((cats[a['category_id']], [x, y, x + w, y + h], bool(a['iscrowd'])))
    missing = [n for n in gt if not (Path(image_dir) / n).exists()]
    if missing: raise SystemExit(f'{len(missing)} subset images are missing from {image_dir}, e.g. {missing[0]}')
    return gt


def precision_recall_at(gt, dets, threshold, iou=0.5):
    """Precision and recall at a confidence threshold, IoU 0.5, one-to-one matching per class (crowd regions ignored)."""
    from benchmark.coco_eval import _iou
    import numpy as np
    tp = fp = npos = 0
    for img, gs in gt.items():
        for k in {g[0] for g in gs} | {d[0] for d in dets.get(img, [])}:
            g = [x for x in gs if x[0] == k]
            gb = np.array([x[1] for x in g], dtype=float).reshape(-1, 4); crowd = np.array([x[2] for x in g], dtype=bool)
            npos += int((~crowd).sum()); taken = np.zeros(len(gb), bool)
            for d in sorted([d for d in dets.get(img, []) if d[0] == k and d[2] >= threshold], key=lambda d: -d[2]):
                ious = _iou(np.asarray(d[1], float), gb, crowd)
                cand = [(ious[i], i) for i in range(len(gb)) if ious[i] >= iou and (crowd[i] or not taken[i])]
                if not cand: fp += 1; continue
                _, i = max(cand)
                if crowd[i]: continue
                taken[i] = True; tp += 1
    return round(tp / max(1, tp + fp), 4), round(tp / max(1, npos), 4)


def run(images, annotations, names, size, seed, threads, out):
    import cv2
    os.environ['CC_DETECTION_THREADS'] = str(threads)
    from app import detection
    from benchmark.coco_eval import evaluate
    gt = load_coco(annotations, images, size, seed)
    files = sorted(gt)
    results = {'measured_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'dataset': f'COCO val2017, seeded subset of {len(files)} images (seed {seed})',
               'labels_licence': 'CC BY 4.0', 'threads': threads, 'opencv': cv2.__version__, 'machine': platform.processor() or platform.machine(),
               'cpus_visible': os.cpu_count(), 'deployment_min_confidence': detection.min_confidence(), 'models': {}}
    for name in names:
        spec = detection.MODELS[name]
        base = _rss_mb()
        try:
            det = detection.load(name)
        except detection.Unavailable as e:
            results['models'][name] = {'status': 'UNAVAILABLE', 'reason': str(e)}; print(name, 'UNAVAILABLE', e, flush=True); continue
        loaded = _rss_mb(); peak = loaded
        dets, ms = {}, []
        det.detect(cv2.imread(str(Path(images) / files[0])), min_conf=0.001)   # warm-up, not timed
        for f in files:
            img = cv2.imread(str(Path(images) / f))
            t = time.perf_counter()
            out_d = det.detect(img, min_conf=0.001)
            ms.append((time.perf_counter() - t) * 1000)
            dets[f] = [(d['label'], d['bbox'], d['confidence']) for d in out_d]
            peak = max(peak, _rss_mb())
        acc = evaluate(gt, dets)
        p, r = precision_recall_at(gt, dets, detection.min_confidence())
        ms.sort()
        results['models'][name] = {
            'status': 'MEASURED', 'version': det.version, 'licence': spec['licence'], 'source': spec['source'], 'input': spec['input'],
            'file_mb': round(Path(det.path).stat().st_size / 2**20, 1), 'published_ap': spec['published_ap'],
            'ap': acc['ap'], 'ap50': acc['ap50'], 'ap75': acc['ap75'], 'classes_with_labels': acc['classes'],
            'at_deployment_threshold': {'min_confidence': detection.min_confidence(), 'precision_iou50': p, 'recall_iou50': r},
            'latency_ms': {'median': round(ms[len(ms) // 2], 1), 'p95': round(ms[int(len(ms) * .95)], 1), 'images': len(ms)},
            'memory_mb': {'model_load': round(loaded - base, 1), 'process_peak': round(peak, 1)},
            'per_class_ap50': acc['per_class_ap50'],
        }
        print(name, {k: v for k, v in results['models'][name].items() if k != 'per_class_ap50'}, flush=True)
        detection._loaded.pop(name, None); del det
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_text(json.dumps(results, indent=1))
    return results


if __name__ == '__main__':
    from app.detection import MODELS
    ap = argparse.ArgumentParser()
    ap.add_argument('--images', required=True); ap.add_argument('--annotations', required=True)
    ap.add_argument('--models', default=','.join(MODELS)); ap.add_argument('--subset-size', type=int, default=500)
    ap.add_argument('--subset-seed', type=int, default=2017); ap.add_argument('--threads', type=int, default=1)
    ap.add_argument('--out', default='benchmark/results/detection.json')
    a = ap.parse_args()
    run(a.images, a.annotations, [m for m in a.models.split(',') if m], a.subset_size, a.subset_seed, a.threads, a.out)
