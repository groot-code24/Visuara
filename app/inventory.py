"""Opportunities review, brand-safety rules, advertising inventory and reservations (Phases 5 and 8).

Inventory lifecycle (one item per eligible opportunity):
  DRAFT      created by analysis for a valid opportunity that is not UNSAFE
  AVAILABLE  listed by the publisher with a price, once the opportunity is reviewed and the video's rights are verified
  RESERVED   held by one brand (a `reservations` row in HELD); the hold expires unless a placement request confirms it
  SOLD       both parties approved a placement request; the reservation is CONFIRMED
  WITHDRAWN  taken off the market by the publisher, or its video was deleted

Concurrency (AD-8): a reservation is won by a conditional UPDATE (`... WHERE status='AVAILABLE'`) whose row count decides
the winner, and a unique partial index allows one HELD or CONFIRMED reservation per item. The loser gets 409
INVENTORY_CONFLICT. This works the same on SQLite and PostgreSQL.
"""
import json, os, re, time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import main as m, opportunities, outbox, storage, db as dbm
from .orgs import require_kind, SELL_SIDE, BUY_SIDE

router = APIRouter()
CURRENCIES = ('EUR', 'USD', 'GBP')


def hold_seconds(): return float(os.getenv('CC_RESERVATION_HOLD_SECONDS', '900'))


def _err(status, code, message, **extra): return HTTPException(status, {'code': code, 'message': message, **extra})


def notify(c, org_id, kind, title, body='', entity_type=None, entity_id=None):
    c.execute('INSERT INTO notifications(org_id,kind,title,body,entity_type,entity_id) VALUES(?,?,?,?,?,?)', (org_id, kind, title, body, entity_type, entity_id))


def transition_log(c, entity_type, entity_id, org_id, frm, to, user_id, reason=''):
    c.execute('INSERT INTO status_transitions(entity_type,entity_id,org_id,from_status,to_status,user_id,reason) VALUES(?,?,?,?,?,?,?)',
              (entity_type, entity_id, org_id, frm, to, user_id, reason or ''))


def create_draft(c, opportunity_id):
    o = c.execute('SELECT * FROM opportunities WHERE id=?', (opportunity_id,)).fetchone()
    if not o or o['validation_status'] == 'INVALID' or o['brand_safety'] == 'UNSAFE': return None
    return c.execute("INSERT INTO inventory_items(org_id,opportunity_id,video_id,scene_id,status) VALUES(?,?,?,?,'DRAFT')",
                     (o['org_id'], o['id'], o['video_id'], o['scene_id'])).lastrowid


def rights_verified(c, video_id):
    return c.execute("SELECT id FROM rights_records WHERE video_id=? AND status='VERIFIED' AND (expires_on IS NULL OR expires_on>=?) ORDER BY id DESC LIMIT 1",
                     (video_id, time.strftime('%Y-%m-%d'))).fetchone()


def eligibility(c, item):
    """Reasons an item cannot be listed (empty when it can)."""
    why = []
    o = c.execute('SELECT * FROM opportunities WHERE id=?', (item['opportunity_id'],)).fetchone()
    v = c.execute('SELECT * FROM videos WHERE id=?', (item['video_id'],)).fetchone()
    if not v or v['deleted_at']: why.append('the video was deleted')
    elif not m._license_ok(dict(v)): why.append('the video licence is not confirmed or has expired')
    if o['validation_status'] != 'VALID': why.append(f"the opportunity is {o['validation_status']}: review its brand safety first")
    if o['brand_safety'] != 'SAFE': why.append(f"brand safety is {o['brand_safety']}")
    if not rights_verified(c, item['video_id']): why.append('the video has no verified rights record')
    a = c.execute('SELECT status FROM analysis_versions WHERE id=?', (o['analysis_version_id'],)).fetchone()
    if a['status'] != 'COMPLETED': why.append('the analysis did not complete')
    return why


def require_sellable(c, item):
    """Refuse a sale step (reserve, approve, produce) when the item no longer meets the listing conditions: rights can be
    revoked or expire, a licence can lapse and a video can be deleted after the item was listed."""
    why = eligibility(c, item)
    if why: raise HTTPException(409, {'code': 'NOT_SELLABLE', 'message': 'This inventory can no longer be sold', 'reasons': why})


