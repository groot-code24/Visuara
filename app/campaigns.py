"""Campaigns, placement requests, two-party approval and production (Phases 9 and 10).

Campaign lifecycle, every change recorded in `status_transitions` with who made it:
  DRAFT -> SUBMITTED -> ACTIVE <-> PAUSED -> COMPLETED; CANCELLED from any open state.

A placement request asks a publisher to place one campaign product in one reserved inventory item:
  REQUESTED     made by a brand manager (this is the brand's approval) on a reservation the brand holds; the hold is
                extended while the publisher decides
  APPROVED      the publisher's manager approved: the reservation is CONFIRMED, the item SOLD and a quote issued
  REJECTED / CANCELLED   the reservation is released and the item is AVAILABLE again
  IN_PRODUCTION the publisher started production: the brand's product (the asset version requested) is copied into the
                publisher's organization as a linked product and a placement is created from the opportunity's geometry.
                The publisher then uses the existing quality check, approval and render (Phase 10 reuses the engine).
  DELIVERED     a validated render exists (`delivery.on_render_succeeded`)
  ACCEPTED      the brand accepted the delivered render
"""
import json, os, secrets, shutil, time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import main as m, outbox, storage
from .inventory import notify, transition_log, CURRENCIES
from .orgs import require_kind, BUY_SIDE, SELL_SIDE

router = APIRouter()
TRANSITIONS = {'DRAFT': {'SUBMITTED', 'CANCELLED'}, 'SUBMITTED': {'ACTIVE', 'DRAFT', 'CANCELLED'}, 'ACTIVE': {'PAUSED', 'COMPLETED', 'CANCELLED'},
               'PAUSED': {'ACTIVE', 'CANCELLED'}, 'COMPLETED': set(), 'CANCELLED': set()}
OPEN = ('DRAFT', 'SUBMITTED', 'ACTIVE', 'PAUSED')


def response_seconds(): return float(os.getenv('CC_REQUEST_RESPONSE_SECONDS', str(72 * 3600)))
def _err(status, code, message, **extra): return HTTPException(status, {'code': code, 'message': message, **extra})


def _campaign(c, cid, org_id):
    r = c.execute('SELECT * FROM campaigns WHERE id=? AND org_id=?', (cid, org_id)).fetchone()
    if not r: raise HTTPException(404, 'Resource not found')
    return dict(r)


def _views(c, camps):
    """API views of campaigns, in four queries whatever their number (M16: the list was four queries per campaign)."""
    camps = [dict(x) for x in camps]
    if not camps: return []
    ids = [x['id'] for x in camps]; marks = ','.join('?' * len(ids))
    products, targeting, transitions, committed = {}, {}, {}, {}
    for r in c.execute(f'SELECT campaign_id, product_id FROM campaign_products WHERE campaign_id IN ({marks}) ORDER BY product_id', ids).fetchall():
        products.setdefault(r['campaign_id'], []).append(r['product_id'])
    for t in c.execute(f'SELECT * FROM campaign_targeting WHERE campaign_id IN ({marks})', ids).fetchall():
        targeting[t['campaign_id']] = {'categories': json.loads(t['categories_json']), 'placement_categories': json.loads(t['placement_categories_json']),
                                       'publisher_ids': json.loads(t['publisher_ids_json']), 'min_score': t['min_score'],
                                       'require_brand_safe': bool(t['require_brand_safe']), 'max_price_minor': t['max_price_minor']}
    for r in c.execute(f"SELECT entity_id, from_status, to_status, user_id, reason, created_at FROM status_transitions WHERE entity_type='campaign' AND entity_id IN ({marks}) ORDER BY id", ids).fetchall():
        transitions.setdefault(r['entity_id'], []).append({k: r[k] for k in ('from_status', 'to_status', 'user_id', 'reason', 'created_at')})
    for r in c.execute(f"SELECT p.campaign_id, COALESCE(SUM(r.price_minor),0) AS total FROM placement_requests p JOIN reservations r ON r.id=p.reservation_id "
                       f"WHERE p.campaign_id IN ({marks}) AND p.status IN ('APPROVED','IN_PRODUCTION','DELIVERED','ACCEPTED') GROUP BY p.campaign_id", ids).fetchall():
        committed[r['campaign_id']] = r['total']
    for d in camps:
        d.update(product_ids=products.get(d['id'], []), targeting=targeting.get(d['id']), transitions=transitions.get(d['id'], []),
                 committed_minor=committed.get(d['id'], 0))
    return camps


