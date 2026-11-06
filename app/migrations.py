"""Ordered, recorded schema migrations (Phase 1, AD-1).

`init_db()` first applies the baseline: the `CREATE TABLE IF NOT EXISTS` schemas and the column list `_MIGRATIONS` in
`app/main.py`. That baseline is frozen. Every later change is a `Migration` in `MIGRATIONS` below and runs once, in
order, recorded in `schema_migrations` with a checksum of its SQL and code.

Rules for a migration:
- Additive only: new tables, columns, indexes and backfills. Nothing is dropped or rewritten.
- Idempotent: every statement is safe to run twice (`IF NOT EXISTS`, `add_column`, backfills guarded by
  `NOT EXISTS`). On SQLite, `executescript` commits as it goes, so a crash part-way must leave a state that the
  same migration can finish.
- Immutable once applied: changing an applied migration stops start-up with an error. Fix forward with a new one.
- SQL uses the portable SQLite dialect of the rest of the schema; `app/db.py` translates it for PostgreSQL.
  A table whose first column is `id INTEGER PRIMARY KEY` gets a generated id on PostgreSQL.
- New tables declare foreign keys `DEFERRABLE INITIALLY DEFERRED`: they are checked at commit, so
  `ops/dbtransfer` can still import tables in any order inside one transaction.
"""
import hashlib, inspect, json
from dataclasses import dataclass
from typing import Callable, Optional

ROLES = ('viewer', 'editor', 'manager', 'admin', 'owner')
ORG_KINDS = ('direct', 'brand', 'publisher', 'agency')


@dataclass(frozen=True)
class Migration:
    id: str                                   # 'NNNN_name'; applied in this order
    description: str
    sql: str = ''                             # portable DDL and set-based backfills
    run: Optional[Callable] = None            # Python step run after `sql`, given the connection
    columns: tuple = ()                       # (table, column, definition) added with Connection.add_column

    def checksum(self):
        parts = [self.id, self.sql, repr(self.columns), inspect.getsource(self.run) if self.run else '']
        text = '\n'.join(line.rstrip() for line in '\n'.join(parts).splitlines())
        return hashlib.sha256(text.encode()).hexdigest()


_FK = 'DEFERRABLE INITIALLY DEFERRED'
_ROLE_LIST = ','.join(f"'{r}'" for r in ROLES)

# ---------------------------------------------------------------- 0001: organization kinds and platform administrators
ORG_KINDS_SQL = ''
ORG_KIND_COLUMNS = (
    ('organizations', 'kind', "TEXT NOT NULL DEFAULT 'direct' CHECK(kind IN ('direct','brand','publisher','agency'))"),
    ('users', 'platform_admin', 'INTEGER NOT NULL DEFAULT 0 CHECK(platform_admin IN (0,1))'),
)

# ---------------------------------------------------------------- 0002: memberships, backfilled from users.org_id
MEMBERSHIPS_SQL = f'''
CREATE TABLE IF NOT EXISTS memberships(id INTEGER PRIMARY KEY,
  org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  user_id INTEGER NOT NULL REFERENCES users(id) {_FK},
  member_role TEXT NOT NULL CHECK(member_role IN ({_ROLE_LIST})),
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','SUSPENDED','REMOVED')),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(org_id,user_id));
CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id,status);
INSERT INTO memberships(org_id,user_id,member_role,status)
  SELECT u.org_id, u.id,
         CASE WHEN u.member_role IN ({_ROLE_LIST}) THEN u.member_role ELSE 'viewer' END,
         CASE WHEN u.removed_at IS NOT NULL THEN 'REMOVED' WHEN u.suspended_at IS NOT NULL THEN 'SUSPENDED' ELSE 'ACTIVE' END
  FROM users u WHERE NOT EXISTS (SELECT 1 FROM memberships m WHERE m.org_id=u.org_id AND m.user_id=u.id);
'''

# ---------------------------------------------------------------- 0003: video versions
VIDEO_VERSIONS_SQL = f'''
CREATE TABLE IF NOT EXISTS video_versions(id INTEGER PRIMARY KEY,
  org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  video_id INTEGER NOT NULL REFERENCES videos(id) {_FK},
  version INTEGER NOT NULL CHECK(version>=1),
  source_path TEXT NOT NULL,
  content_sha256 TEXT,
  size_bytes INTEGER NOT NULL DEFAULT 0,
  duration REAL NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, fps REAL NOT NULL DEFAULT 0,
  codec TEXT,
  created_by INTEGER NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(video_id,version));
CREATE INDEX IF NOT EXISTS idx_video_versions_hash ON video_versions(org_id,content_sha256);
INSERT INTO video_versions(org_id,video_id,version,source_path,size_bytes,duration,width,height,fps,created_by,created_at)
  SELECT v.org_id, v.id, 1, v.source_path, v.size_bytes, v.duration, v.width, v.height, COALESCE(v.fps,0), v.created_by, v.created_at
  FROM videos v WHERE NOT EXISTS (SELECT 1 FROM video_versions x WHERE x.video_id=v.id);
'''
VIDEO_VERSION_COLUMNS = (('videos', 'current_version', 'INTEGER NOT NULL DEFAULT 1'),)

