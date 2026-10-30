# V10 repository audit

**Scope:** the complete V9 tree as delivered (`app/`, `ops/`, `benchmark/`, `qa/`, `tests/`, Docker files, docs).
**Method:** read every module, the V9 docs and report; reproduced the test suite; traced each V9 blocker to code.
**Baseline:** an untouched copy of V9, run on Windows 11 with Python 3.11.0, gave **55 passed in 250.6 s (SQLite)**. The historical V9 result reproduces.

Priority: **P0** security, data loss, deployment failure or a broken core workflow; **P1** required for the first 10 organizations; **P2** commercial growth; **P3** later.

## Findings

| # | Area | Finding (V9) | Pri | V10 status |
|---|---|---|---|---|
| 1 | Worker / Neon | The worker polled the database every 0.5 s, the Docker healthcheck pinged the database every 30 s, and the pool kept a minimum connection open. Neon Free could never scale to zero and would exhaust its compute allowance mid-month. | P0 | **Fixed:** idle backoff 0.5 s → 900 s, file-based wake-up, liveness check with no database, pool `min_size=0` + `max_idle=60 s`. Live Neon dashboard behaviour is **not verified** (no account). |
| 2 | Deployment | No way to deploy the frontend separately. The UI assumed the same origin as the API: relative `fetch`, cookie-read CSRF, links and media URLs, magic-link and OAuth redirects to `/app`. | P0 | **Fixed:** `config.js` API base; credentialed CORS with an exact allowlist; Origin check; `/api/auth/csrf`; redirects to `CC_APP_URL`; Vercel Build Output API build. Verified in a browser on two origins. |
| 3 | Uploads | Videos were read into memory whole (`file.read(150 MB)`) and always passed through the web tier. | P0 | **Fixed:** streamed server uploads, plus direct-to-bucket presigned uploads with completion checks, cancel, retry and expiry cleanup. |
| 4 | Docker | The image was never built; there was one compose file with no database or storage; the healthcheck used the database. | P0 | **Fixed:** image built and run (Python 3.12.16, FFmpeg 7.1.5, non-root uid 10001, tini); local compose with PostgreSQL 16, S3 gateway, Mailpit and 2 workers; production compose. |
| 5 | Tenancy | No member roles: every member could do everything, including invite. No member removal, suspension, invitation revocation or ownership. | P1 | **Fixed:** owner, admin, manager, editor and viewer roles, enforced centrally; suspension, removal, revocation, ownership transfer; audit events. |
| 6 | Invitations | Any member could invite; invitations could not be revoked. | P1 | **Fixed:** admin+ only; revocable; listed with status; emailed when SMTP is configured. |
| 7 | Fairness | Jobs were claimed FIFO globally, so one organization's batch starved the rest. There were no per-organization limits. | P1 | **Fixed:** claim order favours organizations with fewer running jobs; active-job, storage, daily-upload, analysis and quality-check quotas. |
| 8 | Job safety | No maximum job duration. On worker shutdown, a job waited out its lease (up to 2 min) before recovery. | P1 | **Fixed:** `CC_JOB_MAX_SECONDS` (terminal `JOB_TIMEOUT`); SIGTERM hands the job back to the queue immediately with its attempt refunded. |
| 9 | Disk | The local media cache was never cleaned. | P1 | **Fixed:** LRU eviction of files confirmed present in the bucket (same size), skipping files used in the last hour, plus stale temp-file removal. |
| 10 | Observability | Plain prints and Uvicorn access logs (raw paths). No request ids, liveness/readiness split or operator metrics. | P1 | **Fixed:** JSON logs with request/job ids and redaction; `/api/health/live`, `/api/health/ready`; `/api/ops/metrics` (operator token); `ops.monitor` with webhook alerts. |
| 11 | Production safety | Development stand-ins (outbox mail, SQLite, local disk, generated signing key) could run in production silently. | P1 | **Fixed:** `CC_ENV=production` refuses to start while any is active. |
| 12 | OAuth | The ID-token signature was not checked locally (TLS-only, which OIDC permits). | P2 | **Fixed:** RS256 verification against Google's JWKS (cached, refreshed on unknown `kid`). |
| 13 | 3D | A fixed 1 px plane tolerance ignored pose error (V9 held-out false reject). | P1 | **Fixed and validated on fresh seeds 8–10** ([3d-tolerance.md](3d-tolerance.md)). It exposed a pose-quality gap that remains open. |
| 14 | Payments | No payment state at all. | P1 | **Option A implemented:** supervised manual invoicing ledger with an operator CLI/API. No card processing. |
| 15 | Git / CI | Not a repository; no CI. | P1 | **Fixed locally:** git initialised with history; CI workflow written. **Not run on GitHub** (no remote or credentials). |
| 16 | Security headers | No CSP or frame protection; API responses were cacheable. | P1 | **Fixed:** CSP and frame-ancestors on HTML; `no-store` on API responses; HSTS behind HTTPS; Vercel rewrite caching disabled. |
| 17 | UI accuracy | The settings card claimed "API and SQLite… local data directory" on every deployment. | P2 | **Fixed:** shows the real database and storage (found in V10 browser QA). |
| 18 | UI cross-origin | The preview `fetch` and the landing page's signed-in check used relative URLs. | P1 | **Fixed** (found in V10 code review of the QA screenshots). |
| 19 | Live services | Neon, R2, SMTP and Google were tested only against stand-ins. | P0 for launch | **Open:** no credentials here. Configuration and verification scripts are ready ([release-readiness.md](release-readiness.md)). |
| 20 | Real footage | No authorised real footage evaluated. | P0 for launch | **Open:** no footage supplied. |
| 21 | Pen test | No independent penetration test. | P1 | **Open:** an internal review was done ([security-review.md](security-review.md)). |
| 22 | Agency / attribution | Agency workspaces and ad attribution are incomplete. | P2 | **Unchanged:** shared workspaces (brand + creator accounts) still work; attribution stays "observed only". |

