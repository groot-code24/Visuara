"""Passwordless sign-in (magic link / email code, Google OIDC), onboarding, and the SQLite/PostgreSQL database adapter."""
import os, time
import urllib.parse

import pytest

from app import main, auth, db as dbm
from helpers import TestDB, make_user, login, last_mail, email_signin


def test_engine_matches_configuration(client):
    h = client.get('/api/health').json()
    assert h['database']['reachable'] is True
    assert h['database']['engine'] == ('postgres' if os.getenv('CC_TEST_DATABASE_URL') else 'sqlite')
    assert h['decision_engine'].startswith('decision-')
    with main.db() as c:
        tables = set(c.tables())
        assert {'users', 'auth_challenges', 'user_identities', 'pending_signups', 'decisions'} <= tables
        assert not {'offers', 'campaigns', 'tracked_links', 'payment_records'} & tables


def test_providers_reflect_configuration(client, monkeypatch):
    monkeypatch.delenv('CC_MAIL_BACKEND', raising=False); monkeypatch.delenv('CC_SMTP_HOST', raising=False)
    monkeypatch.delenv('GOOGLE_CLIENT_ID', raising=False)
    assert client.get('/api/auth/providers').json() == {'google': False, 'email': False, 'email_backend': None, 'development': True}
    assert client.post('/api/auth/email/start', json={'email': 'a@b.co'}).json()['detail']['code'] == 'EMAIL_SIGNIN_DISABLED'
    r = client.get('/auth/google/start', follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/login?error=google_unavailable'
    # There are no password endpoints any more.
    assert client.post('/api/auth/login', json={'email': 'a@b.co', 'password': 'x'}).status_code in (404, 405)
    assert client.post('/api/auth/register', json={}).status_code in (404, 405)


def test_email_code_signup_onboarding_and_return_signin(mail):
    r = mail.post('/api/auth/email/start', json={'email': ' New.Person@Acme-Foods.com '})
    body = r.json(); body.pop('dev')                                               # the outbox backend hands development the link; see below
    assert r.status_code == 200 and body == {'sent': True, 'email': 'new.person@acme-foods.com', 'expires_in_seconds': auth.CODE_TTL}
    msg, code, token = last_mail()
    assert msg['to'] == 'new.person@acme-foods.com' and msg['subject'] == 'Sign in to Context Commerce'
    assert f'/verify#token={token}' in msg['text']                      # token in the fragment, never sent to servers
    assert mail.post('/api/auth/email/verify', json={'email': 'new.person@acme-foods.com', 'code': code}).json() == {'status': 'signup_required'}
    assert mail.get('/api/me').status_code == 401                       # no session before onboarding
    s = mail.get('/api/auth/signup').json()
    assert s['email'] == 'new.person@acme-foods.com' and s['provider'] == 'email' and s['invitations'] == []
    assert s['suggested_workspace'] == 'Acme-Foods'
    assert mail.post('/api/auth/signup', json={'name': 'Nia', 'organization': ' '}).json()['detail']['code'] == 'ORGANIZATION_REQUIRED'
    u = mail.post('/api/auth/signup', json={'name': 'Nia', 'organization': 'Acme Foods'}).json()['user']
    assert u['member_role'] == 'owner' and u['organization'] == 'Acme Foods'
    me = mail.get('/api/me').json()
    assert me['email'] == 'new.person@acme-foods.com' and me['plan'] == 'team' and 'role' not in me
    assert mail.get('/api/auth/signup').status_code == 404              # pending sign-up consumed
    mail.post('/api/auth/logout')
    assert mail.get('/api/me').status_code == 401
    assert email_signin(mail, 'new.person@acme-foods.com') == 'signed_in'
    assert mail.get('/api/me').json()['org_id'] == u['org_id']
    _, code2, _ = last_mail()
    assert mail.post('/api/auth/email/verify', json={'email': 'new.person@acme-foods.com', 'code': code2}).json()['detail']['code'] == 'CODE_INVALID'


def test_magic_link_needs_an_explicit_click_and_works_once(mail):
    make_user('brand@example.com')
    mail.post('/api/auth/email/start', json={'email': 'brand@example.com'})
    _, _, token = last_mail()
    # The verify page first asks which account the link is for: this does not sign in (mail scanners prefetch links).
    chk = mail.post('/api/auth/email/link/check', json={'token': token})
    assert chk.json() == {'email': 'brand@example.com'} and mail.get('/api/me').status_code == 401
    r = mail.post('/api/auth/email/link', json={'token': token})
    assert r.status_code == 200 and r.json() == {'status': 'signed_in'} and 'HttpOnly' in r.headers['set-cookie']
    assert mail.get('/api/me').json()['email'] == 'brand@example.com'
    mail.cookies.clear()
    assert mail.post('/api/auth/email/link', json={'token': token}).json()['detail']['code'] == 'LINK_EXPIRED'
    assert mail.post('/api/auth/email/link/check', json={'token': token}).status_code == 410
    assert mail.post('/api/auth/email/link', json={'token': 'x' * 43}).status_code == 410
    # A new user arriving by magic link continues to onboarding.
    mail.post('/api/auth/email/start', json={'email': 'fresh@example.com'}); _, _, t2 = last_mail()
    assert mail.post('/api/auth/email/link', json={'token': t2}).json() == {'status': 'signup_required'}
    assert mail.get('/api/auth/signup').json()['email'] == 'fresh@example.com'


def test_wrong_codes_burn_the_challenge_and_responses_do_not_enumerate(mail):
    make_user('known@example.com')
    known = mail.post('/api/auth/email/start', json={'email': 'known@example.com'}).json()
    unknown = mail.post('/api/auth/email/start', json={'email': 'unknown@example.com'}).json()
    assert set(known) == set(unknown) and known['sent'] is unknown['sent'] is True
    mail.post('/api/auth/email/start', json={'email': 'target@example.com'})
    _, code, token = last_mail()
    wrong = '000000' if code != '000000' else '111111'
    for _ in range(auth.MAX_CODE_ATTEMPTS):
        assert mail.post('/api/auth/email/verify', json={'email': 'target@example.com', 'code': wrong}).status_code == 400
    assert mail.post('/api/auth/email/verify', json={'email': 'target@example.com', 'code': code}).status_code == 400
    assert mail.post('/api/auth/email/link', json={'token': token}).status_code == 410   # the link burns with the code
    row = TestDB().execute("SELECT attempts,consumed_at FROM auth_challenges WHERE email='target@example.com'").fetchone()
    assert row['attempts'] == auth.MAX_CODE_ATTEMPTS and row['consumed_at']


def test_expired_code_and_new_code_supersedes_old(mail):
    mail.post('/api/auth/email/start', json={'email': 'x@example.com'}); _, first, _ = last_mail()
    mail.post('/api/auth/email/start', json={'email': 'x@example.com'}); _, second, _ = last_mail()
    if first != second:
        assert mail.post('/api/auth/email/verify', json={'email': 'x@example.com', 'code': first}).status_code == 400
    TestDB().execute("UPDATE auth_challenges SET expires_at=? WHERE email='x@example.com'", (int(time.time()) - 1,))
    assert mail.post('/api/auth/email/verify', json={'email': 'x@example.com', 'code': second}).status_code == 400


def test_email_rate_limit_per_address(mail):
    codes = [mail.post('/api/auth/email/start', json={'email': 'spam@example.com'}).status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


def test_suspended_accounts_cannot_sign_in(mail):
    u = make_user('sus@example.com')
    TestDB().execute('UPDATE users SET suspended_at=CURRENT_TIMESTAMP WHERE id=?', (u['id'],))
    mail.post('/api/auth/email/start', json={'email': 'sus@example.com'}); _, code, token = last_mail()
    assert mail.post('/api/auth/email/verify', json={'email': 'sus@example.com', 'code': code}).json()['detail']['code'] == 'ACCOUNT_SUSPENDED'
    assert mail.get('/api/me').status_code == 401


@pytest.fixture()
def google(client, monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'cid.apps.googleusercontent.com'); monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'secret')
    claims = {}
    monkeypatch.setattr(auth, 'google_exchange', lambda code, verifier: dict(claims, _verifier=verifier))

    def sign_in(**override):
        r = client.get('/auth/google/start', follow_redirects=False)
        assert r.status_code == 303
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.headers['location']).query))
        assert r.headers['location'].startswith(auth.GOOGLE_AUTH_URL) and q['code_challenge_method'] == 'S256' and q['scope'] == 'openid email profile'
        claims.clear()
        claims.update({'iss': 'https://accounts.google.com', 'aud': 'cid.apps.googleusercontent.com', 'exp': int(time.time()) + 300,
                       'nonce': q['nonce'], 'sub': '1001', 'email': 'gina@example.com', 'email_verified': True, 'name': 'Gina G'})
        claims.update(override)
        return client.get('/auth/google/callback', params={'code': 'abc', 'state': override.pop('_state', q['state'])}, follow_redirects=False)
    return client, sign_in


