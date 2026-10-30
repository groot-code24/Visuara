# V10 test evidence

Observed on 30 October 2026 on Windows 11, with:
- **Python 3.11.0** (`.venv311`) on the host, FFmpeg 8.1;
- Docker Desktop 29.5.2 (Linux VM: 8 CPUs, 3.5 GB RAM);
- the image: Python **3.12.16**, FFmpeg **7.1.5**, OpenCV 4.11, Pillow 13, psycopg 3.3, boto3;
- headless Chrome.

Stand-ins: PostgreSQL 16 for Neon, SeaweedFS's S3 gateway (with access keys) for R2, Mailpit (real SMTP) for the email provider, a stubbed token exchange plus locally generated RSA keys for Google, `qa/vercel_emulator.py` for Vercel's router. **No live provider was used.**

## Baseline (before any V10 change)
| Command | Observed |
|---|---|
| V9 code copied unmodified to a scratch directory: `pytest -q` | **55 passed** in 250.6 s (SQLite) |

## Automated tests (final code)
| Command | Observed |
|---|---|
| `PYTHONPATH=".;tests" pytest -q` (SQLite) | **70 passed**, 1 warning, 496.6 s |
| Same with `CC_TEST_DATABASE_URL` → PostgreSQL 16 (Docker) | **70 passed**, 1 warning, 443.4 s (a fresh schema per test; API and worker processes share one schema in the multi-process test) |
| Earlier PostgreSQL run, under heavy parallel load (3D benchmark + Docker builds) | 68 passed, 1 failed: `test_cancel_mid_render_lease_recovery_and_dead_letter`. FFmpeg's pipe broke mid-render and the job was retried (correct behaviour), but the test expects the first attempt to succeed. The test passed in isolation on PostgreSQL and SQLite |

The warning is Starlette's test-client deprecation notice. New in V10, `tests/test_v10.py` (15 tests):
- member roles under load of every kind;
- suspension, removal and ownership transfer;
- invitations: revoke, expire, single use;
- quotas;
- health split and cross-origin protection;
- fair claiming and wake file;
- job timeout and shutdown hand-back;
- direct upload: complete, size mismatch, cancel, expiry, cross-org;
- server-upload fallback;
- cache eviction;
- payment ledger and operator API;
- Google RS256 verification (tampered, wrong key, `alg: none`, unknown key id);
- log redaction;
- production guard;
- secret-scanner detection.

## Browser QA (headless Chrome)
| Script | Setup | Observed |
|---|---|---|
| `qa/browser_qa.py` (V6) | own server, SQLite | **10/10** (script updated for the renamed invite button and the V10 default account type) |
| `qa/browser_qa_v8.py` | own server, SQLite | **6/6** (script fix: a stale toast was mistaken for the sign-in result) |
| `qa/browser_qa_v9.py` | own server, SQLite, email outbox | **6/6** |
| `qa/browser_qa_v10.py` | **split origin**: frontend `http://app.cc.localhost:3000` (Vercel build via emulator), API `http://api.cc.localhost:8000` (container on PostgreSQL + S3 gateway + Mailpit + 2 workers) | **10/10**: security headers; brand sign-up by emailed code over real SMTP; invitation emailed; viewer joins by magic link (API page → app origin); viewer refused on writes; owner suspends the viewer; creator **direct upload from the browser to storage**; thumbnails via presigned redirects; Google start (PKCE, state, nonce, API callback); mobile 390 px. Screenshots in `docs/qa/v10/` |

Defects found by the browser runs and fixed:
- settings showed "SQLite / local disk" on a PostgreSQL + S3 deployment;
- the workspace name was missing after onboarding;
- the preview and landing-page requests ignored the API origin;
- share links used `CC_PUBLIC_URL` instead of the browser's API base.

## Ten-organization concurrency
`python qa/load_10_orgs.py --api http://127.0.0.1:8000 --database-url … --kill-worker context-commerce-worker-1` (compose stack + `deploy/local/compose.loadtest.yml`, which raises only the per-IP sign-in limit, because every simulated user shares one IP).

