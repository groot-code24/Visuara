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


def public_url(): return os.getenv('CC_PUBLIC_URL', 'http://127.0.0.1:8000').rstrip('/')
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


def _redirect(outcome):
    return _apply(RedirectResponse('/app' if outcome[0] == 'session' else '/app#welcome', status_code=303), outcome)


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
        with m.db() as c: c.execute('UPDATE auth_challenges SET consumed_at=CURRENT_TIMESTAMP WHERE token_hash=?', (_h(token),))
        raise _err(502, 'EMAIL_SEND_FAILED', str(e))
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
        return _page('Link expired', '<h1>This sign-in link has expired</h1><p>Links work once and expire after a few minutes. Request a new one.</p><a class="btn" href="/app">Back to sign in</a>', 410)
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
        return _page('Link expired', '<h1>This sign-in link has expired</h1><p>Request a new one.</p><a class="btn" href="/app">Back to sign in</a>', 410)
    return _redirect(outcome)


# ---------------------------------------------------------------- Google (OpenID Connect, authorization code + PKCE)
@router.get('/auth/google/start')
def google_start():
    if not google_configured():
        return _page('Google sign-in unavailable', '<h1>Google sign-in is not configured</h1><p>Use email sign-in instead.</p><a class="btn" href="/app">Back</a>', 503)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    params = {'client_id': os.environ['GOOGLE_CLIENT_ID'], 'redirect_uri': public_url() + '/auth/google/callback', 'response_type': 'code',
              'scope': 'openid email profile', 'state': state, 'nonce': nonce, 'code_challenge': challenge,
              'code_challenge_method': 'S256', 'prompt': 'select_account'}
    resp = RedirectResponse(GOOGLE_AUTH_URL + '?' + urllib.parse.urlencode(params), status_code=303)
    _set(resp, 'cc_oauth', _seal('google-oauth', {'s': state, 'n': nonce, 'v': verifier, 'exp': _now() + 600}), 600, path='/auth/google')
    return resp


def google_exchange(code, verifier):
    """Exchange the authorization code for an ID token and return its claims.

    The ID token comes straight from Google's token endpoint over verified TLS, so per OpenID Connect Core 3.1.3.7 (6)
    the TLS server validation stands in for checking the token signature. The claims are still validated by the caller.
    """
    r = httpx.post(GOOGLE_TOKEN_URL, timeout=15, data={
        'grant_type': 'authorization_code', 'code': code, 'code_verifier': verifier, 'redirect_uri': public_url() + '/auth/google/callback',
        'client_id': os.environ['GOOGLE_CLIENT_ID'], 'client_secret': os.environ['GOOGLE_CLIENT_SECRET']})
    if r.status_code != 200: raise ValueError(f'token endpoint returned {r.status_code}')
    payload = r.json()['id_token'].split('.')[1]
    return json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))


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
    fail = lambda why: _page('Google sign-in failed', f'<h1>Google sign-in did not complete</h1><p>{html.escape(why)}</p><a class="btn" href="/app">Back to sign in</a>', 400)
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
    return c.execute('SELECT i.id,i.role,o.name organization FROM invitations i JOIN organizations o ON o.id=i.org_id WHERE lower(i.email)=? AND i.accepted_at IS NULL AND i.expires_at>? ORDER BY i.id DESC',
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
            org_id, role = inv['org_id'], inv['role']
            c.execute('UPDATE invitations SET accepted_at=CURRENT_TIMESTAMP WHERE id=? AND accepted_at IS NULL', (inv['id'],))
        else:
            role, org_name = body.role, body.organization.strip()
            if role not in ('brand', 'creator'): raise _err(422, 'ROLE_REQUIRED', 'Choose whether you are joining as a brand or a creator')
            if not org_name: raise _err(422, 'ORGANIZATION_REQUIRED', 'Name your workspace (your company, channel or studio)')
            if c.execute('SELECT id FROM organizations WHERE lower(name)=lower(?)', (org_name,)).fetchone():
                raise _err(409, 'ORGANIZATION_EXISTS', 'A workspace with this name exists. Choose another name, or ask a member to invite you.')
            org_id = c.execute('INSERT INTO organizations(name) VALUES(?)', (org_name,)).lastrowid
        uid = c.execute('INSERT INTO users(email,password_hash,name,role,org_id) VALUES(?,?,?,?,?)', (email, '', body.name.strip(), role, org_id)).lastrowid
        if p['provider'] == 'google' and p['subject']:
            c.execute('INSERT INTO user_identities(user_id,provider,subject,email) VALUES(?,?,?,?)', (uid, 'google', p['subject'], email))
        c.execute('DELETE FROM pending_signups WHERE token_hash=?', (p['token_hash'],))
        user = {'id': uid, 'email': email, 'name': body.name.strip(), 'role': role, 'org_id': org_id}
        m.event(c, user, 'user.signed_up', 'user', uid, {'method': p['provider'], 'via_invitation': bool(body.invitation_id)})
        token = _start_session(c, uid)
    resp = JSONResponse({'user': user})
    _set(resp, 'cc_session', token, SESSION_TTL); resp.delete_cookie('cc_signup', path='/')
    return resp
