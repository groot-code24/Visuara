"""Commerce workspace and curated marketplace: listings, campaigns, fixed-price offers, licence grants, delivery, reports.

Cross-organization model: a creator organization lists a video; a brand organization makes a fixed-price offer
from a campaign; acceptance creates a licence grant that lets the brand organization place products on that one
video. Per-version creator consent and approvals still apply to every placement. No payments are processed:
earnings are agreed-fee records only.
"""
import csv, io, json, time
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from . import main as m

router = APIRouter()
CHANNELS = {'social', 'web', 'paid_ads', 'broadcast', 'ott', 'in_app'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS video_listings(video_id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, listed INTEGER NOT NULL DEFAULT 1, description TEXT DEFAULT '', categories TEXT NOT NULL DEFAULT '[]', excluded_categories TEXT NOT NULL DEFAULT '[]', price_usd REAL NOT NULL, channels TEXT NOT NULL DEFAULT '["social","web"]', territories TEXT NOT NULL DEFAULT '["WORLDWIDE"]', updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS campaigns(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, name TEXT NOT NULL, objective TEXT DEFAULT '', product_id INTEGER, category TEXT NOT NULL, budget_usd REAL NOT NULL DEFAULT 0, channels TEXT NOT NULL DEFAULT '["social"]', territories TEXT NOT NULL DEFAULT '["WORLDWIDE"]', brand_safety_exclusions TEXT NOT NULL DEFAULT '[]', starts_on TEXT, ends_on TEXT, status TEXT NOT NULL DEFAULT 'ACTIVE', created_by INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS offers(id INTEGER PRIMARY KEY, campaign_id INTEGER NOT NULL, brand_org_id INTEGER NOT NULL, video_id INTEGER NOT NULL, creator_org_id INTEGER NOT NULL, price_usd REAL NOT NULL, category TEXT NOT NULL, channels TEXT NOT NULL, territories TEXT NOT NULL, deliverables TEXT DEFAULT '', message TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'OFFERED', grant_id INTEGER, decided_by INTEGER, created_by INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS video_grants(id INTEGER PRIMARY KEY, video_id INTEGER NOT NULL, grantee_org_id INTEGER NOT NULL, offer_id INTEGER NOT NULL UNIQUE, channels TEXT NOT NULL, territories TEXT NOT NULL, expires_at TEXT, revoked_at TEXT, revocation_reason TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_grants_video ON video_grants(video_id,grantee_org_id);
CREATE INDEX IF NOT EXISTS idx_offers_video ON offers(video_id,status);
'''
OFFER_TRANSITIONS = {'OFFERED': {'ACCEPTED', 'DECLINED', 'WITHDRAWN'}, 'ACCEPTED': {'DELIVERED', 'REVOKED'}, 'DELIVERED': {'COMPLETED', 'REVOKED'},
                     'DECLINED': set(), 'WITHDRAWN': set(), 'COMPLETED': set(), 'REVOKED': set()}


def init_schema():
    with m.db() as c:
        c.executescript(SCHEMA)


def active_grant(c, video_id, org_id):
    today = time.strftime('%Y-%m-%d')
    return c.execute('SELECT * FROM video_grants WHERE video_id=? AND grantee_org_id=? AND revoked_at IS NULL AND (expires_at IS NULL OR substr(expires_at,1,10)>=?) ORDER BY id DESC LIMIT 1',
                     (video_id, org_id, today)).fetchone()


def _offer_move(c, offer, target, u, reason=None):
    if target not in OFFER_TRANSITIONS[offer['status']]:
        raise m._err(409, 'INVALID_OFFER_TRANSITION', f"Offer is {offer['status']}; it cannot become {target}")
    if c.execute('UPDATE offers SET status=?,updated_at=CURRENT_TIMESTAMP,decided_by=? WHERE id=? AND status=?', (target, u['id'] if u else None, offer['id'], offer['status'])).rowcount != 1:
        raise m._err(409, 'CONCURRENT_CHANGE', 'Offer changed concurrently; reload')
    c.execute('INSERT INTO events(org_id,user_id,event_type,entity_type,entity_id,details) VALUES(?,?,?,?,?,?)',
              (offer['brand_org_id'], u['id'] if u else 0, 'offer.transition', 'offer', offer['id'], json.dumps({'from': offer['status'], 'to': target, 'reason': reason})))


def on_delivered(c, placement):
    """Called when a placement becomes DELIVERED: mark the licence's offer as delivered."""
    v = c.execute('SELECT org_id FROM videos WHERE id=?', (placement['video_id'],)).fetchone()
    if not v or v['org_id'] == placement['org_id']:
        return
    g = active_grant(c, placement['video_id'], placement['org_id'])
    if g:
        o = c.execute("SELECT * FROM offers WHERE id=? AND status='ACCEPTED'", (g['offer_id'],)).fetchone()
        if o: _offer_move(c, dict(o), 'DELIVERED', None, f"placement {placement['id']} v{placement['version']} delivered")


def _lst(x): return json.loads(x) if isinstance(x, str) else x


def _territories_ok(ts):
    return all(t == 'WORLDWIDE' or (t.isalpha() and len(t) in (2, 3)) for t in ts)


# ------------------------------------------------------------------ listings (creator)
class ListingIn(BaseModel):
    listed: bool = True
    description: str = Field(default='', max_length=1000)
    categories: list[str] = Field(default_factory=list, max_length=20)
    excluded_categories: list[str] = Field(default_factory=list, max_length=50)
    price_usd: float = Field(ge=0, le=10_000_000)
    channels: list[str] = Field(default_factory=lambda: ['social', 'web'], min_length=1)
    territories: list[str] = Field(default_factory=lambda: ['WORLDWIDE'], min_length=1)


@router.put('/api/videos/{vid}/listing')
def put_listing(vid: int, body: ListingIn, request: Request):
    u = m.current_user(request); m.require_role(u, 'creator')
    if not set(body.channels) <= CHANNELS: raise HTTPException(422, 'Unsupported channel')
    if not _territories_ok(body.territories): raise HTTPException(422, 'Territories must be WORLDWIDE or ISO codes')
    norm = lambda xs: sorted({x.strip().lower() for x in xs if x.strip()})
    with m.db() as c:
        v = c.execute('SELECT * FROM videos WHERE id=? AND org_id=?', (vid, u['org_id'])).fetchone()
        if not v: raise HTTPException(404, 'Video not found')
        if v['created_by'] != u['id']: raise m._err(403, 'NOT_RIGHTS_HOLDER', 'Only the rights holder can list this video')
        if not m._license_ok(v): raise m._err(409, 'LICENSE_INVALID', 'The video licence is missing or expired')
        if not v['analyzed']: raise m._err(409, 'NOT_ANALYZED', 'Analyse scenes first so brands can preview the video')
        c.execute('INSERT INTO video_listings(video_id,org_id,listed,description,categories,excluded_categories,price_usd,channels,territories) VALUES(?,?,?,?,?,?,?,?,?) '
                  'ON CONFLICT(video_id) DO UPDATE SET listed=excluded.listed,description=excluded.description,categories=excluded.categories,excluded_categories=excluded.excluded_categories,'
                  'price_usd=excluded.price_usd,channels=excluded.channels,territories=excluded.territories,updated_at=CURRENT_TIMESTAMP',
                  (vid, u['org_id'], int(body.listed), body.description, json.dumps(norm(body.categories)), json.dumps(norm(body.excluded_categories)),
                   body.price_usd, json.dumps(sorted(set(body.channels))), json.dumps([t.upper() for t in body.territories])))
        m.event(c, u, 'listing.updated', 'video', vid, {'listed': body.listed, 'price_usd': body.price_usd})
    return get_listing(vid, request)


@router.get('/api/videos/{vid}/listing')
def get_listing(vid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        r = c.execute('SELECT l.* FROM video_listings l JOIN videos v ON v.id=l.video_id WHERE l.video_id=? AND v.org_id=?', (vid, u['org_id'])).fetchone()
    if not r: return {'video_id': vid, 'listed': False}
    return {**dict(r), **{k: _lst(r[k]) for k in ('categories', 'excluded_categories', 'channels', 'territories')}, 'listed': bool(r['listed'])}


# ------------------------------------------------------------------ campaigns (brand)
class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    objective: str = Field(default='', max_length=500)
    product_id: Optional[int] = None
    category: str = Field(min_length=1, max_length=60)
    budget_usd: float = Field(default=0, ge=0, le=100_000_000)
    channels: list[str] = Field(default_factory=lambda: ['social'], min_length=1)
    territories: list[str] = Field(default_factory=lambda: ['WORLDWIDE'], min_length=1)
    brand_safety_exclusions: list[str] = Field(default_factory=list, max_length=50)
    starts_on: Optional[str] = None
    ends_on: Optional[str] = None


@router.post('/api/campaigns')
def create_campaign(body: CampaignIn, request: Request):
    u = m.current_user(request); m.require_role(u, 'brand')
    if not set(body.channels) <= CHANNELS: raise HTTPException(422, 'Unsupported channel')
    if not _territories_ok(body.territories): raise HTTPException(422, 'Territories must be WORLDWIDE or ISO codes')
    if body.starts_on and body.ends_on and body.ends_on < body.starts_on: raise HTTPException(422, 'Campaign must end after it starts')
    with m.db() as c:
        if body.product_id: m.owned(c, 'products', body.product_id, u['org_id'])
        cid = c.execute('INSERT INTO campaigns(org_id,name,objective,product_id,category,budget_usd,channels,territories,brand_safety_exclusions,starts_on,ends_on,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (u['org_id'], body.name.strip(), body.objective, body.product_id, body.category.strip().lower(), body.budget_usd, json.dumps(sorted(set(body.channels))),
                         json.dumps([t.upper() for t in body.territories]), json.dumps(sorted({x.strip().lower() for x in body.brand_safety_exclusions if x.strip()})),
                         body.starts_on, body.ends_on, u['id'])).lastrowid
        m.event(c, u, 'campaign.created', 'campaign', cid, {'name': body.name})
    return {'id': cid, 'status': 'ACTIVE'}


def _campaign_row(r):
    return {**dict(r), **{k: _lst(r[k]) for k in ('channels', 'territories', 'brand_safety_exclusions')}}


@router.get('/api/campaigns')
def list_campaigns(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        rows = c.execute('SELECT * FROM campaigns WHERE org_id=? ORDER BY id DESC', (u['org_id'],)).fetchall()
        out = []
        for r in rows:
            d = _campaign_row(r)
            d['committed_usd'] = c.execute("SELECT COALESCE(SUM(price_usd),0) FROM offers WHERE campaign_id=? AND status IN ('ACCEPTED','DELIVERED','COMPLETED')", (r['id'],)).fetchone()[0]
            d['offers'] = {x['status']: x['n'] for x in c.execute('SELECT status,COUNT(*) n FROM offers WHERE campaign_id=? GROUP BY status', (r['id'],)).fetchall()}
            out.append(d)
    return out


# ------------------------------------------------------------------ marketplace (brand)
def match(campaign, listing, analysed_frames):
    """Transparent rule-based fit between a campaign and a listing. Returns (score 0-100, reasons, blockers)."""
    reasons, blockers, score = [], [], 0
    cats, excl = set(_lst(listing['categories'])), set(_lst(listing['excluded_categories']))
    if campaign['category'] in excl: blockers.append(f"creator excludes the '{campaign['category']}' category")
    unsafe = set(_lst(campaign['brand_safety_exclusions'])) & cats
    if unsafe: blockers.append('brand-safety exclusion: ' + ', '.join(sorted(unsafe)))
    if campaign['category'] in cats: score += 40; reasons.append(f"content category matches '{campaign['category']}'")
    ch = set(_lst(campaign['channels'])); lch = set(_lst(listing['channels']))
    if ch <= lch: score += 20; reasons.append('all campaign channels are licensable')
    elif ch & lch: score += 10; reasons.append('some campaign channels are licensable: ' + ', '.join(sorted(ch & lch)))
    else: blockers.append('none of the campaign channels are licensable')
    lt, ct = set(_lst(listing['territories'])), set(_lst(campaign['territories']))
    if 'WORLDWIDE' in lt or ct <= lt: score += 20; reasons.append('territories covered')
    else: blockers.append('territories not covered: ' + ', '.join(sorted(ct - lt)))
    if analysed_frames: score += 20; reasons.append(f'{analysed_frames} analysed scenes available for preview')
    if campaign['budget_usd'] and listing['price_usd'] > campaign['budget_usd']: blockers.append('price exceeds the campaign budget')
    return score, reasons, blockers


@router.get('/api/marketplace')
def marketplace(request: Request, campaign_id: Optional[int] = None, q: str = '', category: str = '', max_price: Optional[float] = None, include_blocked: bool = False):
    """Curated, rights-cleared inventory from other organizations: metadata and keyframes only, never source footage."""
    u = m.current_user(request); m.require_role(u, 'brand')
    with m.db() as c:
        camp = c.execute('SELECT * FROM campaigns WHERE id=? AND org_id=?', (campaign_id, u['org_id'])).fetchone() if campaign_id else None
        if campaign_id and not camp: raise HTTPException(404, 'Campaign not found')
        rows = c.execute('SELECT l.*,v.title,v.duration,v.width,v.height,v.license_expires,v.license_confirmed,o.name creator_org FROM video_listings l JOIN videos v ON v.id=l.video_id '
                         'JOIN organizations o ON o.id=v.org_id WHERE l.listed=1 AND v.org_id!=? ORDER BY l.updated_at DESC', (u['org_id'],)).fetchall()
        out = []
        for r in rows:
            if not m._license_ok(r): continue
            text = f"{r['title']} {r['description']} {' '.join(_lst(r['categories']))}".lower()
            if q and q.lower() not in text: continue
            if category and category.lower() not in _lst(r['categories']): continue
            if max_price is not None and r['price_usd'] > max_price: continue
            frames = [f['second'] for f in c.execute('SELECT second FROM frames WHERE video_id=? ORDER BY second', (r['video_id'],)).fetchall()]
            item = {'video_id': r['video_id'], 'title': r['title'], 'creator_org': r['creator_org'], 'duration': r['duration'], 'resolution': [r['width'], r['height']],
                    'description': r['description'], 'categories': _lst(r['categories']), 'excluded_categories': _lst(r['excluded_categories']),
                    'price_usd': r['price_usd'], 'channels': _lst(r['channels']), 'territories': _lst(r['territories']),
                    'preview_frames': [f'/api/marketplace/{r["video_id"]}/frames/{int(s * 1000)}' for s in frames[:4]],
                    'licensed_to_you': bool(active_grant(c, r['video_id'], u['org_id'])),
                    'feasibility': {'analysed_scenes': len(frames), 'note': 'Placement feasibility is decided per placement by the quality check after licensing.'}}
            if camp:
                score, reasons, blockers = match(camp, r, len(frames))
                item.update(match_score=score, match_reasons=reasons, blockers=blockers)
                if blockers and not include_blocked: continue
            out.append(item)
    if camp: out.sort(key=lambda x: -x['match_score'])
    return out


@router.get('/api/marketplace/{vid}/frames/{ms}')
def marketplace_frame(vid: int, ms: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        listed = c.execute('SELECT 1 FROM video_listings WHERE video_id=? AND listed=1', (vid,)).fetchone()
        r = c.execute('SELECT image_path FROM frames WHERE video_id=? AND second*1000>=? AND second*1000<? ORDER BY second LIMIT 1', (vid, ms, ms + 1)).fetchone()
    if not listed or not r: raise HTTPException(404, 'Frame not found')
    return m.storage.response(r['image_path'], media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=300'})


# ------------------------------------------------------------------ offers and grants
class OfferIn(BaseModel):
    campaign_id: int
    video_id: int
    channels: Optional[list[str]] = None
    deliverables: str = Field(default='', max_length=1000)
    message: str = Field(default='', max_length=1000)


def _offer_dict(r):
    return {**dict(r), 'channels': _lst(r['channels']), 'territories': _lst(r['territories'])}


@router.post('/api/offers')
def create_offer(body: OfferIn, request: Request):
    """Fixed-price offer at the listed price. Conflicting or excluded placements are refused."""
    u = m.current_user(request); m.require_role(u, 'brand')
    with m.db() as c:
        camp = c.execute("SELECT * FROM campaigns WHERE id=? AND org_id=? AND status='ACTIVE'", (body.campaign_id, u['org_id'])).fetchone()
        if not camp: raise HTTPException(404, 'Active campaign not found')
        lst = c.execute('SELECT l.*,v.org_id creator_org,v.license_confirmed,v.license_expires FROM video_listings l JOIN videos v ON v.id=l.video_id WHERE l.video_id=? AND l.listed=1', (body.video_id,)).fetchone()
        if not lst or lst['creator_org'] == u['org_id']: raise HTTPException(404, 'Listing not found')
        if not m._license_ok(lst): raise m._err(409, 'LICENSE_INVALID', 'The video licence is no longer valid')
        channels = sorted(set(body.channels or _lst(camp['channels'])))
        if not set(channels) <= set(_lst(lst['channels'])): raise m._err(409, 'CHANNEL_NOT_LICENSABLE', 'The creator does not license: ' + ', '.join(sorted(set(channels) - set(_lst(lst['channels'])))))
        score, reasons, blockers = match(camp, lst, 1)
        if blockers: raise m._err(409, 'LISTING_BLOCKED', '; '.join(blockers), blockers=blockers)
        clash = c.execute("SELECT id FROM offers WHERE video_id=? AND category=? AND brand_org_id!=? AND status IN ('ACCEPTED','DELIVERED','COMPLETED')", (body.video_id, camp['category'], u['org_id'])).fetchone()
        if clash: raise m._err(409, 'CATEGORY_CONFLICT', f"Another brand already holds a '{camp['category']}' placement on this video")
        if c.execute("SELECT id FROM offers WHERE video_id=? AND campaign_id=? AND status IN ('OFFERED','ACCEPTED','DELIVERED')", (body.video_id, camp['id'])).fetchone():
            raise m._err(409, 'DUPLICATE_OFFER', 'This campaign already has an open offer for this video')
        oid = c.execute('INSERT INTO offers(campaign_id,brand_org_id,video_id,creator_org_id,price_usd,category,channels,territories,deliverables,message,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (camp['id'], u['org_id'], body.video_id, lst['creator_org'], lst['price_usd'], camp['category'], json.dumps(channels), camp['territories'],
                         body.deliverables, body.message, u['id'])).lastrowid
        m.event(c, u, 'offer.created', 'offer', oid, {'video_id': body.video_id, 'price_usd': lst['price_usd'], 'match_score': score})
    return {'id': oid, 'status': 'OFFERED', 'price_usd': lst['price_usd'], 'match_score': score, 'match_reasons': reasons}


@router.get('/api/offers')
def list_offers(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        rows = c.execute('SELECT o.*,c.name campaign_name,v.title video_title,ob.name brand_org,oc.name creator_org FROM offers o JOIN campaigns c ON c.id=o.campaign_id JOIN videos v ON v.id=o.video_id '
                         'JOIN organizations ob ON ob.id=o.brand_org_id JOIN organizations oc ON oc.id=o.creator_org_id WHERE o.brand_org_id=? OR o.creator_org_id=? ORDER BY o.id DESC', (u['org_id'], u['org_id'])).fetchall()
    return [dict(_offer_dict(r), side='brand' if r['brand_org_id'] == u['org_id'] else 'creator') for r in rows]


def _my_offer(c, oid, u, side):
    col = 'brand_org_id' if side == 'brand' else 'creator_org_id'
    r = c.execute(f'SELECT * FROM offers WHERE id=? AND {col}=?', (oid, u['org_id'])).fetchone()
    if not r: raise HTTPException(404, 'Offer not found')
    return dict(r)


@router.post('/api/offers/{oid}/accept')
def accept_offer(oid: int, request: Request):
    u = m.current_user(request); m.require_role(u, 'creator')
    with m.db() as c:
        o = _my_offer(c, oid, u, 'creator')
        v = c.execute('SELECT * FROM videos WHERE id=?', (o['video_id'],)).fetchone()
        if v['created_by'] != u['id']: raise m._err(403, 'NOT_RIGHTS_HOLDER', 'Only the rights holder can accept offers for this video')
        if not m._license_ok(v): raise m._err(409, 'LICENSE_INVALID', 'The video licence is missing or expired')
        camp = c.execute('SELECT * FROM campaigns WHERE id=?', (o['campaign_id'],)).fetchone()
        clash = c.execute("SELECT id FROM offers WHERE video_id=? AND category=? AND brand_org_id!=? AND status IN ('ACCEPTED','DELIVERED','COMPLETED')", (o['video_id'], o['category'], o['brand_org_id'])).fetchone()
        if clash: raise m._err(409, 'CATEGORY_CONFLICT', 'You already accepted a competing offer in this category for this video')
        _offer_move(c, o, 'ACCEPTED', u)
        expires = min([x for x in (camp['ends_on'], v['license_expires']) if x], default=None)
        gid = c.execute('INSERT INTO video_grants(video_id,grantee_org_id,offer_id,channels,territories,expires_at) VALUES(?,?,?,?,?,?)',
                        (o['video_id'], o['brand_org_id'], oid, o['channels'], o['territories'], expires)).lastrowid
        c.execute('UPDATE offers SET grant_id=? WHERE id=?', (gid, oid))
        m.event(c, u, 'grant.created', 'video', o['video_id'], {'grant_id': gid, 'grantee_org_id': o['brand_org_id'], 'offer_id': oid, 'expires_at': expires})
    return {'id': oid, 'status': 'ACCEPTED', 'grant_id': gid, 'grant_expires_at': expires}


@router.post('/api/offers/{oid}/decline')
def decline_offer(oid: int, request: Request):
    u = m.current_user(request); m.require_role(u, 'creator')
    with m.db() as c: _offer_move(c, _my_offer(c, oid, u, 'creator'), 'DECLINED', u)
    return {'id': oid, 'status': 'DECLINED'}


@router.post('/api/offers/{oid}/withdraw')
def withdraw_offer(oid: int, request: Request):
    u = m.current_user(request); m.require_role(u, 'brand')
    with m.db() as c: _offer_move(c, _my_offer(c, oid, u, 'brand'), 'WITHDRAWN', u)
    return {'id': oid, 'status': 'WITHDRAWN'}


@router.post('/api/offers/{oid}/complete')
def complete_offer(oid: int, request: Request):
    """Brand confirms the delivered placement fulfils the deal. Records the fee as payable; no payment is processed."""
    u = m.current_user(request); m.require_role(u, 'brand')
    with m.db() as c: _offer_move(c, _my_offer(c, oid, u, 'brand'), 'COMPLETED', u)
    return {'id': oid, 'status': 'COMPLETED', 'payment': 'NOT_PROCESSED: no payment provider is integrated'}


@router.post('/api/offers/{oid}/revoke-grant')
def revoke_grant(oid: int, request: Request, reason: str = ''):
    """Creator takedown: ends the licence and revokes every placement the brand made on this video."""
    u = m.current_user(request); m.require_role(u, 'creator')
    if len(reason.strip()) < 3: raise HTTPException(422, 'Provide a reason')
    with m.db() as c:
        o = _my_offer(c, oid, u, 'creator')
        v = c.execute('SELECT * FROM videos WHERE id=?', (o['video_id'],)).fetchone()
        if v['created_by'] != u['id']: raise m._err(403, 'NOT_RIGHTS_HOLDER', 'Only the rights holder can revoke this licence')
        if not o['grant_id']: raise m._err(409, 'NO_GRANT', 'This offer has no licence to revoke')
        c.execute('UPDATE video_grants SET revoked_at=CURRENT_TIMESTAMP,revocation_reason=? WHERE id=?', (reason[:500], o['grant_id']))
        if o['status'] in ('ACCEPTED', 'DELIVERED'): _offer_move(c, o, 'REVOKED', u, reason[:500])
        revoked = 0
        for p in c.execute('SELECT * FROM placements WHERE video_id=? AND org_id=?', (o['video_id'], o['brand_org_id'])).fetchall():
            p = dict(p)
            c.execute('UPDATE placement_rights SET revoked_at=CURRENT_TIMESTAMP,revocation_reason=? WHERE placement_id=?', ('Licence revoked: ' + reason[:400], p['id']))
            c.execute("UPDATE jobs SET status=CASE WHEN status='QUEUED' THEN 'CANCELLED' ELSE status END,cancel_requested=1,error='Licence revoked' WHERE placement_id=? AND status IN ('QUEUED','RUNNING')", (p['id'],))
            c.execute('UPDATE tracked_links SET active=0 WHERE placement_id=?', (p['id'],))
            if 'REVOKED' in m.lifecycle.TRANSITIONS.get(p['status'], ()):
                m.lifecycle.transition(c, p, 'REVOKED', u['id'], 'Licence revoked by creator'); revoked += 1
        m.event(c, u, 'grant.revoked', 'video', o['video_id'], {'grant_id': o['grant_id'], 'placements_revoked': revoked})
    return {'offer_id': oid, 'grant_revoked': True, 'placements_revoked': revoked}


@router.get('/api/earnings')
def earnings(request: Request):
    u = m.current_user(request); m.require_role(u, 'creator')
    with m.db() as c:
        rows = {r['status']: (r['n'], r['total']) for r in c.execute('SELECT status,COUNT(*) n,COALESCE(SUM(price_usd),0) total FROM offers WHERE creator_org_id=? GROUP BY status', (u['org_id'],)).fetchall()}
    get = lambda s: {'count': rows.get(s, (0, 0))[0], 'usd': rows.get(s, (0, 0))[1]}
    return {'agreed_pending_delivery': get('ACCEPTED'), 'delivered_awaiting_confirmation': get('DELIVERED'), 'completed_payable': get('COMPLETED'),
            'payments': 'NOT_PROCESSED: amounts are agreed-fee records; no payment provider is integrated'}


# ------------------------------------------------------------------ reports
@router.get('/api/campaigns/{cid}/report')
def campaign_report(cid: int, request: Request, format: str = 'json'):
    """Brand report separating measured facts from unavailable metrics."""
    u = m.current_user(request); m.require_role(u, 'brand')
    with m.db() as c:
        camp = c.execute('SELECT * FROM campaigns WHERE id=? AND org_id=?', (cid, u['org_id'])).fetchone()
        if not camp: raise HTTPException(404, 'Campaign not found')
        lines = []
        for o in c.execute('SELECT o.*,v.title FROM offers o JOIN videos v ON v.id=o.video_id WHERE o.campaign_id=? ORDER BY o.id', (cid,)).fetchall():
            pls = c.execute('SELECT * FROM placements WHERE video_id=? AND org_id=?', (o['video_id'], u['org_id'])).fetchall()
            for p in pls or [None]:
                job = c.execute("SELECT * FROM jobs WHERE placement_id=? AND placement_version=? AND status='SUCCEEDED' ORDER BY id DESC LIMIT 1", (p['id'], p['version'])).fetchone() if p else None
                val = json.loads(job['validation_json']) if job and job['validation_json'] else {}
                exp = val.get('exposure') or {}
                clicks = c.execute("SELECT COUNT(*) FROM events e JOIN tracked_links t ON t.id=e.entity_id WHERE e.event_type='campaign.click' AND t.placement_id=? AND e.org_id=?", (p['id'], u['org_id'])).fetchone()[0] if p else 0
                conv = c.execute('SELECT COUNT(*) n,COALESCE(SUM(value),0) v FROM conversions x JOIN tracked_links t ON t.id=x.link_id WHERE t.placement_id=? AND x.org_id=?', (p['id'], u['org_id'])).fetchone() if p else None
                lines.append({'offer_id': o['id'], 'video': o['title'], 'offer_status': o['status'], 'price_usd': o['price_usd'],
                              'placement_id': p['id'] if p else None, 'placement_version': p['version'] if p else None, 'placement_status': p['status'] if p else None,
                              'render_validated': val.get('status') == 'PASSED', 'exposure_seconds': exp.get('seconds'), 'mean_screen_area_pct': exp.get('mean_area_pct'),
                              'tracked_clicks': clicks, 'link_attributed_conversions': conv['n'] if conv else 0, 'link_attributed_value': conv['v'] if conv else 0})
    report = {'campaign': _campaign_row(camp), 'lines': lines,
              'totals': {'committed_usd': sum(x['price_usd'] for x in lines if x['offer_status'] in ('ACCEPTED', 'DELIVERED', 'COMPLETED')),
                         'delivered_placements': sum(1 for x in lines if x['placement_status'] == 'DELIVERED'),
                         'exposure_seconds': round(sum(x['exposure_seconds'] or 0 for x in lines), 2),
                         'tracked_clicks': sum(x['tracked_clicks'] for x in lines), 'link_attributed_conversions': sum(x['link_attributed_conversions'] for x in lines)},
              'methodology': {'measured': ['offer and placement status', 'render validation', 'exposure seconds: frames where the rendered product covers >= 0.1% of the frame, from the validated render geometry',
                                           'clicks on tracked links', 'conversions imported with a tracked-link reference'],
                              'not_measured': ['viewer impressions or reach (no platform analytics integration)', 'viewability', 'incremental or causal lift (no holdout experiment)'],
                              'note': 'Exposure is on-screen time in the delivered asset, not audience exposure.'}}
    if format == 'csv':
        buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=list(lines[0].keys()) if lines else ['offer_id'])
        w.writeheader(); [w.writerow(x) for x in lines]
        return Response(buf.getvalue(), media_type='text/csv', headers={'Content-Disposition': f'attachment; filename=campaign-{cid}-report.csv'})
    return report
