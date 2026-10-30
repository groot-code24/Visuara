"""Passwordless sign-in for brands and creators: Google (OpenID Connect) and email (one-time code or magic link).

First sign-in with a verified email that has no account starts onboarding: the person chooses brand or creator and
names a workspace, or accepts a pending invitation addressed to that email. Password sign-in remains available.
"""
import base64, hashlib, hmac, html, json, os, re, secrets, time, urllib.parse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import main as m, mailer

router = APIRouter()
SCHEMA = '''
CREATE TABLE IF NOT EXISTS auth_challenges(id INTEGER PRIMARY KEY, email TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL, code_hash TEXT NOT NULL, expires_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, consumed_at TEXT, ip TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_challenges_email ON auth_challenges(email,id);
CREATE TABLE IF NOT EXISTS user_identities(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, provider TEXT NOT NULL, subject TEXT NOT NULL, email TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(provider,subject));
CREATE TABLE IF NOT EXISTS pending_signups(token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL, subject TEXT, expires_at INTEGER NOT NULL);
'''
CODE_TTL = int(os.getenv('CC_SIGNIN_CODE_TTL', '600'))
MAX_CODE_ATTEMPTS = 5
SIGNUP_TTL = 1800
SESSION_TTL = 86400 * 7
EMAIL_RE = re.compile(r'^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$')
GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_ISSUERS = {'https://accounts.google.com', 'accounts.google.com'}
GOOGLE_JWKS_URL = 'https://www.googleapis.com/oauth2/v3/certs'


def public_url(): return os.getenv('CC_PUBLIC_URL', 'http://127.0.0.1:8000').rstrip('/')
def app_url():
    """Where people use the app. Equals public_url() when the API serves the frontend; the Vercel origin otherwise."""
    return (os.getenv('CC_APP_URL') or public_url()).rstrip('/')
def app_link(path):
    """Link into the app: relative when the API serves the frontend itself, absolute when CC_APP_URL names another origin."""
    return os.getenv('CC_APP_URL', '').rstrip('/') + path
def _back(): return html.escape(app_link('/app'))
def google_configured(): return bool(os.getenv('GOOGLE_CLIENT_ID') and os.getenv('GOOGLE_CLIENT_SECRET'))
def _h(s): return hashlib.sha256(s.encode()).hexdigest()
def _now(): return int(time.time())
def _secure(): return os.getenv('CC_SECURE_COOKIES', '0') == '1'
def _ip(request): return request.client.host if request.client else 'unknown'
def _norm_email(e): return (e or '').strip().lower()
def _err(status, code, message): return HTTPException(status, {'code': code, 'message': message})


def _code_hash(email, code):
    return hmac.new(m._signing_key(), f'signin-code:{email}:{code}'.encode(), hashlib.sha256).hexdigest()


def _seal(purpose, payload):
    """Tamper-proof cookie value. The key is derived per purpose, so it cannot be confused with download-link tokens."""
    key = hmac.new(m._signing_key(), purpose.encode(), hashlib.sha256).digest()
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
    return body + '.' + hmac.new(key, body.encode(), hashlib.sha256).hexdigest()


def _open(purpose, token):
    try:
        key = hmac.new(m._signing_key(), purpose.encode(), hashlib.sha256).digest()
        body, sig = token.rsplit('.', 1)
        if not hmac.compare_digest(sig, hmac.new(key, body.encode(), hashlib.sha256).hexdigest()): return None
        data = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        return data if data.get('exp', 0) > _now() else None
    except Exception:
        return None


def _set(resp, name, value, max_age, path='/'):
    resp.set_cookie(name, value, max_age=max_age, httponly=True, samesite='lax', secure=_secure(), path=path)


def _start_session(c, uid):
    token = secrets.token_urlsafe(32)
    c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)', (_h(token), uid, _now() + SESSION_TTL))
    return token