| Measure | Observed (final code) |
|---|---|
| Organizations / users / parallel clients | 15 (10 brand + 5 creator) / 40 / 10 |
| Requests / unexpected responses | 1,535 / **0** |
| Quality checks passed / placements ready for render | 10/10 / 10/10 |
| Renders succeeded / downloads verified | **10/10** / **10/10** |
| Worker SIGKILL drill | killed 3.1 s after queueing while 2 renders ran; the affected job was recovered after its 120 s lease and **succeeded on attempt 2**; no job ran twice to completion |
| Render queue delay (median / max) | 17.5 s / 154 s (the max includes the 120 s lease expiry after the kill) |
| Render time per job (median / max) | 2.5 s / 7 s; all 10 jobs finished 159 s after queueing |
| Cross-tenant probes refused / leaks | **720 / 0** |
| Editor download attempt | 403 for all 10 (role check under load) |
| PostgreSQL connections (max / median) | 12 / 11 |
| Whole run | 213 s |

API latency under this load (ms, p50 / p95):

| Endpoint | p50 | p95 |
|---|---|---|
| GET /api/jobs (n=520) | 15 | 27 |
| GET /api/jobs/{id}/download (editor refused) | 74 | 93 |
| GET /api/marketplace | 936 | 1,003 |
| GET /api/media/{token} (incl. presigned redirect) | 582 | 1,316 |
| GET isolation probes (n=630) | 119 | 158 |
| POST /api/auth/login | 279 | 567 |
| POST /api/auth/register | 226 | 759 |
| POST /api/campaigns | 233 | 265 |
| POST /api/invitations | 368 | 1,014 |
| POST /api/offers/{id}/accept | 179 | 197 |
| POST /api/placements/{id}/quality-check | 11,642 | 12,417 |
| POST /api/videos (upload + FFprobe) | 2,974 | 3,142 |
| POST /api/videos/{id}/analyze | 4,792 | 5,133 |
| POST /api/placements/{id}/render | 290 | 302 |

Harness note: on Windows, `localhost` first tries IPv6 and adds about 2.1 s to every new connection (measured: 2.1 s vs 0.04 s for `127.0.0.1`). The first run's latencies were distorted by this; the reported run uses `127.0.0.1`.

## Infrastructure drills (Docker stack)
| Drill | Observed |
|---|---|
| **Backup → verify → restore** (`ops/backup.py` inside the API container, after the 10-organization workload) | 242 media files and 1,770 rows backed up; hashes verified; restored into a fresh PostgreSQL database: **29/29 tables, 1,770/1,770 rows, identical counts per table**; a second restore into the now non-empty database was **refused** |
| **Outage drill** (`qa/outage_drill.py`) | **11/11** (details below) |
| **Storage privacy and URLs** (`ops.storage_setup --apply`) | CORS and lifecycle applied; round trip; anonymous read refused; presigned GET works; presigned expiry enforced |
| **Idle database activity** (`qa/idle_db_activity.py`, 32 min, app idle, the compose database health check moved off the app database) | **6 app sessions in 32 minutes**: one per worker at about 4.5, 8.5 and 17 minutes, following the backoff ramp. Each connection closed about 90 s after use. **No connection at all from minute 18.5 to the end of the window (≈ 13.5 min)**, the next poll being due at the 15-minute steady state. Two corrections were made along the way: transaction counters were discarded as a measure (autovacuum produces about 20 per minute with the app stopped), and the compose database health check was moved off the app database (it had opened about 20 sessions per minute). A Neon dashboard check is still required |

Outage drill details:
- **Database stopped:** liveness stays 200; readiness 503 (`database: unavailable`); an authenticated request gets a generic 500 with a request id within 10 s.
- **Database recovery:** readiness 200 again, and the existing session still works.
- **Storage paused:** readiness 503 (`object_storage: unavailable`); upload completion returns a retryable 409 without internals; then recovery.
- **SMTP paused:** email sign-in fails with 502 `EMAIL_SEND_FAILED` and no internals; then recovery.

