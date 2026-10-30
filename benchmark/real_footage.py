"""Real-footage benchmark: consented clips, creator-level splits, decisions, blind human realism ratings.

  python -m benchmark.real_footage validate --manifest real/manifest.json
  python -m benchmark.real_footage run      --manifest real/manifest.json --split dev --out real/runs/dev-1
  python -m benchmark.real_footage sheet    --run real/runs/dev-1                      # writes ratings.html for raters
  python -m benchmark.real_footage report   --run real/runs/dev-1 --ratings r1.json r2.json

Rules enforced here (see docs/v9/real-footage-protocol.md):
  * every clip needs a consent record whose scope includes "benchmark", a licence, a creator id and a SHA-256;
    clips without them are refused, and footage is never copied or uploaded by this tool;
  * splits are by creator (a creator's clips are all dev or all test), so thresholds tuned on dev are tested on
    footage from people the tuning never saw;
  * labels are assigned before any run; the report counts only rows labelled real (synthetic smoke-test rows are
    reported separately and never as real-footage evidence).
"""
import argparse, hashlib, html, json, math, random, shutil, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REQUIRED = ('clip_id', 'file', 'sha256', 'creator_id', 'device', 'consent', 'license', 'label')
CRITERIA = [('lighting', 'Lighting and colour match the scene'), ('edges', 'Edges look natural (no halo, cut-out or flicker)'),
            ('contact', 'Contact and shadows make the product sit on the surface'), ('stability', 'Stable over time (no swimming or jitter)'),
            ('fidelity', 'Product looks like the real product (shape, label, colour)'), ('overall', 'Overall: could this have been filmed?')]


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''): h.update(chunk)
    return h.hexdigest()


