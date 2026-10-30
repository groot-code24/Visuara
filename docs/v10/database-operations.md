# Database operations (Neon PostgreSQL)

**Status:** the full test suite and the 10-organization workload ran on PostgreSQL 16 (Docker). **No Neon project was available.** The steps below are written for Neon, and nothing Neon-specific has been executed.

## Create the database
1. Neon console → New project. Pick the region nearest the backend VM (each request makes several round trips).
2. **Connect** → choose the **pooled** connection string (`-pooler` host, PgBouncer). The app disables server-side prepared statements (`prepare_threshold=None`), so the pooler is safe.
3. Put it in `deploy/.env` as `DATABASE_URL=postgresql://…-pooler…/neondb?sslmode=require&channel_binding=require`. TLS is required by the URL.
4. Create the schema explicitly before the first rollout:
   ```bash
   docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env run --rm api python -m ops.migrate
   ```
   Output: `schema ok: engine=postgres neon=True tables=N …`. Migrations are idempotent and additive, and run under an advisory lock, so the API and workers may start together.

## Moving existing data from SQLite
**Back up first** (`python ops/backup.py backup --data ./data --out ./backups/pre-migration`), then:
```bash
DATABASE_URL=… python -m ops.dbtransfer migrate --sqlite ./data/context_commerce.sqlite3
```
The tool exports every table, imports into the empty target, resets the id sequences, and verifies row counts and order-independent content digests per table. It refuses a non-empty target. Media files go to R2 with `python -m ops.dbtransfer push-media --data ./data`.

## Connections and idle compute
- Pool per process: `CC_DB_POOL_SIZE` (10), `min_size=0`, idle connections closed after `CC_DB_POOL_MAX_IDLE` (60 s), recycled after 30 minutes.
- Nothing polls the database while idle, except the worker's backoff poll (≤ 1 per `CC_WORKER_MAX_IDLE_SECONDS`, default 900 s) and the 6-hourly maintenance that piggybacks on it. Container health checks and external uptime monitors must use `/api/health/live`.
- **Verify after go-live:** Neon → Monitoring. With no traffic the compute should show suspended periods of about 10 minutes between short active windows.

## Transactions and concurrency
- Every request uses one transaction (`with db() as c:`): commit on success, roll back on error.
- Critical sections use PostgreSQL advisory transaction locks: schema init, rate limits, ownership transfer, payment ledger.
- Job claiming is an atomic conditional `UPDATE`; lease renewal and publication are conditioned on the lease token.
- Two workers plus the API on one schema are exercised by `tests/test_e2e_processes.py` and the 10-organization test.

## Isolation
Organization scoping is in the queries: `WHERE org_id=?`, `owned()`, licence grants. One database and schema hold all tenants. There is no row-level security; isolation is tested by `tests/test_v10.py`, `tests/test_api.py::test_org_isolation` and the cross-tenant probes of the 10-organization test.

## Indexes
On every organization-scoped table (users, sessions, jobs(org_id,status), videos, products, placements, frames, invitations, queue events, upload sessions, payment records), in addition to the V9 indexes.