def _resolve(c, email, name, provider, subject):
    """Sign in an existing account, or start onboarding. The email is verified (code/link possession or Google)."""
    user = None
    if provider == 'google':
        ident = c.execute("SELECT user_id FROM user_identities WHERE provider='google' AND subject=?", (subject,)).fetchone()
        if ident: user = c.execute('SELECT * FROM users WHERE id=?', (ident['user_id'],)).fetchone()
    if not user:
        user = c.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if user and provider == 'google':
            c.execute('INSERT INTO user_identities(user_id,provider,subject,email) VALUES(?,?,?,?) ON CONFLICT(provider,subject) DO NOTHING', (user['id'], 'google', subject, email))
            m.event(c, dict(user), 'auth.identity_linked', 'user', user['id'], {'provider': 'google'})
    if user and (user['suspended_at'] or user['removed_at']):
        return 'suspended', None
    if user:
        m.event(c, dict(user), 'auth.signed_in', 'user', user['id'], {'method': provider})
        return 'session', _start_session(c, user['id'])
    token = secrets.token_urlsafe(32)
    c.execute('DELETE FROM pending_signups WHERE expires_at<? OR email=?', (_now(), email))
    c.execute('INSERT INTO pending_signups(token_hash,email,name,provider,subject,expires_at) VALUES(?,?,?,?,?,?)',
              (_h(token), email, (name or '')[:120], provider, subject, _now() + SIGNUP_TTL))
    return 'signup', token


def _apply(resp, outcome):
    kind, token = outcome
    if kind == 'session':
        _set(resp, 'cc_session', token, SESSION_TTL); resp.delete_cookie('cc_signup', path='/')
    else:
        _set(resp, 'cc_signup', token, SIGNUP_TTL)
    return resp


def _suspended_page():
    return _page('Account suspended', f'<h1>This account is suspended</h1><p>Contact your workspace owner to restore access.</p><a class="btn" href="{_back()}">Back</a>', 403)


def _redirect(outcome):
    if outcome[0] == 'suspended': return _suspended_page()
    return _apply(RedirectResponse(app_link('/app' if outcome[0] == 'session' else '/app#welcome'), status_code=303), outcome)


def _anon_event(c, typ, details):
    c.execute('INSERT INTO events(org_id,user_id,event_type,entity_type,entity_id,details) VALUES(0,0,?,?,0,?)', (typ, 'auth', json.dumps(details)))


@router.get('/api/auth/csrf')
def csrf_token(request: Request):
    """CSRF token for frontends on another origin (e.g. Vercel), which cannot read this API's cookies.
    CORS lets only allowlisted origins read the response, so the double-submit check keeps its strength."""
    token = request.cookies.get('cc_csrf') or secrets.token_urlsafe(24)
    resp = JSONResponse({'csrf': token})
    resp.set_cookie('cc_csrf', token, httponly=False, samesite='lax', secure=_secure(), path='/')
    return resp


@router.get('/api/auth/providers')
def providers():
    return {'google': google_configured(), 'email': mailer.enabled(), 'email_backend': mailer.backend() or None, 'password': True}


# ---------------------------------------------------------------- email: one-time code + magic link
class EmailStart(BaseModel):
    email: str = Field(min_length=3, max_length=254)


