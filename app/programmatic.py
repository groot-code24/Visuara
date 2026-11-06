"""Programmatic foundation (Phase 13, M17): versioned contracts, adapter interfaces, capability discovery, test adapters.

Nothing here is connected to an exchange, an SSP, a DSP or a measurement vendor. What exists:
  contracts      versioned JSON shapes a partner integration would exchange (`CONTRACTS`), checked by `validate()`; the
                 inventory feed is generated from canonical inventory and every item is checked against its contract
  adapters       one interface per partner kind (supply, demand, measurement); a partner integration implements it
  test adapters  in-process loopback adapters that implement each interface against the contracts, so the contracts and
                 the verification are exercised end to end. They are enabled only with CC_PROGRAMMATIC_TEST_ADAPTERS=1
                 and refused in production, and they are always reported as `external: false`
  verification   a platform administrator runs the contract checks against every connected adapter; results are stored
                 (`integration_checks`)
Integration states (capability discovery, `GET /api/programmatic/capabilities`):
  DESIGNED_ONLY  a design exists, no adapter interface is implemented (real-time bidding)
  NOT_CONNECTED  the interface and contract exist; no adapter is configured
  CONNECTED      an adapter is configured and answers its health check
  VERIFIED       a connected adapter passed the contract checks within the last 30 days
Every integration also says whether its adapter is `external` (a real partner) or a test adapter. No partner adapter
exists in this repository, so nothing can be reported as an external connection.
"""
import json, os, time
from typing import Protocol

from fastapi import APIRouter, HTTPException, Request

from . import main as m
from .orgs import require_kind, require_platform_admin, SELL_SIDE

router = APIRouter()
FEED_SCHEMA = 'visuara-inventory-feed/1'
VERIFY_DAYS = 30
STATES = ('DESIGNED_ONLY', 'NOT_CONNECTED', 'CONNECTED', 'VERIFIED')

# ---------------------------------------------------------------- contracts
# A small schema language: {'type': 'object', 'required': {...}, 'optional': {...}}, {'type': 'array', 'items': ...},
# {'type': 'string' | 'integer' | 'number' | 'boolean' | 'null' | [types]}, {'enum': [...]}.
_STR, _INT, _NUM = {'type': 'string'}, {'type': 'integer'}, {'type': 'number'}
CONTRACTS = {
    FEED_SCHEMA: {'type': 'object', 'required': {
        'id': _STR,
        'imp': {'type': 'array', 'min': 1, 'items': {'type': 'object', 'required': {
            'id': _STR, 'bidfloor': {'type': ['number', 'null']}, 'bidfloorcur': {'type': ['string', 'null']},
            'ext': {'type': 'object', 'required': {'visuara': {'type': 'object', 'required': {
                'placement_category': _STR, 'region': _STR, 'start_second': _NUM, 'end_second': _NUM, 'visible_seconds': _NUM,
                'feasibility_score': _NUM, 'brand_safety': {'enum': ['SAFE', 'REVIEW', 'UNSAFE', 'NOT_EVALUATED']},
                'rendering_complexity': {'enum': ['LOW', 'MEDIUM', 'HIGH']}, 'bbox': {'type': 'array', 'items': _NUM},
                'score_is_heuristic': {'enum': [True]}}}}}}}},
        'video': {'type': 'object', 'required': {'id': _STR, 'w': _INT, 'h': _INT, 'duration': _NUM}},
        'site': {'type': 'object', 'required': {'publisher': {'type': 'object', 'required': {'id': _STR, 'name': {'type': ['string', 'null']}}}}},
        'ext': {'type': 'object', 'required': {'schema': {'enum': [FEED_SCHEMA]}, 'deal_type': {'enum': ['fixed_price_direct']}}}}},
    'visuara-deal/1': {'type': 'object', 'required': {
        'deal_id': _STR, 'inventory_id': _STR, 'buyer': {'type': 'object', 'required': {'id': _STR, 'name': _STR}},
        'price_minor': _INT, 'currency': _STR, 'expires_at': _INT}, 'optional': {'campaign_ref': _STR}},
    'visuara-delivery-report/1': {'type': 'object', 'required': {
        'delivery_id': _STR, 'source': _STR, 'impressions': _INT, 'views': _INT, 'period_start': _STR, 'period_end': _STR},
        'optional': {'reach': _INT, 'notes': _STR}},
}
_TYPES = {'string': str, 'integer': int, 'number': (int, float), 'boolean': bool, 'null': type(None), 'object': dict, 'array': list}


