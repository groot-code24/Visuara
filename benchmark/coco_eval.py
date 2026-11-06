"""COCO-style detection AP in plain NumPy, so the benchmark needs no extra dependency.

It follows the method of the official `pycocotools` evaluator for the "all areas, 100 detections" setting:
  - IoU thresholds 0.50:0.05:0.95; the reported AP is the mean over thresholds and over classes that have ground truth;
  - detections per image are capped at 100, highest score first;
  - each detection, highest score first, matches the unmatched ground truth it overlaps most (IoU >= threshold);
    a detection matching a crowd region is ignored rather than counted as a false positive (crowd IoU is
    intersection over the detection's own area);
  - precision is made monotone and sampled at 101 recall points.
The small/medium/large breakdowns are not computed. `tests/test_detection.py` checks it on cases with known answers.
"""
import numpy as np

THRESHOLDS = np.linspace(0.5, 0.95, 10)
RECALL_POINTS = np.linspace(0.0, 1.0, 101)


def _iou(det, gts, crowd):
    """IoU of one detection (x0,y0,x1,y1) against many ground truths; crowd ones use the detection's area."""
    if not len(gts): return np.zeros(0)
    ix = np.clip(np.minimum(det[2], gts[:, 2]) - np.maximum(det[0], gts[:, 0]), 0, None)
    iy = np.clip(np.minimum(det[3], gts[:, 3]) - np.maximum(det[1], gts[:, 1]), 0, None)
    inter = ix * iy
    da = (det[2] - det[0]) * (det[3] - det[1]); ga = (gts[:, 2] - gts[:, 0]) * (gts[:, 3] - gts[:, 1])
    union = np.where(crowd, da, da + ga - inter)
    return np.where(union > 0, inter / np.maximum(union, 1e-12), 0.0)


def evaluate(ground_truth, detections, max_dets=100):
    """ground_truth: {image: [(class, [x0,y0,x1,y1], is_crowd)]}; detections: {image: [(class, [x0,y0,x1,y1], score)]}.
    Returns {'ap': mean AP .5:.95, 'ap50': AP at .5, 'ap75': AP at .75, 'per_class_ap50': {class: AP}, 'classes': n}."""
    classes = sorted({g[0] for gs in ground_truth.values() for g in gs if not g[2]})
    ap = np.full((len(THRESHOLDS), len(classes)), np.nan)
    capped = {img: sorted(ds, key=lambda d: -d[2])[:max_dets] for img, ds in detections.items()}
    for ci, k in enumerate(classes):
        scores_t = [[] for _ in THRESHOLDS]; tp_t = [[] for _ in THRESHOLDS]; npos = 0
        for img, gs in ground_truth.items():
            g = [x for x in gs if x[0] == k]
            gb = np.array([x[1] for x in g], dtype=np.float64).reshape(-1, 4)
            crowd = np.array([bool(x[2]) for x in g], dtype=bool)
            order = np.argsort(crowd, kind='stable'); gb, crowd = gb[order], crowd[order]   # non-crowd first, as pycocotools
            npos += int((~crowd).sum())
            ds = [d for d in capped.get(img, []) if d[0] == k]
            for ti, t in enumerate(THRESHOLDS):
                taken = np.zeros(len(gb), dtype=bool)
                for d in ds:
                    ious = _iou(np.asarray(d[1], dtype=np.float64), gb, crowd)
                    best, best_iou = -1, min(t, 1 - 1e-10)
                    for gi in range(len(gb)):
                        if taken[gi] and not crowd[gi]: continue
                        if best > -1 and not crowd[best] and crowd[gi]: break   # a real match beats any crowd one
                        if ious[gi] < best_iou: continue
                        best, best_iou = gi, ious[gi]
                    if best == -1:
                        scores_t[ti].append(d[2]); tp_t[ti].append(0)
                    elif crowd[best]:
                        continue                                                  # ignored
                    else:
                        taken[best] = True; scores_t[ti].append(d[2]); tp_t[ti].append(1)
        if npos == 0: continue
        for ti in range(len(THRESHOLDS)):
            if not scores_t[ti]: ap[ti, ci] = 0.0; continue
            order = np.argsort(-np.asarray(scores_t[ti]), kind='mergesort')
            tp = np.asarray(tp_t[ti])[order]; fp = 1 - tp
            tpc, fpc = np.cumsum(tp), np.cumsum(fp)
            rec = tpc / npos; prec = tpc / np.maximum(tpc + fpc, np.spacing(1))
            for i in range(len(prec) - 1, 0, -1): prec[i - 1] = max(prec[i - 1], prec[i])
            idx = np.searchsorted(rec, RECALL_POINTS, side='left')
            q = np.array([prec[i] if i < len(prec) else 0.0 for i in idx])
            ap[ti, ci] = q.mean()
    valid = ~np.isnan(ap[0])
    mean = lambda a: float(np.mean(a[valid])) if valid.any() else 0.0
    return {'ap': round(mean(ap.mean(axis=0)), 4), 'ap50': round(mean(ap[0]), 4), 'ap75': round(mean(ap[5]), 4), 'classes': int(valid.sum()),
            'per_class_ap50': {classes[i]: round(float(ap[0, i]), 4) for i in range(len(classes)) if valid[i]}}