# ---------------------------------------------------------------- 0004: analysis versions, scenes and scene frames
ANALYSIS_SQL = f'''
CREATE TABLE IF NOT EXISTS analysis_versions(id INTEGER PRIMARY KEY,
  org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  video_id INTEGER NOT NULL REFERENCES videos(id) {_FK},
  video_version_id INTEGER NOT NULL REFERENCES video_versions(id) {_FK},
  version INTEGER NOT NULL CHECK(version>=1),
  status TEXT NOT NULL CHECK(status IN ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED')),
  pipeline TEXT NOT NULL,
  model_versions_json TEXT NOT NULL DEFAULT '{{}}',
  config_json TEXT NOT NULL DEFAULT '{{}}',
  outputs_json TEXT NOT NULL DEFAULT '{{}}',
  error_json TEXT,
  started_at TEXT, finished_at TEXT,
  created_by INTEGER,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(video_id,version));
CREATE INDEX IF NOT EXISTS idx_analysis_versions_video ON analysis_versions(video_id,status,version);
CREATE TABLE IF NOT EXISTS scenes(id INTEGER PRIMARY KEY,
  org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  video_id INTEGER NOT NULL REFERENCES videos(id) {_FK},
  video_version_id INTEGER NOT NULL REFERENCES video_versions(id) {_FK},
  analysis_version_id INTEGER NOT NULL REFERENCES analysis_versions(id) {_FK},
  scene_index INTEGER NOT NULL CHECK(scene_index>=0),
  start_second REAL NOT NULL CHECK(start_second>=0),
  end_second REAL NOT NULL,
  boundary_source TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{{}}',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  CHECK(end_second>start_second),
  UNIQUE(analysis_version_id,scene_index));
CREATE INDEX IF NOT EXISTS idx_scenes_video ON scenes(video_id,analysis_version_id);
CREATE TABLE IF NOT EXISTS scene_frames(id INTEGER PRIMARY KEY,
  scene_id INTEGER NOT NULL REFERENCES scenes(id) {_FK},
  analysis_version_id INTEGER NOT NULL REFERENCES analysis_versions(id) {_FK},
  second REAL NOT NULL CHECK(second>=0),
  image_path TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  score REAL NOT NULL DEFAULT 0,
  representative INTEGER NOT NULL DEFAULT 0 CHECK(representative IN (0,1)),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(scene_id,second));
CREATE INDEX IF NOT EXISTS idx_scene_frames_analysis ON scene_frames(analysis_version_id,second);
'''


def backfill_analyses(c):
    """One COMPLETED analysis version per video that already has keyframes; scene i runs from keyframe i to keyframe
    i+1 (the last to the end of the video). Self-contained on purpose: it uses only what this migration creates, so
    later changes to the application code cannot change what it does."""
    rows = c.execute('SELECT v.id, v.org_id, v.duration, v.created_by, vv.id AS vvid FROM videos v JOIN video_versions vv ON vv.video_id=v.id AND vv.version=1 '
                     'WHERE EXISTS (SELECT 1 FROM frames f WHERE f.video_id=v.id) AND NOT EXISTS (SELECT 1 FROM analysis_versions a WHERE a.video_id=v.id) ORDER BY v.id').fetchall()
    for v in rows:
        frames = list({f['second']: f for f in c.execute('SELECT second,image_path,description,score FROM frames WHERE video_id=? ORDER BY second DESC', (v['id'],)).fetchall()}.values())[::-1]
        aid = c.execute("INSERT INTO analysis_versions(org_id,video_id,video_version_id,version,status,pipeline,config_json,outputs_json,created_by,finished_at) "
                        "VALUES(?,?,?,1,'COMPLETED','legacy-keyframes-v1',?,?,?,CURRENT_TIMESTAMP)",
                        (v['org_id'], v['id'], v['vvid'], json.dumps({'source': 'backfill from frames'}), json.dumps({'keyframes': len(frames)}), v['created_by'])).lastrowid
        for i, f in enumerate(frames):
            end = frames[i + 1]['second'] if i + 1 < len(frames) else max(v['duration'], f['second'] + 0.01)
            sid = c.execute("INSERT INTO scenes(org_id,video_id,video_version_id,analysis_version_id,scene_index,start_second,end_second,boundary_source,description) "
                            "VALUES(?,?,?,?,?,?,?,'LEGACY_KEYFRAME',?)", (v['org_id'], v['id'], v['vvid'], aid, i, f['second'], end, f['description'])).lastrowid
            c.execute('INSERT INTO scene_frames(scene_id,analysis_version_id,second,image_path,description,score,representative) VALUES(?,?,?,?,?,?,1)',
                      (sid, aid, f['second'], f['image_path'], f['description'], f['score']))


