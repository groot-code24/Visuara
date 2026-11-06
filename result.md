# Visuara: repository analysis

**Date:** 5 November 2026. **Code analysed:** the working tree on commit `3bf569f`, with 25 modified and 45 new files, none of them committed yet.
**Purpose:** what has been built, what broke and was fixed, what is still broken or missing, and the order to work in next.

---

## 1. Bottom line

- **The product works end to end on a local machine.**
  - A brand and a publisher complete the whole marketplace in a real browser: sign-up, video analysis, listing, reservation, request, approval, quote, payment, production with a real render, delivery and acceptance.
  - Every result below was produced by running the code; none is estimated.
- **It is not live, and not yet safe to go live.**
  - The database password is in the public GitHub history (B-1).
  - The live API has never started, because no settings have been imported into Vercel (B-2).
  - Google sign-in is rejected on the live address (B-3).
  - Only the owner can clear these three, and none needs a code change.
- **Four product gaps remain:** no object detection, payment recorded by hand, accuracy measured only on synthetic video, and the Vercel containers not rehearsed on the new code.

| Check (run today unless stated) | Result |
|---|---|
| Backend tests, SQLite (182) | 180 passed, 1 skipped (needs PostgreSQL), 1 failed. The failure was an outdated test expectation, fixed; the file now passes 19/19 |
| Backend tests, PostgreSQL 16 (182) | 181 passed, 1 failed (the same test, fixed). The rerun couldn't finish because the local PostgreSQL in Docker stopped responding (B-9). The fixed test doesn't use a database |
| Marketplace browser walk | **12/12** |
| Original product browser walk | **12/12** |
| Web production build as Vercel runs it (`VERCEL=1 next build`) | Passes: 30 routes, all pages rendered per request |
| Ruff lint (the CI command), `compileall`, `tsc --noEmit` | Pass |
| `npm audit --audit-level=high` | 0 vulnerabilities |
| `pip-audit -r requirements.txt` | No known vulnerabilities |
| Secret scan of the current files | 217 files, 0 findings |
| Secret scan of the whole git history | 2 findings, both the same leak (B-1): `.env.vercel.example` line 6 in commit `c37197a` |
| Vercel container rehearsal (`qa/vercel_rehearsal.py`) | **Not run** on the new code: Docker does not respond on this machine (B-9) |

Logs and screenshots: [docs/qa/evidence/](docs/qa/evidence/).

---

## 2. What the system is

| Part | Technology | Where |
|---|---|---|
| Web app | Next.js 16, React 19, TypeScript; 26 pages | `web/` |
| API | FastAPI, Python 3.11+ (3.12 in Docker and CI); 38 modules, about 9,000 lines; 151 endpoints | `app/` |
| Background jobs | Leased, resumable job runner; in production it is a second container called over HTTP | `app/jobrunner.py`, `worker.py` |
| Database | PostgreSQL (Neon) in production, SQLite locally, one SQL layer for both; 15 ordered, checksummed migrations | `app/db.py`, `app/migrations.py` |
| Media | Vercel Blob or S3 in production, local disk in development | `app/storage.py`, `app/blobstore.py` |
| Video | FFmpeg and OpenCV: analysis, surface detection, tracking, compositing, frame-by-frame validation | `app/intelligence.py`, `app/decisions.py`, `app/compositor.py`, `app/quality.py`, `app/validation.py` |
| Hosting | One Vercel project, three services: `web`, `api` and `runner` (the last two from `Dockerfile.vercel`) | `vercel.json` |
| CI | GitHub Actions on every push to `main`: lint, tests on SQLite and PostgreSQL, dependency and secret scans, web build, image build and smoke test | `.github/workflows/ci.yml` |
| Operations | Migrate, back up, transfer the database, demo environment, platform administrator, plans | `ops/` |

---

## 3. What was built

Status words:
- **Verified** means automated tests on both databases plus a real browser run.
- **Tested** means automated tests only.
- **Partial** means it works but does less than the specification asks.
- **Blocked** means it waits on something outside the code.

