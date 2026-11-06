"""Operations: structured logging with request and job ids, liveness/readiness checks and operator metrics.

Logs are one JSON object per line (CC_LOG_FORMAT=json, the default in containers) or plain text. They never contain
passwords, session or sign-in tokens, signed links, query strings or email addresses.
"""
import calendar, contextvars, json, logging, os, re, shutil, socket, sys, time
from pathlib import Path

request_id = contextvars.ContextVar('request_id', default=None)
job_id = contextvars.ContextVar('job_id', default=None)
_REDACT = [(re.compile(r'/api/media/[^ ?"]+'), '/api/media/[REDACTED]'), (re.compile(r'/api/delivery-media/[^ ?"]+'), '/api/delivery-media/[REDACTED]'),
           (re.compile(r'/auth/email/[^ ?"/]+'), '/auth/email/[REDACTED]'),
           # The password in any connection string (postgresql://user:PASSWORD@host), whatever characters it holds.
           (re.compile(r'(?i)\b([a-z][a-z0-9+.-]*://[^:/@\s"\']*):[^@\s"\']*@'), r'\1:[REDACTED]@'),
           (re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+'), 'Bearer [REDACTED]'),
           (re.compile(r'(?i)(token|code|password|secret|signature|x-amz-[a-z-]+)=[^&\s"]+'), r'\1=[REDACTED]'),
           (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), '[email]')]
# Configured secrets are also removed by value, wherever they appear (an exception message can quote one). These are
# the secrets the application reads, plus the S3 key boto3 reads and the token Vercel injects into functions.
SECRET_SETTINGS = ('CC_SIGNING_KEY', 'GOOGLE_CLIENT_SECRET', 'CC_SMTP_PASSWORD', 'BLOB_READ_WRITE_TOKEN', 'CC_OPS_TOKEN',
                   'AWS_SECRET_ACCESS_KEY', 'VERCEL_OIDC_TOKEN')


def _secret_values():
    values = [os.getenv(k, '') for k in SECRET_SETTINGS]
    for k in ('DATABASE_URL', 'CC_DATABASE_URL'):
        m = re.match(r'^[a-z][a-z0-9+.-]*://[^:/@]*:([^@]+)@', os.getenv(k, ''), re.I)
        if m: values.append(m.group(1))
    return sorted({v for v in values if len(v) >= 8}, key=len, reverse=True)


def redact(text):
    for v in _secret_values(): text = text.replace(v, '[REDACTED]')
    for rx, rep in _REDACT: text = rx.sub(rep, text)
    return text


def _redact_value(v):
    if isinstance(v, str): return redact(v)
    if isinstance(v, dict): return {k: _redact_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)): return [_redact_value(x) for x in v]
    return v


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(record.created)) + f'.{int(record.msecs):03d}Z', 'level': record.levelname.lower(),
               'logger': record.name, 'msg': redact(record.getMessage())}
        for k in ('request_id', 'job_id'):
            v = getattr(record, k, None) or (request_id if k == 'request_id' else job_id).get()
            if v: out[k] = v
        for k, v in getattr(record, 'fields', {}).items(): out[k] = _redact_value(v)
        if record.exc_info: out['error'] = redact(self.formatException(record.exc_info))[-2000:]
        return json.dumps(out, default=str)


class _TextFormatter(logging.Formatter):
    def format(self, record):
        extra = ' '.join(f'{k}={v}' for k, v in getattr(record, 'fields', {}).items())
        rid = request_id.get() or job_id.get()
        return redact(f'{time.strftime("%H:%M:%S")} {record.levelname:<7} {record.name} {record.getMessage()} {extra}' + (f' [{rid}]' if rid else '')).rstrip()


_configured = False


def configure_logging():
    global _configured
    if _configured: return
    _configured = True
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter() if os.getenv('CC_LOG_FORMAT', 'text') == 'json' else _TextFormatter())
    root = logging.getLogger('cc'); root.handlers[:] = [handler]; root.setLevel(os.getenv('CC_LOG_LEVEL', 'INFO')); root.propagate = False
    # Uvicorn's own access log would duplicate ours and print raw paths; keep its error log.
    logging.getLogger('uvicorn.access').disabled = os.getenv('CC_UVICORN_ACCESS_LOG', '0') != '1'


