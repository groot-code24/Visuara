"""Opportunity scoring (Phase 5, specification §6.5): deterministic, explained, with versioned weights.

The score is a weighted mean of the factors that were really measured. A factor that could not be measured (for example
contextual relevance when no vision model describes the scene) is reported as unavailable and the remaining weights are
rescaled; nothing is guessed. The score is a heuristic ranking aid, not a validated prediction of performance, and every
explanation says so.

Brand safety: platform rules (seeded) plus the organization's own rules are matched as whole words against the scene
description and the video title. A match sets REVIEW or UNSAFE. With no description the result is NOT_EVALUATED, and the
publisher's reviewer decides (`POST /api/opportunities/{id}/review`).
"""
import json, re, time

from . import opportunities
from .decisions import CATEGORIES

FACTORS = ('visual_feasibility', 'available_area', 'visibility', 'exposure_duration', 'contextual_relevance', 'brand_safety', 'content_rights')


def current(c):
    return dict(c.execute('SELECT * FROM scoring_configs ORDER BY id DESC LIMIT 1').fetchone())


def by_version(c, version):
    r = c.execute('SELECT * FROM scoring_configs WHERE version=?', (version,)).fetchone()
    if not r: raise ValueError(f'unknown scoring configuration {version}')
    d = dict(r); d['weights'] = json.loads(d['weights_json']); return d


def rules(c, org_id):
    return [dict(r) for r in c.execute('SELECT term, verdict, org_id FROM brand_safety_rules WHERE org_id IS NULL OR org_id=? ORDER BY id', (org_id,)).fetchall()]


def brand_safety(c, org_id, description, title, objects=()):
    """`objects`: detected object labels, matched against the same rules (an organization can add a rule for 'knife')."""
    text = f'{description or ""} {title or ""} {" ".join(objects)}'.lower()
    hits = [r for r in rules(c, org_id) if re.search(rf'\b{re.escape(r["term"].lower())}\b', text)]
    if any(h['verdict'] == 'UNSAFE' for h in hits): verdict = 'UNSAFE'
    elif hits: verdict = 'REVIEW'
    elif description: verdict = 'SAFE'                                                   # objects alone never establish safety
    else: verdict = 'NOT_EVALUATED'
    return verdict, [h['term'] for h in hits]


def _area_score(a): return a / .03 if a < .03 else 1.0 if a <= .3 else max(0.0, 1 - (a - .3) / .3)


def combine(weights, factors):
    """Weighted mean over the measured factors (value not None). Returns (score, breakdown)."""
    measured = {k: v for k, v in factors.items() if v is not None and k in weights}
    total_w = sum(weights[k] for k in measured)
    score = sum(weights[k] * measured[k] for k in measured) / total_w if total_w else 0.0
    breakdown = {k: {'value': None if factors.get(k) is None else round(factors[k], 3), 'weight': weights.get(k, 0),
                     'effective_weight': round(weights[k] / total_w, 3) if k in measured and total_w else 0.0,
                     'status': 'MEASURED' if k in measured else 'UNAVAILABLE'} for k in weights}
    return round(score, 4), breakdown


def build(c, cfg, cand, scene, video):
    """An opportunity (dict for `opportunities.Opportunity`) and its score breakdown from a detected surface. The scoring
    configuration's version decides the method, so an opportunity scored under v1 is reproduced exactly by v1."""
    if cfg['version'] == 'v1': return _build_v1(c, cfg, cand, scene, video)
    return _build_v2(c, cfg, cand, scene, video)


