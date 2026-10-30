"""Browser QA for V10 against the production-shaped Docker stack, with the frontend on its own origin (Vercel layout).

    docker compose -f docker-compose.yml -f deploy/local/compose.split-origin.yml up -d --build
    CC_API_ORIGIN=http://api.cc.localhost:8000 node frontend/build.mjs
    python qa/browser_qa_v10.py [--out docs/qa/v10]

Frontend: http://app.cc.localhost:3000 (qa/vercel_emulator.py serving the Vercel build output).
API:      http://api.cc.localhost:8000 (FastAPI container; PostgreSQL 16, S3 gateway, Mailpit SMTP, two workers).
Covers: landing; brand sign-up by emailed code through real SMTP (Mailpit) with cross-origin cookies, CORS and CSRF;
team invitation by email and acceptance by magic link (API page -> redirect back to the app origin); read-only
viewer refused on writes; owner suspends the viewer; creator sign-up and a direct-to-storage video upload from the
browser (presigned PUT, progress) followed by server verification; keyframe thumbnails served through presigned
redirects; the Google button pointing at the API's OAuth start with the right redirect URI; mobile layout.
"""
import argparse, json, os, re, subprocess, sys, tempfile, time, urllib.parse
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'qa'))
from browser_qa import Page, CHROMES, free_port            # noqa: E402

