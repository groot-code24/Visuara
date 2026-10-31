# Deployment (V11)

```
browser ──► app.example.com  (Vercel: Next.js)
              │  /api/*, /auth/google/*  (rewrites)
              ▼
            api.example.com  (VM: Caddy → FastAPI API) ── Neon PostgreSQL
                                     workers (renders + decisions) ── Cloudflare R2 (private)
browser ──► R2 presigned URLs (uploads, downloads) ─────────────────────┘
```

The backend side (VM, Docker, Caddy or Cloudflare Tunnel, Neon, R2, workers, backups, monitoring) is unchanged from V10. See `docs/v10/backend-deployment.md`, `worker-deployment.md`, `database-operations.md` and `storage-operations.md`. The changes are below.

## Order of work
**Vercel hosts only the web app.** It cannot run the API or the workers, because they need FFmpeg and long-running processes. The site's sign-in and dashboard work only once the backend is live, so deploy in this order:

1. **Neon:** create the database and copy the pooled connection string.
2. **Cloudflare R2:** create a private bucket and an API token, then apply CORS for the app's origin: `python -m ops.storage_setup --apply`.
3. **Email:** verify a sending domain with an SMTP provider. Sign-in depends on it.
4. **Google:** create an OAuth client. Its authorised redirect URI is `https://<app domain>/auth/google/callback`. The consent screen needs your home page, `/privacy` and `/terms`.
5. **VM:** copy `deploy/env.production.example` to `deploy/.env`, fill it in and start the API and workers (`docs/v10/backend-deployment.md`). The API refuses to start while a production setting is unsafe. Size the disk for your largest videos plus the 72-hour media cache.
6. **Vercel:** deploy the web app (below).
7. **Check it:** work through [To verify on the first real deployment](#to-verify-on-the-first-real-deployment-never-done).

## Web app on Vercel
1. Push the repository to GitHub, import it in Vercel and set **Root Directory = `web`**. The framework is detected as Next.js, and Node comes from `engines` (22.x).
2. Set these environment variables for **Production**:

   | Variable | Value | Notes |
   |---|---|---|
   | `CC_API_ORIGIN` | `https://api.example.com` | **Required.** The production build fails without it or with plain `http://`. |
   | `NEXT_PUBLIC_CONTACT_EMAIL` | `hello@yourdomain.com` | Needed in practice. "Talk to us", upgrades, Privacy and Terms all point to it. |
   | `NEXT_PUBLIC_SITE_URL` | `https://app.example.com` | Canonical address for social previews and the sitemap. Defaults to the Vercel production domain. |

3. Add your domain. Set `CC_PUBLIC_URL` and `CC_APP_URL` on the backend to exactly that origin, then restart the API.
4. Restrict production deployments to `main`. CI (`.github/workflows/ci.yml`) type-checks and builds the app on every push.
5. In Vercel's project settings, put the functions in the region closest to the API. The dashboard checks the session with the API on every page load.

**Preview deployments.** Do not give previews the production `CC_API_ORIGIN`. The API refuses writes from any origin except `CC_PUBLIC_URL`, `CC_APP_URL` and `CC_CORS_ORIGINS`, and sign-in emails link to the production site, so sign-in would not work on a preview anyway. Without the variable, a preview still builds and shows the marketing pages, and the build logs a warning. To test the dashboard on previews, point them at a separate staging backend.

Self-hosting instead: `docker build --build-arg CC_API_ORIGIN=https://api.example.com -t cc-web web`, then run it on port 3000 behind your proxy.

## Backend settings that change in V11
| Variable | V11 value | Why |
|---|---|---|
| `CC_PUBLIC_URL` | `https://app.example.com` | Magic links and the Google callback must go through the app origin |
| `CC_APP_URL` | `https://app.example.com` | Absolute redirects after Google sign-in |
| `CC_CORS_ORIGINS` | not needed | The browser never calls the API directly. The app origin is still allowed for writes, through `CC_PUBLIC_URL` |
| `FORWARDED_ALLOW_IPS` | `*` | Client IPs come from `X-Forwarded-For`, so per-IP sign-in limits apply per person, not per proxy |
| `CC_QUALITY_WAIT_SECONDS` | `90` (default) | How long a quality-check request waits before answering "still measuring". Keep it under Vercel's 120 s |
| `CC_DEFAULT_PLAN` | `free` | Plan of new workspaces; change one with `python -m ops.plans set` |
| Google OAuth redirect URI | `https://app.example.com/auth/google/callback` | Was the API origin in V10 |
| R2 CORS | allow `https://app.example.com` (PUT, GET, HEAD) | Direct uploads from the browser: `python -m ops.storage_setup --apply` |

## To verify on the first real deployment (never done)
These have not been tested against real Vercel, R2, Neon, SMTP or Google; local tests used `next start`'s rewrite proxy and stand-ins. Check each once on the live site:

- [ ] **Cookies:** after sign-in, `cc_session` is set on the app's domain and is `Secure` and `HttpOnly`, and `/dashboard` opens.
- [ ] **Google:** sign in with a real Google account, and cancel once to see the error message.
- [ ] **Email:** the code and the magic link both arrive, outside the spam folder, and the link opens your domain.
- [ ] **Upload:** a real phone video uploads directly to R2 (the browser's network tab shows the PUT to R2) and its thumbnail appears.
- [ ] **Client IP:** the API log shows real visitor addresses, not Vercel's, so per-IP sign-in limits apply per person.
- [ ] **Product image:** upload one close to 10 MB. Product images go through the proxy; Vercel documents a 4.5 MB limit for functions but none that we could find for external rewrites. If it fails, lower `CC_MAX_IMAGE_MB`.
- [ ] **Long check:** run a quality check on a window of a minute or more. It should answer "still measuring" and then finish (Vercel allows 120 s to the first byte; the API answers within 90 s).
- [ ] **Render and download:** render one placement and download it.

## Local, production-shaped
```bash
docker compose up --build    # web http://localhost:3000 · inbox http://localhost:8025 · API docs http://localhost:8000/docs
```
If ports are taken, move them: `CC_API_PORT=8010 CC_WEB_PORT=3010 docker compose up`. To test the browser against real object storage, run `python qa/browser_qa_stack.py --app http://localhost:3010`.
