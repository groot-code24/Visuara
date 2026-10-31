"""Passwordless sign-in: Google (OpenID Connect) and email (magic link or one-time code), as on claude.ai.

First sign-in with a verified email that has no account starts onboarding (/welcome in the web app): the person
gives their name and names a workspace, or accepts a pending invitation addressed to that email. There are no
passwords. The web app (Next.js) proxies /api/* and /auth/google/* to this API, so cookies are first-party.
"""
import base64, hashlib, hmac, html, json, os, re, secrets, time, urllib.parse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
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


def public_url(): return os.getenv('CC_PUBLIC_URL', 'http://localhost:3000').rstrip('/')
def _production(): return os.getenv('CC_ENV', '').lower() == 'production'
def app_url():
    """Where people use the app. Equals public_url() when the API serves the frontend; the Vercel origin otherwise."""
    return (os.getenv('CC_APP_URL') or public_url()).rstrip('/')
def app_link(path):
    """Link into the web app: relative when the app proxies the API on one origin, absolute when CC_APP_URL is set."""
    return os.getenv('CC_APP_URL', '').rstrip('/') + path
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


def _to_login(error):
    return RedirectResponse(app_link('/login?error=' + urllib.parse.quote(error)), status_code=303)


def _redirect(outcome):
    if outcome[0] == 'suspended': return _to_login('suspended')
    return _apply(RedirectResponse(app_link('/dashboard' if outcome[0] == 'session' else '/welcome'), status_code=303), outcome)


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
    return {'google': google_configured(), 'email': mailer.enabled(), 'email_backend': mailer.backend() or None, 'development': not _production()}


# ---------------------------------------------------------------- email: one-time code + magic link
class EmailStart(BaseModel):
    email: str = Field(min_length=3, max_length=254)


@router.post('/api/auth/email/start')
def email_start(body: EmailStart, request: Request):
    email = _norm_email(body.email)
    if not EMAIL_RE.match(email): raise _err(422, 'EMAIL_INVALID', 'Enter a valid email address')
    if not mailer.enabled(): raise _err(503, 'EMAIL_SIGNIN_DISABLED', 'Email sign-in is not configured on this server')
    if not m.rate_limit('signin-email:' + email, 5, 3600) or not m.rate_limit('signin-ip:' + _ip(request), int(os.getenv('CC_SIGNIN_EMAILS_PER_IP_HOUR', '20')), 3600):
        raise _err(429, 'RATE_LIMITED', 'Too many sign-in emails requested. Wait a few minutes and try again.')
    token = secrets.token_urlsafe(32); code = f'{secrets.randbelow(10 ** 6):06d}'
    with m.db() as c:
        c.execute('UPDATE auth_challenges SET consumed_at=CURRENT_TIMESTAMP WHERE email=? AND consumed_at IS NULL', (email,))
        c.execute('INSERT INTO auth_challenges(email,token_hash,code_hash,expires_at,ip) VALUES(?,?,?,?,?)',
                  (email, _h(token), _code_hash(email, code), _now() + CODE_TTL, _ip(request)))
    # The token travels in the URL fragment, which browsers never send to servers (no tokens in proxy or CDN logs).
    link = f'{app_url()}/verify#token={token}'
    minutes = CODE_TTL // 60
    text = (f'Sign in to Context Commerce\n\nOpen this link to sign in:\n{link}\n\nOr enter this code: {code}\n\n'
            f'The link and code expire in {minutes} minutes and work once. If you did not request this, ignore this email.')
    page = (f'<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:460px;margin:auto;padding:32px 24px;color:#1f1e1d">'
            f'<p style="font-size:15px;font-weight:600;margin:0 0 24px">Context Commerce</p>'
            f'<h2 style="font-size:22px;font-weight:600;margin:0 0 16px">Sign in to Context Commerce</h2>'
            f'<p style="margin:0 0 24px"><a href="{html.escape(link)}" style="background:#1f1e1d;color:#fff;padding:12px 22px;border-radius:10px;text-decoration:none;display:inline-block;font-weight:600">Sign in</a></p>'
            f'<p style="color:#6b6a68;font-size:14px;margin:0 0 6px">Or enter this code:</p>'
            f'<p style="font-size:28px;letter-spacing:6px;font-weight:600;margin:0 0 24px">{code}</p>'
            f'<p style="color:#8a8986;font-size:13px">The link and code expire in {minutes} minutes and work once. If you did not request this, ignore this email.</p></div>')
    try:
        mailer.send(email, 'Sign in to Context Commerce', text, page, outbox_dir=m.DATA / 'outbox')
    except mailer.MailError as e:
        with m.db() as c:
            c.execute('UPDATE auth_challenges SET consumed_at=CURRENT_TIMESTAMP WHERE token_hash=?', (_h(token),))
            _anon_event(c, 'email.failed', {'reason': str(e)[:120]})
        raise _err(502, 'EMAIL_SEND_FAILED', 'The sign-in email could not be sent. Try again in a few minutes.')
    # Identical response whether or not an account exists (no account enumeration).
    out = {'sent': True, 'email': email, 'expires_in_seconds': CODE_TTL}
    # Without an email provider nothing can reach an inbox, so development hands the link to the sign-in page. The
    # production guard refuses the outbox backend, and this checks production again.
    if mailer.backend() == 'outbox' and not _production(): out['dev'] = {'link': f'/verify#token={token}', 'code': code}
    return out


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


