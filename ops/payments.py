"""Supervised-pilot payment ledger (operator only). Nothing here moves money: record what happened outside the app.

    python -m ops.payments list [--status INVOICED]
    python -m ops.payments record --offer 12 --kind INVOICE --amount 450 --reference INV-0007 --by "Ops: Priya"
    python -m ops.payments show --offer 12
Kinds: INVOICE, PAYMENT (brand paid), CREATOR_SETTLEMENT (creator paid out), REFUND, WAIVER, CANCEL.
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    ls = sub.add_parser('list'); ls.add_argument('--status', default='')
    rec = sub.add_parser('record')
    rec.add_argument('--offer', type=int, required=True); rec.add_argument('--kind', required=True); rec.add_argument('--amount', type=float, required=True)
    rec.add_argument('--reference', default=''); rec.add_argument('--note', default=''); rec.add_argument('--by', required=True)
    sh = sub.add_parser('show'); sh.add_argument('--offer', type=int, required=True)
    a = ap.parse_args()
    from app import main, payments
    with main.db() as c:
        if a.cmd == 'list':
            q = 'SELECT id,status,payment_status,price_usd,brand_org_id,creator_org_id,created_at FROM offers' + (' WHERE payment_status=?' if a.status else '') + ' ORDER BY id DESC'
            for r in c.execute(q, ((a.status.upper(),) if a.status else ())).fetchall(): print(json.dumps(dict(r)))
        elif a.cmd == 'show':
            o = c.execute('SELECT id,status,payment_status,price_usd FROM offers WHERE id=?', (a.offer,)).fetchone()
            if not o: sys.exit(f'offer {a.offer} not found')
            print(json.dumps({**dict(o), 'ledger': payments.ledger(c, a.offer)}, indent=2))
        else:
            try: print('payment_status =', payments.record(c, a.offer, a.kind, a.amount, a.reference, a.note, a.by))
            except payments.PaymentError as e: sys.exit(f'refused: {e}')
