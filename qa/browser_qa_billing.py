"""Browser QA for buying a plan: pricing page -> sign-up -> onboarding -> Lemon Squeezy checkout -> paid plan, in headless Chrome.

    cd web && CC_API_ORIGIN=http://127.0.0.1:<api-port> npx next build
    python qa/browser_qa_billing.py --api-port <api-port> --web-port <web-port> [--out data/qa-billing]

Lemon Squeezy itself is replaced by a small local stand-in (CC_LEMONSQUEEZY_API_URL, refused in production). It speaks
the documented API: POST /checkouts returns a hosted-checkout URL, and "paying" there delivers a `subscription_created`
webhook signed exactly as Lemon Squeezy signs (X-Signature, hex HMAC-SHA256 of the body), then redirects to the
redirect_url from the checkout request. PATCH /subscriptions/{id} changes the variant; GET returns the portal link.
Everything the API does with those answers is the production code path.
"""
import argparse, hashlib, hmac, itertools, json, os, re, subprocess, sys, tempfile, threading, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'qa'))
from browser_qa_web import CHROMES, Page, free_port   # noqa: E402

STORE = '77'
VARIANTS = {'LEMONSQUEEZY_VARIANT_PRO_MONTHLY': '1001', 'LEMONSQUEEZY_VARIANT_PRO_ANNUAL': '1002',
            'LEMONSQUEEZY_VARIANT_AGENCY_MONTHLY': '2001', 'LEMONSQUEEZY_VARIANT_AGENCY_ANNUAL': '2002'}


def now(): return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


