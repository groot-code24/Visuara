"""Quotes, invoices, the append-only ledger and revenue allocation (Phase 11, AD-6, AD-10).

Money is integer minor units with an ISO currency code. A brand pays an invoice through the configured payment provider
(`app/payments.py`, applied by a verified webhook), or by bank transfer, recorded by a platform administrator with its
reference. Both post the same ledger transaction (`payments.record_payment`). Every money movement is one double-entry
ledger transaction whose debits equal its credits; the database refuses UPDATE and DELETE on `ledger_entries`.

Flow and ledger transactions:
  publisher approves a request   -> quote ISSUED for the reserved price, platform fee in basis points
  brand accepts the quote        -> invoice ISSUED;  DEBIT receivable:brand   / CREDIT payable:publisher + revenue:platform
  provider webhook, or admin     -> invoice PAID;    DEBIT cash:platform      / CREDIT receivable:brand; publisher share PAYABLE
  admin records the payout       -> allocation PAID; DEBIT payable:publisher  / CREDIT cash:platform
The fee is floor(amount x bps / 10000) and the publisher gets the rest, so the parts always add up to the amount.
Endpoints that move money require an `Idempotency-Key` header; repeating a request with the same key returns the first
response, and reusing a key for a different request is refused.
"""
import hashlib, json, os, time, uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import main as m, outbox, db as dbm
from .inventory import notify
from .orgs import require_platform_admin

router = APIRouter()


def fee_bps(): return int(os.getenv('CC_PLATFORM_FEE_BPS', '2000'))
def _err(status, code, message, **extra): return HTTPException(status, {'code': code, 'message': message, **extra})


def split(amount, bps):
    fee = amount * bps // 10000
    return amount - fee, fee