def validate(schema, value, path='$'):
    """Errors of `value` against a contract (a name in CONTRACTS, or a schema). Empty list: valid. Pure."""
    s = CONTRACTS[schema] if isinstance(schema, str) else schema
    if 'enum' in s:
        return [] if value in s['enum'] and type(value) in {type(e) for e in s['enum']} else [f'{path}: must be one of {s["enum"]}']
    types = s['type'] if isinstance(s['type'], list) else [s['type']]
    ok = any(isinstance(value, _TYPES[t]) and not (t in ('integer', 'number') and isinstance(value, bool)) for t in types)
    if not ok: return [f'{path}: expected {"/".join(types)}, got {type(value).__name__}']
    errs = []
    if isinstance(value, dict) and 'object' in types:
        for k, sub in s.get('required', {}).items():
            errs += validate(sub, value[k], f'{path}.{k}') if k in value else [f'{path}.{k}: missing']
        for k, sub in s.get('optional', {}).items():
            if k in value: errs += validate(sub, value[k], f'{path}.{k}')
        extra = set(value) - set(s.get('required', {})) - set(s.get('optional', {}))
        if extra: errs.append(f'{path}: unexpected fields {sorted(extra)}')
    if isinstance(value, list) and 'array' in types:
        if len(value) < s.get('min', 0): errs.append(f'{path}: needs at least {s["min"]} item(s)')
        for i, v in enumerate(value): errs += validate(s.get('items', {'type': list(_TYPES)}), v, f'{path}[{i}]')
    return errs


# ---------------------------------------------------------------- adapter interfaces
class SupplyAdapter(Protocol):
    """Publishes inventory to a supply-side platform (contract visuara-inventory-feed/1)."""
    name: str
    external: bool
    def health(self) -> bool: ...
    def publish(self, items: list) -> str: ...
    def withdraw(self, item_id: str) -> None: ...


class DemandAdapter(Protocol):
    """Receives deal proposals from a demand-side platform (contract visuara-deal/1)."""
    name: str
    external: bool
    def health(self) -> bool: ...
    def fetch_deals(self) -> list: ...


class MeasurementAdapter(Protocol):
    """Imports delivery measurement from a third party (contract visuara-delivery-report/1)."""
    name: str
    external: bool
    def health(self) -> bool: ...
    def import_report(self, delivery_id: str) -> dict: ...


class NotConnected:
    """What a capability without an adapter has: it refuses, so nothing can pretend to be connected."""
    def __getattr__(self, name):
        def refuse(*a, **k): raise NotImplementedError(f'No programmatic partner is connected ({name}: no adapter is configured, or the capability is DESIGNED_ONLY)')
        return refuse


# ---------------------------------------------------------------- test adapters (loopback, never external)
class LoopbackSupply:
    name, external = 'loopback-supply', False

    def __init__(self): self.published = {}
    def health(self): return True

    def publish(self, items):
        bad = [e for it in items for e in validate(FEED_SCHEMA, it)]
        if bad: raise ValueError('; '.join(bad[:5]))
        for it in items: self.published[it['imp'][0]['id']] = it
        return f'loopback-batch-{len(self.published)}'

    def withdraw(self, item_id): self.published.pop(item_id, None)


class LoopbackDemand:
    name, external = 'loopback-demand', False
    def health(self): return True

    def fetch_deals(self):
        return [{'deal_id': 'loopback-deal-1', 'inventory_id': '0', 'buyer': {'id': 'loopback-buyer', 'name': 'Loopback buyer'},
                 'price_minor': 10000, 'currency': 'EUR', 'expires_at': int(time.time()) + 3600}]


class LoopbackMeasurement:
    name, external = 'loopback-measurement', False
    def health(self): return True

    def import_report(self, delivery_id):
        return {'delivery_id': str(delivery_id), 'source': 'loopback', 'impressions': 0, 'views': 0, 'period_start': '2026-01-01', 'period_end': '2026-01-01'}


