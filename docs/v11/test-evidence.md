# V11 test evidence

Observed on 30–31 October 2026 on Windows 11. Environment:
- **Host:** Python 3.11.0 (`.venv311`), FFmpeg 8.1, Node 24.14.1 and npm 11.11.0, headless Chrome.
- **Docker:** Docker 29.5.2 (Linux VM: 8 CPUs, 3.5 GB RAM).
- **Web app:** Next.js 16.4.2, React 19.3.1, Tailwind 4.3.3, TypeScript 6.0.3.

Stand-ins:
- PostgreSQL 16 for Neon;
- the SeaweedFS S3 gateway for Cloudflare R2;
- Mailpit (real SMTP) for the email provider;
- a placeholder OAuth client for Google's redirect, plus a stubbed token exchange and locally generated RSA keys in the tests;
- `next start` and the Docker web image for Vercel.

**No live provider was used.** Every number below was observed in a run of the final code, unless a row says otherwise.

## Automated backend tests
| Command | Observed |
|---|---|
| `PYTHONPATH=".;tests" pytest -q` (SQLite) | **80 passed**, 1 warning, 608.7 s (31 October) |
| Same with `CC_TEST_DATABASE_URL` → PostgreSQL 16 (compose, database `cc_test`) | **80 passed**, 1 warning, 777.0 s (31 October) |
| `tests/test_decisions.py` alone, run after the consistency fix | 7 passed, 199.9 s |

The warning is Starlette's test-client deprecation notice.

**A defect found by the first PostgreSQL run, and fixed.** That run gave 73 passed and 2 failed:
- **Cause:** `GET /api/videos` used `MIN(second) second` as a column alias. SQLite accepts that, but PostgreSQL requires `AS` before a keyword used as an alias. The video list, and so the web app's Library page, failed on PostgreSQL.
- **Fix:** the alias is now `AS first_second` (`app/main.py`).
- **Checks:**
  - the two failing tests (`test_direct_upload_complete_mismatch_cancel_and_expiry` and `test_overlay_workflow_render_download_and_takedown`) then passed on PostgreSQL;
  - on the rebuilt Docker stack, the video list, its thumbnails and every read endpoint the dashboard calls return 200.
- **Why earlier runs missed it:** browser QA runs on SQLite, and the load test never fetches the video list.

What the suite covers:

| File | Coverage |
|---|---|
| `test_auth.py` | email code and magic link; magic-link confirmation needs an explicit click and works once; the token sits in the URL fragment; Google PKCE, state, nonce and RS256 checks (tampered, wrong key, `alg: none`); Google error redirects; rate limits; CSRF; sign-up and workspace naming |
| `test_decisions.py` | surface detection and orientation; product × video assignment (exact, spread cap, deterministic ties); plan limits on videos and decisions; cancel; apply → placement; **every option's verdict equals the quality check of the placement it creates** |
| `test_workflow.py` | overlay and tracked placement workflows; lifecycle transitions; quality fixtures; tamper detection and takedown; cancel, lease expiry and dead letter |
| `test_platform.py` | member roles, invitations, suspension and transfer; plans and their 402 limits; quotas; health split; direct uploads; operator metrics; plan CLI; log redaction; production guard |
| `test_e2e_processes.py` | real uvicorn plus a separate `worker.py` over HTTP: sign-up by emailed code, a decision on the worker, apply, reject, correct, approve, render, validated download, edit → invalidation, and worker kill → lease recovery |
| `test_assets_3d.py`, `test_storage.py`, `test_dbtransfer.py`, `test_real_footage.py` | 3D asset path, S3 storage, SQLite → PostgreSQL transfer, and benchmark tooling (on clips marked synthetic) |

## Sign-in and white hero (31 October 2026, final code)
| Check | Observed |
|---|---|
| `qa/browser_qa_web.py` | **12/12**, no page errors. Includes the hero being pure white and fitting 320, 390, 768, 1024 and 1440 px, and the whole Google flow through the web app's proxy: new person → onboarding → dashboard, returning person → dashboard, cancelled, forged callback |
| `qa/browser_qa_stack.py` | **8/8**, 0 CSP violations |
| `next dev` on :3000 with the API on :8000 and no email provider, in Chrome, on `localhost` and on `127.0.0.1` | Magic link → onboarding → dashboard on both, no page errors |
| The existing local database and account (on a copy, then live) | Signs in, reaches the dashboard |

