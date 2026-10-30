// Builds the static frontend for Vercel using the Build Output API (v3): .vercel/output/{static,config.json}.
//
// The landing page and app are the same files the FastAPI server serves locally (app/static); this script copies
// them, writes /config.js with the API origin, and generates routes (security headers, optional API proxy).
//
// Environment (set in the Vercel project):
//   CC_API_ORIGIN  https://api.example.com   origin of the FastAPI backend (required on production builds)
//   CC_API_MODE    direct | proxy            direct (default): the browser calls the API origin with CORS and
//                                            same-site cookies (app.example.com + api.example.com).
//                                            proxy: Vercel rewrites /api, /auth and /r to the API origin so the browser
//                                            sees one origin (use for *.vercel.app previews). Vercel limits proxied
//                                            requests to a short timeout, so production should use direct mode.
// No secrets are read or written: the output contains only public configuration.
import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const out = join(root, '.vercel', 'output');
const env = process.env;
const isProd = env.VERCEL_ENV === 'production';
const mode = (env.CC_API_MODE || 'direct').toLowerCase();
const apiOrigin = (env.CC_API_ORIGIN || '').replace(/\/+$/, '');

function fail(msg) { console.error(`frontend build failed: ${msg}`); process.exit(1); }
if (!['direct', 'proxy'].includes(mode)) fail(`CC_API_MODE must be "direct" or "proxy", not "${mode}"`);
if (apiOrigin) {
  let u; try { u = new URL(apiOrigin); } catch { fail(`CC_API_ORIGIN is not a URL: ${apiOrigin}`); }
  if (u.pathname !== '/' || u.search || u.hash) fail('CC_API_ORIGIN must be an origin only, e.g. https://api.example.com');
  const local = ['localhost', '127.0.0.1'].includes(u.hostname) || u.hostname.endsWith('.localhost');
  if (u.protocol !== 'https:' && !local) fail('CC_API_ORIGIN must use https');
} else if (isProd) fail('CC_API_ORIGIN is required for production builds');
for (const k of Object.keys(env)) {
  if (/^(CC_SMTP|GOOGLE_CLIENT_SECRET|DATABASE_URL|AWS_SECRET|CC_SIGNING_KEY|CC_OPS_TOKEN)/.test(k) && env[k])
    console.warn(`warning: ${k} is set in the frontend build environment; it is not used here and belongs only on the backend`);
}

rmSync(out, { recursive: true, force: true });
const stat = join(out, 'static');
mkdirSync(stat, { recursive: true });
const src = join(root, 'app', 'static');
cpSync(join(src, 'landing.html'), join(stat, 'index.html'));
cpSync(join(src, 'index.html'), join(stat, 'app.html'));
cpSync(src, join(stat, 'static'), { recursive: true });   // anything referenced as /static/... keeps working
const apiBase = mode === 'direct' ? apiOrigin : '';
writeFileSync(join(stat, 'config.js'), `window.CC_CONFIG=${JSON.stringify({ apiBase })};\n`);
writeFileSync(join(stat, '404.html'), '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Not found · Context Commerce</title><body style="font-family:system-ui;display:grid;place-items:center;min-height:100vh;margin:0;background:#f6f7fb;color:#191a2c"><main style="text-align:center"><h1>Page not found</h1><p><a href="/">Back to Context Commerce</a></p></main></body>');

const connect = ["'self'", apiOrigin, 'https:'].filter(Boolean).join(' ');
const csp = ["default-src 'self'", "script-src 'self' 'unsafe-inline'", "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' data: https://fonts.gstatic.com", `img-src 'self' data: blob: https: ${apiOrigin}`.trim(), `media-src 'self' blob: https: ${apiOrigin}`.trim(),
  `connect-src ${connect}`, "frame-ancestors 'none'", "base-uri 'self'", `form-action 'self' ${apiOrigin}`.trim(), "object-src 'none'"].join('; ');
const security = { 'Content-Security-Policy': csp, 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'strict-origin-when-cross-origin', 'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=()',
  'Strict-Transport-Security': 'max-age=31536000; includeSubDomains' };
const routes = [
  { src: '^/(.*)$', headers: security, continue: true },
  { src: '^/(config\\.js|app\\.html|index\\.html)?$', headers: { 'Cache-Control': 'no-cache' }, continue: true },
  { src: '^/app/?$', headers: { 'Cache-Control': 'no-cache' }, continue: true },
];
if (mode === 'proxy') {
  if (!apiOrigin) fail('proxy mode needs CC_API_ORIGIN');
  // Never cache proxied API responses at the edge (they are per-user).
  routes.push({ src: '^/(api|auth|r)/(.*)$', dest: `${apiOrigin}/$1/$2`, headers: { 'Cache-Control': 'no-store', 'x-vercel-enable-rewrite-caching': '0' } });
}
routes.push({ handle: 'filesystem' }, { src: '^/app/?$', dest: '/app.html' }, { src: '^/(.*)$', dest: '/404.html', status: 404 });
writeFileSync(join(out, 'config.json'), JSON.stringify({ version: 3, routes }, null, 2));

const pkg = existsSync(join(root, 'frontend', 'package.json')) ? JSON.parse(readFileSync(join(root, 'frontend', 'package.json'), 'utf8')).version : 'dev';
console.log(`frontend ${pkg}: built .vercel/output (mode=${mode}, api=${apiOrigin || 'same origin'})`);