## Mock services and test-only implementations found

| Mock / stand-in | Where | Production guard |
|---|---|---|
| Email outbox (`CC_MAIL_BACKEND=outbox`) | `app/mailer.py` | Refused when `CC_ENV=production` |
| Generated signing key in the data dir | `app/main.py:_signing_key` | `CC_SIGNING_KEY` (32+ chars) required in production |
| SQLite fallback | `app/db.py` | `DATABASE_URL` required in production |
| Local-disk storage | `app/storage.py` | `CC_S3_BUCKET` required in production |
| Inline worker thread | `app/main.py:lifespan` | `CC_INLINE_WORKER=0` required in production |
| `google_exchange` monkeypatched; moto S3; pgserver | tests only | Not importable by the app at runtime |

## Code paths that worked only on localhost (V9)
- Relative API URLs in `index.html`, cookie-read CSRF, and `/app` redirects in `app/auth.py`. Fixed (finding 2).
- Presigned URLs signed with the server's internal storage endpoint (`http://minio:9000` style) are unreachable by browsers. Fixed with `CC_S3_PUBLIC_ENDPOINT_URL`.
- Client IPs for rate limits behind a proxy. Documented: set `FORWARDED_ALLOW_IPS` and run Uvicorn with `--proxy-headers` (in the image).

## Docker and Linux compatibility
- The image builds on `python:3.12-slim` (Debian 13).
- FFmpeg 7.1.5 from Debian is used in the image; FFmpeg 8.1 locally. All tests that use FFmpeg run locally on 8.1; the container renders on 7.1.5 in the 10-organization test.
- Paths come from `pathlib` throughout. `.gitattributes` forces LF so shell scripts run on Linux.
- **Arm64 (e.g. an Oracle Ampere VM) not verified:** all wheels used have aarch64 builds, but the image was built for amd64 only.