log = logging.getLogger('cc')


def info(msg, **fields): log.info(msg, extra={'fields': fields})
def warn(msg, **fields): log.warning(msg, extra={'fields': fields})
def error(msg, exc_info=False, **fields): log.error(msg, exc_info=exc_info, extra={'fields': fields})


# ---------------------------------------------------------------- worker heartbeats (files: no database traffic)
def run_dir():
    from . import main
    d = main.DATA / 'run'; d.mkdir(parents=True, exist_ok=True); return d


def worker_heartbeat(state='idle'):
    p = run_dir() / f'worker-{socket.gethostname()}-{os.getpid()}.json'
    tmp = p.with_suffix('.tmp'); tmp.write_text(json.dumps({'pid': os.getpid(), 'host': socket.gethostname(), 'state': state, 'at': time.time()})); os.replace(tmp, p)


def worker_heartbeat_clear():
    """Remove this worker's heartbeat on a clean stop, so a restart is not counted as an extra live worker."""
    (run_dir() / f'worker-{socket.gethostname()}-{os.getpid()}.json').unlink(missing_ok=True)


def worker_status(max_age=None):
    max_age = max_age or float(os.getenv('CC_WORKER_STALE_SECONDS', '120'))
    now, workers = time.time(), []
    for f in run_dir().glob('worker-*.json'):
        try: d = json.loads(f.read_text())
        except Exception: continue
        age = now - d.get('at', 0)
        if age > 86400: f.unlink(missing_ok=True); continue
        workers.append({'state': d.get('state'), 'age_seconds': round(age, 1), 'alive': age <= max_age})
    return {'alive': sum(w['alive'] for w in workers), 'workers': workers}


def wake_workers():
    """Tell workers sharing this data volume that a job is waiting (a file touch; no database round trip)."""
    try: (run_dir() / 'worker.wake').write_text(str(time.time()))
    except OSError: pass
    from . import jobrunner
    if jobrunner.enabled(): jobrunner.kick()


def wake_mtime():
    try: return (run_dir() / 'worker.wake').stat().st_mtime
    except OSError: return 0.0


def disk_status():
    from . import main
    u = shutil.disk_usage(main.DATA)
    return {'free_gb': round(u.free / 1e9, 2), 'used_percent': round(100 * (u.used / u.total), 1)}


def cache_bytes():
    from . import main, storage
    total = 0
    for d in storage.KNOWN_DIRS:
        for f in (main.DATA / d).rglob('*') if (main.DATA / d).exists() else []:
            try: total += f.stat().st_size if f.is_file() else 0
            except OSError: pass
    return total


