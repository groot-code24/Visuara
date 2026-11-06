# Visuara MVP release report

> **Superseded.** This is the record as of before V4.0 M14. The current state is in [docs/engineering/FINAL_RELEASE_REPORT.md](../engineering/FINAL_RELEASE_REPORT.md) and, for the intelligence pipeline, [docs/engineering/AI_EVALUATION.md](../engineering/AI_EVALUATION.md).

**Date:** 5 November 2026. **Code:** the working tree on commit `3bf569f`, which is not yet committed: the owner commits and pushes.

## Verdict
**The MVP works end to end on a local machine, and it is not production-ready.**
- A publisher and a brand complete the whole marketplace in the browser: analysis, listing, reservation, request, approval, quote, payment, production with a real render, delivery and acceptance.
- **Not production-ready:**
  - The live deployment does not start (B-2), and its database password is public (B-1).
  - Object detection is unavailable (B-6).
  - Payment is recorded by hand, not captured (B-4).
  - Accuracy has only been measured on synthetic footage (B-7).
- **Tested on the final code:** 182 backend tests on each database. One failed on both, an outdated expectation in the secret-scanner test; it was fixed and the file re-run passes on SQLite. The test reads no database; the PostgreSQL re-run could not complete because local PostgreSQL stopped ([REGRESSION_LOG.md](../execution/REGRESSION_LOG.md)). Every other test passed (SQLite skips the one PostgreSQL-only test, as in the baseline). Both browser walks passed, 12/12 each. No regressions against the baseline.

## Status at a glance
| Category | What |
|---|---|
| **Implemented and verified** (automated tests on SQLite and PostgreSQL, plus a real end-to-end run) | Organizations with kinds, memberships, switching and platform administration; staged asynchronous analysis with immutable versions, scenes and frames; scene segmentation; surface opportunities with versioned scoring; brand-safety review; rights records with administrator verification; inventory listing and pricing; reservations with database-enforced conflicts; campaigns with audited transitions; placement requests with two-party approval; production through the existing placement engine (quality check, approval, render, validation); quotes, invoices, append-only double-entry ledger, revenue allocation, idempotency; delivery with evidence, signed downloads, acceptance; campaign reports, which deliver pending events before reading; video deletion from the Library, with a video in use refused; choosing the workspace kind at sign-up; a per-request Content-Security-Policy nonce on every page; the demonstration environment |
| **Implemented but not fully verified** | Invitations for existing accounts and per-organization suspension (tests, no browser walk); uploading a new video version (API and tests, no web control); the Vercel container build of Phases 2–15 (not rehearsed, B-9) |
| **Sandbox only** | Payment: an administrator records a bank transfer with a reference (B-4). Email: written to an outbox folder locally; the live provider is not exercised. Google sign-in: tested against a local stand-in (B-3) |
| **Designed but not implemented** | Programmatic buying: bid requests, exchanges, SSP and DSP connections. The API labels each capability `DESIGNED_ONLY` and the adapters raise `NotConnected` ([PROGRAMMATIC_ARCHITECTURE.md](../architecture/PROGRAMMATIC_ARCHITECTURE.md)). Scene descriptions from a vision model (`CC_VISION_MODEL`, none evaluated) |
| **Blocked by an external dependency** | B-1 database password reset; B-2 Vercel settings import; B-3 Google redirect address; B-4 payment provider; B-5 Vercel plan for commercial use; B-6 a licensed detection model; B-7 licensed real footage; B-8 legal review; B-9 Docker on the test machine. See [BLOCKERS.md](../execution/BLOCKERS.md) |

---

## 1. Repository audit findings
The Phase 0 audit ([REPOSITORY_BASELINE.md](../audit/REPOSITORY_BASELINE.md), [EXISTING_FUNCTIONALITY_MATRIX.md](../audit/EXISTING_FUNCTIONALITY_MATRIX.md)) found a working single-workspace product:
- Sign-in, uploads, the decision engine, placements with frame-by-frame quality checks, rendering and signed downloads.
- No organization types, versioned analysis, inventory, marketplace or ledger.
- An earlier marketplace (`eac6a91`) that stored money as floating-point dollars and had no concurrency control. It was kept as a reference and not restored.