def post_transaction(c, lines, reference_type, reference_id, memo, user_id):
    """Write one balanced transaction. lines: [(account, org_id, 'DEBIT'|'CREDIT', amount_minor, currency)]."""
    by_cur = {}
    for _, _, d, amt, cur in lines:
        if amt <= 0 or not isinstance(amt, int): raise ValueError('ledger amounts are positive integers')
        by_cur.setdefault(cur, 0); by_cur[cur] += amt if d == 'DEBIT' else -amt
    if any(by_cur.values()): raise ValueError(f'unbalanced transaction: {by_cur}')
    txn = str(uuid.uuid4())
    for account, org_id, d, amt, cur in lines:
        c.execute('INSERT INTO ledger_entries(txn_id,account,org_id,direction,amount_minor,currency,reference_type,reference_id,memo,created_by) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (txn, account, org_id, d, amt, cur, reference_type, reference_id, memo, user_id))
    return txn


def issue_quote(c, req_id):
    r = c.execute('SELECT p.*, res.price_minor, res.currency FROM placement_requests p JOIN reservations res ON res.id=p.reservation_id WHERE p.id=?', (req_id,)).fetchone()
    return c.execute("INSERT INTO quotes(placement_request_id,brand_org_id,publisher_org_id,amount_minor,currency,platform_fee_bps) VALUES(?,?,?,?,?,?)",
                     (req_id, r['brand_org_id'], r['publisher_org_id'], r['price_minor'], r['currency'], fee_bps())).lastrowid


# ---------------------------------------------------------------- idempotency
def _idempotent(request, org_id, endpoint, body, work):
    key = request.headers.get('idempotency-key', '').strip()
    if not key or len(key) > 200: raise _err(428, 'IDEMPOTENCY_KEY_REQUIRED', 'Send an Idempotency-Key header (any unique string, up to 200 characters)')
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    for _ in range(2):
        try:
            with m.db() as c:
                prev = c.execute('SELECT * FROM idempotency_keys WHERE org_id=? AND endpoint=? AND idem_key=?', (org_id, endpoint, key)).fetchone()
                if prev:
                    if prev['request_hash'] != digest: raise _err(422, 'IDEMPOTENCY_KEY_REUSED', 'This Idempotency-Key was used for a different request')
                    return json.loads(prev['response_json'])
                c.execute('INSERT INTO idempotency_keys(org_id,endpoint,idem_key,request_hash) VALUES(?,?,?,?)', (org_id, endpoint, key, digest))
                result = work(c)
                c.execute('UPDATE idempotency_keys SET status_code=200, response_json=? WHERE org_id=? AND endpoint=? AND idem_key=?', (json.dumps(result, default=str), org_id, endpoint, key))
                return result
        except dbm.IntegrityError:
            continue                    # a concurrent request with the same key committed first: return its response
    raise _err(409, 'IDEMPOTENCY_CONFLICT', 'A request with this key is being processed')


# ---------------------------------------------------------------- quotes and invoices
def _quote_view(r): return dict(r)


@router.get('/api/quotes')
def list_quotes(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        return [_quote_view(r) for r in c.execute('SELECT * FROM quotes WHERE brand_org_id=? OR publisher_org_id=? ORDER BY id DESC', (u['org_id'], u['org_id'])).fetchall()]


def _next_invoice_number(c):
    year = time.strftime('%Y')
    n = c.execute('SELECT COUNT(*) FROM invoices WHERE number LIKE ?', (f'VIS-{year}-%',)).fetchone()[0] + 1
    return f'VIS-{year}-{n:06d}'


@router.post('/api/quotes/{qid}/accept')
def accept_quote(qid: int, request: Request):
    u = m.current_user(request)

    def work(c):
        q = c.execute('SELECT * FROM quotes WHERE id=? AND brand_org_id=?', (qid, u['org_id'])).fetchone()
        if not q: raise HTTPException(404, 'Resource not found')
        if c.execute("UPDATE quotes SET status='ACCEPTED', accepted_at=CURRENT_TIMESTAMP, accepted_by=? WHERE id=? AND status='ISSUED'", (u['id'], qid)).rowcount != 1:
            raise _err(409, 'INVALID_TRANSITION', f'A {q["status"]} quote cannot be accepted')
        c.lock('invoice-number')
        number = _next_invoice_number(c)
        due = time.strftime('%Y-%m-%d', time.gmtime(time.time() + 30 * 86400))
        inv = c.execute('INSERT INTO invoices(number,quote_id,brand_org_id,publisher_org_id,amount_minor,currency,due_on,created_by) VALUES(?,?,?,?,?,?,?,?)',
                        (number, qid, q['brand_org_id'], q['publisher_org_id'], q['amount_minor'], q['currency'], due, u['id'])).lastrowid
        pub, fee = split(q['amount_minor'], q['platform_fee_bps'])
        lines = [(f'receivable:brand:{q["brand_org_id"]}', q['brand_org_id'], 'DEBIT', q['amount_minor'], q['currency'])]
        if pub: lines.append((f'payable:publisher:{q["publisher_org_id"]}', q['publisher_org_id'], 'CREDIT', pub, q['currency']))
        if fee: lines.append(('revenue:platform', None, 'CREDIT', fee, q['currency']))
        txn = post_transaction(c, lines, 'invoice', inv, f'Invoice {number} issued', u['id'])
        c.execute("INSERT INTO revenue_allocations(invoice_id,org_id,beneficiary,amount_minor,currency) VALUES(?,?,'PUBLISHER',?,?)", (inv, q['publisher_org_id'], pub, q['currency']))
        c.execute("INSERT INTO revenue_allocations(invoice_id,org_id,beneficiary,amount_minor,currency) VALUES(?,NULL,'PLATFORM',?,?)", (inv, fee, q['currency']))
        outbox.emit(c, 'invoice.issued', 'invoice', inv, {'invoice_id': inv, 'number': number, 'amount_minor': q['amount_minor'], 'currency': q['currency'],
                                                          'placement_request_id': q['placement_request_id']}, org_id=q['brand_org_id'])
        notify(c, q['publisher_org_id'], 'invoice.issued', f'Invoice {number} was issued to the brand', '', 'invoice', inv)
        m.event(c, u, 'invoice.issued', 'invoice', inv, {'number': number, 'txn_id': txn})
        return {'invoice_id': inv, 'number': number, 'amount_minor': q['amount_minor'], 'currency': q['currency'], 'due_on': due, 'publisher_share_minor': pub, 'platform_fee_minor': fee}
    return _idempotent(request, u['org_id'], f'quote-accept:{qid}', {'quote': qid}, work)


@router.get('/api/invoices')
def list_invoices(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        return [dict(r) for r in c.execute('SELECT * FROM invoices WHERE brand_org_id=? OR publisher_org_id=? ORDER BY id DESC', (u['org_id'], u['org_id'])).fetchall()]


@router.get('/api/invoices/{iid}')
def get_invoice(iid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        r = c.execute('SELECT * FROM invoices WHERE id=? AND (brand_org_id=? OR publisher_org_id=?)', (iid, u['org_id'], u['org_id'])).fetchone()
        if not r: raise HTTPException(404, 'Resource not found')
        d = dict(r)
        d['allocations'] = [dict(a) for a in c.execute('SELECT beneficiary, org_id, amount_minor, currency, status, paid_at FROM revenue_allocations WHERE invoice_id=?', (iid,)).fetchall()
                            if a['beneficiary'] == 'PUBLISHER' or u['platform_admin']]
    return d


class PaymentIn(BaseModel):
    reference: str = Field(min_length=3, max_length=120)


@router.post('/api/admin/invoices/{iid}/mark-paid')
def mark_paid(iid: int, body: PaymentIn, request: Request):
    u = m.current_user(request); require_platform_admin(u)

    def work(c):
        from .payments import record_payment
        inv = c.execute('SELECT * FROM invoices WHERE id=?', (iid,)).fetchone()
        if not inv: raise HTTPException(404, 'Resource not found')
        txn = record_payment(c, inv, body.reference, u['id'], 'bank transfer')
        if txn is None: raise _err(409, 'INVALID_TRANSITION', f'A {inv["status"]} invoice cannot be marked paid')
        return {'invoice_id': iid, 'status': 'PAID', 'txn_id': txn}
    return _idempotent(request, u['org_id'], f'invoice-paid:{iid}', body.model_dump(), work)


class PayoutIn(BaseModel):
    reference: str = Field(min_length=3, max_length=120)


@router.post('/api/admin/allocations/{aid}/payout')
def payout(aid: int, body: PayoutIn, request: Request):
    u = m.current_user(request); require_platform_admin(u)

    def work(c):
        a = c.execute("SELECT * FROM revenue_allocations WHERE id=? AND beneficiary='PUBLISHER'", (aid,)).fetchone()
        if not a: raise HTTPException(404, 'Resource not found')
        if c.execute("UPDATE revenue_allocations SET status='PAID', paid_at=CURRENT_TIMESTAMP, payout_reference=? WHERE id=? AND status='PAYABLE'", (body.reference, aid)).rowcount != 1:
            raise _err(409, 'INVALID_TRANSITION', f'A {a["status"]} allocation cannot be paid out')
        txn = None
        if a['amount_minor'] > 0:
            txn = post_transaction(c, [(f'payable:publisher:{a["org_id"]}', a['org_id'], 'DEBIT', a['amount_minor'], a['currency']),
                                       ('cash:platform', None, 'CREDIT', a['amount_minor'], a['currency'])], 'payout', aid, f'Payout {body.reference}', u['id'])
        notify(c, a['org_id'], 'payout.paid', 'A payout was sent', body.reference, 'revenue_allocation', aid)
        return {'allocation_id': aid, 'status': 'PAID', 'txn_id': txn}
    return _idempotent(request, u['org_id'], f'payout:{aid}', body.model_dump(), work)


@router.get('/api/ledger')
def my_ledger(request: Request):
    """This organization's ledger lines (platform accounts are not shown)."""
    u = m.current_user(request)
    with m.db() as c:
        rows = c.execute('SELECT id, txn_id, account, direction, amount_minor, currency, reference_type, reference_id, memo, created_at FROM ledger_entries WHERE org_id=? ORDER BY id',
                         (u['org_id'],)).fetchall()
    return [dict(r) for r in rows]


@router.get('/api/earnings')
def earnings(request: Request):
    u = m.current_user(request)
    with m.db() as c:
        rows = c.execute("SELECT a.*, i.number FROM revenue_allocations a JOIN invoices i ON i.id=a.invoice_id WHERE a.org_id=? AND a.beneficiary='PUBLISHER' ORDER BY a.id DESC",
                         (u['org_id'],)).fetchall()
    totals = {}
    for r in rows: totals.setdefault(r['currency'], {}).setdefault(r['status'], 0); totals[r['currency']][r['status']] += r['amount_minor']
    return {'allocations': [dict(r) for r in rows], 'totals': totals}


def trial_balance(c):
    accounts = {}
    for r in c.execute('SELECT account, currency, direction, SUM(amount_minor) AS total FROM ledger_entries GROUP BY account, currency, direction').fetchall():
        a = accounts.setdefault((r['account'], r['currency']), {'debit': 0, 'credit': 0})
        a['debit' if r['direction'] == 'DEBIT' else 'credit'] += r['total']
    unbalanced = [dict(r) for r in c.execute("SELECT txn_id, currency, SUM(CASE WHEN direction='DEBIT' THEN amount_minor ELSE -amount_minor END) AS net FROM ledger_entries "
                                             "GROUP BY txn_id, currency HAVING SUM(CASE WHEN direction='DEBIT' THEN amount_minor ELSE -amount_minor END)<>0").fetchall()]
    return {'accounts': [{'account': k[0], 'currency': k[1], **v, 'balance': v['debit'] - v['credit']} for k, v in sorted(accounts.items())], 'unbalanced_transactions': unbalanced,
            'balanced': not unbalanced}


@router.get('/api/admin/ledger/trial-balance')
def admin_trial_balance(request: Request):
    u = m.current_user(request); require_platform_admin(u)
    with m.db() as c: return trial_balance(c)


@router.get('/api/admin/invoices')
def admin_invoices(request: Request, status: str = 'ISSUED'):
    u = m.current_user(request); require_platform_admin(u)
    with m.db() as c:
        return [dict(r) for r in c.execute('SELECT i.*, b.name AS brand, p.name AS publisher FROM invoices i JOIN organizations b ON b.id=i.brand_org_id '
                                           'JOIN organizations p ON p.id=i.publisher_org_id WHERE i.status=? ORDER BY i.id', (status,)).fetchall()]


@router.get('/api/admin/allocations')
def admin_allocations(request: Request, status: str = 'PAYABLE'):
    u = m.current_user(request); require_platform_admin(u)
    with m.db() as c:
        return [dict(r) for r in c.execute("SELECT a.*, i.number, o.name AS publisher FROM revenue_allocations a JOIN invoices i ON i.id=a.invoice_id "
                                           "LEFT JOIN organizations o ON o.id=a.org_id WHERE a.beneficiary='PUBLISHER' AND a.status=? ORDER BY a.id", (status,)).fetchall()]