def _view(c, camp):
    return _views(c, [camp])[0]


class CampaignIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    objective: str = Field(default='', max_length=500)
    budget_minor: int = Field(default=0, ge=0, le=10_000_000_000)
    currency: str = 'EUR'
    starts_on: Optional[str] = None
    ends_on: Optional[str] = None


def _dates(a, b):
    for d in (a, b):
        if d and not (len(d) == 10 and d[4] == '-' and d[7] == '-'): raise _err(422, 'INVALID_DATE', 'Dates are YYYY-MM-DD')
    if a and b and b < a: raise _err(422, 'INVALID_DATE', 'The campaign ends before it starts')


@router.post('/api/campaigns')
def create_campaign(body: CampaignIn, request: Request):
    u = m.current_user(request); require_kind(u, BUY_SIDE, 'Campaigns')
    if body.currency.upper() not in CURRENCIES: raise _err(422, 'INVALID_CURRENCY', f'currency must be one of {", ".join(CURRENCIES)}')
    _dates(body.starts_on, body.ends_on)
    with m.db() as c:
        cid = c.execute('INSERT INTO campaigns(org_id,name,objective,budget_minor,currency,starts_on,ends_on,created_by) VALUES(?,?,?,?,?,?,?,?)',
                        (u['org_id'], body.name.strip(), body.objective, body.budget_minor, body.currency.upper(), body.starts_on, body.ends_on, u['id'])).lastrowid
        transition_log(c, 'campaign', cid, u['org_id'], None, 'DRAFT', u['id'], 'created')
        m.event(c, u, 'campaign.created', 'campaign', cid, {})
        return _view(c, _campaign(c, cid, u['org_id']))


@router.get('/api/campaigns')
def list_campaigns(request: Request):
    u = m.current_user(request)
    with m.db() as c: return _views(c, c.execute('SELECT * FROM campaigns WHERE org_id=? ORDER BY id DESC', (u['org_id'],)).fetchall())


