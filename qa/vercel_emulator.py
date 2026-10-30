"""Serve .vercel/output locally the way Vercel's router would, for testing the split-origin deployment.

    node frontend/build.mjs   (with CC_API_ORIGIN=http://api.cc.localhost:8000)
    python qa/vercel_emulator.py --port 3000

Implements the subset of Build Output API v3 routes that frontend/build.mjs emits: header routes with `continue`,
`handle: filesystem`, rewrites to static files, external proxy rewrites (proxy mode) and the 404 fallback.
It is a test harness, not a production server.
"""
import argparse, http.server, json, mimetypes, re, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / '.vercel' / 'output'


class Handler(http.server.BaseHTTPRequestHandler):
    routes = []

    def log_message(self, fmt, *args): pass

    def _send(self, status, body, headers, ctype):
        self.send_response(status)
        for k, v in headers.items(): self.send_header(k, v)
        self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(body))); self.end_headers()
        if self.command != 'HEAD': self.wfile.write(body)

    def _route(self):
        path = self.path.split('?', 1)[0]; query = self.path[len(path):]
        headers, filesystem_done = {}, False
        for r in self.routes:
            if r.get('handle') == 'filesystem':
                f = OUT / 'static' / path.lstrip('/')
                if path != '/' and f.is_file(): return self._file(f, headers, 200)
                if path == '/' and (OUT / 'static' / 'index.html').is_file(): return self._file(OUT / 'static' / 'index.html', headers, 200)
                filesystem_done = True; continue
            m = re.match(r['src'], path)
            if not m: continue
            headers.update(r.get('headers', {}))
            if r.get('continue'): continue
            dest = r.get('dest')
            if dest and dest.startswith('http'):
                return self._proxy(re.sub(r'\$(\d)', lambda g: m.group(int(g.group(1))), dest) + query, headers)
            if dest:
                return self._file(OUT / 'static' / dest.lstrip('/'), headers, r.get('status', 200))
        return self._send(404, b'not found', headers, 'text/plain')

    def _file(self, f, headers, status):
        return self._send(status, f.read_bytes(), headers, mimetypes.guess_type(f.name)[0] or 'application/octet-stream')

    def _proxy(self, url, headers):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0)) if self.command in ('POST', 'PUT', 'PATCH', 'DELETE') else None
        req = urllib.request.Request(url, data=body, method=self.command, headers={k: v for k, v in self.headers.items() if k.lower() not in ('host', 'content-length')})
        try: resp = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as e: resp = e
        data = resp.read()
        self.send_response(resp.status if hasattr(resp, 'status') else resp.code)
        for k, v in resp.headers.items():
            if k.lower() not in ('transfer-encoding', 'content-length', 'connection'): self.send_header(k, v)
        for k, v in headers.items(): self.send_header(k, v)
        self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = _route


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--port', type=int, default=3000); a = ap.parse_args()
    if not (OUT / 'config.json').exists(): sys.exit('run node frontend/build.mjs first')
    Handler.routes = json.loads((OUT / 'config.json').read_text())['routes']
    http.server.ThreadingHTTPServer(('127.0.0.1', a.port), Handler).serve_forever()
