"""Run the labelled visual-quality benchmark.

Usage:  python -m benchmark.run_benchmark [--split dev|test|all] [--out benchmark/results.json] [--no-render]

Policy thresholds may only be tuned on the 'dev' split (seeds 0-1). The 'test' split (seeds 2-4)
is held out and reported separately. Results are written as JSON for docs/visual-quality-benchmark.md.
"""
import argparse, json, sys, tempfile, time
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from benchmark.fixtures import KINDS, generate, product_image          # noqa: E402
from app.compositor import product_quad_in_surface, render_video        # noqa: E402
from app.quality import evaluate_track, POLICY_VERSION                  # noqa: E402
from app.tracking import track_surface, homography_quads                # noqa: E402
from app.validation import validate_render                              # noqa: E402

SPLITS = {'dev': [0, 1], 'test': [2, 3, 4]}


def run_clip(meta, product, work, render=True):
    t0 = time.perf_counter()
    track = track_surface(meta['path'], meta['quad_norm'], meta['reference_second'], meta['start_second'], meta['end_second'])
    pw, ph = 120, 90
    pq_ref = product_quad_in_surface(track['quad_ref'], pw, ph)
    quads = {i: q for i, q in homography_quads(track, pq_ref).items() if track['start_idx'] <= i <= track['end_idx']}
    report = evaluate_track(track, quads)
    analysis_s = time.perf_counter() - t0
    row = {'kind': meta['kind'], 'seed': meta['seed'], 'suitable': meta['suitable'], 'expected_code': meta['expected_code'],
           'decision': report['status'], 'codes': [f['code'] for f in report['failures']], 'scene_class': report['scene_class'],
           'analysis_seconds': round(analysis_s, 3), 'measurements': report['measurements']}
    # Geometric error against ground truth, for every interval frame the tracker produced.
    errs = []
    for i in range(track['start_idx'], track['end_idx'] + 1):
        if i in quads:
            gt = product_quad_in_surface(meta['gt_surface_quads'][i], pw, ph)
            errs.append(np.linalg.norm(np.asarray(quads[i]) - gt, axis=1))
    if errs:
        e = np.stack(errs)
        per_frame = e.mean(axis=1)
        row['corner_error_px'] = {'mean': round(float(e.mean()), 3), 'p95': round(float(np.percentile(e, 95)), 3), 'max': round(float(e.max()), 3)}
        row['temporal_jitter_px'] = round(float(np.std(np.diff(per_frame))), 3) if len(per_frame) > 2 else None
    if render and report['status'] == 'PASSED':
        out = Path(work) / f"{meta['kind']}_s{meta['seed']}_render.mp4"
        t1 = time.perf_counter()
        render_video(meta['path'], product, out, quads)
        row['render_seconds'] = round(time.perf_counter() - t1, 3)
        t2 = time.perf_counter()
        val = validate_render(meta['path'], out, product, quads)
        row['validation_seconds'] = round(time.perf_counter() - t2, 3)
        row['output_validation'] = val['status']
        row['output_validation_detail'] = {k: val.get(k) for k in ('failures', 'product_psnr_db', 'scene_psnr_db', 'interval_frames_checked', 'interval_frames_expected')}
    return row


def summarise(rows):
    tp = sum(1 for r in rows if r['suitable'] and r['decision'] == 'PASSED')
    fn = sum(1 for r in rows if r['suitable'] and r['decision'] != 'PASSED')
    tn = sum(1 for r in rows if not r['suitable'] and r['decision'] != 'PASSED')
    fp = sum(1 for r in rows if not r['suitable'] and r['decision'] == 'PASSED')
    right_code = sum(1 for r in rows if not r['suitable'] and r['decision'] != 'PASSED' and r['expected_code'] in r['codes'])
    accepted = [r for r in rows if r['decision'] == 'PASSED' and 'corner_error_px' in r]
    return {
        'clips': len(rows), 'suitable': tp + fn, 'unsuitable': tn + fp,
        'true_accept': tp, 'false_reject': fn, 'true_reject': tn, 'false_accept': fp,
        'false_acceptance_rate': round(fp / max(tn + fp, 1), 3), 'false_rejection_rate': round(fn / max(tp + fn, 1), 3),
        'rejections_with_expected_reason_code': f'{right_code}/{tn}',
        'accepted_corner_error_px_mean': round(float(np.mean([r['corner_error_px']['mean'] for r in accepted])), 3) if accepted else None,
        'accepted_corner_error_px_max': round(float(max(r['corner_error_px']['max'] for r in accepted)), 3) if accepted else None,
        'accepted_temporal_jitter_px_max': round(float(max(r['temporal_jitter_px'] or 0 for r in accepted)), 3) if accepted else None,
        'output_validation_passed': f"{sum(1 for r in rows if r.get('output_validation') == 'PASSED')}/{sum(1 for r in rows if 'output_validation' in r)}",
        'median_analysis_seconds': round(float(np.median([r['analysis_seconds'] for r in rows])), 3),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--split', default='all', choices=['dev', 'test', 'all'])
    ap.add_argument('--out', default=str(ROOT / 'benchmark' / 'results.json'))
    ap.add_argument('--no-render', action='store_true')
    args = ap.parse_args()
    splits = ['dev', 'test'] if args.split == 'all' else [args.split]
    result = {'policy_version': POLICY_VERSION, 'dataset': 'synthetic-planar-v1', 'kinds': {k: {'suitable': v[0], 'expected_code': v[1], 'description': v[2]} for k, v in KINDS.items()}, 'splits': {}}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        product = product_image(Path(work) / 'product.png')
        for split in splits:
            rows = []
            for seed in SPLITS[split]:
                for kind in KINDS:
                    meta = generate(kind, seed, Path(work) / 'clips')
                    row = run_clip(meta, product, work, render=not args.no_render)
                    rows.append(row)
                    ok = (row['decision'] == 'PASSED') == meta['suitable']
                    print(f"[{split}] {kind:16s} s{seed} label={'suitable' if meta['suitable'] else 'unsuitable':10s} "
                          f"decision={row['decision']:6s} {'OK ' if ok else 'ERR'} codes={row['codes']} "
                          f"err={row.get('corner_error_px', {}).get('max')} val={row.get('output_validation')}", flush=True)
            result['splits'][split] = {'seeds': SPLITS[split], 'summary': summarise(rows), 'rows': rows}
    Path(args.out).write_text(json.dumps(result, indent=1))
    for split, data in result['splits'].items():
        print(split, json.dumps(data['summary'], indent=1))


if __name__ == '__main__':
    main()