def expire_holds(c, now=None, inventory_id=None):
    """Release HELD reservations whose time is up. Returns how many expired."""
    now = time.time() if now is None else now
    q = "SELECT id, inventory_id, brand_org_id FROM reservations WHERE status='HELD' AND expires_at<?" + (' AND inventory_id=?' if inventory_id else '')
    rows = c.execute(q, (now, inventory_id) if inventory_id else (now,)).fetchall()
    for r in rows:
        if c.execute("UPDATE reservations SET status='EXPIRED', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='HELD'", (r['id'],)).rowcount:
            c.execute("UPDATE inventory_items SET status='AVAILABLE', version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='RESERVED'", (r['inventory_id'],))
            for q in c.execute("SELECT id FROM placement_requests WHERE reservation_id=? AND status='REQUESTED'", (r['id'],)).fetchall():
                c.execute("UPDATE placement_requests SET status='CANCELLED', rejection_reason='the reservation expired', updated_at=CURRENT_TIMESTAMP WHERE id=?", (q['id'],))
                transition_log(c, 'placement_request', q['id'], r['brand_org_id'], 'REQUESTED', 'CANCELLED', None, 'the reservation expired')
            outbox.emit(c, 'reservation.expired', 'inventory', r['inventory_id'], {'reservation_id': r['id'], 'inventory_id': r['inventory_id']}, org_id=r['brand_org_id'])
    return len(rows)


def run_expiry():
    with m.db() as c: return expire_holds(c)


def item_view(c, it, brand_view=False):
    o = opportunities.load(c.execute('SELECT * FROM opportunities WHERE id=?', (it['opportunity_id'],)).fetchone())
    s = c.execute('SELECT start_second, end_second, description, scene_index FROM scenes WHERE id=?', (it['scene_id'],)).fetchone()
    pub = c.execute('SELECT o.name, p.display_name FROM organizations o LEFT JOIN publisher_profiles p ON p.org_id=o.id WHERE o.id=?', (it['org_id'],)).fetchone()
    d = {k: it[k] for k in ('id', 'org_id', 'opportunity_id', 'video_id', 'scene_id', 'status', 'price_minor', 'currency', 'listed_at', 'version')}
    d['publisher'] = (pub['display_name'] or pub['name']) if pub else None
    d['scene'] = dict(s) if s else None
    keep = ('placement_category', 'candidate_label', 'geometry', 'region', 'start_second', 'end_second', 'visible_seconds', 'feasibility_score', 'score',
            'product_compatibility', 'brand_safety', 'rendering_complexity', 'confidence', 'explanation', 'validation_status')
    d['opportunity'] = {k: o.get(k) for k in keep}
    d['preview_url'] = f'/api/marketplace/inventory/{it["id"]}/preview' if brand_view else f'/api/inventory/{it["id"]}/preview'
    if not brand_view: d['video_title'] = c.execute('SELECT title FROM videos WHERE id=?', (it['video_id'],)).fetchone()['title']
    return d


# ---------------------------------------------------------------- opportunities and review (publisher side)
@router.get('/api/opportunities')
def list_opportunities(request: Request, video_id: Optional[int] = None, analysis_version_id: Optional[int] = None, status: Optional[str] = None):
    u = m.current_user(request)
    q, args = 'SELECT * FROM opportunities WHERE org_id=?', [u['org_id']]
    if video_id: q += ' AND video_id=?'; args.append(video_id)
    if analysis_version_id: q += ' AND analysis_version_id=?'; args.append(analysis_version_id)
    if status: q += ' AND validation_status=?'; args.append(status)
    with m.db() as c: rows = c.execute(q + ' ORDER BY feasibility_score DESC, id LIMIT 500', args).fetchall()
    return [opportunities.load(r) for r in rows]