# ---------------------------------------------------------------- registry and discovery
INTEGRATIONS = {   # capability -> (interface kind, contract, design note)
    'supply_side_platform': ('supply', FEED_SCHEMA, 'publish AVAILABLE inventory to an SSP'),
    'demand_side_platform': ('demand', 'visuara-deal/1', 'receive fixed-price deal proposals from a DSP'),
    'third_party_measurement': ('measurement', 'visuara-delivery-report/1', 'import delivery measurement'),
    'real_time_bidding': (None, None, 'answer OpenRTB bid requests in real time; no interface or adapter exists'),
    'pacing_and_budget_optimisation': (None, None, 'campaign budgets are enforced at request time only'),
    'semantic_scene_search': (None, None, 'structured search is implemented (AD-11); embeddings are not'),
}
CONTRACT_ENDPOINTS = [
    {'name': 'inventory_feed', 'status': 'IMPLEMENTED', 'contract': FEED_SCHEMA, 'detail': f'GET /api/programmatic/inventory-feed ({FEED_SCHEMA}): the publisher\'s AVAILABLE items, each checked against the contract'},
    {'name': 'deal_reservation', 'status': 'IMPLEMENTED', 'contract': None, 'detail': 'Direct deals through marketplace reservations (fixed price, one winner)'},
    {'name': 'bid_request_mapping', 'status': 'IMPLEMENTED', 'contract': FEED_SCHEMA, 'detail': 'to_bid_request(): one inventory item as an OpenRTB-style object; not sent anywhere'},
]


def test_adapters_enabled():
    return os.getenv('CC_PROGRAMMATIC_TEST_ADAPTERS') == '1' and os.getenv('CC_ENV', '').lower() != 'production'


_TEST = {}


def adapters():
    """kind -> adapter, for the adapters configured here. No partner adapter exists, so only test adapters can appear."""
    if not test_adapters_enabled(): return {}
    if not _TEST: _TEST.update(supply=LoopbackSupply(), demand=LoopbackDemand(), measurement=LoopbackMeasurement())
    return dict(_TEST)


def _last_check(c, name):
    return c.execute('SELECT * FROM integration_checks WHERE adapter=? ORDER BY id DESC LIMIT 1', (name,)).fetchone()


def integration_states(c):
    out, configured = [], adapters()
    for cap, (kind, contract, note) in INTEGRATIONS.items():
        a = configured.get(kind) if kind else None
        if kind is None: state = 'DESIGNED_ONLY'
        elif a is None: state = 'NOT_CONNECTED'
        else:
            try: healthy = bool(a.health())
            except Exception: healthy = False
            last = _last_check(c, a.name)
            fresh = last and last['passed'] and last['checked_at_epoch'] > time.time() - VERIFY_DAYS * 86400
            state = 'VERIFIED' if healthy and fresh else 'CONNECTED' if healthy else 'NOT_CONNECTED'
        last = _last_check(c, a.name) if a else None
        out.append({'name': cap, 'status': state, 'contract': contract, 'adapter': a.name if a else None, 'external': bool(a and a.external),
                    'last_check': {'passed': bool(last['passed']), 'at': last['checked_at'], 'detail': last['detail']} if last else None, 'detail': note})
    return out


def verify(c, user_id=None):
    """Run the contract checks against every configured adapter and store the results."""
    results = []
    for kind, a in adapters().items():
        errors = []
        try:
            if not a.health(): errors.append('health check failed')
            if kind == 'supply':
                sample = _sample_feed_item()
                ref = a.publish([sample]); a.withdraw(sample['imp'][0]['id'])
                if not isinstance(ref, str) or not ref: errors.append('publish did not return a reference')
            elif kind == 'demand':
                deals = a.fetch_deals()
                if not isinstance(deals, list): errors.append('fetch_deals did not return a list')
                else:
                    for i, d in enumerate(deals): errors += [f'deal {i}: {e}' for e in validate('visuara-deal/1', d)]
            elif kind == 'measurement':
                errors += validate('visuara-delivery-report/1', a.import_report('0'))
        except Exception as e:                                     # an adapter that raises fails its check
            errors.append(f'{type(e).__name__}: {str(e)[:200]}')
        passed = not errors
        c.execute('INSERT INTO integration_checks(adapter,kind,external,passed,detail,checked_at_epoch,checked_by) VALUES(?,?,?,?,?,?,?)',
                  (a.name, kind, int(a.external), int(passed), '; '.join(errors)[:500] or 'all contract checks passed', time.time(), user_id))
        results.append({'adapter': a.name, 'kind': kind, 'external': a.external, 'passed': passed, 'errors': errors})
    return results


