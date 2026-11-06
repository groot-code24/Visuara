"""How fast the dashboard feels to someone far from the servers, in headless Chrome.

    cd web && CC_API_ORIGIN=http://127.0.0.1:<api-port> npx next build
    python qa/perf_probe.py --api-port <api-port> --web-port <web-port> [--rtt 300] [--web-dir web] [--out file.json]

Seeds the demo data, signs the demo brand in by the emailed code, then adds `--rtt` milliseconds to every request
(Chrome's network emulation), which is roughly what a visitor in India sees against servers in the eastern US. It then
clicks between dashboard pages and reports, per click:
  feedback  the click until the page changes (a loading outline or the new page)
  ready     the click until the new page shows its data
for first visits and for revisits. Also the time until the home page can be seen, on a first and a second visit.
"""
import argparse, json, os, re, statistics, subprocess, sys, tempfile, time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'qa'))
from browser_qa_web import CHROMES, Page, free_port   # noqa: E402

BRAND = 'brand@demo.visuara.si'
# (menu label, text that only appears once the page's data is shown)
# "ready" also needs the page's loading outlines (skeletons) gone, so a heading alone never counts as data.
PAGES = [('Campaigns', 'Back to work'), ('Placement requests', 'Placement requests'), ('Library', 'Laptop sleeve'), ('Marketplace', 'Marketplace'),
         ('Settings', 'Decisions this month'), ('Finance', 'Invoices')]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--api-port', type=int, default=8000); ap.add_argument('--web-port', type=int, default=3000)
    ap.add_argument('--rtt', type=int, default=300); ap.add_argument('--web-dir', default=str(ROOT / 'web')); ap.add_argument('--out')
    args = ap.parse_args()
    chrome = next(c for c in CHROMES if Path(c).exists())
    tmp = Path(tempfile.mkdtemp(prefix='visuara-perf-')); data = tmp / 'data'
    APP, API = f'http://localhost:{args.web_port}', f'http://127.0.0.1:{args.api_port}'
    env = dict(os.environ, CC_DOTENV='0', CC_MAIL_BACKEND='outbox', CC_PUBLIC_URL=APP, PYTHONPATH=str(ROOT), PYTHONIOENCODING='utf-8', CC_AUTH_RATE_PER_MINUTE='60')
    for k in ('DATABASE_URL', 'CC_DATABASE_URL', 'CC_S3_BUCKET', 'CC_APP_URL'): env.pop(k, None)
    seeded = subprocess.run([sys.executable, '-m', 'ops.demo', 'seed', '--data', str(data)], cwd=ROOT, env=env, capture_output=True, text=True)
    if seeded.returncode: raise SystemExit(seeded.stderr[-2000:])
    env.update(CC_DATA_DIR=str(data), CC_INLINE_WORKER='1', CC_DEFAULT_PLAN='team')
    logs = tmp / 'logs'; logs.mkdir()
    api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(args.api_port)], cwd=ROOT, env=env,
                           stdout=open(logs / 'api.log', 'wb'), stderr=subprocess.STDOUT)
    npx = 'npx.cmd' if os.name == 'nt' else 'npx'
    web = subprocess.Popen([npx, 'next', 'start', '--port', str(args.web_port)], cwd=args.web_dir, env=dict(env, CC_API_ORIGIN=API, NEXT_TELEMETRY_DISABLED='1'),
                           stdout=open(logs / 'web.log', 'wb'), stderr=subprocess.STDOUT)
    dport = free_port()
    browser = subprocess.Popen([chrome, '--headless=new', f'--remote-debugging-port={dport}', f'--user-data-dir={tmp / "p"}', '--no-first-run', 'about:blank'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = {'rtt_ms': args.rtt, 'clicks': []}
    try:
        for url in (API + '/api/health/live', APP + '/'):
            for _ in range(240):
                try:
                    if httpx.get(url, timeout=2).status_code < 500: break
                except httpx.HTTPError: pass
                time.sleep(.5)
        for _ in range(50):
            try: tabs = httpx.get(f'http://127.0.0.1:{dport}/json', timeout=2).json(); break
            except Exception: time.sleep(.2)
        pg = Page(next(t for t in tabs if t['type'] == 'page')['webSocketDebuggerUrl'])
        pg.viewport(1440, 900)
        pg.cmd('Network.enable')
        # sign in without added latency, then measure with it
        pg.go(APP + '/login'); pg.wait("!!document.getElementById('email')", what='login form')
        pg.type('#email', BRAND); pg.click_text('Continue with email'); pg.wait_text('Check your email')
        d = json.loads((data / 'outbox' / 'latest.json').read_text())
        pg.type('input[aria-label="Digit 1"]', re.search(r'enter this code: (\d{6})', d['text']).group(1))
        pg.wait("location.pathname==='/dashboard'", what='dashboard')
        pg.cmd('Network.emulateNetworkConditions', offline=False, latency=args.rtt, downloadThroughput=6_000_000, uploadThroughput=2_000_000)

        def visible_home():
            t = time.time(); pg.go(APP + '/')
            pg.wait("document.readyState!=='loading'&&(()=>{const l=document.querySelector('.vs-loader');return !l||getComputedStyle(l).visibility==='hidden'||getComputedStyle(l).display==='none'})()", what='home visible', timeout=30)
            return round((time.time() - t) * 1000)
        result['home_first_visit_ms'] = visible_home()
        result['home_second_visit_ms'] = visible_home()
        pg.go(APP + '/dashboard'); pg.wait_text('Hello', timeout=60); time.sleep(3)   # let idle prefetching (if any) happen

        def click(label, needle):
            before = pg.js('location.pathname')
            pg.js("(()=>{window.__t0=performance.now();window.__fb=0;window.__ok=0;"
                  "const o=new MutationObserver(()=>{if(!window.__fb&&(location.pathname!==" + json.dumps(before) + "||document.querySelector('[aria-busy=true]')))window.__fb=performance.now();"
                  "const m=document.querySelector('main');if(!window.__ok&&m&&location.pathname!==" + json.dumps(before) + f"&&!m.querySelector('.cc-skeleton,[aria-busy=true]')&&m.innerText.includes({json.dumps(needle)}))window.__ok=performance.now()}});"
                  "o.observe(document.body,{subtree:true,childList:true,characterData:true});window.__obs=o;})()")
            ok = pg.js(f"(()=>{{const a=[...document.querySelectorAll('aside nav a')].find(a=>a.textContent.trim()==={json.dumps(label)});if(!a)return false;a.dispatchEvent(new MouseEvent('mouseover',{{bubbles:true}}));a.click();return true}})()")
            assert ok, f'no menu link {label}'
            pg.wait('window.__ok>0', timeout=60, what=f'{label} data')
            fb, done, t0 = pg.js('[window.__fb||window.__ok, window.__ok, window.__t0]')
            pg.js('window.__obs.disconnect()')
            return round(fb - t0), round(done - t0)

        for rnd in ('first visit', 'revisit'):
            for label, needle in PAGES:
                fb, ready = click(label, needle)
                result['clicks'].append({'round': rnd, 'page': label, 'feedback_ms': fb, 'ready_ms': ready})
                print(f'{rnd:12s} {label:20s} feedback {fb:5d} ms   ready {ready:5d} ms', flush=True)
                time.sleep(.6)
        for rnd in ('first visit', 'revisit'):
            r = [c['ready_ms'] for c in result['clicks'] if c['round'] == rnd]; f = [c['feedback_ms'] for c in result['clicks'] if c['round'] == rnd]
            result[f'{rnd} median'] = {'feedback_ms': statistics.median(f), 'ready_ms': statistics.median(r)}
        print(json.dumps({k: v for k, v in result.items() if k != 'clicks'}, indent=1))
        if args.out: Path(args.out).write_text(json.dumps(result, indent=2))
    finally:
        for p in (browser, web, api):
            try:
                if os.name == 'nt': subprocess.run(['taskkill', '/T', '/F', '/PID', str(p.pid)], capture_output=True)
                else: p.kill()
            except Exception: pass


if __name__ == '__main__':
    main()
