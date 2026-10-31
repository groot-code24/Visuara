"""A local stand-in for Google's OpenID Connect endpoints, for browser QA only (no real Google account can be scripted).

It behaves as the real flow requires: it checks the client id and redirect URI, verifies the PKCE code verifier, makes
each code single-use, and signs the ID token with an RSA key published as a JWKS. It proves that our side of the flow
works through the web app's proxy. It does not prove that a real Google client is configured correctly.
"""
import base64, hashlib, json, secrets, threading, time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def _b64(b): return base64.urlsafe_b64encode(b).decode().rstrip('=')


class GoogleStub:
    def __init__(self, client_id, redirect_uri, email='ana@northbrew.co', name='Ana Silva'):
        self.client_id, self.redirect_uri, self.email, self.name = client_id, redirect_uri, email, name
        self.deny = False; self.codes = {}; self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _json(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path); q = dict(urllib.parse.parse_qsl(u.query))
                if u.path == '/jwks':
                    nums = stub.key.public_key().public_numbers()
                    return self._json(200, {'keys': [{'kid': 'stub', 'kty': 'RSA', 'n': _b64(nums.n.to_bytes(256, 'big')), 'e': _b64(nums.e.to_bytes(3, 'big'))}]})
                if u.path != '/auth' or q.get('client_id') != stub.client_id or q.get('redirect_uri') != stub.redirect_uri or q.get('code_challenge_method') != 'S256':
                    return self._json(400, {'error': 'invalid_request'})
                back = {'state': q['state'], 'error': 'access_denied'} if stub.deny else {'state': q['state'], 'code': stub._issue(q)}
                self.send_response(302); self.send_header('Location', stub.redirect_uri + '?' + urllib.parse.urlencode(back)); self.end_headers()

            def do_POST(self):
                f = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers.get('Content-Length', 0))).decode()))
                c = stub.codes.pop(f.get('code'), None)
                digest = _b64(hashlib.sha256(f.get('code_verifier', '').encode()).digest())
                if not c or digest != c['code_challenge'] or f.get('client_id') != stub.client_id or f.get('redirect_uri') != stub.redirect_uri:
                    return self._json(400, {'error': 'invalid_grant'})
                self._json(200, {'id_token': stub._id_token(c['nonce']), 'token_type': 'Bearer'})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _issue(self, q):
        code = secrets.token_urlsafe(16); self.codes[code] = q; return code

    def _id_token(self, nonce):
        now = int(time.time())
        claims = {'iss': 'https://accounts.google.com', 'aud': self.client_id, 'sub': 'stub-' + self.email, 'email': self.email, 'email_verified': True,
                  'name': self.name, 'nonce': nonce, 'iat': now, 'exp': now + 300}
        signing = f"{_b64(json.dumps({'alg': 'RS256', 'kid': 'stub'}).encode())}.{_b64(json.dumps(claims).encode())}"
        return signing + '.' + _b64(self.key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256()))

    def env(self):
        return {'GOOGLE_CLIENT_ID': self.client_id, 'GOOGLE_CLIENT_SECRET': 'stub-secret', 'CC_GOOGLE_AUTH_URL': self.url + '/auth',
                'CC_GOOGLE_TOKEN_URL': self.url + '/token', 'CC_GOOGLE_JWKS_URL': self.url + '/jwks'}

    def close(self): self.server.shutdown()