def _sample_feed_item():
    return {'id': 'visuara-inv-0-v1', 'imp': [{'id': '0', 'bidfloor': 1.0, 'bidfloorcur': 'EUR', 'ext': {'visuara': {
        'placement_category': 'VERTICAL_SURFACE', 'region': 'CENTER', 'start_second': 0.0, 'end_second': 4.0, 'visible_seconds': 4.0,
        'feasibility_score': .8, 'brand_safety': 'SAFE', 'rendering_complexity': 'LOW', 'bbox': [.1, .1, .3, .3], 'score_is_heuristic': True}}}],
        'video': {'id': '0', 'w': 640, 'h': 360, 'duration': 4.0}, 'site': {'publisher': {'id': '0', 'name': 'Contract check'}},
        'ext': {'schema': FEED_SCHEMA, 'deal_type': 'fixed_price_direct'}}


def to_bid_request(c, item):
    o = c.execute('SELECT * FROM opportunities WHERE id=?', (item['opportunity_id'],)).fetchone()
    v = c.execute('SELECT id, duration, width, height FROM videos WHERE id=?', (item['video_id'],)).fetchone()
    pub = c.execute('SELECT name FROM organizations WHERE id=?', (item['org_id'],)).fetchone()
    g = json.loads(o['geometry_json'])
    return {
        'id': f'visuara-inv-{item["id"]}-v{item["version"]}',
        'imp': [{'id': str(item['id']), 'bidfloor': item['price_minor'] / 100 if item['price_minor'] else None, 'bidfloorcur': item['currency'],
                 'ext': {'visuara': {'placement_category': o['placement_category'], 'region': o['region'], 'start_second': o['start_second'], 'end_second': o['end_second'],
                                     'visible_seconds': o['visible_seconds'], 'feasibility_score': o['feasibility_score'], 'brand_safety': o['brand_safety'],
                                     'rendering_complexity': o['rendering_complexity'], 'bbox': g['bbox'], 'score_is_heuristic': True}}}],
        'video': {'id': str(v['id']), 'w': v['width'], 'h': v['height'], 'duration': v['duration']},
        'site': {'publisher': {'id': str(item['org_id']), 'name': pub['name'] if pub else None}},
        'ext': {'schema': FEED_SCHEMA, 'deal_type': 'fixed_price_direct'},
    }


# ---------------------------------------------------------------- API
@router.get('/api/programmatic/capabilities')
def capabilities(request: Request):
    m.current_user(request)
    with m.db() as c: integrations = integration_states(c)
    return {'capabilities': CONTRACT_ENDPOINTS + integrations, 'states': list(STATES),
            'external_connections': sum(1 for x in integrations if x['external'] and x['status'] in ('CONNECTED', 'VERIFIED')),
            'note': 'No programmatic partner is connected. NOT_CONNECTED means the interface and contract exist; CONNECTED and VERIFIED say whether the '
                    'adapter is external or a test adapter.'}


@router.get('/api/programmatic/contracts')
def contracts(request: Request):
    m.current_user(request)
    return {'contracts': CONTRACTS}


@router.post('/api/admin/programmatic/verify')
def run_verification(request: Request):
    u = m.current_user(request); require_platform_admin(u)
    with m.db() as c:
        results = verify(c, u['id'])
        return {'results': results, 'capabilities': integration_states(c)}


@router.get('/api/programmatic/inventory-feed')
def inventory_feed(request: Request):
    u = m.current_user(request); require_kind(u, SELL_SIDE, 'The inventory feed')
    with m.db() as c:
        rows = c.execute("SELECT * FROM inventory_items WHERE org_id=? AND status='AVAILABLE' ORDER BY id", (u['org_id'],)).fetchall()
        items = [to_bid_request(c, r) for r in rows]
    bad = [e for it in items for e in validate(FEED_SCHEMA, it)]
    if bad: raise HTTPException(500, {'code': 'CONTRACT_VIOLATION', 'message': 'The feed does not match its contract', 'errors': bad[:10]})
    return {'schema': FEED_SCHEMA, 'publisher_org_id': u['org_id'], 'items': items}