# ---------------------------------------------------------------- 0005: placement opportunities (typed, validated)
OPPORTUNITIES_SQL = f'''
CREATE TABLE IF NOT EXISTS opportunities(id INTEGER PRIMARY KEY,
  org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  video_id INTEGER NOT NULL REFERENCES videos(id) {_FK},
  video_version_id INTEGER NOT NULL REFERENCES video_versions(id) {_FK},
  analysis_version_id INTEGER NOT NULL REFERENCES analysis_versions(id) {_FK},
  scene_id INTEGER NOT NULL REFERENCES scenes(id) {_FK},
  schema_version INTEGER NOT NULL DEFAULT 1,
  candidate_kind TEXT NOT NULL CHECK(candidate_kind IN ('SURFACE','OBJECT')),
  candidate_label TEXT NOT NULL,
  placement_category TEXT NOT NULL CHECK(placement_category IN ('HORIZONTAL_SURFACE','VERTICAL_SURFACE','ANGLED_SURFACE','SCREEN_OVERLAY','OBJECT_REPLACEMENT')),
  geometry_json TEXT NOT NULL,
  region TEXT NOT NULL CHECK(region IN ('TOP_LEFT','TOP','TOP_RIGHT','LEFT','CENTER','RIGHT','BOTTOM_LEFT','BOTTOM','BOTTOM_RIGHT')),
  start_second REAL NOT NULL CHECK(start_second>=0),
  end_second REAL NOT NULL,
  visible_seconds REAL NOT NULL CHECK(visible_seconds>=0),
  feasibility_score REAL NOT NULL CHECK(feasibility_score>=0 AND feasibility_score<=1),
  product_compatibility_json TEXT NOT NULL,
  brand_safety TEXT NOT NULL CHECK(brand_safety IN ('SAFE','REVIEW','UNSAFE','NOT_EVALUATED')),
  rendering_complexity TEXT NOT NULL CHECK(rendering_complexity IN ('LOW','MEDIUM','HIGH')),
  confidence REAL NOT NULL CHECK(confidence>=0 AND confidence<=1),
  confidence_json TEXT NOT NULL DEFAULT '{{}}',
  explanation TEXT NOT NULL,
  validation_status TEXT NOT NULL CHECK(validation_status IN ('VALID','INVALID','NEEDS_REVIEW')),
  validation_json TEXT NOT NULL DEFAULT '{{}}',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  CHECK(end_second>start_second));
CREATE INDEX IF NOT EXISTS idx_opportunities_scene ON opportunities(scene_id);
CREATE INDEX IF NOT EXISTS idx_opportunities_org ON opportunities(org_id,validation_status);
'''

# ---------------------------------------------------------------- 0006: transactional outbox
OUTBOX_SQL = '''
CREATE TABLE IF NOT EXISTS outbox_events(id INTEGER PRIMARY KEY,
  event_id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  event_version INTEGER NOT NULL CHECK(event_version>=1),
  org_id INTEGER,
  aggregate_type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  trace_id TEXT,
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','DELIVERED','DEAD')),
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 8,
  available_at REAL NOT NULL,
  lease_until REAL, lease_token TEXT,
  last_error TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  delivered_at TEXT);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_events(status,available_at,id);
CREATE INDEX IF NOT EXISTS idx_outbox_aggregate ON outbox_events(aggregate_type,aggregate_id,id);
CREATE TABLE IF NOT EXISTS outbox_consumptions(consumer TEXT NOT NULL, event_id TEXT NOT NULL,
  processed_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(consumer,event_id));
'''

# ---------------------------------------------------------------- 0009: analysis as a staged job; detection contracts
ANALYSIS_JOB_SQL = f'''
CREATE UNIQUE INDEX IF NOT EXISTS idx_analysis_one_active ON analysis_versions(video_version_id) WHERE status IN ('QUEUED','RUNNING');
CREATE TABLE IF NOT EXISTS detected_objects(id INTEGER PRIMARY KEY,
  analysis_version_id INTEGER NOT NULL REFERENCES analysis_versions(id) {_FK},
  scene_id INTEGER NOT NULL REFERENCES scenes(id) {_FK},
  frame_second REAL NOT NULL, label TEXT NOT NULL, confidence REAL NOT NULL CHECK(confidence>=0 AND confidence<=1),
  bbox_json TEXT NOT NULL, model TEXT NOT NULL, track_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS object_tracks(id INTEGER PRIMARY KEY,
  analysis_version_id INTEGER NOT NULL REFERENCES analysis_versions(id) {_FK},
  scene_id INTEGER NOT NULL REFERENCES scenes(id) {_FK},
  label TEXT NOT NULL, start_second REAL NOT NULL, end_second REAL NOT NULL, frames INTEGER NOT NULL, model TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, CHECK(end_second>=start_second));
'''
ANALYSIS_JOB_COLUMNS = (
    ('analysis_versions', 'stage', "TEXT NOT NULL DEFAULT ''"), ('analysis_versions', 'stages_json', "TEXT NOT NULL DEFAULT '{}'"),
    ('analysis_versions', 'progress', 'REAL NOT NULL DEFAULT 0'), ('analysis_versions', 'attempts', 'INTEGER NOT NULL DEFAULT 0'),
    ('analysis_versions', 'max_attempts', 'INTEGER NOT NULL DEFAULT 2'), ('analysis_versions', 'lease_until', 'REAL'),
    ('analysis_versions', 'lease_token', 'TEXT'), ('analysis_versions', 'cancel_requested', 'INTEGER NOT NULL DEFAULT 0'),
)

