"""2D compositing quality: v6 compositor vs v8 compositor against ground truth.

Usage: python -m benchmark.run_2d_quality [--seeds 2 3 4] [--out benchmark/results_2d.json]

Both compositors receive the same tracked geometry, so the comparison isolates compositing
(alpha handling, colour space, temporal colour matching). Ground truth is the same scene rendered with
the product inserted in the world plane before camera, lighting and colour changes.
Error is measured per pixel in CIE Lab (deltaE76) inside the product region, and separately on the edge band.
"""
import argparse, json, sys, tempfile, time
from pathlib import Path
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from benchmark.fixtures import generate                                   # noqa: E402
from app.compositor import Planar2DPlan, product_quad_in_surface, temporal_gains, product_mask   # noqa: E402
from app.quality import evaluate_track                                    # noqa: E402
from app.tracking import track_surface, homography_quads                  # noqa: E402

KINDS = ['static', 'pan_zoom', 'perspective', 'handheld', 'mild_lighting', 'exposure_drift', 'wb_drift']


def cutout_product(path, w=150, h=110):
    """Product with a transparent background whose hidden RGB is black (a common PNG export)."""
    img = np.zeros((h, w, 4), np.uint8)
    cv2.ellipse(img, (w // 2, h // 2), (w // 2 - 4, h // 2 - 4), 0, 0, 360, (235, 235, 245, 255), -1, cv2.LINE_AA)
    cv2.ellipse(img, (w // 2, h // 2), (w // 2 - 14, h // 2 - 14), 0, 0, 360, (40, 60, 210, 255), -1, cv2.LINE_AA)
    cv2.putText(img, 'CC', (w // 2 - 28, h // 2 + 14), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255, 255), 3, cv2.LINE_AA)
    # Anti-aliased silhouette: alpha follows the drawn shape; transparent pixels keep RGB = 0.
    a = cv2.GaussianBlur((img[:, :, :3].max(axis=2) > 0).astype(np.float32), (0, 0), .8)
    img[:, :, 3] = (a * 255).round().astype(np.uint8)
    img[img[:, :, 3] == 0, :3] = 0
    cv2.imwrite(str(path), img)
    return str(path)


def legacy_composite(frame, prod, quad):
    """The v6 compositor: straight-alpha warp and blend in sRGB."""
    h, w = frame.shape[:2]
    quad = np.asarray(quad, np.float32)
    x0, y0 = np.floor(quad.min(axis=0)).astype(int); x1, y1 = np.ceil(quad.max(axis=0)).astype(int)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1 + 1), min(h, y1 + 1)
    ph, pw = prod.shape[:2]
    P = cv2.getPerspectiveTransform(np.array([[0, 0], [pw, 0], [pw, ph], [0, ph]], np.float32), quad - np.array([x0, y0], np.float32))
    warped = cv2.warpPerspective(prod, P, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    alpha = warped[:, :, 3:4].astype(np.float32) / 255
    out = frame.copy()
    out[y0:y1, x0:x1] = (out[y0:y1, x0:x1].astype(np.float32) * (1 - alpha) + warped[:, :, :3].astype(np.float32) * alpha).round().clip(0, 255).astype(np.uint8)
    return out


def delta_e(a, b, m):
    la = cv2.cvtColor(a.astype(np.float32) / 255, cv2.COLOR_BGR2Lab); lb = cv2.cvtColor(b.astype(np.float32) / 255, cv2.COLOR_BGR2Lab)
    d = np.sqrt(((la - lb) ** 2).sum(axis=2))
    return float(d[m > 0].mean()) if (m > 0).any() else None


def run_clip(kind, seed, work, product):
    meta = generate(kind, seed, work, product=product)
    track = track_surface(meta['path'], meta['quad_norm'], .4, .4, 2.8)
    prod = cv2.imread(product, cv2.IMREAD_UNCHANGED); ph, pw = prod.shape[:2]
    pq = product_quad_in_surface(track['quad_ref'], pw, ph)
    s, e = track['start_idx'], track['end_idx']
    quads = {i: q for i, q in homography_quads(track, pq).items() if s <= i <= e}
    report = evaluate_track(track, quads)
    gains, gsum = temporal_gains(track, s, e)
    v8 = Planar2DPlan(product, quads, gains)
    v8_nogain = Planar2DPlan(product, quads)
    src, gt = cv2.VideoCapture(meta['path']), cv2.VideoCapture(meta['gt_path'])
    rows = {'v6': [], 'v8_alpha_only': [], 'v8': []}; edge = {'v6': [], 'v8': []}; idx = 0
    t_v6 = t_v8 = 0.0
    while True:
        ok1, fs = src.read(); ok2, fg = gt.read()
        if not (ok1 and ok2): break
        if idx in quads:
            m = product_mask(fs.shape, quads[idx])
            band = cv2.dilate(m, np.ones((3, 3), np.uint8)) - cv2.erode(m, np.ones((5, 5), np.uint8))
            t0 = time.perf_counter(); o6 = legacy_composite(fs, prod, quads[idx]); t_v6 += time.perf_counter() - t0
            t0 = time.perf_counter(); o8 = v8.apply(idx, fs); t_v8 += time.perf_counter() - t0
            rows['v6'].append(delta_e(o6, fg, m)); rows['v8'].append(delta_e(o8, fg, m))
            rows['v8_alpha_only'].append(delta_e(v8_nogain.apply(idx, fs), fg, m))
            edge['v6'].append(delta_e(o6, fg, band)); edge['v8'].append(delta_e(o8, fg, band))
        idx += 1
    src.release(); gt.release()
    n = max(len(rows['v8']), 1)
    out = {'kind': kind, 'seed': seed, 'quality_status': report['status'], 'frames': len(rows['v8']), 'color_match': gsum,
           'compositing_ms_per_frame': {'v6': round(t_v6 / n * 1000, 2), 'v8': round(t_v8 / n * 1000, 2)}}
    for k, v in rows.items():
        out[f'deltaE_{k}'] = {'mean': round(float(np.mean(v)), 3), 'p95': round(float(np.percentile(v, 95)), 3)}
    for k, v in edge.items():
        out[f'edge_deltaE_{k}'] = round(float(np.mean(v)), 3)
    # Temporal flicker of the product's appearance: frame-to-frame change of mean error.
    out['flicker_v6'] = round(float(np.std(np.diff(rows['v6']))), 4); out['flicker_v8'] = round(float(np.std(np.diff(rows['v8']))), 4)
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--seeds', nargs='+', type=int, default=[2, 3, 4])
    ap.add_argument('--out', default=str(ROOT / 'benchmark' / 'results_2d.json')); args = ap.parse_args()
    rows = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        product = cutout_product(Path(work) / 'cutout.png')
        for seed in args.seeds:
            for kind in KINDS:
                r = run_clip(kind, seed, work, product); rows.append(r)
                print(f"{kind:15s} s{seed} gate={r['quality_status']:6s} dE v6={r['deltaE_v6']['mean']:6.2f} alpha-only={r['deltaE_v8_alpha_only']['mean']:6.2f} "
                      f"v8={r['deltaE_v8']['mean']:6.2f} | edge v6={r['edge_deltaE_v6']:6.2f} v8={r['edge_deltaE_v8']:6.2f}", flush=True)
    summary = {}
    for kind in KINDS:
        rk = [r for r in rows if r['kind'] == kind]
        summary[kind] = {k: round(float(np.mean([r[k]['mean'] if isinstance(r[k], dict) else r[k] for r in rk])), 3)
                         for k in ('deltaE_v6', 'deltaE_v8_alpha_only', 'deltaE_v8', 'edge_deltaE_v6', 'edge_deltaE_v8')}
    summary['all'] = {k: round(float(np.mean([summary[x][k] for x in KINDS])), 3) for k in summary[KINDS[0]]}
    summary['clips_where_v8_worse'] = [f"{r['kind']}_s{r['seed']}" for r in rows if r['deltaE_v8']['mean'] > r['deltaE_v6']['mean'] + 0.05]
    Path(args.out).write_text(json.dumps({'metric': 'mean CIE76 deltaE vs ground truth inside product region (lower is better)', 'seeds': args.seeds, 'summary': summary, 'rows': rows}, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
