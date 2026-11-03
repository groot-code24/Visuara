"""Checks .env.vercel before you import it into Vercel. Never prints a secret, and changes nothing in any service.

    python qa/check_env.py                       # format, Neon, Google, Gmail/SMTP login, Blob
    python qa/check_env.py --mail you@email.com  # also sends one test email, the way magic links are sent
"""
import argparse, os, re, smtplib, ssl, sys
from urllib.parse import urlparse
from email.message import EmailMessage
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
results = []


def report(name, ok, detail=''):
    results.append(ok)
    print(f'{"OK  " if ok else "FIX "} {name}{": " + detail if detail else ""}')


def read_env(path):
    values = {}
    for line in path.read_text('utf-8-sig').splitlines():
        s = line.strip()
        if s and not s.startswith('#') and '=' in s:
            k, v = s.split('=', 1); values[k.strip()] = v.strip().strip('"\'')
    return values


def error_name(e, *secrets):
    text = f'{type(e).__name__}: {e}'[:160]
    for s in secrets:
        if s: text = text.replace(s, '<hidden>')
    return re.sub(r'://[^@\s]+@', '://<hidden>@', text)


def check_format(v):
    key = v.get('CC_SIGNING_KEY', '')
    report('CC_SIGNING_KEY', len(key) >= 32, '' if len(key) >= 32 else 'generate one: python -c "import secrets; print(secrets.token_urlsafe(48))"')
    report('NEXT_PUBLIC_CONTACT_EMAIL', '@' in v.get('NEXT_PUBLIC_CONTACT_EMAIL', ''), 'the legal pages and upgrade links need it' if '@' not in v.get('NEXT_PUBLIC_CONTACT_EMAIL', '') else '')
    sender = v.get('CC_MAIL_FROM', '')
    report('CC_MAIL_FROM', '@' in sender and 'you@' not in sender, 'use your real sending address, for example Visuara <you@gmail.com> with your own address' if '@' not in sender or 'you@' in sender else '')
    for name in ('CC_PUBLIC_URL', 'NEXT_PUBLIC_SITE_URL'):
        if v.get(name): report(name, v[name].startswith('https://'), 'must start with https://' if not v[name].startswith('https://') else '')


def check_neon(url):
    if not url: return report('DATABASE_URL', False, 'empty: paste the connection string from Neon')
    host = urlparse(url).hostname or ''
    if 'neon.tech' in host and '-pooler' not in host: report('DATABASE_URL', False, 'use the pooled connection string (its host contains -pooler)')
    import psycopg
    for attempt in range(3):
        try:
            with psycopg.connect(url, connect_timeout=30, autocommit=True) as c: c.execute('SELECT 1')
            return report('Neon connection', True)
        except Exception as e:
            problem = error_name(e, url)
    report('Neon connection', False, problem)


def check_google(v):
    cid, secret = v.get('GOOGLE_CLIENT_ID', ''), v.get('GOOGLE_CLIENT_SECRET', '')
    if not cid or not secret: return report('Google sign-in', False, 'GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are needed')
    try:   # a code that cannot exist: Google answers invalid_grant only after it has accepted the client
        r = httpx.post('https://oauth2.googleapis.com/token', timeout=20, data={'grant_type': 'authorization_code', 'code': '4/not-a-real-code', 'client_id': cid, 'client_secret': secret, 'redirect_uri': 'https://example.com/auth/google/callback'})
        error = r.json().get('error')
        report('Google client ID and secret', error in ('invalid_grant', 'redirect_uri_mismatch'), f'Google answered {error}' if error not in ('invalid_grant', 'redirect_uri_mismatch') else '')
    except Exception as e:
        report('Google client ID and secret', False, error_name(e, secret))


def check_google_redirect(v):
    site = v.get('CC_PUBLIC_URL', '').rstrip('/')
    if not site or not v.get('GOOGLE_CLIENT_ID'): return
    uri = f'{site}/auth/google/callback'
    try:   # opens Google's consent address without signing in: Google rejects a return address it does not know
        r = httpx.get('https://accounts.google.com/o/oauth2/v2/auth', follow_redirects=True, timeout=30, headers={'User-Agent': 'Mozilla/5.0'},
                      params={'client_id': v['GOOGLE_CLIENT_ID'], 'redirect_uri': uri, 'response_type': 'code', 'scope': 'openid email profile', 'state': 'check', 'nonce': 'check'})
        ok = 'redirect_uri_mismatch' not in r.text and '/signin/oauth/error' not in str(r.url)
        report('Google return address', ok, '' if ok else f'add exactly {uri} under Authorised redirect URIs in your Google OAuth client, wait 5 minutes, then check again')
    except Exception as e:
        report('Google return address', False, error_name(e))


def smtp_session(v):
    ctx = ssl.create_default_context(); port = int(v.get('CC_SMTP_PORT') or 587)
    s = smtplib.SMTP(v['CC_SMTP_HOST'], port, timeout=20); s.starttls(context=ctx); s.login(v['CC_SMTP_USER'], v['CC_SMTP_PASSWORD'])
    return s


def check_mail(v, to):
    if not v.get('CC_SMTP_HOST'): return report('Magic-link email', False, 'CC_SMTP_HOST is empty')
    if not v.get('CC_SMTP_USER') or not v.get('CC_SMTP_PASSWORD'): return report('Magic-link email', False, 'CC_SMTP_USER and CC_SMTP_PASSWORD are needed')
    try:
        with smtp_session(v) as s:
            report('Email login', True)
            if to:
                msg = EmailMessage(); msg['From'] = v.get('CC_MAIL_FROM') or v['CC_SMTP_USER']; msg['To'] = to; msg['Subject'] = 'Visuara test email'
                msg.set_content('Magic-link email is working.')
                s.send_message(msg); report('Test email sent', True, 'check that inbox, including spam')
    except smtplib.SMTPAuthenticationError:
        report('Email login', False, 'rejected: for Gmail use an app password, not your normal password')
    except Exception as e:
        report('Email login', False, error_name(e, v['CC_SMTP_PASSWORD']))


def check_blob(token):
    if not token: return report('BLOB_READ_WRITE_TOKEN', False, 'create a Private Blob store in Vercel, connect it to the project, then remove this empty line (Vercel sets it)')
    if not token.startswith('vercel_blob_rw_'): return report('BLOB_READ_WRITE_TOKEN', False, 'should start with vercel_blob_rw_')
    os.environ['BLOB_READ_WRITE_TOKEN'] = token
    from app import blobstore
    outcome = blobstore.probe()
    report('Blob store', outcome == 'ok', '' if outcome == 'ok' else outcome)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--file', default=str(ROOT / '.env.vercel')); ap.add_argument('--mail')
    a = ap.parse_args(); path = Path(a.file)
    if not path.exists(): sys.exit(f'{path} not found')
    v = read_env(path)
    check_format(v); check_neon(v.get('DATABASE_URL', '')); check_google(v); check_google_redirect(v); check_mail(v, a.mail); check_blob(v.get('BLOB_READ_WRITE_TOKEN', ''))
    print(f'\n{sum(results)}/{len(results)} ready' if not all(results) else f'\nAll {len(results)} checks passed. Vercel does not read this file: import it under Settings > Environment Variables, for Production, then deploy.')
    sys.exit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