def _build_v1(c, cfg, cand, scene, video):
    desc = scene.get('description') or ''
    safety, terms = brand_safety(c, video['org_id'], desc, video.get('title'))
    keywords = {k: any(w in desc.lower() for w in kws) for k, (_, _, kws) in CATEGORIES.items()} if desc else {}
    compat = {k: round(min(1.0, aff.get(cand['orientation'], .7) + (.1 if keywords.get(k) else 0.0)), 3) for k, (_, aff, _) in CATEGORIES.items()}
    rights_ok = _rights_factor(c, video)
    factors = {
        'visual_feasibility': cand['detector_confidence'],
        'available_area': _area_score(cand['area_fraction']),
        'visibility': cand['stability'],
        'exposure_duration': min(1.0, cand['visible_seconds'] / 5.0),
        'contextual_relevance': (1.0 if any(keywords.values()) else 0.4) if desc else None,
        'brand_safety': {'SAFE': 1.0, 'REVIEW': .5, 'UNSAFE': 0.0}.get(safety),
        'content_rights': rights_ok,
    }
    score, breakdown = combine(cfg['weights'], factors)
    motion = cand.get('scene_motion', 0.0)
    complexity = 'LOW' if (cand['orientation'] in ('horizontal', 'vertical') and motion < 2 and cand['stability'] > .7) else ('HIGH' if motion > 8 or cand['stability'] < .4 else 'MEDIUM')
    unavailable = [k for k, b in breakdown.items() if b['status'] == 'UNAVAILABLE']
    explanation = (f"Heuristic score {score:.2f} (scoring {cfg['version']}; a ranking aid, not a validated prediction). "
                   f"{cand['orientation'].capitalize()} surface covering {cand['area_fraction'] * 100:.1f}% of the frame, stable for {cand['visible_seconds']:.1f} s "
                   f"(stability {cand['stability']:.2f}). Brand safety {safety}{' (' + ', '.join(terms) + ')' if terms else ''}."
                   + (f" Not measured: {', '.join(unavailable)}." if unavailable else ''))
    opp = {'scene_id': scene['id'], 'candidate_kind': 'SURFACE', 'candidate_label': f"{cand['orientation']} planar surface",
           'placement_category': opportunities.ORIENTATION_CATEGORY.get(cand['orientation'], 'ANGLED_SURFACE'),
           'geometry': {'frame_second': cand['frame_second'], 'bbox': [round(max(0.0, min(1.0, v)), 4) for v in cand['bbox']], 'quad': cand['quad']},
           'start_second': cand['start_second'], 'end_second': cand['end_second'], 'visible_seconds': cand['visible_seconds'],
           'feasibility_score': score, 'product_compatibility': compat, 'brand_safety': safety, 'rendering_complexity': complexity,
           'confidence': round(cand['detector_confidence'] * cand['stability'], 3),
           'confidence_factors': {'detector': round(cand['detector_confidence'], 3), 'stability': cand['stability'], 'texture': round(cand['texture'], 3)},
           'explanation': explanation}
    # keep the bbox inside the frame after rounding
    x, y, w, h = opp['geometry']['bbox']; opp['geometry']['bbox'] = [x, y, round(min(w, 1 - x), 4), round(min(h, 1 - y), 4)]
    return opp, breakdown


CONTEXT_CATEGORIES = {'kitchen': ('beverage', 'food', 'home'), 'dining': ('beverage', 'food'), 'living room': ('home', 'tech'),
                      'workspace': ('tech',), 'bedroom': ('home', 'fashion', 'beauty'), 'bathroom': ('beauty',), 'street': ('fashion',),
                      'sports': ('fitness', 'beverage'), 'outdoors with animals': (), 'travel': ('fashion',)}


def _camera_factor(cam):
    if not cam or cam.get('status') != 'MEASURED': return None, 'camera motion was not measured'
    base = {'static': 1.0, 'handheld': .5, 'mixed': .7}.get(cam['movement'], .85)
    value = max(0.0, base - min(.3, cam.get('shake', 0.0)))
    return value, f"camera {cam['movement']} (shake {cam.get('shake', 0):.3f})"


