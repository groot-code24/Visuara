from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager
import secrets, hashlib, hmac, os, json, shutil, subprocess, time, mimetypes, threading, socket, struct, base64, urllib.request, urllib.parse, logging, re

ROOT = Path(__file__).resolve().parent.parent
from .config import load_dotenv
load_dotenv()

class _RedactSignedLinks(logging.Filter):
    """Signed download tokens are bearer credentials: redact them from access logs."""
    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(re.sub(r'/api/media/[^ ?"]+', '/api/media/[REDACTED]', a) if isinstance(a, str) else a for a in record.args)
        return True
logging.getLogger('uvicorn.access').addFilter(_RedactSignedLinks())
DATA = Path(os.getenv('CC_DATA_DIR', ROOT / 'data')).resolve()
UPLOADS = DATA / 'uploads'; OUTPUTS = DATA / 'outputs'; FRAMES = DATA / 'frames'
for p in (DATA, UPLOADS, OUTPUTS, FRAMES): p.mkdir(parents=True, exist_ok=True)
DB = DATA / 'context_commerce.sqlite3'
from . import lifecycle, renderers, media
from . import db as dbm, storage, limits, obs
obs.configure_logging()
from .compositor import RenderCancelled, render_plan
from .validation import validate_plan
app = FastAPI(title='Context Commerce MVP', version='0.6.0', description='Authorized creator video product-placement workflow')
app.mount('/static', StaticFiles(directory=ROOT / 'app' / 'static'), name='static')

def _origin(url):
    p=urllib.parse.urlsplit(url or ''); return f'{p.scheme}://{p.netloc}'.lower() if p.scheme and p.netloc else ''
def allowed_origins():
    """Exact origins allowed to make credentialed browser requests: CC_CORS_ORIGINS plus CC_APP_URL/CC_PUBLIC_URL."""
    out={_origin(x.strip()) for x in os.getenv('CC_CORS_ORIGINS','').split(',') if x.strip()}
    out|={_origin(os.getenv(k,'')) for k in ('CC_APP_URL','CC_PUBLIC_URL')}
    out.discard(''); return out
def _csp():
    extra=' '.join(sorted({_origin(os.getenv(k,'')) for k in ('CC_APP_URL','CC_PUBLIC_URL','CC_S3_PUBLIC_ENDPOINT_URL','CC_S3_ENDPOINT_URL')}-{''}))
    return (f"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; "
            f"img-src 'self' data: blob: https: {extra}; media-src 'self' blob: https: {extra}; connect-src 'self' https: {extra}; frame-ancestors 'none'; base-uri 'self'; "
            f"form-action 'self' {extra}; object-src 'none'")
_MUTATING=('POST','PUT','PATCH','DELETE')
@app.middleware('http')
async def security_middleware(request: Request, call_next):
    path=request.url.path; supplied_rid=request.headers.get('x-request-id','')
    rid=supplied_rid if re.fullmatch(r'[A-Za-z0-9._-]{8,64}',supplied_rid) else secrets.token_hex(8)
    rid_token=obs.request_id.set(rid); started=time.perf_counter()
    try:
        response=None
        if path in ('/api/auth/login','/api/auth/register','/api/auth/email/start','/api/auth/email/verify') and request.method=='POST':
            ip=request.client.host if request.client else 'unknown'
            if not rate_limit('auth:'+ip,int(os.getenv('CC_AUTH_RATE_PER_MINUTE','12')),60): response=JSONResponse({'detail':'Too many authentication attempts. Try again in a minute.'},status_code=429)
        if response is None and request.method in _MUTATING:
            origin=(request.headers.get('origin') or '').lower()
            # Browsers always send Origin on cross-origin writes; refuse any origin that is not this site or allowlisted.
            if origin and origin!='null' and origin not in allowed_origins() and origin!=_origin(str(request.base_url)):
                response=JSONResponse({'detail':'Cross-origin request refused'},status_code=403)
            # /auth/* are browser form posts (magic-link confirmation) that check a CSRF form field themselves.
            elif path not in ('/api/auth/login','/api/auth/register') and not path.startswith('/auth/'):
                csrf=request.cookies.get('cc_csrf'); supplied=request.headers.get('x-csrf-token')
                if request.cookies.get('cc_session') and (not csrf or not supplied or not hmac.compare_digest(csrf,supplied)):
                    response=JSONResponse({'detail':'CSRF token missing or invalid. Refresh the page and retry.'},status_code=403)
        if response is None:
            try: response=await call_next(request)
            except Exception:
                obs.error('unhandled error',exc_info=True,method=request.method,path=path)
                response=JSONResponse({'detail':'Internal error. Quote this request id to support.','request_id':rid},status_code=500)
        if path in ('/','/app','/api/auth/login','/api/auth/register') and not request.cookies.get('cc_csrf'):
            response.set_cookie('cc_csrf',secrets.token_urlsafe(24),httponly=False,samesite='lax',secure=os.getenv('CC_SECURE_COOKIES','0')=='1',path='/')
        h=response.headers; h['X-Request-ID']=rid
        h.setdefault('X-Content-Type-Options','nosniff'); h.setdefault('Referrer-Policy','strict-origin-when-cross-origin'); h.setdefault('X-Frame-Options','DENY')
        h.setdefault('Permissions-Policy','camera=(), microphone=(), geolocation=(), payment=()')
        if os.getenv('CC_SECURE_COOKIES','0')=='1': h.setdefault('Strict-Transport-Security','max-age=31536000; includeSubDomains')
        if path.startswith(('/api/','/auth/')) and 'cache-control' not in h: h['Cache-Control']='no-store'
        if path.startswith(('/api/','/auth/')): h.setdefault('x-vercel-enable-rewrite-caching','0')
        if h.get('content-type','').startswith('text/html'): h.setdefault('Content-Security-Policy',_csp())
        if path not in ('/api/health/live',):
            obs.info('request',method=request.method,path=path,status=response.status_code,ms=round((time.perf_counter()-started)*1000,1))
        return response
    finally:
        obs.request_id.reset(rid_token)
if os.getenv('CC_CORS_ORIGINS'):
    from starlette.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware,allow_origins=sorted(allowed_origins()),allow_credentials=True,allow_methods=['GET','POST','PUT','PATCH','DELETE'],
                       allow_headers=['Content-Type','X-CSRF-Token','X-Request-ID'],expose_headers=['X-Request-ID','Content-Disposition'],max_age=600)

LEASE_SECONDS=int(os.getenv('CC_JOB_LEASE_SECONDS','120'))
MAX_ANALYSIS_SECONDS=float(os.getenv('CC_MAX_ANALYSIS_SECONDS','60'))

def recover_expired_leases(c):
    """Requeue RUNNING jobs whose worker stopped renewing its lease; dead-letter them once retries are exhausted."""
    rows=c.execute("SELECT id,attempts,max_attempts,placement_id,placement_version FROM jobs WHERE status='RUNNING' AND (lease_until IS NULL OR lease_until<?)",(time.time(),)).fetchall()
    for r in rows:
        if r['attempts']<r['max_attempts']:
            cur=c.execute("UPDATE jobs SET status='QUEUED',lease_token=NULL,error='Recovered expired worker lease' WHERE id=? AND status='RUNNING'",(r['id'],)); kind='lease_recovered'
        else:
            cur=c.execute("UPDATE jobs SET status='DEAD_LETTER',lease_token=NULL,error='Worker lease expired after retry budget',finished_at=CURRENT_TIMESTAMP WHERE id=? AND status='RUNNING'",(r['id'],)); kind='dead_lettered'
            _placement_render_failed(c,r['placement_id'],r['placement_version'],'Worker lease expired after retry budget')
        if cur.rowcount: c.execute('INSERT INTO queue_events(job_id,event_type,details) VALUES(?,?,?)',(r['id'],kind,'{}'))

def claim_next_job():
    """Atomically claim a queued job. Returns (job_id, lease_token), (None, None) when the queue is empty, or
    (None, 'retry') when another worker won the race. Organizations with fewer running jobs go first, so one busy
    workspace cannot starve the others."""
    with db() as c:
        recover_expired_leases(c)
        row=c.execute("SELECT j.id FROM jobs j WHERE j.status='QUEUED' ORDER BY (SELECT COUNT(*) FROM jobs r WHERE r.org_id=j.org_id AND r.status='RUNNING'), j.id LIMIT 1").fetchone()
        if not row: return None,None
        token=secrets.token_hex(12)
        cur=c.execute("UPDATE jobs SET status='RUNNING',attempts=attempts+1,lease_until=?,lease_token=?,started_at=CURRENT_TIMESTAMP WHERE id=? AND status='QUEUED'",(time.time()+LEASE_SECONDS,token,row['id']))
        if not cur.rowcount: return None,'retry'
        c.execute('INSERT INTO queue_events(job_id,event_type,details) VALUES(?,?,?)',(row['id'],'claimed',json.dumps({'pid':os.getpid()})))
        return row['id'],token

def worker_max_idle(): return float(os.getenv('CC_WORKER_MAX_IDLE_SECONDS','900'))

def _worker_loop():
    """Claim and render jobs. When the queue is empty the poll interval doubles from 0.5 s up to CC_WORKER_MAX_IDLE_SECONDS
    (default 900 s, longer than Neon's 5-minute scale-to-zero window). The web process touches a wake-up file on the
    shared data volume whenever it queues a job, so new work starts within about half a second without database polling."""
    from . import maintenance
    idle=0.5; last_wake=obs.wake_mtime(); last_beat=0.0; last_cache=time.time(); last_db_maint=time.time()
    obs.info('worker started',max_idle_seconds=worker_max_idle(),pid=os.getpid())
    while not _WORKER_STOP.is_set():
        try:
            obs.worker_heartbeat('polling'); last_beat=time.time()
            job_id,token=claim_next_job()
            if job_id is not None:
                obs.worker_heartbeat('rendering'); render_job(job_id,token); idle=0.5; continue
            if token=='retry': continue
            if time.time()-last_db_maint>maintenance.db_interval():   # piggybacks on a poll that already woke the database
                maintenance.run_db_maintenance(); last_db_maint=time.time()
            idle=min(worker_max_idle(),idle*2)
        except Exception:
            obs.error('worker loop error',exc_info=True); idle=min(worker_max_idle(),max(idle,5.0))
        deadline=time.time()+idle
        while not _WORKER_STOP.is_set() and time.time()<deadline:
            now=time.time()
            if now-last_beat>30: obs.worker_heartbeat('idle'); last_beat=now
            if now-last_cache>maintenance.cache_interval(): maintenance.run_cache_maintenance(); last_cache=now
            w=obs.wake_mtime()
            if w!=last_wake: last_wake=w; idle=0.5; break
            _WORKER_STOP.wait(min(0.5,max(0.0,deadline-now)))
    obs.info('worker stopped',pid=os.getpid())

@asynccontextmanager
async def lifespan(_app):
    global _WORKER_THREAD
    obs.configure_logging()
    _WORKER_STOP.clear()
    if os.getenv('CC_INLINE_WORKER','1')=='1' and (not _WORKER_THREAD or not _WORKER_THREAD.is_alive()):
        _WORKER_THREAD=threading.Thread(target=_worker_loop,name='cc-render-worker',daemon=True); _WORKER_THREAD.start()
    yield
    _WORKER_STOP.set()
    if _WORKER_THREAD and _WORKER_THREAD.is_alive(): _WORKER_THREAD.join(timeout=5)
app.router.lifespan_context=lifespan

SCHEMA = '''
CREATE TABLE IF NOT EXISTS organizations(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('brand','creator')), org_id INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS invitations(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, email TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('brand','creator')), token_hash TEXT UNIQUE NOT NULL, expires_at INTEGER NOT NULL, accepted_at TEXT, created_by INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT DEFAULT '', image_path TEXT NOT NULL, created_by INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS videos(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, title TEXT NOT NULL, source_path TEXT NOT NULL, duration REAL NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, fps REAL DEFAULT 0, license_confirmed INTEGER NOT NULL DEFAULT 0, license_expires TEXT, analyzed INTEGER NOT NULL DEFAULT 0, created_by INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS frames(id INTEGER PRIMARY KEY, video_id INTEGER NOT NULL, second REAL NOT NULL, image_path TEXT NOT NULL, description TEXT NOT NULL, score REAL NOT NULL);
CREATE TABLE IF NOT EXISTS placements(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, video_id INTEGER NOT NULL, product_id INTEGER NOT NULL, frame_second REAL NOT NULL, x REAL NOT NULL, y REAL NOT NULL, width REAL NOT NULL, start_second REAL NOT NULL, end_second REAL NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT', version INTEGER NOT NULL DEFAULT 1, creator_approved INTEGER NOT NULL DEFAULT 0, brand_approved INTEGER NOT NULL DEFAULT 0, created_by INTEGER NOT NULL, render_options TEXT NOT NULL DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, placement_id INTEGER NOT NULL, org_id INTEGER NOT NULL, status TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'COMPOSITE', output_path TEXT, error TEXT, started_at TEXT, finished_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, user_id INTEGER NOT NULL, event_type TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL, details TEXT DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status,id);
CREATE INDEX IF NOT EXISTS idx_events_org ON events(org_id,id);
CREATE TABLE IF NOT EXISTS rate_limits(bucket_key TEXT NOT NULL, hit_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_rate_limits_key_time ON rate_limits(bucket_key,hit_at);
CREATE TABLE IF NOT EXISTS generative_jobs(id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL UNIQUE, provider TEXT NOT NULL, prediction_id TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS placement_approvals(id INTEGER PRIMARY KEY, placement_id INTEGER NOT NULL, placement_version INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL, approved INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(placement_id,placement_version,user_id));
CREATE TABLE IF NOT EXISTS placement_rights(id INTEGER PRIMARY KEY, placement_id INTEGER NOT NULL UNIQUE, placement_version INTEGER NOT NULL, permitted_channels TEXT NOT NULL DEFAULT '["social","web"]', territories TEXT NOT NULL DEFAULT '["WORLDWIDE"]', start_at TEXT, expires_at TEXT, creator_consent INTEGER NOT NULL DEFAULT 0, revoked_at TEXT, revocation_reason TEXT, updated_by INTEGER NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS surface_proposals(id INTEGER PRIMARY KEY, video_id INTEGER NOT NULL, org_id INTEGER NOT NULL, frame_second REAL NOT NULL, quad_json TEXT NOT NULL, confidence REAL NOT NULL, fit_score REAL NOT NULL, status TEXT NOT NULL DEFAULT 'PROPOSED', evidence_json TEXT NOT NULL DEFAULT '{}', reviewed_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS tracked_links(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, placement_id INTEGER NOT NULL, placement_version INTEGER NOT NULL, slug TEXT UNIQUE NOT NULL, destination_url TEXT NOT NULL, campaign TEXT DEFAULT '', created_by INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS conversions(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, link_id INTEGER, external_id TEXT, event_name TEXT NOT NULL, value REAL DEFAULT 0, currency TEXT DEFAULT 'USD', occurred_at TEXT, source TEXT DEFAULT 'import', dedupe_key TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(org_id,dedupe_key));
CREATE TABLE IF NOT EXISTS queue_events(id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, event_type TEXT NOT NULL, details TEXT DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_approvals_version ON placement_approvals(placement_id,placement_version);
CREATE INDEX IF NOT EXISTS idx_surface_video ON surface_proposals(video_id,org_id);
CREATE INDEX IF NOT EXISTS idx_conversions_org ON conversions(org_id,created_at);
CREATE TABLE IF NOT EXISTS placement_versions(id INTEGER PRIMARY KEY, placement_id INTEGER NOT NULL, version INTEGER NOT NULL, snapshot_json TEXT NOT NULL, content_hash TEXT NOT NULL, created_by INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(placement_id,version));
CREATE TABLE IF NOT EXISTS placement_analyses(id INTEGER PRIMARY KEY, placement_id INTEGER NOT NULL, placement_version INTEGER NOT NULL, content_hash TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('PASSED','FAILED')), scene_class TEXT NOT NULL, policy_version TEXT NOT NULL, report_json TEXT NOT NULL, track_path TEXT, track_sha256 TEXT, created_by INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_analyses_placement ON placement_analyses(placement_id,placement_version);
CREATE TABLE IF NOT EXISTS upload_sessions(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, user_id INTEGER NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', content_type TEXT NOT NULL, expected_bytes INTEGER NOT NULL, license_expires TEXT, status TEXT NOT NULL DEFAULT 'PENDING', result_id INTEGER, error TEXT, expires_at INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, finished_at TEXT);
CREATE INDEX IF NOT EXISTS idx_upload_sessions ON upload_sessions(org_id,status);
CREATE TABLE IF NOT EXISTS payment_records(id INTEGER PRIMARY KEY, offer_id INTEGER NOT NULL, kind TEXT NOT NULL, amount_usd REAL NOT NULL, reference TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', recorded_by TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_payment_records_offer ON payment_records(offer_id,id);
'''
_INDEXES=['CREATE INDEX IF NOT EXISTS idx_users_org ON users(org_id)','CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)',
          'CREATE INDEX IF NOT EXISTS idx_jobs_org_status ON jobs(org_id,status)','CREATE INDEX IF NOT EXISTS idx_videos_org ON videos(org_id)',
          'CREATE INDEX IF NOT EXISTS idx_products_org ON products(org_id)','CREATE INDEX IF NOT EXISTS idx_placements_org ON placements(org_id)',
          'CREATE INDEX IF NOT EXISTS idx_placements_video ON placements(video_id)','CREATE INDEX IF NOT EXISTS idx_frames_video ON frames(video_id,second)',
          'CREATE INDEX IF NOT EXISTS idx_invitations_email ON invitations(email)','CREATE INDEX IF NOT EXISTS idx_queue_events_job ON queue_events(job_id)']
