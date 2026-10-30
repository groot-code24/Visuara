"""Passwordless sign-in (email code / magic link, Google OIDC), onboarding, and the SQLite/PostgreSQL database adapter."""
import json, os, re, time
import urllib.parse

import pytest

from app import main, auth, db as dbm
from test_api import CSRSTestClient, TestDB, register, client, isolated_db  # noqa: F401  (shared fixtures)


@pytest.fixture()
def mail(client, monkeypatch):
    monkeypatch.setenv('CC_MAIL_BACKEND', 'outbox')
    return client


def last_mail():
    d = json.loads((main.DATA / 'outbox' / 'latest.json').read_text())
    code = re.search(r'sign-in code is (\d{6})', d['text']).group(1)
    token = re.search(r'/auth/email/([A-Za-z0-9_-]+)', d['text']).group(1)
    return d, code, token


def test_engine_matches_configuration(client):
    h = client.get('/api/health').json()
    assert h['database']['reachable'] is True
    assert h['database']['engine'] == ('postgres' if os.getenv('CC_TEST_DATABASE_URL') else 'sqlite')
    with main.db() as c:
        assert c.kind == h['database']['engine'].replace('postgres', 'postgres')
        assert {'users', 'auth_challenges', 'user_identities', 'pending_signups', 'offers'} <= set(c.tables())


def test_providers_reflect_configuration(client, monkeypatch):
    monkeypatch.delenv('CC_MAIL_BACKEND', raising=False); monkeypatch.delenv('CC_SMTP_HOST', raising=False)
    monkeypatch.delenv('GOOGLE_CLIENT_ID', raising=False)
    assert client.get('/api/auth/providers').json() == {'google': False, 'email': False, 'email_backend': None, 'password': True}
    assert client.post('/api/auth/email/start', json={'email': 'a@b.co'}).json()['detail']['code'] == 'EMAIL_SIGNIN_DISABLED'
    assert client.get('/auth/google/start', follow_redirects=False).status_code == 503


def test_email_code_signup_onboarding_and_return_signin(mail):
    r = mail.post('/api/auth/email/start', json={'email': ' New.Creator@Example.com '})
    assert r.status_code == 200 and r.json() == {'sent': True, 'email': 'new.creator@example.com', 'expires_in_seconds': auth.CODE_TTL}
    msg, code, _ = last_mail()
    assert msg['to'] == 'new.creator@example.com' and code in msg['subject']
    assert mail.post('/api/auth/email/verify', json={'email': 'new.creator@example.com', 'code': code}).json() == {'status': 'signup_required'}
    assert mail.get('/api/me').status_code == 401                       # no session before onboarding
    s = mail.get('/api/auth/signup').json()
    assert s['email'] == 'new.creator@example.com' and s['provider'] == 'email' and s['invitations'] == []
    assert mail.post('/api/auth/signup', json={'name': 'Nia', 'organization': 'Nia Films'}).json()['detail']['code'] == 'ROLE_REQUIRED'
    u = mail.post('/api/auth/signup', json={'name': 'Nia', 'role': 'creator', 'organization': 'Nia Films'}).json()['user']
    assert u['role'] == 'creator' and mail.get('/api/me').json()['email'] == 'new.creator@example.com'
    assert mail.get('/api/auth/signup').status_code == 404              # pending sign-up consumed
    # Password sign-in is impossible for a passwordless account.
    assert mail.post('/api/auth/login', json={'email': 'new.creator@example.com', 'password': ''}).status_code in (401, 422)
    mail.post('/api/auth/logout')
    mail.post('/api/auth/email/start', json={'email': 'new.creator@example.com'})
    _, code2, _ = last_mail()
    assert mail.post('/api/auth/email/verify', json={'email': 'new.creator@example.com', 'code': code2}).json() == {'status': 'signed_in'}
    assert mail.get('/api/me').json()['org_id'] == u['org_id']
    # Codes are single use.
    assert mail.post('/api/auth/email/verify', json={'email': 'new.creator@example.com', 'code': code2}).json()['detail']['code'] == 'CODE_INVALID'


