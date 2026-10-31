"""Browser QA against the production-shaped docker compose stack: PostgreSQL, S3 gateway, SMTP inbox, separate workers.

    CC_WEB_PORT=3010 CC_API_PORT=8010 docker compose -f docker-compose.yml -f deploy/local/compose.loadtest.yml up -d --build
    python qa/browser_qa_stack.py --app http://localhost:3010

qa/browser_qa_web.py uses SQLite and local disk, so it cannot see what only happens with real object storage:
the browser's direct upload to the bucket (CORS and the content security policy), thumbnails and previews behind
presigned redirects, and downloads. This script walks those in Chrome, checks the public pages and security headers,
then stops the API to confirm the site fails politely and recovers. It also fails on any CSP violation or page error.
"""
import argparse, json, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'qa'))
from benchmark import fixtures_scene as scene   # noqa: E402
from browser_qa_web import CHROMES, Page, free_port   # noqa: E402
from qa_session import mail_code   # noqa: E402


def get(url, **kw):
    kw.setdefault('timeout', 30)
    return httpx.get(url, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', default='http://localhost:3000'); ap.add_argument('--out', default=str(ROOT / 'docs' / 'v11' / 'evidence' / 'browser_qa_stack.json'))
    a = ap.parse_args(); APP = a.app.rstrip('/')
    tmp = Path(tempfile.mkdtemp(prefix='cc-qastack-')); clips = tmp / 'clips'; clips.mkdir()
    room = scene.generate('room', 31, clips)['path']; product = scene.product(clips / 'tech.png', 160, 100, (180, 80, 30))
    chrome = next(c for c in CHROMES if Path(c).exists()); port = free_port()
    browser = subprocess.Popen([chrome, '--headless=new', f'--remote-debugging-port={port}', f'--user-data-dir={tmp / "p"}', '--no-first-run', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try: tabs = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json')); break
        except Exception: time.sleep(.25)
    pg = Page(next(t for t in tabs if t['type'] == 'page')['webSocketDebuggerUrl'])
    pg.cmd('Page.addScriptToEvaluateOnNewDocument', source="window.__csp=[];document.addEventListener('securitypolicyviolation',e=>window.__csp.push(e.violatedDirective+' '+e.blockedURI));")
    pg.viewport(1440, 900)
    log, violations = [], []

    def collect_csp():
        v = pg.js('window.__csp||[]') or []
        violations.extend(v)

    def step(name, fn):
        t = time.time()
        try:
            fn(); collect_csp(); log.append({'step': name, 'ok': True, 'seconds': round(time.time() - t, 1)}); print('PASS', name, flush=True)
        except Exception as e:
            page = ''
            try: page = pg.js("location.href+' | '+document.body.innerText.slice(0,300)")
            except Exception: pass
            log.append({'step': name, 'ok': False, 'error': str(e)[:400], 'page': page}); print('FAIL', name, e, '| PAGE:', page, flush=True)
            raise

    email = f'stack{int(time.time())}@qa.test'
    try:
        def public():
            r = get(APP + '/'); assert r.status_code == 200
            csp = r.headers.get('content-security-policy', '')
            for need in ("frame-ancestors 'none'", "object-src 'none'", 'connect-src'): assert need in csp, need
            assert r.headers.get('x-content-type-options') == 'nosniff' and 'x-powered-by' not in r.headers
            assert 'og:image' in r.text and 'twitter:card' in r.text
            for path, needle in (('/pricing', 'Agency'), ('/privacy', 'What we collect'), ('/terms', 'Your content and rights'), ('/robots.txt', 'Disallow: /dashboard'), ('/sitemap.xml', '/pricing')):
                r = get(APP + path); assert r.status_code == 200 and needle in r.text, path
            r = get(APP + '/opengraph-image'); assert r.status_code == 200 and r.headers['content-type'] == 'image/png' and len(r.content) > 5000
            r = get(APP + '/dashboard', follow_redirects=False); assert r.status_code in (302, 307) and '/login' in r.headers['location']
        step('public pages, security headers, SEO files, dashboard guard', public)

        def sign_up():
            pg.go(APP + '/login'); pg.wait_text('Sign in or create your account'); assert 'Privacy Policy' in pg.text()
            after = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(time.time() - 2))
            pg.type('#email', email); pg.click_text('Continue with email'); pg.wait_text('Check your email')
            pg.type('input[aria-label="Digit 1"]', mail_code(email, after))
            pg.wait("location.pathname==='/welcome'", what='onboarding'); pg.wait("!!document.getElementById('name')", what='onboarding form'); pg.type('#name', 'Stack QA')
            pg.js("(()=>{const e=document.getElementById('org');e.focus();e.select()})()"); pg.cmd('Input.insertText', text=f'Stack QA {int(time.time())}')
            pg.click_text('Create workspace'); pg.wait("location.pathname==='/dashboard'", what='dashboard')
        step('sign-up over real SMTP', sign_up)

        def upload():
            pg.go(APP + '/dashboard/decide'); pg.wait_text('New decision')
            pg.set_files('input[type=file][accept^="image"]', [product]); pg.wait("!!document.getElementById('pname')", what='product form')
            pg.type('#pname', 'Sleeve'); pg.click_text('Save product'); pg.wait_text('Sleeve', timeout=60)
            pg.js("document.querySelector('input[type=checkbox]').click()")
            pg.set_files('input[type=file][accept^="video"]', [room]); pg.wait_text('Uploaded', timeout=180)
            puts = pg.js("performance.getEntriesByType('resource').filter(e=>e.name.includes('X-Amz')||e.name.includes('Signature')).map(e=>e.name.split('?')[0])")
            assert puts, 'the video did not go straight to the bucket'
            assert not pg.js('window.__csp.length'), pg.js('window.__csp')
        step('browser uploads the video straight to the bucket (CORS, CSP)', upload)

        def decide():
            pg.type('#dname', 'Stack run'); pg.click_text('Run decision engine', exact=False)
            pg.wait("location.pathname.startsWith('/dashboard/decisions/')", what='decision page')
            pg.wait_text('Best fit:', timeout=600)
            pg.wait("[...document.images].some(i=>i.src.includes('/preview?')&&i.complete&&i.naturalWidth>0)", what='preview behind a presigned redirect')
        step('decision on a worker, preview through storage', decide)

        def library():
            url = pg.js('location.href')
            pg.go(APP + '/dashboard/library'); pg.wait("[...document.images].some(i=>i.src.includes('/products/')&&i.complete&&i.naturalWidth>0)||[...document.images].some(i=>i.complete&&i.naturalWidth>0)", what='product image'); pg.go(APP + '/dashboard/library?tab=videos'); pg.wait("[...document.images].some(i=>i.src.includes('/frames/')&&i.complete&&i.naturalWidth>0)", what='video thumbnail')
            pg.go(url); pg.wait_text('Best fit:')
        step('library thumbnails load', library)

        def render():
            pg.click_text('Create placement'); pg.wait("location.pathname.startsWith('/dashboard/placements/')", what='placement page')
            pg.click_text('Run quality check'); pg.wait_text('frames validated', timeout=240); assert 'Passed' in pg.text()
            pg.click_text('Approve'); pg.wait_text('Approved for version 1')
            pg.click_text('Start render'); pg.wait_text('Download video', timeout=300)
            status = pg.js("fetch(location.pathname.replace('/dashboard/placements/','/api/placements/')).then(r=>r.json()).then(d=>fetch('/api/jobs/'+d.jobs[0].id+'/download')).then(r=>r.status+' '+r.headers.get('content-type'))")
            assert status.startswith('200 video/mp4'), status
        step('quality check, approval, render, download through storage', render)

        def api_down():
            before = len(pg.errors)
            subprocess.run(['docker', 'compose', 'stop', 'api'], cwd=ROOT, check=True, capture_output=True, timeout=120)
            try:
                assert get(APP + '/').status_code == 200 and get(APP + '/pricing').status_code == 200      # marketing stays up
                pg.go(APP + '/dashboard'); pg.wait_text('reach Context Commerce right now', timeout=60)
                assert 'Try again' in pg.text()
            finally:
                subprocess.run(['docker', 'compose', 'start', 'api'], cwd=ROOT, check=True, capture_output=True, timeout=120)
            for _ in range(120):
                try:
                    if get(APP + '/api/health', timeout=10).status_code == 200: break
                except httpx.HTTPError: pass
                time.sleep(1)
            del pg.errors[before:]                                                  # the outage itself logs failed requests and React's server-error notice
            pg.go(APP + '/dashboard'); pg.wait_text('Hello, Stack', timeout=60)     # the session survives the outage
        step('API outage: polite error, marketing stays up, session recovers', api_down)

        errs = [e for e in pg.errors if 'favicon' not in e]
        ok = not violations and not errs
        log.append({'step': 'no CSP violations or page errors', 'ok': ok, 'violations': violations[:10], 'errors': errs[:10]})
        print('PASS' if ok else 'FAIL', 'no CSP violations or page errors', violations[:10], errs[:10], flush=True)
    finally:
        browser.kill()
        out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(log, indent=1))
    passed = sum(1 for x in log if x['ok'])
    print(f'{passed}/{len(log)} passed')
    return 0 if passed == len(log) else 1


if __name__ == '__main__':
    sys.exit(main())