class LinkIn(BaseModel):
    token: str = Field(min_length=20, max_length=100)


def _live_challenge(c, token):
    return c.execute('SELECT id,email FROM auth_challenges WHERE token_hash=? AND consumed_at IS NULL AND expires_at>? AND attempts<?',
                     (_h(token), _now(), MAX_CODE_ATTEMPTS)).fetchone()


@router.post('/api/auth/email/link/check')
def email_link_check(body: LinkIn):
    """Which address a magic link belongs to, without using it. Email scanners prefetch links, so the web app shows
    "Continue as <email>" and signs in only on an explicit click (POST /api/auth/email/link)."""
    with m.db() as c: ch = _live_challenge(c, body.token)
    if not ch: raise _err(410, 'LINK_EXPIRED', 'This sign-in link has expired or was already used. Request a new one.')
    return {'email': ch['email']}


@router.post('/api/auth/email/link')
def email_link_confirm(body: LinkIn):
    outcome = None
    with m.db() as c:
        ch = _live_challenge(c, body.token)
        if ch and c.execute('UPDATE auth_challenges SET consumed_at=CURRENT_TIMESTAMP WHERE id=? AND consumed_at IS NULL', (ch['id'],)).rowcount == 1:
            outcome = _resolve(c, ch['email'], '', 'email', None)
    if not outcome: raise _err(410, 'LINK_EXPIRED', 'This sign-in link has expired or was already used. Request a new one.')
    if outcome[0] == 'suspended': raise _err(403, 'ACCOUNT_SUSPENDED', 'This account is suspended. Contact your workspace owner.')
    return _apply(JSONResponse({'status': 'signed_in' if outcome[0] == 'session' else 'signup_required'}), outcome)


