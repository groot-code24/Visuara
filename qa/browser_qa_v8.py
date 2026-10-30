"""Browser QA for the V8 journey: cross-organization marketplace, licence, 3D placement, preview, report.

Usage: python qa/browser_qa_v8.py [--out docs/qa/v8]
"""
import argparse, json, os, subprocess, sys, tempfile, time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'qa'))
from browser_qa import Page, CHROMES, free_port            # noqa: E402
from benchmark.fixtures3d import generate3d                # noqa: E402
from benchmark.glb_assets import bottle_glb                # noqa: E402
from benchmark.fixtures import product_image               # noqa: E402

PW = 'strong-password-123'


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out', default=str(ROOT / 'docs' / 'qa' / 'v8')); args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    chrome = next(c for c in CHROMES if Path(c).exists())
    tmp = Path(tempfile.mkdtemp(prefix='cc-qa8-')); port, dport = free_port(), free_port()
    env = dict(os.environ, CC_DATA_DIR=str(tmp / 'data'), CC_INLINE_WORKER='1', PYTHONPATH=str(ROOT))
    api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--port', str(port)], cwd=ROOT, env=env, stdout=open(tmp / 'api.log', 'wb'), stderr=subprocess.STDOUT)
    browser = subprocess.Popen([chrome, '--headless=new', f'--remote-debugging-port={dport}', f'--user-data-dir={tmp / "p"}', '--no-first-run', 'about:blank'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}'; log = []

    def step(name, fn):
        t = time.time()
        try: fn(); log.append({'step': name, 'ok': True, 'seconds': round(time.time() - t, 1)}); print('PASS', name, flush=True)
        except Exception as e:
            ctx = ''
            try: ctx = pg.js("(document.getElementById('toast').textContent||'')+' | '+document.getElementById('pageContent').textContent.slice(0,300)")
            except Exception: pass
            log.append({'step': name, 'ok': False, 'error': str(e)[:400], 'page': ctx}); print('FAIL', name, e, '| PAGE:', ctx, flush=True)
            print('API LOG TAIL:', (tmp / 'api.log').read_text(errors='replace')[-1500:], flush=True); raise
    try:
        for _ in range(100):
            try: httpx.get(base + '/api/health', timeout=1); targets = httpx.get(f'http://127.0.0.1:{dport}/json', timeout=1).json(); break
            except Exception: time.sleep(.2)
        pg = Page(next(t for t in targets if t['type'] == 'page')['webSocketDebuggerUrl']); pg.viewport(1366, 900)
        glb = bottle_glb(tmp / 'bottle.glb'); meta = generate3d('dolly', 31, tmp / 'clips', glb); prod_png = product_image(tmp / 'p.png')

        def auth(mode, **f):
            pg.js("window.confirm=()=>true")
            pg.click_text('Create account' if mode == 'register' else 'Sign in', "document.querySelector('.tabs')")
            for k, v in f.items(): pg.js(f"document.querySelector('#authForm [name={k}]').value={json.dumps(v)}")
            pg.js("document.getElementById('toast').style.display='none'")   # a toast left by the previous step must not count as the answer
            pg.click("document.getElementById('authSubmit')")
            pg.wait("document.getElementById('shell').style.display==='block' || document.getElementById('toast').style.display==='block'")
            if pg.js("document.getElementById('shell').style.display!=='block'"): raise AssertionError(pg.js("document.getElementById('toast').textContent"))
            pg.js("window.confirm=()=>true")

        def logout():
            pg.js('logout()'); pg.wait("document.getElementById('auth').style.display==='grid'")

        def creator_lists():
            pg.cmd('Page.navigate', url=base + '/app'); pg.wait("document.readyState==='complete' && typeof setAuthMode==='function' && document.getElementById('auth').style.display==='grid'")
            auth('register', name='Cara Creator', email='cara@studio.test', password=PW, role='creator', organization='Cara Studio')
            pg.js("navigate('videos')"); time.sleep(.5); pg.js('openVideo()')
            pg.js("document.querySelector('#videoForm [name=title]').value='Desk vlog (QA)'")
            pg.set_files('#videoForm [name=video]', meta['path']); pg.js("document.getElementById('licenseConfirm').checked=true")
            pg.js("document.getElementById('videoForm').requestSubmit()"); pg.wait('cache.videos.length===1', what='upload')
            pg.click_text('Analyze scenes'); pg.wait("document.getElementById('modalTitle').textContent==='Scene analysis'"); pg.js('closeModal()')
            pg.js("renderPage()"); time.sleep(.6); pg.click_text('Marketplace listing'); pg.wait("!!document.getElementById('lf')")
            pg.js("document.getElementById('lc').value='home, lifestyle';document.getElementById('lp').value='750'")
            pg.shot(out / '01-creator-listing.png'); pg.js("document.getElementById('lf').requestSubmit()")
            pg.wait("document.getElementById('modalBackdrop').style.display==='none'", what='listing saved')
        step('Creator (own organization) uploads, analyses and lists a video', creator_lists)

        def brand_setup():
            logout(); auth('register', name='Bea Brand', email='bea@brand.test', password=PW, role='brand', organization='Bea Home Goods')
            assert pg.js("!document.querySelector('.nav[data-page=marketplace]').classList.contains('hide')")
            pg.js("navigate('products')"); time.sleep(.5); pg.js('openProduct()')
            pg.js("document.querySelector('#productForm [name=name]').value='Bottle'"); pg.set_files('#productForm [name=image]', prod_png)
            pg.js("document.getElementById('productForm').requestSubmit()"); pg.wait('cache.products.length===1')
            pg.js("renderPage()"); time.sleep(.6)
            pid = pg.js('cache.products[0].id'); pg.set_files(f'#glb{pid}', glb)
            pg.js(f"document.getElementById('glb{pid}').dispatchEvent(new Event('change'))")
            pg.wait("cache.products[0].has_model===true || (renderPage(),false)", timeout=20, what='3D model accepted')
            pg.js("navigate('campaigns')"); time.sleep(.6); pg.click_text('＋ New campaign'); pg.wait("!!document.getElementById('cf')")
            pg.js("document.getElementById('cn').value='Spring launch';document.getElementById('cc').value='home';document.getElementById('cb').value='2000'")
            pg.js("document.getElementById('cf').requestSubmit()"); time.sleep(.8)
            pg.click_text('Find videos'); pg.wait("document.querySelectorAll('#pageContent .grid3 .card').length>=1", what='marketplace results')
            assert pg.js("document.getElementById('pageContent').textContent.includes('Match')")
            pg.shot(out / '02-marketplace-match.png')
            pg.click_text('Make offer'); time.sleep(.8)
        step('Separate brand organization: product + 3D model, campaign, marketplace match, offer', brand_setup)

        def creator_accepts():
            logout(); auth('login', email='cara@studio.test', password=PW)
            pg.js("navigate('offers')"); pg.wait("document.getElementById('pageContent').textContent.includes('Accept & license')")
            pg.shot(out / '03-creator-offer.png'); pg.click_text('Accept & license'); time.sleep(.8)
            assert pg.js("document.getElementById('pageContent').textContent.includes('ACCEPTED')")
        step('Creator accepts the offer, creating a licence', creator_accepts)

        def brand_3d():
            logout(); auth('login', email='bea@brand.test', password=PW)
            pg.js("navigate('placements')"); time.sleep(.6); pg.click_text('＋ New placement')
            pg.wait("!!document.getElementById('edVideo') && document.getElementById('edVideo').readyState>=1", what='licensed video plays in editor')
            pg.js("setMode('3d')"); assert pg.js("ed.mode")=='3d'
            m3 = meta['model3d']
            pg.js(f"ed.quad={json.dumps(meta['quad_norm'])};ed.ref=0.4;ed.start=0.4;ed.end=2.8;Object.assign(ed.m3,{{sw:{m3['surface_size_m'][0]},sh:{m3['surface_size_m'][1]},u:{m3['anchor_uv'][0]},v:{m3['anchor_uv'][1]},yaw:{m3['yaw_deg']}}});syncInputs();document.getElementById('edVideo').currentTime=0.4")
            time.sleep(.5); pg.shot(out / '04-editor-3d-mode.png')
            pg.click_text('Create placement & check quality')
            pg.wait("document.getElementById('modalTitle').textContent==='Quality report'", timeout=180, what='3D quality report')
            assert pg.js("document.getElementById('modalBody').textContent.includes('PASSED')"), pg.js("document.getElementById('modalBody').textContent")[:400]
            pg.wait("document.getElementById('pvImg') && document.getElementById('pvImg').naturalWidth>0", timeout=60, what='renderer preview frame')
            pg.js("document.getElementById('pvT').value=2.0;loadPreview(cache.placements[0].id)"); time.sleep(2.5)
            pg.shot(out / '05-3d-quality-preview.png')
            pg.js("document.getElementById('pvOrig').click()"); time.sleep(2.5); pg.shot(out / '06-3d-original-frame.png'); pg.js('closeModal()')
        step('Brand builds a 3D placement on the licensed video; quality PASSED; exact-renderer preview', brand_3d)

        def approvals_render():
            pg.js('renderPage()'); time.sleep(.6); pg.click_text('Review fit'); pg.click_text('Accept fit')
            pg.wait("cache.placements[0].fit_status==='ACCEPTED'"); pg.click_text('Propose'); pg.wait("cache.placements[0].status==='PROPOSED'")
            logout(); auth('login', email='cara@studio.test', password=PW)
            pg.js("navigate('placements')"); time.sleep(.8); pg.click_text('Review & consent'); pg.wait("!!document.getElementById('cConsent')")
            pg.js("document.querySelectorAll('input[name=ch]').forEach(x=>x.checked=x.value==='social');document.getElementById('cConsent').checked=true")
            pg.click_text('Consent & approve'); pg.wait("cache.placements[0].status==='CREATOR_APPROVED'")
            logout(); auth('login', email='bea@brand.test', password=PW)
            pg.js("navigate('placements')"); time.sleep(.6); pg.click_text('Approve'); pg.wait("cache.placements[0].status==='READY_FOR_RENDER'")
            pg.click_text('Render')
            pg.wait("cache.jobs.length && ['SUCCEEDED','FAILED','DEAD_LETTER'].includes(cache.jobs[0].status)", timeout=300, what='3D render')
            assert pg.js("cache.jobs[0].status") == 'SUCCEEDED', pg.js("cache.jobs[0].error")
            assert pg.js("cache.jobs[0].validation.status") == 'PASSED'
            pg.shot(out / '07-3d-render-validated.png')
            assert pg.js("fetch(cache.jobs[0].download_url).then(r=>r.status)") == 200
            pg.click_text('Share link'); pg.wait("document.getElementById('modalTitle').textContent==='Share download link'")
            url = pg.js("document.querySelector('#modalBody input').value"); pg.js('closeModal()')
            assert httpx.get(url, timeout=30).status_code == 200      # works without a session, within its TTL
        step('Consent, approvals, 3D render validated, download, signed share link', approvals_render)

        def report_and_mobile():
            pg.js("navigate('campaigns')"); time.sleep(.6); pg.click_text('Report')
            pg.wait("document.getElementById('modalTitle').textContent.startsWith('Campaign report')")
            assert pg.js("document.getElementById('modalBody').textContent.includes('Not measured')")
            pg.shot(out / '08-campaign-report.png'); pg.js('closeModal()')
            pg.viewport(390, 844, True); pg.js("navigate('marketplace')"); time.sleep(1)
            assert pg.js('document.documentElement.scrollWidth<=window.innerWidth+1'), 'horizontal scroll at 390px'
            pg.shot(out / '09-mobile-marketplace.png')
        step('Campaign report separates measured and unmeasured; marketplace fits 390 px', report_and_mobile)
        if pg.errors: raise AssertionError('Console errors: ' + '; '.join(pg.errors[:5]))
        log.append({'step': 'No console errors or uncaught exceptions', 'ok': True})
    finally:
        (out / 'browser-qa-v8-log.json').write_text(json.dumps({'steps': log}, indent=1)); browser.kill(); api.kill()
    print('ALL V8 BROWSER QA STEPS PASSED')


if __name__ == '__main__':
    main()
