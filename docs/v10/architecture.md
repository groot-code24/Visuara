# V10 architecture

A modular monolith with a separately scaled worker. There are no new services and no Kubernetes; it is the smallest shape that keeps video processing off the web tier and the frontend on a CDN.

```
 Browser ── https://app.example.com ──────────────► Vercel (static: landing, app, config.js; security headers)
    │
    │ credentialed CORS (exact allowlist), SameSite=Lax session cookie, CSRF header (token from /api/auth/csrf)
    ▼
 https://api.example.com ── Caddy (TLS) or Cloudflare Tunnel ──► FastAPI container (app.main)
    │                                                               ├ auth.py      Google OIDC (PKCE, JWKS-verified), email code / magic link
    │                                                               ├ members.py   roles, invitations, suspension, ownership; central authorize()
    │                                                               ├ commerce.py  listings, campaigns, offers, licence grants, reports
    │                                                               ├ uploads.py   direct-to-bucket upload sessions
    │                                                               ├ payments.py  supervised payment ledger, operator API
    │                                                               ├ quality / tracking / render3d  quality gates (sync)
    │                                                               └ obs.py       JSON logs, request ids, liveness/readiness, metrics
    │  presigned PUT (upload) / 307 → presigned GET (download, thumbnails)
    ▼
 Cloudflare R2 (private bucket) ◄──────── write-through / restore ───────── Worker container(s) (worker.py)
                                                                              claim (fair) → lease + heartbeat → FFmpeg render
 Neon PostgreSQL ◄── jobs, leases, users, licences, events ──────────────────  → frame-by-frame validation → publish
          ▲                                                                   wake-up file on shared /data volume
          └────────────── API (pooled, idle connections closed) ──────────────  heartbeat files, cache + db maintenance
```

## Request path
1. The browser loads static pages from Vercel. `config.js` names the API origin.
2. `api()` calls the API with `credentials: 'include'`. The API answers only allowlisted origins, refuses writes from any other `Origin`, and requires the CSRF header on cookie-authenticated writes.
3. `current_user()` loads the session, refuses suspended or removed accounts, and applies the role table (`members.required_role`). Ownership and licence checks (`owned()`, grants) then scope every query to the organization.

## Media path
- **Upload:** `POST /api/uploads/video` returns a presigned PUT for a fresh object key → the browser uploads directly → `POST /api/uploads/{id}/complete`. The server checks the object's size, downloads it, runs the malware scan if configured, probes it with FFprobe, applies limits and quotas, then records the video. Without object storage the browser falls back to a streamed `POST /api/videos`.
- **Read:** every media endpoint authorises first, then redirects (307) to a 5-minute presigned GET. The API never proxies video bytes when object storage is on.

## Job path
`POST /render` re-checks all gates, enforces the organization's active-job quota, inserts a `QUEUED` job, commits, and touches `/data/run/worker.wake`. A worker claims the job with an atomic `UPDATE … WHERE status='QUEUED'`, preferring organizations with fewer running jobs. It renews its lease every 15 frames. Cancellation, revocation, lease loss, timeout and shutdown are all checked at each heartbeat. The output is published only if the job still holds its lease and validation passed. Expired leases are recovered by any worker; retries are bounded (3), then the job is dead-lettered.

## Data model additions in V10
`users.member_role`, `suspended_at`, `removed_at`; `invitations.member_role`, `revoked_at`; `videos.size_bytes`, `products.size_bytes`, `jobs.output_bytes`; `offers.payment_status`; tables `upload_sessions`, `payment_records`; indexes on every organization-scoped table. All migrations are additive and idempotent (`init_db`, under an advisory lock on PostgreSQL).
