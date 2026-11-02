"""A full rehearsal of the Vercel deployment on this machine, in Chrome, with nothing mocked inside the application.

    docker compose up -d postgres mailpit
    python qa/vercel_rehearsal.py

What matches Vercel: the image from Dockerfile.vercel with production settings; API and job runner as separate containers that
share no disk; a read-only filesystem except /tmp; 1 vCPU and 2 GB each; routing as in vercel.json, HTTPS, and Vercel's 4.5 MB
request limit (a small proxy below); Vercel's own JavaScript and Python Blob SDKs.

What stands in: PostgreSQL for Neon, qa/blob_stub.py for Blob, Mailpit for an email provider. So this proves our code and
configuration, not Vercel's platform, Neon, Google or a real inbox. docs/vercel.md lists what to check on the real thing.
"""
import argparse, datetime, http.client, json, ssl, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'qa'))
from benchmark import fixtures_scene as scene   # noqa: E402
from blob_stub import BlobStub   # noqa: E402
from browser_qa_web import CHROMES, Page, free_port   # noqa: E402

PUBLIC, API_PORT, WEB_PORT, BLOB_PORT, PROXY_PORT = 'https://localhost:8443', 8021, 3021, 8031, 8443
NET = 'context-commerce_default'
API, RUNNER, WEB = 'cc-sim-api', 'cc-sim-runner', 'cc-sim-web'
VERCEL_BODY_LIMIT = int(4.5 * 1048576)


def sh(*cmd, **kw): return subprocess.run(cmd, check=kw.pop('check', True), capture_output=True, text=True, encoding='utf-8', errors='replace', **kw)


