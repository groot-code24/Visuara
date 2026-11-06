"""Browser QA for the marketplace pages (Phases 2 to 15) against the real API, in headless Chrome.

    cd web && CC_API_ORIGIN=http://127.0.0.1:<api-port> npx next build
    python qa/browser_qa_marketplace.py --api-port <api-port> --web-port <web-port> [--out data/qa-marketplace]

Seeds the demonstration environment (`ops/demo.py`: a publisher with two analysed, rights-verified, listed videos; a brand
with a product, a submitted campaign, a reservation and a placement request; a platform administrator) into a fresh
SQLite folder, starts the API and `next start` on it, and then walks the marketplace as the three people would, each signed
in by the emailed code:
  publisher  analyses list and detail, inventory, approves the request
  brand      marketplace, campaigns and the campaign page, accepts the quote
  admin      marks the invoice paid, sees the trial balance
  publisher  starts production; quality check, approval and render on the placement page; earnings
  brand      accepts the delivery; the request reads Accepted
  brand      finance shows the paid invoice; organizations page
Writes screenshots and a JSON log; exits non-zero on any failed step or page error.
"""
import argparse, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'qa'))
from browser_qa_web import CHROMES, Page, free_port   # noqa: E402

PUBLISHER, BRAND, ADMIN = 'publisher@demo.visuara.si', 'brand@demo.visuara.si', 'admin@demo.visuara.si'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(ROOT / 'data' / 'qa-marketplace'))
    ap.add_argument('--api-port', type=int, default=8000); ap.add_argument('--web-port', type=int, default=3000)
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    chrome = next(c for c in CHROMES if Path(c).exists())
    tmp = Path(tempfile.mkdtemp(prefix='visuara-qamkt-')); data = tmp / 'data'
    APP, API = f'http://localhost:{args.web_port}', f'http://127.0.0.1:{args.api_port}'
    # The walk signs six different people in from one machine, and the API allows 12 sign-in requests a minute per IP
    # address. When the steps run quickly they exceed that and the 13th gets a 429, which is the limit working.
    # Real people have their own addresses, so the walk raises this one setting rather than slowing every step.
    env = dict(os.environ, CC_DOTENV='0', CC_MAIL_BACKEND='outbox', CC_PUBLIC_URL=APP, PYTHONPATH=str(ROOT), PYTHONIOENCODING='utf-8',
               CC_AUTH_RATE_PER_MINUTE='60',
               # M15: the brand pays through the built-in test-mode provider; its webhook secret exists only for this walk
               CC_PAYMENT_PROVIDER='sandbox', CC_PAYMENT_WEBHOOK_SECRET='qa-walk-' + os.urandom(12).hex())
    for k in ('DATABASE_URL', 'CC_DATABASE_URL', 'CC_S3_BUCKET', 'CC_APP_URL', 'CC_VISION_MODEL'): env.pop(k, None)
    seeded = subprocess.run([sys.executable, '-m', 'ops.demo', 'seed', '--data', str(data)], cwd=ROOT, env=env, capture_output=True, text=True)
    if seeded.returncode: raise SystemExit('Demo seeding failed:\n' + seeded.stdout[-2000:] + seeded.stderr[-2000:])
    env.update(CC_DATA_DIR=str(data), CC_INLINE_WORKER='1', CC_DEFAULT_PLAN='team')
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

    def sign_in(email):
        """Sign out whoever is signed in, then sign in by the emailed code: the real path, no forged session."""
        pg.cmd('Network.enable'); pg.cmd('Network.clearBrowserCookies')
        pg.go(APP + '/login'); pg.wait("!!document.getElementById('email')", what='login form')
        pg.type('#email', email); pg.click_text('Continue with email'); pg.wait_text('Check your email')
        d = json.loads((data / 'outbox' / 'latest.json').read_text())
        assert d['to'] == email, d['to']
        pg.type('input[aria-label="Digit 1"]', re.search(r'enter this code: (\d{6})', d['text']).group(1))
        pg.wait("location.pathname==='/dashboard'", what=f'dashboard for {email}')

    def click_link(prefix, text=None):
        cond = f"a.getAttribute('href')&&a.getAttribute('href').startsWith({json.dumps(prefix)})" + (f"&&a.textContent.includes({json.dumps(text)})" if text else '')
        if not pg.js(f"(()=>{{const a=[...document.querySelectorAll('a')].find(a=>{cond}&&a.offsetParent);if(!a)return false;a.click();return true}})()"):
            raise AssertionError(f'No visible link to {prefix}' + (f' with {text!r}' if text else ''))

    def shot(path):
        """Screenshot once the page-load preloader (a 1.7 s CSS animation over the page) has played out."""
        pg.wait("(()=>{const l=document.querySelector('.vs-loader');return !l||getComputedStyle(l).visibility==='hidden'})()", what='preloader finished', timeout=10)
        pg.shot(path)

    def api_json(path):
        """A GET through the web app's proxy with the signed-in browser's cookies."""
        return pg.js(f"fetch({json.dumps(path)}).then(r=>r.json())")

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

        def content_security_policy():
            seen = set()
            for path in ('/', '/login', '/pricing', '/dashboard'):
                r = httpx.get(APP + path, follow_redirects=True, timeout=30)
                csp = r.headers.get('content-security-policy', '')
                script = next((d for d in csp.split(';') if d.strip().startswith('script-src')), '')
                nonce = re.search(r"'nonce-([^']+)'", script)
                assert nonce and "'unsafe-inline'" not in script, f'{path}: script-src is {script!r}'
                assert f'nonce="{nonce.group(1)}"' in r.text, f'{path}: page scripts do not carry the nonce'
                seen.add(nonce.group(1))
            assert len(seen) == 4, 'a nonce was reused across requests'
        step('every page has a per-request script nonce and no inline-script allowance', content_security_policy)

        def new_publisher_signs_up():
            pg.cmd('Network.enable'); pg.cmd('Network.clearBrowserCookies')
            pg.go(APP + '/login'); pg.wait("!!document.getElementById('email')", what='login form')
            pg.type('#email', 'new.studio@example.org'); pg.click_text('Continue with email'); pg.wait_text('Check your email')
            d = json.loads((data / 'outbox' / 'latest.json').read_text())
            pg.type('input[aria-label="Digit 1"]', re.search(r'enter this code: (\d{6})', d['text']).group(1))
            pg.wait("location.pathname==='/welcome'", what='onboarding'); pg.wait_text("Let's set you up")
            pg.type('#name', 'Nora Studio'); pg.click_text('Continue'); pg.wait_text('What will you use Visuara for?')
            pg.type('#org', 'Nora Films')
            pg.js("[...document.querySelectorAll('label')].find(l=>l.textContent.includes('A publisher or studio')).click()")
            shot(out / '00-welcome-kind.png')
            pg.click_text('Continue'); pg.wait_text('Choose your plan')
            pg.click_text('Create workspace'); pg.wait("location.pathname==='/dashboard'", what='dashboard after onboarding')
            me = api_json('/api/me'); assert me['org_kind'] == 'publisher', me['org_kind']
            pg.wait("[...document.querySelectorAll('a')].some(a=>a.getAttribute('href')==='/dashboard/inventory')", what='publisher navigation')
        step('a new person signs up as a publisher and gets publisher navigation', new_publisher_signs_up)

        def publisher_review():
            sign_in(PUBLISHER)
            pg.go(APP + '/dashboard/analyses'); pg.wait_text('Living room, take 1'); pg.wait_text('Living room, take 2')
            pg.wait("[...document.querySelectorAll('a')].filter(a=>a.textContent.trim()==='Open').length===2", what='an Open link per analysed video')
            shot(out / '01-analyses.png')
            click_link('/dashboard/analyses/'); pg.wait("location.pathname.startsWith('/dashboard/analyses/')", what='analysis page')
            pg.wait_text('Stages'); pg.wait_text('Placement opportunities')
            t = pg.text()
            assert 'SCORING_OPPORTUNITIES' in t or 'Scoring opportunities' in t or 'scoring' in t.lower(), 'stage list missing'
            # M14: the detection model and its speed are shown, and each opportunity explains its score components
            assert 'yolox-tiny@' in t and 'ms a frame' in t, 'detection stage detail missing'
            assert 'Score components' in t, 'score components missing'
            shot(out / '02-analysis-detail.png')
            pg.go(APP + '/dashboard/inventory'); pg.wait_text('Rights records'); shot(out / '03-inventory.png')
            items = api_json('/api/inventory'); assert any(i['status'] == 'RESERVED' for i in items), [i['status'] for i in items]
        step('publisher: analyses, analysis detail and inventory', publisher_review)

        def publisher_approves():
            pg.go(APP + '/dashboard/requests'); pg.wait_text('Placement requests'); pg.wait_text('Approve')
            shot(out / '04-request-to-approve.png')
            pg.click_text('Approve'); pg.wait_text('Start production', timeout=30)
            reqs = api_json('/api/placement-requests'); assert reqs[0]['status'] == 'APPROVED', reqs[0]['status']
        step('publisher: approves the placement request', publisher_approves)

        def brand_marketplace():
            sign_in(BRAND)
            pg.go(APP + '/dashboard/marketplace'); pg.wait_text('Marketplace'); shot(out / '05-marketplace.png')
            pg.go(APP + '/dashboard/campaigns'); pg.wait_text('Back to work'); shot(out / '06-campaigns.png')
            click_link('/dashboard/campaigns/', 'Back to work'); pg.wait("location.pathname.startsWith('/dashboard/campaigns/')", what='campaign page')
            pg.wait_text('Recommended scenes'); pg.wait_text('Report'); shot(out / '07-campaign.png')
        step('brand: marketplace, campaigns and the campaign page', brand_marketplace)

        def brand_accepts_quote():
            pg.go(APP + '/dashboard/requests'); pg.wait_text('Accept quote'); pg.click_text('Accept quote')
            pg.wait("!document.body.innerText.includes('Accept quote')", what='quote accepted', timeout=30)
            pg.go(APP + '/dashboard/finance'); pg.wait_text('Invoices'); pg.wait_text('VIS-'); shot(out / '08-brand-invoice.png')
        step('brand: accepts the quote and sees the invoice', brand_accepts_quote)

        def brand_pays_in_test_mode():
            pg.go(APP + '/dashboard/finance'); pg.wait_text('test mode'); pg.wait_text('Pay (test)')
            pg.click_text('Pay (test)'); pg.wait("location.pathname.startsWith('/dashboard/finance/pay/')", what='test checkout', timeout=30)
            pg.wait_text('no real money moves'); shot(out / '09-test-checkout.png')
            pg.click_text('Pay (test)'); pg.wait_text('Provider result: applied', timeout=30)
            pg.go(APP + '/dashboard/finance'); pg.wait("!document.body.innerText.includes('Pay (test)')", what='invoice paid', timeout=30)
            sign_in(ADMIN)
            pg.go(APP + '/dashboard/admin'); pg.wait_text('Trial balance'); shot(out / '09-admin.png')
            tb = api_json('/api/admin/ledger/trial-balance')
            assert tb.get('balanced') is True, tb
            events = api_json('/api/admin/payments/events')
            assert [e['result'] for e in events] == ['APPLIED'], events
        step('brand: pays the invoice in test mode; webhook applied once; the ledger balances', brand_pays_in_test_mode)

        def publisher_produces():
            sign_in(PUBLISHER)
            pg.go(APP + '/dashboard/requests'); pg.wait_text('Start production'); pg.click_text('Start production')
            pg.wait("location.pathname.startsWith('/dashboard/placements/')", what='placement page', timeout=60)
            pg.wait_text('Run quality check', timeout=60); pg.click_text('Run quality check'); pg.wait_text('frames validated', timeout=240)
            pg.click_text('Approve'); pg.wait_text('Approved for version 1')
            pg.click_text('Start render'); pg.wait_text('Download video', timeout=300)
            assert 'Output validated frame by frame' in pg.text()
            shot(out / '10-placement-rendered.png')
            for _ in range(60):                                     # the delivery record is written when the render succeeds
                if api_json('/api/placement-requests')[0]['status'] == 'DELIVERED': break
                time.sleep(1)
            else: raise AssertionError('request never reached DELIVERED')
            pg.go(APP + '/dashboard/finance'); pg.wait_text('Earnings'); shot(out / '11-publisher-finance.png')
        step('publisher: starts production, quality check, approval, render, delivery', publisher_produces)

        def brand_accepts_delivery():
            sign_in(BRAND)
            pg.go(APP + '/dashboard/requests'); pg.wait_text('Accept delivery'); pg.click_text('Accept delivery')
            pg.wait("!document.body.innerText.includes('Accept delivery')", what='delivery accepted', timeout=30)
            r = api_json('/api/placement-requests')[0]; assert r['status'] == 'ACCEPTED', r['status']
            shot(out / '12-completed.png')
            pg.go(APP + '/dashboard/organizations'); pg.wait_text('New organization'); pg.wait_text('Marketplace profile')
            shot(out / '13-organizations.png')
        step('brand: accepts the delivery; organizations page', brand_accepts_delivery)

        def publisher_deletes_videos():
            sign_in(PUBLISHER)
            items = api_json('/api/inventory')
            sold = next(i['video_id'] for i in items if i['status'] == 'SOLD')
            videos = {v['id']: v['title'] for v in api_json('/api/videos')}
            other = next(vid for vid in videos if vid != sold)
            pg.go(APP + '/dashboard/library?tab=videos'); pg.wait_text(videos[sold])
            pg.js('window.confirm = () => true')                     # the browser's own dialog, accepted as a person would
            pg.js(f"document.querySelector('button[aria-label={json.dumps('Delete ' + videos[sold])}]').click()")
            pg.wait_text('cannot be deleted', timeout=30)            # sold inventory keeps its video
            pg.js(f"document.querySelector('button[aria-label={json.dumps('Delete ' + videos[other])}]').click()")
            pg.wait(f"!document.body.innerText.includes({json.dumps(videos[other])})", what='deleted video gone', timeout=30)
            assert other not in {v['id'] for v in api_json('/api/videos')} and sold in {v['id'] for v in api_json('/api/videos')}
            shot(out / '13b-library-after-delete.png')
        step('publisher: a sold video cannot be deleted; another one can', publisher_deletes_videos)

        def mobile():
            sign_in(BRAND)
            pg.viewport(390, 844, mobile=True)
            for path, needle in (('/dashboard/requests', 'Placement requests'), ('/dashboard/campaigns', 'Back to work'), ('/dashboard/finance', 'Invoices')):
                pg.go(APP + path); pg.wait_text(needle)
                assert pg.js('document.documentElement.scrollWidth<=window.innerWidth+1'), f'{path} scrolls sideways at 390 px'
            shot(out / '14-mobile-finance.png'); pg.viewport(1440, 900)
        step('mobile layout (390 px) of the marketplace pages', mobile)

        errors = [e for e in pg.errors if 'favicon' not in e]
        log.append({'step': 'no page errors', 'ok': not errors, 'errors': errors[:10]})
        print('PASS' if not errors else 'FAIL', 'no page errors', errors[:5])
    finally:
        (out / 'browser_qa_marketplace.json').write_text(json.dumps(log, indent=2))
        for p in (browser, web, api):
            try:
                if os.name == 'nt': subprocess.run(['taskkill', '/T', '/F', '/PID', str(p.pid)], capture_output=True)
                else: p.kill()
            except Exception: pass
    if not all(s['ok'] for s in log): sys.exit(1)
    print(f'{sum(s["ok"] for s in log)}/{len(log)} passed; screenshots in {out}')


if __name__ == '__main__':
    main()