The final audit, after Phases 1–15:
- **Bugs found and fixed:**
  - Upgrading a PostgreSQL database that already had data failed (deferred constraint checks).
  - Restoring a backup failed on seeded tables.
  - Lighting drift was read as scene cuts.
  - A misleading plan label in the account box.
- **Checks that came back clean:**
  - Every one of the 148 `/api` routes is in [API_CONTRACTS.md](../architecture/API_CONTRACTS.md), and every event type is in [EVENT_CONTRACTS.md](../architecture/EVENT_CONTRACTS.md).
  - No debug output, leftover markers or literal secrets in the application, operations or web code.
  - Ruff and TypeScript are clean, and `next build` succeeds.

Open debt is ranked in [TECHNICAL_DEBT.md](../audit/TECHNICAL_DEBT.md).

## 2. Final architecture
- **Structure:** a modular monolith, kept from the baseline (AD-1, AD-2). Details: [SYSTEM_ARCHITECTURE.md](../architecture/SYSTEM_ARCHITECTURE.md).
- **Backend:** FastAPI with hand-written SQL that runs on SQLite and PostgreSQL.
- **Frontend:** Next.js 16 and React 19.
- **Hosting:** one Vercel project with three services (`web`, `api`, `runner`), Neon PostgreSQL and Vercel Blob.
- **How the domains connect:** through shared tables with foreign keys, a transactional outbox with per-aggregate ordering and idempotent consumers, and status transitions recorded in `status_transitions`.

The pipeline:
- **Background work:** analysis, rendering and outbox delivery run as leased background jobs. On Vercel they run in the HTTP job runner, within the 300 s function limit, and resume where they stopped.

## 3. Modules implemented
| Module | File | Phase |
|---|---|---|
| Migrations | `app/migrations.py` | 1 |
| Outbox | `app/outbox.py` | 1 |
| Video and analysis versions | `app/video_versions.py`, `app/analysis_versions.py` | 1 |
| Opportunities schema | `app/opportunities.py` | 1 |
| Organizations, kinds, platform admin | `app/orgs.py`, `ops/platform_admin.py` | 2 |
| Video assets | `app/videos.py` | 3 |
| Intelligence pipeline | `app/intelligence.py` | 4 |
| Scoring | `app/scoring.py` | 5 |
| Inventory, reservations, marketplace browsing | `app/inventory.py` | 5, 8 |
| Profiles and rights | `app/profiles.py` | 6 |
| Catalogue and matching | `app/catalogue.py` | 7 |
| Campaigns, placement requests, production | `app/campaigns.py` | 9, 10 |
| Commercial: quotes, invoices, ledger | `app/commercial.py` | 11 |
| Delivery and reporting | `app/delivery.py` | 12 |
| Programmatic foundation | `app/programmatic.py` | 13 |
| Demonstration environment | `ops/demo.py` | 15 |
| Web pages | `web/app/dashboard/{analyses, inventory, marketplace, campaigns, requests, finance, organizations, admin}` | 2–12 |

## 4. Existing modules preserved
- **Unchanged in behaviour:** sign-in (Google and email), the decision engine, placements, quality checks, two-step approval, rendering and validation, signed downloads, plans and limits, storage, and the Vercel job runner.
- **The insertion engine itself is untouched.** Marketplace production calls the same `create_placement` on a linked copy of the brand's product.
- **Evidence:** all 115 baseline tests still pass, with two expectations changed and their reasons recorded in [REGRESSION_LOG.md](../execution/REGRESSION_LOG.md). The original browser walk passes 12/12.

## 5. Database migrations
- **How migrations work:**
  - The baseline is frozen.
  - Fifteen ordered, checksummed migrations follow it, recorded in `schema_migrations`.
  - Start-up refuses a database whose applied migration was edited afterwards, or one that has migrations this code doesn't know.
  - All migrations are additive.
- **The migrations:**
  - `0001_organization_kinds`, `0002_memberships`, `0003_video_versions`, `0004_analysis_versions`, `0005_opportunities`
  - `0006_outbox`, `0007_session_active_org`, `0008_video_assets`, `0009_analysis_jobs`, `0010_scoring_inventory`
  - `0011_profiles_rights`, `0012_catalogue`, `0013_marketplace`, `0014_commercial`, `0015_delivery`