@router.post('/api/auth/email/start')
def email_start(body: EmailStart, request: Request):
    email = _norm_email(body.email)
    if not EMAIL_RE.match(email): raise _err(422, 'EMAIL_INVALID', 'Enter a valid email address')
    if not mailer.enabled(): raise _err(503, 'EMAIL_SIGNIN_DISABLED', 'Email sign-in is not configured on this server')
    if not m.rate_limit('signin-email:' + email, 5, 3600) or not m.rate_limit('signin-ip:' + _ip(request), 20, 3600):
        raise _err(429, 'RATE_LIMITED', 'Too many sign-in emails requested. Wait a few minutes and try again.')
    token = secrets.token_urlsafe(32); code = f'{secrets.randbelow(10 ** 6):06d}'
    with m.db() as c:
        c.execute('UPDATE auth_challenges SET consumed_at=CURRENT_TIMESTAMP WHERE email=? AND consumed_at IS NULL', (email,))
        c.execute('INSERT INTO auth_challenges(email,token_hash,code_hash,expires_at,ip) VALUES(?,?,?,?,?)',
                  (email, _h(token), _code_hash(email, code), _now() + CODE_TTL, _ip(request)))
    link = f'{public_url()}/auth/email/{token}'
    minutes = CODE_TTL // 60
    text = (f'Your Context Commerce sign-in code is {code}\n\nOr open this link to sign in:\n{link}\n\n'
            f'The code and link expire in {minutes} minutes and work once. If you did not request this, ignore this email.')
    page = (f'<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:24px;color:#191a2c">'
            f'<h2 style="margin:0 0 12px">Sign in to Context Commerce</h2><p>Your one-time code:</p>'
            f'<p style="font-size:30px;letter-spacing:6px;font-weight:bold;margin:8px 0 20px">{code}</p>'
            f'<p><a href="{html.escape(link)}" style="background:#5148e8;color:#fff;padding:12px 20px;border-radius:8px;text-decoration:none;display:inline-block">Sign in</a></p>'
            f'<p style="color:#6b6d80;font-size:13px">The code and link expire in {minutes} minutes and work once. If you did not request this, ignore this email.</p></div>')
    try:
        mailer.send(email, f'Your Context Commerce sign-in code: {code}', text, page, outbox_dir=m.DATA / 'outbox')
    except mailer.MailError as e:
        with m.db() as c:
            c.execute('UPDATE auth_challenges SET consumed_at=CURRENT_TIMESTAMP WHERE token_hash=?', (_h(token),))
            _anon_event(c, 'email.failed', {'reason': str(e)[:120]})
        raise _err(502, 'EMAIL_SEND_FAILED', 'The sign-in email could not be sent. Try again in a few minutes.')
    # Identical response whether or not an account exists (no account enumeration).
    return {'sent': True, 'email': email, 'expires_in_seconds': CODE_TTL}


class EmailVerify(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=6, max_length=12)


@router.post('/api/auth/email/verify')
def email_verify(body: EmailVerify):
    email = _norm_email(body.email); code = re.sub(r'\s', '', body.code)
    outcome = None
    with m.db() as c:
        ch = c.execute('SELECT * FROM auth_challenges WHERE email=? AND consumed_at IS NULL AND expires_at>? ORDER BY id DESC LIMIT 1', (email, _now())).fetchone()
        if ch:
            if hmac.compare_digest(ch['code_hash'], _code_hash(email, code)):
                if c.execute('UPDATE auth_challenges SET consumed_at=CURRENT_TIMESTAMP WHERE id=? AND consumed_at IS NULL AND attempts<?', (ch['id'], MAX_CODE_ATTEMPTS)).rowcount == 1:
                    outcome = _resolve(c, email, '', 'email', None)
            else:
                # Counted in its own committed transaction; the challenge burns after MAX_CODE_ATTEMPTS wrong codes.
                c.execute('UPDATE auth_challenges SET attempts=attempts+1, consumed_at=CASE WHEN attempts+1>=? THEN CURRENT_TIMESTAMP ELSE consumed_at END WHERE id=?', (MAX_CODE_ATTEMPTS, ch['id']))
    if not outcome: raise _err(400, 'CODE_INVALID', 'That code is invalid or has expired. Request a new one.')
    if outcome[0] == 'suspended': raise _err(403, 'ACCOUNT_SUSPENDED', 'This account is suspended. Contact your workspace owner.')
    return _apply(JSONResponse({'status': 'signed_in' if outcome[0] == 'session' else 'signup_required'}), outcome)


