"""Browser QA: drives the real UI in headless Chrome over the DevTools protocol against a live server.

Usage: python qa/browser_qa.py [--chrome PATH] [--out docs/qa]
Starts uvicorn (inline worker) on a temporary data directory, then walks the brand and creator workflow
through the UI: register, invite, upload, analyse, create a tracked placement in the corner editor (real
pointer drag and keyboard nudge), quality report, fit review, propose, consent, approve, render, download.
Writes screenshots and a JSON log; exits non-zero on any failed step or page error.
"""
import argparse, base64, itertools, json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path
import httpx
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from benchmark.fixtures import generate, product_image   # noqa: E402

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
            if self.js(expr): return True
            time.sleep(.2)
        raise AssertionError(f'Timed out waiting for {what or expr}')

    def click(self, selector_js):
        self.js(f'({selector_js}).click()'); time.sleep(.3)

    def click_text(self, text, scope='document'):
        ok = self.js(f"(()=>{{let b=[...{scope}.querySelectorAll('button,a')].find(e=>e.textContent.trim()==={json.dumps(text)}&&e.offsetParent);if(!b)return false;b.click();return true}})()")
        if not ok: raise AssertionError(f'No visible button {text!r}')
        time.sleep(.4)

    def set_files(self, selector, path):
        root = self.cmd('DOM.getDocument', depth=-1)['root']['nodeId']
        node = self.cmd('DOM.querySelector', nodeId=root, selector=selector)['nodeId']
        self.cmd('DOM.setFileInputFiles', nodeId=node, files=[str(path)])

    def shot(self, path):
        Path(path).write_bytes(base64.b64decode(self.cmd('Page.captureScreenshot', format='png')['data']))

    def viewport(self, w, h, mobile=False):
        self.cmd('Emulation.setDeviceMetricsOverride', width=w, height=h, deviceScaleFactor=1, mobile=mobile)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--chrome'); ap.add_argument('--out', default=str(ROOT / 'docs' / 'qa'))
    args = ap.parse_args()
    chrome = args.chrome or next((c for c in CHROMES if Path(c).exists()), None)
    if not chrome: raise SystemExit('Chrome/Edge not found')
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='cc-qa-'))
    port, dport = free_port(), free_port()
    env = dict(os.environ, CC_DATA_DIR=str(tmp / 'data'), CC_INLINE_WORKER='1', PYTHONPATH=str(ROOT))
    api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--port', str(port)], cwd=ROOT, env=env,
                           stdout=open(tmp / 'api.log', 'wb'), stderr=subprocess.STDOUT)
    browser = subprocess.Popen([chrome, '--headless=new', f'--remote-debugging-port={dport}', f'--user-data-dir={tmp / "profile"}',
                                '--no-first-run', '--autoplay-policy=no-user-gesture-required', 'about:blank'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}'; log = []

    def step(name, fn):
        t = time.time()
        try:
            fn(); log.append({'step': name, 'ok': True, 'seconds': round(time.time() - t, 2)}); print('PASS', name, flush=True)
        except Exception as e:
            log.append({'step': name, 'ok': False, 'error': str(e)[:500]}); print('FAIL', name, e, flush=True); raise
    try:
        for _ in range(100):
            try:
                httpx.get(base + '/api/health', timeout=1); targets = httpx.get(f'http://127.0.0.1:{dport}/json', timeout=1).json(); break
            except Exception: time.sleep(.2)
        page = Page(next(t for t in targets if t['type'] == 'page')['webSocketDebuggerUrl'])
        page.viewport(1366, 900)
        clip = generate('pan_zoom', 21, tmp)
        product = product_image(tmp / 'bottle.png')

        def fill_auth(mode, **f):
            page.click_text('Create account' if mode == 'register' else 'Sign in', "document.querySelector('.tabs')")
            for k, v in f.items():
                page.js(f"document.querySelector('#authForm [name={k}]').value={json.dumps(v)}")
            page.click("document.getElementById('authSubmit')")

        def logout():
            page.js('logout()'); page.wait("document.getElementById('auth').style.display==='grid'", what='sign-in screen')

        def register_brand():
            page.cmd('Page.navigate', url=base + '/app'); page.wait("document.readyState==='complete' && !!document.getElementById('authForm')")
            page.shot(out / '01-sign-in.png')
            fill_auth('register', name='Brand QA', email='brand@qa.test', password='strong-password-123', role='brand', organization='QA Brand')
            page.wait("document.getElementById('shell').style.display==='block'", what='workspace')
        step('Brand registers through the UI', register_brand)

        token = {}
        def invite():
            page.js("navigate('settings')"); page.wait("!!document.getElementById('inviteEmail')")
            page.js("document.getElementById('inviteEmail').value='creator@qa.test'; document.getElementById('inviteRole').value='creator'")   # V10 defaults to the inviter's own type
            page.click_text('Send invitation')   # V10 label (was 'Create invitation token')
            page.wait("document.getElementById('inviteResult').textContent.includes(':')", what='invitation token')
            token['t'] = page.js("document.getElementById('inviteResult').textContent.split(': ')[1].split(' ')[0]")
        step('Brand creates a creator invitation', invite)

        def creator_upload():
            logout()
            fill_auth('register', name='Creator QA', email='creator@qa.test', password='strong-password-123', role='creator', organization='', invitation_token=token['t'])
            page.wait("document.getElementById('shell').style.display==='block' || document.getElementById('toast').style.display==='block'")
            if page.js("document.getElementById('shell').style.display!=='block'"):
                raise AssertionError('Registration refused: ' + page.js("document.getElementById('toast').textContent"))
            assert page.js('user.role') == 'creator'
            page.js("navigate('videos')"); time.sleep(.5); page.js('openVideo()')
            page.js("document.querySelector('#videoForm [name=title]').value='Desk pan (QA)'")
            page.set_files('#videoForm [name=video]', clip['path'])
            page.js("document.getElementById('licenseConfirm').checked=true")
            page.js("document.getElementById('videoForm').requestSubmit()")
            page.wait("cache.videos.length===1", what='video upload')
            page.click_text('Analyze scenes')
            page.wait("document.getElementById('modalTitle').textContent==='Scene analysis' && document.getElementById('modalBackdrop').style.display==='flex'", what='scene analysis')
            page.shot(out / '02-scene-analysis.png'); page.js('closeModal()')
        step('Creator registers with the invitation, uploads and analyses a video', creator_upload)

        def brand_product():
            logout(); fill_auth('login', email='brand@qa.test', password='strong-password-123')
            page.wait("document.getElementById('shell').style.display==='block'")
            page.js("navigate('products')"); time.sleep(.5); page.js('openProduct()')
            page.js("document.querySelector('#productForm [name=name]').value='QA bottle'")
            page.set_files('#productForm [name=image]', product)
            page.js("document.getElementById('productForm').requestSubmit()")
            page.wait("cache.products.length===1", what='product upload')
        step('Brand adds a product image', brand_product)

        def editor():
            page.js("navigate('placements')"); time.sleep(.5); page.click_text('＋ New placement')
            page.wait("!!document.getElementById('edVideo') && document.getElementById('edVideo').readyState>=1", what='video metadata in editor')
            page.js(f"ed.quad={json.dumps(clip['quad_norm'])};ed.ref=0.4;ed.start=0.4;ed.end=2.8;syncInputs();document.getElementById('edVideo').currentTime=0.4")
            page.wait("Math.abs(document.getElementById('edVideo').currentTime-0.4)<0.01 && !document.getElementById('edPreview').classList.contains('hide')", what='product preview on surface')
            # Real pointer drag of the top-left handle by +20 px, then undo it; then a keyboard nudge.
            r = page.js("(()=>{let b=document.querySelector('.handle[data-i=\"0\"]').getBoundingClientRect();return [b.x+b.width/2,b.y+b.height/2]})()")
            before = page.js('JSON.stringify(ed.quad[0])')
            for t, x in (('mousePressed', r[0]), ('mouseMoved', r[0] + 10), ('mouseMoved', r[0] + 20), ('mouseReleased', r[0] + 20)):
                page.cmd('Input.dispatchMouseEvent', type=t, x=x, y=r[1], button='left', buttons=1 if t != 'mouseReleased' else 0, clickCount=1)
            moved = page.js('JSON.stringify(ed.quad[0])'); assert moved != before, 'drag did not move the corner'
            page.js('undoQuad()'); assert page.js('JSON.stringify(ed.quad[0])') == before, 'undo did not restore the corner'
            page.js("document.querySelector('.handle[data-i=\"1\"]').focus()")
            x1 = page.js('ed.quad[1][0]')
            page.cmd('Input.dispatchKeyEvent', type='keyDown', key='ArrowRight', code='ArrowRight', windowsVirtualKeyCode=39)
            page.cmd('Input.dispatchKeyEvent', type='keyUp', key='ArrowRight', code='ArrowRight', windowsVirtualKeyCode=39)
            assert abs(page.js('ed.quad[1][0]') - x1 - .002) < 1e-6, 'keyboard nudge failed'
            page.js('undoQuad()')
            page.shot(out / '03-corner-editor.png')
            page.js("document.getElementById('edShow').click()"); time.sleep(.2); page.shot(out / '04-corner-editor-original.png'); page.js("document.getElementById('edShow').click()")
            page.click_text('Create placement & check quality')
            page.wait("document.getElementById('modalTitle').textContent==='Quality report'", timeout=90, what='quality report')
            assert page.js("document.getElementById('modalBody').textContent.includes('PASSED')")
            page.shot(out / '05-quality-report.png'); page.js('closeModal()')
        step('Brand places the product with the corner editor; quality check passes', editor)

        def fit_and_propose():
            page.js('renderPage()'); time.sleep(.6); page.click_text('Review fit')
            page.click_text('Accept fit'); page.wait("cache.placements[0].fit_status==='ACCEPTED'", what='fit accepted')
            page.click_text('Propose'); page.wait("cache.placements[0].status==='PROPOSED'", what='proposed')
            page.shot(out / '06-placements-proposed.png')
        step('Brand accepts fit and proposes', fit_and_propose)

        def creator_consent():
            logout(); fill_auth('login', email='creator@qa.test', password='strong-password-123')
            page.wait("document.getElementById('shell').style.display==='block'")
            page.js("navigate('placements')"); time.sleep(.6); page.click_text('Review & consent')
            page.wait("!!document.getElementById('cConsent')"); page.shot(out / '07-creator-consent.png')
            page.js("document.getElementById('cConsent').checked=true"); page.click_text('Consent & approve')
            page.wait("cache.placements[0].status==='CREATOR_APPROVED'", what='creator approval')
        step('Creator reviews, consents and approves', creator_consent)

        def brand_approve_render():
            logout(); fill_auth('login', email='brand@qa.test', password='strong-password-123')
            page.wait("document.getElementById('shell').style.display==='block'")
            page.js("navigate('placements')"); time.sleep(.6); page.click_text('Approve')
            page.wait("cache.placements[0].status==='READY_FOR_RENDER'", what='READY_FOR_RENDER')
            page.click_text('Render')
            page.wait("cache.jobs.length && cache.jobs[0].status==='SUCCEEDED'", timeout=120, what='render success (auto-refresh)')
            assert page.js("cache.jobs[0].validation.status") == 'PASSED'
            page.shot(out / '08-renders-validated.png')
            status = page.js("fetch(cache.jobs[0].download_url).then(r=>r.status)"); assert status == 200, status
        step('Brand approves, renders, sees validation, downloads', brand_approve_render)

        def details():
            page.js("navigate('placements')"); time.sleep(.6); page.click_text('Details')
            page.wait("document.getElementById('modalTitle').textContent==='Placement details'")
            assert page.js("document.getElementById('modalBody').textContent.includes('DELIVERED')")
            page.shot(out / '09-audit-trail.png'); page.js('closeModal()')
        step('Audit trail shows the full lifecycle', details)

        def mobile():
            page.viewport(390, 844, True); page.js("navigate('placements')"); time.sleep(.8)
            assert page.js('document.documentElement.scrollWidth<=window.innerWidth+1'), 'horizontal page scroll at 390px'
            page.shot(out / '10-mobile-placements.png')
            page.click_text('Revise'); page.wait("!!document.getElementById('edVideo') && document.getElementById('edVideo').readyState>=1")
            assert page.js('document.documentElement.scrollWidth<=window.innerWidth+1'), 'horizontal page scroll in editor at 390px'
            page.shot(out / '11-mobile-editor.png'); page.js('closeModal()')
        step('Phone-width layout (390 px) has no horizontal scroll', mobile)

        if page.errors: raise AssertionError('Browser console errors: ' + '; '.join(page.errors[:5]))
        log.append({'step': 'No console errors or uncaught exceptions', 'ok': True})
    finally:
        (out / 'browser-qa-log.json').write_text(json.dumps({'chrome': chrome, 'steps': log}, indent=1))
        browser.kill(); api.kill()
    print('ALL BROWSER QA STEPS PASSED')


if __name__ == '__main__':
    main()
