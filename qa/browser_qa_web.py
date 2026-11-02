"""Browser QA for the Next.js web app against the real API, in headless Chrome over the DevTools protocol.

    cd web && npm ci && npx next build        # CC_API_ORIGIN must match --api-port (default http://127.0.0.1:8000)
    python qa/browser_qa_web.py [--out data/qa]

Starts the FastAPI API (SQLite, inline worker, sign-in emails written to an outbox) and `next start`, then walks the
product as a person would: landing and pricing; the dashboard refusing unauthenticated and forged sessions; sign-up
by emailed code and onboarding; sign-in again by magic link; adding two products and two videos through the UI;
running the decision engine; turning the recommendation into a placement; quality check, approval, render and
download; team invitation; the responsive white hero; and the whole Google flow against a local stand-in for Google
(qa/google_stub.py), through the web app's proxy: sign-up, onboarding, dashboard, returning sign-in, cancel, forged callback.
Writes screenshots and a JSON log; exits non-zero on any failed step or page error.
"""
import argparse, base64, itertools, json, os, re, socket, subprocess, sys, tempfile, time
from pathlib import Path
import httpx
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'qa'))
from benchmark import fixtures_scene as scene   # noqa: E402
from google_stub import GoogleStub   # noqa: E402

CHROMES = [r'C:\Program Files\Google\Chrome\Application\chrome.exe', r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
           '/usr/bin/google-chrome', '/usr/bin/chromium']


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