def _page(title, body, status=200):
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} · Context Commerce</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f7fb;font-family:Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif;color:#191a2c}}
.card{{background:#fff;border-radius:18px;box-shadow:0 12px 40px rgba(25,26,44,.08);padding:34px;max-width:420px;width:calc(100% - 32px);box-sizing:border-box}}
h1{{font-size:22px;margin:0 0 10px}}p{{color:#5f6178;line-height:1.6}}button,a.btn{{display:inline-block;width:100%;box-sizing:border-box;text-align:center;background:#5148e8;color:#fff;border:0;border-radius:11px;padding:13px 18px;font-size:15px;font-weight:700;cursor:pointer;text-decoration:none;margin-top:12px}}</style>
</head><body><main class="card">{body}</main></body></html>''', status_code=status)


@router.get('/auth/email/{token}')
def email_link(token: str, request: Request):
    """Confirmation page. Email scanners prefetch links, so signing in requires an explicit POST from this page."""
    with m.db() as c:
        ch = c.execute('SELECT email FROM auth_challenges WHERE token_hash=? AND consumed_at IS NULL AND expires_at>? AND attempts<?', (_h(token), _now(), MAX_CODE_ATTEMPTS)).fetchone()
    if not ch:
        return _page('Link expired', f'<h1>This sign-in link has expired</h1><p>Links work once and expire after a few minutes. Request a new one.</p><a class="btn" href="{_back()}">Back to sign in</a>', 410)
    csrf = request.cookies.get('cc_csrf') or secrets.token_urlsafe(24)
    resp = _page('Confirm sign-in', f'<h1>Sign in to Context Commerce</h1><p>Continue as <strong>{html.escape(ch["email"])}</strong>.</p>'
                 f'<form method="post"><input type="hidden" name="csrf" value="{html.escape(csrf)}"><button type="submit">Continue</button></form>')
    resp.set_cookie('cc_csrf', csrf, httponly=False, samesite='lax', secure=_secure(), path='/')
    return resp


@router.post('/auth/email/{token}')
async def email_link_confirm(token: str, request: Request):
    form = await request.form()
    csrf = request.cookies.get('cc_csrf')
    if not csrf or not hmac.compare_digest(csrf, str(form.get('csrf', ''))):
        return _page('Sign-in failed', '<h1>Please try again</h1><p>The request could not be verified. Open the link from your email again.</p>', 403)
    with m.db() as c:
        ch = c.execute('SELECT id,email FROM auth_challenges WHERE token_hash=? AND consumed_at IS NULL AND expires_at>? AND attempts<?', (_h(token), _now(), MAX_CODE_ATTEMPTS)).fetchone()
        outcome = None
        if ch and c.execute('UPDATE auth_challenges SET consumed_at=CURRENT_TIMESTAMP WHERE id=? AND consumed_at IS NULL', (ch['id'],)).rowcount == 1:
            outcome = _resolve(c, ch['email'], '', 'email', None)
    if not outcome:
        return _page('Link expired', f'<h1>This sign-in link has expired</h1><p>Request a new one.</p><a class="btn" href="{_back()}">Back to sign in</a>', 410)
    return _redirect(outcome)


# ---------------------------------------------------------------- Google (OpenID Connect, authorization code + PKCE)
@router.get('/auth/google/start')
def google_start():
    if not google_configured():
        return _page('Google sign-in unavailable', f'<h1>Google sign-in is not configured</h1><p>Use email sign-in instead.</p><a class="btn" href="{_back()}">Back</a>', 503)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    params = {'client_id': os.environ['GOOGLE_CLIENT_ID'], 'redirect_uri': public_url() + '/auth/google/callback', 'response_type': 'code',
              'scope': 'openid email profile', 'state': state, 'nonce': nonce, 'code_challenge': challenge,
              'code_challenge_method': 'S256', 'prompt': 'select_account'}
    resp = RedirectResponse(GOOGLE_AUTH_URL + '?' + urllib.parse.urlencode(params), status_code=303)
    _set(resp, 'cc_oauth', _seal('google-oauth', {'s': state, 'n': nonce, 'v': verifier, 'exp': _now() + 600}), 600, path='/auth/google')
    return resp


_JWKS = {'at': 0.0, 'keys': {}}
_SHA256_DIGEST_INFO = bytes.fromhex('3031300d060960864801650304020105000420')


def _b64d(s): return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def google_jwks(force=False):
    """Google's current signing keys (cached for an hour; refreshed when an unknown key id appears)."""
    if force or time.time() - _JWKS['at'] > 3600 or not _JWKS['keys']:
        r = httpx.get(os.getenv('CC_GOOGLE_JWKS_URL', GOOGLE_JWKS_URL), timeout=10); r.raise_for_status()
        _JWKS.update(at=time.time(), keys={k['kid']: k for k in r.json()['keys'] if k.get('kty') == 'RSA'})
    return _JWKS['keys']


def verify_rs256(jwt, keys=google_jwks):
    """Verify an RS256 JWT signature (RSASSA-PKCS1-v1_5 with SHA-256) against a JWKS and return its claims."""
    head_b64, payload_b64, sig_b64 = jwt.split('.')
    header = json.loads(_b64d(head_b64))
    if header.get('alg') != 'RS256': raise ValueError('unexpected signing algorithm')
    key = keys().get(header.get('kid')) or keys(True).get(header.get('kid'))
    if not key: raise ValueError('unknown signing key')
    n, e = int.from_bytes(_b64d(key['n']), 'big'), int.from_bytes(_b64d(key['e']), 'big')
    k = (n.bit_length() + 7) // 8; sig = _b64d(sig_b64)
    if len(sig) != k or n.bit_length() < 2048: raise ValueError('malformed signature or weak key')
    em = pow(int.from_bytes(sig, 'big'), e, n).to_bytes(k, 'big')
    digest = hashlib.sha256(f'{head_b64}.{payload_b64}'.encode()).digest()
    expected = b'\x00\x01' + b'\xff' * (k - 3 - len(_SHA256_DIGEST_INFO) - 32) + b'\x00' + _SHA256_DIGEST_INFO + digest
    if not hmac.compare_digest(em, expected): raise ValueError('invalid signature')
    return json.loads(_b64d(payload_b64))


def google_exchange(code, verifier):
    """Exchange the authorization code for an ID token, verify its signature against Google's keys, return its claims.

    The token also arrives over verified TLS straight from Google (OpenID Connect Core 3.1.3.7 (6)); the signature
    check is defence in depth. The claims are validated by the caller.
    """
    r = httpx.post(GOOGLE_TOKEN_URL, timeout=15, data={
        'grant_type': 'authorization_code', 'code': code, 'code_verifier': verifier, 'redirect_uri': public_url() + '/auth/google/callback',
        'client_id': os.environ['GOOGLE_CLIENT_ID'], 'client_secret': os.environ['GOOGLE_CLIENT_SECRET']})
    if r.status_code != 200: raise ValueError(f'token endpoint returned {r.status_code}')
    return verify_rs256(r.json()['id_token'])


def validate_google_claims(claims, nonce):
    aud = claims.get('aud'); aud = aud if isinstance(aud, list) else [aud]
    if claims.get('iss') not in GOOGLE_ISSUERS: return 'wrong issuer'
    if os.environ.get('GOOGLE_CLIENT_ID') not in aud: return 'wrong audience'
    if int(claims.get('exp', 0)) < _now() - 60: return 'token expired'
    if not hmac.compare_digest(str(claims.get('nonce', '')), nonce): return 'nonce mismatch'
    if not claims.get('sub'): return 'missing subject'
    if claims.get('email_verified') not in (True, 'true') or not EMAIL_RE.match(_norm_email(claims.get('email'))): return 'email not verified'
    return None


@router.get('/auth/google/callback')
def google_callback(request: Request, code: str = '', state: str = '', error: str = ''):
    def fail(why):
        try:
            with m.db() as c: _anon_event(c, 'auth.google_failed', {'reason': why[:80]})
        except Exception: pass
        return _page('Google sign-in failed', f'<h1>Google sign-in did not complete</h1><p>{html.escape(why)}</p><a class="btn" href="{_back()}">Back to sign in</a>', 400)
    if error: return fail('Sign-in was cancelled or refused at Google.')
    data = _open('google-oauth', request.cookies.get('cc_oauth', ''))
    if not data or not state or not hmac.compare_digest(state, data['s']): return fail('The sign-in session expired or did not match. Please start again.')
    try:
        claims = google_exchange(code, data['v'])
    except Exception:
        return fail('Google could not confirm the sign-in. Please try again.')
    why = validate_google_claims(claims, data['n'])
    if why: return fail(f'Google account could not be verified ({why}).')
    with m.db() as c:
        outcome = _resolve(c, _norm_email(claims['email']), claims.get('name', ''), 'google', str(claims['sub']))
    resp = _redirect(outcome); resp.delete_cookie('cc_oauth', path='/auth/google')
    return resp


# ---------------------------------------------------------------- onboarding (first sign-in)
def _pending(c, request):
    token = request.cookies.get('cc_signup')
    if not token: return None
    return c.execute('SELECT * FROM pending_signups WHERE token_hash=? AND expires_at>?', (_h(token), _now())).fetchone()


def _invitations_for(c, email):
    return c.execute('SELECT i.id,i.role,i.member_role,o.name organization FROM invitations i JOIN organizations o ON o.id=i.org_id WHERE lower(i.email)=? AND i.accepted_at IS NULL AND i.revoked_at IS NULL AND i.expires_at>? ORDER BY i.id DESC',
                     (email, _now())).fetchall()


@router.get('/api/auth/signup')
def signup_state(request: Request):
    with m.db() as c:
        p = _pending(c, request)
        if not p: raise _err(404, 'NO_PENDING_SIGNUP', 'No sign-up in progress')
        return {'email': p['email'], 'name': p['name'], 'provider': p['provider'], 'invitations': [dict(r) for r in _invitations_for(c, p['email'])]}


@router.post('/api/auth/signup/cancel')
def cancel_signup(request: Request):
    token = request.cookies.get('cc_signup')
    if token:
        with m.db() as c: c.execute('DELETE FROM pending_signups WHERE token_hash=?', (_h(token),))
    resp = JSONResponse({'ok': True}); resp.delete_cookie('cc_signup', path='/')
    return resp


class SignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: str = ''
    organization: str = Field(default='', max_length=120)
    invitation_id: int | None = None


@router.post('/api/auth/signup')
def complete_signup(body: SignupIn, request: Request):
    with m.db() as c:
        p = _pending(c, request)
        if not p: raise _err(401, 'SIGNUP_EXPIRED', 'Your sign-up session expired. Sign in again to continue.')
        email = p['email']
        if c.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone(): raise _err(409, 'ACCOUNT_EXISTS', 'An account with this email already exists. Sign in instead.')
        if body.invitation_id:
            inv = next((r for r in _invitations_for(c, email) if r['id'] == body.invitation_id), None)
            if not inv: raise _err(403, 'INVITATION_INVALID', 'That invitation is not valid for this email')
            inv = c.execute('SELECT * FROM invitations WHERE id=?', (inv['id'],)).fetchone()
            org_id, role, member_role = inv['org_id'], inv['role'], inv['member_role']
            if c.execute('UPDATE invitations SET accepted_at=CURRENT_TIMESTAMP WHERE id=? AND accepted_at IS NULL AND revoked_at IS NULL', (inv['id'],)).rowcount != 1:
                raise _err(403, 'INVITATION_INVALID', 'That invitation is no longer valid')
        else:
            role, org_name = body.role, body.organization.strip()
            if role not in ('brand', 'creator'): raise _err(422, 'ROLE_REQUIRED', 'Choose whether you are joining as a brand or a creator')
            if not org_name: raise _err(422, 'ORGANIZATION_REQUIRED', 'Name your workspace (your company, channel or studio)')
            if c.execute('SELECT id FROM organizations WHERE lower(name)=lower(?)', (org_name,)).fetchone():
                raise _err(409, 'ORGANIZATION_EXISTS', 'A workspace with this name exists. Choose another name, or ask a member to invite you.')
            org_id = c.execute('INSERT INTO organizations(name) VALUES(?)', (org_name,)).lastrowid
            member_role = 'owner'
        uid = c.execute('INSERT INTO users(email,password_hash,name,role,org_id,member_role) VALUES(?,?,?,?,?,?)', (email, '', body.name.strip(), role, org_id, member_role)).lastrowid
        if p['provider'] == 'google' and p['subject']:
            c.execute('INSERT INTO user_identities(user_id,provider,subject,email) VALUES(?,?,?,?)', (uid, 'google', p['subject'], email))
        c.execute('DELETE FROM pending_signups WHERE token_hash=?', (p['token_hash'],))
        user = {'id': uid, 'email': email, 'name': body.name.strip(), 'role': role, 'org_id': org_id, 'member_role': member_role,
                'organization': c.execute('SELECT name FROM organizations WHERE id=?', (org_id,)).fetchone()['name']}
        m.event(c, user, 'user.signed_up', 'user', uid, {'method': p['provider'], 'via_invitation': bool(body.invitation_id)})
        token = _start_session(c, uid)
    resp = JSONResponse({'user': user})
    _set(resp, 'cc_session', token, SESSION_TTL); resp.delete_cookie('cc_signup', path='/')
    return resp
