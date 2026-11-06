// Reproduces D-SEC-01: the post-sign-in redirect check against the WHATWG URL parser browsers use.
const safeNext = (n) => (n && n.startsWith('/') && !n.startsWith('//') && !n.includes('\\') ? n : '/dashboard');
for (const raw of ['/dashboard/requests', '//evil.example', '/\t/evil.example', '/\n/evil.example', '/\r/evil.example', '/\\evil.example']) {
  const out = safeNext(raw);
  const host = new URL(out, 'https://www.visuara.si').host;
  console.log(JSON.stringify(raw).padEnd(24), '->', JSON.stringify(out).padEnd(22), 'lands on', host);
}
