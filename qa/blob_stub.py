"""A local stand-in for Vercel Blob's HTTP API, for tests and QA only (no real store can be scripted without an account).

It enforces what the real service is documented to enforce: client tokens are checked by HMAC and bound to one pathname,
content type, size and expiry; private objects need a bearer token or a valid signature; signed links expire. The request
shapes come from Vercel's own SDKs, which the tests run unmodified against this server. It proves our side works with those
SDKs. It does not prove anything about Vercel's servers, CDN or CORS rules.
"""
import base64, hashlib, hmac, json, secrets, threading, time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _b64url(b): return base64.urlsafe_b64encode(b).decode().rstrip('=')


class BlobStub:
    def __init__(self, store='TESTSTORE', host='127.0.0.1', advertise=None, port=0):
        self.store = store; self.rw_token = f'vercel_blob_rw_{store}_{secrets.token_hex(12)}'
        self.signing_key = secrets.token_hex(16); self.objects = {}; self.mpu = {}; self.calls = []; self.lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *a): pass

            def _send(self, status, body=b'', ctype='application/json', extra=None):
                if isinstance(body, (dict, list)): body = json.dumps(body).encode()
                self.send_response(status)
                for k, v in {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': '*', 'Access-Control-Allow-Headers': self.headers.get('Access-Control-Request-Headers', '*'), 'Access-Control-Expose-Headers': '*',
                             'Content-Type': ctype, 'Content-Length': str(len(body)), **(extra or {})}.items():
                    self.send_header(k, v)
                self.end_headers()
                if self.command != 'HEAD': self.wfile.write(body)

            def _err(self, status, code, message=''): self._send(status, {'error': {'code': code, 'message': message}})
            def _body(self):
                if 'chunked' not in (self.headers.get('Transfer-Encoding') or '').lower():
                    return self.rfile.read(int(self.headers.get('Content-Length', 0)))
                out = b''                                    # the SDK streams uploads where the platform allows it
                while True:
                    size = int(self.rfile.readline().split(b';')[0].strip() or b'0', 16)
                    if not size: self.rfile.readline(); return out
                    out += self.rfile.read(size); self.rfile.readline()
            def _token(self): return (self.headers.get('authorization') or '').removeprefix('Bearer ').strip()
            def do_OPTIONS(self): self._send(204, b'')

            def _authorise(self, pathname):
                """None when the request may write this pathname, else (status, code)."""
                t = self._token()
                if t == stub.rw_token: return None
                prefix = f'vercel_blob_client_{stub.store}_'
                if not t.startswith(prefix): return 403, 'forbidden'
                try:
                    sig, payload = base64.b64decode(t[len(prefix):]).decode().split('.', 1)
                    claims = json.loads(base64.b64decode(payload))
                except Exception: return 403, 'forbidden'
                if not hmac.compare_digest(sig, hmac.new(stub.rw_token.encode(), payload.encode(), hashlib.sha256).hexdigest()): return 403, 'forbidden'
                if claims['validUntil'] < time.time() * 1000: return 400, 'client_token_expired'
                if claims.get('pathname') != pathname: return 400, 'client_token_pathname_mismatch'
                self.claims = claims
                ctype = self.headers.get('x-content-type')
                if claims.get('allowedContentTypes') and ctype not in claims['allowedContentTypes']: return 400, 'content_type_not_allowed'
                if not claims.get('allowOverwrite') and pathname in stub.objects: return 400, 'bad_request'
                return None

            def _store(self, pathname, data, ctype):
                limit = getattr(self, 'claims', {}).get('maximumSizeInBytes')
                if limit and len(data) > limit: return self._err(400, 'file_too_large', 'the file length cannot be greater than the limit')
                etag = '"' + hashlib.md5(data).hexdigest() + '"'
                with stub.lock: stub.objects[pathname] = {'data': data, 'type': ctype or 'application/octet-stream', 'etag': etag}
                u = stub.object_url(pathname)
                self._send(200, {'url': u, 'downloadUrl': u + '?download=1', 'pathname': pathname, 'contentType': ctype or 'application/octet-stream',
                                 'contentDisposition': f'inline; filename="{pathname.rsplit("/", 1)[-1]}"', 'etag': etag})

            def do_PUT(self):
                u = urllib.parse.urlsplit(self.path); q = dict(urllib.parse.parse_qsl(u.query)); pathname = q.get('pathname', '')
                stub.calls.append(('PUT', pathname))
                denied = self._authorise(pathname)
                data = self._body()
                if denied: return self._err(*denied)
                if self.headers.get('x-vercel-blob-access') != 'private': return self._err(400, 'bad_request', 'the store is private')
                self._store(pathname, data, self.headers.get('x-content-type'))

            def do_POST(self):
                u = urllib.parse.urlsplit(self.path); q = dict(urllib.parse.parse_qsl(u.query)); route = u.path.rstrip('/')
                body = self._body()
                if route.endswith('/signed-token'):
                    if self._token() != stub.rw_token: return self._err(403, 'forbidden')
                    req = json.loads(body or b'{}'); scope = {'storeId': stub.store, 'pathname': req.get('pathname', '*'), 'operations': req.get('operations', ['get']), 'validUntil': req['validUntil']}
                    stub.calls.append(('SIGNED_TOKEN', scope['pathname']))
                    return self._send(200, {'delegationToken': _b64url(json.dumps(scope).encode()) + '.sig', 'clientSigningToken': stub.signing_key, 'validUntil': req['validUntil']})
                if route.endswith('/delete'):
                    if self._token() != stub.rw_token: return self._err(403, 'forbidden')
                    for target in json.loads(body)['urls']:
                        stub.calls.append(('DELETE', target))
                        with stub.lock: stub.objects.pop(stub.pathname_of(target), None)
                    return self._send(200, {})
                if route.endswith('/mpu'):
                    pathname = q.get('pathname', '')
                    denied = self._authorise(pathname)
                    if denied: return self._err(*denied)
                    action = self.headers.get('x-mpu-action'); upload_id = self.headers.get('x-mpu-upload-id')
                    if action == 'create':
                        upload_id = secrets.token_hex(8); stub.mpu[upload_id] = {'pathname': pathname, 'parts': {}, 'type': self.headers.get('x-content-type')}
                        stub.calls.append(('MPU_CREATE', pathname))
                        return self._send(200, {'key': 'k' + upload_id, 'uploadId': upload_id})
                    state = stub.mpu.get(upload_id)
                    if not state: return self._err(400, 'bad_request', 'unknown upload')
                    if action == 'upload':
                        n = int(self.headers['x-mpu-part-number']); etag = hashlib.md5(body).hexdigest(); state['parts'][n] = body
                        stub.calls.append(('MPU_PART', pathname))
                        return self._send(200, {'etag': etag, 'partNumber': n})
                    if action == 'complete':
                        data = b''.join(state['parts'][p['partNumber']] for p in sorted(json.loads(body), key=lambda p: p['partNumber']))
                        del stub.mpu[upload_id]
                        return self._store(pathname, data, state['type'])
                self._err(404, 'not_found')

            def do_HEAD(self): self.do_GET()

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path); q = dict(urllib.parse.parse_qsl(u.query)); route = u.path.rstrip('/')
                if route.startswith('/delivery/'): return self._deliver(urllib.parse.unquote(route[len('/delivery/'):]), q)
                if self._token() != stub.rw_token: return self._err(403, 'forbidden')
                if 'url' in q:
                    pathname = stub.pathname_of(q['url']); o = stub.objects.get(pathname)
                    if not o: return self._err(404, 'not_found')
                    u2 = stub.object_url(pathname)
                    return self._send(200, {'url': u2, 'downloadUrl': u2 + '?download=1', 'pathname': pathname, 'size': len(o['data']), 'etag': o['etag'], 'uploadedAt': '2026-10-31T00:00:00.000Z',
                                            'contentType': o['type'], 'contentDisposition': 'inline', 'cacheControl': 'public, max-age=31536000'})
                self._send(200, {'blobs': [], 'hasMore': False})

            def _deliver(self, pathname, q):
                o = stub.objects.get(pathname)
                if self._token() != stub.rw_token and not stub.signature_ok(pathname, q): return self._err(403, 'forbidden')
                if not o: return self._err(404, 'not_found')
                data, status, extra = o['data'], 200, {'Accept-Ranges': 'bytes', 'ETag': o['etag']}
                if q.get('download') == '1': extra['Content-Disposition'] = f'attachment; filename="{pathname.rsplit("/", 1)[-1]}"'
                rng = self.headers.get('range', '')
                if rng.startswith('bytes='):
                    a, _, b = rng[6:].partition('-'); a = int(a or 0); b = int(b) if b else len(data) - 1
                    extra['Content-Range'] = f'bytes {a}-{b}/{len(data)}'; data = data[a:b + 1]; status = 206
                self._send(status, data, o['type'], extra)

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.handle_error = lambda *a: None
        self.origin = f'http://{advertise or "127.0.0.1"}:{self.server.server_address[1]}'
        self.api_url = self.origin + '/api/blob'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def object_url(self, pathname): return f'{self.origin}/delivery/{urllib.parse.quote(pathname)}'

    def pathname_of(self, target):
        if '://' not in target: return target
        return urllib.parse.unquote(urllib.parse.urlsplit(target).path).removeprefix('/delivery/').lstrip('/')

    def signature_ok(self, pathname, q):
        """A signed GET: not expired, and the HMAC the SDK's canonical string produces under the delegation's signing key."""
        if int(q.get('vercel-blob-valid-until', 0)) < time.time() * 1000: return False
        lines = sorted(['operation=get', f'pathname={pathname}', f'vercel-blob-valid-until={q.get("vercel-blob-valid-until")}'], key=lambda s: s.encode())
        want = _b64url(hmac.new(self.signing_key.encode(), '\n'.join(lines).encode(), hashlib.sha256).digest())
        return hmac.compare_digest(want, q.get('vercel-blob-signature', ''))

    def patch_delivery(self):
        """Point Vercel's Python SDK and our module at this server, because the delivery host is fixed in code (test processes only)."""
        from vercel.blob._internal import service
        from app import blobstore
        delivery = lambda pathname: self.object_url(pathname)
        service.construct_delivery_url = lambda store, pathname, access: delivery(pathname)
        blobstore.delivery_url = delivery

    def close(self): self.server.shutdown()