# ---------------------------------------------------------------- 0010: scoring configurations, brand safety, inventory
SCORING_SQL = f'''
CREATE TABLE IF NOT EXISTS scoring_configs(id INTEGER PRIMARY KEY, version TEXT NOT NULL UNIQUE, weights_json TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
INSERT INTO scoring_configs(version,weights_json,notes) SELECT 'v1',
  '{{"visual_feasibility":0.25,"available_area":0.15,"visibility":0.15,"exposure_duration":0.15,"contextual_relevance":0.10,"brand_safety":0.10,"content_rights":0.10}}',
  'Heuristic weights. Not a validated prediction of performance.'
  WHERE NOT EXISTS (SELECT 1 FROM scoring_configs WHERE version='v1');
CREATE TABLE IF NOT EXISTS brand_safety_rules(id INTEGER PRIMARY KEY, org_id INTEGER REFERENCES organizations(id) {_FK},
  term TEXT NOT NULL, verdict TEXT NOT NULL CHECK(verdict IN ('REVIEW','UNSAFE')), created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_brand_safety_org ON brand_safety_rules(org_id);
CREATE TABLE IF NOT EXISTS inventory_items(id INTEGER PRIMARY KEY,
  org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  opportunity_id INTEGER NOT NULL UNIQUE REFERENCES opportunities(id) {_FK},
  video_id INTEGER NOT NULL REFERENCES videos(id) {_FK},
  scene_id INTEGER NOT NULL REFERENCES scenes(id) {_FK},
  status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','AVAILABLE','RESERVED','SOLD','WITHDRAWN')),
  price_minor INTEGER CHECK(price_minor IS NULL OR price_minor>0),
  currency TEXT CHECK(currency IS NULL OR length(currency)=3),
  listed_at TEXT, version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_inventory_status ON inventory_items(status,org_id);
'''
SCORING_COLUMNS = (
    ('opportunities', 'score', 'REAL'), ('opportunities', 'score_breakdown_json', "TEXT NOT NULL DEFAULT '{}'"),
    ('opportunities', 'scoring_config_id', 'INTEGER'), ('opportunities', 'reviewed_by', 'INTEGER'), ('opportunities', 'reviewed_at', 'TEXT'),
)
PLATFORM_SAFETY_TERMS = (('weapon', 'UNSAFE'), ('gun', 'UNSAFE'), ('blood', 'UNSAFE'), ('violence', 'UNSAFE'), ('drugs', 'UNSAFE'), ('nudity', 'UNSAFE'),
                         ('alcohol', 'REVIEW'), ('tobacco', 'REVIEW'), ('cigarette', 'REVIEW'), ('gambling', 'REVIEW'), ('casino', 'REVIEW'), ('hospital', 'REVIEW'))


def seed_safety_terms(c):
    for term, verdict in PLATFORM_SAFETY_TERMS:
        if not c.execute('SELECT 1 FROM brand_safety_rules WHERE org_id IS NULL AND term=?', (term,)).fetchone():
            c.execute('INSERT INTO brand_safety_rules(org_id,term,verdict) VALUES(NULL,?,?)', (term, verdict))


# ---------------------------------------------------------------- 0011: brand and publisher profiles, rights records
PROFILES_SQL = f'''
CREATE TABLE IF NOT EXISTS brand_profiles(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL UNIQUE REFERENCES organizations(id) {_FK},
  display_name TEXT NOT NULL, website TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', categories_json TEXT NOT NULL DEFAULT '[]',
  excluded_terms_json TEXT NOT NULL DEFAULT '[]', created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS publisher_profiles(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL UNIQUE REFERENCES organizations(id) {_FK},
  display_name TEXT NOT NULL, website TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', content_categories_json TEXT NOT NULL DEFAULT '[]',
  audience_json TEXT NOT NULL DEFAULT '{{}}', created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS rights_records(id INTEGER PRIMARY KEY,
  org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  video_id INTEGER NOT NULL REFERENCES videos(id) {_FK},
  holder TEXT NOT NULL, channels_json TEXT NOT NULL DEFAULT '[]', territories_json TEXT NOT NULL DEFAULT '[]',
  starts_on TEXT, expires_on TEXT,
  evidence_path TEXT NOT NULL, evidence_sha256 TEXT NOT NULL, evidence_name TEXT NOT NULL, evidence_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','VERIFIED','REJECTED','EXPIRED','REVOKED')),
  verified_by INTEGER, verified_at TEXT, review_notes TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_rights_video ON rights_records(video_id,status);
'''

# ---------------------------------------------------------------- 0012: product catalogue and asset versions, recommendations
CATALOGUE_SQL = f'''
CREATE TABLE IF NOT EXISTS product_assets(id INTEGER PRIMARY KEY,
  org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  product_id INTEGER NOT NULL REFERENCES products(id) {_FK},
  version INTEGER NOT NULL CHECK(version>=1), kind TEXT NOT NULL CHECK(kind IN ('IMAGE','MODEL_3D')),
  path TEXT NOT NULL, sha256 TEXT, size_bytes INTEGER NOT NULL DEFAULT 0, created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(product_id,kind,version));
INSERT INTO product_assets(org_id,product_id,version,kind,path,sha256,size_bytes,created_by,created_at)
  SELECT p.org_id, p.id, 1, 'IMAGE', p.image_path, p.sha256, p.size_bytes, p.created_by, p.created_at FROM products p
  WHERE NOT EXISTS (SELECT 1 FROM product_assets a WHERE a.product_id=p.id AND a.kind='IMAGE');
CREATE TABLE IF NOT EXISTS scene_recommendations(id INTEGER PRIMARY KEY,
  brand_org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  product_id INTEGER NOT NULL REFERENCES products(id) {_FK},
  inventory_id INTEGER NOT NULL REFERENCES inventory_items(id) {_FK},
  run_id TEXT NOT NULL, rank INTEGER NOT NULL, score REAL NOT NULL, breakdown_json TEXT NOT NULL, created_by INTEGER,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_recommendations_run ON scene_recommendations(brand_org_id,run_id,rank);
'''
CATALOGUE_COLUMNS = (
    ('products', 'status', "TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('DRAFT','ACTIVE','ARCHIVED'))"),
    ('products', 'image_version', 'INTEGER NOT NULL DEFAULT 1'),
    ('products', 'source_product_id', 'INTEGER'),                 # a copy made for a publisher to render a campaign placement
    ('products', 'source_org_id', 'INTEGER'),
)

