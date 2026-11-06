"""Delivery records, evidence and campaign reporting (Phase 12).

When a render of a campaign placement passes validation, `on_render_succeeded` (called by the render worker in the same
transaction that marks the job SUCCEEDED) records a delivery with evidence:
  OUTPUT        SHA-256 and size of the rendered file
  VALIDATION    the frame-by-frame validation report of the render
  APPROVALS     the brand's and the publisher's approvals of the request, and the placement approvals of the version
  RIGHTS        the verified rights record and the placement rights of the version
  PROVENANCE    original video, video version, analysis version, scene, opportunity, product and asset version,
                placement request, placement version and render configuration, renderer (specification §5)
Each evidence item stores the SHA-256 of its content. The brand downloads the render through a short-lived signed link
that re-checks every delivery gate of the publisher's placement.

Reporting reads only persisted records: requests, reservations, deliveries, the ledger and `analytics_events`, which an
outbox consumer fills from domain events (each source event once, by its event id).
"""
import hashlib, json, time

from fastapi import APIRouter, HTTPException, Request

from . import main as m, outbox, storage
from .inventory import notify, transition_log

router = APIRouter()


def _sha(obj): return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def on_render_succeeded(c, job_id, output_path):
    j = c.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    r = c.execute("SELECT * FROM placement_requests WHERE placement_id=? AND status IN ('IN_PRODUCTION','DELIVERED')", (j['placement_id'],)).fetchone()
    if not r: return None
    p = c.execute('SELECT * FROM placements WHERE id=?', (j['placement_id'],)).fetchone()
    from .video_versions import sha256_file
    output_sha = sha256_file(storage.local(output_path))                # streamed: renders can be large
    did = c.execute('INSERT INTO delivery_records(placement_request_id,campaign_id,job_id,brand_org_id,publisher_org_id,output_sha256,output_bytes) VALUES(?,?,?,?,?,?,?)',
                    (r['id'], r['campaign_id'], job_id, r['brand_org_id'], r['publisher_org_id'], output_sha, j['output_bytes'] or 0)).lastrowid
    inv = c.execute('SELECT * FROM inventory_items WHERE id=?', (r['inventory_id'],)).fetchone()
    opp = c.execute('SELECT id, analysis_version_id, video_version_id, scene_id FROM opportunities WHERE id=?', (inv['opportunity_id'],)).fetchone()
    rights = c.execute("SELECT id, holder, status, expires_on, evidence_sha256 FROM rights_records WHERE video_id=? AND status='VERIFIED' ORDER BY id DESC LIMIT 1", (inv['video_id'],)).fetchone()
    evidence = {
        'OUTPUT': {'sha256': output_sha, 'bytes': j['output_bytes'], 'job_id': job_id},
        'VALIDATION': json.loads(j['validation_json'] or '{}'),
        'APPROVALS': {'brand': {'by': r['brand_approved_by'], 'at': r['brand_approved_at']}, 'publisher': {'by': r['publisher_approved_by'], 'at': r['publisher_approved_at']},
                      'placement': [dict(a) for a in c.execute('SELECT user_id, role, approved, content_hash, created_at FROM placement_approvals WHERE placement_id=? AND placement_version=?',
                                                               (p['id'], p['version'])).fetchall()]},
        'RIGHTS': {'rights_record': dict(rights) if rights else None,
                   'placement_rights': dict(c.execute('SELECT permitted_channels, territories, start_at, expires_at, revoked_at FROM placement_rights WHERE placement_id=?', (p['id'],)).fetchone() or {})},
        'PROVENANCE': {'video_id': inv['video_id'], 'video_version_id': opp['video_version_id'], 'analysis_version_id': opp['analysis_version_id'], 'scene_id': opp['scene_id'],
                       'opportunity_id': opp['id'], 'brand_product_id': r['product_id'], 'product_asset_version': r['product_asset_version'], 'publisher_product_id': r['shadow_product_id'],
                       'placement_request_id': r['id'], 'placement_id': p['id'], 'placement_version': p['version'], 'placement_content_hash': p['content_hash'],
                       'render_options': json.loads(p['render_options'] or '{}'), 'renderer': j['renderer'], 'rendering_engine': 'visuara-compositor'},
    }
    for kind, content in evidence.items():
        c.execute('INSERT INTO delivery_evidence(delivery_id,kind,content_json,sha256) VALUES(?,?,?,?)', (did, kind, json.dumps(content, default=str), _sha(content)))
    if r['status'] == 'IN_PRODUCTION':
        c.execute("UPDATE placement_requests SET status='DELIVERED', version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (r['id'],))
        transition_log(c, 'placement_request', r['id'], r['brand_org_id'], 'IN_PRODUCTION', 'DELIVERED', None, f'delivery {did}')
    outbox.emit(c, 'delivery.recorded', 'placement_request', r['id'], {'delivery_id': did, 'placement_request_id': r['id'], 'campaign_id': r['campaign_id'], 'job_id': job_id,
                                                                       'output_sha256': output_sha}, org_id=r['brand_org_id'])
    notify(c, r['brand_org_id'], 'delivery.recorded', 'Your placement was delivered', f'Request {r["id"]}: review and accept it', 'placement_request', r['id'])
    return did


def _delivery(c, did, org_id):
    d = c.execute('SELECT * FROM delivery_records WHERE id=? AND (brand_org_id=? OR publisher_org_id=?)', (did, org_id, org_id)).fetchone()
    if not d: raise HTTPException(404, 'Resource not found')
    return dict(d)


@router.get('/api/deliveries/{did}')
def get_delivery(did: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        d = _delivery(c, did, u['org_id'])
        d['evidence'] = [dict(e, content=json.loads(e['content_json'])) for e in c.execute('SELECT id, kind, content_json, sha256, created_at FROM delivery_evidence WHERE delivery_id=? ORDER BY id',
                                                                                             (did,)).fetchall()]
        for e in d['evidence']: e.pop('content_json')
    return d


@router.post('/api/deliveries/{did}/download-link')
def delivery_link(did: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        d = _delivery(c, did, u['org_id'])
        if d['brand_org_id'] != u['org_id']: raise HTTPException(403, {'code': 'WRONG_PARTY', 'message': 'The publisher downloads through its own render job'})
        if d['status'] == 'REVOKED': raise HTTPException(403, {'code': 'DELIVERY_REVOKED', 'message': 'This delivery was revoked'})
    _gate(d)
    exp = int(time.time()) + m.LINK_TTL_SECONDS
    url = f'/api/delivery-media/{m._sign({"d": did, "u": u["id"], "o": u["org_id"], "exp": exp})}'
    return {'url': url, 'expires_at': exp}


def _gate(d):
    """Every publisher-side delivery gate (current version, validation, rights, status) via the existing check."""
    return m._authorize_delivery(d['job_id'], {'id': 0, 'org_id': d['publisher_org_id']}, record=False)


@router.get('/api/delivery-media/{token}')
def delivery_media(token: str):
    data = m._unsign(token)
    if not isinstance(data, dict) or set(data) != {'d', 'u', 'o', 'exp'}: raise HTTPException(403, 'Invalid download link')
    if data['exp'] < time.time(): raise HTTPException(410, {'code': 'LINK_EXPIRED', 'message': 'This download link has expired; request a new one'})
    with m.db() as c:
        ms = c.execute("SELECT member_role FROM memberships WHERE user_id=? AND org_id=? AND status='ACTIVE'", (data['u'], data['o'])).fetchone()
        from .members import RANK
        if not ms or RANK.get(ms['member_role'], 0) < RANK['manager']: raise HTTPException(403, 'Invalid download link')      # as when it was issued
        d = c.execute('SELECT * FROM delivery_records WHERE id=? AND brand_org_id=?', (data['d'], data['o'])).fetchone()
        if not d or d['status'] == 'REVOKED': raise HTTPException(403, 'Invalid download link')
        d = dict(d)
    path = _gate(d)
    with m.db() as c:
        c.execute("INSERT INTO events(org_id,user_id,event_type,entity_type,entity_id,details) VALUES(?,?,?,?,?,?)", (d['brand_org_id'], data['u'], 'delivery.downloaded', 'delivery', d['id'], '{}'))
    return storage.response(path, media_type='video/mp4', filename=f'visuara-delivery-{d["id"]}.mp4')


# ---------------------------------------------------------------- analytics events (an outbox consumer) and reports
ANALYTICS_TYPES = {'reservation.created': 'reservation', 'placement_request.created': 'request', 'placement_request.approved': 'request', 'delivery.recorded': 'delivery',
                   'delivery.accepted': 'delivery', 'invoice.issued': 'invoice', 'invoice.paid': 'invoice'}


def record_analytics(c, ev):
    p = ev['payload']; campaign = p.get('campaign_id')
    if campaign is None and p.get('placement_request_id'):
        r = c.execute('SELECT campaign_id FROM placement_requests WHERE id=?', (p['placement_request_id'],)).fetchone(); campaign = r['campaign_id'] if r else None
    c.execute('INSERT INTO analytics_events(org_id,campaign_id,event_type,entity_type,entity_id,value_minor,properties_json,source_event_id,occurred_at) VALUES(?,?,?,?,?,?,?,?,?) '
              'ON CONFLICT(source_event_id) DO NOTHING',
              (ev['org_id'] or 0, campaign, ev['event_type'], ANALYTICS_TYPES[ev['event_type']], int(ev['aggregate_id']), p.get('amount_minor') or p.get('price_minor'),
               json.dumps(p, sort_keys=True), ev['event_id'], ev['created_at']))


def register_consumers():
    for t in ANALYTICS_TYPES:
        try: outbox.register(t, 'analytics', record_analytics)
        except ValueError: pass


REPORT_DELIVERY_BATCH = 200


@router.get('/api/campaigns/{cid}/report')
def campaign_report(cid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        if not c.execute('SELECT 1 FROM campaigns WHERE id=? AND org_id=?', (cid, u['org_id'])).fetchone(): raise HTTPException(404, 'Resource not found')
    # Counts come from analytics events, which the outbox delivers. In HTTP mode that waits for the next runner request,
    # so deliver what is due first (bounded, leased, so a concurrent runner is safe): the report then reflects every change.
    from . import outbox
    outbox.deliver(limit=REPORT_DELIVERY_BATCH)
    with m.db() as c:
        camp = c.execute('SELECT * FROM campaigns WHERE id=? AND org_id=?', (cid, u['org_id'])).fetchone()
        if not camp: raise HTTPException(404, 'Resource not found')
        req = {r['status']: r['n'] for r in c.execute('SELECT status, COUNT(*) AS n FROM placement_requests WHERE campaign_id=? GROUP BY status', (cid,)).fetchall()}
        committed = c.execute("SELECT COALESCE(SUM(res.price_minor),0) FROM placement_requests p JOIN reservations res ON res.id=p.reservation_id WHERE p.campaign_id=? "
                              "AND p.status IN ('APPROVED','IN_PRODUCTION','DELIVERED','ACCEPTED')", (cid,)).fetchone()[0]
        inv = c.execute("SELECT COALESCE(SUM(i.amount_minor),0) AS invoiced, COALESCE(SUM(CASE WHEN i.status='PAID' THEN i.amount_minor ELSE 0 END),0) AS paid FROM invoices i "
                        "JOIN quotes q ON q.id=i.quote_id JOIN placement_requests p ON p.id=q.placement_request_id WHERE p.campaign_id=?", (cid,)).fetchone()
        deliveries = [dict(d) for d in c.execute('SELECT id, placement_request_id, status, output_sha256, delivered_at, accepted_at FROM delivery_records WHERE campaign_id=? ORDER BY id', (cid,)).fetchall()]
        events = {r['event_type']: r['n'] for r in c.execute('SELECT event_type, COUNT(*) AS n FROM analytics_events WHERE campaign_id=? GROUP BY event_type', (cid,)).fetchall()}
        visible = c.execute("SELECT COALESCE(SUM(o.visible_seconds),0) FROM delivery_records d JOIN placement_requests p ON p.id=d.placement_request_id JOIN inventory_items i ON i.id=p.inventory_id "
                            "JOIN opportunities o ON o.id=i.opportunity_id WHERE d.campaign_id=? AND d.status IN ('DELIVERED','ACCEPTED')", (cid,)).fetchone()[0]
    return {'campaign_id': cid, 'name': camp['name'], 'status': camp['status'], 'currency': camp['currency'], 'budget_minor': camp['budget_minor'],
            'requests_by_status': req, 'committed_minor': committed, 'invoiced_minor': inv['invoiced'], 'paid_minor': inv['paid'],
            'deliveries': deliveries, 'delivered_visible_seconds': round(visible, 2), 'events': events,
            'note': 'Built from persisted records only. Audience reach and views are not measured: no delivery platform reports them back yet.'}