### 3.1 Platform foundations
| Feature | Status |
|---|---|
| Ordered, checksummed migrations (0001–0015), with upgrades of databases that already hold data | Verified |
| Organizations with kinds (brand, publisher, agency, direct), memberships, roles, switching the active organization | Verified |
| Sign-in by emailed code or magic link, and by Google; no passwords | Verified (Google against a local stand-in only, B-3) |
| Choosing the workspace kind at sign-up | Verified |
| Invitations for existing accounts; per-organization suspension and removal | Tested |
| Platform administration, granted only from the command line (`ops/platform_admin.py`) | Verified |
| Outbox for domain events | Verified |

### 3.2 Videos and analysis
| Feature | Status |
|---|---|
| Upload with SHA-256 per version, duplicate refusal, metadata read from the file, mislabelled files refused | Tested |
| New versions of a video, keeping the earlier versions and analyses | Tested (API only, no web control) |
| Deleting a video from the Library; a video in use is refused | Verified |
| Staged background analysis: leases, resume after a timeout, retries, cancellation | Verified |
| Immutable analysis versions with their own scenes and frames | Verified |
| Scene segmentation from colour histograms, robust to lighting drift | Verified (precision 1.00, recall 0.50, synthetic footage) |
| Surface opportunities, followed over time by re-detection (IoU ≥ 0.5) | Verified, plain surfaces only (see 5.2) |
| Versioned scoring with a breakdown on every opportunity | Verified |
| Scene understanding | **Partial**: measured attributes only (motion, brightness, colourfulness); descriptions need a vision model |
| Object detection and tracking | **Blocked** (B-6): the stages report `UNAVAILABLE` with the reason and never guess |

### 3.3 Marketplace
| Feature | Status |
|---|---|
| Brand and publisher profiles; rights records with evidence checked by content; administrator verification | Verified |
| Brand-safety review of inventory | Verified |
| Product catalogue with asset versions; brand-to-scene recommendations across organizations | Verified |
| Inventory listing with a price; reservations enforced by the database (409 on conflict, holds that expire) | Verified, including concurrent reservations |
| Campaigns with an audited lifecycle, products and targeting | Verified |
| Placement requests with two-party approval | Verified |
| Production through the existing placement engine: quality check, approval, render, validation | Verified (147 s for a real render in the browser walk) |
| Quotes, invoices, append-only double-entry ledger, revenue split, `Idempotency-Key` | Verified (the trial balance balances to the unit) |
| Payment | **Sandbox**: an administrator records a bank transfer with a reference (B-4) |
| Delivery with evidence, signed brand downloads, acceptance | Verified |
| Campaign reports from persisted events | Verified |
| Programmatic buying | **Designed only**: the API labels each capability `DESIGNED_ONLY`, and the adapters raise `NotConnected` |
| Resettable demonstration environment (`python -m ops.demo reset`) | Verified |

### 3.4 Security
| Control | Status |
|---|---|
| Tenant isolation, roles and organization kinds, enforced in the API and tested on both databases | Tested |
| CSRF and origin checks, secure cookies, short-lived signed media links | Verified |
| Content-Security-Policy with a fresh nonce per request and `'strict-dynamic'`; no inline scripts | Verified (the browser walk checks 4 pages) |
| Production guard: the API refuses to start in production when a required setting is missing or is a development stand-in | Tested |
| Secret scanner over files and the whole history; it reports file, line and kind, never the value | Tested |
| `qa/check_env.py`: refuses a database password or secret that appears anywhere in the git history | Tested |
| `.env`, `.env.*` (except `.env.example`), `data/` and `demo-data/` ignored by git | Checked with `git check-ignore` |
| Pre-commit hook running the secret scan (`.githooks/pre-commit`) | Written, **not enabled** (run `git config core.hooksPath .githooks`) |

---

## 4. What broke and was fixed during the build