class FakeLemonSqueezy:
    """The parts of Lemon Squeezy the API uses. Records every request it receives."""

    def __init__(self, secret, webhook_url):
        self.secret, self.webhook_url = secret, webhook_url
        self.checkouts, self.subs, self.requests, self.ids = {}, {}, [], itertools.count(5001)
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _json(self, status, obj):
                b = json.dumps(obj).encode(); self.send_response(status)
                self.send_header('Content-Type', 'application/vnd.api+json'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)

            def _body(self):
                n = int(self.headers.get('Content-Length') or 0)
                return self.rfile.read(n) if n else b''

            def do_POST(self):
                raw = self._body()
                if self.path == '/checkouts':
                    fake.requests.append(('POST', self.path, dict(self.headers), json.loads(raw)))
                    cid = f'chk-{len(fake.checkouts) + 1}'; fake.checkouts[cid] = json.loads(raw)['data']
                    return self._json(201, {'data': {'type': 'checkouts', 'id': cid, 'attributes': {'url': f'{fake.base}/checkout/{cid}', 'test_mode': True}}})
                m = re.match(r'^/checkout/([\w-]+)/pay$', self.path)
                if m and m.group(1) in fake.checkouts:
                    ck = fake.checkouts[m.group(1)]; sid = next(fake.ids)
                    attrs = {'store_id': int(ck['relationships']['store']['data']['id']), 'customer_id': 9, 'variant_id': int(ck['relationships']['variant']['data']['id']),
                             'status': 'active', 'renews_at': '2027-11-06T10:00:00.000000Z', 'ends_at': None, 'test_mode': ck['attributes'].get('test_mode', True),
                             'card_brand': 'visa', 'card_last_four': '4242', 'user_email': ck['attributes']['checkout_data']['email'], 'updated_at': now()}
                    fake.subs[str(sid)] = attrs
                    event = json.dumps({'meta': {'event_name': 'subscription_created', 'custom_data': ck['attributes']['checkout_data']['custom']},
                                        'data': {'type': 'subscriptions', 'id': str(sid), 'attributes': attrs}}).encode()
                    sig = hmac.new(fake.secret.encode(), event, hashlib.sha256).hexdigest()
                    r = httpx.post(fake.webhook_url, content=event, headers={'X-Signature': sig, 'X-Event-Name': 'subscription_created', 'Content-Type': 'application/json'}, timeout=30)
                    fake.requests.append(('WEBHOOK', r.status_code, r.text))
                    self.send_response(303); self.send_header('Location', ck['attributes']['product_options']['redirect_url']); self.end_headers(); return
                self._json(404, {'errors': [{'detail': 'not found'}]})

            def do_PATCH(self):
                raw = self._body(); m = re.match(r'^/subscriptions/(\d+)$', self.path)
                if not m or m.group(1) not in fake.subs: return self._json(404, {'errors': [{'detail': 'not found'}]})
                body = json.loads(raw); fake.requests.append(('PATCH', self.path, dict(self.headers), body))
                fake.subs[m.group(1)].update(variant_id=int(body['data']['attributes']['variant_id']), updated_at=now())
                self._json(200, {'data': {'type': 'subscriptions', 'id': m.group(1), 'attributes': fake.subs[m.group(1)]}})

            def do_GET(self):
                m = re.match(r'^/subscriptions/(\d+)$', self.path)
                if m and m.group(1) in fake.subs:
                    a = dict(fake.subs[m.group(1)], urls={'customer_portal': f'{fake.base}/portal', 'update_payment_method': f'{fake.base}/card'})
                    return self._json(200, {'data': {'type': 'subscriptions', 'id': m.group(1), 'attributes': a}})
                m = re.match(r'^/checkout/([\w-]+)$', self.path)
                if m or self.path == '/portal':
                    html = ('<!doctype html><title>Lemon Squeezy (local stand-in)</title><body style="font-family:sans-serif;padding:40px">'
                            + (f'<h1>Checkout</h1><p>Test card 4242 4242 4242 4242</p><form method="post" action="/checkout/{m.group(1)}/pay"><button>Pay</button></form>'
                               if m else '<h1>Customer portal</h1>') + '</body>').encode()
                    self.send_response(200); self.send_header('Content-Type', 'text/html'); self.send_header('Content-Length', str(len(html))); self.end_headers(); self.wfile.write(html); return
                self._json(404, {'errors': [{'detail': 'not found'}]})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(ROOT / 'data' / 'qa-billing'))
    ap.add_argument('--api-port', type=int, default=8000); ap.add_argument('--web-port', type=int, default=3000)
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    chrome = next(c for c in CHROMES if Path(c).exists())
    tmp = Path(tempfile.mkdtemp(prefix='visuara-qabill-')); data = tmp / 'data'; data.mkdir()
    APP, API = f'http://localhost:{args.web_port}', f'http://127.0.0.1:{args.api_port}'
    secret = 'qa-walk-' + os.urandom(12).hex()                    # exists only for this walk
    ls = FakeLemonSqueezy(secret, API + '/api/billing/webhooks/lemonsqueezy')
    env = dict(os.environ, CC_DOTENV='0', CC_MAIL_BACKEND='outbox', CC_PUBLIC_URL=APP, PYTHONPATH=str(ROOT), PYTHONIOENCODING='utf-8',
               CC_DATA_DIR=str(data), CC_INLINE_WORKER='1', CC_DEFAULT_PLAN='free', CC_AUTH_RATE_PER_MINUTE='60',
               LEMONSQUEEZY_API_KEY='qa-walk-key', LEMONSQUEEZY_STORE_ID=STORE, LEMONSQUEEZY_WEBHOOK_SECRET=secret, CC_LEMONSQUEEZY_API_URL=ls.base, **VARIANTS)
    for k in ('DATABASE_URL', 'CC_DATABASE_URL', 'CC_S3_BUCKET', 'CC_APP_URL', 'CC_PAYMENT_LIVE', 'CC_ENV'): env.pop(k, None)
    logs = tmp / 'logs'; logs.mkdir()
    api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(args.api_port)], cwd=ROOT, env=env,
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
            try: ctx = pg.js("location.href+' | '+document.body.innerText.slice(0,600)")
            except Exception: pass
            log.append({'step': name, 'ok': False, 'error': str(e)[:400], 'page': ctx}); print('FAIL', name, e, '| PAGE:', ctx, flush=True)
            try: pg.shot(out / f'FAIL-{re.sub(r"[^a-z0-9]+", "-", name.lower())}.png')
            except Exception: pass
            raise

    def shot(path):
        pg.wait("(()=>{const l=document.querySelector('.vs-loader');return !l||getComputedStyle(l).visibility==='hidden'})()", what='preloader finished', timeout=10)
        pg.shot(path)

    def api_json(path): return pg.js(f"fetch({json.dumps(path)}).then(r=>r.json())")

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
        email = 'nina@lumen.example'

        def pricing_to_onboarding():
            pg.cmd('Network.enable'); pg.cmd('Network.clearBrowserCookies')
            pg.go(APP + '/pricing'); pg.wait_text('Start with Pro')
            pg.click_text('Start with Pro'); pg.wait("location.pathname==='/login'", what='login')
            pg.wait_text('pay securely on the last step')
            pg.type('#email', email); pg.click_text('Continue with email'); pg.wait_text('Check your email')
            d = json.loads((data / 'outbox' / 'latest.json').read_text())
            pg.type('input[aria-label="Digit 1"]', re.search(r'enter this code: (\d{6})', d['text']).group(1))
            pg.wait("location.pathname==='/welcome'", what='onboarding'); pg.wait_text("Let's set you up")
            shot(out / '01-welcome-you.png')
            pg.type('#name', 'Nina Park'); pg.click_text('Continue'); pg.wait_text('What will you use Visuara for?')
            pg.type('#org', 'Lumen Studio')
            pg.js("[...document.querySelectorAll('label')].find(l=>l.textContent.includes('A publisher or studio')).click()")
            shot(out / '02-welcome-workspace.png')
            pg.click_text('Continue'); pg.wait_text('Choose your plan')
            pg.wait("[...document.querySelectorAll('button[aria-pressed=true]')].some(b=>b.textContent.startsWith('Pro'))", what='Pro chosen on the pricing page is preselected')
            pg.wait_text('Test mode: no real charge')
            shot(out / '03-welcome-plan.png')
        step('pricing page -> sign-up -> onboarding offers the plan chosen on the pricing page', pricing_to_onboarding)

        def checkout_and_webhook():
            pg.click_text('Create workspace and pay $348 a year')
            pg.wait(f"location.href.startsWith({json.dumps(ls.base + '/checkout/')})", what='Lemon Squeezy checkout')
            method, path, headers, body = next(r for r in ls.requests if r[0] == 'POST')
            h = {k.lower(): v for k, v in headers.items()}
            assert h['authorization'] == 'Bearer qa-walk-key' and h['accept'] == 'application/vnd.api+json', h
            assert body['data']['relationships']['variant']['data']['id'] == '1002' and body['data']['attributes']['test_mode'] is True
            assert body['data']['attributes']['checkout_data']['email'] == email
            pg.shot(out / '04-checkout.png')
            pg.click_text('Pay')
            pg.wait("location.pathname==='/dashboard/settings'", what='back in Settings')
            assert ('WEBHOOK', 200) == next(r for r in ls.requests if r[0] == 'WEBHOOK')[:2], ls.requests
            pg.wait_text('billed yearly'); pg.wait_text('Renews')
            b = api_json('/api/billing'); assert b['plan'] == 'pro' and b['subscription']['status'] == 'active', b
            assert api_json('/api/me')['org_kind'] == 'publisher'
            shot(out / '05-settings-pro.png')
        step('pay on the checkout: the signed webhook moves the workspace to Pro', checkout_and_webhook)

        def switch_plan():
            pg.js('window.confirm=()=>true')
            pg.click_text('Switch to Agency'); pg.wait_text('Plan changed')
            b = api_json('/api/billing'); assert b['plan'] == 'team' and b['subscription']['plan'] == 'team', b
            patch = next(r for r in ls.requests if r[0] == 'PATCH'); assert patch[3]['data']['attributes'] == {'variant_id': 2002}, patch
            assert api_json('/api/usage')['plan_limits']['renders_per_month'] == 200
            shot(out / '06-settings-agency.png')
        step('switch to Agency: the subscription moves to the Agency variant', switch_plan)

        def portal():
            pg.click_text('Manage billing, card and invoices')
            pg.wait(f"location.href==={json.dumps(ls.base + '/portal')}", what='customer portal')
        step('manage billing opens the customer portal', portal)

        def no_double_checkout():
            pg.go(APP + '/dashboard/settings'); pg.wait_text('Switch plan')
            r = pg.js("fetch('/api/billing/checkout',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':document.cookie.match(/cc_csrf=([^;]+)/)[1]},body:JSON.stringify({plan:'pro',interval:'monthly'})}).then(r=>r.status)")
            assert r == 409, r
        step('a subscribed workspace cannot start a second subscription', no_double_checkout)

        def mobile():
            pg.viewport(390, 844, mobile=True)
            pg.go(APP + '/dashboard/settings'); pg.wait_text('Switch plan')
            assert pg.js('document.documentElement.scrollWidth<=window.innerWidth+1'), 'settings scroll sideways at 390 px'
            shot(out / '07-mobile-settings.png'); pg.viewport(1440, 900)
        step('mobile layout (390 px) of the billing settings', mobile)

        errors = [e for e in pg.errors if 'favicon' not in e]
        log.append({'step': 'no page errors', 'ok': not errors, 'errors': errors[:10]})
        print('PASS' if not errors else 'FAIL', 'no page errors', errors[:5])
    finally:
        (out / 'browser_qa_billing.json').write_text(json.dumps(log, indent=2))
        for p in (browser, web, api):
            try:
                if os.name == 'nt': subprocess.run(['taskkill', '/T', '/F', '/PID', str(p.pid)], capture_output=True)
                else: p.kill()
            except Exception: pass
        ls.server.shutdown()
    if not all(s['ok'] for s in log): sys.exit(1)
    print(f'{sum(s["ok"] for s in log)}/{len(log)} passed; screenshots in {out}')


if __name__ == '__main__':
    main()