APP, API, MAILPIT = 'http://app.cc.localhost:3000', 'http://api.cc.localhost:8000', 'http://127.0.0.1:8025'


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out', default=str(ROOT / 'docs' / 'qa' / 'v10')); args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    chrome = next(c for c in CHROMES if Path(c).exists())
    tmp = Path(tempfile.mkdtemp(prefix='cc-qa10-')); dport = free_port(); tag = str(int(time.time()))[-6:]
    emu = subprocess.Popen([sys.executable, str(ROOT / 'qa' / 'vercel_emulator.py'), '--port', '3000'], cwd=ROOT)
    browser = subprocess.Popen([chrome, '--headless=new', f'--remote-debugging-port={dport}', f'--user-data-dir={tmp / "p"}', '--no-first-run', 'about:blank'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log, console = [], []

    def step(name, fn):
        t = time.time()
        try: fn(); log.append({'step': name, 'ok': True, 'seconds': round(time.time() - t, 1)}); print('PASS', name, flush=True)
        except Exception as e:
            ctx = ''
            try: ctx = pg.js("(document.getElementById('toast')||{}).textContent+' | '+document.body.innerText.slice(0,300)")
            except Exception: pass
            log.append({'step': name, 'ok': False, 'error': str(e)[:400], 'page': ctx}); print('FAIL', name, e, '| PAGE:', ctx, flush=True)
            raise

    def mail_for(addr, after):
        for _ in range(60):
            msgs = httpx.get(f'{MAILPIT}/api/v1/search', params={'query': f'to:{addr}'}, timeout=5).json().get('messages', [])
            msgs = [m for m in msgs if m['Created'] > after]
            if msgs:
                full = httpx.get(f'{MAILPIT}/api/v1/message/{msgs[0]["ID"]}', timeout=5).json()
                return full['Subject'], full['Text']
            time.sleep(.5)
        raise AssertionError(f'no email for {addr}')

    def now_iso(): return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(time.time() - 2))

    def open_app(q=''):
        pg.cmd('Page.navigate', url=APP + '/app' + q)
        pg.wait("document.readyState==='complete' && typeof loadProviders==='function' && document.getElementById('auth').style.display==='grid'")
        pg.wait("!document.getElementById('emailStartForm').classList.contains('hide')", what='email sign-in offered')

    def email_code(email):
        t = now_iso()
        pg.js(f"document.getElementById('pwlEmail').value={json.dumps(email)}"); pg.click("document.getElementById('emailStartBtn')")
        pg.wait("!document.getElementById('codeForm').classList.contains('hide')", what='code form')
        _, text = mail_for(email, t)
        code = re.search(r'code is (\d{6})', text).group(1)
        pg.js(f"document.getElementById('pwlCode').value='{code}'"); pg.click("document.getElementById('codeBtn')")
        return text

    def onboard(name, role=None, org=None, invite=False):
        pg.wait("!document.getElementById('welcome').classList.contains('hide')", what='onboarding')
        pg.js(f"document.getElementById('wName').value={json.dumps(name)}")
        if not invite:
            pg.js("(document.querySelector('#welcomeForm [name=invite][value=\"\"]')||{}).checked=true; typeof toggleInvite==='function'&&toggleInvite()")
            if role: pg.js(f"document.querySelector('#welcomeForm [name=role][value={role}]').checked=true")
            if org: pg.js(f"document.getElementById('wOrg').value={json.dumps(org)}")
        pg.click("document.getElementById('welcomeBtn')")
        pg.wait("document.getElementById('shell').style.display==='block'", what='workspace')

    def go(page_name): pg.js(f"document.querySelector('.nav[data-page={page_name}]').click()"); time.sleep(.6)

    def logout(): pg.js('logout()'); pg.wait("document.getElementById('auth').style.display==='grid'")

    try:
        for _ in range(100):
            try:
                httpx.get('http://127.0.0.1:3000/', timeout=1); targets = httpx.get(f'http://127.0.0.1:{dport}/json', timeout=1).json(); break
            except Exception: time.sleep(.2)
        pg = Page(next(t for t in targets if t['type'] == 'page')['webSocketDebuggerUrl']); pg.viewport(1366, 900)
        pg.cmd('Runtime.enable'); pg.cmd('Log.enable')
        brand, viewer, creator = f'brand{tag}@qa.test', f'viewer{tag}@qa.test', f'creator{tag}@qa.test'

        def landing():
            pg.cmd('Page.navigate', url=APP + '/'); pg.wait("document.readyState==='complete' && !!document.querySelector('.hero h1')")
            assert 'Join as a brand' in pg.js('document.body.innerText')
            h = httpx.get('http://127.0.0.1:3000/', headers={'Host': 'app.cc.localhost:3000'}).headers
            assert "frame-ancestors 'none'" in h['content-security-policy'] and h['x-frame-options'] == 'DENY'
            assert json.dumps(API) in httpx.get('http://127.0.0.1:3000/config.js').text
            pg.shot(out / '01-landing-app-origin.png')
        step('landing page served from the app origin with security headers and API origin config', landing)

        def brand_signup():
            open_app('?join=brand')
            assert pg.js("document.getElementById('googleBtn').href") == API + '/auth/google/start'
            email_code(brand); onboard('Brand Owner', 'brand', f'QA Brand {tag}')
            me = pg.js("JSON.stringify(user)"); assert '"member_role":"owner"' in me, me
            assert pg.js("document.cookie.includes('cc_session')") is False      # session cookie lives on the API origin, HttpOnly
            pg.shot(out / '02-brand-workspace-cross-origin.png')
        step('brand signs up by emailed code (real SMTP via Mailpit); session on the API origin, CORS + CSRF', brand_signup)

        def invite_viewer():
            go('settings'); pg.wait("!!document.getElementById('inviteEmail') && !!document.querySelector('#teamList table')", what='team list')
            pg.js(f"document.getElementById('inviteEmail').value={json.dumps(viewer)}; document.getElementById('inviteMemberRole').value='viewer'")
            t = now_iso(); pg.js('createInvite()')
            pg.wait("document.getElementById('inviteResult').textContent.includes('emailed')", what='invitation emailed')
            subject, text = mail_for(viewer, t); assert 'invited you' in subject and APP + '/app' in text, (subject, text)
            pg.wait("document.getElementById('teamList').innerText.includes('pending')", what='pending invitation listed')
            pg.shot(out / '03-team-invitation.png')
        step('owner invites a read-only viewer; invitation email delivered over SMTP', invite_viewer)

        def viewer_magic_link():
            logout(); open_app()
            t = now_iso()
            pg.js(f"document.getElementById('pwlEmail').value={json.dumps(viewer)}"); pg.click("document.getElementById('emailStartBtn')")
            _, text = mail_for(viewer, t)
            link = re.search(r'(http\S+/auth/email/[A-Za-z0-9_-]+)', text).group(1)
            assert link.startswith(API + '/auth/email/'), link
            pg.cmd('Page.navigate', url=link); pg.wait("!!document.querySelector('form button')", what='confirmation page on API origin')
            pg.click("document.querySelector('form button')")
            pg.wait(f"location.origin==={json.dumps(APP)} && !document.getElementById('welcome').classList.contains('hide')", what='redirect back to app origin + onboarding')
            assert 'as viewer' in pg.js("document.getElementById('wInvites').innerText")
            onboard('Viewer Person', invite=True)
            assert '"member_role":"viewer"' in pg.js('JSON.stringify(user)')
            pg.shot(out / '04-viewer-joined-by-magic-link.png')
        step('viewer signs in by magic link (API page) and is redirected to the app origin to accept the invitation', viewer_magic_link)

        def viewer_read_only():
            r = pg.js("api('/api/campaigns',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'x',category:'beverage'})}).then(()=>'allowed',e=>e.message)", )
            time.sleep(1)
            msg = pg.js("api('/api/campaigns',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'x',category:'beverage'})}).then(()=>'allowed').catch(e=>e.message)")
            assert 'viewer' in str(msg) and 'cannot' in str(msg), msg
            go('settings'); pg.wait("!!document.querySelector('#teamList table')")
            assert 'Suspend' not in pg.js("document.getElementById('teamList').innerText")   # no team controls for viewers
        step('viewer is refused on writes by the server (role check), no team controls shown', viewer_read_only)

        def owner_suspends_viewer():
            logout(); open_app(); email_code(brand)
            pg.wait("document.getElementById('shell').style.display==='block'", what='owner back in')
            go('settings'); pg.wait("document.getElementById('teamList').innerText.includes('Viewer Person')")
            pg.js("window.confirm=()=>true")
            uid = pg.js("api('/api/members').then(t=>t.members.find(m=>m.member_role==='viewer').id)")
            time.sleep(.5); uid = pg.js("api('/api/members').then(t=>t.members.find(m=>m.member_role==='viewer').id)")
            pg.js(f"memberAction({int(uid)},'suspend')")
            pg.wait("document.getElementById('teamList').innerText.includes('Suspended')", what='suspended badge')
            pg.shot(out / '05-viewer-suspended.png')
        step('owner suspends the viewer from the team panel', owner_suspends_viewer)

        def creator_direct_upload():
            logout(); open_app('?join=creator'); email_code(creator); onboard('Creator Person', 'creator', f'QA Creator {tag}')
            from benchmark.fixtures import generate
            meta = generate('static', 77, tmp)
            go('videos'); pg.js('openVideo()'); pg.wait("!!document.getElementById('videoForm')")
            pg.js("document.querySelector('#videoForm [name=title]').value='QA direct upload'; document.getElementById('licenseConfirm').checked=true")
            pg.set_files('#videoForm input[type=file]', meta['path'])
            pg.js("document.getElementById('videoForm').requestSubmit()")
            pg.wait("document.getElementById('upStatus') && /Uploading|Checking/.test(document.getElementById('upStatus').textContent) || document.getElementById('modalBackdrop').style.display!=='flex'", what='upload progress')
            pg.wait("document.getElementById('modalBackdrop').style.display!=='flex'", timeout=120, what='upload completed')
            pg.wait("document.getElementById('pageContent').innerText.includes('QA direct upload')", what='video listed')
            ups = pg.js("api('/api/uploads').then(u=>JSON.stringify(u[0]))"); time.sleep(.5); ups = json.loads(pg.js("api('/api/uploads').then(u=>JSON.stringify(u[0]))"))
            assert ups['status'] == 'COMPLETED', ups
            pg.shot(out / '06-creator-direct-upload.png')
        step('creator uploads a video straight to storage from the browser (presigned PUT), server verifies it', creator_direct_upload)

        def thumbnails_via_presigned():
            vid = int(pg.js("cache.videos[0].id"))
            pg.js(f'analyzeVideo({vid})')
            pg.wait("document.querySelectorAll('.scene img').length>0", timeout=120, what='scene thumbnails')
            pg.wait("[...document.querySelectorAll('.scene img')].every(i=>i.complete&&i.naturalWidth>0)", timeout=30, what='thumbnails loaded through presigned redirects')
            src = pg.js("document.querySelector('.scene img').src"); assert src.startswith(API + '/api/videos/'), src
            pg.shot(out / '07-scene-thumbnails.png')
        step('scene analysis; keyframe thumbnails load through access-checked presigned redirects', thumbnails_via_presigned)

        def google_redirect():
            r = httpx.get('http://127.0.0.1:8000/auth/google/start', headers={'Host': 'api.cc.localhost:8000'}, follow_redirects=False)
            assert r.status_code == 303
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers['location']).query)
            assert q['redirect_uri'] == [API + '/auth/google/callback'] and q['code_challenge_method'] == ['S256'] and q['state'] and q['nonce']
        step('Google sign-in starts on the API origin with PKCE, state, nonce and the API callback URI', google_redirect)

        def mobile():
            pg.viewport(390, 844, mobile=True); pg.cmd('Page.navigate', url=APP + '/')
            pg.wait("document.readyState==='complete'"); assert pg.js('document.documentElement.scrollWidth<=window.innerWidth+1')
            pg.shot(out / '08-landing-mobile.png'); pg.viewport(1366, 900)
        step('mobile landing (390 px) without horizontal scroll', mobile)
    finally:
        (out / 'qa_v10_log.json').write_text(json.dumps({'steps': log, 'app_origin': APP, 'api_origin': API}, indent=1))
        browser.kill(); emu.kill()
    print(f"{sum(s['ok'] for s in log)}/{len(log)} steps passed")


if __name__ == '__main__':
    main()
