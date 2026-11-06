"""Transactional outbox (AD-7, specification §11).

`emit()` writes an event in the caller's transaction, so the event exists exactly when the change that caused it was
committed. `deliver()` runs in the worker and the HTTP job runner: it leases pending events, oldest first, and passes
each one to the consumers registered for its type.

Guarantees:
- Versioned: every event type has a current version (`EVENT_TYPES`); its payload contract is in
  docs/architecture/EVENT_CONTRACTS.md.
- Traceable: every event has a unique `event_id`, its aggregate, organization and an optional trace id.
- Retryable: a failing consumer makes the event wait with exponential backoff; after `max_attempts` it is `DEAD`
  and is logged, for an operator to inspect and replay.
- Safe to process more than once: each consumer's handler runs in a transaction that also records
  (consumer, event_id) in `outbox_consumptions`. A handler that already succeeded is never run again for that event,
  even when the event is retried for another consumer or redelivered after a lost lease.
- Ordered per aggregate: an event is not delivered while an earlier event of the same aggregate is still `PENDING`.
  A `DEAD` event does not hold later ones back, so consumers must tolerate a gap.
"""
import json, secrets, time, uuid
from typing import Callable, Dict, List, Tuple

from . import obs

EVENT_TYPES = {
    'video.version.created': 1,
    'analysis.completed': 1,
    'analysis.failed': 1,
    'video.deleted': 1,
    'reservation.created': 1,
    'reservation.released': 1,
    'reservation.expired': 1,
    'campaign.status_changed': 1,
    'placement_request.created': 1,
    'placement_request.approved': 1,
    'placement_request.rejected': 1,
    'invoice.issued': 1,
    'invoice.paid': 1,
    'delivery.recorded': 1,
    'delivery.accepted': 1,
}
LEASE_SECONDS = 120
BATCH = 50

_CONSUMERS: Dict[str, List[Tuple[str, Callable]]] = {}


class UnknownEvent(ValueError):
    pass


def register(event_type, consumer, handler):
    """Register `handler(c, event)` under a stable consumer name. It runs inside a transaction; raising rolls it back and retries."""
    if event_type not in EVENT_TYPES: raise UnknownEvent(event_type)
    lst = _CONSUMERS.setdefault(event_type, [])
    if any(name == consumer for name, _ in lst): raise ValueError(f'Consumer {consumer} is already registered for {event_type}')
    lst.append((consumer, handler))


def unregister(consumer):
    for lst in _CONSUMERS.values(): lst[:] = [(n, h) for n, h in lst if n != consumer]


def emit(c, event_type, aggregate_type, aggregate_id, payload, org_id=None, trace_id=None, max_attempts=8):
    """Write an event in the caller's open transaction. Returns its event_id."""
    if event_type not in EVENT_TYPES: raise UnknownEvent(event_type)
    event_id = str(uuid.uuid4())
    c.execute('INSERT INTO outbox_events(event_id,event_type,event_version,org_id,aggregate_type,aggregate_id,payload_json,trace_id,max_attempts,available_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
              (event_id, event_type, EVENT_TYPES[event_type], org_id, aggregate_type, str(aggregate_id), json.dumps(payload, sort_keys=True, default=str),
               trace_id, max_attempts, time.time()))
    return event_id


def backoff(attempts):
    return min(3600.0, 5.0 * 2 ** max(0, attempts - 1))


def _claim(db, now, limit):
    """Lease up to `limit` deliverable events. The conditional UPDATE makes each claim exclusive across workers."""
    with db() as c:
        rows = c.execute(
            "SELECT e.id FROM outbox_events e WHERE e.status='PENDING' AND e.available_at<=? AND (e.lease_until IS NULL OR e.lease_until<?) "
            "AND NOT EXISTS (SELECT 1 FROM outbox_events p WHERE p.aggregate_type=e.aggregate_type AND p.aggregate_id=e.aggregate_id AND p.id<e.id AND p.status='PENDING') "
            "ORDER BY e.id LIMIT ?", (now, now, limit)).fetchall()
    claimed = []
    for r in rows:
        token = secrets.token_hex(8)
        with db() as c:
            if c.execute("UPDATE outbox_events SET lease_until=?, lease_token=? WHERE id=? AND status='PENDING' AND (lease_until IS NULL OR lease_until<?)",
                         (now + LEASE_SECONDS, token, r['id'], now)).rowcount == 1:
                claimed.append((r['id'], token))
    return claimed


def _event(row):
    return {'id': row['id'], 'event_id': row['event_id'], 'event_type': row['event_type'], 'event_version': row['event_version'], 'org_id': row['org_id'],
            'aggregate_type': row['aggregate_type'], 'aggregate_id': row['aggregate_id'], 'payload': json.loads(row['payload_json']),
            'trace_id': row['trace_id'], 'attempts': row['attempts'], 'created_at': row['created_at']}


def _deliver_one(db, event_pk, token):
    with db() as c:
        row = c.execute('SELECT * FROM outbox_events WHERE id=? AND lease_token=?', (event_pk, token)).fetchone()
    if not row: return 'lost'
    ev = _event(row); failure = None
    for consumer, handler in list(_CONSUMERS.get(ev['event_type'], [])):
        try:
            with db() as c:
                if c.execute('SELECT 1 FROM outbox_consumptions WHERE consumer=? AND event_id=?', (consumer, ev['event_id'])).fetchone(): continue
                handler(c, ev)
                c.execute('INSERT INTO outbox_consumptions(consumer,event_id) VALUES(?,?)', (consumer, ev['event_id']))
        except Exception as e:
            failure = f'{consumer}: {type(e).__name__}: {str(e)[:300]}'; break
    now = time.time()
    with db() as c:
        if failure is None:
            c.execute("UPDATE outbox_events SET status='DELIVERED', delivered_at=CURRENT_TIMESTAMP, lease_until=NULL, lease_token=NULL, last_error=NULL WHERE id=? AND lease_token=?", (event_pk, token))
            return 'delivered'
        attempts = row['attempts'] + 1
        dead = attempts >= row['max_attempts']
        c.execute('UPDATE outbox_events SET status=?, attempts=?, available_at=?, last_error=?, lease_until=NULL, lease_token=NULL WHERE id=? AND lease_token=?',
                  ('DEAD' if dead else 'PENDING', attempts, now + backoff(attempts), failure, event_pk, token))
    (obs.error if dead else obs.warn)('outbox event dead-lettered' if dead else 'outbox event will be retried',
                                      event_id=ev['event_id'], event_type=ev['event_type'], attempts=attempts, error=failure)
    return 'dead' if dead else 'retry'


def deliver(limit=BATCH, now=None, db=None):
    """Deliver what is due. Returns counts by outcome; an empty dict when nothing was due."""
    if db is None:
        from . import main
        db = main.db
    out = {}; budget = limit
    while budget > 0:                     # rounds: the next event of an aggregate becomes due once the previous is delivered
        claimed = _claim(db, time.time() if now is None else now, budget)
        if not claimed: break
        for pk, token in claimed:
            k = _deliver_one(db, pk, token); out[k] = out.get(k, 0) + 1
        budget -= len(claimed)
    return out


def pending(c, now=None):
    now = time.time() if now is None else now
    return c.execute("SELECT 1 FROM outbox_events WHERE status='PENDING' AND available_at<=? AND (lease_until IS NULL OR lease_until<?) LIMIT 1", (now, now)).fetchone() is not None