- **Database constraints:**
  - New foreign keys are `DEFERRABLE INITIALLY DEFERRED`, and on PostgreSQL they are checked after every migration.
  - The ledger is append-only through database triggers on both engines.
  - Reservations and active analysis jobs are made unique by partial indexes.
- **Reference:** [DATABASE_DESIGN.md](../architecture/DATABASE_DESIGN.md).

## 6. API endpoints
- **148 routes under `/api`**, all listed in [API_CONTRACTS.md](../architecture/API_CONTRACTS.md), with error codes (`INVENTORY_CONFLICT`, `WRONG_PARTY`, `ORGANIZATION_KIND_FORBIDDEN`, `KIND_LOCKED`, `PLATFORM_ADMIN_REQUIRED`, `ROLE_FORBIDDEN`).
- **Money-moving requests** accept `Idempotency-Key`.
- **Events:** 15 types, version 1, in [EVENT_CONTRACTS.md](../architecture/EVENT_CONTRACTS.md).

## 7. Frontend workflows
- **Publisher:** Analyses (start, follow stages, open a version) → analysis detail (stages, scenes, opportunities, safety review) → Inventory (rights records, list with a price, withdraw) → Placement requests (approve, reject, start production) → placement page (quality check, approval, render) → Finance (earnings, ledger).
- **Brand:** Marketplace (filter, reserve) → Campaigns (create, products, targeting, submit) → campaign page (recommended scenes, reserve, report) → Placement requests (request, accept quote, accept delivery, download) → Finance (invoices).
- **Platform administrator:** Admin (verify rights evidence, record payment with a bank reference, record payouts, trial balance).
- **Everyone:** Organizations (create, switch, accept invitations, kind, marketplace profile).
- **Layout and permissions:** pages render at 390 px without sideways scrolling, and the navigation follows the organization kind and role.

## 8. AI pipeline details
- **Stages and statuses:**
  - VALIDATING, EXTRACTING_METADATA, SEGMENTING and EXTRACTING_FRAMES: done.
  - DETECTING_OBJECTS and TRACKING_OBJECTS: **`UNAVAILABLE`** (B-6).
  - UNDERSTANDING_SCENES: **`PARTIAL`**, measured attributes only.
  - DETECTING_PLACEMENTS: plain planar surfaces, followed by re-detection at IoU ≥ 0.5.
  - SCORING_OPPORTUNITIES: deterministic and versioned, with unmeasured factors left out.
- **Recorded with each version:** its configuration and model versions. A new analysis never changes an earlier one.
- **The explanation for each score** says it is a ranking aid, not a validated prediction.
- **Reference:** [VISUARA_INTELLIGENCE.md](../product/VISUARA_INTELLIGENCE.md).

## 9. Rendering integration status
- **What happens:** an approved placement request becomes a placement in the publisher's organization, using a linked copy of the brand's product. It then goes through the existing quality check, approval, render and frame-by-frame output validation.
- **On success,** a delivery record is written with five kinds of evidence (output, validation, approvals, rights, provenance), and the brand gets a signed download link.
- **Verified:** in `test_the_whole_marketplace_flow` with a real render, and in the browser walk.

## 10. Marketplace transaction status
- **How a transaction runs:**
  - Reservation, with conflict handling and hold expiry.
  - Placement request, then the publisher's approval, which issues a quote and marks the item SOLD.
  - The brand accepts the quote, and an invoice `VIS-YYYY-NNNNNN` is issued with ledger lines.
  - An administrator records payment, and revenue is allocated: the platform fee `CC_PLATFORM_FEE_BPS` (default 20%), the rest payable to the publisher.
  - Payout is recorded.
- **Money** is integer minor units with an ISO currency code.
- **Verified:** the trial balance is balanced after the full flow, in the tests and in the browser walk.
- **Gap:** real payment capture is blocked (B-4).

## 11. Security audit findings
- **Tenant isolation, roles and organization kinds** are tested on both databases, as are adversarial input and concurrent reservations. See [TENANT_ISOLATION_REPORT.md](../security/TENANT_ISOLATION_REPORT.md) and [SECURITY_MODEL.md](../security/SECURITY_MODEL.md).
- **Found and fixed during the build:**
  - Signed media checked the role from the wrong organization.
  - Decisions logged events in the creator's default organization.
  - Brand download links didn't re-check the role.
  - Automatic cancellations were not logged as transitions.
