# Job lifecycle contract

Jobs live in SQLite (`jobs`, `queue_events`). Workers are the inline API thread (`CC_INLINE_WORKER=1`) and/or `python worker.py` processes sharing `CC_DATA_DIR`.

```
QUEUED ─claim─▶ RUNNING ─▶ SUCCEEDED
   ▲              │ ├────▶ FAILED        (policy gate or output validation failed: terminal)
   │              │ ├────▶ CANCELLED     (cancel requested, rights revoked, or revision)
   └─ retry ──────┤ └────▶ DEAD_LETTER   (transient errors or expired leases after max_attempts = 3)
   └─ lease expired (recovered on every claim cycle)
```

- **Claim:** atomic `UPDATE … WHERE status='QUEUED'` sets a fresh random `lease_token`, `lease_until = now + CC_JOB_LEASE_SECONDS` (default 120) and increments `attempts`.
- **Heartbeat:** every 15 frames during render and validation, the worker renews the lease *only if* it still holds the token, the job is RUNNING and no cancel was requested. Otherwise it aborts immediately (mid-render cancellation).
- **Completion:** `SUCCEEDED` is written conditionally on token, status and cancel flag, so a stale or cancelled worker cannot overwrite the result. Then the placement moves RENDERING → RENDERED.
- **Recovery:** each claim cycle requeues RUNNING jobs with expired leases, or dead-letters them at `max_attempts`. The placement moves to RENDER_FAILED.
- **Idempotency:** outputs are named per placement, version and job; temp files per lease token. A recovered job re-renders from scratch. **Exactly-once execution is not claimed.** The guarantee is at most one *successful completion record* per job.
- **Binding:** jobs store `placement_version`, `analysis_id` and `renderer`. A job whose version or analysis no longer matches fails with `VERSION_MISMATCH`.
- Every state change writes a `queue_events` row, visible in `GET /api/observability`.

Limitations: single shared SQLite database; no multi-host coordination guarantees beyond SQLite locking; no priority or fairness; no per-tenant concurrency limits.