# ---------------------------------------------------------------- Google (OpenID Connect, authorization code + PKCE)
@router.get('/auth/google/start')
def google_start(request: Request):
    if not google_configured(): return _to_login('google_unavailable')
    # The state cookie must be set on the host Google will return to, or the callback cannot match it. In development
    # the same app answers on localhost and 127.0.0.1.
    origin = urllib.parse.urlsplit(public_url())
    seen = request.headers.get('x-forwarded-host')
    if not _production() and seen and seen.lower() != origin.netloc.lower():
        return RedirectResponse(public_url() + '/auth/google/start', status_code=303)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    params = {'client_id': os.environ['GOOGLE_CLIENT_ID'], 'redirect_uri': public_url() + '/auth/google/callback', 'response_type': 'code',
              'scope': 'openid email profile', 'state': state, 'nonce': nonce, 'code_challenge': challenge,
              'code_challenge_method': 'S256', 'prompt': 'select_account'}
    resp = RedirectResponse(os.getenv('CC_GOOGLE_AUTH_URL', GOOGLE_AUTH_URL) + '?' + urllib.parse.urlencode(params), status_code=303)
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
    r = httpx.post(os.getenv('CC_GOOGLE_TOKEN_URL', GOOGLE_TOKEN_URL), timeout=15, data={
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
    def fail(why, code='google_failed'):
        try:
            with m.db() as c: _anon_event(c, 'auth.google_failed', {'reason': why[:80]})
        except Exception: pass
        resp = _to_login(code); resp.delete_cookie('cc_oauth', path='/auth/google')
        return resp
    if error: return fail('Sign-in was cancelled or refused at Google.', 'google_cancelled')
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
    return c.execute('SELECT i.id,i.member_role,o.name organization FROM invitations i JOIN organizations o ON o.id=i.org_id WHERE lower(i.email)=? AND i.accepted_at IS NULL AND i.revoked_at IS NULL AND i.expires_at>? ORDER BY i.id DESC',
                     (email, _now())).fetchall()


@router.get('/api/auth/signup')
def signup_state(request: Request):
    with m.db() as c:
        p = _pending(c, request)
        if not p: raise _err(404, 'NO_PENDING_SIGNUP', 'No sign-up in progress')
        return {'email': p['email'], 'name': p['name'], 'provider': p['provider'], 'invitations': [dict(r) for r in _invitations_for(c, p['email'])],
                'suggested_workspace': _suggest_workspace(p['email'], p['name'])}


_PERSONAL_DOMAINS = {'gmail', 'googlemail', 'outlook', 'hotmail', 'yahoo', 'icloud', 'proton', 'protonmail', 'live', 'aol', 'example'}


def _suggest_workspace(email, name):
    local, _, domain = email.partition('@')
    company = domain.split('.')[0]
    if company and company not in _PERSONAL_DOMAINS: return company.title()
    first = (name or local).split(' ')[0].title()
    return first + "'s workspace"


@router.post('/api/auth/signup/cancel')
def cancel_signup(request: Request):
    token = request.cookies.get('cc_signup')
    if token:
        with m.db() as c: c.execute('DELETE FROM pending_signups WHERE token_hash=?', (_h(token),))
    resp = JSONResponse({'ok': True}); resp.delete_cookie('cc_signup', path='/')
    return resp


class SignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
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
            org_id, member_role = inv['org_id'], inv['member_role']
            if c.execute('UPDATE invitations SET accepted_at=CURRENT_TIMESTAMP WHERE id=? AND accepted_at IS NULL AND revoked_at IS NULL', (inv['id'],)).rowcount != 1:
                raise _err(403, 'INVITATION_INVALID', 'That invitation is no longer valid')
        else:
            org_name = body.organization.strip()
            if not org_name: raise _err(422, 'ORGANIZATION_REQUIRED', 'Name your workspace (your company, brand or studio)')
            if c.execute('SELECT id FROM organizations WHERE lower(name)=lower(?)', (org_name,)).fetchone():
                raise _err(409, 'ORGANIZATION_EXISTS', 'A workspace with this name exists. Choose another name, or ask a member to invite you.')
            org_id = c.execute('INSERT INTO organizations(name,plan) VALUES(?,?)', (org_name, m.limits.default_plan())).lastrowid
            member_role = 'owner'
        # users.role is a legacy column (V6-V10 brand/creator workspaces); every V11 account is 'brand'.
        uid = c.execute('INSERT INTO users(email,password_hash,name,role,org_id,member_role) VALUES(?,?,?,?,?,?)', (email, '', body.name.strip(), 'brand', org_id, member_role)).lastrowid
        if p['provider'] == 'google' and p['subject']:
            c.execute('INSERT INTO user_identities(user_id,provider,subject,email) VALUES(?,?,?,?)', (uid, 'google', p['subject'], email))
        c.execute('DELETE FROM pending_signups WHERE token_hash=?', (p['token_hash'],))
        user = {'id': uid, 'email': email, 'name': body.name.strip(), 'org_id': org_id, 'member_role': member_role,
                'organization': c.execute('SELECT name FROM organizations WHERE id=?', (org_id,)).fetchone()['name']}
        m.event(c, user, 'user.signed_up', 'user', uid, {'method': p['provider'], 'via_invitation': bool(body.invitation_id)})
        token = _start_session(c, uid)
    resp = JSONResponse({'user': user})
    _set(resp, 'cc_session', token, SESSION_TTL); resp.delete_cookie('cc_signup', path='/')
    return resp