def test_google_rejects_bad_state_and_bad_claims(google):
    c, sign_in = google
    r = sign_in(_state='forged')
    assert r.status_code == 303 and r.headers['location'] == '/login?error=google_failed'
    for bad in ({'aud': 'someone-else'}, {'email_verified': False}, {'nonce': 'replayed'}, {'iss': 'https://evil.example'}, {'exp': 1}):
        r = sign_in(**bad)
        assert r.headers['location'] == '/login?error=google_failed' and c.get('/api/me').status_code == 401, bad
    assert c.get('/auth/google/callback', params={'error': 'access_denied'}, follow_redirects=False).headers['location'] == '/login?error=google_cancelled'


def test_google_signup_links_identity_and_joins_invitation(google):
    c, sign_in = google
    make_user('owner@example.com', org='Acme'); login(c, 'owner@example.com')
    inv = c.post('/api/invitations', json={'email': 'gina@example.com', 'member_role': 'viewer'}).json()
    assert 'invitation_token' not in inv                                # tokens never leave the server
    c.post('/api/auth/logout'); c.cookies.clear()
    r = sign_in()
    assert r.status_code == 303 and r.headers['location'] == '/welcome'
    s = c.get('/api/auth/signup').json()
    assert s['name'] == 'Gina G' and s['provider'] == 'google' and s['invitations'][0]['organization'] == 'Acme'
    u = c.post('/api/auth/signup', json={'name': 'Gina', 'invitation_id': s['invitations'][0]['id']}).json()['user']
    assert u['member_role'] == 'viewer' and u['org_id'] == c.get('/api/me').json()['org_id']
    assert TestDB().execute("SELECT accepted_at FROM invitations WHERE email='gina@example.com'").fetchone()['accepted_at']
    c.post('/api/auth/logout'); c.cookies.clear()
    r = sign_in(email='gina.new@example.com')                            # matched by Google subject even if the email changed
    assert r.status_code == 303 and r.headers['location'] == '/dashboard'
    assert c.get('/api/me').json()['id'] == u['id']


