# Monitoring and operations

## Endpoints
| Endpoint | Purpose | Touches database? | Public? |
|---|---|---|---|
| `GET /api/health/live` | Liveness: the web process answers | **No** | Yes. Use it for uptime monitors and container health checks |
| `GET /api/health/ready` | Readiness: database, object storage, FFmpeg, disk, live workers. 503 when not ready. Cached `CC_READY_CACHE_SECONDS` (30) | Yes (at most every 30 s) | Yes. Shows check names and states only; no hosts, bucket names or versions |
| `GET /api/health` | Summary for the settings page | Yes (shares the readiness cache) | Signed-in UI |
| `GET /api/ops/metrics` | Operator metrics: queue depth, running, oldest queued age, dead letters; 24 h job outcomes, failure and retry counts, render p50/p95; upload, email, OAuth and download failures; worker heartbeats; disk; local cache size | Yes | **Only with `Authorization: Bearer $CC_OPS_TOKEN`**; 404 when no token is configured |

## Alerts
`python -m ops.monitor --api https://api.example.com [--webhook URL]` exits 1 and posts to the webhook (Slack-compatible JSON) when:

| Condition | Threshold |
|---|---|
| backend down | liveness not 200 |
| backend not ready | readiness 503 (database, storage, FFmpeg) |
| no live render worker | 0 heartbeats < 120 s |
| queue backlog | > 20 queued |
| oldest queued job | > 30 minutes |
| job failure rate (24 h, ≥ 4 finished jobs) | > 25 % |
| disk low | < 2 GB free |

Schedule it from cron on a machine other than the backend VM, e.g. `*/30 * * * *`. It queries the database through readiness and metrics, so on Neon Free do not run it every minute. External uptime services (any free plan) should poll **only** `/api/health/live`, every 1–5 minutes.

## Logs
- One JSON object per line on stdout (`CC_LOG_FORMAT=json`), rotated by Docker. Fields: `ts`, `level`, `logger`, `msg`, `request_id` or `job_id`, plus per-event fields (`method`, `path`, `status`, `ms`; `job`, `attempt`, `seconds`, `error`).
- Every response carries `X-Request-ID`. A client-supplied id of 8–64 safe characters is kept, so traces can be correlated. Internal errors return the request id, never a stack trace.
- **Never logged:** passwords, session cookies, sign-in codes, magic-link tokens, signed download tokens (`/api/media/[REDACTED]`), presigned URL signatures, query strings, email addresses (`[email]`). Uvicorn's own access log is off (`CC_UVICORN_ACCESS_LOG=0`) because it prints raw paths. Redaction is unit-tested (`tests/test_v10.py::test_log_redaction`).

Useful queries: `docker compose logs api | grep '"status": 5'` (server errors); `docker compose logs worker | grep '"job failed"'`.

## Routine operations
| Task | Command |
|---|---|
| Queue and worker state | `curl -H "Authorization: Bearer $CC_OPS_TOKEN" https://api.example.com/api/ops/metrics` |
| Evict cache now | `docker compose … exec worker python -m ops.maintenance cache` |
| Expire abandoned uploads now | `docker compose … exec worker python -m ops.maintenance db` |
| Record a payment step | `docker compose … exec api python -m ops.payments record --offer 12 --kind PAYMENT --amount 450 --reference BANK-REF --by "Ops: name"` |
| Backup drill | [backup-and-recovery.md](backup-and-recovery.md) |