# ---------------------------------------------------------------- 0013: reservations, campaigns, placement requests, notifications
MARKETPLACE_SQL = f'''
CREATE TABLE IF NOT EXISTS campaigns(id INTEGER PRIMARY KEY,
  org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  name TEXT NOT NULL, objective TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','SUBMITTED','ACTIVE','PAUSED','COMPLETED','CANCELLED')),
  budget_minor INTEGER NOT NULL DEFAULT 0 CHECK(budget_minor>=0), currency TEXT NOT NULL DEFAULT 'EUR' CHECK(length(currency)=3),
  starts_on TEXT, ends_on TEXT, version INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_campaigns_org ON campaigns(org_id,status);
CREATE TABLE IF NOT EXISTS campaign_products(campaign_id INTEGER NOT NULL REFERENCES campaigns(id) {_FK},
  product_id INTEGER NOT NULL REFERENCES products(id) {_FK}, created_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(campaign_id,product_id));
CREATE TABLE IF NOT EXISTS campaign_targeting(campaign_id INTEGER PRIMARY KEY REFERENCES campaigns(id) {_FK},
  categories_json TEXT NOT NULL DEFAULT '[]', placement_categories_json TEXT NOT NULL DEFAULT '[]', publisher_ids_json TEXT NOT NULL DEFAULT '[]',
  min_score REAL NOT NULL DEFAULT 0 CHECK(min_score>=0 AND min_score<=1), require_brand_safe INTEGER NOT NULL DEFAULT 1,
  max_price_minor INTEGER, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS status_transitions(id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL,
  org_id INTEGER NOT NULL, from_status TEXT, to_status TEXT NOT NULL, user_id INTEGER, reason TEXT NOT NULL DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_transitions_entity ON status_transitions(entity_type,entity_id,id);
CREATE TABLE IF NOT EXISTS reservations(id INTEGER PRIMARY KEY,
  inventory_id INTEGER NOT NULL REFERENCES inventory_items(id) {_FK},
  brand_org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  publisher_org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  campaign_id INTEGER REFERENCES campaigns(id) {_FK},
  status TEXT NOT NULL CHECK(status IN ('HELD','CONFIRMED','RELEASED','EXPIRED')),
  price_minor INTEGER NOT NULL CHECK(price_minor>0), currency TEXT NOT NULL CHECK(length(currency)=3),
  expires_at REAL, created_by INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reservation_exclusive ON reservations(inventory_id) WHERE status IN ('HELD','CONFIRMED');
CREATE INDEX IF NOT EXISTS idx_reservations_brand ON reservations(brand_org_id,status);
CREATE TABLE IF NOT EXISTS placement_requests(id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id) {_FK},
  brand_org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  publisher_org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  inventory_id INTEGER NOT NULL REFERENCES inventory_items(id) {_FK},
  reservation_id INTEGER NOT NULL UNIQUE REFERENCES reservations(id) {_FK},
  product_id INTEGER NOT NULL REFERENCES products(id) {_FK},
  product_asset_version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'REQUESTED' CHECK(status IN ('REQUESTED','APPROVED','REJECTED','CANCELLED','IN_PRODUCTION','DELIVERED','ACCEPTED')),
  brand_approved_by INTEGER, brand_approved_at TEXT, publisher_approved_by INTEGER, publisher_approved_at TEXT,
  shadow_product_id INTEGER REFERENCES products(id) {_FK}, placement_id INTEGER REFERENCES placements(id) {_FK},
  notes TEXT NOT NULL DEFAULT '', rejection_reason TEXT, version INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_requests_brand ON placement_requests(brand_org_id,status);
CREATE INDEX IF NOT EXISTS idx_requests_publisher ON placement_requests(publisher_org_id,status);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', entity_type TEXT, entity_id INTEGER, read_at TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_notifications_org ON notifications(org_id,read_at,id);
'''

