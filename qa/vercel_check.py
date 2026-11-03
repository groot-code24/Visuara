"""Check a real Vercel deployment from the outside, right after deploying. It changes nothing and needs no credentials.

    python qa/vercel_check.py https://your-project.vercel.app

Each line says what passed or what to fix. Signing in with Google and receiving a magic-link email still need a person:
docs/vercel.md lists those last steps.
"""
import sys

import httpx

FIXES = {'database': 'DATABASE_URL: use the Neon connection string (pooled) and check the Neon project is not suspended',
         'object_storage': 'BLOB_READ_WRITE_TOKEN: connect a private Blob store to this project and redeploy',
         'ffmpeg': 'the API image was not built from Dockerfile.vercel: check vercel.json is at the repository root'}


def main(base):
    base = base.rstrip('/'); failures = 0
    http = httpx.Client(timeout=60, follow_redirects=False)
    moved = http.get(base + '/')
    if moved.is_redirect:       # a custom domain usually redirects one address to the other: check the one people end up on
        base = str(httpx.URL(moved.headers['location']).copy_with(path='', query=None)).rstrip('/'); print(f'Checking {base}, where the site redirects\n')

    def check(name, fn):
        nonlocal failures
        try:
            note = fn()
            print(f'PASS  {name}' + (f' ({note})' if note else ''))
        except AssertionError as e:
            failures += 1; print(f'FAIL  {name}\n      {e}')
        except httpx.HTTPError as e:
            failures += 1; print(f'FAIL  {name}\n      could not reach it: {type(e).__name__}')

    def home():
        r = http.get(base + '/'); assert r.status_code == 200, f'home page returned {r.status_code}'
        assert 'content-security-policy' in r.headers and 'strict-transport-security' in r.headers, 'security headers are missing'

    def api():
        r = http.get(base + '/api/health/live')
        assert r.status_code == 200 and r.headers.get('content-type', '').startswith('application/json'), (
            f'/api is not reaching the backend (status {r.status_code}). If this is 500, open Vercel > Logs: the API lists any missing setting when it starts. If it says DATABASE_URL is not set, your settings are not in Vercel: import .env.vercel for Production, then deploy again. '
            'If the page is HTML, the project framework is not set to Services.')

    def ready():
        r = http.get(base + '/api/health/ready')
        assert r.headers.get('content-type', '').startswith('application/json'), f'/api/health/ready returned {r.status_code}: the API is not running'
        checks = r.json().get('checks', {})
        bad = [k for k in FIXES if checks.get(k) not in ('ok',)]
        assert not bad, '; '.join(f'{k} is {checks.get(k)}. Fix {FIXES[k]}' for k in bad)
        return 'database, Blob storage and FFmpeg'

    def mode():
        r = http.get(base + '/api/health'); assert r.headers.get('content-type', '').startswith('application/json'), f'/api/health returned {r.status_code}: the API is not running'
        h = r.json()
        assert h.get('mode') == 'production', 'not running in production mode: the API should be built from Dockerfile.vercel'
        assert h.get('queue') == 'on_demand_runner', f'unexpected job queue: {h.get("queue")}'
        assert h['object_storage']['backend'] == 'vercel_blob', f'storage is {h["object_storage"]["backend"]}, expected vercel_blob'

    def providers():
        r = http.get(base + '/api/auth/providers'); assert r.headers.get('content-type', '').startswith('application/json'), f'/api/auth/providers returned {r.status_code}: the API is not running'
        p = r.json()
        assert p.get('email'), 'magic links are off: set CC_SMTP_HOST, CC_SMTP_USER, CC_SMTP_PASSWORD and CC_MAIL_FROM, then redeploy'
        assert p.get('google'), 'Google sign-in is off: set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, then redeploy'

    def google():
        r = http.get(base + '/auth/google/start')
        assert r.status_code == 303 and 'accounts.google.com' in r.headers.get('location', ''), f'/auth/google/start returned {r.status_code}'
        uri = dict(httpx.URL(r.headers['location']).params).get('redirect_uri')
        assert uri == base + '/auth/google/callback', f'Google is being told to return to {uri}. Set CC_PUBLIC_URL={base} in Vercel and redeploy'
        return f'add exactly this redirect URI to your Google client: {uri}'

    def guard():
        r = http.get(base + '/dashboard'); assert r.status_code in (302, 307) and '/login' in r.headers.get('location', ''), f'/dashboard returned {r.status_code} when signed out'

    def runner():
        r = http.post(base + '/api/internal/run-jobs'); assert r.status_code == 401, f'the job runner answered {r.status_code} to a request with no key'

    check('home page and security headers', home)
    check('/api reaches the backend', api)
    check('dependencies ready', ready)
    check('production mode, on-demand jobs, Blob storage', mode)
    check('magic link and Google are configured', providers)
    check('Google sign-in starts correctly', google)
    check('signed-out dashboard redirects to sign-in', guard)
    check('job runner refuses outsiders', runner)
    print('\nAll automatic checks passed.' if not failures else f'\n{failures} check(s) failed.')
    return 1 if failures else 0


if __name__ == '__main__':
    if len(sys.argv) != 2: sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