- **Built in from the start:** rights evidence types are checked from the file's content, not its name.
- **Hardening before release (5 November 2026):**
  - **Scripts:** the Content-Security-Policy no longer allows inline scripts. Each response carries a fresh nonce with `'strict-dynamic'`, which the marketplace walk checks on four pages.
  - **Secret scanning:** `qa/secret_scan.py` reports the file, line and kind only, never a fragment of the value. It covers TypeScript and SQL files, and with `--history` it reads every blob in every commit. A pre-commit hook in `.githooks/` runs it once the owner enables it with `git config core.hooksPath .githooks`.
  - **Leaked credentials:** before go-live, `qa/check_env.py` refuses any database password or secret setting that also appears anywhere in the git history, and names only the setting.
  - **Configuration:** `.env.vercel`, `.env` and `demo-data/` are ignored by git, and a missing production setting stops the API at start-up.
- **Open:**
  - **The live database password is public (B-1, critical).** It is in one commit already on GitHub. Resetting it in Neon is the fix, and only the owner can do it. Rewriting history doesn't help once a value is public, though the owner may still choose to do it.
  - No external penetration test.
  - Rate limits sit in the database.

## 12. Unit test results
- **What the unit tests cover:** the pure functions, which run in every suite. These are segmentation, surface following, score combination, the money split, migration ordering, opportunity validation and brand-safety matching.
- **Results:** all passed on both databases in the final run (5 November 2026).

## 13. Integration test results
- **Release run on the final code (5 November 2026, FFmpeg 9.0.2), 182 tests:**
  - **PostgreSQL 16:** 181 passed, 1 failed.
  - **SQLite:** 180 passed, 1 skipped (needs PostgreSQL, as in the baseline), 1 failed.
  - **The failure** was the same test on both databases. The secret-scanner test still expected the old result, which carried a fragment of the value: the leak this release removes. The test now checks the new contract, and `tests/test_platform.py` passes on SQLite. The test reads no database; the PostgreSQL re-run could not complete because the local server stopped (B-9). No product code changed.
  - All 115 baseline tests pass. No regressions.
- **Found and fixed on the way:** the first full run after Phases 1–15 had 4 failures on PostgreSQL. Three were baseline tests, broken by seeded tables and one test expectation; one was a new test that exposed the migration bug. An intermediate run was invalidated when FFmpeg was removed from the machine mid-run.
- **Details:** [REGRESSION_REPORT.md](../qa/REGRESSION_REPORT.md).

## 14. End-to-end test results
- **Marketplace browser walk:** 12/12 across a new sign-up, a publisher, a brand and an administrator. It also checks the script nonce, payment by bank reference, acceptance of the delivery, and deletion.
- **Original browser walk:** 12/12.
- **Backend whole-flow test:** passed on both databases.
- **Which code:** all three ran on the final code.
- **Not run:** the Vercel container rehearsal (B-9).
- **Details:** [END_TO_END_TEST_REPORT.md](../qa/END_TO_END_TEST_REPORT.md).

## 15. AI evaluation results
On synthetic footage only (B-7). See [AI_EVALUATION_REPORT.md](../qa/AI_EVALUATION_REPORT.md).

| Measure | Result |
|---|---|
| Scene cuts | Precision 1.00 (0.33 before the lighting fix), recall 0.50. All misses are 0.2 s closing scenes, merged by design |
| Poster found on textured tracking clips | 3 of 42 (7%). The detector finds plain surfaces only |
| Room clips with opportunities | 3 of 3 (2 each) |
| Bare clips with opportunities | 0 of 3 |

## 16. Performance measurements
- **Analysis:** median 0.49 s per second of video, maximum 2.15, for the whole pipeline on short clips on this 8-thread machine. Longer and larger videos are not measured, and neither is Vercel's 1 vCPU.
- **Browser walk:** production with a real render took 147 s on this machine, and every other step under 11 s.
- **Not done:** load testing.