class Page:
    def __init__(self, ws_url):
        self.ws = connect(ws_url, max_size=50_000_000); self.ids = itertools.count(1); self.errors = []
        self.cmd('Runtime.enable'); self.cmd('Page.enable'); self.cmd('DOM.enable')

    def cmd(self, method, **params):
        i = next(self.ids); self.ws.send(json.dumps({'id': i, 'method': method, 'params': params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get('method') == 'Runtime.exceptionThrown':
                self.errors.append(msg['params']['exceptionDetails'].get('exception', {}).get('description', 'exception'))
            elif msg.get('method') == 'Runtime.consoleAPICalled' and msg['params']['type'] == 'error':
                self.errors.append(' '.join(str(a.get('value', a.get('description', ''))) for a in msg['params']['args']))
            if msg.get('id') == i:
                if 'error' in msg: raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get('result', {})

    def js(self, expr):
        r = self.cmd('Runtime.evaluate', expression=expr, awaitPromise=True, returnByValue=True)
        if 'exceptionDetails' in r: raise RuntimeError('JS error: ' + json.dumps(r['exceptionDetails'])[:400])
        return r['result'].get('value')

    def wait(self, expr, timeout=60, what=None):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(expr): return True
            except RuntimeError: pass        # mid-navigation
            time.sleep(.25)
        raise AssertionError(f'Timed out waiting for {what or expr}')

    def go(self, url):
        self.cmd('Page.navigate', url=url); time.sleep(.4)
        self.wait("document.readyState==='complete'", what='page load')

    def text(self): return self.js('document.body.innerText')

    def wait_text(self, needle, timeout=60):
        self.wait(f'document.body.innerText.includes({json.dumps(needle)})', timeout, what=repr(needle))

    def click_text(self, text, exact=True):
        cond = f"e.textContent.trim()==={json.dumps(text)}" if exact else f"e.textContent.includes({json.dumps(text)})"
        ok = self.js(f"(()=>{{const b=[...document.querySelectorAll('button,a')].filter(e=>{cond}&&e.offsetParent&&!e.disabled);if(!b.length)return false;b[0].click();return true}})()")
        if not ok: raise AssertionError(f'No enabled visible button/link {text!r}')
        time.sleep(.4)

    def type(self, selector, value):
        """Focus and type (CDP Input.insertText), so React sees real input events."""
        self.js(f"(()=>{{const e=document.querySelector({json.dumps(selector)});e.focus();e.select&&e.select();}})()")
        self.cmd('Input.insertText', text=value); time.sleep(.15)

    def set_files(self, selector, paths):
        root = self.cmd('DOM.getDocument', depth=-1)['root']['nodeId']
        node = self.cmd('DOM.querySelector', nodeId=root, selector=selector)['nodeId']
        if not node: raise AssertionError(f'No element {selector}')
        self.cmd('DOM.setFileInputFiles', nodeId=node, files=[str(p) for p in paths]); time.sleep(.4)

    def shot(self, path):
        Path(path).write_bytes(base64.b64decode(self.cmd('Page.captureScreenshot', format='png', captureBeyondViewport=True)['data']))

    def viewport(self, w, h, mobile=False):
        self.cmd('Emulation.setDeviceMetricsOverride', width=w, height=h, deviceScaleFactor=1, mobile=mobile)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(ROOT / 'data' / 'qa')); ap.add_argument('--api-port', type=int, default=8000); ap.add_argument('--web-port', type=int, default=3000)
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    chrome = next(c for c in CHROMES if Path(c).exists())
    tmp = Path(tempfile.mkdtemp(prefix='cc-qaweb-')); data = tmp / 'data'
    APP, API = f'http://localhost:{args.web_port}', f'http://127.0.0.1:{args.api_port}'
    env = dict(os.environ, CC_DATA_DIR=str(data), CC_DOTENV='0', CC_MAIL_BACKEND='outbox', CC_PUBLIC_URL=APP, CC_INLINE_WORKER='1',
               CC_DEFAULT_PLAN='free', CC_DECISION_WINDOW_SECONDS='2.5', CC_DECISION_CANDIDATES_PER_VIDEO='3', PYTHONPATH=str(ROOT))
    for k in ('DATABASE_URL', 'CC_S3_BUCKET', 'CC_APP_URL'): env.pop(k, None)
    google = GoogleStub('qa-client.apps.googleusercontent.com', APP + '/auth/google/callback')
    env.update(google.env())
    logs = tmp / 'logs'; logs.mkdir()
    api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(args.api_port), '--timeout-keep-alive', '75'], cwd=ROOT, env=env,
                           stdout=open(logs / 'api.log', 'wb'), stderr=subprocess.STDOUT)
    npx = 'npx.cmd' if os.name == 'nt' else 'npx'
    web = subprocess.Popen([npx, 'next', 'start', '--port', str(args.web_port)], cwd=ROOT / 'web', env=dict(env, CC_API_ORIGIN=API, NEXT_TELEMETRY_DISABLED='1'),
                           stdout=open(logs / 'web.log', 'wb'), stderr=subprocess.STDOUT)
    dport = free_port()
    browser = subprocess.Popen([chrome, '--headless=new', f'--remote-debugging-port={dport}', f'--user-data-dir={tmp / "p"}', '--no-first-run', 'about:blank'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log = []; pg = None

    def step(name, fn):
        t = time.time()
        try:
            fn(); log.append({'step': name, 'ok': True, 'seconds': round(time.time() - t, 1)}); print('PASS', name, flush=True)
        except Exception as e:
            ctx = ''
            try: ctx = pg.js("location.href+' | '+document.body.innerText.slice(0,400)")
            except Exception: pass
            log.append({'step': name, 'ok': False, 'error': str(e)[:400], 'page': ctx}); print('FAIL', name, e, '| PAGE:', ctx, flush=True)
            try: pg.shot(out / f'FAIL-{re.sub(r"[^a-z0-9]+", "-", name.lower())}.png')
            except Exception: pass
            raise

    def outbox():
        d = json.loads((data / 'outbox' / 'latest.json').read_text())
        return d['to'], re.search(r'enter this code: (\d{6})', d['text']).group(1), re.search(r'(/verify#token=[A-Za-z0-9_-]+)', d['text']).group(1)

    def clear_cookies():
        pg.cmd('Network.enable'); pg.cmd('Network.clearBrowserCookies')

    try:
        for url in (API + '/api/health/live', APP + '/'):
            for _ in range(240):
                try:
                    if httpx.get(url, timeout=2).status_code < 500: break
                except httpx.HTTPError: pass
                time.sleep(.5)
            else: raise SystemExit(f'{url} did not start; see {logs}')
        for _ in range(50):
            try: tabs = httpx.get(f'http://127.0.0.1:{dport}/json', timeout=2).json(); break
            except Exception: time.sleep(.2)
        pg = Page(next(t for t in tabs if t['type'] == 'page')['webSocketDebuggerUrl'])
        pg.viewport(1440, 900)
        clips = tmp / 'clips'
        room = scene.generate('room', 7, clips)['path']; bare = scene.generate('bare', 8, clips)['path']
        prod_a = scene.product(clips / 'aura-headphones.png', 160, 100, (180, 80, 30))
        prod_b = scene.product(clips / 'granola-bar.png', 120, 90, (40, 160, 220))

        def landing():
            pg.go(APP + '/')
            pg.wait_text('you already have.')
            assert 'into sponsor inventory.' in pg.text() and pg.js("document.title").startswith('Visuara')
            # The interactive demo: choosing the food product moves the recommendation to the tabletop.
            pg.click_text('Granola', exact=False); pg.wait_text('Granola → Tabletop', timeout=10)
            pg.wait("document.body.innerText.includes('Placed')", timeout=10, what='placement in the demo')
            pg.shot(out / '01-landing.png')
            pg.go(APP + '/pricing'); pg.wait_text('Priced like a tool, not a production budget')
            assert '$29' in pg.text() and '$119' in pg.text()      # annual by default
            pg.click_text('Monthly'); pg.wait_text('$159')
            assert '$39' in pg.text() and 'How this compares' in pg.text()
            pg.shot(out / '02-pricing.png')
        step('landing and pricing pages', landing)

        def guard():
            clear_cookies()
            for path in ('/dashboard', '/dashboard/decide', '/dashboard/settings'):
                pg.go(APP + path)
                pg.wait(f"location.pathname==='/login' && location.search.includes({json.dumps('next=' + path.replace('/', '%2F'))})", what=f'redirect from {path}')
                assert 'Hello' not in pg.text()
            # A forged session cookie passes the first check but is refused by the server-side validation.
            pg.cmd('Network.setCookie', name='cc_session', value='forged-session-token', url=APP)
            pg.go(APP + '/dashboard/library')
            pg.wait("location.pathname==='/login'", what='forged cookie refused')
            r = httpx.get(APP + '/api/products', cookies={'cc_session': 'forged'})
            assert r.status_code == 401
            clear_cookies()
        step('dashboard refuses unauthenticated and forged sessions', guard)

        def signup():
            pg.go(APP + '/login'); pg.wait_text('Sign in or create your account')
            pg.wait("[...document.querySelectorAll('a')].some(a=>a.textContent.includes('Continue with Google'))", what='Google button')
            pg.shot(out / '03-login.png')
            pg.type('#email', 'maya@aurasound.io'); pg.click_text('Continue with email')
            pg.wait_text('Check your email'); pg.wait_text('Open the magic link')  # no email provider here: the page offers the link itself
            to, code, _ = outbox(); assert to == 'maya@aurasound.io'
            pg.shot(out / '04-check-email.png')
            pg.type('input[aria-label="Digit 1"]', code)                          # paste fills every box and submits
            pg.wait("location.pathname==='/welcome'", what='onboarding')
            pg.wait_text("Let's set you up")
            assert pg.js("document.getElementById('org').value") == 'Aurasound'   # suggested from the email domain
            pg.type('#name', 'Maya Chen')
            pg.shot(out / '05-welcome.png')
            pg.click_text('Create workspace')
            pg.wait("location.pathname==='/dashboard'", what='dashboard'); pg.wait_text('Hello, Maya')
            pg.wait_text('Place your first product')
            pg.shot(out / '06-dashboard-empty.png')
        step('sign-up by emailed code and onboarding', signup)

        def magic_link():
            pg.js("document.querySelector('button[aria-label=\"Sign out\"]').click()")
            pg.wait("location.pathname==='/login'", what='signed out')
            pg.go(APP + '/dashboard'); pg.wait("location.pathname==='/login'", what='session gone after sign-out')
            pg.type('#email', 'maya@aurasound.io'); pg.click_text('Continue with email'); pg.wait_text('Check your email')
            _, _, link = outbox()
            pg.click_text('Open the magic link')
            pg.wait_text('Continue as maya@aurasound.io')
            assert pg.js('location.hash') == ''                                  # token removed from the address bar
            assert 'Hello' not in pg.text()                                      # opening the link alone does not sign in
            pg.shot(out / '07-magic-link.png')
            pg.click_text('Continue'); pg.wait("location.pathname==='/dashboard'"); pg.wait_text('Hello, Maya')
            pg.go(APP + link); pg.wait_text('This link has expired')            # single use
        step('sign-in again by magic link (explicit click, single use)', magic_link)

        def library():
            pg.go(APP + '/dashboard/decide'); pg.wait_text('New decision')
            for path, name, cat in ((prod_a, 'Aura Headphones', 'tech'), (prod_b, 'Granola Bar', 'food')):
                pg.set_files('input[type=file][accept^="image"]', [path])
                pg.wait("!!document.getElementById('pname')", what='product form')
                pg.type('#pname', name)
                pg.js(f"(()=>{{const s=document.getElementById('pcat');const set=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set;set.call(s,{json.dumps(cat)});s.dispatchEvent(new Event('change',{{bubbles:true}}))}})()")
                pg.click_text('Save product'); pg.wait_text(name)
            pg.wait("document.body.innerText.includes('2 products')", what='both products selected')
            pg.js("document.querySelector('input[type=checkbox]').click()")      # rights confirmation
            pg.set_files('input[type=file][accept^="video"]', [room, bare])
            pg.wait("document.body.innerText.split('Uploaded').length-1>=2", timeout=120, what='two uploads')
            pg.wait("document.body.innerText.includes('2 videos')", what='both videos selected')
            pg.type('#dname', 'Spring launch')
            pg.shot(out / '08-new-decision.png')
        step('add two products and two videos through the UI', library)

        def decide():
            pg.click_text('Run decision engine', exact=False)
            pg.wait("location.pathname.startsWith('/dashboard/decisions/')", what='decision page')
            time.sleep(1.5)
            pg.shot(out / '09-decision-running.png')
            pg.wait_text('Best fit:', timeout=300)
            t = pg.text()
            assert 'No confident fit' in t                                        # the bare room has no usable surface
            assert '1 of 2 videos have a confident fit' in t
            pg.wait("[...document.images].some(i=>i.src.includes('/preview?')&&i.complete&&i.naturalWidth>0)", what='composited preview image')
            pg.shot(out / '10-decision-result.png')
        step('decision engine: which product fits where', decide)

        def placement():
            pg.click_text('Create placement')
            pg.wait("location.pathname.startsWith('/dashboard/placements/')", what='placement page')
            pg.wait_text('Run quality check', timeout=60); pg.click_text('Run quality check'); pg.wait_text('frames validated', timeout=180)
            assert 'Passed' in pg.text()
            pg.wait("[...document.images].some(i=>i.src.includes('/preview?t=')&&i.complete&&i.naturalWidth>0)", what='frame preview')
            pg.click_text('Approve'); pg.wait_text('Approved for version 1')
            pg.click_text('Start render'); pg.wait_text('Download video', timeout=240)
            assert 'Output validated frame by frame' in pg.text()
            pg.shot(out / '11-placement-rendered.png')
            status = pg.js("fetch(location.pathname.replace('/dashboard/placements/','/api/placements/')).then(r=>r.json()).then(d=>fetch('/api/jobs/'+d.jobs[0].id+'/download')).then(r=>r.status+' '+r.headers.get('content-type'))")
            assert status.startswith('200 video/mp4'), status
            pg.go(APP + '/dashboard/renders'); pg.wait_text('Download'); pg.shot(out / '12-renders.png')
        step('placement: quality check, approval, render, download', placement)

        def settings():
            pg.go(APP + '/dashboard/settings'); pg.wait_text('Team'); pg.wait_text('Decisions this month')
            assert 'Free' in pg.text() and 'Decisions this month' in pg.text()
            pg.type('input[aria-label="Email to invite"]', 'leo@aurasound.io'); pg.click_text('Invite')
            pg.wait("document.body.innerText.includes('Upgrade your plan')||document.body.innerText.includes('Pending invitations')", what='invite outcome')
            assert 'Upgrade your plan' in pg.text()                               # the Free plan has one seat
            pg.shot(out / '13-settings.png')
        step('settings: plan usage and the Free plan seat limit', settings)

        def mobile():
            pg.viewport(390, 844, mobile=True)
            pg.go(APP + '/dashboard'); pg.wait_text('Hello, Maya')
            assert pg.js('document.documentElement.scrollWidth<=window.innerWidth+1'), 'horizontal scroll on mobile'
            pg.shot(out / '14-mobile-dashboard.png')
            pg.go(APP + '/'); pg.wait_text('Put your products')
            assert pg.js('document.documentElement.scrollWidth<=window.innerWidth+1'), 'horizontal scroll on mobile landing'
            pg.shot(out / '15-mobile-landing.png')
            pg.viewport(1440, 900)
        step('mobile layout (390 px)', mobile)

        def hero():
            for w, h, mobile_ in ((320, 640, True), (390, 844, True), (768, 1024, True), (1024, 768, False), (1440, 900, False)):
                pg.viewport(w, h, mobile=mobile_); pg.go(APP + '/'); pg.wait_text('you already have.')
                assert pg.js("getComputedStyle(document.querySelector('main section')).backgroundColor") == 'rgb(255, 255, 255)', 'hero is not pure white'
                assert pg.js('document.documentElement.scrollWidth<=window.innerWidth+1'), f'horizontal scroll at {w}px'
                assert pg.js("document.querySelector('h1').getBoundingClientRect().right<=innerWidth"), f'headline cut off at {w}px'
                if w < 768: assert pg.js("""(()=>{const b=document.querySelector('button[aria-label="Open menu"]').getBoundingClientRect();return b.width>0&&b.right<=innerWidth})()"""), f'menu button off screen at {w}px'
                pg.shot(out / f'16-hero-{w}.png')
            pg.viewport(1440, 900)
        step('hero is pure white and fits 320 to 1440 px', hero)

        def google_flow():
            clear_cookies(); pg.go(APP + '/login'); pg.wait_text('Sign in or create your account')
            pg.wait("[...document.querySelectorAll('a')].some(a=>a.textContent.includes('Continue with Google')&&a.getAttribute('aria-disabled')!=='true')", what='Google button')
            pg.click_text('Continue with Google')
            pg.wait("location.pathname==='/welcome'", what='onboarding after Google'); pg.wait_text("Let's set you up")
            assert pg.js("document.getElementById('name').value") == 'Ana Silva'     # the name comes from Google
            assert pg.js("document.getElementById('org').value") == 'Northbrew'
            pg.click_text('Create workspace')
            pg.wait("location.pathname==='/dashboard'", what='dashboard after onboarding'); pg.wait_text('Hello, Ana')
            pg.js("document.querySelector('button[aria-label=\"Sign out\"]').click()"); pg.wait("location.pathname==='/login'", what='signed out')
            pg.wait("[...document.querySelectorAll('a')].some(a=>a.textContent.includes('Continue with Google')&&a.getAttribute('aria-disabled')!=='true')", what='Google button')
            pg.click_text('Continue with Google')                                    # a returning person goes straight in
            pg.wait("location.pathname==='/dashboard'", what='dashboard on return'); pg.wait_text('Hello, Ana')
            clear_cookies(); google.deny = True; pg.go(APP + '/login')
            pg.wait("[...document.querySelectorAll('a')].some(a=>a.textContent.includes('Continue with Google')&&a.getAttribute('aria-disabled')!=='true')", what='Google button')
            pg.click_text('Continue with Google'); pg.wait_text('Google sign-in was cancelled'); google.deny = False
            r = httpx.get(APP + '/auth/google/callback', params={'code': 'x', 'state': 'forged'}, follow_redirects=False)
            assert r.status_code == 303 and r.headers['location'] == '/login?error=google_failed'
            r = httpx.get(APP + '/auth/google/start', follow_redirects=False)
            assert 'HttpOnly' in r.headers['set-cookie'] and 'cc_oauth' in r.headers['set-cookie']
        step('Google sign-in through the proxy: onboarding, dashboard, returning, cancelled, forged', google_flow)

        errors = [e for e in pg.errors if 'favicon' not in e]
        log.append({'step': 'no page errors', 'ok': not errors, 'errors': errors[:10]})
        print('PASS' if not errors else 'FAIL', 'no page errors', errors[:5])
    finally:
        (out / 'browser_qa_web.json').write_text(json.dumps(log, indent=2))
        for p in (browser, web, api):            # whole process trees: npx starts node as a child process
            try:
                if os.name == 'nt': subprocess.run(['taskkill', '/T', '/F', '/PID', str(p.pid)], capture_output=True)
                else: p.kill()
            except Exception: pass
    if not all(s['ok'] for s in log): sys.exit(1)
    print(f'{sum(s["ok"] for s in log)}/{len(log)} passed; screenshots in {out}')


if __name__ == '__main__':
    main()