DB_SCHEMA=os.getenv('CC_DATABASE_SCHEMA') or None   # PostgreSQL schema (search_path); tests use one per test
_ID_TABLES=frozenset()
def db():
    """SQLite file at DB by default; PostgreSQL (e.g. Neon) when DATABASE_URL is set."""
    return dbm.connect(DB, schema=DB_SCHEMA, id_tabs=_ID_TABLES)

def all_schemas():
    from .commerce import SCHEMA as COMMERCE_SCHEMA
    from .auth import SCHEMA as AUTH_SCHEMA
    return SCHEMA, AUTH_SCHEMA, COMMERCE_SCHEMA

def init_db(connect=None):
    """Create or migrate the schema (idempotent). `connect` targets another database, e.g. for migrations."""
    global _ID_TABLES
    _, AUTH_SCHEMA, COMMERCE_SCHEMA = all_schemas()
    _ID_TABLES=frozenset(dbm.id_tables(SCHEMA+COMMERCE_SCHEMA+AUTH_SCHEMA))
    with (connect or db)() as c:
        if c.kind=='postgres': c.lock('schema-init')   # API and workers may start together
        c.executescript(SCHEMA)
        c.executescript(AUTH_SCHEMA)
        c.executescript(COMMERCE_SCHEMA)
        # Idempotent migrations for databases created by earlier MVP versions.
        for table, column, definition in [('placements','render_options',"TEXT NOT NULL DEFAULT '{}'"),('jobs','mode',"TEXT NOT NULL DEFAULT 'COMPOSITE'"),('placements','fit_confidence','REAL NOT NULL DEFAULT 0.0'),('placements','fit_status',"TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED'"),('jobs','attempts','INTEGER NOT NULL DEFAULT 0'),('jobs','max_attempts','INTEGER NOT NULL DEFAULT 3'),('jobs','cancel_requested','INTEGER NOT NULL DEFAULT 0'),('jobs','lease_until','REAL'),('jobs','lease_token','TEXT'),('jobs','placement_version','INTEGER'),('jobs','analysis_id','INTEGER'),('jobs','renderer','TEXT'),('jobs','validation_json','TEXT'),('placements','quality_status',"TEXT NOT NULL DEFAULT 'NOT_EVALUATED'"),('placements','analysis_id','INTEGER'),('placements','content_hash','TEXT'),('placement_approvals','content_hash','TEXT'),('tracked_links','channel',"TEXT NOT NULL DEFAULT 'social'"),('products','sha256','TEXT'),('products','derived_path','TEXT'),('products','asset_report',"TEXT NOT NULL DEFAULT '{}'"),('products','model_path','TEXT'),('products','model_sha256','TEXT'),('products','model_report','TEXT'),
                                          ('users','member_role',"TEXT NOT NULL DEFAULT 'manager'"),('users','suspended_at','TEXT'),('users','removed_at','TEXT'),
                                          ('invitations','member_role',"TEXT NOT NULL DEFAULT 'manager'"),('invitations','revoked_at','TEXT'),
                                          ('videos','size_bytes','INTEGER NOT NULL DEFAULT 0'),('products','size_bytes','INTEGER NOT NULL DEFAULT 0'),
                                          ('jobs','output_bytes','INTEGER NOT NULL DEFAULT 0'),('offers','payment_status',"TEXT NOT NULL DEFAULT 'NOT_INVOICED'")]:
            c.add_column(table,column,definition)
        # V10: every workspace has exactly one owner (the earliest member of workspaces created before member roles).
        c.execute("UPDATE users SET member_role='owner' WHERE id IN (SELECT MIN(id) FROM users GROUP BY org_id) AND org_id NOT IN (SELECT org_id FROM users WHERE member_role='owner')")
        for ddl in _INDEXES: c.execute(ddl)
init_db()

_RATE_BUCKETS = {}
_RATE_LOCK = threading.Lock()
_WORKER_STOP = threading.Event()
_WORKER_THREAD = None
def rate_limit(key, limit, window):
    # Shared SQLite-backed limiter works across API worker processes on one shared DB.
    # For multi-host production, replace this with Redis or an API gateway limiter.
    now=time.time()
    try:
        with db() as c:
            c.lock('rate_limits')
            c.execute('DELETE FROM rate_limits WHERE hit_at < ?', (now-max(window,3600),))
            count=c.execute('SELECT COUNT(*) FROM rate_limits WHERE bucket_key=? AND hit_at>?',(key,now-window)).fetchone()[0]
            if count>=limit: c.rollback(); return False
            c.execute('INSERT INTO rate_limits(bucket_key,hit_at) VALUES(?,?)',(key,now)); c.commit(); return True
    except dbm.OperationalError:
        # Fail closed if shared rate-limit persistence is unavailable.
        return False

def clamav_scan(path):
    if not os.getenv('CC_CLAMAV_HOST'): return {'enabled':False,'clean':None}
    try:
        with socket.create_connection((os.getenv('CC_CLAMAV_HOST','127.0.0.1'),int(os.getenv('CC_CLAMAV_PORT','3310'))),timeout=5) as sock:
            sock.sendall(b'zINSTREAM\0')
            with open(path,'rb') as f:
                while True:
                    chunk=f.read(65536)
                    if not chunk: break
                    sock.sendall(struct.pack('!I',len(chunk))+chunk)
            sock.sendall(struct.pack('!I',0)); result=sock.recv(4096).decode('utf-8','replace')
        if 'FOUND' in result: return {'enabled':True,'clean':False,'result':result.strip()}
        if 'OK' in result: return {'enabled':True,'clean':True,'result':result.strip()}
        raise RuntimeError('Unexpected ClamAV response: '+result)
    except Exception as e:
        if os.getenv('CC_CLAMAV_REQUIRED','0')=='1': raise HTTPException(503,'Malware scanner unavailable: '+str(e)[:150])
        return {'enabled':True,'clean':None,'error':str(e)[:150]}


def hash_password(password: str) -> str:
    salt=secrets.token_bytes(16); digest=hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 180000)
    return salt.hex()+':'+digest.hex()
def verify_password(password, stored):
    try:
        salt, expected=stored.split(':'); actual=hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 180000).hex(); return hmac.compare_digest(actual, expected)
    except Exception: return False

def event(c, user, typ, etype, eid, details=None):
    c.execute('INSERT INTO events(org_id,user_id,event_type,entity_type,entity_id,details) VALUES(?,?,?,?,?,?)',(user['org_id'],user['id'],typ,etype,eid,json.dumps(details or {})))

def rights_are_active(rights, channel=None):
    if not rights or not rights['creator_consent'] or rights['revoked_at']:
        return False
    today=time.strftime('%Y-%m-%d')
    if rights['start_at'] and rights['start_at'][:10] > today: return False
    if rights['expires_at'] and rights['expires_at'][:10] < today: return False
    if channel and channel not in json.loads(rights['permitted_channels'] or '[]'): return False
    return True
def session_cookie(token, max_age=604800):
    return f'cc_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}'+('; Secure' if os.getenv('CC_SECURE_COOKIES','0')=='1' else '')
def current_user(request: Request):
    token=request.cookies.get('cc_session')
    if not token: raise HTTPException(401,'Please sign in')
    with db() as c:
        row=c.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
        if not row: raise HTTPException(401,'Session expired. Please sign in again.')
    from .members import authorize
    user=dict(row); authorize(user,request)
    return user
def require_role(user, *roles):
    if user['role'] not in roles: raise HTTPException(403,'Your account role cannot perform this action')
def owned(c, table, item_id, org_id):
    # Table names are internal constants only.
    row=c.execute(f'SELECT * FROM {table} WHERE id=? AND org_id=?',(item_id,org_id)).fetchone()
    if not row and table in ('videos','placements','products'): row=_shared(c,table,item_id,org_id)
    if not row: raise HTTPException(404,'Resource not found')
    return dict(row)

def _shared(c, table, item_id, org_id):
    """Cross-organization access. Writes stay guarded by role and rights-holder checks at each endpoint."""
    if table=='videos':      # a brand organization holding an active licence grant for this video
        from .commerce import active_grant
        return c.execute('SELECT * FROM videos WHERE id=?',(item_id,)).fetchone() if active_grant(c,item_id,org_id) else None
    if table=='placements':  # the creator organization that owns the placement's video
        return c.execute('SELECT p.* FROM placements p JOIN videos v ON v.id=p.video_id WHERE p.id=? AND v.org_id=?',(item_id,org_id)).fetchone()
    if table=='products':    # a creator organization reviewing a placement that uses this product
        return c.execute('SELECT pr.* FROM products pr WHERE pr.id=? AND EXISTS(SELECT 1 FROM placements p JOIN videos v ON v.id=p.video_id WHERE p.product_id=pr.id AND v.org_id=?)',(item_id,org_id)).fetchone()
def safe_name(name): return ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in name)[:120] or 'upload'
def tool_exists(name): return shutil.which(name) is not None
def _has_cv2():
    try: import cv2; return True
    except Exception: return False

def probe(path):
    if not tool_exists('ffprobe'): raise HTTPException(503,'FFprobe is required for video metadata. Install FFmpeg and retry.')
    p=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type,width,height,avg_frame_rate','-of','json',str(path)],capture_output=True,text=True,timeout=20)
    if p.returncode: raise HTTPException(400,'Unable to read this video. Try a valid MP4, MOV, or WebM file.')
    try:
        info=json.loads(p.stdout); v=next(s for s in info['streams'] if s.get('codec_type')=='video'); duration=float(info.get('format',{}).get('duration',0)); width=int(v['width']); height=int(v['height']); fr=v.get('avg_frame_rate','0/1').split('/'); fps=float(fr[0])/max(float(fr[1]),1)
    except Exception: raise HTTPException(400,'Video metadata is incomplete or unsupported')
    limits.check_video_metadata(duration,width,height,fps)
    return duration,width,height,fps

def _ollama_describe(image_path):
    model=os.getenv('CC_VISION_MODEL','').strip()
    if not model: return None
    try:
        payload={'model':model,'prompt':'Describe the visible scene in one concise sentence for a video product-placement editor. Mention visible objects and whether a placement area appears unobstructed. Do not infer identities.','images':[base64.b64encode(Path(image_path).read_bytes()).decode()],'stream':False}
        req=urllib.request.Request(os.getenv('CC_OLLAMA_URL','http://127.0.0.1:11434/api/generate'),data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=float(os.getenv('CC_VISION_TIMEOUT','20'))) as r: data=json.loads(r.read().decode())
        return str(data.get('response','')).strip()[:500] or None
    except Exception: return None

def extract_frames(video_id, path, duration):
    if not tool_exists('ffmpeg'): raise RuntimeError('FFmpeg is required for scene analysis and rendering')
    targets=[]
    try:
        import cv2
        cap=cv2.VideoCapture(str(path)); fps=cap.get(cv2.CAP_PROP_FPS) or 1; total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        prev=None; changes=[]; step=max(1,int(fps*.75)); idx=0
        while idx<total:
            cap.set(cv2.CAP_PROP_POS_FRAMES,idx); ok,frame=cap.read()
            if not ok: break
            small=cv2.resize(frame,(96,54)); hist=cv2.calcHist([small],[0,1,2],None,[8,8,8],[0,256,0,256,0,256]); cv2.normalize(hist,hist)
            if prev is not None:
                score=float(cv2.compareHist(prev,hist,cv2.HISTCMP_BHATTACHARYYA))
                if score>.18: changes.append((score,idx/max(fps,1)))
            prev=hist; idx+=step
        cap.release(); targets=[t for _,t in sorted(changes,reverse=True)[:5]]
    except Exception: pass
    if not targets: targets=[duration*.12,duration*.32,duration*.52,duration*.72,max(.1,duration-.5)]
    targets=sorted(set(round(max(0,min(duration-.05,t)),2) for t in targets))[:8]; results=[]
    for sec in targets:
        out=FRAMES/f'v{video_id}_{int(sec*1000)}.jpg'
        proc=subprocess.run(['ffmpeg','-y','-ss',str(sec),'-i',str(path),'-frames:v','1','-vf','scale=640:-2','-q:v','4',str(out)],capture_output=True,timeout=30)
        if proc.returncode==0 and out.exists() and out.stat().st_size:
            storage.persist(out)
            semantic=_ollama_describe(out); score=round(max(.35,1-abs(sec-duration*.5)/max(duration,1)*.45),3)
            desc=semantic or f'Candidate scene at {sec:.1f}s (scene-change/keyframe analysis; review before placement)'
            results.append((sec,str(out),desc,score))
    return results

class Register(BaseModel):
    name: str = Field(min_length=1,max_length=80)
    email: str = Field(min_length=5,max_length=254)
    password: str = Field(min_length=8,max_length=128)
    role: str
    organization: str = Field(default='',max_length=100)   # ignored when joining by invitation
    invitation_token: Optional[str] = None