The drill found three defects, all fixed:
- requests hung indefinitely when the database went down (no connect or pool timeouts);
- the storage probe crashed on timeouts;
- upload completion could hang for minutes because storage metadata calls used the long-transfer client.

A database that freezes **without** dropping connections (simulated with `docker pause`) still holds requests until the reverse proxy times out. That case is listed in known-limitations.md.

## Benchmarks (synthetic)
| Command | Observed |
|---|---|
| `python -m benchmark.run_benchmark --split all` | Identical to V9 in every decision and metric (timings excluded) |
| `python -m benchmark.run_2d_quality --seeds 2 3 4` | Identical to V9 in every quality metric; only compositing speed differs (machine under load) |
| `python -m benchmark.run_3d_benchmark --seeds 0 1 --no-render` (dev) | 24/24 correct, no decision changed vs the V9 tolerance |
| Diagnosis `topdown_user_fov` s5 (the V9 failure) | V9 FAILED → V10 PASSED; occluder clips of that seed still rejected |
| `python -m benchmark.run_3d_benchmark --seeds 8 9 10` (**fresh held-out**, code frozen at `6d672e4`) | **36/36 correct, 0/18 false accepts, 0/18 false rejects, 18/18 renders validated**; the V9 tolerance would also have scored 36/36. See [3d-tolerance.md](3d-tolerance.md) |

**0 real clips evaluated.**

## Security scans
| Scan | Observed |
|---|---|
| `pip-audit` (`.venv311`) and `pip-audit -r requirements.txt` | No known vulnerabilities |
| `pip-audit` inside the image | First build: 12 advisories, all in `pip` 25.0.1 from the base image. After upgrading pip in the Dockerfile: **no known vulnerabilities** (pip 26.2.2) |
| `python qa/secret_scan.py` | 0 findings in the repository (the scanner's own detection is unit-tested) |
| `ruff check` (defect rules) | All checks passed |
| `node frontend/check.mjs` | Inline scripts parse; build output has the security headers and no secret-like values |
| Production guard | `CC_ENV=production` with development settings refuses to start and lists all six problems |

## End-to-end scenarios
| Scenario | Where verified |
|---|---|
| A Brand registration, email verification, organization, collaborator invitation | browser QA V9 + V10 (real SMTP), load test |
| B Creator registration, workspace, upload, listing | browser QA V8 + V10 (direct upload), load test |
| C Campaign, inventory search, offer, acceptance | browser QA V8, load test |
| D Licensing terms and licence record | browser QA V8, tests (grants), load test |
| E Product, placement, quality report | browser QA V6/V8, load test (10 quality checks passed) |
| F Creator inspects exact version, consents, approves | browser QA V6/V8, load test |
| G Worker renders, validates every frame, keeps audio, uploads | tests (V8 audio/validation), load test (containerised workers, 10/10) |
| H Secure short-lived download | browser QA V8 (signed share link), load test (10 downloads via presigned redirect) |
| I Revocation stops use and delivery | tests (`test_tampered_track_and_revocation…`, V8 grant revocation) |
| J Cross-tenant access denied | tests + 720 load-test probes, 0 leaks |
| K Worker interrupted, recovered by another worker | tests (lease recovery, shutdown hand-back) + load test SIGKILL drill |
| L Database, storage and SMTP outages controlled and recoverable | outage drill 11/11 |
| M 10 organizations concurrently | load test |

## Not executed
- **Live services:** Neon, R2, the SMTP provider, Google OAuth (a real authorisation and callback) and a Vercel deployment.
- **CI:** GitHub Actions (no remote).
- **Platforms:** arm64 image; a production VM.
- **Real footage.**
- **An independent penetration test.**
