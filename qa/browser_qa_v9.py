"""Browser QA for V9: landing page, passwordless sign-up (email code and magic link), onboarding, invitations, Google redirect.

Usage: python qa/browser_qa_v9.py [--out docs/qa/v9]
Email goes to the server's outbox (CC_MAIL_BACKEND=outbox); Google is checked up to the redirect only (no real Google account).
"""
import argparse, json, os, re, subprocess, sys, tempfile, time, urllib.parse
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'qa'))
from browser_qa import Page, CHROMES, free_port            # noqa: E402


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out', default=str(ROOT / 'docs' / 'qa' / 'v9')); args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    chrome = next(c for c in CHROMES if Path(c).exists())
    tmp = Path(tempfile.mkdtemp(prefix='cc-qa9-')); port, dport = free_port(), free_port()
    base = f'http://127.0.0.1:{port}'
    env = dict(os.environ, CC_DATA_DIR=str(tmp / 'data'), CC_INLINE_WORKER='1', PYTHONPATH=str(ROOT), CC_DOTENV='0', CC_MAIL_BACKEND='outbox',
               CC_PUBLIC_URL=base, GOOGLE_CLIENT_ID='qa-client.apps.googleusercontent.com', GOOGLE_CLIENT_SECRET='qa-secret')
    for k in ('DATABASE_URL', 'CC_DATABASE_URL', 'CC_S3_BUCKET', 'CC_SMTP_HOST'): env.pop(k, None)
    api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--port', str(port)], cwd=ROOT, env=env, stdout=open(tmp / 'api.log', 'wb'), stderr=subprocess.STDOUT)
    browser = subprocess.Popen([chrome, '--headless=new', f'--remote-debugging-port={dport}', f'--user-data-dir={tmp / "p"}', '--no-first-run', 'about:blank'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log = []

    def step(name, fn):
        t = time.time()
        try: fn(); log.append({'step': name, 'ok': True, 'seconds': round(time.time() - t, 1)}); print('PASS', name, flush=True)
        except Exception as e:
            ctx = ''
            try: ctx = pg.js("(document.getElementById('toast')||{}).textContent+' | '+document.body.innerText.slice(0,300)")
            except Exception: pass
            log.append({'step': name, 'ok': False, 'error': str(e)[:400], 'page': ctx}); print('FAIL', name, e, '| PAGE:', ctx, flush=True)
            print('API LOG TAIL:', (tmp / 'api.log').read_text(errors='replace')[-1500:], flush=True); raise

    def mail():
        d = json.loads((tmp / 'data' / 'outbox' / 'latest.json').read_text())
        return d, re.search(r'code is (\d{6})', d['text']).group(1), re.search(r'(/auth/email/[A-Za-z0-9_-]+)', d['text']).group(1)

    def no_hscroll(): return pg.js('document.documentElement.scrollWidth<=window.innerWidth+1')

    def open_app(q=''):
        pg.cmd('Page.navigate', url=base + '/app' + q)
        pg.wait("document.readyState==='complete' && typeof loadProviders==='function' && document.getElementById('auth').style.display==='grid'")
        pg.wait("!document.getElementById('emailStartForm').classList.contains('hide')", what='providers loaded')

    def email_code(email):
        pg.js(f"document.getElementById('pwlEmail').value={json.dumps(email)}")
        pg.click("document.getElementById('emailStartBtn')")
        pg.wait("!document.getElementById('codeForm').classList.contains('hide')", what='code form')
        _, code, _ = mail()
        pg.js(f"document.getElementById('pwlCode').value='{code}'"); pg.click("document.getElementById('codeBtn')")

    def onboard(name, role=None, org=None):
        pg.wait("!document.getElementById('welcome').classList.contains('hide')", what='onboarding')
        pg.js(f"document.getElementById('wName').value={json.dumps(name)}")
        if role: pg.js(f"document.querySelector('#welcomeForm [name=role][value={role}]').checked=true")
        if org: pg.js(f"document.getElementById('wOrg').value={json.dumps(org)}")

    def submit_onboarding():
        pg.click("document.getElementById('welcomeBtn')")
        pg.wait("document.getElementById('shell').style.display==='block'", what='workspace')

    def logout():
        pg.js('logout()'); pg.wait("document.getElementById('auth').style.display==='grid'")

    try:
        for _ in range(100):
            try: httpx.get(base + '/api/health', timeout=1); targets = httpx.get(f'http://127.0.0.1:{dport}/json', timeout=1).json(); break
            except Exception: time.sleep(.2)
        pg = Page(next(t for t in targets if t['type'] == 'page')['webSocketDebuggerUrl']); pg.viewport(1366, 900)

        def landing():
            pg.cmd('Page.navigate', url=base + '/'); pg.wait("document.readyState==='complete' && !!document.querySelector('.hero h1')")
            assert 'Join as a brand' in pg.js('document.body.innerText') and 'Join as a creator' in pg.js('document.body.innerText')
            assert pg.js("document.querySelectorAll('a[href=\"/app?join=brand\"]').length") >= 2
            pg.shot(out / '01-landing.png')
            pg.viewport(390, 844, True); pg.cmd('Page.reload'); pg.wait("document.readyState==='complete' && !!document.querySelector('.hero h1')"); time.sleep(.4)
            assert no_hscroll(), 'landing scrolls horizontally at 390 px'
            pg.shot(out / '02-landing-mobile.png'); pg.viewport(1366, 900)
        step('Landing page renders with brand and creator calls to action; fits 390 px', landing)

        def join_screen():
            pg.cmd('Page.navigate', url=base + '/'); pg.wait("document.readyState==='complete' && !!document.querySelector('.hero h1')")
            pg.click("document.querySelector('.hero a[href=\"/app?join=brand\"]')")
            pg.wait("location.pathname==='/app' && typeof loadProviders==='function'")
            pg.wait("!document.getElementById('googleBtn').classList.contains('hide') && !document.getElementById('emailStartForm').classList.contains('hide')")
            assert pg.js("document.getElementById('authTitle').textContent") == 'Join as a brand'
            pg.shot(out / '03-join-sign-in.png')
            r = httpx.get(base + '/auth/google/start', follow_redirects=False)
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.headers['location']).query))
            assert r.status_code == 303 and r.headers['location'].startswith('https://accounts.google.com/o/oauth2/v2/auth')
            assert q['redirect_uri'] == base + '/auth/google/callback' and q['code_challenge_method'] == 'S256' and 'cc_oauth' in r.headers.get('set-cookie', '')
        step('Join-as-brand opens sign-in with Google and email options; Google redirect carries PKCE and state', join_screen)

        def brand_by_code():
            email_code('bea@brand.test')
            onboard('Bea Brand', org='Bea Home Goods')
            assert pg.js("document.querySelector('#welcomeForm [name=role][value=brand]').checked"), 'role not preselected from the landing CTA'
            pg.shot(out / '04-onboarding.png'); submit_onboarding()
            assert pg.js('user.role') == 'brand' and pg.js("location.pathname+location.search") == '/app'
            pg.shot(out / '05-brand-workspace.png')
        step('Brand signs up with an emailed one-time code and onboarding (no password)', brand_by_code)

        def invite_and_join():
            pg.js("api('/api/invitations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:'ivy@brand.test',role:'creator'})}).then(()=>window._inv=1)")
            pg.wait('window._inv===1'); logout()
            pg.js("history.replaceState(null,'','/app')"); open_app()
            email_code('ivy@brand.test'); pg.wait("!!document.querySelector('#welcomeForm [name=invite]')", what='invitation option')
            assert 'Bea Home Goods' in pg.js("document.getElementById('wInvites').textContent")
            onboard('Ivy'); pg.shot(out / '06-onboarding-invitation.png'); submit_onboarding()
            assert pg.js('user.role') == 'creator'
            assert pg.js("api('/api/me').then(u=>u.org_id)") == pg.js('user.org_id')
            logout()
        step('Invited teammate signs in by email code and joins the inviting workspace', invite_and_join)

        def creator_by_link():
            open_app('?join=creator')
            pg.js("document.getElementById('pwlEmail').value='cara@studio.test'"); pg.click("document.getElementById('emailStartBtn')")
            pg.wait("!document.getElementById('codeForm').classList.contains('hide')")
            _, _, link = mail()
            pg.cmd('Page.navigate', url=base + link); pg.wait("document.readyState==='complete' && !!document.querySelector('form button')")
            assert 'cara@studio.test' in pg.js('document.body.innerText')
            pg.shot(out / '07-magic-link-confirm.png')
            pg.click("document.querySelector('form button')")
            pg.wait("location.pathname==='/app' && typeof loadProviders==='function'")
            onboard('Cara Creator', org='Cara Studio')
            assert pg.js("document.querySelector('#welcomeForm [name=role][value=creator]').checked"), 'creator role not preselected'
            pg.viewport(390, 844, True); time.sleep(.4); assert no_hscroll(), 'onboarding scrolls horizontally at 390 px'
            pg.shot(out / '08-onboarding-mobile.png'); pg.viewport(1366, 900)
            submit_onboarding(); assert pg.js('user.role') == 'creator'
            assert httpx.get(base + link.replace('/auth/email/', '/auth/email/'), timeout=10).status_code == 410   # single use
        step('Creator signs up through the magic link (explicit confirmation page, single use)', creator_by_link)

        def returning_user():
            logout(); open_app()
            email_code('bea@brand.test'); pg.wait("document.getElementById('shell').style.display==='block'", what='straight to workspace')
            assert pg.js('user.email') == 'bea@brand.test'
        step('Returning user signs in by email code straight into the workspace', returning_user)
        if pg.errors: raise AssertionError('Console errors: ' + '; '.join(pg.errors[:5]))
        log.append({'step': 'No console errors or uncaught exceptions', 'ok': True})
    finally:
        (out / 'browser-qa-v9-log.json').write_text(json.dumps({'steps': log}, indent=1)); browser.kill(); api.kill()
    print('ALL V9 BROWSER QA STEPS PASSED')


if __name__ == '__main__':
    main()
