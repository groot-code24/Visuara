"""Load KEY=VALUE lines from a .env file into os.environ. Real environment variables always win.

The file is the repository-root .env, or CC_ENV_FILE. Set CC_DOTENV=0 to disable (the test suite does, so tests
never touch the database or services configured for a real deployment). Values may be quoted; in unquoted values
a ' #' starts a comment.
"""
import os
from pathlib import Path


def load_dotenv(path=None):
    if os.getenv('CC_DOTENV', '1') == '0': return []
    p = Path(path or os.getenv('CC_ENV_FILE') or Path(__file__).resolve().parent.parent / '.env')
    if not p.is_file(): return []
    loaded = []
    for line in p.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        if line.startswith('export '): line = line[7:]
        key, value = (x.strip() for x in line.split('=', 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'': value = value[1:-1]
        elif ' #' in value: value = value.split(' #', 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value; loaded.append(key)
    return loaded


def apply_platform_defaults():
    """On Vercel the public address is known, so it does not have to be configured twice. CC_PUBLIC_URL still wins (custom domains)."""
    if os.getenv('CC_WORKER_MODE') != 'http': return
    host = os.getenv('VERCEL_PROJECT_PRODUCTION_URL', '').strip()
    if host and not os.getenv('CC_PUBLIC_URL'): os.environ['CC_PUBLIC_URL'] = f'https://{host}'
    # Work has to finish, and report, inside one request: stop short of the function's time limit (CC_FUNCTION_SECONDS).
    budget = str(max(30, int(os.getenv('CC_FUNCTION_SECONDS', '300')) - 30))
    for key in ('CC_JOB_MAX_SECONDS', 'CC_RUNNER_BUDGET_SECONDS', 'CC_QUALITY_WAIT_SECONDS'): os.environ.setdefault(key, budget)


def production_problems(env=None):
    """Settings that are acceptable in development but unsafe in production (CC_ENV=production).

    Development stand-ins (SQLite, local disk, the email file outbox, a generated signing key, plain-HTTP cookies)
    must never serve real customers by accident, so the app refuses to start in production while any is active.
    """
    env = os.environ if env is None else env
    get = lambda k, default='': (env.get(k) or default).strip()
    problems = []
    if not (get('DATABASE_URL') or get('CC_DATABASE_URL')): problems.append('DATABASE_URL is not set (SQLite is development-only)')
    if not get('CC_S3_BUCKET') and not get('BLOB_READ_WRITE_TOKEN'): problems.append('No media storage: set BLOB_READ_WRITE_TOKEN (Vercel Blob) or CC_S3_BUCKET (local-disk media storage is single-host development only)')
    if get('CC_MAIL_BACKEND').lower() == 'outbox': problems.append('CC_MAIL_BACKEND=outbox writes sign-in emails to disk (development only)')
    if len(get('CC_SIGNING_KEY')) < 32: problems.append('CC_SIGNING_KEY must be set to at least 32 random characters, identical on every host (it also protects the job runner)')
    if get('CC_SECURE_COOKIES') != '1': problems.append('CC_SECURE_COOKIES must be 1 (HTTPS-only cookies)')
    if not get('CC_PUBLIC_URL').startswith('https://'): problems.append('CC_PUBLIC_URL must be the public https:// address people use (the web app origin that proxies /api and /auth/google)')
    mail = get('CC_MAIL_BACKEND').lower() or ('smtp' if get('CC_SMTP_HOST') else '')
    if not (mail == 'smtp' and get('CC_SMTP_HOST')) and not (get('GOOGLE_CLIENT_ID') and get('GOOGLE_CLIENT_SECRET')):
        problems.append('Nobody could sign in: configure SMTP (CC_MAIL_BACKEND=smtp, CC_SMTP_HOST) or Google (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)')
    if any(get(k) for k in ('CC_GOOGLE_AUTH_URL', 'CC_GOOGLE_TOKEN_URL', 'CC_GOOGLE_JWKS_URL')): problems.append('CC_GOOGLE_*_URL replaces Google with another sign-in server (test setups only)')
    if get('CC_APP_URL') and not get('CC_APP_URL').startswith('https://'): problems.append('CC_APP_URL must use https://')
    if any(o.strip().startswith('http://') for o in get('CC_CORS_ORIGINS').split(',') if o.strip()): problems.append('CC_CORS_ORIGINS must list https:// origins only')
    if get('CC_OPS_TOKEN') and len(get('CC_OPS_TOKEN')) < 32: problems.append('CC_OPS_TOKEN must be at least 32 characters')
    if get('CC_INLINE_WORKER', '1') == '1': problems.append('CC_INLINE_WORKER must be 0 in production (run worker.py as its own process)')
    return problems


def check_production():
    if os.getenv('CC_ENV', '').lower() != 'production': return
    problems = production_problems()
    if problems:
        raise RuntimeError('Refusing to start with CC_ENV=production:\n  - ' + '\n  - '.join(problems))