class Login(BaseModel): email: str; password: str
class ProductIn(BaseModel): name: str = Field(min_length=1,max_length=120); description: str = Field(default='',max_length=1000)
class PlacementIn(BaseModel):
    video_id:int; product_id:int; frame_second:float=Field(ge=0); x:float=Field(ge=0,le=1); y:float=Field(ge=0,le=1); width:float=Field(gt=0.02,le=0.7); start_second:float=Field(ge=0); end_second:float=Field(gt=0)
    # Four normalized target-surface corners in reference-frame TL,TR,BR,BL order.
    tracking_quad: Optional[list[list[float]]] = None
    color_match: bool = True
    adjust: Optional[dict] = None
    model3d: Optional[dict] = None
class RightsIn(BaseModel):
    permitted_channels: list[str] = Field(default_factory=lambda:['social','web'], min_length=1)
    territories: list[str] = Field(default_factory=lambda:['WORLDWIDE'], min_length=1)
    start_at: Optional[str] = None
    expires_at: Optional[str] = None
    creator_consent: bool = False
class SurfaceReviewIn(BaseModel):
    decision: str
    quad: Optional[list[list[float]]] = None
    notes: str = Field(default='', max_length=1000)
class TrackedLinkIn(BaseModel):
    placement_id: Optional[int] = None
    channel: str = 'social'
    destination_url: str = Field(min_length=8, max_length=2048)
    campaign: str = Field(default='', max_length=120)
class ConversionIn(BaseModel):
    external_id: Optional[str] = None
    link_slug: Optional[str] = None
    event_name: str = Field(min_length=1,max_length=80)
    value: float = Field(default=0,ge=0,le=100000000)
    currency: str = Field(default='USD',min_length=3,max_length=3)
    occurred_at: Optional[str] = None
    dedupe_key: str = Field(min_length=3,max_length=200)


@app.get('/', response_class=HTMLResponse)
def landing(): return (ROOT/'app'/'static'/'landing.html').read_text(encoding='utf-8')
@app.get('/app', response_class=HTMLResponse)
def home(): return (ROOT/'app'/'static'/'index.html').read_text(encoding='utf-8')
@app.get('/config.js')
def frontend_config():
    """Runtime configuration for the bundled frontend. Served by the API in local and single-origin deployments;
    the Vercel build writes its own copy with the production API origin."""
    from fastapi.responses import Response
    return Response('window.CC_CONFIG='+json.dumps({'apiBase':os.getenv('CC_FRONTEND_API_BASE','')})+';',media_type='application/javascript',headers={'Cache-Control':'no-store'})

@app.get('/api/health/live')
def health_live():
    """Liveness: the web process is serving requests. Touches no dependency, so monitors never wake the database."""
    return {'status':'ok'}

_READY={'at':0.0,'result':None}
def _readiness():
    if _READY['result'] and time.time()-_READY['at']<float(os.getenv('CC_READY_CACHE_SECONDS','30')): return _READY['result']
    checks={}
    try:
        with db() as c: c.execute('SELECT 1').fetchone()
        checks['database']='ok'
    except Exception: checks['database']='unavailable'
    if storage.enabled():
        try: storage.client().head_bucket(Bucket=storage.bucket()); checks['object_storage']='ok'
        except Exception: checks['object_storage']='unavailable'
    else: checks['object_storage']='local_disk'
    checks['ffmpeg']='ok' if tool_exists('ffmpeg') and tool_exists('ffprobe') else 'missing'
    try: checks['disk']='ok' if obs.disk_status()['free_gb']>=float(os.getenv('CC_MIN_FREE_DISK_GB','2')) else 'low'
    except Exception: checks['disk']='unknown'
    if os.getenv('CC_INLINE_WORKER','1')!='1': checks['workers_alive']=obs.worker_status()['alive']
    ready=checks['database']=='ok' and checks['object_storage'] in ('ok','local_disk') and checks['ffmpeg']=='ok'
    _READY.update(at=time.time(),result=({'status':'ready' if ready else 'not_ready','checks':checks},ready))
    return _READY['result']

@app.get('/api/health/ready')
def health_ready():
    """Readiness: dependencies reachable (cached for CC_READY_CACHE_SECONDS). Reveals no hostnames or bucket names."""
    body,ready=_readiness()
    return JSONResponse(body,status_code=200 if ready else 503)

@app.get('/api/health')
def health():
    body,_=_readiness(); db_ok=body['checks']['database']=='ok'
    d=dbm.describe(); st=storage.describe()   # public endpoint: no hostnames, bucket names or credentials
    return {'status':'ok' if db_ok else 'degraded','database':{'engine':d['engine'],'neon':d.get('neon',False),'reachable':db_ok},'ffmpeg':tool_exists('ffmpeg'),'ffprobe':tool_exists('ffprobe'),'opencv':_has_cv2(),'vision_model':os.getenv('CC_VISION_MODEL') or None,'malware_scanning':'clamav' if os.getenv('CC_CLAMAV_HOST') else 'not_configured','object_storage':{k:st[k] for k in ('backend','private') if k in st} | ({'presigned_ttl_seconds':st['presigned_ttl_seconds']} if 'presigned_ttl_seconds' in st else {}),'queue':'persistent_db_queue' if os.getenv('CC_INLINE_WORKER','1')=='1' else 'external_worker_required','perspective_tracking':'opencv_optical_flow_homography' if _has_cv2() else 'unavailable','generative_provider':'configured_unverified' if renderers.generative_configured() else 'not_configured','renderers_available':[p['id'] for p in renderers.capabilities() if p.get('available')],'quality_policy':__import__('app.quality',fromlist=['x']).POLICY_VERSION,'visual_fit_gate':'automated_quality_check_plus_human_acceptance_min_0.75','rights_versioning':'placement_version_bound','campaign_attribution':'observed_only_no_causal_lift','mode':'production' if os.getenv('CC_ENV')=='production' else 'development','workers_alive':obs.worker_status()['alive'] if os.getenv('CC_INLINE_WORKER','1')!='1' else 'inline'}
@app.post('/api/auth/register')
def register(body:Register, request:Request):
    if body.role not in ('brand','creator'): raise HTTPException(422,'Role must be brand or creator')
    email=body.email.strip().lower()
    if '@' not in email: raise HTTPException(422,'Enter a valid email address')
    with db() as c:
        if c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone(): raise HTTPException(409,'An account with this email already exists')
        invitation=None
        if body.invitation_token:
            invitation=c.execute('SELECT * FROM invitations WHERE token_hash=? AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>?',(hashlib.sha256(body.invitation_token.encode()).hexdigest(),int(time.time()))).fetchone()
            if not invitation: raise HTTPException(400,'Invitation is invalid, expired, or already used')
            if invitation['email'].lower()!=email or invitation['role']!=body.role: raise HTTPException(403,'Invitation email or role does not match')
            orgid=invitation['org_id']
        else:
            if not body.organization.strip(): raise HTTPException(422,'Organization name is required to create a new workspace')
            org=c.execute('SELECT id FROM organizations WHERE lower(name)=lower(?)',(body.organization.strip(),)).fetchone()
            if org: raise HTTPException(409,'This organization already exists. Ask an organization member for an invitation link.')
            orgid=c.execute('INSERT INTO organizations(name) VALUES(?)',(body.organization.strip(),)).lastrowid
        member_role=invitation['member_role'] if invitation else 'owner'
        cur=c.execute('INSERT INTO users(email,password_hash,name,role,org_id,member_role) VALUES(?,?,?,?,?,?)',(email,hash_password(body.password),body.name.strip(),body.role,orgid,member_role)); uid=cur.lastrowid
        if invitation: c.execute('UPDATE invitations SET accepted_at=CURRENT_TIMESTAMP WHERE id=? AND accepted_at IS NULL',(invitation['id'],))
        token=secrets.token_urlsafe(32); c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),uid,int(time.time())+86400*7))
        return JSONResponse({'user':{'id':uid,'email':email,'name':body.name,'role':body.role,'org_id':orgid,'member_role':member_role}},headers={'Set-Cookie':session_cookie(token)})
@app.post('/api/auth/login')
def login(body:Login):
    with db() as c:
        row=c.execute('SELECT * FROM users WHERE email=? AND removed_at IS NULL',(body.email.strip().lower(),)).fetchone()
        if not row or not verify_password(body.password,row['password_hash']): raise HTTPException(401,'Incorrect email or password')
        if row['suspended_at']: raise HTTPException(403,'This account is suspended. Contact your workspace owner.')
        token=secrets.token_urlsafe(32); c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),row['id'],int(time.time())+86400*7))
        user={k:row[k] for k in ('id','email','name','role','org_id','member_role')}
        return JSONResponse({'user':user},headers={'Set-Cookie':session_cookie(token)})
@app.post('/api/auth/logout')
def logout(request:Request):
    token=request.cookies.get('cc_session')
    if token:
        with db() as c: c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
    return JSONResponse({'ok':True},headers={'Set-Cookie':session_cookie('',0)})
@app.get('/api/me')
def me(request:Request):
    u=current_user(request)
    with db() as c: org=c.execute('SELECT name FROM organizations WHERE id=?',(u['org_id'],)).fetchone()
    return {**{k:u[k] for k in ('id','email','name','role','org_id','member_role')},'organization':org['name'] if org else None}

@app.post('/api/products')
def create_product(request:Request, name:str=Form(...), description:str=Form(''), image:UploadFile=File(...)):
    u=current_user(request); require_role(u,'brand')
    if not image.content_type or not image.content_type.startswith('image/'): raise HTTPException(415,'Product image must be an image file')
    raw=image.file.read(limits.max_image_bytes()+1)
    if len(raw)>limits.max_image_bytes(): raise HTTPException(413,f'Product image limit is {limits.max_image_bytes()//1048576} MB')
    with db() as c: limits.check_storage(c,u['org_id'],len(raw)*2)
    try:
        from PIL import Image
        import io
        im=Image.open(io.BytesIO(raw))
        if im.width>8000 or im.height>8000 or im.width*im.height>40000000: raise ValueError('Image dimensions exceed safety limits')
        im.verify()
        if im.format not in ('PNG','JPEG','WEBP'): raise ValueError('Unsupported image format')
    except Exception: raise HTTPException(400,'Invalid image file')
    path=UPLOADS/f'product_{secrets.token_hex(8)}_{safe_name(image.filename or "product.png")}' ; path.write_bytes(raw)
    scan=clamav_scan(path)
    if scan.get('clean') is False: path.unlink(missing_ok=True); raise HTTPException(400,'Malware detected in uploaded image')
    from .compositor import prepare_asset
    derived=path.with_name(path.stem+'.derived.png')
    try: report=prepare_asset(path,derived)
    except Exception as e: path.unlink(missing_ok=True); raise HTTPException(400,f'Invalid product image: {str(e)[:120]}')
    storage.persist(path); storage.persist(derived)
    with db() as c:
        cur=c.execute('INSERT INTO products(org_id,name,description,image_path,created_by,sha256,derived_path,asset_report,size_bytes) VALUES(?,?,?,?,?,?,?,?,?)',(u['org_id'],name.strip(),description.strip(),str(path),u['id'],hashlib.sha256(raw).hexdigest(),str(derived),json.dumps(report),len(raw)+derived.stat().st_size)); pid=cur.lastrowid; event(c,u,'product.created','product',pid,{'asset_warnings':[w['code'] for w in report['warnings']]})
    return {'id':pid,'name':name,'description':description,'image_url':f'/api/products/{pid}/image','asset_report':report}
@app.post('/api/products/{pid}/model')
def upload_product_model(pid:int,request:Request,model:UploadFile=File(...)):
    """Attach a validated, self-contained .glb 3D model to a product. The image asset is unchanged."""
    u=current_user(request); require_role(u,'brand')
    from .assets3d import load_glb, AssetError, MAX_GLB_BYTES
    raw=model.file.read(MAX_GLB_BYTES+1)
    try: _mesh,report=load_glb(raw)
    except AssetError as e: raise _err(400,'MODEL_INVALID',str(e))
    except Exception: raise _err(400,'MODEL_INVALID','The file is not a valid glTF 2.0 binary model')
    with db() as c:
        prod=owned(c,'products',pid,u['org_id'])
        if prod['org_id']!=u['org_id']: raise HTTPException(404,'Resource not found')
        limits.check_storage(c,u['org_id'],len(raw))
        path=UPLOADS/f'model_{secrets.token_hex(8)}.glb'; path.write_bytes(raw); storage.persist(path)
        c.execute('UPDATE products SET model_path=?,model_sha256=?,model_report=?,size_bytes=size_bytes+? WHERE id=?',(str(path),hashlib.sha256(raw).hexdigest(),json.dumps(report),len(raw),pid))
        event(c,u,'product.model_uploaded','product',pid,{'triangles':report['triangles'],'warnings':report['warnings']})
    return {'product_id':pid,'model_report':report}

@app.get('/api/products')
def products(request:Request):
    u=current_user(request)
    with db() as c: rows=c.execute('SELECT id,name,description,created_at,asset_report,model_report FROM products WHERE org_id=? ORDER BY id DESC',(u['org_id'],)).fetchall()
    return [dict(r, asset_report=json.loads(r['asset_report'] or '{}'), model_report=json.loads(r['model_report']) if r['model_report'] else None, has_model=bool(r['model_report']), image_url=f'/api/products/{r["id"]}/image') for r in rows]
@app.get('/api/products/{pid}/image')
def product_image(pid:int,request:Request):
    u=current_user(request)
    with db() as c: p=owned(c,'products',pid,u['org_id'])
    return storage.response(p['image_path'])

@app.post('/api/videos')
def upload_video(request:Request, title:str=Form(...), license_confirmed:bool=Form(...), license_expires:Optional[str]=Form(None), video:UploadFile=File(...)):
    u=current_user(request); require_role(u,'creator')
    if not license_confirmed: raise HTTPException(400,'Confirm that you own or are authorized to license this video')
    if video.content_type not in VIDEO_TYPES: raise HTTPException(415,'Supported formats: MP4, MOV, WebM')
    with db() as c: limits.check_daily_uploads(c,u['org_id'])
    path=UPLOADS/f'video_{secrets.token_hex(8)}_{safe_name(video.filename or "upload.mp4")}'
    size=0
    with open(path,'wb') as out:          # streamed to disk in 1 MB chunks: never the whole video in memory
        while chunk:=video.file.read(1048576):
            size+=len(chunk)
            if size>limits.max_video_bytes(): out.close(); path.unlink(missing_ok=True); raise HTTPException(413,f'Video upload limit is {limits.max_video_bytes()//1048576} MB')
            out.write(chunk)
    return ingest_video(u,path,title,license_expires,size)