| # | Problem | Effect | Fix |
|---|---|---|---|
| 1 | Deferred foreign-key checks were left pending after a backfill, so PostgreSQL refused `ALTER TABLE` | Upgrading a database that already held data failed | `migrations.apply` checks the constraints after each migration |
| 2 | Restoring a backup failed on tables that migrations seed | Backups could not be restored on PostgreSQL | `ops/dbtransfer` clears the seeded tables before importing; two round-trip tests cover it |
| 2a | Outbox delivery left work behind, and the runner kept restarting itself | Events delivered late or not at all | Delivery loops over claim rounds while its time budget lasts |
| 3 | Signed media checked the role in the wrong organization | Possible access across organizations | The role is checked in the media's own organization |
| 4 | Decisions logged events in the creator's default organization | Events in the wrong tenant | Logged in the active organization |
| 5 | Brand download links didn't re-check the role | A removed member could still download | Checked on every download |
| 6 | Automatic cancellations were not logged as transitions | Gaps in the audit trail | Logged |
| 7 | Exposure drift of ±8% read as a scene cut (precision 0.33) | False scenes, unstable opportunities | A cut must change both the raw and the gain-normalised histogram (`cut_gain_normalise`) |
| 8 | The secret scanner printed the first characters of each match | It leaked part of the secret it found | It reports file, line and kind only; the test checks no fragment appears |
| 9 | The CSP allowed inline scripts | Weaker protection against cross-site scripting | Per-request nonce, `'strict-dynamic'`, pages rendered per request |
| 10 | New sign-ups always became `direct` workspaces | New brands and publishers couldn't reach the marketplace without finding a hidden setting | Sign-up asks what the workspace does |
| 11 | Campaign reports read events that nothing had delivered yet on Vercel | Stale reports | The report delivers pending events first |
| 12 | No way to delete a video in the web app | API only | Library Delete, with confirmation and the reason when refused |
| 13 | The account box read "… · Agency", where Agency was the plan's name | Misleading | Now reads "… · Agency plan" |
| 14 | The evaluation script crashed writing NumPy integers to JSON | No evaluation report | Values are converted to plain numbers |
| 15 | The secret-scanner test still expected the old leaking output (today's release run) | 1 failing test on each database | Test updated to the new contract; the change is recorded in the regression log |

Every regression and changed test expectation is recorded in [docs/execution/REGRESSION_LOG.md](docs/execution/REGRESSION_LOG.md). All 115 tests from before the build still pass. Two of their expectations were changed, each for a recorded reason.

---

## 5. What is still broken or missing

### 5.1 Blocks going live (owner only, no code change)
| ID | Problem | What to do |
|---|---|---|
| **B-1 (critical)** | The Neon database password is in commit `c37197a` (`.env.vercel.example`, line 6), which is on GitHub. Anyone who has seen the repository can reach the database | Reset the role's password in Neon, put the new connection string in `.env.vercel`, and run `python qa/check_env.py`, which refuses a password found in the history. Make the repository private. Rewriting history is optional and cannot make the old password secret again |
| **B-2 (critical)** | The live API does not start: Vercel's log says `DATABASE_URL is not set` | Import `.env.vercel` into the Vercel project for Production and redeploy ([docs/vercel.md](docs/vercel.md), step 5) |
| **B-3 (high)** | Google answers `redirect_uri_mismatch` for `https://www.visuara.si/auth/google/callback` | Add that exact address to the OAuth client in Google Cloud Console |
| Sign-in email | Production refuses the file outbox. If Google is the only sign-in method and B-3 is open, nobody can sign in | Configure SMTP (`CC_MAIL_BACKEND=smtp`, `CC_SMTP_*`, `CC_MAIL_FROM`; Resend works) so email sign-in works regardless of Google |
| Contact address | `NEXT_PUBLIC_CONTACT_EMAIL` isn't set (the build warns), so "Talk to us", upgrade links and the legal pages have no address | Set it for the `web` service in Vercel |
| B-5 | Vercel's free plan forbids commercial use | Vercel Pro before charging real customers |
| B-8 | Terms, privacy, rights and payout wording have had no legal review | A lawyer, before commercial launch |

### 5.2 Product gaps (engineering)
| Gap | Why it matters | Blocked by |
|---|---|---|
| **No object detection or tracking** | The specification's analysis stages 4–5 are missing; opportunities come from surfaces only | B-6: a commercially licensed model that fits 1 vCPU, 2 GB and 300 s |
| **Textured surfaces aren't found**: the poster was found in 3 of 42 textured tracking clips | Posters, screens and framed pictures are common placement surfaces | B-6, or feature-based plane fitting, plus B-7 |
| **Accuracy only on synthetic footage** | No evidence yet for real videos | B-7: licensed real footage |
| **Payment recorded by hand** | No card or bank capture; an administrator marks invoices paid | B-4: a payment provider (the ledger, invoices and idempotency are ready for an adapter) |
| Scene descriptions | Only measured attributes, no "what is in the scene" | An evaluated vision model (`CC_VISION_MODEL`) |
| Programmatic buying | Designed and labelled, not connected | Exchange and SSP/DSP partners |
| No web control to upload a new version of a video | Available through the API only | — |

### 5.3 Not verified (could be broken; nobody has run it)
| What | Why not run | How to verify |
|---|---|---|
| The Vercel containers with the Phase 2–15 code | Docker does not respond on this machine (B-9) | Restart Docker Desktop, then `python qa/vercel_rehearsal.py`. CI's `image` job also builds and smoke-tests the image on the first push |
| The PostgreSQL rerun of `tests/test_platform.py` after today's test fix | The local PostgreSQL in Docker stopped (B-9) | Restart Docker and run that file with `CC_TEST_DATABASE_URL` set. CI runs it on PostgreSQL on push |
| The live deployment as a whole | B-2 | `python qa/vercel_check.py https://<your-domain>` after deploying |
| The nonce CSP on Vercel preview deployments | Not deployed. Vercel's preview toolbar injects its own script, which `'strict-dynamic'` may block | Open a preview deployment and check the browser console. Production deployments don't load the toolbar |
| Load and long videos | No load test; analysis measured only on short clips (median 0.49 s per second of video, on 8 threads, not Vercel's 1 vCPU) | A load test and a long-video run on the deployed runner |
| External penetration test | Not done | Before handling real money |

### 5.4 Technical debt
Full list: [docs/audit/TECHNICAL_DEBT.md](docs/audit/TECHNICAL_DEBT.md).

| Debt | Severity |
|---|---|
| Legacy `POST /api/videos/{vid}/analyze` still runs inside the request (debt 20) | Low |
| The legacy `frames` table is still refilled after each analysis (19) | Low |
| No ESLint configuration in `web/` (21) | Low |
| Mixed error shapes in the older routes (10) | Low |
| Rate limits stored in the database (12) | Low |
| Plan prices are whole dollars; marketplace money is already integer minor units (13) | Low |
| On PostgreSQL every pending migration runs in one transaction (22) | Low |
| Other outbox consumers wait for the next runner request in HTTP mode; none exists yet (18) | Low |
| `app/main.py` still holds the pre-marketplace routes (1,231 lines) | Low |
| The README describes only the original product, not the marketplace | Low |
| The `Dockerfile.vercel` header mentions a `Dockerfile` that no longer exists | Cosmetic |

---

## 6. Before you push to GitHub

1. **Clear B-1 first.** Reset the Neon password before anything else, because a push to `main` can start a Vercel deployment.
2. **Commit all 70 changed files together.** `app/main.py` imports the new modules (`campaigns`, `catalogue`, `commercial`, `delivery`, `intelligence`, `inventory` and others), and the web pages import `web/lib/csp.ts`. A partial commit fails CI and the deployment.
3. **Check that nothing secret is staged:**
   ```bash
   git status --short | grep -E "\.env|data/|demo-data/"      # only .env.example may appear
   python qa/secret_scan.py                                    # expect: 0 findings
   ```
4. **Optional:** `git config core.hooksPath .githooks` runs the secret scan before every commit.
5. **After pushing, watch the CI jobs:** `lint`, `test-sqlite`, `test-postgres`, `security`, `web` and `image`. The `image` job is the first build of the Vercel container with the new code (see 5.3).

## 7. Going live, in order

1. Clear B-1 (above). Then put the new connection string in `.env.vercel`, plus SMTP, `CC_PUBLIC_URL`, `CC_SIGNING_KEY`, Blob, Google and `NEXT_PUBLIC_CONTACT_EMAIL`.
2. `python qa/check_env.py`: every line must pass. It also tests the SMTP login and refuses any secret found in the history.
3. Import `.env.vercel` into Vercel for Production (B-2). Add the Google return address (B-3).
4. Migrate the production database: `DATABASE_URL=… python -m ops.migrate`.
5. Deploy, then `python qa/vercel_check.py https://<your-domain>`.
6. Sign in once yourself, then grant yourself platform administration: `python -m ops.platform_admin grant <your-email>`.
7. By hand: email sign-in, Google sign-in, upload and analyse a short video, and the demo flow in [MVP_RELEASE_REPORT.md](docs/release/MVP_RELEASE_REPORT.md) §20–21.

The full checklist is in [docs/deployment/DEPLOYMENT_GUIDE.md](docs/deployment/DEPLOYMENT_GUIDE.md).

## 8. What to build and fix next (engineering, by priority)

| # | Work | Why first | Size |
|---|---|---|---|
| 1 | Get Docker working, run `qa/vercel_rehearsal.py` and the PostgreSQL rerun | Closes the only unverified paths into production | Small |
| 2 | Watch the first live deployment; fix anything `qa/vercel_check.py` reports | The live site has never run | Small to medium |
| 3 | Update the README for the marketplace; fix the Dockerfile comment | It's the first thing visitors to the repository read | Small |
| 4 | Textured-surface detection (feature-based plane fitting), measured with `benchmark/evaluate_intelligence.py` | The largest gap in what the product finds today (3 of 42) | Medium |
| 5 | Evaluate a licensed CPU object-detection model inside 1 vCPU, 2 GB and 300 s (B-6) | Specification stages 4–5; unlocks object-anchored placements | Large |
| 6 | Real-footage evaluation with licensed clips (B-7): `benchmark/real` and `tests/test_real_footage.py` are ready | Evidence that accuracy holds outside synthetic video | Medium, once footage exists |
| 7 | Payment provider adapter, in test mode first (B-4), then Vercel Pro (B-5) | Needed before taking real money | Medium |
| 8 | Web control to upload a new video version | API and tests exist; only the button is missing | Small |
| 9 | ESLint for `web/`; retire the legacy analyze endpoint and `frames` table; normalise error shapes | Keeps the codebase maintainable | Small each |
| 10 | Load test, long-video timing on Vercel, external penetration test, legal review (B-8) | Before commercial launch | Medium |

---

## 9. Where things are

| Need | Document |
|---|---|
| Release status, by category | [docs/release/MVP_RELEASE_REPORT.md](docs/release/MVP_RELEASE_REPORT.md) |
| Every feature row and its evidence | [docs/execution/IMPLEMENTATION_LEDGER.md](docs/execution/IMPLEMENTATION_LEDGER.md) |
| Blockers and who clears them | [docs/execution/BLOCKERS.md](docs/execution/BLOCKERS.md) |
| Every test run, including failures | [docs/execution/REGRESSION_LOG.md](docs/execution/REGRESSION_LOG.md) |
| Browser walks, step by step | [docs/qa/END_TO_END_TEST_REPORT.md](docs/qa/END_TO_END_TEST_REPORT.md) |
| Accuracy measurements | [docs/qa/AI_EVALUATION_REPORT.md](docs/qa/AI_EVALUATION_REPORT.md) |
| Security model | [docs/security/SECURITY_MODEL.md](docs/security/SECURITY_MODEL.md) |
| Deployment | [docs/vercel.md](docs/vercel.md), [docs/deployment/DEPLOYMENT_GUIDE.md](docs/deployment/DEPLOYMENT_GUIDE.md) |
| Decisions and their reasons | [docs/execution/DECISION_LOG.md](docs/execution/DECISION_LOG.md), [docs/architecture/DECISIONS.md](docs/architecture/DECISIONS.md) |

**Running the tests locally** (Windows Bash, FFmpeg on `PATH`):
```bash
PYTHONPATH=".;tests" .venv311/Scripts/python -m pytest -q                       # SQLite
CC_TEST_DATABASE_URL=postgresql://cc:cc-local-only@127.0.0.1:5433/postgres \
  PYTHONPATH=".;tests" .venv311/Scripts/python -m pytest -q                     # PostgreSQL (docker compose up -d)
```
Without FFmpeg on `PATH`, the media tests skip or fail and `/api/health` answers 503. That is intended: the API reports itself unhealthy when it cannot render.