def test_magic_link_requires_explicit_confirmation_and_csrf(mail):
    register(mail, 'Brand', 'brand@example.com', 'brand', 'Acme')
    mail.post('/api/auth/logout'); mail.cookies.clear()
    mail.post('/api/auth/email/start', json={'email': 'brand@example.com'})
    _, _, token = last_mail()
    page = mail.get(f'/auth/email/{token}')                             # a mail scanner's GET does not sign in
    assert page.status_code == 200 and 'brand@example.com' in page.text and mail.get('/api/me').status_code == 401
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    assert mail.post(f'/auth/email/{token}', data={'csrf': 'forged'}, follow_redirects=False).status_code == 403
    r = mail.post(f'/auth/email/{token}', data={'csrf': csrf}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/app'
    assert mail.get('/api/me').json()['email'] == 'brand@example.com'
    assert mail.get(f'/auth/email/{token}').status_code == 410          # links are single use


def test_wrong_codes_burn_the_challenge_and_responses_do_not_enumerate(mail):
    register(mail, 'Brand', 'known@example.com', 'brand', 'Known'); mail.post('/api/auth/logout')
    known = mail.post('/api/auth/email/start', json={'email': 'known@example.com'}).json()
    unknown = mail.post('/api/auth/email/start', json={'email': 'unknown@example.com'}).json()
    assert set(known) == set(unknown) and known['sent'] is unknown['sent'] is True
    mail.post('/api/auth/email/start', json={'email': 'target@example.com'})
    _, code, token = last_mail()
    wrong = '000000' if code != '000000' else '111111'
    for _ in range(auth.MAX_CODE_ATTEMPTS):
        assert mail.post('/api/auth/email/verify', json={'email': 'target@example.com', 'code': wrong}).status_code == 400
    assert mail.post('/api/auth/email/verify', json={'email': 'target@example.com', 'code': code}).status_code == 400
    assert mail.get(f'/auth/email/{token}').status_code == 410
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
    assert sign_in(_state='forged').status_code == 400
    for bad in ({'aud': 'someone-else'}, {'email_verified': False}, {'nonce': 'replayed'}, {'iss': 'https://evil.example'}, {'exp': 1}):
        r = sign_in(**bad)
        assert r.status_code == 400 and c.get('/api/me').status_code == 401, bad
    assert c.get('/auth/google/callback', params={'error': 'access_denied'}).status_code == 400


def test_google_signup_links_identity_and_joins_invitation(google):
    c, sign_in = google
    register(c, 'Brand', 'brand@example.com', 'brand', 'Acme')
    inv = c.post('/api/invitations', json={'email': 'gina@example.com', 'role': 'creator'}).json()
    c.post('/api/auth/logout'); c.cookies.clear()
    r = sign_in()
    assert r.status_code == 303 and r.headers['location'] == '/app#welcome'
    s = c.get('/api/auth/signup').json()
    assert s['name'] == 'Gina G' and s['provider'] == 'google' and s['invitations'][0]['organization'] == 'Acme'
    u = c.post('/api/auth/signup', json={'name': 'Gina', 'invitation_id': s['invitations'][0]['id']}).json()['user']
    assert u['role'] == 'creator' and u['org_id'] == c.get('/api/me').json()['org_id']
    assert TestDB().execute("SELECT accepted_at FROM invitations WHERE email='gina@example.com'").fetchone()['accepted_at']
    c.post('/api/auth/logout'); c.cookies.clear()
    # Returning user: matched by Google subject even if the Google email changed.
    r = sign_in(email='gina.new@example.com')
    assert r.status_code == 303 and r.headers['location'] == '/app'
    assert c.get('/api/me').json()['id'] == u['id']
    assert inv['email'] == 'gina@example.com'


def test_google_links_existing_password_account_by_verified_email(google):
    c, sign_in = google
    existing = register(c, 'Gina', 'gina@example.com', 'brand', 'Gina Co')
    c.post('/api/auth/logout'); c.cookies.clear()
    assert sign_in().headers['location'] == '/app'
    assert c.get('/api/me').json()['id'] == existing['id']
    assert TestDB().execute("SELECT user_id FROM user_identities WHERE provider='google' AND subject='1001'").fetchone()['user_id'] == existing['id']


def test_signup_workspace_name_conflict_and_cancel(mail):
    register(mail, 'Brand', 'b@example.com', 'brand', 'Taken Studio'); mail.post('/api/auth/logout'); mail.cookies.clear()
    mail.post('/api/auth/email/start', json={'email': 'n@example.com'}); _, code, _ = last_mail()
    mail.post('/api/auth/email/verify', json={'email': 'n@example.com', 'code': code})
    r = mail.post('/api/auth/signup', json={'name': 'N', 'role': 'brand', 'organization': 'taken studio'})
    assert r.status_code == 409 and r.json()['detail']['code'] == 'ORGANIZATION_EXISTS'
    mail.post('/api/auth/signup/cancel')
    assert mail.get('/api/auth/signup').status_code == 404


def test_landing_and_app_routes(client):
    landing = client.get('/')
    assert landing.status_code == 200 and '/app?join=brand' in landing.text and '/app?join=creator' in landing.text
    assert 'id="googleBtn"' in client.get('/app').text


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
