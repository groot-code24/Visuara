# Deployment architecture

| Component | Where | Why there |
|---|---|---|
| Landing page + app (static HTML/JS) | **Vercel** (`frontend/build.mjs`, Build Output API) | CDN, previews, no server code |
| FastAPI API | **One Linux VM**, Docker (`deploy/docker-compose.prod.yml`) | Needs FFprobe, OpenCV and synchronous quality checks of up to ~60 s, beyond a serverless function's limits |
| Render worker(s) | Same VM, separate containers (`worker.py`) | Minutes-long FFmpeg jobs and CPU-heavy work. Never in Vercel functions |
| TLS | Caddy (automatic HTTPS) or Cloudflare Tunnel (no open ports) | Pick one profile |
| Database | **Neon PostgreSQL** (pooled connection string) | Managed, branching for restore drills |
| Media | **Cloudflare R2**, private bucket | S3 API, no egress fees, presigned URLs |
| Email | Any SMTP provider (Resend, Brevo, SES, Postmark…) | Sign-in codes, magic links, invitations |
| Google sign-in | Google Cloud OAuth client (Web application) | |

## Recommended domain layout
```
app.example.com   → Vercel project (production branch: main)
api.example.com   → VM: Caddy :443 → api:8000   (or a Cloudflare Tunnel hostname)
```
`app.` and `api.` must be subdomains of **the same registrable domain**. Then they are the same site: the `SameSite=Lax` session cookie set by `api.` is sent on the app's credentialed `fetch` calls, and no third-party-cookie rules apply. Set:
- API: `CC_PUBLIC_URL=https://api.example.com`, `CC_APP_URL=https://app.example.com`, `CC_CORS_ORIGINS=https://app.example.com`
- Vercel: `CC_API_ORIGIN=https://api.example.com` (direct mode)
- Google OAuth redirect URI: `https://api.example.com/auth/google/callback`
- R2 CORS: `python -m ops.storage_setup --app-origin https://app.example.com --apply`

## Preview deployments
`*.vercel.app` preview URLs are a different site from `api.example.com`, so browsers will not send the session cookie in direct mode. Previews either:
- build with `CC_API_MODE=proxy` (Vercel rewrites `/api`, `/auth` and `/r` to the API; one origin; subject to Vercel's proxy timeout and caching rules, disabled by `x-vercel-enable-rewrite-caching: 0`); or
- point at a staging API with its own allowlist.

**Never** add `*.vercel.app` to the production API's `CC_CORS_ORIGINS`.

## Why not everything on Vercel
- Rendering takes minutes of CPU and needs FFmpeg and a persistent worker that renews leases.
- Uploads of up to 500 MB must not pass through function request bodies; they go to R2 directly.

## Sizing for the first 10 organizations
One VM with 4 vCPU and 8 GB RAM runs the API (1 CPU, 1 GB limit) and 1–2 workers (2 CPU, 3 GB each). See [multi-tenant-readiness.md](multi-tenant-readiness.md) for the measured throughput on the local stack.