## 17. Remaining limitations
- No object detection or tracking.
- Textured surfaces are not detected.
- No real-footage accuracy.
- Payment is recorded by hand.
- Programmatic buying is designed only.
- Uploading a new video version has no web control.
- Outbox consumers other than reports wait for the next runner request in HTTP mode; today there are none.
- Very short closing shots merge into the scene before.
- No ESLint in the web app.

All of these are ranked in [TECHNICAL_DEBT.md](../audit/TECHNICAL_DEBT.md).

## 18. External dependencies
- **Services:** Vercel (Services, Functions, Blob), Neon PostgreSQL, Google OpenID Connect, and an email provider.
- **Tools:** FFmpeg and FFprobe, OpenCV, Python 3.11, Node.js, and Chrome for the browser tests.
- **Still to be chosen:** a payment provider (B-4), a detection model (B-6) and licensed footage (B-7).

## 19. Exact startup instructions
Local, with SQLite. These are Windows Bash paths as on this machine; use `:` instead of `;` in `PYTHONPATH` elsewhere.
```
python -m venv .venv311 && .venv311/Scripts/pip install -r requirements.txt
cd web && npm ci && cd ..
# FFmpeg and FFprobe must be on PATH
CC_DATA_DIR=./data CC_MAIL_BACKEND=outbox .venv311/Scripts/python.exe -m uvicorn app.main:app --port 18000
cd web && CC_API_ORIGIN=http://127.0.0.1:18000 npx next dev --port 13000
```
- **Open:** http://localhost:13000.
- **Sign-in codes:** written to `data/outbox/latest.json`.
- **Production:** follow [DEPLOYMENT_GUIDE.md](../deployment/DEPLOYMENT_GUIDE.md). Clear B-1 to B-3, run `python -m ops.migrate` with `DATABASE_URL`, deploy, then grant the first administrator with `python -m ops.platform_admin grant <email>`.

## 20. Brand demonstration instructions
1. **Build the demo:** `python -m ops.demo reset --data ./demo-data`. It refuses to run when `DATABASE_URL` is set. Then start the API with `CC_DATA_DIR=./demo-data CC_MAIL_BACKEND=outbox` and the web app as in §19.
2. **Sign in** as `brand@demo.visuara.si`, with the code from `demo-data/outbox/latest.json`.
3. **Marketplace:** browse listed scenes, filter them, and open a preview.
4. **Campaigns:** open "Back to work". It shows its product (Laptop sleeve), its targeting, the recommended scenes and the report.
5. **Placement requests:** the seeded request is waiting for the publisher. After the publisher approves it, press **Accept quote**, and the invoice appears in Finance.
6. **Pay:** sign in as `admin@demo.visuara.si`, then go to Admin, type a bank reference and press **Mark paid**.
7. **Accept:** after the publisher has rendered, go to Placement requests as the brand. **Download** the video, then **Accept delivery**. The campaign report shows the events.

## 21. Publisher demonstration instructions
1. **Sign in** as `publisher@demo.visuara.si`, on the same environment as §20.
2. **Analyses:** two videos are analysed. **Open** one to see the stages, including the unavailable ones and their reasons, plus the scenes and the scored opportunities. **Analyse again** to create version 2; version 1 is kept.
3. **Inventory:** shows the rights records and the listed items, one of them reserved by the brand. To list a new item, review its brand safety, submit a rights record, have it verified as administrator, and **List** it with a price.
4. **Placement requests:** **Approve** the brand's request, which issues a quote. After payment, **Start production**, then on the placement page **Run quality check**, **Approve** and **Start render**.
5. **Finance:** earnings and the ledger, once the administrator has recorded payment.

## 22. Release blockers
| ID | Blocker | Severity |
|---|---|---|
| B-1 | The live database password is public; reset it and make the repository private | **Critical** |
| B-2 | The live API does not start: the settings are not imported into Vercel | **Critical** |
| B-3 | Google rejects the live return address | High |
| B-6 | No evaluated detection model | High for the product promise; the MVP works without it |
| B-4 | No payment provider | High for charging customers; not for demonstrations |
| B-5 | Vercel's free plan forbids commercial use | High for charging customers |
| B-7 | No licensed real footage | Medium: demos use synthetic footage |
| B-8 | Legal review | Before commercial launch |
| B-9 | Docker does not respond, so the Vercel rehearsal did not run | Medium |
