// Frontend checks without dependencies: every inline <script> in the shipped pages must parse, and the Vercel build
// output must contain the pages, config.js and routes with security headers and no secrets.
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
let failures = 0;
const fail = (m) => { console.error('FAIL ' + m); failures++; };
for (const page of ['landing.html', 'index.html']) {
  const html = readFileSync(join(root, 'app', 'static', page), 'utf8');
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
  scripts.forEach((js, i) => { try { new vm.Script(js, { filename: `${page}#script${i}` }); } catch (e) { fail(`${page} script ${i}: ${e.message}`); } });
  console.log(`ok ${page}: ${scripts.length} inline script(s) parse`);
}
const out = join(root, '.vercel', 'output');
if (existsSync(out)) {
  for (const f of ['static/index.html', 'static/app.html', 'static/config.js', 'static/404.html', 'config.json']) if (!existsSync(join(out, f))) fail(`build output missing ${f}`);
  const cfg = JSON.parse(readFileSync(join(out, 'config.json'), 'utf8'));
  const sec = cfg.routes.find((r) => r.headers && r.headers['Content-Security-Policy']);
  if (!sec || !/frame-ancestors 'none'/.test(sec.headers['Content-Security-Policy'])) fail('security headers route missing');
  const walk = (d) => readdirSync(d).flatMap((n) => statSync(join(d, n)).isDirectory() ? walk(join(d, n)) : [join(d, n)]);
  for (const f of walk(out)) {
    const t = readFileSync(f, 'utf8');
    if (/GOCSPX-|postgres(ql)?:\/\/[^:\s]+:[^@\s]+@|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY/.test(t)) fail(`secret-like value in build output: ${f}`);
  }
  console.log(`ok build output: ${cfg.routes.length} routes`);
}
process.exit(failures ? 1 : 0);