def test_google_links_existing_email_account(google):
    c, sign_in = google
    existing = make_user('gina@example.com', org='Gina Co')
    assert sign_in().headers['location'] == '/dashboard'
    assert c.get('/api/me').json()['id'] == existing['id']
    assert TestDB().execute("SELECT user_id FROM user_identities WHERE provider='google' AND subject='1001'").fetchone()['user_id'] == existing['id']


def test_app_redirects_are_absolute_when_the_app_has_its_own_origin(google, monkeypatch):
    c, sign_in = google
    monkeypatch.setenv('CC_APP_URL', 'https://app.example.com')
    assert sign_in().headers['location'] == 'https://app.example.com/welcome'


def test_signup_workspace_name_conflict_and_cancel(mail):
    make_user('b@example.com', org='Taken Studio')
    assert email_signin(mail, 'n@gmail.com') == 'signup_required'
    assert mail.get('/api/auth/signup').json()['suggested_workspace'] == "N's workspace"
    r = mail.post('/api/auth/signup', json={'name': 'N', 'organization': 'taken studio'})
    assert r.status_code == 409 and r.json()['detail']['code'] == 'ORGANIZATION_EXISTS'
    mail.post('/api/auth/signup/cancel')
    assert mail.get('/api/auth/signup').status_code == 404


def test_secure_cookie_flag(mail, monkeypatch):
    monkeypatch.setenv('CC_SECURE_COOKIES', '1')
    make_user('s@example.com')
    mail.post('/api/auth/email/start', json={'email': 's@example.com'}); _, code, _ = last_mail()
    r = mail.post('/api/auth/email/verify', json={'email': 's@example.com', 'code': code})
    assert 'Secure' in r.headers['set-cookie'] and 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=lax' in r.headers['set-cookie']