def split_of(creator_id, salt, dev_share=0.4):
    """Deterministic creator-level split."""
    v = int(hashlib.sha256(f'{salt}:{creator_id}'.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return 'dev' if v < dev_share else 'test'


def validate(manifest_path, check_files=True):
    m = json.loads(Path(manifest_path).read_text(encoding='utf-8')); base = Path(manifest_path).resolve().parent
    errors, clips = [], []
    salt = m.get('split_salt')
    if not salt: errors.append('manifest: split_salt is required (fix it before the first run and never change it)')
    seen = set()
    for c in m.get('clips', []):
        cid = c.get('clip_id', '?')
        missing = [k for k in REQUIRED if not c.get(k)]
        if missing: errors.append(f'{cid}: missing {missing}'); continue
        if cid in seen: errors.append(f'{cid}: duplicate clip_id'); continue
        seen.add(cid)
        consent = c['consent']
        if 'benchmark' not in (consent.get('scope') or []) or not consent.get('granted_by') or not consent.get('date') or not consent.get('reference'):
            errors.append(f'{cid}: consent must name granted_by, date, reference and include "benchmark" in scope'); continue
        lab = c['label']
        if lab.get('mode') not in ('2d', '3d') or not isinstance(lab.get('suitable'), bool) or len(lab.get('quad_norm') or []) != 4:
            errors.append(f'{cid}: label needs mode (2d|3d), suitable (bool) and quad_norm (4 corners)'); continue
        if lab['mode'] == '3d' and not ((lab.get('model3d') or {}).get('surface_size_m') and lab.get('model')):
            errors.append(f'{cid}: 3d labels need model (a .glb path) and model3d.surface_size_m'); continue
        if lab['mode'] == '2d' and not lab.get('product_image'):
            errors.append(f'{cid}: 2d labels need product_image'); continue
        if not all(isinstance(lab.get(k), (int, float)) for k in ('ref_second', 'start_second', 'end_second')):
            errors.append(f'{cid}: label needs ref_second, start_second and end_second'); continue
        path = (base / c['file']).resolve()
        if check_files:
            if not path.is_file(): errors.append(f'{cid}: file not found ({c["file"]})'); continue
            if _sha(path) != c['sha256']: errors.append(f'{cid}: SHA-256 mismatch (file changed since labelling)'); continue
        clips.append({**c, 'path': str(path), 'split': split_of(c['creator_id'], salt) if salt else None, 'synthetic': bool(c.get('synthetic'))})
    creators = {}
    for c in clips: creators.setdefault(c['creator_id'], set()).add(c['split'])
    assert all(len(s) == 1 for s in creators.values())
    return clips, errors


def run(manifest_path, split, out):
    from app.tracking import track_surface
    from app.quality import evaluate_track, evaluate_3d
    from app.render3d import prepare_3d, scene_consistency
    from app.compositor import Planar2DPlan, render_plan
    from app.validation import validate_plan
    clips, errors = validate(manifest_path)
    if errors: raise SystemExit('Manifest invalid:\n  ' + '\n  '.join(errors))
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    rows = []
    for c in [c for c in clips if c['split'] == split]:
        lab = c['label']; t0 = time.perf_counter()
        row = {'clip_id': c['clip_id'], 'creator_id': c['creator_id'], 'device': c['device'], 'mode': lab['mode'], 'synthetic': c['synthetic'],
               'suitable': lab['suitable'], 'expected_code': lab.get('expected_code')}
        try:
            track = track_surface(c['path'], lab['quad_norm'], lab['ref_second'], lab['start_second'], lab['end_second'])
            s, e = track['start_idx'], track['end_idx']
            if lab['mode'] == '3d':
                plan, cam, poses, mo = prepare_3d(track, str((Path(manifest_path).parent / lab['model']).resolve()), {'model3d': lab['model3d'], 'color_match': True}, s, e)
                scene = scene_consistency(c['path'], plan, s, e) if plan else {}
                rep = evaluate_3d(track, plan, cam, poses, mo, scene)
            else:   # 2D: the product image is fitted inside the annotated surface, exactly as the app does
                from PIL import Image
                from app.compositor import prepare_asset, product_quad_in_surface, temporal_gains
                from app.tracking import homography_quads
                derived = out / f"{c['clip_id']}_product.png"
                prepare_asset(str((Path(manifest_path).parent / lab['product_image']).resolve()), derived)
                pw, ph = Image.open(derived).size
                quads = {i: q for i, q in homography_quads(track, product_quad_in_surface(track['quad_ref'], pw, ph)).items() if s <= i <= e}
                rep = evaluate_track(track, quads)
                plan = Planar2DPlan(str(derived), quads, gains=temporal_gains(track, s, e)[0]) if rep['status'] == 'PASSED' else None
            row.update(decision=rep['status'], codes=[f['code'] for f in rep['failures']], analysis_seconds=round(time.perf_counter() - t0, 2))
            if rep['status'] == 'PASSED' and plan is not None:
                render = out / f"{c['clip_id']}_render.mp4"; render_plan(c['path'], plan, render)
                row['render'] = str(render); row['output_validation'] = validate_plan(c['path'], render, plan)['status']
        except Exception as ex:   # a crash is a failure of the product, recorded, never skipped
            row.update(decision='ERROR', codes=['PIPELINE_ERROR'], error=f'{type(ex).__name__}: {str(ex)[:200]}')
        row['source'] = c['path']; rows.append(row)
        print(f"{c['clip_id']:24s} {row['decision']:7s} suitable={row['suitable']} codes={row['codes']}", flush=True)
    res = {'split': split, 'manifest': str(Path(manifest_path).resolve()), 'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'rows': rows}
    (out / 'run.json').write_text(json.dumps(res, indent=1))
    return res


def sheet(run_dir, seed=0):
    """Blind rating page: accepted renders in random order, no labels, no clip ids, no pipeline scores."""
    r = json.loads((Path(run_dir) / 'run.json').read_text())
    items = [x for x in r['rows'] if x.get('render')]
    random.Random(seed).shuffle(items)
    keymap = {f'item{i + 1}': x['clip_id'] for i, x in enumerate(items)}
    (Path(run_dir) / 'sheet_key.json').write_text(json.dumps(keymap, indent=1))   # keep away from raters
    cards = []
    for i, x in enumerate(items):
        k = f'item{i + 1}'
        shutil.copyfile(x['render'], Path(run_dir) / f'{k}.mp4')        # anonymous file name: the clip id must not leak
        sel = ''.join(f'<label>{html.escape(t)}<select name="{k}:{c}" required><option value="">-</option>' +
                      ''.join(f'<option>{v}</option>' for v in range(1, 6)) + '</select></label>' for c, t in CRITERIA)
        cards.append(f'<section><h2>Clip {i + 1}</h2><video controls preload="metadata" src="{k}.mp4"></video>{sel}'
                     f'<label>Notes<textarea name="{k}:notes" rows="2"></textarea></label></section>')
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Realism ratings</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:auto;padding:16px;color:#1e2430;background:#f6f7fb}}section{{background:#fff;border-radius:12px;padding:16px;margin:16px 0}}
video{{width:100%;border-radius:8px;background:#000}}label{{display:flex;justify-content:space-between;gap:12px;margin:8px 0;align-items:center}}textarea{{flex:1}}button{{padding:12px 18px;border:0;border-radius:10px;background:#635bff;color:#fff;font-weight:700}}</style></head>
<body><h1>Realism ratings</h1><p>Rate each clip from 1 (clearly fake) to 5 (indistinguishable from filmed). Some clips may contain no edits at all. Judge only what you see.</p>
<label>Your rater id<input id="rater" required></label><form id="f">{''.join(cards)}</form><button onclick="save()">Download my ratings</button>
<script>function save(){{const d={{rater:document.getElementById('rater').value,ratings:{{}}}};for(const el of document.querySelectorAll('#f [name]')){{const[k,c]=el.name.split(':');(d.ratings[k]=d.ratings[k]||{{}})[c]=c==='notes'?el.value:(el.value?+el.value:null)}}
const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(d,null,1)],{{type:'application/json'}}));a.download='ratings_'+(d.rater||'anon')+'.json';a.click()}}</script></body></html>'''
    (Path(run_dir) / 'ratings.html').write_text(page, encoding='utf-8')
    return {'items': len(items), 'page': str(Path(run_dir) / 'ratings.html')}


def wilson(k, n, z=1.96):
    if n == 0: return (None, None)
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round((c - r) / d, 4), round((c + r) / d, 4))


def krippendorff_interval(units):
    """Krippendorff's alpha (interval metric). units: list of lists of numeric ratings for the same item."""
    units = [[v for v in u if v is not None] for u in units]; units = [u for u in units if len(u) >= 2]
    vals = [v for u in units for v in u]; n = len(vals)
    if n < 2 or not units: return None
    do = sum(sum((a - b) ** 2 for i, a in enumerate(u) for j, b in enumerate(u) if i != j) / (len(u) - 1) for u in units) / n
    de = sum((a - b) ** 2 for i, a in enumerate(vals) for j, b in enumerate(vals) if i != j) / (n * (n - 1))
    return None if de == 0 else round(1 - do / de, 4)


def report(run_dir, rating_files=()):
    r = json.loads((Path(run_dir) / 'run.json').read_text())
    real = [x for x in r['rows'] if not x['synthetic']]; synth = [x for x in r['rows'] if x['synthetic']]
    def gate(rows):
        neg = [x for x in rows if not x['suitable']]; pos = [x for x in rows if x['suitable']]
        fa = sum(x['decision'] == 'PASSED' for x in neg); fr = sum(x['decision'] != 'PASSED' for x in pos)
        return {'clips': len(rows), 'creators': len({x['creator_id'] for x in rows}), 'devices': len({x['device'] for x in rows}),
                'false_accept': f'{fa}/{len(neg)}', 'false_accept_rate_95ci': wilson(fa, len(neg)),
                'false_reject': f'{fr}/{len(pos)}', 'false_reject_rate_95ci': wilson(fr, len(pos)),
                'errors': sum(x['decision'] == 'ERROR' for x in rows),
                'expected_code_present': f"{sum(1 for x in neg if x.get('expected_code') in x.get('codes', []))}/{sum(1 for x in neg if x.get('expected_code'))}",
                'output_validation_passed': f"{sum(x.get('output_validation') == 'PASSED' for x in rows)}/{sum('output_validation' in x for x in rows)}"}
    out = {'split': r['split'], 'real_footage': gate(real), 'synthetic_smoke_rows_not_evidence': gate(synth) if synth else None}
    if rating_files:
        key = json.loads((Path(run_dir) / 'sheet_key.json').read_text())
        sheets = [json.loads(Path(f).read_text()) for f in rating_files]
        synthetic_ids = {x['clip_id'] for x in synth}
        crit = {}
        for c, _ in CRITERIA:
            units = [[s['ratings'].get(item, {}).get(c) for s in sheets] for item, cid in key.items() if cid not in synthetic_ids]
            flat = [v for u in units for v in u if v is not None]
            mean = sum(flat) / len(flat) if flat else None
            sd = math.sqrt(sum((v - mean) ** 2 for v in flat) / (len(flat) - 1)) if len(flat) > 1 else None
            crit[c] = {'mean': round(mean, 3) if mean is not None else None, 'n': len(flat),
                       'mean_95ci_halfwidth': round(1.96 * sd / math.sqrt(len(flat)), 3) if sd else None,
                       'share_rated_4_or_5': round(sum(v >= 4 for v in flat) / len(flat), 3) if flat else None,
                       'krippendorff_alpha_interval': krippendorff_interval(units)}
        out['ratings'] = {'raters': len(sheets), 'criteria': crit}
    (Path(run_dir) / 'report.json').write_text(json.dumps(out, indent=1))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter); sub = ap.add_subparsers(dest='cmd', required=True)
    v = sub.add_parser('validate'); v.add_argument('--manifest', required=True)
    r = sub.add_parser('run'); r.add_argument('--manifest', required=True); r.add_argument('--split', choices=('dev', 'test'), required=True); r.add_argument('--out', required=True)
    s = sub.add_parser('sheet'); s.add_argument('--run', required=True); s.add_argument('--seed', type=int, default=0)
    p = sub.add_parser('report'); p.add_argument('--run', required=True); p.add_argument('--ratings', nargs='*', default=[])
    a = ap.parse_args()
    if a.cmd == 'validate':
        clips, errors = validate(a.manifest)
        print(json.dumps({'valid_clips': len(clips), 'real': sum(not c['synthetic'] for c in clips), 'creators': len({c['creator_id'] for c in clips}),
                          'dev': sum(c['split'] == 'dev' for c in clips), 'test': sum(c['split'] == 'test' for c in clips), 'errors': errors}, indent=1))
        sys.exit(1 if errors else 0)
    if a.cmd == 'run': run(a.manifest, a.split, a.out)
    elif a.cmd == 'sheet': print(json.dumps(sheet(a.run, a.seed)))
    else: print(json.dumps(report(a.run, a.ratings), indent=1))


if __name__ == '__main__':
    main()
