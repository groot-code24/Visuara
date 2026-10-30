# Worker deployment

The worker is the same image with `command: python worker.py`, run as its own containers (`worker` service; `CC_WORKER_REPLICAS`). It never serves HTTP.

## What a worker does
1. **Claim:** an atomic `UPDATE jobs SET status='RUNNING' … WHERE id=? AND status='QUEUED'`. Organizations with fewer running jobs go first.
2. **Lease:** 120 s (`CC_JOB_LEASE_SECONDS`), renewed every 15 frames. Each renewal also checks cancellation, licence revocation, lease ownership, the job timeout (`CC_JOB_MAX_SECONDS`, default 1800 s) and shutdown.
3. **Render and validate:** restores sources from R2 if missing locally; renders with FFmpeg (audio kept); decodes the output and checks every frame against the plan.
4. **Publish:** uploads the output to R2, then marks the job `SUCCEEDED` only if it still holds the lease. Otherwise it deletes its output.
5. **Failure:** bounded retries (3), then `DEAD_LETTER`. Gate failures, validation failures and timeouts are terminal `FAILED`.
6. **SIGTERM:** the current job goes back to `QUEUED` with its attempt refunded, so another worker (or this one after restart) picks it up at once.

## Idle behaviour and Neon
With an empty queue the poll interval doubles from 0.5 s to `CC_WORKER_MAX_IDLE_SECONDS` (default **900 s**). The API touches `/data/run/worker.wake` whenever it queues a job, and workers check that file (a local `stat`, no database traffic) every 0.5 s. New jobs therefore start within about a second, while an idle system queries the database at most once every 15 minutes.

Why not 60 s: Neon suspends compute after **5 minutes** without activity. A 60 s poll would keep it awake permanently and use about 182 CU-hours a month at 0.25 CU, beyond the Free plan's 100. At 900 s the compute wakes for about 5 minutes per poll, roughly a third of the time (~60 CU-hours a month). Set `CC_WORKER_MAX_IDLE_SECONDS=1800` to halve that. The cost is that jobs queued by an API on **another host** (without the shared volume) wait up to that long. On the recommended single VM, the wake-up file makes it immediate.

**Measured locally** (PostgreSQL 16, `qa/idle_db_activity.py`): 6 app sessions in 32 idle minutes, each connection closed about 90 s after use, then no connection for the last ~13.5 minutes. **Not verified:** the actual suspend/resume pattern in a Neon dashboard. Do it after go-live: leave the system idle for 30 minutes and check **Neon → Monitoring → Compute**.

## Housekeeping run by the worker
| Task | Interval | Database? |
|---|---|---|
| Cache eviction: local media copies already in R2 (same size), unused for 1 h+, oldest first, when older than `CC_CACHE_MAX_AGE_HOURS` (72) or the cache exceeds `CC_CACHE_MAX_MB` (20 GB); stale `*.part.mp4`, `*.dl` and `*.tmp` files | `CC_CACHE_INTERVAL_SECONDS` (3600) | No |
| Expire abandoned upload sessions (deleting their objects); prune expired sessions, sign-in challenges, pending sign-ups and old rate-limit rows | `CC_MAINTENANCE_INTERVAL_SECONDS` (21600), only right after a poll that already woke the database | Yes |

Manual runs: `python -m ops.maintenance cache [--dry-run]` and `python -m ops.maintenance db`.

## Health
Each worker writes `/data/run/worker-<host>-<pid>.json` at least every 30 s and deletes it on a clean stop. The container health check `python -m ops.worker_health` passes if this host has a heartbeat younger than 120 s. Readiness and `/api/ops/metrics` report live worker counts. Docker restarts unhealthy workers.

## Sizing
- One worker renders one job at a time. Rendering is CPU-bound (OpenCV + the software rasteriser + x264).
- Give each worker 2 CPUs and 2–3 GB RAM.
- Raise `CC_WORKER_REPLICAS` when `/api/ops/metrics` shows `oldest_queued_age_seconds` above a few minutes during working hours.
- `CC_ORG_MAX_ACTIVE_JOBS` (default 2) stops any one organization from occupying every worker.
