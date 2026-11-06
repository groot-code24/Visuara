"""Decision engine: given 1-4 products and one or more videos, decide which product fits where.

For every video the engine
  1. finds candidate flat surfaces on the analysed keyframes (contour quadrilaterals with enough texture to track),
  2. tracks each candidate through a short window with the same tracker the renderer uses,
  3. scores every product on it: the placement quality policy must pass (quality.evaluate_track, the same check a
     placement goes through later), then screen time, on-screen size, how well the product's shape fills the surface,
     tracking stability, contrast against the surface, and how well the surface type suits the product category
     (plus scene keywords when a local vision model describes keyframes),
  4. assigns products to videos exactly (dynamic programming) to maximise the total score while spreading the
     products across the videos.

Every recommendation is explained in plain language. A recommendation becomes a draft placement only when a person
applies it, and is rendered only after its own quality check passes and a manager approves it. Scores are heuristic
(weights below), not calibrated probabilities; they were developed on synthetic footage.
"""
import json, math, os, secrets, time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import main as m, jobrunner, limits, obs, storage

router = APIRouter()
ENGINE_VERSION = 'decision-v1.0-heuristic'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, name TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'QUEUED', product_ids TEXT NOT NULL, video_ids TEXT NOT NULL, spread INTEGER NOT NULL DEFAULT 1, progress REAL NOT NULL DEFAULT 0, stage TEXT NOT NULL DEFAULT '', result_json TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0, lease_until REAL, lease_token TEXT, created_by INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, started_at TEXT, finished_at TEXT);
CREATE INDEX IF NOT EXISTS idx_decisions_org ON decisions(org_id,id);
CREATE INDEX IF NOT EXISTS idx_decisions_queue ON decisions(status,id);
'''

# Category -> (label, affinity by surface orientation, scene keywords that suggest a good context)
CATEGORIES = {
    'beverage': ('Beverage', {'vertical': .80, 'horizontal': .75, 'angled': .70}, ('kitchen', 'cafe', 'bar', 'table', 'fridge', 'drink', 'restaurant', 'counter', 'party')),
    'food':     ('Food & snacks', {'vertical': .65, 'horizontal': .95, 'angled': .70}, ('kitchen', 'table', 'dining', 'restaurant', 'counter', 'picnic', 'plate')),
    'beauty':   ('Beauty & personal care', {'vertical': .70, 'horizontal': .85, 'angled': .70}, ('bathroom', 'vanity', 'mirror', 'bedroom', 'salon', 'dresser', 'sink')),
    'fashion':  ('Fashion & accessories', {'vertical': .90, 'horizontal': .55, 'angled': .70}, ('bedroom', 'closet', 'street', 'store', 'wardrobe', 'boutique', 'mirror')),
    'tech':     ('Tech & electronics', {'vertical': 1.0, 'horizontal': .65, 'angled': .75}, ('desk', 'office', 'laptop', 'monitor', 'studio', 'screen', 'computer', 'setup')),
    'home':     ('Home & living', {'vertical': .80, 'horizontal': .85, 'angled': .70}, ('living room', 'sofa', 'shelf', 'bedroom', 'kitchen', 'couch', 'wall')),
    'fitness':  ('Sports & fitness', {'vertical': .90, 'horizontal': .55, 'angled': .70}, ('gym', 'park', 'outdoor', 'yoga', 'track', 'field', 'court')),
    'other':    ('Other', {'vertical': .75, 'horizontal': .75, 'angled': .70}, ()),
}
WEIGHTS = {'screen_time': .25, 'size': .20, 'fill': .15, 'stability': .15, 'contrast': .10, 'context': .15}
QUALITY_FAIL_FACTOR = .4      # a candidate that would fail the placement quality check keeps 40% of its score
MIN_RECOMMEND_SCORE = .55     # below this a video gets "no confident fit" rather than a weak recommendation
LEASE_SECONDS = 300
MAX_ATTEMPTS = 2


def window_seconds(): return float(os.getenv('CC_DECISION_WINDOW_SECONDS', '5'))
def min_seconds(): return float(os.getenv('CC_DECISION_MIN_SECONDS', '1.2'))
def max_candidates(): return int(os.getenv('CC_DECISION_CANDIDATES_PER_VIDEO', '4'))


class _Cancelled(Exception): pass
class _Shutdown(Exception): pass


# ---------------------------------------------------------------- surface candidates
def _order_quad(pts):
    centre = pts.mean(axis=0)
    pts = pts[np.argsort(np.arctan2(pts[:, 1] - centre[1], pts[:, 0] - centre[0]))]
    return np.roll(pts, -int(np.argmin(pts.sum(axis=1))), axis=0)        # TL, TR, BR, BL


def orientation(q):
    """'vertical' (wall, screen, sign), 'horizontal' (table, floor, counter seen at an angle) or 'angled'."""
    q = np.asarray(q, dtype=np.float64)
    def tilt(a, b):
        d = b - a
        return math.degrees(math.atan2(abs(d[0]), abs(d[1]) + 1e-9))
    side_tilt = max(tilt(q[0], q[3]), tilt(q[1], q[2]))
    top, bottom = np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3])
    if top < .85 * bottom and side_tilt >= 6: return 'horizontal'
    if side_tilt < 12: return 'vertical'
    return 'angled'


def detect_surfaces(image, max_out=6):
    """Flat, textured quadrilaterals in a keyframe (BGR). Returns dicts with a normalised quad and evidence."""
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    found = []
    for lo, hi, dil in ((60, 160, 0), (30, 100, 1)):
        edges = cv2.Canny(gray, lo, hi)
        if dil: edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:120]:
            area = cv2.contourArea(contour) / (w * h)
            if area < .02 or area > .55: continue
            approx = cv2.approxPolyDP(contour, .025 * cv2.arcLength(contour, True), True)
            if len(approx) != 4 or not cv2.isContourConvex(approx): continue
            pts = _order_quad(approx.reshape(4, 2).astype(np.float64))
            mask = np.zeros((h, w), np.uint8); cv2.fillConvexPoly(mask, pts.astype(np.int32), 255)
            mask = cv2.erode(mask, np.ones((5, 5), np.uint8))
            if not (mask > 0).any(): continue
            texture = min(1.0, float(lap[mask > 0].var()) / 250.0)
            sides = [np.linalg.norm(pts[(i + 1) % 4] - pts[i]) for i in range(4)]
            aspect = max(sides[0], 1) / max(sides[1], 1)
            if min(sides) < 12 or not .15 < aspect < 6.5: continue
            x0, y0 = pts.min(axis=0); x1, y1 = pts.max(axis=0)
            if x0 < 2 and y0 < 2 and x1 > w - 3 and y1 > h - 3: continue        # the frame border itself
            # Poster- to tabletop-sized surfaces score best; near-full-frame quads are usually the background itself.
            area_score = area / .03 if area < .03 else 1.0 if area <= .3 else max(0.0, 1 - (area - .3) / .3)
            confidence = .35 * area_score + .45 * texture + .2
            found.append({'quad_px': pts, 'area_fraction': area, 'texture': texture, 'aspect': aspect, 'confidence': confidence,
                          'bbox': (x0, y0, x1, y1), 'orientation': orientation(pts), 'mask': mask})
    found.sort(key=lambda c: c['confidence'], reverse=True)
    kept = []
    for cand in found:
        if all(_iou(cand['bbox'], k['bbox']) < .6 for k in kept): kept.append(cand)
        if len(kept) >= max_out: break
    for k in kept:
        k['quad'] = (k['quad_px'] / np.array([w, h])).clip(0, 1).tolist()
    return kept


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy; union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _lab(bgr_pixels):
    px = np.asarray(bgr_pixels, np.float32).reshape(-1, 1, 3) / 255.0
    return cv2.cvtColor(px, cv2.COLOR_BGR2Lab).reshape(-1, 3).mean(axis=0)


# ---------------------------------------------------------------- scoring
def _poly_area(q): return abs(cv2.contourArea(np.asarray(q, np.float32)))


def _size_score(f):
    if f < .004: return 0.0
    if f < .03: return (f - .004) / .026
    if f <= .35: return 1.0
    return max(.3, 1 - (f - .35) / .4)


def _semantic(desc):
    return bool(desc) and not desc.startswith('Candidate scene at')


def score_option(report, track, pq, product, surface_lab, frame_desc):
    """Score one product on one tracked surface. Returns the components, total and plain-language reasons."""
    fps = track['fps']; s, e = track['start_idx'], track['end_idx']
    secs = (e - s + 1) / fps
    frame_area = track['width'] * track['height']
    prod_frac = _poly_area(pq) / frame_area
    fill = min(1.0, _poly_area(pq) / max(_poly_area(track['quad_ref']), 1e-6))
    meas = report['measurements']
    ncc = (meas.get('ncc') or {}).get('median') or 0.0
    rmse = (meas.get('rmse_px') or {}).get('median') or 0.0
    stability = .7 * min(1.0, max(0.0, (ncc - .7) / .25)) + .3 * min(1.0, max(0.0, 1 - rmse / 2.0))
    d_e = float(np.linalg.norm(product['lab'] - surface_lab))
    contrast = min(1.0, d_e / 30.0)
    orient = orientation(track['quad_ref'])
    label, affinity, keywords = CATEGORIES.get(product['category'], CATEGORIES['other'])
    context = affinity[orient]
    kw_hit = None
    if _semantic(frame_desc) and keywords:
        text = frame_desc.lower(); kw_hit = next((k for k in keywords if k in text), None)
        context = .7 * context + .3 * (1.0 if kw_hit else .3)
    comp = {'screen_time': min(1.0, secs / 4.0), 'size': _size_score(prod_frac), 'fill': fill, 'stability': stability,
            'contrast': contrast, 'context': context}
    total = sum(WEIGHTS[k] * v for k, v in comp.items())
    passed = report['status'] == 'PASSED'
    if not passed: total *= QUALITY_FAIL_FACTOR
    where = {'vertical': 'an upright flat surface (wall, sign or screen)', 'horizontal': 'a flat surface seen at an angle (table, counter or floor)',
             'angled': 'a flat surface at an angle to the camera'}[orient]
    reasons = [f'Sits on {where}, visible for {secs:.1f} s from {_ts(s / fps)}.',
               f'Fills {fill * 100:.0f}% of the surface and {prod_frac * 100:.1f}% of the frame.']
    if passed: reasons.append(f'Passes the automated quality check (tracking match {ncc:.2f}).')
    else: reasons.append('Would fail the quality check: ' + '; '.join(f['message'].rstrip('.') for f in report['failures'][:2]) + '.')
    reasons.append(('Clear contrast against the surface.' if contrast >= .6 else 'Similar colours to the surface, so it may blend in.'))
    reasons.append(f'{"Good" if affinity[orient] >= .8 else "Fair"} context for {label.lower()} products' + (f' (scene mentions "{kw_hit}").' if kw_hit else '.'))
    return {'score': round(total, 4), 'components': {k: round(v, 3) for k, v in comp.items()}, 'passes_quality': passed,
            'quality_status': report['status'], 'failure_codes': [f['code'] for f in report['failures']],
            'reasons': reasons, 'orientation': orient, 'duration_s': round(secs, 2)}


def _ts(sec): return f'{int(sec // 60)}:{sec % 60:04.1f}'


def evaluate_with_trim(track, pq_ref, ms):
    """Evaluate the product on the tracked surface; when it fails part-way, shorten the window to the frames before
    the first failure and re-check (never extend). Returns (report, track_view)."""
    from .tracking import homography_quads
    from . import quality
    all_quads = homography_quads(track, pq_ref)
    view = dict(track)
    for _ in range(4):
        quads = {i: q for i, q in all_quads.items() if view['start_idx'] <= i <= view['end_idx']}
        report = quality.evaluate_track(view, quads)
        if report['status'] == 'PASSED': return report, view
        codes = {f['code'] for f in report['failures']}
        if codes & {'LOW_TEXTURE', 'INCOMPLETE_VALIDATION'} and len(codes) == 1: return report, view
        starts = [ts[0] for f in report['failures'] for ts in f['timestamps']]
        if not starts: return report, view
        new_end = int(math.floor(min(starts) * track['fps'])) - 2
        if new_end - view['start_idx'] + 1 < ms * track['fps'] or new_end >= view['end_idx']: return report, view
        view = dict(view, end_idx=new_end)
    return report, view


def assign(videos, product_ids, spread=True, min_score=MIN_RECOMMEND_SCORE):
    """Exact assignment of at most one product per video maximising the total score of confident options.
    With spread, each product is used at most ceil(videos/products) times (at most once when there are fewer videos)."""
    n = len(product_ids)
    cap = max(1, math.ceil(len(videos) / n)) if spread else len(videos)
    best = {(0,) * n: (0, 0.0, [])}
    for v in videos:
        eligible = {o['product_id']: o['score'] for o in v.get('options', []) if o['passes_quality'] and o['score'] >= min_score}
        nxt = {}
        for counts, (k, total, picks) in best.items():
            cand = [((counts, (k, total, picks + [None])))]
            for j, pid in enumerate(product_ids):
                if pid in eligible and counts[j] < cap:
                    c2 = counts[:j] + (counts[j] + 1,) + counts[j + 1:]
                    cand.append((c2, (k + 1, total + eligible[pid], picks + [pid])))
            for c2, val in cand:
                if c2 not in nxt or _rank(val) > _rank(nxt[c2]): nxt[c2] = val
        best = nxt
    return max(best.values(), key=_rank)[2] if best else []


def _rank(val):
    """More placements, then a higher total; on ties, earlier videos get a product (deterministic)."""
    k, total, picks = val
    return (k, round(total, 9), [p is not None for p in picks])


# ---------------------------------------------------------------- engine
def _product_info(prod):
    from .compositor import load_product
    img = load_product(m._asset_path(prod))
    alpha = img[:, :, 3]
    opaque = img[:, :, :3][alpha > 128] if (alpha > 128).any() else img[:, :, :3].reshape(-1, 3)
    return {'id': prod['id'], 'name': prod['name'], 'category': prod.get('category') or 'other', 'width': img.shape[1], 'height': img.shape[0],
            'aspect': round(img.shape[1] / img.shape[0], 3), 'lab': _lab(opaque), 'image': img}


def _preview(did, vid, kf_path, product, quad_norm):
    from .compositor import composite_frame
    frame = cv2.imread(storage.local(kf_path))
    if frame is None: return None
    h, w = frame.shape[:2]
    out = composite_frame(frame, product['image'], np.asarray(quad_norm) * np.array([w, h]))
    path = m.DATA / 'decisions' / f'decision_{did}_v{vid}_p{product["id"]}.jpg'
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), out, [cv2.IMWRITE_JPEG_QUALITY, 88]); storage.persist(path)
    return str(path)


def analyse_video(did, v, products, creator, renew, ms, resume=None, save=None):
    """Options for every product on this video (best surface per product), with previews. `save` records progress after each
    surface and `resume` continues from it, so an analysis can span several requests where each has a time limit."""
    from .tracking import track_surface, validate_quad
    from .compositor import product_quad_in_surface
    with m.db() as c: rows = [dict(r) for r in c.execute('SELECT second,image_path,description FROM frames WHERE video_id=? ORDER BY second', (v['id'],)).fetchall()]
    if not rows:
        m.analyze_keyframes(v, creator)
        with m.db() as c: rows = [dict(r) for r in c.execute('SELECT second,image_path,description FROM frames WHERE video_id=? ORDER BY second', (v['id'],)).fetchall()]
    src = storage.local(v['source_path'])
    cap = cv2.VideoCapture(src); fps = cap.get(cv2.CAP_PROP_FPS) or 25.0; cap.release()
    cands = []
    for r in rows:
        img = cv2.imread(storage.local(r['image_path']))
        if img is None or v['duration'] - r['second'] < ms: continue
        for k in detect_surfaces(img)[:2]:
            k.update(frame=r, lab=_lab(img[k['mask'] > 0])); cands.append(k)
    cands.sort(key=lambda k: k['confidence'], reverse=True)
    cands = cands[:max_candidates()]
    best = {int(pid): o for pid, o in (resume or {}).get('best', {}).items()}     # product id -> best option
    examined = list((resume or {}).get('examined', []))
    for i, k in enumerate(cands):
        if i < (resume or {}).get('next', 0): continue
        renew()
        kf = round(k['frame']['second'] * fps) / fps
        end = min(v['duration'] - 1 / fps, kf + window_seconds())
        # Exactly the corners a placement made from this option will store (rounded, float32), so the engine's quality
        # verdict is the same computation the placement's own quality check repeats.
        quad = validate_quad([[round(x, 5), round(y, 5)] for x, y in k['quad']]).tolist()
        try:
            track = track_surface(src, quad, kf, kf, end, progress=lambda _i: renew())
        except (_Cancelled, _Shutdown): raise
        except Exception as e:
            examined.append({'frame_second': kf, 'quad': quad, 'error': str(e)[:160]})
            if save: save({'video_id': v['id'], 'next': i + 1, 'best': best, 'examined': examined})
            continue
        examined.append({'frame_second': round(kf, 3), 'quad': quad, 'orientation': k['orientation'], 'surface_confidence': round(k['confidence'], 3)})
        for p in products:
            pq = product_quad_in_surface(track['quad_ref'], p['width'], p['height'])
            report, view = evaluate_with_trim(track, pq, ms)
            opt = score_option(report, view, pq, p, k['lab'], k['frame']['description'])
            # Rounded so that media.interval_frames maps them back to exactly the frames evaluated here.
            start_s = math.floor(view['start_idx'] / fps * 1e6) / 1e6
            opt.update(product_id=p['id'], frame_second=start_s, start_second=start_s,
                       end_second=math.ceil(view['end_idx'] / fps * 1e6) / 1e6, quad=quad,
                       product_quad=[[round(pt[0] / track['width'], 5), round(pt[1] / track['height'], 5)] for pt in np.asarray(pq).tolist()],
                       keyframe=k['frame']['image_path'])
            if p['id'] not in best or (opt['passes_quality'], opt['score']) > (best[p['id']]['passes_quality'], best[p['id']]['score']):
                best[p['id']] = opt
        if save: save({'video_id': v['id'], 'next': i + 1, 'best': best, 'examined': examined})
    options = sorted(best.values(), key=lambda o: (o['passes_quality'], o['score']), reverse=True)
    for o in options:
        prod = next(p for p in products if p['id'] == o['product_id'])
        o['preview'] = _preview(did, v['id'], o.pop('keyframe'), prod, o['product_quad'])
    return {'video_id': v['id'], 'options': options, 'candidates_examined': examined, 'keyframes': len(rows)}


def run(did, token, stop=None):
    with m.db() as c:
        d = dict(c.execute('SELECT * FROM decisions WHERE id=?', (did,)).fetchone())
        creator = dict(c.execute('SELECT * FROM users WHERE id=?', (d['created_by'],)).fetchone()); creator['org_id'] = d['org_id']   # acts in the decision's organization
        pids, vids = json.loads(d['product_ids']), json.loads(d['video_ids'])
        prods = [dict(r) for r in c.execute(f'SELECT * FROM products WHERE org_id=? AND id IN ({",".join("?" * len(pids))})', (d['org_id'], *pids)).fetchall()]
        vrows = {r['id']: dict(r) for r in c.execute(f'SELECT * FROM videos WHERE org_id=? AND id IN ({",".join("?" * len(vids))})', (d['org_id'], *vids)).fetchall()}
    state = {'last': 0.0, 'advanced': False}

    def renew(progress=None, stage=None):
        if stop is not None and stop.is_set():
            e = _Shutdown(); e.progressed = state['advanced']; raise e
        now = time.time()
        if progress is None and now - state['last'] < 3: return
        state['last'] = now
        with m.db() as c:
            sets, args = ['lease_until=?'], [now + LEASE_SECONDS]
            if progress is not None: sets.append('progress=?'); args.append(round(progress, 3))
            if stage is not None: sets.append('stage=?'); args.append(stage[:120])
            if c.execute(f"UPDATE decisions SET {','.join(sets)} WHERE id=? AND lease_token=? AND status='RUNNING' AND cancel_requested=0", (*args, did, token)).rowcount != 1:
                raise _Cancelled()

    ms = min_seconds()
    products = [_product_info(p) for p in sorted(prods, key=lambda p: pids.index(p['id']))]
    checkpoint = json.loads(d['checkpoint_json'] or '{}')  # what an earlier run finished before it ran out of time
    videos, partial = checkpoint.get('videos', []), checkpoint.get('partial')

    def save(part=None):
        with m.db() as c: c.execute('UPDATE decisions SET checkpoint_json=? WHERE id=? AND lease_token=?', (json.dumps({'videos': videos, 'partial': part}), did, token))
        state['advanced'] = True

    for n, vid in enumerate(vids):
        if n < len(videos): continue
        v = vrows.get(vid)
        renew(progress=n / len(vids), stage=f'Analysing {v["title"] if v else "video"} ({n + 1} of {len(vids)})')
        if not v: videos.append({'video_id': vid, 'options': [], 'error': 'Video no longer exists'}); continue
        try: res = analyse_video(did, v, products, creator, renew, ms, resume=partial if partial and partial['video_id'] == vid else None, save=save)
        except (_Cancelled, _Shutdown): raise
        except Exception as e:
            obs.warn('decision video failed', decision=did, video=vid, error=str(e)[:200])
            res = {'video_id': vid, 'options': [], 'error': f'Analysis failed: {str(e)[:160]}'}
        res.update(title=v['title'], duration=v['duration'], width=v['width'], height=v['height'])
        videos.append(res)
        save()
    renew(progress=.97, stage='Choosing the best product for each video')
    picks = assign(videos, [p['id'] for p in products], bool(d['spread']))
    for v, pid in zip(videos, picks):
        if pid is not None:
            v['status'] = 'assigned'; v['recommendation'] = next(o for o in v['options'] if o['product_id'] == pid)
        else:
            v['status'] = 'error' if v.get('error') else 'no_fit'; v['recommendation'] = None
            if not v.get('error'):
                v['explanation'] = ('No flat, trackable surface was found in this video.' if not v['options'] else
                                    'No product reached a confident fit here (it needs to pass the quality check and score at least '
                                    f'{MIN_RECOMMEND_SCORE:.2f}). The closest options are listed.')
    used = sorted({p for p in picks if p is not None})
    result = {'engine_version': ENGINE_VERSION, 'weights': WEIGHTS, 'min_recommend_score': MIN_RECOMMEND_SCORE,
              'quality_fail_factor': QUALITY_FAIL_FACTOR, 'spread': bool(d['spread']),
              'semantic_scene_descriptions': bool(os.getenv('CC_VISION_MODEL')),
              'products': [{k: p[k] for k in ('id', 'name', 'category', 'aspect')} for p in products],
              'videos': videos,
              'matrix': [{'video_id': v['video_id'], 'product_id': o['product_id'], 'score': o['score'], 'passes_quality': o['passes_quality']} for v in videos for o in v['options']],
              'summary': {'videos': len(videos), 'assigned': sum(1 for p in picks if p is not None), 'products_used': used,
                          'unused_products': [p['id'] for p in products if p['id'] not in used]}}
    return result


# ---------------------------------------------------------------- queue (runs in the render workers)
def claim(c):
    now = time.time()
    for r in c.execute("SELECT id,attempts FROM decisions WHERE status='RUNNING' AND (lease_until IS NULL OR lease_until<?)", (now,)).fetchall():
        if r['attempts'] < MAX_ATTEMPTS:
            c.execute("UPDATE decisions SET status='QUEUED',lease_token=NULL WHERE id=? AND status='RUNNING'", (r['id'],))
        else:
            c.execute("UPDATE decisions SET status='FAILED',error='The analysis stopped repeatedly, usually because a video is too long to analyse. Try again, or use a shorter video.',finished_at=CURRENT_TIMESTAMP WHERE id=? AND status='RUNNING'", (r['id'],))
    row = c.execute("SELECT id FROM decisions WHERE status='QUEUED' ORDER BY id LIMIT 1").fetchone()
    if not row: return None, None
    token = secrets.token_hex(12)
    if c.execute("UPDATE decisions SET status='RUNNING',attempts=attempts+1,lease_until=?,lease_token=?,started_at=CURRENT_TIMESTAMP WHERE id=? AND status='QUEUED'",
                 (now + LEASE_SECONDS, token, row['id'])).rowcount != 1: return None, None
    return row['id'], token


def run_next(stop=None):
    """Claim and run one queued decision. Returns True when one was processed."""
    with m.db() as c: did, token = claim(c)
    if did is None: return False
    obs.worker_heartbeat('deciding'); started = time.time()
    try:
        result = run(did, token, stop)
        with m.db() as c:
            c.execute("UPDATE decisions SET status='SUCCEEDED',result_json=?,progress=1,stage='Done',finished_at=CURRENT_TIMESTAMP,lease_until=NULL WHERE id=? AND lease_token=? AND status='RUNNING'",
                      (json.dumps(result), did, token))
        obs.info('decision succeeded', decision=did, seconds=round(time.time() - started, 1), assigned=result['summary']['assigned'])
    except _Shutdown as e:
        # A worker stopping is not the decision's fault, and neither is running out of time after finishing a surface. Running out
        # of time on one surface repeatedly would never end, so that uses up attempts.
        free = not isinstance(stop, jobrunner.OutOfTime) or getattr(e, 'progressed', False)
        with m.db() as c: c.execute("UPDATE decisions SET status='QUEUED',lease_token=NULL,attempts=CASE WHEN ?=1 AND attempts>0 THEN attempts-1 ELSE attempts END WHERE id=? AND lease_token=?", (int(free), did, token))
    except _Cancelled:
        with m.db() as c: c.execute("UPDATE decisions SET status='CANCELLED',finished_at=CURRENT_TIMESTAMP,lease_until=NULL WHERE id=? AND lease_token=?", (did, token))
    except Exception as e:
        obs.error('decision failed', exc_info=True, decision=did)
        with m.db() as c:
            c.execute("UPDATE decisions SET status='FAILED',error=?,finished_at=CURRENT_TIMESTAMP,lease_until=NULL WHERE id=? AND lease_token=?",
                      (f'The analysis failed: {str(e)[:300]}', did, token))
    return True


# ---------------------------------------------------------------- API
class DecisionIn(BaseModel):
    name: str = Field(default='', max_length=120)
    product_ids: list[int] = Field(min_length=1, max_length=4)
    video_ids: list[int] = Field(min_length=1, max_length=50)
    spread: bool = True


def _summary(r):
    out = {k: r[k] for k in ('id', 'name', 'status', 'progress', 'stage', 'error', 'created_at', 'finished_at')}
    out['product_ids'] = json.loads(r['product_ids']); out['video_ids'] = json.loads(r['video_ids'])
    if r['result_json']: out['summary'] = json.loads(r['result_json'])['summary']
    return out


@router.get('/api/categories')
def categories():
    return [{'id': k, 'label': v[0]} for k, v in CATEGORIES.items()]


@router.post('/api/decisions')
def create_decision(body: DecisionIn, request: Request):
    u = m.current_user(request)
    pids, vids = list(dict.fromkeys(body.product_ids)), list(dict.fromkeys(body.video_ids))
    with m.db() as c:
        plan, lim = limits.plan_limits(c, u['org_id'])
        if len(pids) > lim['max_products_per_decision']: raise HTTPException(422, f'Choose at most {lim["max_products_per_decision"]} products')
        if len(vids) > lim['max_videos_per_decision']:
            raise HTTPException(402, {'code': 'PLAN_VIDEOS_PER_DECISION', 'message': f'Your {lim["name"]} plan analyses up to {lim["max_videos_per_decision"]} videos per decision. Upgrade your plan to include more.', 'plan': plan})
        for pid in pids: m.owned(c, 'products', pid, u['org_id'])
        for vid in vids: m.owned(c, 'videos', vid, u['org_id'])
        limits.check_monthly(c, u['org_id'], 'decisions')
        if c.execute("SELECT COUNT(*) FROM decisions WHERE org_id=? AND status IN ('QUEUED','RUNNING')", (u['org_id'],)).fetchone()[0] >= limits.effective_active_jobs(c, u['org_id']):
            raise HTTPException(429, {'code': 'QUOTA_ACTIVE_DECISIONS', 'message': 'Wait for the running decisions to finish before starting another.'})
        name = body.name.strip() or time.strftime('Decision %b %d, %H:%M')
        did = c.execute('INSERT INTO decisions(org_id,name,product_ids,video_ids,spread,created_by,stage) VALUES(?,?,?,?,?,?,?)',
                        (u['org_id'], name, json.dumps(pids), json.dumps(vids), int(body.spread), u['id'], 'Waiting for a worker')).lastrowid
        m.event(c, u, 'decision.created', 'decision', did, {'products': len(pids), 'videos': len(vids)})
        c.commit()
    obs.wake_workers()
    return {'id': did, 'status': 'QUEUED', 'name': name}


@router.get('/api/decisions')
def list_decisions(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        jobrunner.nudge(c, u['org_id'])
        rows = c.execute('SELECT * FROM decisions WHERE org_id=? ORDER BY id DESC LIMIT 100', (u['org_id'],)).fetchall()
    return [_summary(r) for r in rows]


def _owned_decision(c, did, org_id):
    r = c.execute('SELECT * FROM decisions WHERE id=? AND org_id=?', (did, org_id)).fetchone()
    if not r: raise HTTPException(404, 'Decision not found')
    return dict(r)


@router.get('/api/decisions/{did}')
def get_decision(did: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        jobrunner.nudge(c, u['org_id'])
        r = _owned_decision(c, did, u['org_id'])
        placements = {(p['video_id'], p['product_id']): p['id'] for p in c.execute(
            "SELECT id,video_id,product_id FROM placements WHERE org_id=? AND decision_id=? AND status NOT IN ('CANCELLED','REJECTED')", (u['org_id'], did)).fetchall()}
    out = _summary(r)
    if r['result_json']:
        res = json.loads(r['result_json'])
        for v in res['videos']:
            for o in v['options'] + ([v['recommendation']] if v.get('recommendation') else []):
                if o.pop('preview', None): o['preview_url'] = f'/api/decisions/{did}/preview?video_id={v["video_id"]}&product_id={o["product_id"]}'
                o['placement_id'] = placements.get((v['video_id'], o['product_id']))
        out['result'] = res
    return out


@router.get('/api/decisions/{did}/preview')
def decision_preview(did: int, video_id: int, product_id: int, request: Request):
    u = m.current_user(request)
    with m.db() as c: _owned_decision(c, did, u['org_id'])
    path = m.DATA / 'decisions' / f'decision_{did}_v{video_id}_p{product_id}.jpg'
    if not storage.exists(str(path)): raise HTTPException(404, 'Preview not found')
    return storage.response(str(path), media_type='image/jpeg')


@router.post('/api/decisions/{did}/cancel')
def cancel_decision(did: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        r = _owned_decision(c, did, u['org_id'])
        if r['status'] not in ('QUEUED', 'RUNNING'): raise HTTPException(409, 'Only queued or running decisions can be cancelled')
        c.execute("UPDATE decisions SET cancel_requested=1,status=CASE WHEN status='QUEUED' THEN 'CANCELLED' ELSE status END,finished_at=CASE WHEN status='QUEUED' THEN CURRENT_TIMESTAMP ELSE finished_at END WHERE id=?", (did,))
    return {'id': did, 'cancel_requested': True}


class ApplyIn(BaseModel):
    video_id: int
    product_id: Optional[int] = None    # default: the recommended product


@router.post('/api/decisions/{did}/apply')
def apply_decision(did: int, body: ApplyIn, request: Request):
    """Turn a recommendation (or another product's option on the same video) into a draft tracked placement."""
    u = m.current_user(request)
    with m.db() as c:
        r = _owned_decision(c, did, u['org_id'])
        if r['status'] != 'SUCCEEDED': raise HTTPException(409, 'The decision has not finished')
        res = json.loads(r['result_json'])
        v = next((x for x in res['videos'] if x['video_id'] == body.video_id), None)
        if not v: raise HTTPException(404, 'This video is not part of the decision')
        pid = body.product_id or (v['recommendation'] or {}).get('product_id')
        opt = next((o for o in v['options'] if o['product_id'] == pid), None)
        if not opt: raise HTTPException(409, 'There is no option for this product on this video')
        existing = c.execute("SELECT id FROM placements WHERE org_id=? AND video_id=? AND product_id=? AND decision_id=? AND status NOT IN ('CANCELLED','REJECTED')",
                             (u['org_id'], body.video_id, pid, did)).fetchone()
        if existing: return {'placement_id': existing['id'], 'created': False}
    q = np.asarray(opt['quad'])
    out = m.create_placement(m.PlacementIn(video_id=body.video_id, product_id=pid, frame_second=opt['frame_second'], x=float(q[:, 0].mean()),
                                           y=float(q[:, 1].mean()), width=.2, start_second=opt['start_second'], end_second=opt['end_second'],
                                           tracking_quad=opt['quad'], color_match=True), request)
    with m.db() as c:
        c.execute('UPDATE placements SET decision_id=? WHERE id=?', (did, out['id']))   # provenance; not part of the version hash
        m.event(c, u, 'decision.applied', 'decision', did, {'placement_id': out['id'], 'video_id': body.video_id, 'product_id': pid, 'score': opt['score']})
    return {'placement_id': out['id'], 'created': True}