**Google is tested against a local stand-in (`qa/google_stub.py`), not real Google.** The stand-in checks the client id, redirect URI and PKCE, and it signs a real RS256 token, so our side of the flow is exercised. A real Google sign-in has never happened: it needs an OAuth client that only the account owner can create. **Real email has also never been sent:** it needs an SMTP provider. Without one, the sign-in page shows the link and code itself, in development only.

## Pre-deployment audit (31 October 2026)
| Check | Observed |
|---|---|
| `qa/browser_qa_stack.py`: Chrome against the compose stack (PostgreSQL, S3 gateway, SMTP, separate workers) | 8/8, 0 CSP violations: security headers and SEO files; sign-up over SMTP; **browser video upload straight to the bucket**; decision on a worker; previews and thumbnails through presigned redirects; check, approval, render, download through storage; API stopped (polite error page, marketing pages stay up, session recovers) |
| Clean-copy build as Vercel would run it: only files that would be committed, `npm ci`, `VERCEL=1 VERCEL_ENV=production` | Fails with a clear message when `CC_API_ORIGIN` is missing or `http://`; builds with the right values, and the rewrites point at the configured API. Run on Windows, not Vercel's Linux; the lockfile lists the Linux packages |
| `npm audit`, `pip-audit -r requirements.txt`, `qa/secret_scan.py`, `ruff`, `tsc` | 0 vulnerabilities, none known, 178 files with 0 findings, clean, clean |

Timings on a 5-minute 1080p synthetic clip (8.6 MB, mostly static) through the web proxy, on the compose stack, with other work running for part of the time:

| Step | Observed |
|---|---|
| Upload to the bucket / complete the upload | 0.8 s / 3.6 s |
| Decision for this one video (1 product) | 211.9 s |
| Quality check, 5-second placement | 51.9 s |
| Quality check, 60-second placement | Still running when the request answered 202 at the 90 s limit. The run was not timed to completion |

**Not repeated after the audit's changes:** the 10-workspace load test and the outage drill below ran on the code from 30 October, before the per-plan limits and the quality-check pool. Run them again before a real launch.

## Web app
| Check | Observed |
|---|---|
| `npm run typecheck` (tsc) | clean |
| `npm run build` (`next build`) | succeeded |
| `npm audit` | 0 vulnerabilities |
| Web Docker image (`web/Dockerfile`, standalone) | built and served the app in the compose stack |

## Browser QA (headless Chrome)
`python qa/browser_qa_web.py`: the real API (uvicorn, SQLite, email outbox) and `next start`, driven over the Chrome DevTools protocol. Result: **11/11**, no page errors. The 15 screenshots are in [`docs/qa/v11/`](../qa/v11/) and the log in `docs/qa/v11/browser_qa_web.json`.

| Step | Time |
|---|---|
| Landing and pricing pages | 3.8 s |
| Dashboard refuses unauthenticated and forged sessions (redirect to `/login?next=…`) | 2.7 s |
| Sign-up by emailed code, then onboarding | 3.5 s |
| Sign-in again by magic link (explicit click, single use) | 2.8 s |
| Two products and two videos added through the UI | 3.7 s |
| Decision engine: which product fits where | 65.5 s |
| Placement: quality check, approval, render, download | 27.5 s |
| Settings: plan usage and the Free plan seat limit | 1.4 s |
| Mobile layout (390 px) | 1.9 s |
| Google sign-in: OAuth redirect via the app origin (S256 PKCE), forged callback refused | 0.7 s |
| No page errors | — |

Defects the browser runs found, all fixed and covered by the run above:
- **Engine recommendations failed their own quality check.** The engine tracked unrounded corners. It now rounds exactly as stored placements are rounded, and a regression test covers it.
- **A placement page stayed on "Queued".** Polling only started on the first load. It now restarts whenever the data changes.

## Ten workspaces at once (Docker stack, through the web app)
`python qa/load_10_orgs.py --app http://127.0.0.1:3000 --database-url … --kill-worker context-commerce-worker-1`

Setup:
- compose stack: PostgreSQL, the S3 gateway, Mailpit, the API, 2 workers, the web app;
- `deploy/local/compose.loadtest.yml`, which raises only the per-IP sign-in limits, because every simulated user shares one IP;
- every request goes through the Next.js proxy, as a browser's would.