def test_api_root_is_not_a_web_page(client):
    assert client.get('/').json()['service'] == 'context-commerce-api'
    assert client.get('/app').status_code == 404


# ---------------------------------------------------------------- adapter unit tests (no database needed)
def test_sql_translation_for_postgres():
    tabs = frozenset({'users'})
    assert dbm.translate("SELECT * FROM users WHERE email=? AND name LIKE '%?%'", tabs) == "SELECT * FROM users WHERE email=%s AND name LIKE '%%?%%'"
    assert dbm.translate('INSERT INTO users(email) VALUES(?)', tabs).endswith(' RETURNING id')
    assert not dbm.translate('INSERT INTO sessions(token_hash) VALUES(?)', tabs).endswith('RETURNING id')
    assert "to_char(timezone('UTC', now())" in dbm.translate('UPDATE jobs SET finished_at=CURRENT_TIMESTAMP', tabs)
    ddl = dbm.translate_ddl("CREATE TABLE IF NOT EXISTS t(id INTEGER PRIMARY KEY, n INTEGER NOT NULL, x REAL, note TEXT DEFAULT 'INTEGER')")
    assert 'id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY' in ddl and 'x DOUBLE PRECISION' in ddl and "'INTEGER'" in ddl
    r = dbm.Row(['a', 'b'], [1, 2])
    assert (r[0], r['b'], dict(r), r.keys()) == (1, 2, {'a': 1, 'b': 2}, ['a', 'b'])
    assert dbm.describe('postgresql://u:p@ep-cool-1.eu-central-1.aws.neon.tech/db?sslmode=require') == {'engine': 'postgres', 'host': 'ep-cool-1.eu-central-1.aws.neon.tech', 'neon': True}


def test_development_sign_in_works_without_an_email_provider(client, mail, monkeypatch):
    # The outbox reaches nobody, so development hands the link and code to the sign-in page. Production never does.
    r = client.post('/api/auth/email/start', json={'email': 'new@acme.io'}).json()
    assert r['dev']['link'].startswith('/verify#token=') and len(r['dev']['code']) == 6
    token = r['dev']['link'].split('token=')[1]
    assert client.post('/api/auth/email/link/check', json={'token': token}).json()['email'] == 'new@acme.io'
    monkeypatch.setenv('CC_ENV', 'production')
    assert 'dev' not in client.post('/api/auth/email/start', json={'email': 'other@acme.io'}).json()
    monkeypatch.setenv('CC_ENV', 'development'); monkeypatch.setenv('CC_MAIL_BACKEND', 'smtp'); monkeypatch.setenv('CC_SMTP_HOST', '127.0.0.1'); monkeypatch.setenv('CC_SMTP_PORT', '1')
    assert client.post('/api/auth/email/start', json={'email': 'third@acme.io'}).status_code == 502      # no provider listening, and no link handed out


def test_loopback_origins_are_allowed_only_in_development(client, monkeypatch):
    # People open the local app on localhost or 127.0.0.1; a sign-in must not fail as "cross-origin" there.
    client.get('/api/auth/csrf')
    for origin in ('http://127.0.0.1:3000', 'http://localhost:3000'):
        assert client.post('/api/auth/email/start', json={'email': 'x'}, headers={'Origin': origin}).status_code == 422       # reached the handler
    assert client.post('/api/auth/email/start', json={'email': 'x'}, headers={'Origin': 'http://evil.example'}).status_code == 403
    monkeypatch.setenv('CC_ENV', 'production')
    assert client.post('/api/auth/email/start', json={'email': 'x'}, headers={'Origin': 'http://127.0.0.1:3000'}).status_code == 403


def test_magic_links_point_at_the_web_app_by_default(client, mail, monkeypatch):
    monkeypatch.delenv('CC_PUBLIC_URL', raising=False); monkeypatch.delenv('CC_APP_URL', raising=False)
    client.post('/api/auth/email/start', json={'email': 'new@acme.io'})
    assert 'http://localhost:3000/verify#token=' in last_mail()[0]['text']