def certificate(tmp):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'localhost')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=2)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), False).sign(key, hashes.SHA256()))
    (tmp / 'c.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM) + key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return tmp / 'c.pem'


class Edge(BaseHTTPRequestHandler):
    """What Vercel's edge does for this project: the top-level rewrites of vercel.json, X-Forwarded-*, and the body limit."""
    protocol_version = 'HTTP/1.1'
    routes = []
    def log_message(self, *a): pass

    def _handle(self):
        size = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(size) if size else None
        if size > VERCEL_BODY_LIMIT:
            self.send_response(413); self.send_header('Content-Length', '0'); self.end_headers(); return
        port = WEB_PORT
        for r in Edge.routes:
            if self.path.startswith(r): port = API_PORT
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'connection')}
        headers.update({'Host': 'localhost:8443', 'X-Forwarded-For': self.client_address[0], 'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'localhost:8443'})
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=320)
        try:
            conn.request(self.command, self.path, body=body, headers=headers); res = conn.getresponse(); data = res.read()
        except OSError:
            self.send_response(502); self.send_header('Content-Length', '0'); self.end_headers(); return
        self.send_response(res.status)
        for k, v in res.getheaders():
            if k.lower() not in ('transfer-encoding', 'connection', 'content-length', 'date', 'server'): self.send_header(k, v)
        self.send_header('Content-Length', str(len(data))); self.end_headers()
        if self.command != 'HEAD': self.wfile.write(data)
    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _handle


def edge_routes():
    """Read the routing from the real vercel.json, so the rehearsal fails if that file changes."""
    cfg = json.loads((ROOT / 'vercel.json').read_text())
    assert cfg['rewrites'][-1] == {'source': '/(.*)', 'destination': {'service': 'web'}}, 'the web app must be the last rule'
    return [r['source'].replace('(.*)', '') for r in cfg['rewrites'][:-1] if r['destination']['service'] == 'api']


def web_image(tmp):
    """The web app built as in production, with only the Blob API address pointed at the stand-in."""
    text = (ROOT / 'web' / 'Dockerfile').read_text()
    # The SDK streams uploads (HTTP/2 only in Chrome) except when its API address starts with http://localhost; this stand-in speaks HTTP/1.1.
    arg = f'ENV NEXT_PUBLIC_VERCEL_BLOB_API_URL=http://localhost:{BLOB_PORT}/api/blob\nRUN npm run build'
    assert 'RUN npm run build' in text
    f = tmp / 'Dockerfile.web'; f.write_text(text.replace('RUN npm run build', arg))
    sh('docker', 'build', '-q', '-f', str(f), '--build-arg', f'CC_API_ORIGIN=http://{API}:80', '-t', 'context-commerce-web:rehearsal', str(ROOT / 'web'), timeout=900)


def start_backend(name, env, publish=None):
    sh('docker', 'rm', '-f', name, check=False)
    args = ['docker', 'run', '-d', '--name', name, '--network', NET, '--add-host', 'host.docker.internal:host-gateway', '--cpus=1', '--memory=2g', '--memory-swap=2g',
            '--read-only', '--tmpfs', '/tmp:size=1024m,exec',
            '-v', f'{ROOT / "qa" / "rehearsal_app.py"}:/app/rehearsal_app.py:ro']
    if publish: args += ['-p', f'127.0.0.1:{publish}:80']
    for k, v in env.items(): args += ['-e', f'{k}={v}']
    args += ['context-commerce-vercel:local', 'sh', '-c', 'exec uvicorn rehearsal_app:app --host 0.0.0.0 --port 80 --proxy-headers --timeout-keep-alive 75 --no-server-header']
    sh(*args)


def wait_up(url, timeout=120):
    t = time.time()
    while time.time() - t < timeout:
        try:
            if httpx.get(url, timeout=5, verify=False).status_code == 200: return round(time.time() - t, 1)
        except httpx.HTTPError: pass
        time.sleep(.25)
    raise AssertionError(f'{url} did not come up')


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out', default=str(ROOT / 'docs' / 'evidence' / 'vercel_rehearsal.json')); a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix='cc-rehearsal-')); clips = tmp / 'clips'; clips.mkdir()
    Edge.routes = edge_routes()
    room = scene.generate('room', 31, clips)['path']; product = scene.product(clips / 'tech.png', 160, 100, (180, 80, 30))
    big_image = clips / 'big.png'; big_image.write_bytes(Path(product).read_bytes() + b'\0' * (5 * 1048576))
    big_video = clips / 'big.mp4'
    padding = 36 * 1048576       # a valid MP4 'free' box: the file is large enough for a multipart upload, and still plays
    big_video.write_bytes(Path(room).read_bytes() + (padding + 8).to_bytes(4, 'big') + b'free' + bytes(padding))

    stub = BlobStub(host='0.0.0.0', port=BLOB_PORT, advertise='127.0.0.1')
    sh('docker', 'exec', 'context-commerce-postgres-1', 'psql', '-U', 'cc', '-d', 'postgres', '-c', 'DROP DATABASE IF EXISTS cc_rehearsal')
    sh('docker', 'exec', 'context-commerce-postgres-1', 'psql', '-U', 'cc', '-d', 'postgres', '-c', 'CREATE DATABASE cc_rehearsal')
    env = {'DATABASE_URL': 'postgresql://cc:cc-local-only@postgres:5432/cc_rehearsal', 'CC_SIGNING_KEY': 'rehearsal-only-' + 'k' * 32, 'BLOB_READ_WRITE_TOKEN': stub.rw_token,
           'VERCEL_BLOB_API_URL': f'http://host.docker.internal:{BLOB_PORT}/api/blob', 'SIM_BLOB_ORIGIN': f'http://host.docker.internal:{BLOB_PORT}', 'SIM_BLOB_PUBLIC_ORIGIN': f'http://127.0.0.1:{BLOB_PORT}',
           'CC_SMTP_HOST': 'mailpit', 'CC_SMTP_PORT': '1025', 'CC_SMTP_TLS': 'none', 'CC_MAIL_FROM': 'Context Commerce <signin@qa.test>', 'CC_PUBLIC_URL': PUBLIC,
           'GOOGLE_CLIENT_ID': 'rehearsal-client.apps.googleusercontent.com', 'GOOGLE_CLIENT_SECRET': 'rehearsal-secret', 'CC_WORKER_URL': f'http://{RUNNER}:80', 'CC_JOB_LEASE_SECONDS': '30', 'CC_RUNNER_NUDGE_SECONDS': '5', 'CC_DEFAULT_PLAN': 'pro'}
    web_image(tmp)
    log, violations = [], []
    browser = None
    try:
        start_backend(RUNNER, env); start_backend(API, env, publish=API_PORT)
        cold = wait_up(f'http://127.0.0.1:{API_PORT}/api/health/live')
        sh('docker', 'rm', '-f', WEB, check=False)
        sh('docker', 'run', '-d', '--name', WEB, '--network', NET, '-p', f'127.0.0.1:{WEB_PORT}:3000', 'context-commerce-web:rehearsal')
        wait_up(f'http://127.0.0.1:{WEB_PORT}/')
        srv = ThreadingHTTPServer(('127.0.0.1', PROXY_PORT), Edge)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(certificate(tmp)); srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        srv.handle_error = lambda *a: None
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        chrome = next(c for c in CHROMES if Path(c).exists()); port = free_port()
        browser = subprocess.Popen([chrome, '--headless=new', f'--remote-debugging-port={port}', f'--user-data-dir={tmp / "p"}', '--no-first-run', '--ignore-certificate-errors', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try: tabs = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json')); break
            except Exception: time.sleep(.25)
        pg = Page(next(t for t in tabs if t['type'] == 'page')['webSocketDebuggerUrl'])
        pg.cmd('Page.addScriptToEvaluateOnNewDocument', source="window.__csp=[];document.addEventListener('securitypolicyviolation',e=>window.__csp.push(e.violatedDirective+' '+e.blockedURI));")
        pg.viewport(1440, 900)
        http = httpx.Client(verify=False, timeout=30)

        def step(name, fn):
            t = time.time()
            try:
                note = fn(); violations.extend(pg.js('window.__csp||[]') or []); log.append({'step': name, 'ok': True, 'seconds': round(time.time() - t, 1), **({'note': note} if note else {})}); print('PASS', name, note or '', flush=True)
            except Exception as e:
                page = ''
                try: page = pg.js("location.href+' | '+document.body.innerText.slice(-700)")
                except Exception: pass
                log.append({'step': name, 'ok': False, 'error': str(e)[:500], 'page': page}); print('FAIL', name, e, '| PAGE:', page, flush=True)
                print(sh('docker', 'logs', '--tail', '30', RUNNER, check=False).stdout[-1800:]); print(sh('docker', 'logs', '--tail', '30', API, check=False).stdout[-1800:])
                raise

        email = f'vercel{int(time.time())}@qa.test'

        def ready():
            r = http.get(PUBLIC + '/api/health/ready'); assert r.status_code == 200, r.text
            checks = r.json()['checks']; assert checks['database'] == 'ok' and checks['object_storage'] == 'ok' and checks['ffmpeg'] == 'ok', checks
            h = http.get(PUBLIC + '/api/health').json(); assert h['queue'] == 'on_demand_runner' and h['object_storage']['backend'] == 'vercel_blob' and h['mode'] == 'production', h
            assert http.get(PUBLIC + '/docs').status_code != 200 or 'swagger' not in http.get(PUBLIC + '/docs').text.lower()
            return f'first start {cold}s'
        step('production settings accepted; database, Blob and FFmpeg ready', ready)

        def fast_restart():
            start_backend(API, env, publish=API_PORT); took = wait_up(f'http://127.0.0.1:{API_PORT}/api/health/live')
            logs = sh('docker', 'exec', 'context-commerce-postgres-1', 'psql', '-U', 'cc', '-d', 'cc_rehearsal', '-tAc', 'SELECT count(*) FROM schema_fingerprint').stdout.strip()
            assert logs == '1'
            return f'new instance serving after {took}s without repeating schema setup'
        step('a new instance starts without repeating schema setup', fast_restart)

        def security():
            assert http.post(PUBLIC + '/api/internal/run-jobs').status_code == 401
            assert http.post(PUBLIC + '/api/internal/run-jobs', headers={'Authorization': 'Bearer guess'}).status_code == 401
            r = http.get(PUBLIC + '/dashboard', follow_redirects=False); assert r.status_code in (302, 307) and '/login' in r.headers['location']
            r = http.get(PUBLIC + '/'); assert r.headers.get('strict-transport-security') and "frame-ancestors 'none'" in r.headers.get('content-security-policy', '')
        step('job runner refuses outsiders; dashboard guarded; security headers', security)

        def google():
            p = http.get(PUBLIC + '/api/auth/providers').json(); assert p['google'] and p['email'] and not p.get('development'), p
            r = http.get(PUBLIC + '/auth/google/start', follow_redirects=False); assert r.status_code == 303, r.status_code
            loc = httpx.URL(r.headers['location']); assert loc.host == 'accounts.google.com', loc
            q = dict(loc.params); assert q['redirect_uri'] == PUBLIC + '/auth/google/callback' and q['client_id'].startswith('rehearsal-client') and q['code_challenge_method'] == 'S256', q
            assert any('secure' in c.lower() for c in r.headers.get_list('set-cookie')), r.headers.get_list('set-cookie')
        step('Google sign-in starts with the right client, callback and PKCE', google)

        def sign_up():
            pg.go(PUBLIC + '/login'); pg.wait_text('Sign in or create your account', timeout=60)
            assert 'Open the magic link' not in pg.text()
            pg.type('#email', email); pg.click_text('Continue with email'); pg.wait_text('Check your email')
            assert 'Open the magic link' not in pg.text(), 'production must not show the link on screen'
            import re
            for _ in range(60):
                msgs = httpx.get('http://127.0.0.1:8025/api/v1/search', params={'query': f'to:{email}'}, timeout=10).json().get('messages', [])
                if msgs: break
                time.sleep(.5)
            text = httpx.get(f'http://127.0.0.1:8025/api/v1/message/{msgs[0]["ID"]}', timeout=10).json()['Text']
            link = re.search(r'https://\S*/verify#token=[A-Za-z0-9_-]+', text).group(0); assert link.startswith(PUBLIC), link
            pg.go(link); pg.wait_text('Continue as', timeout=60); pg.click_text('Continue')
            pg.wait("location.pathname==='/welcome'", what='onboarding'); pg.wait("!!document.getElementById('name')", what='onboarding form'); pg.type('#name', 'Vercel QA')
            pg.js("(()=>{const e=document.getElementById('org');e.focus();e.select()})()"); pg.cmd('Input.insertText', text=f'Vercel QA {int(time.time())}')
            pg.click_text('Create workspace'); pg.wait("location.pathname==='/dashboard'", what='dashboard', timeout=60); pg.wait_text('Hello, Vercel', timeout=60)
        step('magic link by email, onboarding, dashboard', sign_up)

        def over_limit():
            pg.go(PUBLIC + '/dashboard/decide'); pg.wait_text('New decision')
            pg.set_files('input[type=file][accept^="image"]', [str(big_image)]); pg.wait("!!document.getElementById('pname')", what='product form')
            pg.type('#pname', 'Too big'); pg.click_text('Save product'); pg.wait_text('too large', timeout=30)
            assert 'Too big' not in pg.js("[...document.querySelectorAll('[data-product],li')].map(e=>e.innerText).join(' ')")
        step('an image over the request limit gets a clear message', over_limit)

        def upload():
            pg.go(PUBLIC + '/dashboard/decide'); pg.wait_text('New decision')
            pg.set_files('input[type=file][accept^="image"]', [product]); pg.wait("!!document.getElementById('pname')", what='product form')
            pg.type('#pname', 'Sleeve'); pg.click_text('Save product'); pg.wait_text('Sleeve', timeout=60)
            pg.js("document.querySelector('input[type=checkbox]').click()")
            pg.set_files('input[type=file][accept^="video"]', [room]); pg.wait_text('Uploaded', timeout=180)
            assert not any(c[1].startswith('uploads/video') for c in stub.calls if c[0] == 'MPU_CREATE')
            assert any(c[0] == 'PUT' and c[1].startswith('uploads/video') for c in stub.calls), 'the video did not go straight to Blob'
            assert any(k.startswith('uploads/video_') for k in stub.objects)
            assert not pg.js('window.__csp.length'), pg.js('window.__csp')
        step('browser uploads the video straight to Blob with the real SDK', upload)

        def decide():
            pg.type('#dname', 'Vercel run'); pg.click_text('Run decision engine', exact=False)
            pg.wait("location.pathname.startsWith('/dashboard/decisions/')", what='decision page')
            pg.wait_text('Best fit:', timeout=280)
            pg.wait("[...document.images].some(i=>i.src.includes('/preview?')&&i.complete&&i.naturalWidth>0)", what='preview through a signed link')
            assert 'decision succeeded' in sh('docker', 'logs', RUNNER, check=False).stdout, 'the decision should run on the runner'
            assert 'decision succeeded' not in sh('docker', 'logs', API, check=False).stdout
        step('decision runs on the separate runner; preview through a signed link', decide)

        def library():
            url = pg.js('location.href')
            pg.go(PUBLIC + '/dashboard/library?tab=videos'); pg.wait("[...document.images].some(i=>i.complete&&i.naturalWidth>0)", what='thumbnail', timeout=60)
            pg.go(url); pg.wait_text('Best fit:')
        step('library thumbnails load through signed links', library)

        def crash_during_render():
            pg.click_text('Create placement'); pg.wait("location.pathname.startsWith('/dashboard/placements/')", what='placement page')
            pg.wait_text('Run quality check', timeout=60); pg.click_text('Run quality check'); pg.wait_text('frames validated', timeout=280); assert 'Passed' in pg.text()
            pg.click_text('Approve'); pg.wait_text('Approved for version 1')
            pg.click_text('Start render')
            # Kill the runner as soon as it holds the job, like a function killed at its time limit. Nothing is left on its disk.
            for _ in range(200):
                if 'job started' in sh('docker', 'logs', RUNNER, check=False).stdout: break
                time.sleep(.1)
            sh('docker', 'rm', '-f', RUNNER); start_backend(RUNNER, env); wait_up_container(RUNNER)
            pg.go(PUBLIC + '/dashboard/renders')       # this page polls while a job is unfinished, which is what restarts the runner
            events = ''
            for _ in range(280):
                events = sh('docker', 'exec', 'context-commerce-postgres-1', 'psql', '-U', 'cc', '-d', 'cc_rehearsal', '-tAc', "SELECT string_agg(event_type, ',' ORDER BY id) FROM queue_events").stdout.strip()
                if 'succeeded' in events: break
                time.sleep(1)
            assert 'lease_recovered' in events and 'succeeded' in events, events
            return events
        step('runner killed mid-render: dashboard polling restarts it and the job completes', crash_during_render)

        def download():
            status = pg.js("fetch('/api/jobs').then(r=>r.json()).then(j=>fetch('/api/jobs/'+j[0].id+'/download')).then(r=>r.status+' '+r.headers.get('content-type')+' '+r.url.includes('vercel-blob-signature'))")
            assert status == '200 video/mp4 true', status
            key = next(k for k in stub.objects if k.startswith('outputs/'))
            head = pg.js("fetch('/api/jobs').then(r=>r.json()).then(j=>fetch('/api/jobs/'+j[0].id+'/download',{headers:{Range:'bytes=0-99'}})).then(r=>r.status)")
            assert head == 206, head
            return key
        step('finished video downloads through a signed link, with range requests', download)

        def multipart():
            pg.go(PUBLIC + '/dashboard/decide'); pg.wait_text('New decision')
            before = sum(c[0] == 'MPU_PART' for c in stub.calls)
            pg.js("[...document.querySelectorAll('label')].find(l=>l.innerText.includes('I own these videos')).querySelector('input').click()")
            pg.set_files('input[type=file][accept^="video"]', [str(big_video)]); pg.wait_text('Uploaded', timeout=240)
            parts = sum(c[0] == 'MPU_PART' for c in stub.calls) - before
            assert parts >= 2, f'expected a multipart upload, saw {parts} parts for {big_video.stat().st_size} bytes'
            return f'{big_video.stat().st_size // 1048576} MB in {parts} parts'
        step('a large video uploads in parts', multipart)

        def writes_only_tmp():
            for c in (API, RUNNER):
                r = sh('docker', 'exec', c, 'sh', '-c', 'touch /app/x', check=False); assert r.returncode != 0, 'the filesystem should be read-only'
        step('nothing depends on a writable disk outside /tmp', writes_only_tmp)

        errs = [e for e in pg.errors if 'favicon' not in e and '413' not in e and 'Failed to load resource' not in e]
        ok = not violations and not errs
        log.append({'step': 'no CSP violations or page errors', 'ok': ok, 'violations': violations[:10], 'errors': errs[:10]})
        print('PASS' if ok else 'FAIL', 'no CSP violations or page errors', violations[:10], errs[:10], flush=True)
    finally:
        if browser: browser.kill()
        for c in (API, RUNNER, WEB): sh('docker', 'rm', '-f', c, check=False)
        stub.close()
        out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(log, indent=1))
    passed = sum(1 for x in log if x['ok'])
    print(f'{passed}/{len(log)} passed')
    return 0 if passed == len(log) else 1


def wait_up_container(name, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        r = sh('docker', 'exec', name, 'python', '-c', "import urllib.request;urllib.request.urlopen('http://127.0.0.1:80/api/health/live',timeout=3)", check=False)
        if r.returncode == 0: return
        time.sleep(.5)
    raise AssertionError(f'{name} did not come up')


if __name__ == '__main__':
    sys.exit(main())
