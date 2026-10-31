> **Superseded by [docs/v11/deployment.md](../v11/deployment.md).** This describes the V10 static frontend, which no longer exists. Follow the V11 guide.

# Vercel frontend deployment

**Status:** the configuration is complete and was tested with a local emulator of Vercel's router, against the real API on a second origin (browser QA 10/10). **No Vercel deployment has been made**: there is no Vercel account or CLI here.

## What is deployed
Only static files: the landing page (`/`), the app (`/app`), `config.js`, `404.html`. No serverless functions and no secrets. Video uploads go from the browser straight to R2; API calls go straight to `api.<domain>`.

## Project settings
| Setting | Value |
|---|---|
| Framework preset | Other (`vercel.json` sets `framework: null`) |
| Root directory | repository root |
| Install command | none (from `vercel.json`) |
| Build command | `node frontend/build.mjs` (from `vercel.json`) |
| Output | `.vercel/output`, written by the build (Build Output API v3); no output directory setting needed |
| Node.js | 18 or newer |
| Production branch | `main` |

## Environment variables (Vercel → Settings → Environment Variables)
| Name | Production | Preview | Notes |
|---|---|---|---|
| `CC_API_ORIGIN` | `https://api.example.com` | your staging API, or the production API with proxy mode | Required on production builds; the build fails without it or with `http://` |
| `CC_API_MODE` | `direct` (default) | `proxy` | Proxy mode rewrites `/api`, `/auth` and `/r` to the API, so preview URLs work without CORS |
| `CC_STORAGE_ORIGIN` | leave unset | leave unset | Only for non-https storage in local tests |

These values are public: they end up in `config.js`. **Never** put backend secrets in the Vercel project. The build prints a warning if it sees `DATABASE_URL`, SMTP, Google secret, signing-key or ops-token variables.

## What the build generates
- `config.js` = `window.CC_CONFIG={"apiBase":"https://api.example.com"}`.
- Routes:
  - security headers on every response: CSP with `connect-src` and `img-src` limited to self, the API and https; `frame-ancestors 'none'`; HSTS; nosniff; Referrer-Policy; Permissions-Policy;
  - `no-cache` on the HTML and `config.js`, so a new API origin takes effect immediately;
  - `/app` → `app.html`;
  - 404 fallback.
- Proxy mode only: `/api/*`, `/auth/*` and `/r/*` → the API, with `Cache-Control: no-store` and `x-vercel-enable-rewrite-caching: 0` (Vercel caches external rewrites by default since April 2026).

## Domains
Add `app.example.com` to the project. The API must be `api.example.com` on the same registrable domain (see [deployment-architecture.md](deployment-architecture.md)). On the API set `CC_APP_URL=https://app.example.com` and `CC_CORS_ORIGINS=https://app.example.com`.

## Blocking deployment on failed checks
Protect `main` on GitHub: require the `ci` workflow jobs (lint, tests on SQLite and PostgreSQL, security, frontend, docker), and let Vercel deploy production only from `main`. Pull requests still get preview deployments, but nothing reaches production until CI is green.

## Verify after the first deployment
1. `https://app.example.com/config.js` shows the API origin.
2. Response headers on `/` include `content-security-policy` and `strict-transport-security`.
3. Sign in with an email code: the session works, and `GET https://api.example.com/api/me` from the app returns your user (DevTools → Network: credentialed request, `access-control-allow-origin: https://app.example.com`).
4. Upload a small video: DevTools shows a `PUT` to `*.r2.cloudflarestorage.com` and then `POST /api/uploads/{id}/complete`.

## Local equivalent
```bash
CC_API_ORIGIN=http://api.cc.localhost:8000 CC_STORAGE_ORIGIN=http://localhost:8333 node frontend/build.mjs
node frontend/check.mjs
python qa/vercel_emulator.py --port 3000
```
