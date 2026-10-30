# Context Commerce

Authorised product placement in creator videos. Brands and creators join from the landing page, with Google or an emailed code or magic link. Creators list videos they hold the rights to; brands license them, place a product image or 3D model on a tracked surface, and get the creator's consent to that exact version. A deterministic renderer produces the video, every frame is validated, and only then can it be downloaded through short-lived, access-checked links.

**The system renders only what it can render convincingly, and refuses everything else with a reason.**

**Release status (V10): BLOCKED for public launch; not yet a controlled pilot.** Everything runs locally and in Docker, and is tested, including 10 concurrent organizations. But Neon, Cloudflare R2, the SMTP provider, Google OAuth and Vercel have never been connected (no accounts were available), and no real creator footage has been evaluated. See [docs/v10/release-readiness.md](docs/v10/release-readiness.md).

## What V10 adds
- **Deployment:**
  - frontend on Vercel (static, Build Output API);
  - API and render workers as separate Docker services on one VM;
  - Neon PostgreSQL and Cloudflare R2;
  - cross-domain auth with an exact CORS allowlist and same-site cookies.
- **Uploads go straight from the browser to private storage** (presigned PUT), then are verified server-side; abandoned uploads are cleaned up.
- **Workspaces for teams:**
  - roles: owner, admin, manager, editor and viewer, enforced on the server;
  - invitations by email (revocable);
  - suspension, removal and ownership transfer, with an audit log.
- **Fair, bounded processing:**
  - per-organization quotas for storage, daily uploads, active renders and analysis rates;
  - fair job claiming across organizations;
  - job timeout;
  - graceful worker shutdown that hands the job back to the queue.
- **Neon-friendly idle behaviour:**
  - the worker backs off to a 15-minute poll and wakes at once through a file on the shared volume;
  - liveness checks never touch the database;
  - idle connections are closed.
- **Operations:**
  - JSON logs with request and job ids, with redaction;
  - liveness and readiness endpoints;
  - operator metrics;
  - alert script;
  - cache cleanup;
  - production guard: the app refuses to start in production with any development stand-in active.
- **Supervised payments:** an invoice, payment and settlement ledger recorded by the operator. There is no card processing.
- **3D:** the plane tolerance now accounts for pose error (validated on fresh held-out scenes).
- **Google ID tokens** are verified against Google's signing keys.
- **CI workflow, deploy and rollback scripts, git history.**

## Quick start (local, no accounts needed)
```bash
docker compose up --build        # app http://localhost:8000 · email inbox http://localhost:8025
```
This runs PostgreSQL 16, an S3-compatible store, a real SMTP inbox (Mailpit), the API and two workers.

Without Docker (Python 3.11+ and FFmpeg):
```bash
scripts/dev.sh setup && scripts/dev.sh run          # Windows: .\scripts\dev.ps1 setup; .\scripts\dev.ps1 run
```
Details: [docs/v10/local-development.md](docs/v10/local-development.md).

## Deploy
| Part | Guide |
|---|---|
| Frontend (Vercel) | [vercel-deployment.md](docs/v10/vercel-deployment.md) |
| API (VM + Docker + Caddy/Tunnel) | [backend-deployment.md](docs/v10/backend-deployment.md) |
| Workers | [worker-deployment.md](docs/v10/worker-deployment.md) |
| Neon | [database-operations.md](docs/v10/database-operations.md) |
| Cloudflare R2 | [storage-operations.md](docs/v10/storage-operations.md) |
| Google OAuth and SMTP | [backend-deployment.md](docs/v10/backend-deployment.md), [security-review.md](docs/v10/security-review.md#smtp-sender-authentication-for-the-provider-you-choose) |
| Backups | [backup-and-recovery.md](docs/v10/backup-and-recovery.md) |
| Monitoring | [monitoring.md](docs/v10/monitoring.md) |
| Go-live checklist | [release-readiness.md](docs/v10/release-readiness.md#production-deployment-checklist) |

## Tests and evidence
```bash
PYTHONPATH=".:tests" python -m pytest -q                         # SQLite (Windows: ".;tests")
CC_TEST_DATABASE_URL=postgresql://… PYTHONPATH=".:tests" python -m pytest -q
python qa/browser_qa_v10.py                                      # split-origin browser QA (compose stack)
python qa/load_10_orgs.py --api http://localhost:8000            # 10 organizations, concurrent, isolation probes
python -m benchmark.run_3d_benchmark --seeds 8 9 10              # 3D, fresh held-out seeds
node frontend/build.mjs && node frontend/check.mjs
python qa/secret_scan.py
```
Observed results: [docs/v10/test-evidence.md](docs/v10/test-evidence.md).

## Documents
**V10:**
- [release readiness](docs/v10/release-readiness.md) · [audit](docs/v10/repository-audit.md) · [architecture](docs/v10/architecture.md) · [deployment architecture](docs/v10/deployment-architecture.md)
- [multi-tenant readiness](docs/v10/multi-tenant-readiness.md) · [3D tolerance](docs/v10/3d-tolerance.md) · [security](docs/v10/security-review.md) · [data protection](docs/v10/data-protection.md) · [incidents](docs/v10/incident-response.md)
- [limitations](docs/v10/known-limitations.md) · [technical debt](docs/v10/technical-debt.md)

**Earlier:** [V9](docs/v9/) · [V8](docs/v8/) · [V6](docs/)

Only upload media you own or have explicit permission to process and license.
