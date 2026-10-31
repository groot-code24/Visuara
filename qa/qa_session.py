"""HTTP session for QA scripts against a running stack: passwordless sign-in with the code read from Mailpit, the
double-submit CSRF header on writes, and per-endpoint latency samples."""
import re, time

import httpx

MAILPIT = 'http://127.0.0.1:8025'


def mail_code(addr, after_iso, mailpit=MAILPIT, timeout=60):
    """The 6-digit sign-in code from the newest email to `addr` received after `after_iso` (UTC)."""
    end = time.time() + timeout
    while time.time() < end:
        msgs = httpx.get(f'{mailpit}/api/v1/search', params={'query': f'to:{addr}'}, timeout=10).json().get('messages', [])
        msgs = [m for m in msgs if m['Created'] > after_iso]
        if msgs:
            text = httpx.get(f'{mailpit}/api/v1/message/{msgs[0]["ID"]}', timeout=10).json()['Text']
            return re.search(r'enter this code: (\d{6})', text).group(1)
        time.sleep(.4)
    raise AssertionError(f'no sign-in email for {addr}')


class Session:
    def __init__(self, base, email, samples=None, mailpit=MAILPIT):
        self.c = httpx.Client(base_url=base, timeout=300); self.email = email; self.samples = samples; self.mailpit = mailpit

    def call(self, method, url, label=None, expect=None, **kw):
        if method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            kw.setdefault('headers', {})['X-CSRF-Token'] = self.c.cookies.get('cc_csrf', '')
        t = time.perf_counter(); r = self.c.request(method, url, **kw); ms = (time.perf_counter() - t) * 1000
        if self.samples is not None: self.samples.append((f'{method} {label or url}', ms, r.status_code))
        if expect is not None and r.status_code not in (expect if isinstance(expect, tuple) else (expect,)):
            raise AssertionError(f'{method} {url} -> {r.status_code} {r.text[:200]}')
        return r

    def sign_in(self):
        """Email code sign-in. Returns 'signed_in' or 'signup_required'."""
        self.c.cookies.clear(); self.c.get('/api/auth/csrf')
        after = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(time.time() - 2))
        self.call('POST', '/api/auth/email/start', json={'email': self.email}, label='/api/auth/email/start', expect=200)
        code = mail_code(self.email, after, self.mailpit)
        return self.call('POST', '/api/auth/email/verify', json={'email': self.email, 'code': code}, label='/api/auth/email/verify', expect=200).json()['status']

    def sign_up(self, name, org=None, invitation=True):
        """Sign in as a new person and finish onboarding: a new workspace, or the first pending invitation."""
        assert self.sign_in() == 'signup_required'
        pending = self.call('GET', '/api/auth/signup', expect=200).json()
        body = {'name': name, 'invitation_id': pending['invitations'][0]['id']} if invitation and pending['invitations'] else {'name': name, 'organization': org}
        return self.call('POST', '/api/auth/signup', json=body, label='/api/auth/signup', expect=200).json()['user']
