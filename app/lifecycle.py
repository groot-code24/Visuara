"""Server-enforced placement lifecycle. Every status change goes through transition()."""
import hashlib, json

STATES = ('DRAFT', 'PROPOSED', 'CREATOR_APPROVED', 'BRAND_APPROVED', 'RIGHTS_VERIFIED', 'QUALITY_VERIFIED',
          'READY_FOR_RENDER', 'RENDERING', 'RENDERED', 'DELIVERED',
          'REJECTED', 'RENDER_FAILED', 'REVOKED', 'EXPIRED', 'CANCELLED')

# Revision (any state except CANCELLED -> DRAFT with a new version) is handled by revise(), not here.
TRANSITIONS = {
    'DRAFT':            {'PROPOSED', 'CANCELLED'},
    'PROPOSED':         {'CREATOR_APPROVED', 'REJECTED', 'CANCELLED', 'REVOKED'},
    'CREATOR_APPROVED': {'BRAND_APPROVED', 'REJECTED', 'CANCELLED', 'REVOKED'},
    'BRAND_APPROVED':   {'RIGHTS_VERIFIED', 'EXPIRED', 'CANCELLED', 'REVOKED'},
    'RIGHTS_VERIFIED':  {'QUALITY_VERIFIED', 'CANCELLED', 'REVOKED'},
    'QUALITY_VERIFIED': {'READY_FOR_RENDER', 'CANCELLED', 'REVOKED'},
    'READY_FOR_RENDER': {'RENDERING', 'EXPIRED', 'CANCELLED', 'REVOKED'},
    'RENDERING':        {'RENDERED', 'RENDER_FAILED', 'CANCELLED', 'REVOKED'},
    'RENDER_FAILED':    {'READY_FOR_RENDER', 'CANCELLED', 'REVOKED'},
    'RENDERED':         {'DELIVERED', 'EXPIRED', 'REVOKED', 'CANCELLED'},
    'DELIVERED':        {'EXPIRED', 'REVOKED'},
    'REJECTED':         set(),
    'REVOKED':          set(),
    'EXPIRED':          set(),
    'CANCELLED':        set(),
}
REVISABLE = set(STATES) - {'CANCELLED'}
# States in which the current version's approvals are complete.
APPROVED_STATES = {'BRAND_APPROVED', 'RIGHTS_VERIFIED', 'QUALITY_VERIFIED', 'READY_FOR_RENDER', 'RENDERING', 'RENDERED', 'DELIVERED', 'RENDER_FAILED'}


class TransitionError(Exception):
    def __init__(self, current, target, reason=None):
        self.current, self.target = current, target
        super().__init__(reason or f'Placement cannot move from {current} to {target}')


def check(current, target):
    if target not in TRANSITIONS.get(current, set()):
        raise TransitionError(current, target)


def transition(c, placement, target, actor_id, reason=None, details=None):
    """Atomically move a placement from its current status to target; records an audit event."""
    current = placement['status']
    check(current, target)
    cur = c.execute('UPDATE placements SET status=? WHERE id=? AND status=? AND version=?',
                    (target, placement['id'], current, placement['version']))
    if cur.rowcount != 1:
        raise TransitionError(current, target, 'Placement changed concurrently; reload and retry')
    c.execute('INSERT INTO events(org_id,user_id,event_type,entity_type,entity_id,details) VALUES(?,?,?,?,?,?)',
              (placement['org_id'], actor_id, 'placement.transition', 'placement', placement['id'],
               json.dumps({'from': current, 'to': target, 'version': placement['version'], 'reason': reason, **(details or {})})))
    placement['status'] = target
    return placement


# Fields whose change creates a new placement version (and invalidates approvals and quality checks).
MATERIAL_FIELDS = ('video_id', 'product_id', 'frame_second', 'x', 'y', 'width', 'start_second', 'end_second', 'render_options')


def snapshot(placement, product_sha256):
    snap = {k: placement[k] for k in MATERIAL_FIELDS}
    snap['render_options'] = json.loads(snap['render_options'] or '{}') if isinstance(snap['render_options'], str) else snap['render_options']
    snap['product_sha256'] = product_sha256
    body = json.dumps(snap, sort_keys=True, separators=(',', ':'))
    return snap, hashlib.sha256(body.encode()).hexdigest()
