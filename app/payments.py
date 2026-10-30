"""Supervised-pilot payments: a ledger of invoices, payments and creator settlements recorded by the platform operator.

No card or bank processing happens in the application. The operator invoices the brand outside the platform (e.g.
from their accounting tool), confirms the money arrived, pays the creator, and records each step here with
`python -m ops.payments` or the operator API (bearer CC_OPS_TOKEN). A deal is never marked paid by a browser.
Brands and creators see the resulting status on their offers and earnings.

Operator endpoints (all 404 unless CC_OPS_TOKEN is set): GET /api/ops/metrics, GET/POST /api/ops/payments.
"""
import hmac, os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import main as m, obs

router = APIRouter()
STATES = ('NOT_INVOICED', 'INVOICED', 'PAID', 'SETTLED', 'REFUNDED', 'WAIVED', 'CANCELLED')
KINDS = {'INVOICE': 'INVOICED', 'PAYMENT': 'PAID', 'CREATOR_SETTLEMENT': 'SETTLED', 'REFUND': 'REFUNDED', 'WAIVER': 'WAIVED', 'CANCEL': 'CANCELLED'}
TRANSITIONS = {'NOT_INVOICED': {'INVOICED', 'WAIVED'}, 'INVOICED': {'PAID', 'CANCELLED', 'WAIVED'}, 'PAID': {'SETTLED', 'REFUNDED'},
               'SETTLED': {'REFUNDED'}, 'REFUNDED': set(), 'WAIVED': set(), 'CANCELLED': {'INVOICED'}}
BILLABLE_OFFERS = ('ACCEPTED', 'DELIVERED', 'COMPLETED')


class PaymentError(ValueError):
    pass


def record(c, offer_id, kind, amount_usd, reference, note, recorded_by):
    """Append one ledger entry and move the offer's payment status. Raises PaymentError on an invalid step."""
    kind = kind.upper()
    if kind not in KINDS: raise PaymentError(f'kind must be one of {", ".join(KINDS)}')
    if amount_usd < 0: raise PaymentError('amount must not be negative')
    c.lock(f'payment-offer-{offer_id}')
    o = c.execute('SELECT * FROM offers WHERE id=?', (offer_id,)).fetchone()
    if not o: raise PaymentError(f'offer {offer_id} not found')
    current, target = o['payment_status'] or 'NOT_INVOICED', KINDS[kind]
    if target not in TRANSITIONS.get(current, set()): raise PaymentError(f'cannot record {kind} while the offer is {current}')
    if kind == 'INVOICE' and o['status'] not in BILLABLE_OFFERS: raise PaymentError(f'offer is {o["status"]}; only accepted offers can be invoiced')
    if kind in ('INVOICE', 'PAYMENT') and abs(amount_usd - float(o['price_usd'])) > 0.005 and not note:
        raise PaymentError(f'amount differs from the agreed price ${o["price_usd"]:.2f}; add a note explaining why')
    c.execute('INSERT INTO payment_records(offer_id,kind,amount_usd,reference,note,recorded_by) VALUES(?,?,?,?,?,?)',
              (offer_id, kind, round(amount_usd, 2), reference[:200], note[:500], recorded_by[:120]))
    c.execute('UPDATE offers SET payment_status=? WHERE id=?', (target, offer_id))
    for org in {o['brand_org_id'], o['creator_org_id']}:
        c.execute('INSERT INTO events(org_id,user_id,event_type,entity_type,entity_id,details) VALUES(?,?,?,?,?,?)',
                  (org, 0, 'payment.' + target.lower(), 'offer', offer_id, '{"recorded_by":"operator"}'))
    return target


def ledger(c, offer_id):
    return [dict(r) for r in c.execute('SELECT kind,amount_usd,reference,note,created_at FROM payment_records WHERE offer_id=? ORDER BY id', (offer_id,)).fetchall()]


@router.get('/api/offers/{oid}/payments')
def offer_payments(oid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        o = c.execute('SELECT id,payment_status,price_usd FROM offers WHERE id=? AND (brand_org_id=? OR creator_org_id=?)', (oid, u['org_id'], u['org_id'])).fetchone()
        if not o: raise HTTPException(404, 'Offer not found')
        return {'offer_id': oid, 'payment_status': o['payment_status'], 'agreed_price_usd': o['price_usd'], 'ledger': ledger(c, oid),
                'processing': 'supervised_pilot_manual_invoicing'}


# ---------------------------------------------------------------- operator API
def _operator(request):
    token = os.getenv('CC_OPS_TOKEN', '')
    supplied = request.headers.get('authorization', '')
    if not token or len(token) < 24: raise HTTPException(404, 'Not found')
    if not hmac.compare_digest(supplied, 'Bearer ' + token): raise HTTPException(401, 'Operator token required')


@router.get('/api/ops/metrics')
def ops_metrics(request: Request):
    _operator(request)
    with m.db() as c: return obs.metrics(c)


class PaymentIn(BaseModel):
    offer_id: int
    kind: str
    amount_usd: float = Field(ge=0, le=10_000_000)
    reference: str = Field(default='', max_length=200)
    note: str = Field(default='', max_length=500)
    recorded_by: str = Field(min_length=1, max_length=120)


@router.post('/api/ops/payments')
def ops_record_payment(body: PaymentIn, request: Request):
    _operator(request)
    try:
        with m.db() as c: status = record(c, body.offer_id, body.kind, body.amount_usd, body.reference, body.note, body.recorded_by)
    except PaymentError as e: raise HTTPException(409, str(e))
    obs.info('payment recorded', offer=body.offer_id, kind=body.kind.upper(), status=status)
    return {'offer_id': body.offer_id, 'payment_status': status}


@router.get('/api/ops/payments')
def ops_list_payments(request: Request, status: str = ''):
    _operator(request)
    with m.db() as c:
        q = 'SELECT o.id offer_id,o.status offer_status,o.payment_status,o.price_usd,b.name brand,cr.name creator,o.created_at FROM offers o JOIN organizations b ON b.id=o.brand_org_id JOIN organizations cr ON cr.id=o.creator_org_id'
        rows = c.execute(q + (' WHERE o.payment_status=?' if status else '') + ' ORDER BY o.id DESC LIMIT 500', ((status.upper(),) if status else ())).fetchall()
    return [dict(r) for r in rows]