# ---------------------------------------------------------------- 0014: quotes, invoices, append-only ledger, revenue, idempotency
COMMERCIAL_SQL = f'''
CREATE TABLE IF NOT EXISTS quotes(id INTEGER PRIMARY KEY,
  placement_request_id INTEGER NOT NULL UNIQUE REFERENCES placement_requests(id) {_FK},
  brand_org_id INTEGER NOT NULL, publisher_org_id INTEGER NOT NULL,
  amount_minor INTEGER NOT NULL CHECK(amount_minor>0), currency TEXT NOT NULL CHECK(length(currency)=3),
  platform_fee_bps INTEGER NOT NULL CHECK(platform_fee_bps>=0 AND platform_fee_bps<=10000),
  status TEXT NOT NULL DEFAULT 'ISSUED' CHECK(status IN ('ISSUED','ACCEPTED','VOID')),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, accepted_at TEXT, accepted_by INTEGER);
CREATE TABLE IF NOT EXISTS invoices(id INTEGER PRIMARY KEY, number TEXT NOT NULL UNIQUE,
  quote_id INTEGER NOT NULL UNIQUE REFERENCES quotes(id) {_FK},
  brand_org_id INTEGER NOT NULL, publisher_org_id INTEGER NOT NULL,
  amount_minor INTEGER NOT NULL CHECK(amount_minor>0), currency TEXT NOT NULL CHECK(length(currency)=3),
  status TEXT NOT NULL DEFAULT 'ISSUED' CHECK(status IN ('ISSUED','PAID','VOID')),
  due_on TEXT NOT NULL, issued_at TEXT DEFAULT CURRENT_TIMESTAMP, paid_at TEXT, payment_reference TEXT, created_by INTEGER);
CREATE TABLE IF NOT EXISTS ledger_entries(id INTEGER PRIMARY KEY, txn_id TEXT NOT NULL, account TEXT NOT NULL, org_id INTEGER,
  direction TEXT NOT NULL CHECK(direction IN ('DEBIT','CREDIT')), amount_minor INTEGER NOT NULL CHECK(amount_minor>0),
  currency TEXT NOT NULL CHECK(length(currency)=3), reference_type TEXT NOT NULL, reference_id INTEGER NOT NULL, memo TEXT NOT NULL DEFAULT '',
  created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_ledger_txn ON ledger_entries(txn_id);
CREATE INDEX IF NOT EXISTS idx_ledger_org ON ledger_entries(org_id,id);
CREATE TABLE IF NOT EXISTS revenue_allocations(id INTEGER PRIMARY KEY,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id) {_FK},
  org_id INTEGER, beneficiary TEXT NOT NULL CHECK(beneficiary IN ('PUBLISHER','PLATFORM')),
  amount_minor INTEGER NOT NULL CHECK(amount_minor>=0), currency TEXT NOT NULL CHECK(length(currency)=3),
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','PAYABLE','PAID')),
  paid_at TEXT, payout_reference TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(invoice_id,beneficiary));
CREATE TABLE IF NOT EXISTS idempotency_keys(org_id INTEGER NOT NULL, endpoint TEXT NOT NULL, idem_key TEXT NOT NULL, request_hash TEXT NOT NULL,
  status_code INTEGER, response_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(org_id,endpoint,idem_key));
'''


def ledger_append_only(c):
    """The ledger refuses UPDATE and DELETE in the database itself, not only in code."""
    if c.kind == 'sqlite':
        c.executescript('''
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger_entries BEGIN SELECT RAISE(ABORT, 'ledger entries are append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger_entries BEGIN SELECT RAISE(ABORT, 'ledger entries are append-only'); END;
''')
    else:
        c.raw.execute("CREATE OR REPLACE FUNCTION ledger_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'ledger entries are append-only' USING ERRCODE = '23514'; END $$")
        c.raw.execute('DROP TRIGGER IF EXISTS ledger_no_change ON ledger_entries')
        c.raw.execute('CREATE TRIGGER ledger_no_change BEFORE UPDATE OR DELETE ON ledger_entries FOR EACH ROW EXECUTE FUNCTION ledger_append_only()')


# ---------------------------------------------------------------- 0015: delivery records, evidence, analytics events
DELIVERY_SQL = f'''
CREATE TABLE IF NOT EXISTS delivery_records(id INTEGER PRIMARY KEY,
  placement_request_id INTEGER NOT NULL REFERENCES placement_requests(id) {_FK},
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id) {_FK},
  job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id) {_FK},
  brand_org_id INTEGER NOT NULL, publisher_org_id INTEGER NOT NULL,
  output_sha256 TEXT NOT NULL, output_bytes INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'DELIVERED' CHECK(status IN ('DELIVERED','ACCEPTED','REVOKED')),
  delivered_at TEXT DEFAULT CURRENT_TIMESTAMP, accepted_at TEXT, accepted_by INTEGER);
CREATE TABLE IF NOT EXISTS delivery_evidence(id INTEGER PRIMARY KEY,
  delivery_id INTEGER NOT NULL REFERENCES delivery_records(id) {_FK},
  kind TEXT NOT NULL, content_json TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS analytics_events(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, campaign_id INTEGER,
  event_type TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL, value_minor INTEGER, properties_json TEXT NOT NULL DEFAULT '{{}}',
  source_event_id TEXT UNIQUE, occurred_at TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_analytics_campaign ON analytics_events(campaign_id,event_type);
'''