def metrics(c):
    """Platform-wide operational metrics for the operator (never exposed without CC_OPS_TOKEN)."""
    now = time.time(); day = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now - 86400))
    jobs = {r['status']: r['n'] for r in c.execute('SELECT status,COUNT(*) n FROM jobs GROUP BY status').fetchall()}
    oldest = c.execute("SELECT MIN(created_at) FROM jobs WHERE status='QUEUED'").fetchone()[0]
    fin = c.execute("SELECT status,COUNT(*) n FROM jobs WHERE finished_at>=? GROUP BY status", (day,)).fetchall()
    fin = {r['status']: r['n'] for r in fin}
    done = sum(fin.values()) or 0
    retries = c.execute("SELECT COUNT(*) FROM queue_events WHERE event_type IN ('retry_scheduled','lease_recovered') AND created_at>=?", (day,)).fetchone()[0]
    durations = sorted(r[0] for r in c.execute("SELECT (julianday(finished_at)-julianday(started_at))*86400 FROM jobs WHERE status='SUCCEEDED' AND finished_at>=?", (day,)).fetchall()
                       if r[0] is not None) if c.kind == 'sqlite' else sorted(r[0] for r in c.execute(
                       "SELECT EXTRACT(EPOCH FROM (finished_at::timestamp - started_at::timestamp)) FROM jobs WHERE status='SUCCEEDED' AND finished_at>=?", (day,)).fetchall() if r[0] is not None)
    pct = lambda q: round(float(durations[min(len(durations) - 1, int(q * len(durations)))]), 1) if durations else None
    ev = {r['event_type']: r['n'] for r in c.execute("SELECT event_type,COUNT(*) n FROM events WHERE created_at>=? AND event_type IN ('upload.failed','email.failed','auth.google_failed','asset.downloaded','download.denied') GROUP BY event_type", (day,)).fetchall()}
    oldest_age = round(now - calendar.timegm(time.strptime(oldest, '%Y-%m-%d %H:%M:%S')), 1) if oldest else None   # stored as UTC text
    return {'queue': {'depth': jobs.get('QUEUED', 0), 'running': jobs.get('RUNNING', 0), 'oldest_queued_age_seconds': oldest_age, 'dead_letter_total': jobs.get('DEAD_LETTER', 0)},
            'jobs_24h': {'finished': done, 'succeeded': fin.get('SUCCEEDED', 0), 'failed': fin.get('FAILED', 0) + fin.get('DEAD_LETTER', 0),
                         'failure_rate': round((fin.get('FAILED', 0) + fin.get('DEAD_LETTER', 0)) / done, 3) if done else 0.0, 'retries': retries,
                         'render_seconds_p50': pct(.5), 'render_seconds_p95': pct(.95)},
            'events_24h': {'upload_failures': ev.get('upload.failed', 0), 'email_failures': ev.get('email.failed', 0), 'oauth_failures': ev.get('auth.google_failed', 0),
                           'downloads': ev.get('asset.downloaded', 0), 'download_denials': ev.get('download.denied', 0)},
            'organizations': c.execute('SELECT COUNT(*) FROM organizations').fetchone()[0],
            'analyses': _analysis_metrics(c, day), 'payments_24h': _payment_metrics(c, day), 'ledger': _ledger_metrics(c),
            'workers': worker_status(), 'disk': disk_status(), 'local_media_cache_mb': round(cache_bytes() / 1048576, 1)}


def _analysis_metrics(c, day):
    rows = {r['status']: r['n'] for r in c.execute('SELECT status, COUNT(*) n FROM analysis_versions GROUP BY status').fetchall()}
    failed = c.execute("SELECT COUNT(*) FROM analysis_versions WHERE status='FAILED' AND finished_at>=?", (day,)).fetchone()[0]
    done = c.execute("SELECT COUNT(*) FROM analysis_versions WHERE status IN ('COMPLETED','FAILED') AND finished_at>=?", (day,)).fetchone()[0]
    return {'queued': rows.get('QUEUED', 0), 'running': rows.get('RUNNING', 0), 'finished_24h': done, 'failed_24h': failed}


def _payment_metrics(c, day):
    """Provider events by result. MISMATCH, ALREADY_PAID and UNKNOWN_CHECKOUT need a person (app/payments.py)."""
    res = {r['result']: r['n'] for r in c.execute('SELECT result, COUNT(*) n FROM payment_events WHERE received_at>=? GROUP BY result', (day,)).fetchall()}
    return {'events': res, 'needs_attention': sum(res.get(k, 0) for k in ('MISMATCH', 'ALREADY_PAID', 'UNKNOWN_CHECKOUT')),
            'checkouts_created': c.execute('SELECT COUNT(*) FROM payment_attempts WHERE created_at>=?', (day,)).fetchone()[0]}


def _ledger_metrics(c):
    unbalanced = c.execute("SELECT COUNT(*) FROM (SELECT txn_id FROM ledger_entries GROUP BY txn_id, currency "
                           "HAVING SUM(CASE WHEN direction='DEBIT' THEN amount_minor ELSE -amount_minor END)<>0) t").fetchone()[0]
    return {'unbalanced_transactions': unbalanced, 'balanced': unbalanced == 0}