Evidence: [`evidence/load_10_orgs.json`](evidence/load_10_orgs.json).

| Measure | Observed |
|---|---|
| Workspaces / users | 10 / 30. Each workspace has an owner, plus an editor and a viewer who joined by emailed invitation |
| Requests / unexpected responses / 5xx | 3,572 / **0** / **0** |
| Uploads | 20 videos uploaded straight to the bucket (presigned PUT), 20 products |
| Decisions (10 at once, 2 products × 2 videos each) | **10/10 succeeded**, 20/20 videos assigned. Median 704.9 s and maximum 1,107.6 s from submission, queued behind each other on 2 workers |
| Placements | 10 applied from decisions; the quality check passed; an editor's approval was refused; the owner approved |
| Renders | **10/10 succeeded.** `worker-1` was killed 7.7 s into the render phase. 1 job was recovered after its lease expired and retried, and everything was done after 180.5 s |
| Downloads | 10 signed downloads; an editor was refused a download link |
| Isolation | **900** cross-workspace probes (reads and writes on another workspace's IDs), all refused, no leaks |
| PostgreSQL connections | at most 13 |
| Total | 1,418.3 s |

Latency through the proxy (p50 / p95):

| Request | p50 / p95 |
|---|---|
| `GET /api/decisions/{id}` (polling, n = 2,171) | 59 / 776 ms |
| `POST /api/auth/email/verify` | 431 / 766 ms |
| `POST /api/decisions` | 397 / 445 ms |
| `POST /api/decisions/{id}/apply` | 1,095 / 1,136 ms |
| `POST /api/placements/{id}/render` | 382 / 400 ms |
| `POST /api/auth/email/start` (sends SMTP) | 551 / 5,033 ms |
| `POST /api/placements/{id}/quality-check` (10 at once) | **49.6 / 55.3 s** |

The quality check runs inside the API request, so ten at once on one API container took about 50 s each. See [known-limitations.md](known-limitations.md).

**A defect found by the first run, and fixed.** The first attempt aborted on a dropped connection, and the web log showed `Failed to proxy … read ECONNRESET`:
- **Cause:** uvicorn closes idle keep-alive connections after 5 s by default, and the Next.js proxy sometimes reused a connection at that moment.
- **Fix:** uvicorn now runs with `--timeout-keep-alive 75` (Dockerfile, dev scripts, tests and QA), so the proxy always closes first.
- **Result:** the run above uses the fixed images.

## Outage drill (Docker stack)
`python qa/outage_drill.py --api http://127.0.0.1:8010`. Evidence: [`evidence/outage_drill.json`](evidence/outage_drill.json).

**11/11 checks passed** (run against the API directly):

| Outage | Observed |
|---|---|
| PostgreSQL stopped | liveness stays 200; readiness 503 `database=unavailable`; an authenticated request fails in 10 s with a generic 500 and a request id (no stack trace, connection string or hostname) |
| PostgreSQL restarted | readiness 200 again; the existing session is still valid |
| S3 gateway paused | readiness 503 `object_storage=unavailable`; upload authorisation is still issued (signing is local); upload completion is refused with a retryable 409 `UPLOAD_NOT_RECEIVED`, with no internals in the response |
| S3 gateway unpaused | readiness 200 again |
| SMTP paused | email sign-in fails with 502 `EMAIL_SEND_FAILED` and no internals |
| SMTP unpaused | email sign-in works again |

## Static checks
| Check | Observed |
|---|---|
| `ruff check .` | clean |
| `pip-audit -r requirements.txt` | no known vulnerabilities |
| Secret scan (`qa/secret_scan.py`) | 173 files, 0 findings |
| Docker images: API (`Dockerfile`) and web (`web/Dockerfile`) | both built. The compose stack started on the V10 PostgreSQL volume, migrated in place and reported ready (database `postgres`, storage `s3_compatible`, 2 workers) |

## Not verified
- **Live services:** Neon, Cloudflare R2, a real SMTP provider, a real Google sign-in (consent screen and token exchange) and a Vercel deployment. No accounts were available.
- **CI:** GitHub Actions has never run (no remote). The web job's steps (`npm ci`, audit, typecheck, build) and the image build were run locally.
- **Footage:** real creator footage has not been evaluated. All decision-engine results above come from synthetic room clips (`benchmark/fixtures_scene.py`).
- **Long videos:** long or 4K videos have not been timed.