VIDEO_TYPES=('video/mp4','video/quicktime','video/webm','application/octet-stream')
def ingest_video(u, path, title, license_expires, size, persisted=False):
    """Validate a received video file (malware scan, FFprobe metadata, limits, quotas) and record it."""
    try:
        with db() as c: limits.check_storage(c,u['org_id'],size); limits.check_daily_uploads(c,u['org_id'])
        scan=clamav_scan(path)
        if scan.get('clean') is False: raise HTTPException(400,'Malware detected in uploaded video')
        duration,width,height,fps=probe(path)
    except Exception:
        Path(path).unlink(missing_ok=True)
        if persisted: storage.delete(str(path))
        raise
    if not persisted: storage.persist(path)
    with db() as c:
        cur=c.execute('INSERT INTO videos(org_id,title,source_path,duration,width,height,fps,license_confirmed,license_expires,created_by,size_bytes) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(u['org_id'],title.strip(),str(path),duration,width,height,fps,1,license_expires,u['id'],size)); vid=cur.lastrowid; event(c,u,'video.uploaded','video',vid,{'duration':duration,'bytes':size})
    return {'id':vid,'title':title,'duration':duration,'width':width,'height':height,'fps':fps,'analyzed':False}
@app.get('/api/videos')
def videos(request:Request):
    u=current_user(request)
    cols='v.id,v.title,v.duration,v.width,v.height,v.fps,v.analyzed,v.license_confirmed,v.license_expires,v.created_at'
    with db() as c:
        # Own videos, plus videos licensed to this organization through an active marketplace grant.
        rows=c.execute(f'SELECT {cols},0 licensed FROM videos v WHERE v.org_id=? UNION SELECT {cols},1 licensed FROM videos v JOIN video_grants g ON g.video_id=v.id '
                       'WHERE g.grantee_org_id=? AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR substr(g.expires_at,1,10)>=?) ORDER BY 1 DESC',(u['org_id'],u['org_id'],_today())).fetchall()
    return [dict(r) for r in rows]
@app.post('/api/videos/{vid}/analyze')
def analyze_video(vid:int,request:Request):
    u=current_user(request); require_role(u,'creator')
    with db() as c: v=owned(c,'videos',vid,u['org_id'])
    limits.check_rate(u['org_id'],'analyses',limits.org_analyses_per_hour())
    try: frames=extract_frames(vid,storage.local(v['source_path']),v['duration'])
    except Exception as e: raise HTTPException(503,f'Scene analysis failed: {str(e)[:180]}')
    with db() as c:
        c.execute('DELETE FROM frames WHERE video_id=?',(vid,))
        for sec,path,desc,score in frames: c.execute('INSERT INTO frames(video_id,second,image_path,description,score) VALUES(?,?,?,?,?)',(vid,sec,path,desc,score))
        c.execute('UPDATE videos SET analyzed=1 WHERE id=?',(vid,)); event(c,u,'video.analyzed','video',vid,{'keyframes':len(frames)})
    return {'video_id':vid,'analyzed':True,'method':('OpenCV scene-change detection + local Ollama vision descriptions' if os.getenv('CC_VISION_MODEL') else 'OpenCV scene-change detection + FFmpeg keyframes; heuristic ranking'), 'semantic_vision_enabled':bool(os.getenv('CC_VISION_MODEL')),'scenes':[{'second':s,'image_url':f'/api/videos/{vid}/frames/{int(s*1000)}','description':d,'score':sc} for s,p,d,sc in frames]}
@app.get('/api/videos/{vid}/scenes')
def scenes(vid:int,request:Request):
    u=current_user(request)
    with db() as c:
        owned(c,'videos',vid,u['org_id']); rows=c.execute('SELECT * FROM frames WHERE video_id=? ORDER BY second',(vid,)).fetchall()
    return [{'second':r['second'],'description':r['description'],'score':r['score'],'image_url':f'/api/videos/{vid}/frames/{int(r["second"]*1000)}'} for r in rows]
@app.get('/api/videos/{vid}/frames/{ms}')
def frame(vid:int,ms:int,request:Request):
    u=current_user(request)
    with db() as c:
        owned(c,'videos',vid,u['org_id']); r=c.execute('SELECT image_path FROM frames WHERE video_id=? AND second*1000>=? AND second*1000<? ORDER BY second LIMIT 1',(vid,ms,ms+1)).fetchone()
    if not r: raise HTTPException(404,'Frame not found')
    return storage.response(r['image_path'],media_type='image/jpeg')

def _today(): return time.strftime('%Y-%m-%d')
def _license_ok(v): return bool(v['license_confirmed']) and not (v['license_expires'] and v['license_expires']<_today())
def _err(http_status, code, message, **extra): return HTTPException(http_status, {'code':code,'message':message,**extra})

def _transition(c, p, target, u, reason=None, details=None):
    try: return lifecycle.transition(c, p, target, u['id'], reason, details)
    except lifecycle.TransitionError as e: raise _err(409,'INVALID_TRANSITION',str(e),current=e.current,target=e.target)

def _product_sha(c, prod, with_model=False):
    digest=prod.get('sha256')
    if not digest:
        digest=hashlib.sha256(Path(storage.local(prod['image_path'])).read_bytes()).hexdigest()
        c.execute('UPDATE products SET sha256=? WHERE id=?',(digest,prod['id']))
    if with_model:
        model=storage.local(prod.get('model_path'))
        if not model or not Path(model).exists(): return digest+':model-missing'
        return digest+':'+hashlib.sha256(Path(model).read_bytes()).hexdigest()
    return digest

def _uses_model(p):
    return bool(json.loads(p['render_options'] or '{}').get('model3d'))

def _record_version(c, pid, user_id):
    """Persist an immutable snapshot of the placement's current version and bind its content hash."""
    p=dict(c.execute('SELECT * FROM placements WHERE id=?',(pid,)).fetchone())
    prod=dict(c.execute('SELECT * FROM products WHERE id=?',(p['product_id'],)).fetchone())
    snap,digest=lifecycle.snapshot(p,_product_sha(c,prod,_uses_model(p)))
    c.execute('INSERT INTO placement_versions(placement_id,version,snapshot_json,content_hash,created_by) VALUES(?,?,?,?,?)',(pid,p['version'],json.dumps(snap,sort_keys=True),digest,user_id))
    c.execute('UPDATE placements SET content_hash=? WHERE id=?',(digest,pid))
    return digest

def _rights(c, p):
    return c.execute('SELECT * FROM placement_rights WHERE placement_id=? AND placement_version=?',(p['id'],p['version'])).fetchone()

def _approvals_ok(c, p, v):
    """Brand approval plus approval by the video's rights holder, both bound to this exact version and content hash."""
    rows=c.execute('SELECT user_id,role FROM placement_approvals WHERE placement_id=? AND placement_version=? AND approved=1 AND content_hash=?',(p['id'],p['version'],p['content_hash'])).fetchall()
    return any(r['role']=='brand' for r in rows) and any(r['role']=='creator' and r['user_id']==v['created_by'] for r in rows)

def _current_analysis(c, p):
    if not p.get('analysis_id'): return None
    a=c.execute('SELECT * FROM placement_analyses WHERE id=? AND placement_id=?',(p['analysis_id'],p['id'])).fetchone()
    if not a or a['placement_version']!=p['version'] or a['content_hash']!=p['content_hash']: return None
    return dict(a)

def _quality_rejections(c, org_id):
    counts={}
    for r in c.execute("SELECT a.report_json FROM placement_analyses a JOIN placements p ON p.id=a.placement_id WHERE p.org_id=? AND a.status='FAILED'",(org_id,)).fetchall():
        for f in json.loads(r['report_json']).get('failures',[]): counts[f['code']]=counts.get(f['code'],0)+1
    return counts

def _asset_path(prod):
    derived=storage.local(prod.get('derived_path'))
    return derived if derived and Path(derived).exists() else storage.local(prod['image_path'])

def _grant_for(c, v, org_id):
    if v['org_id']==org_id: return None
    from .commerce import active_grant
    return active_grant(c,v['id'],org_id)

def _apply_grant_terms(c, pid, v, org_id):
    """Default a cross-organization placement's rights to the licence grant's channels, territories and expiry."""
    g=_grant_for(c,v,org_id)
    if g:
        exp=min([x for x in (g['expires_at'],v['license_expires']) if x],default=None)
        c.execute('UPDATE placement_rights SET permitted_channels=?,territories=?,expires_at=? WHERE placement_id=?',(g['channels'],g['territories'],exp,pid))

def _validate_adjust(adjust):
    if not adjust: return None
    if set(adjust)-{'exposure_ev','temperature'}: raise HTTPException(422,'adjust supports exposure_ev and temperature only')
    out={}
    for k in ('exposure_ev','temperature'):
        if k in adjust:
            val=float(adjust[k])
            if not -1<=val<=1: raise HTTPException(422,f'{k} must be between -1 and 1')
            if val: out[k]=round(val,3)
    return out or None

def _validate_model3d(m, prod):
    if not m: return None
    if not prod.get('model_path'): raise _err(422,'MODEL_REQUIRED','Upload a .glb 3D model for this product before creating a 3D placement')
    allowed={'anchor_uv','surface_size_m','yaw_deg','scale','light','camera_hfov_deg'}
    if set(m)-allowed: raise HTTPException(422,'model3d supports: '+', '.join(sorted(allowed)))
    try:
        size=[float(x) for x in m['surface_size_m']]; anchor=[float(x) for x in m.get('anchor_uv',[.5,.5])]
        out={'surface_size_m':size,'anchor_uv':anchor,'yaw_deg':float(m.get('yaw_deg',0)),'scale':float(m.get('scale',1))}
    except (KeyError,TypeError,ValueError): raise HTTPException(422,'model3d requires surface_size_m [width, height] in metres')
    if len(size)!=2 or not all(.02<=x<=20 for x in size): raise HTTPException(422,'surface_size_m must be two values between 0.02 and 20 metres')
    if len(anchor)!=2 or not all(0<=x<=1 for x in anchor): raise HTTPException(422,'anchor_uv must be two values between 0 and 1')
    if not .05<=out['scale']<=20: raise HTTPException(422,'scale must be between 0.05 and 20')
    if m.get('light') is not None:
        lt=m['light']; az,el=float(lt.get('azimuth_deg',135)),float(lt.get('elevation_deg',55))
        if not (0<=el<=89 and -360<=az<=360): raise HTTPException(422,'light elevation must be 0-89 degrees')
        out['light']={'azimuth_deg':az,'elevation_deg':el}
    if m.get('camera_hfov_deg') is not None:
        fov=float(m['camera_hfov_deg'])
        if not 10<=fov<=150: raise HTTPException(422,'camera_hfov_deg must be between 10 and 150')
        out['camera_hfov_deg']=fov
    return out

def _validate_geometry(v, frame_second, start_second, end_second, tracking_quad):
    if start_second<0 or start_second>=end_second or end_second>v['duration']: raise HTTPException(422,'Placement timing must fit inside the source video')
    if frame_second<0 or frame_second>v['duration']: raise HTTPException(422,'Selected scene is outside the video duration')
    opts={}
    if tracking_quad is not None:
        if frame_second>start_second: raise HTTPException(422,'For tracked placement, the reference frame must be at or before the placement start time')
        if end_second-frame_second>MAX_ANALYSIS_SECONDS: raise HTTPException(422,f'Tracked placements are limited to {MAX_ANALYSIS_SECONDS:g} seconds from the reference frame to the end')
        try:
            from .tracking import validate_quad
            opts['tracking_quad']=validate_quad(tracking_quad).tolist()
        except Exception as e: raise HTTPException(422,str(e))
    return opts

@app.post('/api/placements')
def create_placement(body:PlacementIn,request:Request):
    u=current_user(request); require_role(u,'brand')
    with db() as c:
        v=owned(c,'videos',body.video_id,u['org_id']); owned(c,'products',body.product_id,u['org_id'])
        if not _license_ok(v): raise HTTPException(403,'Video license permission is missing or expired')
        render_options=_validate_geometry(v,body.frame_second,body.start_second,body.end_second,body.tracking_quad)
        if render_options.get('tracking_quad'): render_options['color_match']=body.color_match
        if _validate_adjust(body.adjust): render_options['adjust']=_validate_adjust(body.adjust)
        if body.model3d:
            if not render_options.get('tracking_quad'): raise HTTPException(422,'3D placements need the supporting surface corners (tracking_quad)')
            render_options['model3d']=_validate_model3d(body.model3d,owned(c,'products',body.product_id,u['org_id']))
        cur=c.execute('INSERT INTO placements(org_id,video_id,product_id,frame_second,x,y,width,start_second,end_second,created_by,render_options,fit_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(u['org_id'],body.video_id,body.product_id,body.frame_second,body.x,body.y,body.width,body.start_second,body.end_second,u['id'],json.dumps(render_options),'REVIEW_REQUIRED')); pid=cur.lastrowid
        c.execute('INSERT INTO placement_rights(placement_id,placement_version,creator_consent,expires_at,updated_by) VALUES(?,?,?,?,?)',(pid,1,0,v['license_expires'],u['id']))
        _apply_grant_terms(c,pid,v,u['org_id'])
        digest=_record_version(c,pid,u['id'])
        event(c,u,'placement.created','placement',pid,{'render_options':render_options,'content_hash':digest})
    return {'id':pid,'status':'DRAFT','version':1,'content_hash':digest,'next_step':'Run the quality check'}
@app.get('/api/placements')
def placements(request:Request):
    u=current_user(request)
    with db() as c:
        rows=c.execute('SELECT p.*,v.title video_title,v.created_by video_owner,pr.name product_name FROM placements p JOIN videos v ON v.id=p.video_id JOIN products pr ON pr.id=p.product_id WHERE p.org_id=? OR v.org_id=? ORDER BY p.id DESC',(u['org_id'],u['org_id'])).fetchall()
    return [dict(r,render_options=json.loads(r['render_options'] or '{}'),is_rights_holder=(r['video_owner']==u['id'])) for r in rows]
@app.get('/api/placements/{pid}')
def placement_detail(pid:int,request:Request):
    u=current_user(request)
    with db() as c:
        p=owned(c,'placements',pid,u['org_id']); v=dict(c.execute('SELECT * FROM videos WHERE id=?',(p['video_id'],)).fetchone())
        a=_current_analysis(c,p); rights=_rights(c,p)
        versions=[dict(r,snapshot=json.loads(r['snapshot_json'])) for r in c.execute('SELECT version,content_hash,snapshot_json,created_at FROM placement_versions WHERE placement_id=? ORDER BY version',(pid,)).fetchall()]
        approvals=[dict(r) for r in c.execute('SELECT placement_version,user_id,role,content_hash,created_at FROM placement_approvals WHERE placement_id=? ORDER BY id',(pid,)).fetchall()]
        history=[dict(r,details=json.loads(r['details'])) for r in c.execute("SELECT event_type,details,created_at FROM events WHERE org_id=? AND entity_type='placement' AND entity_id=? ORDER BY id",(u['org_id'],pid)).fetchall()]
        jobs=[dict(r) for r in c.execute('SELECT id,status,renderer,placement_version,error,created_at,finished_at FROM jobs WHERE placement_id=? ORDER BY id DESC',(pid,)).fetchall()]
    for x in versions: x.pop('snapshot_json',None)
    return {'placement':dict(p,render_options=json.loads(p['render_options'] or '{}')),'video':{k:v[k] for k in ('id','title','duration','width','height','fps')},
            'is_rights_holder':v['created_by']==u['id'],'quality':(dict(json.loads(a['report_json']),analysis_id=a['id'],created_at=a['created_at']) if a else None),
            'rights':(dict(rights,permitted_channels=json.loads(rights['permitted_channels']),territories=json.loads(rights['territories'])) if rights else None),
            'versions':versions,'approvals':approvals,'jobs':jobs,'history':history}

@app.post('/api/placements/{pid}/quality-check')
def quality_check(pid:int,request:Request):
    """Measure every frame of the placement interval and apply the quality policy to this exact version."""
    u=current_user(request); require_role(u,'brand','creator')
    with db() as c:
        p=owned(c,'placements',pid,u['org_id'])
        if p['status']!='DRAFT': raise _err(409,'NOT_DRAFT','Quality checks run on draft versions. Revise the placement to create a new draft version.')
        v=owned(c,'videos',p['video_id'],u['org_id']); prod=owned(c,'products',p['product_id'],u['org_id'])
        keyframes=[r['second'] for r in c.execute('SELECT second FROM frames WHERE video_id=? ORDER BY second',(v['id'],)).fetchall()]
    from .compositor import load_product, overlay_quad, product_quad_in_surface
    from . import quality
    t0=time.time(); opts=json.loads(p['render_options'] or '{}'); track=None
    try:
        ph,pw=load_product(_asset_path(prod)).shape[:2]
        if opts.get('model3d'):
            from .tracking import track_surface
            from .render3d import prepare_3d, scene_consistency
            track=track_surface(storage.local(v['source_path']),opts['tracking_quad'],p['frame_second'],p['start_second'],p['end_second'])
            plan,cam,poses,mo=prepare_3d(track,storage.local(prod['model_path']),opts,track['start_idx'],track['end_idx'])
            fg=scene_consistency(storage.local(v['source_path']),plan,track['start_idx'],track['end_idx']) if plan else {}
            report=quality.evaluate_3d(track,plan,cam,poses,mo,fg)
        elif opts.get('tracking_quad'):
            from .tracking import track_surface, homography_quads
            track=track_surface(storage.local(v['source_path']),opts['tracking_quad'],p['frame_second'],p['start_second'],p['end_second'])
            pq=product_quad_in_surface(track['quad_ref'],pw,ph)
            quads={i:q for i,q in homography_quads(track,pq).items() if track['start_idx']<=i<=track['end_idx']}
            report=quality.evaluate_track(track,quads); report['product_quad_ref']=pq.tolist()
        else:
            import cv2
            src=storage.local(v['source_path']); info=media.probe_info(src); cap=cv2.VideoCapture(src); n=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); cap.release()
            s,e=media.interval_frames(info['fps'],n,p['start_second'],p['end_second'])
            report=quality.evaluate_overlay(overlay_quad(p['x'],p['y'],p['width'],info['width'],info['height'],pw,ph),info['width'],info['height'],info['fps'],s,e)
    except ValueError as e: raise HTTPException(422,str(e))
    except Exception as e: raise HTTPException(503,f'Quality analysis failed: {str(e)[:200]}')
    if report['status']=='FAILED':
        report['alternative_scenes']=[k for k in keyframes if k<p['frame_second'] or k>p['end_second']]
    report['analysis_seconds']=round(time.time()-t0,2)
    with db() as c:
        now=owned(c,'placements',pid,u['org_id'])
        if now['version']!=p['version'] or now['content_hash']!=p['content_hash'] or now['status']!='DRAFT': raise _err(409,'CHANGED_DURING_ANALYSIS','Placement changed during analysis; re-run the quality check')
        aid=c.execute('INSERT INTO placement_analyses(placement_id,placement_version,content_hash,status,scene_class,policy_version,report_json,created_by) VALUES(?,?,?,?,?,?,?,?)',(pid,p['version'],p['content_hash'],report['status'],report['scene_class'],report['policy_version'],json.dumps(report),u['id'])).lastrowid
        if track is not None:
            d=DATA/'analyses'; d.mkdir(parents=True,exist_ok=True); tp=d/f'placement_{pid}_v{p["version"]}_a{aid}.json'
            body=json.dumps(track).encode(); tp.write_bytes(body); storage.persist(tp)
            c.execute('UPDATE placement_analyses SET track_path=?,track_sha256=? WHERE id=?',(str(tp),hashlib.sha256(body).hexdigest(),aid))
        c.execute('UPDATE placements SET quality_status=?,analysis_id=? WHERE id=?',(report['status'],aid,pid))
        event(c,u,'placement.quality_checked','placement',pid,{'analysis_id':aid,'status':report['status'],'scene_class':report['scene_class'],'codes':[f['code'] for f in report['failures']],'version':p['version']})
    return {'analysis_id':aid,'placement_id':pid,'placement_version':p['version'],**report}

@app.post('/api/placements/{pid}/propose')
def propose(pid:int,request:Request):
    u=current_user(request); require_role(u,'brand')
    with db() as c:
        p=owned(c,'placements',pid,u['org_id'])
        a=_current_analysis(c,p)
        if not a or a['status']!='PASSED': raise _err(409,'QUALITY_NOT_PASSED','The automated quality check must pass for this exact version before proposal',quality_status=p['quality_status'])
        if p['fit_status']!='ACCEPTED': raise _err(409,'FIT_NOT_ACCEPTED','A reviewer must accept visual fit at confidence >= 0.75 before proposal')
        _transition(c,p,'PROPOSED',u)
    return {'id':pid,'status':'PROPOSED','version':p['version']}
@app.post('/api/placements/{pid}/approve')
def approve(pid:int,request:Request):
    u=current_user(request); require_role(u,'brand','creator')
    checks=None
    with db() as c:
        p=owned(c,'placements',pid,u['org_id']); v=owned(c,'videos',p['video_id'],u['org_id'])
        if u['role']=='creator':
            if v['created_by']!=u['id']: raise _err(403,'NOT_RIGHTS_HOLDER','Only the creator who holds rights to this video can approve its placements')
            if not rights_are_active(_rights(c,p)): raise _err(409,'CONSENT_REQUIRED','Grant creator consent for this placement version before approving')
            _transition(c,p,'CREATOR_APPROVED',u)
            c.execute('UPDATE placements SET creator_approved=1 WHERE id=?',(pid,))
        else:
            if p['status']=='PROPOSED': raise _err(409,'AWAITING_CREATOR_APPROVAL','The creator (rights holder) must approve first')
            _transition(c,p,'BRAND_APPROVED',u)
            c.execute('UPDATE placements SET brand_approved=1 WHERE id=?',(pid,))
        c.execute('INSERT INTO placement_approvals(placement_id,placement_version,user_id,role,approved,content_hash) VALUES(?,?,?,?,1,?) ON CONFLICT(placement_id,placement_version,user_id) DO UPDATE SET role=excluded.role,approved=1,content_hash=excluded.content_hash,created_at=CURRENT_TIMESTAMP',(pid,p['version'],u['id'],u['role'],p['content_hash']))
        event(c,u,'placement.approved','placement',pid,{'role':u['role'],'placement_version':p['version'],'content_hash':p['content_hash']})
        if u['role']=='brand': checks=_verify_chain(c,p,u)
        p2=owned(c,'placements',pid,u['org_id'])
    return {'id':pid,'status':p2['status'],'creator_approved':p2['creator_approved'],'brand_approved':p2['brand_approved'],'placement_version':p2['version'],'verification':checks}
@app.post('/api/placements/{pid}/reject')
def reject_placement(pid:int,request:Request,reason:str=Form(...)):
    u=current_user(request); require_role(u,'brand','creator')
    if len(reason.strip())<3: raise HTTPException(422,'Give a reason so the other party can revise the placement')
    with db() as c:
        p=owned(c,'placements',pid,u['org_id']); v=owned(c,'videos',p['video_id'],u['org_id'])
        if u['role']=='creator' and v['created_by']!=u['id']: raise _err(403,'NOT_RIGHTS_HOLDER','Only the rights holder can reject on the creator side')
        _transition(c,p,'REJECTED',u,reason[:500])
    return {'id':pid,'status':'REJECTED','next_step':'The brand can revise the placement to create a new draft version'}

def _verify_chain(c, p, u):
    """Walk BRAND_APPROVED -> RIGHTS_VERIFIED -> QUALITY_VERIFIED -> READY_FOR_RENDER, stopping at the first failed gate."""
    v=dict(c.execute('SELECT * FROM videos WHERE id=?',(p['video_id'],)).fetchone()); rights=_rights(c,p); checks=[]
    def gate(name, ok, code, message):
        checks.append({'check':name,'passed':bool(ok),'code':None if ok else code,'message':'ok' if ok else message}); return bool(ok)
    expired=bool((rights and rights['expires_at'] and rights['expires_at'][:10]<_today()) or (v['license_expires'] and v['license_expires']<_today()))
    if expired:
        if 'EXPIRED' in lifecycle.TRANSITIONS.get(p['status'],()): _transition(c,p,'EXPIRED',u,'Video license or placement rights expired')
        gate('rights',False,'RIGHTS_EXPIRED','The video license or placement rights have expired'); return checks
    if p['status'] in ('BRAND_APPROVED','RIGHTS_VERIFIED','QUALITY_VERIFIED','RENDER_FAILED'):
        if not gate('rights',rights_are_active(rights) and _license_ok(v),'RIGHTS_NOT_ACTIVE','Creator consent for this version is missing, not yet active, or revoked'): return checks
        if p['status']=='BRAND_APPROVED': _transition(c,p,'RIGHTS_VERIFIED',u)
    a=_current_analysis(c,p)
    if p['status'] in ('RIGHTS_VERIFIED','QUALITY_VERIFIED','RENDER_FAILED'):
        ok=gate('quality',a and a['status']=='PASSED' and p['fit_status']=='ACCEPTED','QUALITY_NOT_VERIFIED','Automated checks must pass and a reviewer must accept fit for this exact version')
        ok=gate('approvals',_approvals_ok(c,p,v),'APPROVALS_NOT_BOUND','Rights-holder and brand approvals must exist for this exact version') and ok
        if not ok: return checks
        if p['status']=='RIGHTS_VERIFIED': _transition(c,p,'QUALITY_VERIFIED',u,details={'analysis_id':a['id'],'scene_class':a['scene_class']})
    if p['status'] in ('QUALITY_VERIFIED','RENDER_FAILED'):
        routing=renderers.route(a['scene_class'],v['duration'],v['width'],v['height'])
        if not gate('renderer',routing['selected'],'NO_CAPABLE_RENDERER',routing.get('message')): return checks
        _transition(c,p,'READY_FOR_RENDER',u,details={'renderer':routing['selected']})
    return checks
@app.post('/api/placements/{pid}/verify')
def verify_placement(pid:int,request:Request):
    u=current_user(request); require_role(u,'brand')
    with db() as c:
        p=owned(c,'placements',pid,u['org_id'])
        if p['status'] not in ('BRAND_APPROVED','RIGHTS_VERIFIED','QUALITY_VERIFIED','RENDER_FAILED'): raise _err(409,'NOT_VERIFIABLE',f'Placement is {p["status"]}; verification runs after brand approval')
        checks=_verify_chain(c,p,u)
    return {'id':pid,'status':p['status'],'verification':checks}

def _run_replicate_generation(video, product, placement, job_id):
    """Run a configured Replicate model. Model input schema is explicitly operator-configured."""
    token=os.getenv('CC_REPLICATE_API_TOKEN'); model=os.getenv('CC_REPLICATE_MODEL'); template=os.getenv('CC_REPLICATE_INPUT_TEMPLATE')
    if not (token and model and template):
        raise RuntimeError('Generative insertion is not configured. Set CC_REPLICATE_API_TOKEN, CC_REPLICATE_MODEL and CC_REPLICATE_INPUT_TEMPLATE.')
    try:
        import replicate
    except ImportError as e: raise RuntimeError('Install the optional replicate dependency to use generative rendering') from e
    max_video_seconds=float(os.getenv('CC_GENERATIVE_MAX_VIDEO_SECONDS','30'))
    max_input_bytes=int(os.getenv('CC_GENERATIVE_MAX_INPUT_BYTES',str(100*1024*1024)))
    max_output_bytes=int(os.getenv('CC_GENERATIVE_MAX_OUTPUT_BYTES',str(1024*1024*1024)))
    if float(video['duration'])>max_video_seconds: raise RuntimeError(f'Source duration exceeds configured generative limit of {max_video_seconds:g} seconds')
    if Path(storage.local(video['source_path'])).stat().st_size>max_input_bytes: raise RuntimeError(f'Source video exceeds configured generative input limit of {max_input_bytes} bytes')
    try: mapping=json.loads(template)
    except Exception as e: raise RuntimeError('CC_REPLICATE_INPUT_TEMPLATE must be a JSON object mapping model input fields to {{video}}, {{product_image}}, and/or {{prompt}}') from e
    if not isinstance(mapping,dict): raise RuntimeError('CC_REPLICATE_INPUT_TEMPLATE must be a JSON object')
    prompt=(f'Insert the exact product shown in the product image into the source video during seconds {placement["start_second"]:.2f} to {placement["end_second"]:.2f}. '
            'Preserve the product identity, scene continuity, camera motion, lighting and shadows. Do not add text, logos, or unrelated objects. Return a video file.')
    inputs={}
    with open(storage.local(video['source_path']),'rb') as vf, open(storage.local(product['image_path']),'rb') as pf:
        for key,val in mapping.items():
            if val=='{{video}}': inputs[key]=vf
            elif val=='{{product_image}}': inputs[key]=pf
            elif val=='{{prompt}}': inputs[key]=prompt
            elif isinstance(val,str) and val.startswith('{{') and val.endswith('}}'):
                raise RuntimeError('Unknown input template placeholder: '+val)
            else: inputs[key]=val
        result=replicate.run(model,input=inputs)
    if isinstance(result,(list,tuple)):
        result=result[0] if result else None
    elif not hasattr(result,'read') and not isinstance(result,(str,bytes,Path)):
        try: result=next(iter(result))
        except (TypeError,StopIteration): pass
    if result is None: raise RuntimeError('Generative model returned no output')
    output=OUTPUTS/f'generative_placement_{placement["id"]}_job_{job_id}.mp4'
    if hasattr(result,'read'):
        total=0
        with open(output,'wb') as f:
            while True:
                chunk=result.read(1024*1024)
                if not chunk: break
                total+=len(chunk)
                if total>max_output_bytes: raise RuntimeError('Generated output exceeds configured output-size limit')
                f.write(chunk)
    else:
        url=str(result)
        parsed=urllib.parse.urlparse(url)
        allowed=[x.strip().lower() for x in os.getenv('CC_GENERATIVE_OUTPUT_HOSTS','replicate.delivery,replicate.com,storage.googleapis.com').split(',') if x.strip()]
        host=(parsed.hostname or '').lower()
        if parsed.scheme!='https' or not host or not any(host==domain or host.endswith('.'+domain) for domain in allowed):
            raise RuntimeError('Generative output URL host is not allowlisted. Configure CC_GENERATIVE_OUTPUT_HOSTS for your provider output domain.')
        req=urllib.request.Request(url,headers={'User-Agent':'ContextCommerce/1.0'})
        with urllib.request.urlopen(req,timeout=120) as response, open(output,'wb') as f:
            total=0
            while True:
                chunk=response.read(1024*1024)
                if not chunk: break
                total+=len(chunk)
                if total>max_output_bytes: raise RuntimeError('Generated output exceeds configured output-size limit')
                f.write(chunk)
    # Require a real playable video, not an image or an empty provider response.
    probe(output)
    return output


class _GateError(RuntimeError):
    """A policy gate failed. Terminal: never retried."""
    @property
    def code(self): return str(self).split(':',1)[0]

class _JobTimeout(_GateError):
    """The job exceeded CC_JOB_MAX_SECONDS. Terminal."""

class _WorkerShutdown(Exception):
    """The worker is stopping (SIGTERM): hand the job back to the queue instead of letting its lease expire."""

class _ValidationFailed(RuntimeError):
    def __init__(self, report):
        self.report=report; super().__init__('OUTPUT_VALIDATION_FAILED: '+', '.join(f['code'] for f in report['failures']))

def _render_gates(c, p, j=None):
    """Re-check every gate for p's current version. Returns (video, product, analysis) or raises _GateError."""
    v=c.execute('SELECT * FROM videos WHERE id=?',(p['video_id'],)).fetchone()
    if v and v['org_id']!=p['org_id'] and not _grant_for(c,v,p['org_id']): raise _GateError('LICENCE_NOT_ACTIVE: the licence grant for this video has ended or been revoked')
    prod=c.execute('SELECT * FROM products WHERE id=? AND org_id=?',(p['product_id'],p['org_id'])).fetchone()
    if not v or not prod: raise _GateError('SOURCE_UNAVAILABLE: a required source asset is unavailable')
    v,prod=dict(v),dict(prod)
    if not _license_ok(v) or not rights_are_active(_rights(c,p)): raise _GateError('RIGHTS_NOT_ACTIVE: rights are not active, expired, or revoked for this version')
    if _product_sha(c,prod,_uses_model(p))!=json.loads(c.execute('SELECT snapshot_json FROM placement_versions WHERE placement_id=? AND version=?',(p['id'],p['version'])).fetchone()['snapshot_json'])['product_sha256']:
        raise _GateError('PRODUCT_CHANGED: the product image no longer matches the approved version')
    if not _approvals_ok(c,p,v): raise _GateError('APPROVALS_NOT_BOUND: rights-holder and brand approvals for this exact version are missing')
    a=_current_analysis(c,p)
    if not a or a['status']!='PASSED' or p['fit_status']!='ACCEPTED': raise _GateError('QUALITY_NOT_VERIFIED: no passing quality analysis and accepted fit for this version')
    if j is not None and (j['placement_version']!=p['version'] or j['analysis_id']!=a['id']): raise _GateError('VERSION_MISMATCH: the job was created for a different placement version')
    tpath=storage.local(a['track_path'])
    if a['track_path'] and (not Path(tpath).exists() or hashlib.sha256(Path(tpath).read_bytes()).hexdigest()!=a['track_sha256']):
        raise _GateError('TRACK_INTEGRITY: the validated track file is missing or modified')
    return v,prod,a

def _build_plan(a, prod, p):
    """Render plan for exactly the geometry the quality check validated. Shared by preview, render and validation."""
    from .compositor import Planar2DPlan, temporal_gains
    report=json.loads(a['report_json']); s,e=report['interval']['start_frame'],report['interval']['end_frame']
    opts=json.loads(p['render_options'] or '{}'); summary={'mode':report['mode'],'adjust':opts.get('adjust')}
    if report['mode']=='OVERLAY_SCREEN_SPACE':
        q=report['measurements']['overlay_quad_px']
        return Planar2DPlan(_asset_path(prod),{i:q for i in range(s,e+1)},adjust=opts.get('adjust')),summary
    track=json.loads(Path(storage.local(a['track_path'])).read_text())
    if report['mode']=='TRACKED_3D':
        from .render3d import build_3d_plan
        return build_3d_plan(track,report,opts,prod,summary)
    from .tracking import homography_quads
    quads={i:q for i,q in homography_quads(track,report['product_quad_ref']).items() if s<=i<=e}
    gains,gsum=temporal_gains(track,s,e) if opts.get('color_match',True) else ({},{'applied':False,'reason':'disabled for this placement'})
    summary['color_match']=gsum
    return Planar2DPlan(_asset_path(prod),quads,gains,opts.get('adjust')),summary

def _placement_render_failed(c, pid, version, reason, actor_id=None):
    row=c.execute('SELECT * FROM placements WHERE id=?',(pid,)).fetchone()
    if row and row['status']=='RENDERING' and row['version']==version:
        lifecycle.transition(c,dict(row),'RENDER_FAILED',actor_id or row['created_by'],reason[:300])

def render_job(job_id, token=None):
    with db() as c:
        j=c.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not j: return
    j=dict(j); token=token or j['lease_token']
    obs.job_id.set(f'job-{job_id}'); started=time.time(); max_seconds=limits.job_max_seconds()
    obs.info('job started',job=job_id,attempt=j['attempts'],renderer=j['renderer'])
    def heartbeat(_frame=0):
        # Renews the lease; aborts the render if the job was cancelled, revoked, claimed by another worker, or too slow.
        if time.time()-started>max_seconds: raise _JobTimeout(f'JOB_TIMEOUT: the render exceeded {max_seconds} seconds')
        if _WORKER_STOP.is_set(): raise _WorkerShutdown()
        with db() as c:
            if c.execute("UPDATE jobs SET lease_until=? WHERE id=? AND status='RUNNING' AND lease_token=? AND cancel_requested=0",(time.time()+LEASE_SECONDS,job_id,token)).rowcount!=1:
                raise RenderCancelled('Job was cancelled, revoked, or its lease was lost')
    tmp=None
    try:
        with db() as c:
            p=c.execute('SELECT * FROM placements WHERE id=? AND org_id=?',(j['placement_id'],j['org_id'])).fetchone()
            if not p: raise _GateError('SOURCE_UNAVAILABLE: placement no longer exists')
            p=dict(p)
            if p['status']!='RENDERING': raise _GateError(f'INVALID_STATE: placement is {p["status"]}, not RENDERING')
            v,prod,a=_render_gates(c,p,j)
        if j['mode']=='GENERATIVE':
            raise _GateError('GENERATIVE_OUTPUT_UNVALIDATED: generative outputs cannot be validated automatically in this release')
        plan,plan_summary=_build_plan(a,prod,p)
        output=OUTPUTS/f'placement_{p["id"]}_v{p["version"]}_job_{job_id}.mp4'; tmp=output.with_name(f'{output.stem}.{token[:8]}.part.mp4')
        src=storage.local(v['source_path'])
        info=render_plan(src,plan,tmp,progress=heartbeat)
        heartbeat()
        val=validate_plan(src,tmp,plan,progress=heartbeat); val['plan']=plan_summary
        if val['status']!='PASSED': raise _ValidationFailed(val)
        os.replace(tmp,output); tmp=None
        storage.persist(output)
        with db() as c:
            if c.execute("UPDATE jobs SET status='SUCCEEDED',output_path=?,validation_json=?,error=NULL,finished_at=CURRENT_TIMESTAMP,lease_until=NULL,output_bytes=? WHERE id=? AND status='RUNNING' AND lease_token=? AND cancel_requested=0",(str(output),json.dumps(val),output.stat().st_size,job_id,token)).rowcount!=1:
                storage.delete(output); raise RenderCancelled('Job was cancelled before completion')
            now=dict(c.execute('SELECT * FROM placements WHERE id=?',(p['id'],)).fetchone())
            if now['version']==p['version'] and now['status']=='RENDERING': lifecycle.transition(c,now,'RENDERED',p['created_by'],details={'job_id':job_id})
            c.execute('INSERT INTO events(org_id,user_id,event_type,entity_type,entity_id,details) VALUES(?,?,?,?,?,?)',(j['org_id'],p['created_by'],'render.succeeded','job',job_id,json.dumps({'bytes':output.stat().st_size,'frames':info['frames_written'],'composited':info['frames_composited'],'renderer':j['renderer'],'product_psnr_db':val['product_psnr_db']})))
            c.execute('INSERT INTO queue_events(job_id,event_type,details) VALUES(?,?,?)',(job_id,'succeeded','{}'))
        obs.info('job succeeded',job=job_id,seconds=round(time.time()-started,1))
    except _WorkerShutdown:
        with db() as c:
            if c.execute("UPDATE jobs SET status='QUEUED',lease_token=NULL,lease_until=NULL,attempts=CASE WHEN attempts>0 THEN attempts-1 ELSE 0 END WHERE id=? AND lease_token=? AND status='RUNNING'",(job_id,token)).rowcount:
                c.execute('INSERT INTO queue_events(job_id,event_type,details) VALUES(?,?,?)',(job_id,'released_on_shutdown','{}'))
        obs.info('job released on shutdown',job=job_id)
    except RenderCancelled as e:
        obs.info('job cancelled',job=job_id)
        with db() as c:
            if c.execute("UPDATE jobs SET status='CANCELLED',error=?,finished_at=CURRENT_TIMESTAMP,lease_until=NULL WHERE id=? AND lease_token=? AND status IN ('RUNNING','CANCELLED')",(str(e),job_id,token)).rowcount:
                _placement_render_failed(c,j['placement_id'],j['placement_version'],'Render cancelled')
                c.execute('INSERT INTO queue_events(job_id,event_type,details) VALUES(?,?,?)',(job_id,'cancelled',json.dumps({'error':str(e)})))
    except Exception as e:
        validation=e.report if isinstance(e,_ValidationFailed) else None
        message=str(e)[:1000]
        terminal=isinstance(e,(_GateError,_ValidationFailed,ValueError))
        with db() as c:
            row=c.execute('SELECT attempts,max_attempts,cancel_requested,lease_token FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row or row['lease_token']!=token: return   # lease lost: another worker owns this job now
            retry=not terminal and row['attempts']<row['max_attempts'] and not row['cancel_requested']
            status='QUEUED' if retry else 'CANCELLED' if row['cancel_requested'] else 'FAILED' if terminal else 'DEAD_LETTER'
            c.execute("UPDATE jobs SET status=?,error=?,validation_json=?,finished_at=CASE WHEN ?=1 THEN NULL ELSE CURRENT_TIMESTAMP END,lease_until=NULL,lease_token=CASE WHEN ?=1 THEN NULL ELSE lease_token END WHERE id=?",(status,message,json.dumps(validation) if validation else None,int(retry),int(retry),job_id))
            if not retry: _placement_render_failed(c,j['placement_id'],j['placement_version'],message)
            c.execute('INSERT INTO queue_events(job_id,event_type,details) VALUES(?,?,?)',(job_id,'retry_scheduled' if retry else status.lower(),json.dumps({'error':message,'attempts':row['attempts']})))
        obs.warn('job failed',job=job_id,status=status,retry=retry,error=message[:200])
    finally:
        if tmp is not None: Path(tmp).unlink(missing_ok=True)
        obs.job_id.set(None)

def _queue_render(c, u, p, mode='COMPOSITE', max_cost_usd=None):
    if p['status']=='RENDER_FAILED': _verify_chain(c,p,u)
    if p['status']!='READY_FOR_RENDER': raise _err(409,'NOT_READY_FOR_RENDER',f'Placement is {p["status"]}; it must be READY_FOR_RENDER (both approvals, verified rights and quality)',status=p['status'])
    try: v,prod,a=_render_gates(c,p)
    except _GateError as e: raise _err(409,e.code,str(e))
    routing=renderers.route(a['scene_class'],v['duration'],v['width'],v['height'],max_cost_usd)
    if not routing['selected']: raise _err(409,routing['reason_code'],routing['message'],routing=routing)
    limits.check_active_jobs(c,u['org_id'])
    jid=c.execute("INSERT INTO jobs(placement_id,org_id,status,mode,placement_version,analysis_id,renderer) VALUES(?,?,'QUEUED',?,?,?,?)",(p['id'],u['org_id'],mode,p['version'],a['id'],routing['selected'])).lastrowid
    _transition(c,p,'RENDERING',u,details={'job_id':jid,'renderer':routing['selected']})
    event(c,u,'render.queued','job',jid,{'renderer':routing['selected'],'placement_version':p['version']})
    c.commit(); obs.wake_workers()
    return {'id':jid,'status':'QUEUED','renderer':routing['selected'],'placement_version':p['version'],'routing':routing,'message':'Render queued for the background worker'}

@app.post('/api/placements/{pid}/render')
def start_render(pid:int,request:Request):
    u=current_user(request); require_role(u,'brand')
    with db() as c: return _queue_render(c,u,owned(c,'placements',pid,u['org_id']))
@app.post('/api/placements/{pid}/route-render')
def route_render(pid:int, request:Request, quality:str='balanced', max_cost_usd:float=1.0, max_latency_seconds:int=900):
    """Kept for API compatibility. Routing is decided by the analysed scene class, never by a requested quality tier."""
    u=current_user(request); require_role(u,'brand')
    with db() as c: out=_queue_render(c,u,owned(c,'placements',pid,u['org_id']),max_cost_usd=max_cost_usd)
    out['routing'].update({'selected_mode':out['renderer'],'quality_target':quality,'cost_ceiling_usd':max_cost_usd,'latency_budget_seconds':max_latency_seconds})
    return out
@app.post('/api/placements/{pid}/generative-render')
def start_generative_render(pid:int,request:Request):
    """Generative rendering stays unavailable until a configured provider passes a live integration check."""
    u=current_user(request); require_role(u,'brand')
    cap=next(p for p in renderers.capabilities() if p['id']=='replicate_generative')
    if not cap['available']:
        raise _err(503,'GENERATIVE_UNAVAILABLE','Generative rendering is not available: '+('the configured provider has not passed a live integration check (CC_REPLICATE_VERIFIED)' if cap['configured'] else 'no compatible provider is configured'),capability=cap)
    raise _err(409,'NO_VALIDATED_GENERATIVE_PATH','No scene class currently routes to generative rendering, because its outputs cannot be validated automatically.')

@app.get('/api/jobs')
def jobs(request:Request):
    u=current_user(request)
    with db() as c: rows=c.execute('SELECT j.*,p.video_id,p.product_id,v.title video_title,pr.name product_name FROM jobs j JOIN placements p ON p.id=j.placement_id JOIN videos v ON v.id=p.video_id JOIN products pr ON pr.id=p.product_id WHERE j.org_id=? OR v.org_id=? ORDER BY j.id DESC',(u['org_id'],u['org_id'])).fetchall()
    return [dict({k:r[k] for k in r.keys() if k not in ('validation_json','lease_token','output_path')},validation=(json.loads(r['validation_json']) if r['validation_json'] else None),download_url=f'/api/jobs/{r["id"]}/download' if r['status']=='SUCCEEDED' else None) for r in rows]
LINK_TTL_SECONDS=int(os.getenv('CC_DOWNLOAD_LINK_TTL','300'))

def _signing_key():
    """HMAC key for download links: CC_SIGNING_KEY, else a random key persisted (owner-only) in the data directory."""
    env=os.getenv('CC_SIGNING_KEY')
    if env: return env.encode()
    kp=DATA/'.signing_key'
    if not kp.exists():
        kp.write_bytes(secrets.token_bytes(32))
        try: os.chmod(kp,0o600)
        except OSError: pass
    return kp.read_bytes()

def _sign(payload):
    body=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode().rstrip('=')
    return body+'.'+hmac.new(_signing_key(),body.encode(),hashlib.sha256).hexdigest()

def _unsign(token):
    try:
        body,sig=token.rsplit('.',1)
        if not hmac.compare_digest(sig,hmac.new(_signing_key(),body.encode(),hashlib.sha256).hexdigest()): return None
        return json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
    except Exception: return None

@app.post('/api/jobs/{jid}/download-link')
def download_link(jid:int,request:Request,channel:Optional[str]=None):
    """Short-lived link bound to this job, user and placement version. All delivery gates are re-checked when it is used."""
    u=current_user(request)
    _authorize_delivery(jid,u,channel,record=False)
    with db() as c: j=c.execute('SELECT placement_version FROM jobs WHERE id=?',(jid,)).fetchone()
    exp=int(time.time())+LINK_TTL_SECONDS
    url=f'/api/media/{_sign({"j":jid,"u":u["id"],"v":j["placement_version"],"c":channel,"exp":exp})}'
    from .auth import public_url
    return {'url':url,'absolute_url':public_url()+url,'expires_at':exp,'ttl_seconds':LINK_TTL_SECONDS}

@app.get('/api/media/{token}')
def signed_media(token:str):
    data=_unsign(token)
    if not data: raise HTTPException(403,'Invalid download link')
    if data['exp']<time.time(): raise _err(410,'LINK_EXPIRED','This download link has expired; request a new one')
    with db() as c:
        row=c.execute('SELECT * FROM users WHERE id=? AND removed_at IS NULL AND suspended_at IS NULL',(data['u'],)).fetchone()
        if not row: raise HTTPException(403,'Invalid download link')
        from .members import can
        if not can(dict(row),'manager'): raise HTTPException(403,'Invalid download link')
        j=c.execute('SELECT placement_version FROM jobs WHERE id=?',(data['j'],)).fetchone()
        if not j or j['placement_version']!=data['v']: raise _err(403,'SUPERSEDED_VERSION','This link was issued for an earlier placement version')
    return _authorize_delivery(data['j'],dict(row),data.get('c'),record=True)

@app.get('/api/jobs/{jid}/download')
def download(jid:int,request:Request,channel:Optional[str]=None):
    """Deliver only a validated render of the current, approved, rights-cleared placement version."""
    return _authorize_delivery(jid,current_user(request),channel,record=True)

def _authorize_delivery(jid, u, channel=None, record=True):
    with db() as c:
        j=c.execute('SELECT j.* FROM jobs j JOIN placements p ON p.id=j.placement_id JOIN videos v ON v.id=p.video_id WHERE j.id=? AND (j.org_id=? OR v.org_id=?)',(jid,u['org_id'],u['org_id'])).fetchone()
        if not j: raise HTTPException(404,'Asset not found')
        if j['status']!='SUCCEEDED' or not j['output_path']: raise HTTPException(409,'Asset is not ready')
        p=dict(c.execute('SELECT * FROM placements WHERE id=?',(j['placement_id'],)).fetchone())
        if j['placement_version']!=p['version']: raise _err(403,'SUPERSEDED_VERSION','This render belongs to an earlier placement version and cannot be delivered')
        if p['status'] not in ('RENDERED','DELIVERED'): raise _err(403,'NOT_DELIVERABLE',f'Placement is {p["status"]}; delivery is blocked')
        if (json.loads(j['validation_json'] or '{}')).get('status')!='PASSED': raise _err(403,'OUTPUT_NOT_VALIDATED','The rendered output has not passed validation')
        try: _render_gates(c,p,dict(j))
        except _GateError as e: raise _err(403,e.code,'Delivery blocked: '+str(e))
        if channel and not rights_are_active(_rights(c,p),channel): raise _err(403,'CHANNEL_NOT_PERMITTED',f'The creator has not licensed the {channel} channel')
        path=j['output_path']
        if not storage.exists(path): raise HTTPException(410,'Rendered asset file is no longer available')
        if not record: return path
        if p['status']=='RENDERED':
            lifecycle.transition(c,p,'DELIVERED',u['id'],details={'job_id':jid})
            from .commerce import on_delivered
            on_delivered(c,p)
        c.execute('INSERT INTO events(org_id,user_id,event_type,entity_type,entity_id,details) VALUES(?,?,?,?,?,?)',(u['org_id'],u['id'],'asset.downloaded','job',jid,json.dumps({'placement_version':p['version'],'channel':channel})))
    return storage.response(path,media_type='video/mp4',filename=f'context-commerce-placement-{p["id"]}-v{p["version"]}.mp4')

@app.patch('/api/placements/{pid}/revise')
def revise_placement(pid:int, request:Request, frame_second:float=Form(...), x:float=Form(...), y:float=Form(...), width:float=Form(...), start_second:float=Form(...), end_second:float=Form(...), tracking_quad:str=Form(''), color_match:bool=Form(True), adjust:str=Form(''), model3d:str=Form('')):
    """Create a new immutable placement version; approvals, consent, quality and fit for the old version cannot authorize it."""
    u=current_user(request); require_role(u,'brand')
    if not (0<=x<=1 and 0<=y<=1 and .02<width<=.7): raise HTTPException(422,'Invalid placement geometry or timing')
    try: quad=json.loads(tracking_quad) if tracking_quad.strip() else None
    except ValueError: raise HTTPException(422,'tracking_quad must be JSON')
    with db() as c:
        p=owned(c,'placements',pid,u['org_id']); v=owned(c,'videos',p['video_id'],u['org_id'])
        if p['status'] not in lifecycle.REVISABLE: raise _err(409,'NOT_REVISABLE',f'Placement is {p["status"]} and cannot be revised')
        if not _license_ok(v): raise HTTPException(403,'Video license permission is missing or expired')
        opts=_validate_geometry(v,frame_second,start_second,end_second,quad)
        if opts.get('tracking_quad'): opts['color_match']=color_match
        try: adj=_validate_adjust(json.loads(adjust)) if adjust.strip() else None
        except ValueError: raise HTTPException(422,'adjust must be JSON')
        if adj: opts['adjust']=adj
        if model3d.strip():
            if not opts.get('tracking_quad'): raise HTTPException(422,'3D placements need the supporting surface corners (tracking_quad)')
            try: m3=json.loads(model3d)
            except ValueError: raise HTTPException(422,'model3d must be JSON')
            opts['model3d']=_validate_model3d(m3,owned(c,'products',p['product_id'],u['org_id']))
        version=p['version']+1
        c.execute("UPDATE placements SET frame_second=?,x=?,y=?,width=?,start_second=?,end_second=?,version=?,status='DRAFT',creator_approved=0,brand_approved=0,fit_confidence=0,fit_status='REVIEW_REQUIRED',quality_status='NOT_EVALUATED',analysis_id=NULL,render_options=? WHERE id=? AND version=?",(frame_second,x,y,width,start_second,end_second,version,json.dumps(opts),pid,p['version']))
        c.execute('INSERT INTO placement_rights(placement_id,placement_version,creator_consent,expires_at,updated_by) VALUES(?,?,?,?,?) ON CONFLICT(placement_id) DO UPDATE SET placement_version=excluded.placement_version,permitted_channels=\'["social","web"]\',territories=\'["WORLDWIDE"]\',start_at=NULL,expires_at=excluded.expires_at,creator_consent=0,revoked_at=NULL,revocation_reason=NULL,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP',(pid,version,0,v['license_expires'],u['id']))
        _apply_grant_terms(c,pid,v,u['org_id'])
        c.execute("UPDATE tracked_links SET active=0 WHERE placement_id=?",(pid,))
        c.execute("UPDATE jobs SET cancel_requested=1,status=CASE WHEN status='QUEUED' THEN 'CANCELLED' ELSE status END,error='Superseded by placement revision' WHERE placement_id=? AND status IN ('QUEUED','RUNNING')",(pid,))
        digest=_record_version(c,pid,u['id'])
        event(c,u,'placement.revised','placement',pid,{'new_version':version,'previous_version':p['version'],'from_status':p['status'],'content_hash':digest})
        event(c,u,'placement.transition','placement',pid,{'from':p['status'],'to':'DRAFT','version':version,'reason':'revision'})
    return {'id':pid,'version':version,'status':'DRAFT','content_hash':digest,'fit_status':'REVIEW_REQUIRED','quality_status':'NOT_EVALUATED','approvals_reset':True,'creator_consent_required':True}

@app.get('/api/placements/{pid}/rights')
def get_placement_rights(pid:int, request:Request):
    u=current_user(request)
    with db() as c:
        p=owned(c,'placements',pid,u['org_id']); row=_rights(c,p)
        if not row: raise HTTPException(404,'Rights record not found for current placement version')
        out=dict(row); out['permitted_channels']=json.loads(out['permitted_channels']); out['territories']=json.loads(out['territories']); return out

@app.put('/api/placements/{pid}/rights')
def update_placement_rights(pid:int, body:RightsIn, request:Request):
    u=current_user(request); require_role(u,'brand','creator')
    allowed_channels={'social','web','paid_ads','broadcast','ott','in_app'}
    if not set(body.permitted_channels).issubset(allowed_channels): raise HTTPException(422,'Unsupported permitted channel')
    if any(not (t=='WORLDWIDE' or (len(t)==2 and t.isalpha()) or (len(t)==3 and t.isalpha())) for t in body.territories): raise HTTPException(422,'Territories must use WORLDWIDE or ISO country/region codes')
    if body.expires_at and body.start_at and body.expires_at < body.start_at: raise HTTPException(422,'Rights expiry must be after start date')
    with db() as c:
        p=owned(c,'placements',pid,u['org_id']); v=owned(c,'videos',p['video_id'],u['org_id'])
        if p['status'] not in ('DRAFT','PROPOSED'): raise _err(409,'RIGHTS_LOCKED','Rights terms are locked after creator approval. Revise the placement to change them.')
        if v['org_id']!=p['org_id']:
            g=_grant_for(c,v,p['org_id'])
            if not g: raise _err(409,'LICENCE_NOT_ACTIVE','The brand no longer holds a licence for this video')
            gt=json.loads(g['territories'])
            if not set(body.permitted_channels)<=set(json.loads(g['channels'])): raise _err(409,'OUTSIDE_LICENCE','Channels must stay within the licence: '+', '.join(json.loads(g['channels'])))
            if 'WORLDWIDE' not in gt and not set(body.territories)<=set(gt): raise _err(409,'OUTSIDE_LICENCE','Territories must stay within the licence: '+', '.join(gt))
            if g['expires_at'] and (not body.expires_at or body.expires_at[:10]>g['expires_at'][:10]): raise _err(409,'OUTSIDE_LICENCE','Rights must expire on or before the licence end '+g['expires_at'][:10])
        if u['role']=='creator' and v['created_by']!=u['id']: raise _err(403,'NOT_RIGHTS_HOLDER','Only the creator who holds rights to this video can grant or change consent')
        existing=_rights(c,p); consent=int(body.creator_consent)
        if u['role']=='brand':
            if not existing and body.creator_consent: raise HTTPException(403,'Only the creator can grant creator consent')
            if existing and int(body.creator_consent)!=int(existing['creator_consent']): raise HTTPException(403,'Only the creator can grant or withdraw creator consent')
            # Consent was given for specific terms; a brand-side change of terms requires fresh consent.
            same=existing and json.loads(existing['permitted_channels'])==body.permitted_channels and json.loads(existing['territories'])==body.territories and existing['start_at']==body.start_at and existing['expires_at']==body.expires_at
            if not same: consent=0
        c.execute('INSERT INTO placement_rights(placement_id,placement_version,permitted_channels,territories,start_at,expires_at,creator_consent,updated_by) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(placement_id) DO UPDATE SET placement_version=excluded.placement_version,permitted_channels=excluded.permitted_channels,territories=excluded.territories,start_at=excluded.start_at,expires_at=excluded.expires_at,creator_consent=excluded.creator_consent,revoked_at=NULL,revocation_reason=NULL,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP',(pid,p['version'],json.dumps(body.permitted_channels),json.dumps(body.territories),body.start_at,body.expires_at,consent,u['id']))
        event(c,u,'rights.updated','placement',pid,{'version':p['version'],'channels':body.permitted_channels,'territories':body.territories,'creator_consent':bool(consent)})
    return {'placement_id':pid,'placement_version':p['version'],'creator_consent':bool(consent),'permitted_channels':body.permitted_channels,'territories':body.territories,'expires_at':body.expires_at}

@app.post('/api/placements/{pid}/revoke-rights')
def revoke_rights(pid:int, request:Request, reason:str=Form(...)):
    u=current_user(request); require_role(u,'creator','brand')
    if len(reason.strip())<3: raise HTTPException(422,'Provide a clear revocation reason')
    with db() as c:
        p=owned(c,'placements',pid,u['org_id']); v=owned(c,'videos',p['video_id'],u['org_id'])
        if u['role']=='creator' and v['created_by']!=u['id']: raise _err(403,'NOT_RIGHTS_HOLDER','Only the rights holder can revoke on the creator side')
        c.execute('UPDATE placement_rights SET revoked_at=CURRENT_TIMESTAMP,revocation_reason=?,updated_by=? WHERE placement_id=? AND placement_version=?',(reason[:500],u['id'],pid,p['version']))
        c.execute("UPDATE jobs SET status=CASE WHEN status='QUEUED' THEN 'CANCELLED' ELSE status END,cancel_requested=1,error='Rights revoked' WHERE placement_id=? AND status IN ('QUEUED','RUNNING')",(pid,))
        c.execute('UPDATE tracked_links SET active=0 WHERE placement_id=?',(pid,))
        if 'REVOKED' in lifecycle.TRANSITIONS.get(p['status'],()): lifecycle.transition(c,p,'REVOKED',u['id'],reason[:500])
        event(c,u,'rights.revoked','placement',pid,{'version':p['version'],'reason':reason[:500]})
    return {'placement_id':pid,'revoked':True,'delivery_blocked':True,'status':p['status']}

@app.post('/api/videos/{vid}/surface-proposals')
def surface_proposals(vid:int, request:Request, frame_second:float=Form(1.0)):
    """Generate conservative planar-surface proposals from the selected frame. Candidates require human review."""
    u=current_user(request)
    with db() as c:
        v=owned(c,'videos',vid,u['org_id'])
        frame=c.execute('SELECT image_path,second FROM frames WHERE video_id=? ORDER BY ABS(second-?) LIMIT 1',(vid,frame_second)).fetchone()
    if not frame: raise HTTPException(409,'Analyze the video first to generate keyframes')
    frame_second=frame['second']   # proposals belong to the keyframe actually analysed
    if not _has_cv2(): raise HTTPException(503,'OpenCV is required for surface proposals')
    import cv2, numpy as np
    image=cv2.imread(storage.local(frame['image_path']))
    if image is None: raise HTTPException(500,'Could not read selected keyframe')
    h,w=image.shape[:2]; gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    edges=cv2.Canny(gray,60,160); contours,_=cv2.findContours(edges,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
    candidates=[]
    for contour in sorted(contours,key=cv2.contourArea,reverse=True)[:80]:
        area=cv2.contourArea(contour)/(w*h)
        if area < .025 or area > .75: continue
        peri=cv2.arcLength(contour,True); approx=cv2.approxPolyDP(contour,.025*peri,True)
        if len(approx)!=4 or not cv2.isContourConvex(approx): continue
        pts=approx.reshape(4,2).astype(float)
        center=pts.mean(axis=0); angles=np.arctan2(pts[:,1]-center[1],pts[:,0]-center[0]); pts=pts[np.argsort(angles)]
        # Normalize and rotate to approximate top-left first; candidate remains a proposal, not an automatic acceptance.
        start=int(np.argmin(pts.sum(axis=1))); pts=np.roll(pts,-start,axis=0)
        quad=(pts/np.array([w,h])).clip(0,1).tolist()
        patch=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY); mask=np.zeros((h,w),np.uint8); cv2.fillConvexPoly(mask,pts.astype(np.int32),255)
        lap=cv2.Laplacian(patch,cv2.CV_64F); texture=float(lap[mask>0].var()) if (mask>0).any() else 0.0; texture_score=min(1.0,texture/250.0)
        aspect=max(np.linalg.norm(pts[1]-pts[0]),1)/max(np.linalg.norm(pts[2]-pts[1]),1)
        aspect_score=1.0 if .2<aspect<5 else .4
        confidence=round(max(0,min(.89,.35*min(area/.12,1)+.35*texture_score+.3*aspect_score)),3)
        candidates.append({'quad':quad,'confidence':confidence,'fit_score':confidence,'area_fraction':round(area,3),'texture_score':round(texture_score,3),'aspect_ratio':round(aspect,3)})
        if len(candidates)>=8: break
    with db() as c:
        ids=[]
        for cand in candidates:
            cur=c.execute('INSERT INTO surface_proposals(video_id,org_id,frame_second,quad_json,confidence,fit_score,evidence_json) VALUES(?,?,?,?,?,?,?)',(vid,u['org_id'],frame_second,json.dumps(cand['quad']),cand['confidence'],cand['fit_score'],json.dumps({k:v for k,v in cand.items() if k not in ('quad','confidence','fit_score')})))
            ids.append(cur.lastrowid)
        event(c,u,'surface.proposals_generated','video',vid,{'count':len(ids),'frame_second':frame_second})
    return {'video_id':vid,'frame_second':frame_second,'proposals':[dict(id=i,**cand,status='PROPOSED',requires_human_review=True) for i,cand in zip(ids,candidates)],'message':'CV contour candidates only. They are not semantic proof that a product fits; review or correct the quadrilateral before use.'}

@app.post('/api/surface-proposals/{proposal_id}/review')
def review_surface(proposal_id:int, body:SurfaceReviewIn, request:Request):
    u=current_user(request)
    if body.decision not in ('accept','reject','correct'): raise HTTPException(422,'decision must be accept, reject, or correct')
    with db() as c:
        row=c.execute('SELECT * FROM surface_proposals WHERE id=? AND org_id=?',(proposal_id,u['org_id'])).fetchone()
        if not row: raise HTTPException(404,'Surface proposal not found')
        quad=json.loads(row['quad_json'])
        if body.decision=='correct':
            if body.quad is None: raise HTTPException(422,'Corrected quad is required')
            try:
                from .tracking import validate_quad
                quad=validate_quad(body.quad).tolist()
            except Exception as e: raise HTTPException(422,str(e))
        status={'accept':'ACCEPTED','reject':'REJECTED','correct':'CORRECTED'}[body.decision]
        c.execute('UPDATE surface_proposals SET quad_json=?,status=?,reviewed_by=? WHERE id=?',(json.dumps(quad),status,u['id'],proposal_id))
        event(c,u,'surface.reviewed','surface_proposal',proposal_id,{'decision':body.decision,'confidence':row['confidence'],'notes':body.notes})
    return {'id':proposal_id,'status':status,'quad':quad,'confidence':row['confidence'],'confidence_is_heuristic':True}

@app.post('/api/placements/{pid}/fit-review')
def fit_review(pid:int, request:Request, decision:str=Form(...), confidence:float=Form(1.0), notes:str=Form('')):
    """Human judgement that the product belongs in the scene. Required in addition to the automated quality check."""
    u=current_user(request); require_role(u,'brand','creator')
    if decision not in ('accept','reject'): raise HTTPException(422,'decision must be accept or reject')
    if not 0<=confidence<=1: raise HTTPException(422,'confidence must be between 0 and 1')
    with db() as c:
        p=owned(c,'placements',pid,u['org_id'])
        if decision=='accept' and confidence<.75: raise _err(422,'FIT_CONFIDENCE_TOO_LOW','Confidence below the 0.75 threshold. Correct the placement or reject it; it will not be rendered.')
        if p['status']!='DRAFT': raise _err(409,'NOT_DRAFT','Fit review applies to draft versions. Revise the placement to change it.')
        status='ACCEPTED' if decision=='accept' else 'REJECTED'
        c.execute('UPDATE placements SET fit_confidence=?,fit_status=? WHERE id=?',(confidence,status,pid))
        event(c,u,'placement.fit_reviewed','placement',pid,{'decision':decision,'confidence':confidence,'fit_status':status,'notes':notes[:1000],'version':p['version']})
        if status=='REJECTED': _transition(c,p,'REJECTED',u,'Visual fit rejected by reviewer',{'notes':notes[:500]})
    return {'id':pid,'fit_status':status,'confidence':confidence,'threshold':.75,'confidence_is_human_attestation':True,'status':p['status']}

@app.post('/api/placements/{pid}/tracked-links')
def create_tracked_link(pid:int, body:TrackedLinkIn, request:Request):
    u=current_user(request); require_role(u,'brand')
    from urllib.parse import urlparse
    parsed=urlparse(body.destination_url)
    if parsed.scheme!='https' or not parsed.netloc: raise HTTPException(422,'Destination URL must be a valid HTTPS URL')
    with db() as c:
        p=owned(c,'placements',pid,u['org_id'])
        if p['status'] not in ('RENDERED','DELIVERED'): raise _err(409,'NOT_DELIVERABLE','Only rendered, approved placements can be promoted')
        if not rights_are_active(_rights(c,p),body.channel): raise _err(409,'CHANNEL_NOT_PERMITTED',f'The creator has not licensed the {body.channel} channel for this version')
        slug=secrets.token_urlsafe(9)
        c.execute('INSERT INTO tracked_links(org_id,placement_id,placement_version,slug,destination_url,campaign,created_by,channel) VALUES(?,?,?,?,?,?,?,?)',(u['org_id'],pid,p['version'],slug,body.destination_url,body.campaign,u['id'],body.channel))
        event(c,u,'tracked_link.created','placement',pid,{'slug':slug,'campaign':body.campaign,'channel':body.channel})
    return {'slug':slug,'tracking_url':f'/r/{slug}','destination_url':body.destination_url,'placement_version':p['version'],'channel':body.channel}

@app.get('/r/{slug}')
def redirect_tracked(slug:str, request:Request):
    from fastapi.responses import RedirectResponse
    with db() as c:
        row=c.execute('SELECT * FROM tracked_links WHERE slug=? AND active=1',(slug,)).fetchone()
        if not row: raise HTTPException(404,'Tracking link not found or inactive')
        p=c.execute('SELECT * FROM placements WHERE id=? AND org_id=?',(row['placement_id'],row['org_id'])).fetchone()
        rights=c.execute('SELECT * FROM placement_rights WHERE placement_id=? AND placement_version=?',(row['placement_id'],row['placement_version'])).fetchone()
        if not p or p['version']!=row['placement_version'] or p['status'] not in ('RENDERED','DELIVERED') or not rights_are_active(rights, row['channel']):
            c.execute('UPDATE tracked_links SET active=0 WHERE id=?',(row['id'],)); c.commit(); raise HTTPException(410,'Campaign link disabled because placement version or rights are no longer valid')
        c.execute('INSERT INTO events(org_id,user_id,event_type,entity_type,entity_id,details) VALUES(?,?,?,?,?,?)',(row['org_id'],row['created_by'],'campaign.click','tracked_link',row['id'],json.dumps({'slug':slug,'campaign':row['campaign'],'channel':row['channel']})))
        target=row['destination_url']
    return RedirectResponse(target,status_code=302)

@app.post('/api/conversions/import')
def import_conversion(body:ConversionIn, request:Request):
    u=current_user(request); require_role(u,'brand')
    with db() as c:
        link=None
        if body.link_slug:
            link=c.execute('SELECT * FROM tracked_links WHERE slug=? AND org_id=?',(body.link_slug,u['org_id'])).fetchone()
            if not link: raise HTTPException(404,'Tracking link not found')
        try:
            conv_id=c.execute('INSERT INTO conversions(org_id,link_id,external_id,event_name,value,currency,occurred_at,source,dedupe_key) VALUES(?,?,?,?,?,?,?,?,?)',(u['org_id'],link['id'] if link else None,body.external_id,body.event_name,body.value,body.currency.upper(),body.occurred_at,'manual_import',body.dedupe_key)).lastrowid
        except dbm.IntegrityError: raise HTTPException(409,'Duplicate conversion dedupe_key')
        event(c,u,'conversion.imported','conversion',conv_id,{'event_name':body.event_name,'value':body.value,'currency':body.currency.upper(),'source':'observed_import'})
    return {'imported':True,'attribution':'observed_click_attribution_only','causal_lift':'NOT_MEASURED','note':'Observed conversions are not evidence of incremental lift.'}

@app.get('/api/campaign-analytics')
def campaign_analytics(request:Request):
    u=current_user(request)
    with db() as c:
        clicks=c.execute("SELECT COUNT(*) FROM events WHERE org_id=? AND event_type='campaign.click'",(u['org_id'],)).fetchone()[0]
        conversions=c.execute('SELECT COUNT(*) n,COALESCE(SUM(value),0) value FROM conversions WHERE org_id=?',(u['org_id'],)).fetchone()
        attributed=c.execute('SELECT COUNT(*) FROM conversions WHERE org_id=? AND link_id IS NOT NULL',(u['org_id'],)).fetchone()[0]
    return {'observed_clicks':clicks,'imported_conversions':conversions['n'],'conversion_value':conversions['value'],'conversions_with_link_attribution':attributed,'causal_incrementality':'NOT_MEASURED','warning':'No randomized holdout or valid counterfactual experiment has been run.'}

@app.post('/api/jobs/{jid}/cancel')
def cancel_job(jid:int, request:Request):
    u=current_user(request); require_role(u,'brand')
    with db() as c:
        j=c.execute('SELECT * FROM jobs WHERE id=? AND org_id=?',(jid,u['org_id'])).fetchone()
        if not j: raise HTTPException(404,'Job not found')
        if j['status'] not in ('QUEUED','RUNNING'): raise HTTPException(409,'Only queued or running jobs can be cancelled')
        # A running job stops at its next heartbeat; a queued one is cancelled immediately.
        c.execute("UPDATE jobs SET cancel_requested=1,status=CASE WHEN status='QUEUED' THEN 'CANCELLED' ELSE status END,error='Cancelled by user' WHERE id=?",(jid,))
        if j['status']=='QUEUED': _placement_render_failed(c,j['placement_id'],j['placement_version'],'Render cancelled by user',u['id'])
        c.execute('INSERT INTO queue_events(job_id,event_type,details) VALUES(?,?,?)',(jid,'cancel_requested','{}'))
    return {'id':jid,'cancel_requested':True}

@app.get('/api/placements/{pid}/preview')
def preview_frame(pid:int,request:Request,t:float=0.0,original:bool=False):
    """Diagnostic frame built from the exact render plan. Watermarked UNAPPROVED PREVIEW and never deliverable."""
    u=current_user(request)
    import cv2
    from fastapi.responses import Response
    from .compositor import render_frame
    with db() as c:
        p=owned(c,'placements',pid,u['org_id']); a=_current_analysis(c,p)
        if not a: raise _err(409,'NO_ANALYSIS','Run the quality check for this version first')
        v=owned(c,'videos',p['video_id'],u['org_id']); prod=dict(c.execute('SELECT * FROM products WHERE id=?',(p['product_id'],)).fetchone())
    plan,_=_build_plan(a,prod,p)
    fps=json.loads(a['report_json'])['interval']['fps']
    src,out=render_frame(storage.local(v['source_path']),plan,max(0,round(t*fps)))
    img=(src if original else out).copy()
    for color,thick in (((255,255,255),3),((30,30,200),1)):
        cv2.putText(img,'UNAPPROVED PREVIEW',(10,img.shape[0]-12),cv2.FONT_HERSHEY_SIMPLEX,.55,color,thick,cv2.LINE_AA)
    ok,buf=cv2.imencode('.jpg',img,[cv2.IMWRITE_JPEG_QUALITY,90])
    return Response(buf.tobytes(),media_type='image/jpeg',headers={'Cache-Control':'no-store','X-Preview':'unapproved'})

@app.get('/api/renderers')
def renderer_capabilities(request:Request):
    current_user(request)
    return {'providers':renderers.capabilities(),'routing':'least expensive verified provider supporting the analysed scene class; no silent fallback'}

@app.get('/api/videos/{vid}/stream')
def stream_video(vid:int,request:Request):
    u=current_user(request)
    with db() as c: v=owned(c,'videos',vid,u['org_id'])
    return storage.response(v['source_path'],media_type=mimetypes.guess_type(v['source_path'])[0] or 'video/mp4')

@app.get('/api/analytics')
def analytics(request:Request):
    u=current_user(request)
    with db() as c:
        def count(q,args=(u['org_id'],)): return c.execute(q,args).fetchone()[0]
        return {'products':count('SELECT COUNT(*) FROM products WHERE org_id=?'),'videos':count('SELECT COUNT(*) FROM videos WHERE org_id=?'),'placements':count('SELECT COUNT(*) FROM placements WHERE org_id=?'),'renders_succeeded':count("SELECT COUNT(*) FROM jobs WHERE org_id=? AND status='SUCCEEDED'"),'renders_failed':count("SELECT COUNT(*) FROM jobs WHERE org_id=? AND status='FAILED'"),'downloads':count("SELECT COUNT(*) FROM events WHERE org_id=? AND event_type='asset.downloaded'"),'events':count('SELECT COUNT(*) FROM events WHERE org_id=?')}
@app.get('/api/observability')
def observability(request:Request):
    u=current_user(request)
    with db() as c:
        jobs_by_status={r['status']:r['n'] for r in c.execute('SELECT status,COUNT(*) n FROM jobs WHERE org_id=? GROUP BY status',(u['org_id'],)).fetchall()}
        recent_errors=[dict(r) for r in c.execute("SELECT id,status,error,attempts,created_at,finished_at FROM jobs WHERE org_id=? AND status IN ('FAILED','CANCELLED') ORDER BY id DESC LIMIT 20",(u['org_id'],)).fetchall()]
        queue_depth=c.execute("SELECT COUNT(*) FROM jobs WHERE org_id=? AND status='QUEUED'",(u['org_id'],)).fetchone()[0]
        return {'queue_depth':queue_depth,'jobs_by_status':jobs_by_status,'recent_failures':recent_errors,'quality_rejections':_quality_rejections(c,u['org_id']),'queue_events':[dict(r) for r in c.execute('SELECT q.job_id,q.event_type,q.created_at FROM queue_events q JOIN jobs j ON j.id=q.job_id WHERE j.org_id=? ORDER BY q.id DESC LIMIT 30',(u['org_id'],)).fetchall()],'lease_seconds':LEASE_SECONDS,'request_rate_limit':'shared SQLite; single database deployment only','distributed_queue':'not_configured','trace_export':'not_configured'}

@app.get('/api/events')
def events(request:Request):
    u=current_user(request)
    with db() as c: rows=c.execute('SELECT event_type,entity_type,entity_id,details,created_at FROM events WHERE org_id=? ORDER BY id DESC LIMIT 100',(u['org_id'],)).fetchall()
    return [dict(r) for r in rows]

from .commerce import router as commerce_router   # noqa: E402  (imports this module)
app.include_router(commerce_router)
from .auth import router as auth_router  # noqa: E402
app.include_router(auth_router)
from .members import router as members_router  # noqa: E402
app.include_router(members_router)
from .uploads import router as uploads_router  # noqa: E402
app.include_router(uploads_router)
from .payments import router as payments_router  # noqa: E402
app.include_router(payments_router)