@router.get('/api/opportunities/{oid}')
def get_opportunity(oid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        r = c.execute('SELECT * FROM opportunities WHERE id=? AND org_id=?', (oid, u['org_id'])).fetchone()
        if not r: raise HTTPException(404, 'Resource not found')
        d = opportunities.load(r)
        it = c.execute('SELECT id, status FROM inventory_items WHERE opportunity_id=?', (oid,)).fetchone()
        d['inventory'] = dict(it) if it else None
    return d


class ReviewIn(BaseModel):
    brand_safety: str
    notes: str = Field(default='', max_length=1000)


@router.post('/api/opportunities/{oid}/review')
def review_opportunity(oid: int, body: ReviewIn, request: Request):
    """A person's brand-safety decision. It is recorded with who made it; the measured data of the opportunity is unchanged."""
    u = m.current_user(request)
    if body.brand_safety not in ('SAFE', 'REVIEW', 'UNSAFE'): raise _err(422, 'INVALID_VERDICT', 'brand_safety must be SAFE, REVIEW or UNSAFE')
    status = {'SAFE': 'VALID', 'REVIEW': 'NEEDS_REVIEW', 'UNSAFE': 'INVALID'}[body.brand_safety]
    with m.db() as c:
        o = c.execute('SELECT * FROM opportunities WHERE id=? AND org_id=?', (oid, u['org_id'])).fetchone()
        if not o: raise HTTPException(404, 'Resource not found')
        notes = json.loads(o['validation_json'] or '{}').get('notes', [])
        # the explanation was written when the opportunity was scored; say what the person decided since (debt 24)
        explanation = re.sub(r' Reviewed: (SAFE|REVIEW|UNSAFE) by a person\.$', '', o['explanation']) + f' Reviewed: {body.brand_safety} by a person.'
        c.execute('UPDATE opportunities SET brand_safety=?, validation_status=?, reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP, validation_json=?, explanation=? WHERE id=?',
                  (body.brand_safety, status, u['id'], json.dumps({'notes': notes, 'review': {'by': u['id'], 'verdict': body.brand_safety, 'notes': body.notes}}),
                   explanation[:2000], oid))
        it = c.execute('SELECT * FROM inventory_items WHERE opportunity_id=?', (oid,)).fetchone()
        if body.brand_safety == 'UNSAFE' and it and it['status'] in ('DRAFT', 'AVAILABLE'):
            c.execute("UPDATE inventory_items SET status='WITHDRAWN', version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (it['id'],))
            transition_log(c, 'inventory', it['id'], u['org_id'], it['status'], 'WITHDRAWN', u['id'], 'brand safety UNSAFE')
        elif body.brand_safety != 'UNSAFE' and not it:
            create_draft(c, oid)
        m.event(c, u, 'opportunity.reviewed', 'opportunity', oid, {'brand_safety': body.brand_safety})
    return {'id': oid, 'brand_safety': body.brand_safety, 'validation_status': status}


class RuleIn(BaseModel):
    term: str = Field(min_length=2, max_length=60)
    verdict: str


@router.get('/api/brand-safety/rules')
def list_rules(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        rows = c.execute('SELECT id, org_id, term, verdict FROM brand_safety_rules WHERE org_id IS NULL OR org_id=? ORDER BY org_id, term', (u['org_id'],)).fetchall()
    return [dict(r, scope='platform' if r['org_id'] is None else 'organization') for r in rows]


@router.post('/api/brand-safety/rules')
def add_rule(body: RuleIn, request: Request):
    u = m.current_user(request)
    if body.verdict not in ('REVIEW', 'UNSAFE'): raise _err(422, 'INVALID_VERDICT', 'verdict must be REVIEW or UNSAFE')
    term = body.term.strip().lower()
    with m.db() as c:
        if c.execute('SELECT 1 FROM brand_safety_rules WHERE org_id=? AND term=?', (u['org_id'], term)).fetchone(): raise _err(409, 'RULE_EXISTS', 'This term already has a rule')
        rid = c.execute('INSERT INTO brand_safety_rules(org_id,term,verdict,created_by) VALUES(?,?,?,?)', (u['org_id'], term, body.verdict, u['id'])).lastrowid
        m.event(c, u, 'brand_safety.rule_added', 'brand_safety_rule', rid, {'term': term, 'verdict': body.verdict})
    return {'id': rid, 'term': term, 'verdict': body.verdict}


@router.delete('/api/brand-safety/rules/{rid}')
def delete_rule(rid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        if c.execute('DELETE FROM brand_safety_rules WHERE id=? AND org_id=?', (rid, u['org_id'])).rowcount != 1: raise HTTPException(404, 'Resource not found')
        m.event(c, u, 'brand_safety.rule_removed', 'brand_safety_rule', rid, {})
    return {'id': rid, 'deleted': True}


# ---------------------------------------------------------------- inventory (publisher side)
def _own_item(c, iid, org_id):
    it = c.execute('SELECT * FROM inventory_items WHERE id=? AND org_id=?', (iid, org_id)).fetchone()
    if not it: raise HTTPException(404, 'Resource not found')
    return dict(it)


@router.get('/api/inventory')
def my_inventory(request: Request, status: Optional[str] = None):
    u = m.current_user(request)
    with m.db() as c:
        expire_holds(c)
        rows = c.execute('SELECT * FROM inventory_items WHERE org_id=?' + (' AND status=?' if status else '') + ' ORDER BY id DESC LIMIT 500',
                         (u['org_id'], status) if status else (u['org_id'],)).fetchall()
        return [dict(item_view(c, r), eligibility=eligibility(c, r) if r['status'] in ('DRAFT', 'WITHDRAWN') else []) for r in rows]


class ListIn(BaseModel):
    price_minor: int = Field(gt=0, le=100_000_000_00)
    currency: str = 'EUR'


@router.post('/api/inventory/{iid}/list')
def list_item(iid: int, body: ListIn, request: Request):
    u = m.current_user(request); require_kind(u, SELL_SIDE, 'Listing inventory')
    cur = body.currency.upper()
    if cur not in CURRENCIES: raise _err(422, 'INVALID_CURRENCY', f'currency must be one of {", ".join(CURRENCIES)}')
    with m.db() as c:
        it = _own_item(c, iid, u['org_id'])
        if it['status'] not in ('DRAFT', 'WITHDRAWN', 'AVAILABLE'): raise _err(409, 'INVALID_TRANSITION', f'An item that is {it["status"]} cannot be listed')
        why = eligibility(c, it)
        if why: raise _err(409, 'NOT_ELIGIBLE', 'This item cannot be listed: ' + '; '.join(why), reasons=why)
        if c.execute("UPDATE inventory_items SET status='AVAILABLE', price_minor=?, currency=?, listed_at=CURRENT_TIMESTAMP, version=version+1, updated_at=CURRENT_TIMESTAMP "
                     "WHERE id=? AND version=?", (body.price_minor, cur, iid, it['version'])).rowcount != 1:
            raise _err(409, 'CONCURRENT_UPDATE', 'The item changed meanwhile; reload and try again')
        transition_log(c, 'inventory', iid, u['org_id'], it['status'], 'AVAILABLE', u['id'], f'{body.price_minor} {cur}')
        m.event(c, u, 'inventory.listed', 'inventory', iid, {'price_minor': body.price_minor, 'currency': cur})
        return item_view(c, c.execute('SELECT * FROM inventory_items WHERE id=?', (iid,)).fetchone())


@router.post('/api/inventory/{iid}/withdraw')
def withdraw_item(iid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        it = _own_item(c, iid, u['org_id'])
        if c.execute("UPDATE inventory_items SET status='WITHDRAWN', version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('DRAFT','AVAILABLE')", (iid,)).rowcount != 1:
            raise _err(409, 'INVALID_TRANSITION', f'An item that is {it["status"]} cannot be withdrawn')
        transition_log(c, 'inventory', iid, u['org_id'], it['status'], 'WITHDRAWN', u['id'])
        m.event(c, u, 'inventory.withdrawn', 'inventory', iid, {})
    return {'id': iid, 'status': 'WITHDRAWN'}


def _preview(c, scene_id):
    f = c.execute('SELECT image_path FROM scene_frames WHERE scene_id=? ORDER BY representative DESC, second LIMIT 1', (scene_id,)).fetchone()
    if not f: raise HTTPException(404, 'No preview frame')
    return storage.response(f['image_path'], media_type='image/jpeg')


@router.get('/api/inventory/{iid}/preview')
def item_preview(iid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c: it = _own_item(c, iid, u['org_id'])
    with m.db() as c: return _preview(c, it['scene_id'])


# ---------------------------------------------------------------- marketplace (buy side)
@router.get('/api/marketplace/inventory')
def browse(request: Request, placement_category: Optional[str] = None, product_category: Optional[str] = None, min_score: float = Query(0, ge=0, le=1),
           max_price_minor: Optional[int] = Query(None, gt=0), publisher_org_id: Optional[int] = None, currency: Optional[str] = None):
    u = m.current_user(request); require_kind(u, BUY_SIDE, 'Browsing the marketplace')
    q = "SELECT i.* FROM inventory_items i JOIN opportunities o ON o.id=i.opportunity_id WHERE i.status='AVAILABLE' AND o.feasibility_score>=? AND i.org_id<>?"
    args = [min_score, u['org_id']]
    if placement_category: q += ' AND o.placement_category=?'; args.append(placement_category)
    if max_price_minor: q += ' AND i.price_minor<=?'; args.append(max_price_minor)
    if publisher_org_id: q += ' AND i.org_id=?'; args.append(publisher_org_id)
    if currency: q += ' AND i.currency=?'; args.append(currency.upper())
    with m.db() as c:
        expire_holds(c)
        rows = c.execute(q + ' ORDER BY o.feasibility_score DESC, i.id LIMIT 200', args).fetchall()
        out = [item_view(c, r, brand_view=True) for r in rows if not eligibility(c, r)]          # still sellable now, not only when listed
    if product_category:
        out = sorted((d for d in out if d['opportunity']['product_compatibility'].get(product_category, 0) > 0),
                     key=lambda d: -d['opportunity']['product_compatibility'].get(product_category, 0) * d['opportunity']['feasibility_score'])
    return out


def _visible_to_brand(c, iid, org_id):
    it = c.execute('SELECT * FROM inventory_items WHERE id=?', (iid,)).fetchone()
    if not it: raise HTTPException(404, 'Resource not found')
    mine = c.execute("SELECT 1 FROM reservations WHERE inventory_id=? AND brand_org_id=? AND status IN ('HELD','CONFIRMED')", (iid, org_id)).fetchone()
    if it['status'] != 'AVAILABLE' and not mine: raise HTTPException(404, 'Resource not found')
    return dict(it)


@router.get('/api/marketplace/inventory/{iid}')
def browse_item(iid: int, request: Request):
    u = m.current_user(request); require_kind(u, BUY_SIDE, 'Browsing the marketplace')
    with m.db() as c: return item_view(c, _visible_to_brand(c, iid, u['org_id']), brand_view=True)


@router.get('/api/marketplace/inventory/{iid}/preview')
def browse_preview(iid: int, request: Request):
    u = m.current_user(request); require_kind(u, BUY_SIDE, 'Browsing the marketplace')
    with m.db() as c: return _preview(c, _visible_to_brand(c, iid, u['org_id'])['scene_id'])


class ReserveIn(BaseModel):
    campaign_id: Optional[int] = None


@router.post('/api/marketplace/inventory/{iid}/reserve')
def reserve(iid: int, body: ReserveIn, request: Request):
    u = m.current_user(request); require_kind(u, BUY_SIDE, 'Reserving inventory')
    now = time.time()
    try:
        with m.db() as c:
            if body.campaign_id is not None and not c.execute("SELECT 1 FROM campaigns WHERE id=? AND org_id=? AND status IN ('DRAFT','SUBMITTED','ACTIVE')", (body.campaign_id, u['org_id'])).fetchone():
                raise _err(404, 'CAMPAIGN_NOT_FOUND', 'No open campaign with this id')
            expire_holds(c, now, iid)
            it = c.execute('SELECT * FROM inventory_items WHERE id=?', (iid,)).fetchone()
            if not it or (it['status'] not in ('AVAILABLE', 'RESERVED', 'SOLD')): raise HTTPException(404, 'Resource not found')
            if it['org_id'] == u['org_id']: raise _err(409, 'OWN_INVENTORY', 'An organization cannot reserve its own inventory')
            if it['status'] == 'AVAILABLE': require_sellable(c, it)
            # The conditional update decides the winner; the loser sees rowcount 0.
            if c.execute("UPDATE inventory_items SET status='RESERVED', version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='AVAILABLE'", (iid,)).rowcount != 1:
                raise _err(409, 'INVENTORY_CONFLICT', 'This inventory is already reserved or sold')
            rid = c.execute("INSERT INTO reservations(inventory_id,brand_org_id,publisher_org_id,campaign_id,status,price_minor,currency,expires_at,created_by) VALUES(?,?,?,?,'HELD',?,?,?,?)",
                            (iid, u['org_id'], it['org_id'], body.campaign_id, it['price_minor'], it['currency'], now + hold_seconds(), u['id'])).lastrowid
            transition_log(c, 'inventory', iid, it['org_id'], 'AVAILABLE', 'RESERVED', u['id'], f'reservation {rid}')
            outbox.emit(c, 'reservation.created', 'inventory', iid, {'reservation_id': rid, 'inventory_id': iid, 'brand_org_id': u['org_id'], 'campaign_id': body.campaign_id,
                                                                     'price_minor': it['price_minor'], 'currency': it['currency']}, org_id=u['org_id'])
            notify(c, it['org_id'], 'reservation.created', 'An item of your inventory was reserved', f'Item {iid} is held for {int(hold_seconds() // 60)} minutes', 'inventory', iid)
            m.event(c, u, 'inventory.reserved', 'reservation', rid, {'inventory_id': iid})
    except dbm.IntegrityError:
        raise _err(409, 'INVENTORY_CONFLICT', 'This inventory is already reserved or sold')
    return {'id': rid, 'inventory_id': iid, 'status': 'HELD', 'expires_at': now + hold_seconds(), 'price_minor': it['price_minor'], 'currency': it['currency']}


def _reservation(c, rid, org_id):
    r = c.execute('SELECT * FROM reservations WHERE id=? AND (brand_org_id=? OR publisher_org_id=?)', (rid, org_id, org_id)).fetchone()
    if not r: raise HTTPException(404, 'Resource not found')
    return dict(r)


@router.get('/api/reservations')
def list_reservations(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        expire_holds(c)
        rows = c.execute('SELECT * FROM reservations WHERE brand_org_id=? OR publisher_org_id=? ORDER BY id DESC LIMIT 500', (u['org_id'], u['org_id'])).fetchall()
    return [dict(r, side='brand' if r['brand_org_id'] == u['org_id'] else 'publisher') for r in rows]


@router.post('/api/reservations/{rid}/release')
def release(rid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        r = _reservation(c, rid, u['org_id'])
        if r['brand_org_id'] != u['org_id']: raise HTTPException(404, 'Resource not found')
        if c.execute("UPDATE reservations SET status='RELEASED', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='HELD'", (rid,)).rowcount != 1:
            raise _err(409, 'INVALID_TRANSITION', f'A reservation that is {r["status"]} cannot be released')
        c.execute("UPDATE inventory_items SET status='AVAILABLE', version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='RESERVED'", (r['inventory_id'],))
        for q in c.execute("SELECT id FROM placement_requests WHERE reservation_id=? AND status='REQUESTED'", (rid,)).fetchall():
            c.execute("UPDATE placement_requests SET status='CANCELLED', rejection_reason='the brand released the reservation', updated_at=CURRENT_TIMESTAMP WHERE id=?", (q['id'],))
            transition_log(c, 'placement_request', q['id'], u['org_id'], 'REQUESTED', 'CANCELLED', u['id'], 'the brand released the reservation')
        transition_log(c, 'inventory', r['inventory_id'], r['publisher_org_id'], 'RESERVED', 'AVAILABLE', u['id'], f'reservation {rid} released')
        outbox.emit(c, 'reservation.released', 'inventory', r['inventory_id'], {'reservation_id': rid, 'inventory_id': r['inventory_id']}, org_id=u['org_id'])
    return {'id': rid, 'status': 'RELEASED'}


@router.get('/api/notifications')
def list_notifications(request: Request, unread: bool = False):
    u = m.current_user(request)
    with m.db() as c:
        rows = c.execute('SELECT * FROM notifications WHERE org_id=?' + (' AND read_at IS NULL' if unread else '') + ' ORDER BY id DESC LIMIT 200', (u['org_id'],)).fetchall()
    return [dict(r) for r in rows]


@router.post('/api/notifications/{nid}/read')
def read_notification(nid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        if c.execute('UPDATE notifications SET read_at=CURRENT_TIMESTAMP WHERE id=? AND org_id=? AND read_at IS NULL', (nid, u['org_id'])).rowcount != 1: raise HTTPException(404, 'Resource not found')
    return {'id': nid, 'read': True}
