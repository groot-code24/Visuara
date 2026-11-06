"""Product catalogue states, asset versions, and brand-to-scene matching (Phase 7).

Matching ranks AVAILABLE inventory from other organizations for one of the brand's ACTIVE products. The match score is
explained and deterministic (MATCH_VERSION):
  category fit   the opportunity's product compatibility for the product's category (from surface orientation and scene
                 context)
  feasibility    the opportunity's heuristic feasibility score
  context fit    1 when the scene's setting (from detected objects) favours the product's category, or a word of the
                 product's name or description is among the objects detected in the scene or in its description; 0 when a
                 setting was determined and does not favour it; 0.5 when the scene's setting is unknown
match = category_fit x feasibility x (0.85 + 0.15 x context_fit), reported with its parts and plain-language reasons.
Left out, never ranked lower:
  - items whose video no longer has a confirmed licence or a verified, unexpired rights record, or is otherwise no longer
    listable (`inventory.eligibility`, checked again at match time: rights can expire or be revoked after listing);
  - items that hit the brand's excluded terms in the scene description, the video title, the objects detected in the scene
    or its setting;
  - items outside the campaign's targeting: price, placement categories, publishers, brand safety.
Every run is persisted (`scene_recommendations`) with a run id, so a recommendation shown to a person can be traced later.
"""
import hashlib, json, secrets
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from . import main as m, storage
from .orgs import require_kind, BUY_SIDE

router = APIRouter()
PRODUCT_STATES = ('DRAFT', 'ACTIVE', 'ARCHIVED')
MATCH_VERSION = 'match-v2'
_WORD = __import__('re').compile(r'[a-z]{3,}')


class ProductPatch(BaseModel):
    status: Optional[str] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    category: Optional[str] = None


@router.patch('/api/products/{pid}')
def update_product(pid: int, body: ProductPatch, request: Request):
    u = m.current_user(request)
    from .decisions import CATEGORIES
    if body.status is not None and body.status not in PRODUCT_STATES: raise HTTPException(422, {'code': 'INVALID_STATUS', 'message': f'status must be one of {", ".join(PRODUCT_STATES)}'})
    if body.category is not None and body.category not in CATEGORIES: raise HTTPException(422, {'code': 'INVALID_CATEGORY', 'message': 'Unknown category'})
    with m.db() as c:
        p = m.owned(c, 'products', pid, u['org_id'])
        if p['source_product_id']: raise HTTPException(409, {'code': 'LINKED_PRODUCT', 'message': 'A product copied for a campaign placement is managed by its brand'})
        for k in ('status', 'name', 'category'):
            v = getattr(body, k)
            if v is not None: c.execute(f'UPDATE products SET {k}=? WHERE id=?', (v.strip() if k == 'name' else v, pid))
        m.event(c, u, 'product.updated', 'product', pid, body.model_dump(exclude_none=True))
        return dict(c.execute('SELECT id,name,category,status,image_version FROM products WHERE id=?', (pid,)).fetchone())