@router.get('/api/campaigns/{cid}')
def get_campaign(cid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c: return _view(c, _campaign(c, cid, u['org_id']))


class CampaignPatch(BaseModel):
    version: int
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    objective: Optional[str] = Field(default=None, max_length=500)
    budget_minor: Optional[int] = Field(default=None, ge=0, le=10_000_000_000)
    starts_on: Optional[str] = None
    ends_on: Optional[str] = None


@router.patch('/api/campaigns/{cid}')
def update_campaign(cid: int, body: CampaignPatch, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        camp = _campaign(c, cid, u['org_id'])
        if camp['status'] not in ('DRAFT', 'SUBMITTED', 'PAUSED'): raise _err(409, 'INVALID_STATE', f'A {camp["status"]} campaign cannot be edited')
        fields = body.model_dump(exclude_none=True, exclude={'version'})
        _dates(fields.get('starts_on', camp['starts_on']), fields.get('ends_on', camp['ends_on']))
        if 'budget_minor' in fields and fields['budget_minor'] < _view(c, camp)['committed_minor']: raise _err(409, 'BUDGET_BELOW_COMMITTED', 'The budget is below what is already committed')
        sets = ', '.join(f'{k}=?' for k in fields)
        if c.execute(f'UPDATE campaigns SET {sets}{", " if sets else ""}version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND version=?',
                     (*fields.values(), cid, body.version)).rowcount != 1:
            raise _err(409, 'CONCURRENT_UPDATE', 'The campaign changed meanwhile; reload and try again')
        m.event(c, u, 'campaign.updated', 'campaign', cid, fields)
        return _view(c, _campaign(c, cid, u['org_id']))


class ProductsIn(BaseModel):
    product_ids: list[int] = Field(min_length=1, max_length=20)


@router.put('/api/campaigns/{cid}/products')
def set_products(cid: int, body: ProductsIn, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        camp = _campaign(c, cid, u['org_id'])
        if camp['status'] not in OPEN: raise _err(409, 'INVALID_STATE', f'A {camp["status"]} campaign cannot change')
        for pid in body.product_ids:
            p = m.owned(c, 'products', pid, u['org_id'])
            if p['status'] != 'ACTIVE' or p['source_product_id']: raise _err(409, 'PRODUCT_NOT_ACTIVE', f'Product {pid} is not an active product of this brand')
        c.execute('DELETE FROM campaign_products WHERE campaign_id=?', (cid,))
        for pid in sorted(set(body.product_ids)): c.execute('INSERT INTO campaign_products(campaign_id,product_id) VALUES(?,?)', (cid, pid))
        m.event(c, u, 'campaign.products_set', 'campaign', cid, {'product_ids': sorted(set(body.product_ids))})
        return _view(c, camp)


class TargetingIn(BaseModel):
    categories: list[str] = Field(default_factory=list)
    placement_categories: list[str] = Field(default_factory=list)
    publisher_ids: list[int] = Field(default_factory=list)
    min_score: float = Field(default=0.0, ge=0, le=1)
    require_brand_safe: bool = True
    max_price_minor: Optional[int] = Field(default=None, gt=0)


@router.put('/api/campaigns/{cid}/targeting')
def set_targeting(cid: int, body: TargetingIn, request: Request):
    u = m.current_user(request)
    from .opportunities import CATEGORIES as PLACEMENT_CATEGORIES
    from .decisions import CATEGORIES
    bad = [x for x in body.placement_categories if x not in PLACEMENT_CATEGORIES] + [x for x in body.categories if x not in CATEGORIES]
    if bad: raise _err(422, 'INVALID_CATEGORY', f'Unknown categories: {bad}')
    with m.db() as c:
        camp = _campaign(c, cid, u['org_id'])
        if camp['status'] not in OPEN: raise _err(409, 'INVALID_STATE', f'A {camp["status"]} campaign cannot change')
        c.execute('INSERT INTO campaign_targeting(campaign_id,categories_json,placement_categories_json,publisher_ids_json,min_score,require_brand_safe,max_price_minor) VALUES(?,?,?,?,?,?,?) '
                  'ON CONFLICT(campaign_id) DO UPDATE SET categories_json=excluded.categories_json, placement_categories_json=excluded.placement_categories_json, '
                  'publisher_ids_json=excluded.publisher_ids_json, min_score=excluded.min_score, require_brand_safe=excluded.require_brand_safe, '
                  'max_price_minor=excluded.max_price_minor, updated_at=CURRENT_TIMESTAMP',
                  (cid, json.dumps(body.categories), json.dumps(body.placement_categories), json.dumps(body.publisher_ids), body.min_score, int(body.require_brand_safe), body.max_price_minor))
        m.event(c, u, 'campaign.targeting_set', 'campaign', cid, body.model_dump())
        return _view(c, camp)


class TransitionIn(BaseModel):
    to: str
    reason: str = Field(default='', max_length=500)


@router.post('/api/campaigns/{cid}/transition')
def transition_campaign(cid: int, body: TransitionIn, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        camp = _campaign(c, cid, u['org_id'])
        if body.to not in TRANSITIONS.get(camp['status'], set()):
            raise _err(409, 'INVALID_TRANSITION', f'A campaign cannot move from {camp["status"]} to {body.to}', current=camp['status'], target=body.to)
        if body.to in ('SUBMITTED', 'ACTIVE') and not c.execute('SELECT 1 FROM campaign_products WHERE campaign_id=?', (cid,)).fetchone():
            raise _err(409, 'CAMPAIGN_INCOMPLETE', 'Add at least one product first')
        if c.execute('UPDATE campaigns SET status=?, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status=?', (body.to, cid, camp['status'])).rowcount != 1:
            raise _err(409, 'CONCURRENT_UPDATE', 'The campaign changed meanwhile')
        transition_log(c, 'campaign', cid, u['org_id'], camp['status'], body.to, u['id'], body.reason)
        if body.to == 'CANCELLED':
            for r in c.execute("SELECT id FROM placement_requests WHERE campaign_id=? AND status='REQUESTED'", (cid,)).fetchall(): _close_request(c, r['id'], 'CANCELLED', u, 'campaign cancelled')
            for r in c.execute("SELECT id, inventory_id FROM reservations WHERE campaign_id=? AND status='HELD'", (cid,)).fetchall(): _release(c, r['id'], r['inventory_id'])
        outbox.emit(c, 'campaign.status_changed', 'campaign', cid, {'campaign_id': cid, 'from': camp['status'], 'to': body.to}, org_id=u['org_id'])
        return _view(c, _campaign(c, cid, u['org_id']))


@router.get('/api/campaigns/{cid}/recommendations')
def campaign_recommendations(cid: int, request: Request, limit: int = 20):
    u = m.current_user(request)
    from .catalogue import match
    with m.db() as c:
        camp = _view(c, _campaign(c, cid, u['org_id'])); t = camp['targeting'] or {}
        out = []
        for pid in camp['product_ids']:
            p = dict(c.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone())
            if t.get('categories') and p['category'] not in t['categories']: continue
            left_out = {}
            ranked = match(c, u['org_id'], p, t.get('max_price_minor'), t.get('placement_categories') or (), t.get('min_score', 0.0), t.get('publisher_ids') or (),
                           t.get('require_brand_safe', True), excluded=left_out)[:max(1, min(limit, 100))]
            out.append({'product_id': pid, 'results': ranked, 'left_out': left_out})
    return {'campaign_id': cid, 'products': out}


# ---------------------------------------------------------------- placement requests
def _release(c, rid, inventory_id):
    if c.execute("UPDATE reservations SET status='RELEASED', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='HELD'", (rid,)).rowcount:
        c.execute("UPDATE inventory_items SET status='AVAILABLE', version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='RESERVED'", (inventory_id,))


def _close_request(c, req_id, status, u, reason):
    r = c.execute('SELECT * FROM placement_requests WHERE id=?', (req_id,)).fetchone()
    c.execute('UPDATE placement_requests SET status=?, rejection_reason=?, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?', (status, reason, req_id))
    _release(c, r['reservation_id'], r['inventory_id'])
    transition_log(c, 'placement_request', req_id, r['brand_org_id'], r['status'], status, u['id'], reason)


def request_view(c, r, org_id):
    d = dict(r); d['side'] = 'brand' if r['brand_org_id'] == org_id else 'publisher'
    d['product'] = dict(c.execute('SELECT id,name,category FROM products WHERE id=?', (r['product_id'],)).fetchone())
    res = c.execute('SELECT status, price_minor, currency, expires_at FROM reservations WHERE id=?', (r['reservation_id'],)).fetchone()
    d['reservation'] = dict(res) if res else None
    q = c.execute('SELECT id, amount_minor, currency, platform_fee_bps, status FROM quotes WHERE placement_request_id=?', (r['id'],)).fetchone()
    d['quote'] = dict(q) if q else None
    dl = c.execute('SELECT id, status, output_sha256, delivered_at, accepted_at FROM delivery_records WHERE placement_request_id=? ORDER BY id DESC LIMIT 1', (r['id'],)).fetchone()
    d['delivery'] = dict(dl) if dl else None
    d['transitions'] = [dict(x) for x in c.execute("SELECT from_status,to_status,user_id,reason,created_at FROM status_transitions WHERE entity_type='placement_request' AND entity_id=? ORDER BY id",
                                                    (r['id'],)).fetchall()]
    if d['side'] == 'brand': d.pop('shadow_product_id', None)
    return d


def _request(c, req_id, org_id):
    r = c.execute('SELECT * FROM placement_requests WHERE id=? AND (brand_org_id=? OR publisher_org_id=?)', (req_id, org_id, org_id)).fetchone()
    if not r: raise HTTPException(404, 'Resource not found')
    return dict(r)


class RequestIn(BaseModel):
    reservation_id: int
    product_id: int
    notes: str = Field(default='', max_length=1000)


@router.post('/api/campaigns/{cid}/placement-requests')
def create_request(cid: int, body: RequestIn, request: Request):
    u = m.current_user(request); require_kind(u, BUY_SIDE, 'Placement requests')
    with m.db() as c:
        camp = _campaign(c, cid, u['org_id'])
        if camp['status'] not in ('SUBMITTED', 'ACTIVE'): raise _err(409, 'INVALID_STATE', f'A {camp["status"]} campaign cannot request placements')
        if not c.execute('SELECT 1 FROM campaign_products WHERE campaign_id=? AND product_id=?', (cid, body.product_id)).fetchone():
            raise _err(409, 'PRODUCT_NOT_IN_CAMPAIGN', 'The product is not part of this campaign')
        p = m.owned(c, 'products', body.product_id, u['org_id'])
        if p['status'] != 'ACTIVE': raise _err(409, 'PRODUCT_NOT_ACTIVE', f'The product is {p["status"]}')
        res = c.execute("SELECT * FROM reservations WHERE id=? AND brand_org_id=? AND status='HELD' AND expires_at>=?", (body.reservation_id, u['org_id'], time.time())).fetchone()
        if not res: raise _err(409, 'RESERVATION_NOT_HELD', 'You do not hold this reservation (it may have expired)')
        if res['campaign_id'] not in (None, cid): raise _err(409, 'RESERVATION_OTHER_CAMPAIGN', 'The reservation belongs to another campaign')
        committed = _view(c, camp)['committed_minor']
        if camp['budget_minor'] and committed + res['price_minor'] > camp['budget_minor']: raise _err(409, 'OVER_BUDGET', 'This placement would exceed the campaign budget')
        if res['currency'] != camp['currency']: raise _err(409, 'CURRENCY_MISMATCH', f'The item is priced in {res["currency"]}, the campaign in {camp["currency"]}')
        c.execute('UPDATE reservations SET campaign_id=?, expires_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?', (cid, time.time() + response_seconds(), res['id']))
        req = c.execute('INSERT INTO placement_requests(campaign_id,brand_org_id,publisher_org_id,inventory_id,reservation_id,product_id,product_asset_version,notes,brand_approved_by,brand_approved_at,created_by) '
                        'VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?)', (cid, u['org_id'], res['publisher_org_id'], res['inventory_id'], res['id'], p['id'], p['image_version'],
                                                                         body.notes, u['id'], u['id'])).lastrowid
        transition_log(c, 'placement_request', req, u['org_id'], None, 'REQUESTED', u['id'], 'brand approval')
        notify(c, res['publisher_org_id'], 'placement_request.created', 'A brand requests a placement', f'Request {req} for inventory item {res["inventory_id"]}', 'placement_request', req)
        outbox.emit(c, 'placement_request.created', 'placement_request', req, {'placement_request_id': req, 'campaign_id': cid, 'inventory_id': res['inventory_id']}, org_id=u['org_id'])
        m.event(c, u, 'placement_request.created', 'placement_request', req, {'campaign_id': cid})
        return request_view(c, c.execute('SELECT * FROM placement_requests WHERE id=?', (req,)).fetchone(), u['org_id'])


@router.get('/api/placement-requests')
def list_requests(request: Request, status: Optional[str] = None):
    u = m.current_user(request)
    with m.db() as c:
        rows = c.execute('SELECT * FROM placement_requests WHERE (brand_org_id=? OR publisher_org_id=?)' + (' AND status=?' if status else '') + ' ORDER BY id DESC LIMIT 500',
                         (u['org_id'], u['org_id'], status) if status else (u['org_id'], u['org_id'])).fetchall()
        return [request_view(c, r, u['org_id']) for r in rows]


@router.get('/api/placement-requests/{req_id}')
def get_request(req_id: int, request: Request):
    u = m.current_user(request)
    with m.db() as c: return request_view(c, _request(c, req_id, u['org_id']), u['org_id'])


class DecisionIn(BaseModel):
    reason: str = Field(default='', max_length=1000)


@router.post('/api/placement-requests/{req_id}/approve')
def approve_request(req_id: int, request: Request):
    u = m.current_user(request); require_kind(u, SELL_SIDE, 'Approving placement requests')
    from . import commercial
    with m.db() as c:
        r = _request(c, req_id, u['org_id'])
        if r['publisher_org_id'] != u['org_id']: raise _err(403, 'WRONG_PARTY', 'Only the publisher approves here; the brand approved when it made the request')
        if r['status'] != 'REQUESTED': raise _err(409, 'INVALID_TRANSITION', f'A {r["status"]} request cannot be approved')
        from .inventory import require_sellable
        require_sellable(c, c.execute('SELECT * FROM inventory_items WHERE id=?', (r['inventory_id'],)).fetchone())
        if c.execute("UPDATE placement_requests SET status='APPROVED', publisher_approved_by=?, publisher_approved_at=CURRENT_TIMESTAMP, version=version+1, updated_at=CURRENT_TIMESTAMP "
                     "WHERE id=? AND status='REQUESTED'", (u['id'], req_id)).rowcount != 1:
            raise _err(409, 'CONCURRENT_UPDATE', 'The request changed meanwhile')
        if c.execute("UPDATE reservations SET status='CONFIRMED', expires_at=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='HELD'", (r['reservation_id'],)).rowcount != 1:
            raise _err(409, 'RESERVATION_NOT_HELD', 'The reservation is no longer held')
        c.execute("UPDATE inventory_items SET status='SOLD', version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='RESERVED'", (r['inventory_id'],))
        transition_log(c, 'placement_request', req_id, r['brand_org_id'], 'REQUESTED', 'APPROVED', u['id'], 'publisher approval')
        transition_log(c, 'inventory', r['inventory_id'], u['org_id'], 'RESERVED', 'SOLD', u['id'], f'request {req_id}')
        qid = commercial.issue_quote(c, req_id)
        if c.execute("UPDATE campaigns SET status='ACTIVE', version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='SUBMITTED'", (r['campaign_id'],)).rowcount:
            transition_log(c, 'campaign', r['campaign_id'], r['brand_org_id'], 'SUBMITTED', 'ACTIVE', u['id'], f'first approved request {req_id}')
        notify(c, r['brand_org_id'], 'placement_request.approved', 'The publisher approved your placement request', f'Request {req_id}; quote {qid}', 'placement_request', req_id)
        outbox.emit(c, 'placement_request.approved', 'placement_request', req_id, {'placement_request_id': req_id, 'quote_id': qid, 'campaign_id': r['campaign_id']}, org_id=r['brand_org_id'])
        m.event(c, u, 'placement_request.approved', 'placement_request', req_id, {'quote_id': qid})
        return request_view(c, _request(c, req_id, u['org_id']), u['org_id'])


@router.post('/api/placement-requests/{req_id}/reject')
def reject_request(req_id: int, body: DecisionIn, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        r = _request(c, req_id, u['org_id'])
        if r['publisher_org_id'] != u['org_id']: raise _err(403, 'WRONG_PARTY', 'Only the publisher rejects a request; the brand cancels it')
        if r['status'] != 'REQUESTED': raise _err(409, 'INVALID_TRANSITION', f'A {r["status"]} request cannot be rejected')
        _close_request(c, req_id, 'REJECTED', u, body.reason or 'rejected by the publisher')
        notify(c, r['brand_org_id'], 'placement_request.rejected', 'The publisher rejected your placement request', body.reason, 'placement_request', req_id)
        outbox.emit(c, 'placement_request.rejected', 'placement_request', req_id, {'placement_request_id': req_id}, org_id=r['brand_org_id'])
        return request_view(c, _request(c, req_id, u['org_id']), u['org_id'])


@router.post('/api/placement-requests/{req_id}/cancel')
def cancel_request(req_id: int, body: DecisionIn, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        r = _request(c, req_id, u['org_id'])
        if r['brand_org_id'] != u['org_id']: raise _err(403, 'WRONG_PARTY', 'Only the brand cancels a request; the publisher rejects it')
        if r['status'] != 'REQUESTED': raise _err(409, 'INVALID_TRANSITION', f'A {r["status"]} request cannot be cancelled')
        _close_request(c, req_id, 'CANCELLED', u, body.reason or 'cancelled by the brand')
        notify(c, r['publisher_org_id'], 'placement_request.cancelled', 'A brand cancelled its placement request', body.reason, 'placement_request', req_id)
        return request_view(c, _request(c, req_id, u['org_id']), u['org_id'])


def _copy_file(path):
    if not path: return None
    src = storage.local(path); dst = m.UPLOADS / f'linked_{secrets.token_hex(6)}_{os.path.basename(str(path))[-60:]}'
    shutil.copyfile(src, dst); storage.persist(dst)
    return str(dst)


@router.post('/api/placement-requests/{req_id}/start-production')
def start_production(req_id: int, request: Request):
    """Phase 10: create the placement in the publisher's organization through the existing placement engine."""
    u = m.current_user(request); require_kind(u, SELL_SIDE, 'Production')
    with m.db() as c:
        r = _request(c, req_id, u['org_id'])
        if r['publisher_org_id'] != u['org_id']: raise _err(403, 'WRONG_PARTY', 'Only the publisher produces the placement')
        if r['status'] != 'APPROVED': raise _err(409, 'INVALID_TRANSITION', f'A {r["status"]} request cannot go into production')
        from .inventory import require_sellable
        require_sellable(c, c.execute('SELECT * FROM inventory_items WHERE id=?', (r['inventory_id'],)).fetchone())
        prod = dict(c.execute('SELECT * FROM products WHERE id=?', (r['product_id'],)).fetchone())
        asset = c.execute("SELECT path FROM product_assets WHERE product_id=? AND kind='IMAGE' AND version=?", (r['product_id'], r['product_asset_version'])).fetchone()
        opp = dict(c.execute('SELECT o.* FROM opportunities o JOIN inventory_items i ON i.opportunity_id=o.id WHERE i.id=?', (r['inventory_id'],)).fetchone())
        brand = c.execute('SELECT name FROM organizations WHERE id=?', (r['brand_org_id'],)).fetchone()['name']
    image = _copy_file(asset['path'] if asset else prod['image_path'])
    derived = _copy_file(prod['derived_path']) if (prod.get('derived_path') and (not asset or r['product_asset_version'] == prod['image_version'])) else None
    if derived is None and prod.get('derived_path'):
        from .compositor import prepare_asset
        derived = str(m.Path(image).with_name(m.Path(image).stem + '.derived.png')); prepare_asset(image, derived); storage.persist(m.Path(derived))
    with m.db() as c:
        shadow = c.execute('INSERT INTO products(org_id,name,description,image_path,created_by,sha256,derived_path,asset_report,size_bytes,category,status,source_product_id,source_org_id) '
                           "VALUES(?,?,?,?,?,?,?,?,?,?,'ACTIVE',?,?)",
                           (u['org_id'], f'{brand}: {prod["name"]}'[:120], prod['description'], image, u['id'], prod['sha256'], derived, prod['asset_report'], 0, prod['category'],
                            prod['id'], r['brand_org_id'])).lastrowid
        c.execute("INSERT INTO product_assets(org_id,product_id,version,kind,path,sha256,created_by) VALUES(?,?,1,'IMAGE',?,?,?)", (u['org_id'], shadow, image, prod['sha256'], u['id']))
    g = json.loads(opp['geometry_json']); x, y, w, h = g['bbox']
    width = max(0.03, min(0.7, w * 0.6))
    body = m.PlacementIn(video_id=opp['video_id'], product_id=shadow, frame_second=g['frame_second'], x=min(1.0, x + w * 0.2), y=min(1.0, y + h * 0.1), width=width,
                         start_second=opp['start_second'], end_second=opp['end_second'], tracking_quad=g.get('quad'))
    try:
        placed = m.create_placement(body, request)
    except HTTPException:
        with m.db() as c: c.execute("UPDATE products SET status='ARCHIVED' WHERE id=?", (shadow,))
        raise
    with m.db() as c:
        if c.execute("UPDATE placement_requests SET status='IN_PRODUCTION', shadow_product_id=?, placement_id=?, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='APPROVED'",
                     (shadow, placed['id'], req_id)).rowcount != 1:
            raise _err(409, 'CONCURRENT_UPDATE', 'The request changed meanwhile')
        transition_log(c, 'placement_request', req_id, r['brand_org_id'], 'APPROVED', 'IN_PRODUCTION', u['id'], f'placement {placed["id"]}')
        notify(c, r['brand_org_id'], 'placement_request.in_production', 'Your placement is in production', f'Request {req_id}', 'placement_request', req_id)
        m.event(c, u, 'placement_request.in_production', 'placement_request', req_id, {'placement_id': placed['id'], 'product_id': shadow})
    return {'placement_request_id': req_id, 'placement_id': placed['id'], 'product_id': shadow,
            'next_step': 'Run the quality check, approve and render the placement; delivery is recorded when the render passes validation'}


@router.post('/api/placement-requests/{req_id}/accept')
def accept_delivery(req_id: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        r = _request(c, req_id, u['org_id'])
        if r['brand_org_id'] != u['org_id']: raise _err(403, 'WRONG_PARTY', 'Only the brand accepts the delivered placement')
        if r['status'] != 'DELIVERED': raise _err(409, 'INVALID_TRANSITION', f'A {r["status"]} request cannot be accepted')
        d = c.execute("SELECT id FROM delivery_records WHERE placement_request_id=? AND status='DELIVERED' ORDER BY id DESC LIMIT 1", (req_id,)).fetchone()
        c.execute("UPDATE delivery_records SET status='ACCEPTED', accepted_at=CURRENT_TIMESTAMP, accepted_by=? WHERE id=?", (u['id'], d['id']))
        c.execute("UPDATE placement_requests SET status='ACCEPTED', version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (req_id,))
        transition_log(c, 'placement_request', req_id, r['brand_org_id'], 'DELIVERED', 'ACCEPTED', u['id'], 'brand accepted the delivery')
        outbox.emit(c, 'delivery.accepted', 'placement_request', req_id, {'placement_request_id': req_id, 'delivery_id': d['id'], 'campaign_id': r['campaign_id']}, org_id=r['brand_org_id'])
        notify(c, r['publisher_org_id'], 'delivery.accepted', 'The brand accepted the delivered placement', f'Request {req_id}', 'placement_request', req_id)
        return request_view(c, _request(c, req_id, u['org_id']), u['org_id'])
