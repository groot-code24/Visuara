"""TRACKED_3D benchmark: camera estimation, pose, placement accuracy, temporal stability and safe rejection.

Usage: python -m benchmark.run_3d_benchmark [--seeds 5 6 7] [--kinds ...] [--out benchmark/results_3d_v9.json]

Seeds: 0-1 dev (design), 2-4 V8 held-out (since inspected), 5-7 V9 held-out (since inspected), 8-10 V10 held-out
(first used after the V10 pose-aware plane tolerance was frozen).
"""
import argparse, json, sys, tempfile, time
from pathlib import Path
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from benchmark.fixtures3d import KINDS3D, generate3d, F_TRUE, K_TRUE    # noqa: E402
from benchmark.glb_assets import bottle_glb                              # noqa: E402
from app.tracking import track_surface                                   # noqa: E402
from app.render3d import prepare_3d, foreground_motion, scene_consistency, _project   # noqa: E402
from app.quality import evaluate_3d                                      # noqa: E402
from app.compositor import render_plan                                   # noqa: E402
from app.validation import validate_plan                                 # noqa: E402


def rot_err(Ra, Rb):
    return float(np.degrees(np.arccos(np.clip((np.trace(Ra.T @ Rb) - 1) / 2, -1, 1))))


def run_clip(kind, seed, work, model, render=True):
    meta = generate3d(kind, seed, work, model)
    t0 = time.perf_counter()
    track = track_surface(meta['path'], meta['quad_norm'], .4, .4, 2.8)
    s, e = track['start_idx'], track['end_idx']
    opts = {'model3d': meta['model3d'], 'color_match': True}
    plan, cam, poses, mo = prepare_3d(track, model, opts, s, e)
    fg = scene_consistency(meta['path'], plan, s, e) if plan else {}
    report = evaluate_3d(track, plan, cam, poses, mo, fg)
    # V9 fixed 1 px plane tolerance on the same clip, for comparison only (not used for the decision)
    fixed = evaluate_3d(track, plan, cam, poses, mo, scene_consistency(meta['path'], plan, s, e, pose_aware_plane=False)) if plan else report
    row = {'kind': kind, 'seed': seed, 'suitable': meta['suitable'], 'expected_code': meta['expected_code'], 'decision': report['status'],
           'codes': [f['code'] for f in report['failures']], 'camera': report['camera'], 'analysis_seconds': round(time.perf_counter() - t0, 2),
           'v9_fixed_tolerance_decision': fixed['status'], 'v9_fixed_tolerance_codes': [f['code'] for f in fixed['failures']],
           'plane_tolerance_px': fg.get('plane_tolerance_px') if fg else None}
    if plan is not None:   # V8 check, for comparison only (not used for the decision)
        legacy = foreground_motion(meta['path'], plan.object_rois(track['width'], track['height']), s, e)
        row['scene'] = {'moving_max': max(fg['moving'].values(), default=0), 'occluded_max': max(fg['occluded'].values(), default=0),
                        'legacy_v8_moving_max': max(legacy.values(), default=0),
                        'legacy_v8_would_reject': any(v > 0.03 for v in legacy.values())}
    if plan is not None:
        row['focal_error_pct'] = round(abs(cam['focal_px'] - F_TRUE) / F_TRUE * 100, 3)
    if report['status'] == 'PASSED':
        gt = meta['gt_plan']; base = np.array([[np.mean(plan.foot[:, 0]), np.mean(plan.foot[:, 1]), 0.0]])
        rerr, terr, aerr, ious = [], [], [], []
        for i in range(s, e + 1):
            pe, pt = poses[i], meta['true_poses'][i]
            rerr.append(rot_err(pe['R'], pt['R'])); terr.append(np.linalg.norm(pe['t'] - pt['t']) / np.linalg.norm(pt['t']) * 100)
            aerr.append(float(np.linalg.norm(_project(base, pe['R'], pe['t'], plan.K)[0] - _project(base, pt['R'], pt['t'], K_TRUE)[0])))
            shape = (track['height'], track['width'])
            la, lb = plan._layers(i, shape), gt._layers(i, shape)
            ma = np.zeros(shape, bool); mb = np.zeros(shape, bool)
            ma[la[1]:la[3], la[0]:la[2]] = la[5][:, :, 0] > .5; mb[lb[1]:lb[3], lb[0]:lb[2]] = lb[5][:, :, 0] > .5
            ious.append((ma & mb).sum() / max((ma | mb).sum(), 1))
        row.update(rotation_error_deg={'mean': round(float(np.mean(rerr)), 3), 'max': round(float(np.max(rerr)), 3)},
                   translation_error_pct={'mean': round(float(np.mean(terr)), 3), 'max': round(float(np.max(terr)), 3)},
                   anchor_error_px={'mean': round(float(np.mean(aerr)), 3), 'max': round(float(np.max(aerr)), 3)},
                   anchor_jitter_px=round(float(np.std(np.diff(aerr))), 4),
                   silhouette_iou={'mean': round(float(np.mean(ious)), 4), 'min': round(float(np.min(ious)), 4)})
        if render:
            out = Path(work) / f'{kind}_s{seed}_render.mp4'
            t1 = time.perf_counter(); render_plan(meta['path'], plan, out); row['render_seconds'] = round(time.perf_counter() - t1, 2)
            val = validate_plan(meta['path'], out, plan); row['output_validation'] = val['status']
            # Appearance vs ground truth inside the object/shadow region (geometry errors show up here).
            ca, cb = cv2.VideoCapture(str(out)), cv2.VideoCapture(meta['gt_path']); idx = 0; des = []
            while True:
                oa, fa = ca.read(); ob, fb = cb.read()
                if not (oa and ob): break
                if idx in plan.indices:
                    m = plan.mask(idx, fa.shape) | gt.mask(idx, fa.shape)
                    la = cv2.cvtColor(fa.astype(np.float32) / 255, cv2.COLOR_BGR2Lab); lb = cv2.cvtColor(fb.astype(np.float32) / 255, cv2.COLOR_BGR2Lab)
                    des.append(float(np.sqrt(((la - lb) ** 2).sum(axis=2))[m > 0].mean()))
                idx += 1
            ca.release(); cb.release()
            row['deltaE_vs_gt'] = round(float(np.mean(des)), 3)
    return row


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--seeds', nargs='+', type=int, default=[5, 6, 7])
    ap.add_argument('--kinds', nargs='+', default=list(KINDS3D))
    ap.add_argument('--out', default=str(ROOT / 'benchmark' / 'results_3d_v10.json')); ap.add_argument('--no-render', action='store_true')
    args = ap.parse_args(); rows = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        model = bottle_glb(Path(work) / 'bottle.glb')
        for seed in args.seeds:
            for kind in args.kinds:
                r = run_clip(kind, seed, work, model, not args.no_render); rows.append(r)
                ok = (r['decision'] == 'PASSED') == r['suitable']
                print(f"{kind:17s} s{seed} {'OK ' if ok else 'ERR'} {r['decision']:6s} codes={r['codes']} f_err%={r.get('focal_error_pct')} "
                      f"rot={r.get('rotation_error_deg', {}).get('max')} anchor={r.get('anchor_error_px', {}).get('max')} iou={r.get('silhouette_iou', {}).get('min')} "
                      f"val={r.get('output_validation')} dE={r.get('deltaE_vs_gt')}", flush=True)
    acc = [r for r in rows if r['decision'] == 'PASSED']
    summary = {
        'clips': len(rows), 'suitable': sum(r['suitable'] for r in rows),
        'false_accept': sum(1 for r in rows if not r['suitable'] and r['decision'] == 'PASSED'),
        'false_reject': sum(1 for r in rows if r['suitable'] and r['decision'] != 'PASSED'),
        'rejections_with_expected_code': f"{sum(1 for r in rows if not r['suitable'] and r['expected_code'] in r['codes'])}/{sum(1 for r in rows if not r['suitable'])}",
        'accepted_focal_error_pct_max': max((r['focal_error_pct'] for r in acc if 'focal_error_pct' in r), default=None),
        'accepted_rotation_error_deg_max': max((r['rotation_error_deg']['max'] for r in acc), default=None),
        'accepted_anchor_error_px_mean': round(float(np.mean([r['anchor_error_px']['mean'] for r in acc])), 3) if acc else None,
        'accepted_anchor_error_px_max': max((r['anchor_error_px']['max'] for r in acc), default=None),
        'accepted_silhouette_iou_min': min((r['silhouette_iou']['min'] for r in acc), default=None),
        'output_validation_passed': f"{sum(1 for r in acc if r.get('output_validation') == 'PASSED')}/{sum(1 for r in acc if 'output_validation' in r)}",
        'legacy_v8_check_false_rejects': sum(1 for r in rows if r['suitable'] and r.get('scene', {}).get('legacy_v8_would_reject')),
        'v9_fixed_tolerance_false_accept': sum(1 for r in rows if not r['suitable'] and r['v9_fixed_tolerance_decision'] == 'PASSED'),
        'v9_fixed_tolerance_false_reject': sum(1 for r in rows if r['suitable'] and r['v9_fixed_tolerance_decision'] != 'PASSED'),
        'decisions_changed_vs_v9': [f"{r['kind']} s{r['seed']}: {r['v9_fixed_tolerance_decision']} -> {r['decision']}" for r in rows if r['v9_fixed_tolerance_decision'] != r['decision']],
        'median_analysis_seconds': round(float(np.median([r['analysis_seconds'] for r in rows])), 2),
    }
    Path(args.out).write_text(json.dumps({'seeds': args.seeds, 'summary': summary, 'rows': rows}, indent=1, default=float))
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