# ---------------------------------------------------------------- 0016: intelligence v2 (M14)
INTELLIGENCE_V2_COLUMNS = (
    ('detected_objects', 'appearance_json', 'TEXT'), ('object_tracks', 'track_key', 'INTEGER'), ('object_tracks', 'confidence', 'REAL'),
    ('object_tracks', 'observed_fraction', 'REAL'), ('object_tracks', 'gaps_json', "TEXT NOT NULL DEFAULT '[]'"),
)
INTELLIGENCE_V2_SQL = '''
CREATE INDEX IF NOT EXISTS idx_detected_objects_analysis ON detected_objects(analysis_version_id, scene_id);
CREATE INDEX IF NOT EXISTS idx_object_tracks_analysis ON object_tracks(analysis_version_id, scene_id);
INSERT INTO scoring_configs(version,weights_json,notes) SELECT 'v2',
  '{"visual_feasibility":0.15,"available_area":0.10,"visibility":0.12,"exposure_duration":0.12,"perspective":0.06,"occlusion":0.10,"lighting":0.06,"camera_motion":0.06,"contextual_relevance":0.08,"brand_safety":0.08,"content_rights":0.07}',
  'Heuristic weights over measured evidence (tracked stability, perspective, occlusion, lighting, camera motion, object context). Not a validated prediction of performance.'
  WHERE NOT EXISTS (SELECT 1 FROM scoring_configs WHERE version='v2');
'''


# ---------------------------------------------------------------- 0017: payments through a provider (M15)
PAYMENTS_SQL = f'''
CREATE TABLE IF NOT EXISTS payment_attempts(id INTEGER PRIMARY KEY,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id) {_FK},
  org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  provider TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('test','live')), provider_ref TEXT,
  status TEXT NOT NULL DEFAULT 'CREATED' CHECK(status IN ('CREATED','SUCCEEDED','FAILED','EXPIRED')),
  amount_minor INTEGER NOT NULL CHECK(amount_minor>0), currency TEXT NOT NULL CHECK(length(currency)=3), checkout_url TEXT,
  created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_attempts_ref ON payment_attempts(provider, provider_ref);
CREATE INDEX IF NOT EXISTS idx_payment_attempts_invoice ON payment_attempts(invoice_id);
CREATE TABLE IF NOT EXISTS payment_events(id INTEGER PRIMARY KEY, provider TEXT NOT NULL, event_id TEXT NOT NULL, type TEXT NOT NULL,
  provider_ref TEXT, attempt_id INTEGER REFERENCES payment_attempts(id) {_FK}, result TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
  payload_sha256 TEXT NOT NULL, received_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(provider, event_id));
CREATE TABLE IF NOT EXISTS agency_clients(id INTEGER PRIMARY KEY,
  agency_org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  client_org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','ACTIVE','REVOKED','DECLINED')),
  role TEXT NOT NULL DEFAULT 'manager' CHECK(role IN ('viewer','editor','manager')),
  requested_by INTEGER, decided_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, decided_at TEXT,
  CHECK(agency_org_id<>client_org_id));
CREATE UNIQUE INDEX IF NOT EXISTS idx_agency_clients_open ON agency_clients(agency_org_id, client_org_id) WHERE status IN ('PENDING','ACTIVE');
'''


# ---------------------------------------------------------------- 0020: plan subscriptions (Lemon Squeezy)
SUBSCRIPTIONS_SQL = f'''
CREATE TABLE IF NOT EXISTS billing_checkouts(id INTEGER PRIMARY KEY,
  org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  token TEXT NOT NULL UNIQUE, plan TEXT NOT NULL, billing_interval TEXT NOT NULL CHECK(billing_interval IN ('monthly','annual')),
  variant_id TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('test','live')), provider_checkout_id TEXT,
  created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS subscriptions(id INTEGER PRIMARY KEY,
  org_id INTEGER NOT NULL REFERENCES organizations(id) {_FK},
  provider TEXT NOT NULL, provider_ref TEXT NOT NULL, customer_ref TEXT, variant_id TEXT, plan TEXT NOT NULL, billing_interval TEXT,
  status TEXT NOT NULL, renews_at TEXT, ends_at TEXT, test_mode INTEGER NOT NULL DEFAULT 0, card_brand TEXT, card_last_four TEXT,
  provider_updated_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(provider, provider_ref));
CREATE INDEX IF NOT EXISTS idx_subscriptions_org ON subscriptions(org_id);
'''