def _lighting_factor(li):
    if not li or li.get('status') != 'MEASURED': return None, 'lighting was not measured'
    value = (1.0 if li['exposure'] == 'normal' else .6) * (1.0 if li['stable'] else .6)
    return value, f"{li['exposure']} exposure, {li['colour_cast']} cast, level change {li['level_change']:.2f}"


def _build_v2(c, cfg, cand, scene, video):
    """v2: the factors of v1 plus perspective, occlusion (tracked and from detected people), lighting, camera motion and
    object context, each with the evidence it came from. A factor that was not measured is UNAVAILABLE and its weight is
    spread over the rest, as in v1."""
    meta = json.loads(scene.get('metadata_json') or '{}')
    desc = scene.get('description') or ''
    ctx = meta.get('context') or {}
    objects = sorted((meta.get('objects') or {}).keys())
    safety, terms = brand_safety(c, video['org_id'], desc, video.get('title'), objects)
    signals = meta.get('safety_signals') or []
    if signals and safety in ('SAFE', 'NOT_EVALUATED'):
        safety, terms = 'REVIEW', terms + [f"object:{s['object']}" for s in signals]
    keywords = {k: any(w in desc.lower() for w in kws) for k, (_, _, kws) in CATEGORIES.items()} if desc else {}
    setting = ctx.get('setting') if ctx.get('status') == 'MEASURED' else None
    boost = set(CONTEXT_CATEGORIES.get(setting, ()))
    compat = {k: round(min(1.0, aff.get(cand['orientation'], .7) + (.1 if keywords.get(k) or k in boost else 0.0)), 3)
              for k, (_, aff, _) in CATEGORIES.items()}
    cam_v, cam_e = _camera_factor(meta.get('camera'))
    li_v, li_e = _lighting_factor(meta.get('lighting'))
    occlusion = max(float(cand.get('occlusion') or 0.0), float(cand.get('object_overlap') or 0.0))
    if setting:
        relevance, rel_e = .6 + .4 * float(ctx.get('strength', 0)), f"setting {setting} from {', '.join(ctx.get('evidence', {}))}"
    elif desc:
        relevance, rel_e = (1.0 if any(keywords.values()) else 0.4), 'from the vision model description'
    elif meta.get('objects_detected'):
        relevance, rel_e = .4, 'object detection ran and the objects found (if any) do not indicate a setting'
    else:
        relevance, rel_e = None, 'no object detection or vision model result for this scene'
    rights = _rights_factor(c, video)
    factors = {
        'visual_feasibility': cand['detector_confidence'],
        'available_area': _area_score(cand['area_fraction']),
        'visibility': cand['stability'],
        'exposure_duration': min(1.0, cand['visible_seconds'] / 5.0),
        'perspective': cand.get('frontality'),
        'occlusion': 1.0 - occlusion if 'occlusion' in cand else None,
        'lighting': li_v, 'camera_motion': cam_v, 'contextual_relevance': relevance,
        'brand_safety': {'SAFE': 1.0, 'REVIEW': .5, 'UNSAFE': 0.0}.get(safety),
        'content_rights': rights,
    }
    evidence = {
        'visual_feasibility': f"{cand.get('method', 'contours')} detector confidence {cand['detector_confidence']:.2f}",
        'available_area': f"{cand['area_fraction'] * 100:.1f}% of the frame",
        'visibility': f"tracked by homography: planarity {cand.get('planarity', 0):.2f}, jitter {cand.get('jitter', 0):.4f}",
        'exposure_duration': f"visible for {cand['visible_seconds']:.1f} s continuously",
        'perspective': f"frontality {cand['frontality']:.2f} (1 = seen square-on)" if cand.get('frontality') is not None else 'not measured',
        'occlusion': (f"largest hidden fraction {occlusion:.2f}" + (f"; overlapped by {', '.join(cand.get('overlapping_objects', []))}" if cand.get('overlapping_objects') else ''))
                     if 'occlusion' in cand else 'not measured',
        'lighting': li_e, 'camera_motion': cam_e, 'contextual_relevance': rel_e,
        'brand_safety': f"{safety}" + (f" ({', '.join(terms)})" if terms else ''),
        'content_rights': {1.0: 'rights verified', .7: 'confirmed by the uploader, not yet verified', 0.0: 'licence missing or expired'}.get(rights, ''),
    }
    score, breakdown = combine(cfg['weights'], factors)
    for k, b in breakdown.items(): b['evidence'] = evidence.get(k, '')
    motion = cand.get('scene_motion', 0.0)
    hard = cand['stability'] < .4 or occlusion > .3 or (cam_v is not None and cam_v < .6)
    complexity = 'LOW' if (cand['orientation'] in ('horizontal', 'vertical') and motion < 2 and cand['stability'] > .7 and occlusion < .1) else ('HIGH' if motion > 8 or hard else 'MEDIUM')
    unavailable = [k for k, b in breakdown.items() if b['status'] == 'UNAVAILABLE']
    label = f"{cand['orientation']} planar surface" + (f" ({cand['identified_as']})" if cand.get('identified_as') else '')
    explanation = (f"Heuristic score {score:.2f} (scoring {cfg['version']}; a ranking aid, not a validated prediction). "
                   f"{label.capitalize()} covering {cand['area_fraction'] * 100:.1f}% of the frame, tracked for {cand['visible_seconds']:.1f} s "
                   f"(stability {cand['stability']:.2f}, hidden at most {occlusion:.0%}). "
                   + (f"Setting: {setting}. " if setting else '')
                   + f"Brand safety {safety}{' (' + ', '.join(terms) + ')' if terms else ''}."
                   + (f" Not measured: {', '.join(unavailable)}." if unavailable else ''))
    confidence = cand['detector_confidence'] * cand['stability'] * float(cand.get('planarity', 1.0))
    opp = {'scene_id': scene['id'], 'candidate_kind': 'SURFACE', 'candidate_label': label[:120],
           'placement_category': opportunities.ORIENTATION_CATEGORY.get(cand['orientation'], 'ANGLED_SURFACE'),
           'geometry': {'frame_second': cand['frame_second'], 'bbox': [round(max(0.0, min(1.0, v)), 4) for v in cand['bbox']], 'quad': cand['quad']},
           'start_second': cand['start_second'], 'end_second': cand['end_second'], 'visible_seconds': cand['visible_seconds'],
           'feasibility_score': score, 'product_compatibility': compat, 'brand_safety': safety, 'rendering_complexity': complexity,
           'confidence': round(min(1.0, confidence), 3),
           'confidence_factors': {'detector': round(min(1.0, cand['detector_confidence']), 3), 'stability': cand['stability'],
                                  'planarity': round(float(cand.get('planarity', 1.0)), 3), 'texture': round(cand['texture'], 3)},
           'explanation': explanation[:2000]}
    x, y, w, h = opp['geometry']['bbox']; opp['geometry']['bbox'] = [x, y, round(min(w, 1 - x), 4), round(min(h, 1 - y), 4)]
    return opp, breakdown


def _rights_factor(c, video):
    today = time.strftime('%Y-%m-%d')
    if not (video.get('license_confirmed') and not (video.get('license_expires') and video['license_expires'] < today)): return 0.0
    if c.execute("SELECT 1 FROM rights_records WHERE video_id=? AND status='VERIFIED' AND (expires_on IS NULL OR expires_on>=?)", (video['id'], today)).fetchone():
        return 1.0
    return 0.7                                                                       # confirmed by the uploader, not yet verified


def record(c, built, org_id, cfg, writing_analysis_id=None):
    opp, breakdown = built
    oid = opportunities.record(c, opp, org_id, writing_analysis_id)
    c.execute('UPDATE opportunities SET score=?, score_breakdown_json=?, scoring_config_id=? WHERE id=?', (opp['feasibility_score'], json.dumps(breakdown), cfg['id'], oid))
    return oid