@router.get('/api/products/{pid}/assets')
def list_assets(pid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        m.owned(c, 'products', pid, u['org_id'])
        return [dict(r) for r in c.execute('SELECT id,version,kind,sha256,size_bytes,created_at FROM product_assets WHERE product_id=? ORDER BY kind,version', (pid,)).fetchall()]


@router.post('/api/products/{pid}/assets')
def add_image_version(pid: int, request: Request, image: UploadFile = File(...)):
    """A new image version. Placements and requests keep the version they were made with."""
    u = m.current_user(request)
    data = image.file.read(20 * 1048576 + 1)
    if len(data) > 20 * 1048576: raise HTTPException(413, 'Product images are limited to 20 MB')
    if not (data.startswith(b'\x89PNG') or data.startswith(b'\xff\xd8\xff') or data[8:12] == b'WEBP'):
        raise HTTPException(415, {'code': 'IMAGE_TYPE', 'message': 'PNG, JPEG or WebP (checked from the file contents)'})
    with m.db() as c:
        p = m.owned(c, 'products', pid, u['org_id'])
        if p['source_product_id']: raise HTTPException(409, {'code': 'LINKED_PRODUCT', 'message': 'A product copied for a campaign placement is managed by its brand'})
    ext = '.png' if data.startswith(b'\x89PNG') else '.jpg' if data.startswith(b'\xff\xd8') else '.webp'
    path = m.UPLOADS / f'product_{pid}_{secrets.token_hex(6)}{ext}'; path.write_bytes(data)
    from .compositor import prepare_asset
    derived = path.with_name(path.stem + '.derived.png')
    try:
        from PIL import Image
        import io
        Image.open(io.BytesIO(data)).verify()
        report = prepare_asset(path, derived)
    except Exception as e:
        path.unlink(missing_ok=True); raise HTTPException(400, f'Invalid product image: {str(e)[:120]}')
    storage.persist(path); storage.persist(derived)
    with m.db() as c:
        c.lock(f'product-asset-{pid}')
        version = (c.execute("SELECT MAX(version) FROM product_assets WHERE product_id=? AND kind='IMAGE'", (pid,)).fetchone()[0] or 0) + 1
        aid = c.execute("INSERT INTO product_assets(org_id,product_id,version,kind,path,sha256,size_bytes,created_by) VALUES(?,?,?,'IMAGE',?,?,?,?)",
                        (u['org_id'], pid, version, str(path), hashlib.sha256(data).hexdigest(), len(data), u['id'])).lastrowid
        c.execute('UPDATE products SET image_path=?, derived_path=?, asset_report=?, sha256=?, image_version=?, size_bytes=size_bytes+? WHERE id=?',
                  (str(path), str(derived), json.dumps(report), hashlib.sha256(data).hexdigest(), version, len(data) + derived.stat().st_size, pid))
        m.event(c, u, 'product.asset_added', 'product', pid, {'version': version, 'asset_id': aid})
    return {'id': aid, 'product_id': pid, 'version': version, 'kind': 'IMAGE'}


class MatchIn(BaseModel):
    product_id: int
    limit: int = Field(default=20, ge=1, le=100)
    max_price_minor: Optional[int] = Field(default=None, gt=0)
    placement_categories: list[str] = Field(default_factory=list)
    min_score: float = Field(default=0.0, ge=0, le=1)


def _scene_context(meta_json):
    meta = json.loads(meta_json or '{}')
    ctx = meta.get('context') or {}
    objects = sorted((meta.get('objects') or {}).keys())
    return (ctx.get('setting') if ctx.get('status') == 'MEASURED' else None), objects


def match(c, brand_org_id, product, max_price=None, placement_categories=(), min_score=0.0, publisher_ids=(), require_safe=True, excluded=None):
    """Ranked recommendations for `product`. `excluded`, when a dict, receives a count per reason of the items left out."""
    from .inventory import eligibility
    from .scoring import CONTEXT_CATEGORIES
    profile = c.execute('SELECT excluded_terms_json FROM brand_profiles WHERE org_id=?', (brand_org_id,)).fetchone()
    terms = [t.lower() for t in json.loads(profile['excluded_terms_json'])] if profile else []
    product_words = set(_WORD.findall(f"{product.get('name') or ''} {product.get('description') or ''}".lower()))
    rows = c.execute("SELECT i.*, o.product_compatibility_json, o.feasibility_score, o.brand_safety, o.placement_category, s.description AS scene_description, "
                     "s.metadata_json AS scene_metadata, v.title AS video_title "
                     "FROM inventory_items i JOIN opportunities o ON o.id=i.opportunity_id JOIN scenes s ON s.id=i.scene_id JOIN videos v ON v.id=i.video_id "
                     "WHERE i.status='AVAILABLE' AND i.org_id<>?", (brand_org_id,)).fetchall()
    out, left_out = [], {}

    def leave(reason): left_out[reason] = left_out.get(reason, 0) + 1

    for r in rows:
        if max_price and r['price_minor'] > max_price: leave('over the campaign budget'); continue
        if placement_categories and r['placement_category'] not in placement_categories: leave('placement category not targeted'); continue
        if publisher_ids and r['org_id'] not in publisher_ids: leave('publisher not targeted'); continue
        if require_safe and r['brand_safety'] != 'SAFE': leave('brand safety is not SAFE'); continue
        if eligibility(c, r): leave('no longer listable (licence, rights or review)'); continue
        setting, objects = _scene_context(r['scene_metadata'])
        text = f"{r['scene_description'] or ''} {r['video_title'] or ''} {' '.join(objects)} {setting or ''}".lower()
        if any(t in text for t in terms): leave("matches the brand's excluded terms"); continue
        fit = json.loads(r['product_compatibility_json']).get(product['category'], 0.0)
        scene_words = set(_WORD.findall(f"{r['scene_description'] or ''} {' '.join(objects)}".lower()))
        shared = sorted(product_words & scene_words)
        favoured = bool(setting) and product['category'] in CONTEXT_CATEGORIES.get(setting, ())
        context_fit = 1.0 if (favoured or shared) else (0.0 if setting else 0.5)
        score = round(fit * r['feasibility_score'] * (0.85 + 0.15 * context_fit), 4)
        if score < min_score: leave('below the minimum score'); continue
        reasons = [f"{product['category']} fits a {r['placement_category'].lower().replace('_', ' ')} at {fit:.2f}",
                   f"placement feasibility {r['feasibility_score']:.2f}"]
        if favoured: reasons.append(f"the scene's setting ({setting}) suits {product['category']}")
        elif setting: reasons.append(f"the scene's setting ({setting}) does not particularly suit {product['category']}")
        else: reasons.append("the scene's setting is unknown")
        if shared: reasons.append(f"the scene shows {', '.join(shared)}, named in the product")
        out.append({'inventory_id': r['id'], 'publisher_org_id': r['org_id'], 'score': score, 'price_minor': r['price_minor'], 'currency': r['currency'],
                    'reasons': reasons,
                    'breakdown': {'category_fit': fit, 'feasibility': r['feasibility_score'], 'context_fit': context_fit, 'setting': setting,
                                  'shared_words': shared, 'brand_safety': r['brand_safety'], 'category': product['category'],
                                  'placement_category': r['placement_category'], 'version': MATCH_VERSION,
                                  'formula': 'category_fit x feasibility x (0.85 + 0.15 x context_fit)'}})
    out.sort(key=lambda d: (-d['score'], d['price_minor'], d['inventory_id']))
    if excluded is not None: excluded.update(left_out)
    return out


@router.post('/api/matching/recommendations')
def recommend(body: MatchIn, request: Request):
    u = m.current_user(request); require_kind(u, BUY_SIDE, 'Brand-to-scene matching')
    with m.db() as c:
        p = m.owned(c, 'products', body.product_id, u['org_id'])
        if p['status'] != 'ACTIVE': raise HTTPException(409, {'code': 'PRODUCT_NOT_ACTIVE', 'message': f'The product is {p["status"]}'})
        from .inventory import expire_holds
        expire_holds(c)
        left_out = {}
        ranked = match(c, u['org_id'], p, body.max_price_minor, body.placement_categories, body.min_score, excluded=left_out)[:body.limit]
        run_id = secrets.token_hex(8)
        for i, r in enumerate(ranked, 1):
            c.execute('INSERT INTO scene_recommendations(brand_org_id,product_id,inventory_id,run_id,rank,score,breakdown_json,created_by) VALUES(?,?,?,?,?,?,?,?)',
                      (u['org_id'], p['id'], r['inventory_id'], run_id, i, r['score'], json.dumps(r['breakdown']), u['id']))
        m.event(c, u, 'matching.run', 'product', p['id'], {'run_id': run_id, 'results': len(ranked)})
    return {'run_id': run_id, 'product_id': p['id'], 'results': [dict(r, rank=i) for i, r in enumerate(ranked, 1)], 'left_out': left_out,
            'version': MATCH_VERSION, 'note': 'Scores are heuristic ranking aids, not predictions of campaign performance.'}


@router.get('/api/matching/recommendations/{run_id}')
def get_run(run_id: str, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        rows = c.execute('SELECT * FROM scene_recommendations WHERE run_id=? AND brand_org_id=? ORDER BY rank', (run_id, u['org_id'])).fetchall()
    if not rows: raise HTTPException(404, 'Resource not found')
    return [dict(r, breakdown=json.loads(r['breakdown_json'])) for r in rows]