MIGRATIONS = [
    Migration('0001_organization_kinds', 'Organization kind (direct, brand, publisher, agency) and platform administrator flag',
              sql=ORG_KINDS_SQL, columns=ORG_KIND_COLUMNS),
    Migration('0002_memberships', 'Memberships table, backfilled from users.org_id and member_role', sql=MEMBERSHIPS_SQL),
    Migration('0003_video_versions', 'Video versions; every existing video becomes version 1', sql=VIDEO_VERSIONS_SQL, columns=VIDEO_VERSION_COLUMNS),
    Migration('0004_analysis_versions', 'Analysis versions, scenes and scene frames; existing keyframes become analysis version 1',
              sql=ANALYSIS_SQL, run=backfill_analyses),
    Migration('0005_opportunities', 'Typed placement opportunities', sql=OPPORTUNITIES_SQL),
    Migration('0006_outbox', 'Transactional outbox and consumer records', sql=OUTBOX_SQL),
    Migration('0007_session_active_org', 'Active organization per session', columns=(('sessions', 'active_org_id', 'INTEGER'),)),
    Migration('0008_video_assets', 'Video deletion and file metadata (container, audio, aspect ratio)',
              columns=(('videos', 'deleted_at', 'TEXT'), ('video_versions', 'container', 'TEXT'), ('video_versions', 'has_audio', 'INTEGER'),
                       ('video_versions', 'aspect_ratio', 'TEXT'))),
    Migration('0009_analysis_jobs', 'Analysis as a staged background job (stage, leases, attempts, cancellation); one active analysis per video version; detection tables',
              sql=ANALYSIS_JOB_SQL, columns=ANALYSIS_JOB_COLUMNS),
    Migration('0010_scoring_inventory', 'Versioned scoring configurations, brand-safety rules, inventory items', sql=SCORING_SQL, columns=SCORING_COLUMNS, run=seed_safety_terms),
    Migration('0011_profiles_rights', 'Brand and publisher profiles; rights records with evidence and verification', sql=PROFILES_SQL),
    Migration('0012_catalogue', 'Product states and asset versions; persisted scene recommendations', sql=CATALOGUE_SQL, columns=CATALOGUE_COLUMNS),
    Migration('0013_marketplace', 'Campaigns, targeting, audited transitions, reservations (one active per item), placement requests, notifications', sql=MARKETPLACE_SQL),
    Migration('0014_commercial', 'Quotes, invoices, append-only ledger, revenue allocations, idempotency keys', sql=COMMERCIAL_SQL, run=ledger_append_only),
    Migration('0015_delivery', 'Delivery records, delivery evidence, analytics events', sql=DELIVERY_SQL),
    Migration('0016_intelligence_v2', 'Object detection and tracking outputs (appearance, track confidence and gaps); scoring configuration v2',
              sql=INTELLIGENCE_V2_SQL, columns=INTELLIGENCE_V2_COLUMNS),
    Migration('0017_payments_agencies', 'Payment attempts and provider events (webhook idempotency); agency-to-client delegation grants', sql=PAYMENTS_SQL),
    Migration('0018_version_uploads', 'Direct-to-storage uploads of a new version of an existing video', columns=(('upload_sessions', 'target_video_id', 'INTEGER'),)),
    Migration('0019_integration_checks', 'Contract verification results of programmatic adapters', sql='''
CREATE TABLE IF NOT EXISTS integration_checks(id INTEGER PRIMARY KEY, adapter TEXT NOT NULL, kind TEXT NOT NULL, external INTEGER NOT NULL DEFAULT 0,
  passed INTEGER NOT NULL, detail TEXT NOT NULL DEFAULT '', checked_at_epoch REAL NOT NULL, checked_by INTEGER, checked_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_integration_checks_adapter ON integration_checks(adapter, id);
'''),
    Migration('0020_subscriptions', 'Plan subscriptions through Lemon Squeezy: checkouts created here and subscription state per workspace', sql=SUBSCRIPTIONS_SQL),
]


class MigrationError(RuntimeError):
    pass


def ddl():
    """All migration DDL, so callers that list tables with generated ids (app.db.id_tables) see the new tables."""
    return ''.join(m.sql for m in MIGRATIONS)


def fingerprint():
    return hashlib.sha256(json.dumps([(m.id, m.checksum()) for m in MIGRATIONS]).encode()).hexdigest()


def _check_order():
    ids = [m.id for m in MIGRATIONS]
    if ids != sorted(ids) or len(set(ids)) != len(ids): raise MigrationError(f'Migration ids must be unique and in order: {ids}')


def applied(c):
    return {r['id']: r['checksum'] for r in c.execute('SELECT id, checksum FROM schema_migrations').fetchall()}


def apply(c):
    """Apply pending migrations in order. Returns the ids applied by this call."""
    _check_order()
    c.execute("CREATE TABLE IF NOT EXISTS schema_migrations(id TEXT PRIMARY KEY, description TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    done = applied(c)
    unknown = sorted(set(done) - {m.id for m in MIGRATIONS})
    if unknown: raise MigrationError(f'The database has migrations this code does not know ({", ".join(unknown)}): it was migrated by a newer version')
    ran = []
    for m in MIGRATIONS:
        if m.id in done:
            if done[m.id] != m.checksum(): raise MigrationError(f'Migration {m.id} was changed after it was applied; add a new migration instead')
            continue
        if m.sql.strip(): c.executescript(m.sql)
        for table, column, definition in m.columns: c.add_column(table, column, definition)
        if c.kind == 'sqlite':
            c.lock('schema-migrations')               # another process may have finished this migration meanwhile
            if c.execute('SELECT 1 FROM schema_migrations WHERE id=?', (m.id,)).fetchone(): c.commit(); continue
        if m.run: m.run(c)
        if c.kind == 'postgres':
            # Rows a backfill wrote leave deferred foreign-key checks pending, and PostgreSQL refuses to ALTER a table that
            # has them, which a later migration in this same transaction may need to do. Checking them now clears them,
            # and a bad reference stops the upgrade at the migration that wrote it.
            c.execute('SET CONSTRAINTS ALL IMMEDIATE'); c.execute('SET CONSTRAINTS ALL DEFERRED')
        c.execute('INSERT INTO schema_migrations(id,description,checksum) VALUES(?,?,?) ON CONFLICT(id) DO NOTHING', (m.id, m.description, m.checksum()))
        if c.kind == 'sqlite': c.commit()             # PostgreSQL keeps one transaction so the advisory lock holds to the end
        ran.append(m.id)
    return ran
