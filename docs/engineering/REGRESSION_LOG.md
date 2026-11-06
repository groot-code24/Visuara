# Regression log (V4.0)

Every full run during V4.0, newest first. Earlier runs, Phases 1–15, are in [docs/execution/REGRESSION_LOG.md](../execution/REGRESSION_LOG.md). A test that passed at baseline and fails later is a regression and is fixed before work continues. An expectation is changed only when this log says why the old one was wrong.

## Final run, M15 to M17: 6 November 2026
On the finished code, with the YOLOX-Tiny model installed. FFmpeg 9.0.2, PostgreSQL 16.15.

| Suite | Result |
|---|---|
| Backend, PostgreSQL 16 | **250 passed** (25 min 33 s) |
| Backend, SQLite | **249 passed, 1 skipped** (18 min 28 s). The skip is the PostgreSQL-only `test_blob.py:346` |
| After the payment-settings refactor (below): `test_payments.py`, `test_platform.py`, `test_agencies.py`, `test_programmatic.py` | **39 passed** on PostgreSQL and **39 passed** on SQLite |
| Ruff, `compileall` | Pass |
| Web: `npm test`, `tsc`, ESLint, `npm audit --audit-level=high`, `next build` | All pass (ESLint 0 errors, the same 10 deliberate warnings) |
| `qa/browser_qa_marketplace.py` | **12/12**, now including the brand paying through the test-mode checkout, with one webhook applied and a balanced ledger |
| `qa/browser_qa_web.py` | **12/12** |
| `qa/vercel_rehearsal.py` | **15/15**, on the image with Debian security updates applied at build |
| `qa/load_test.py --docker --users 20 --seconds 60` (production image, one pinned vCPU, 2 GB) | **Passed**: 59 requests a second, 0 errors, p50 314 ms, p95 542 ms, p99 663 ms ([evidence](../qa/evidence/v4/2026-11-06_final_load_1vcpu.json)) |
| `qa/secret_scan.py`, files | 0 findings in 284 files |

**The load test before and after D-PERF-01** (same target, same mix):

| | Before | After |
|---|---|---|
| Requests a second | 42 | 59 |
| p95 | 869 ms | 542 ms |
| p99 | 2.8 s | 663 ms |
| `GET /api/campaigns` p50 | 2.2 s | no longer among the slowest |

**Runs that don't count as the record:**
- A PostgreSQL run of the new test files had 8 errors, all `psycopg ConnectionTimeout`: Docker Desktop's VM had restarted and the local PostgreSQL container was gone. Restarted; the full runs above are after it.
- The first one-vCPU load test, which exposed D-PERF-01.

**After the full runs,** the payment settings checks moved to `app/payment_settings.py`. That module imports nothing from the app, so the production start-up check can't hit an import cycle while `app.main` is loading. Every file that touches it was re-run (above), and production start-up was checked by hand: it refuses with the list of unsafe settings.

## Changed expectations in M15 to M17
| Test | Old expectation | New | Why |
|---|---|---|---|
| `test_security_marketplace.py::test_programmatic_capabilities_are_labelled` | `third_party_measurement` is `DESIGNED_ONLY` | `NOT_CONNECTED` | M17 gave measurement an interface, a contract and a test adapter; without a partner that is NOT_CONNECTED by the specification's definition. `test_programmatic.py` checks every state |
| `qa/browser_qa_marketplace.py` step 7 | An administrator marks the invoice paid | The brand pays through the test-mode checkout; the administrator checks one APPLIED event and the trial balance | M15's provider flow is the journey to verify. The bank-transfer path is still tested (`test_without_a_provider_invoices_are_paid_by_bank_transfer`) |

No assertion was weakened.

## M14 final run: 5 November 2026
On the finished M14 code, with the YOLOX-Tiny model installed (`ops/fetch_models.py`). FFmpeg 9.0.2, PostgreSQL 16.15.

| Suite | Result |
|---|---|
| Backend, PostgreSQL 16 | **234 passed** (26 min 2 s) |
| Backend, SQLite | **233 passed, 1 skipped** (26 min 38 s). The skip is the PostgreSQL-only `test_blob.py:346`, as at baseline |
| Ruff, `compileall` | Pass |
| Web: `npm test`, `tsc`, ESLint, `npm audit --audit-level=high` | All pass (3 unit tests; ESLint 0 errors, the same 10 deliberate warnings; 8 moderate dev-only advisories, D-SEC-08) |
| Web: `next build` | Pass (built against the walks' API port) |
| `qa/browser_qa_web.py` | **12/12** |
| `qa/browser_qa_marketplace.py` | **12/12**, with two new checks on the analysis page (the detection model and its speed; score components). Its screenshot exposed D-AI-07; after the fix, **12/12** again |
| `qa/vercel_rehearsal.py` | **15/15**, on the image that now fetches and verifies the model at build time; run again on the final code, **15/15** |
| A whole analysis on one vCPU (production image, `--cpuset-cpus=0 --memory=2g`) | A 29.6 s clip at 480 × 270 with three scenes: **36 s** end to end (1.22 s per second of video), detection 20.5 s for 55 frames (355 ms a frame), peak memory 552 MB ([evidence](../qa/evidence/v4/2026-11-05_m14_analysis_1vcpu.json)) |
| `qa/secret_scan.py`, files | 0 findings in 268 files |

**Runs that don't count as the record, and why:**
- An SQLite run on M14 code was stopped at 45% with no failures: three fixes came after it started (D-MKT-01, the health field, the lease renewal).
- The first final PostgreSQL run was stopped at 49 tests because it exposed the thread defect below.

**After the full runs,** the walk exposed D-AI-07. Its fix touched only the placements stage and the analysis page, so every test file that runs the analysis pipeline was re-run on both databases. These are the ten files from `test_blob.py` to `test_understanding.py` in the log:

| Run | Result |
|---|---|
| PostgreSQL | **114 passed** |
| SQLite | **113 passed, 1 skipped** |

The rest of the code is unchanged since the full runs. Logs: `2026-11-05_m14_*` in [evidence/v4/](../qa/evidence/v4/).

**Speed on this machine varies run to run, by about 15%.** The same model and image measured 331–349 ms a frame in one run and 403–406 ms in a later one. Switching OpenCV's thread count around each inference costs nothing measurable: 403 ms with switching, 406 ms without, on 40 images. The synthetic benchmark's whole-analysis figure moved from 1.10 to 1.29 s per second of video for the same reason. The one-vCPU Docker figure above is the one to plan with.

## Changed expectations in M14
| Test | Old expectation | New | Why |
|---|---|---|---|
| `test_intelligence.py::test_a_staged_analysis_records_every_stage_and_owns_its_outputs` | DETECTING_OBJECTS and TRACKING_OBJECTS `UNAVAILABLE` (reason AD-5); contextual relevance `UNAVAILABLE` | Both `DONE`, with the model version recorded, 8 frames detected, every stored detection above the threshold; scene camera, lighting and setting measured; every score factor has evidence | The requirement changed: M14 replaces the placeholder with an evaluated model (§8.3). The old behaviour is still tested, by the new `test_without_a_detection_model_the_stages_say_so_and_nothing_is_invented` |
| `test_intelligence.py::test_following_a_surface_stops_where_it_disappears` | Re-detection follower | Removed with the function it tested | The follower was replaced by plane following (D-AI-02), tested in `tests/test_surfaces.py` against ground truth |
| `test_marketplace.py::test_the_whole_marketplace_flow` | `breakdown.formula == 'category_fit x feasibility'` | `breakdown.version == 'match-v2'` and the result has reasons | Matching v2 (§8.8). Its rules are tested in the new `test_matching_uses_scene_context_and_leaves_out_what_it_must` |

No assertion was weakened. Two first drafts of new tests were wrong and corrected before they were run as records: one expected the room clip's camera to pan (it zooms; the measured zoom is now asserted within 0.4% a second of the true rate), and one compared food with fashion compatibility on a vertical surface (the kitchen boost is now asserted directly).

## Found while building M14 (not shipped)
- The first tracker re-created a track on every sample when objects moved more than their own width between samples; found by `test_two_people_crossing_keep_their_identities`. Fixed by centre-distance gating and by taking the velocity in full at the second sighting.
- The first plane follower let an occluder drag the homography 129 px off the surface; fixed by stopping at `occluded` or `jump`.
- Loading the detector set OpenCV's thread count to 1 for the whole process, so after the first detection every other OpenCV step (tracking, rendering, validation) ran on one core. Found because the first final PostgreSQL run used about one core and slowed down after the first analysis test; that run was stopped. The limit now applies to the inference call only (`test_inference_does_not_change_the_rest_of_the_process_threads`). The speed figures measured before the fix were re-measured.
- The first tracking benchmark counted real people in its background photographs as false tracks; backgrounds are now photographs with no labelled object bigger than 1%, and objects the photographs contain are counted separately.

## M13 final run: 5 November 2026
On the finished M13 code. FFmpeg 9.0.2, PostgreSQL 16.15.

| Suite | Result |
|---|---|
| Backend, PostgreSQL 16 | **189 passed** (18 min 4 s) |
| Backend, SQLite | **188 passed, 1 skipped** (12 min 25 s). The skip is the PostgreSQL-only `test_blob.py:346`, as at baseline |
| `tests/test_auth.py`, added after the full runs (the per-IP rate-limit test) | 21 passed on PostgreSQL and on SQLite |
| Ruff, `compileall` | Pass |
| Web: ESLint, `npm test`, `tsc`, `npm audit --audit-level=high`, `next build` | All pass (ESLint: 0 errors, 10 deliberate warnings) |
| `qa/browser_qa_web.py` | **12/12** |
| `qa/browser_qa_marketplace.py` | First attempt **9/12**: step 10 got a 429 (D-QA-08). After the fix, **12/12** |
| `qa/vercel_rehearsal.py` | **15/15** (below) |
| `qa/secret_scan.py`, files | 0 findings in 234 files, after D-QA-07 was fixed |
| `qa/secret_scan.py --history` | 2 findings, both D-SEC-00 |

All 115 tests from before Phase 1 and every baseline test are present and pass. Logs: [evidence/v4/](../qa/evidence/v4/) (`2026-11-05_m13_*`).

**The marketplace walk failure (D-QA-08).** Step 10's sign-in got a 429. The API log shows 13 sign-in requests in 54 seconds from one IP address, and the API allows 12 a minute (`CC_AUTH_RATE_PER_MINUTE`). The walk signs six people in from one machine, and earlier runs passed only because a slow render spaced the sign-ins out. That's the limit working, not a regression. The walk now raises that one setting in its own environment, with the reason in the script. The limit itself had no test, so `test_sign_in_requests_are_limited_per_address_and_per_ip` now checks both the per-IP and the per-address limits.

## Changed expectations in M13
| Test | Old expectation | New | Why the old one was wrong |
|---|---|---|---|
| `test_platform.py::test_production_refuses_development_stand_ins` (the "good" configuration) | `postgresql://u:p@ep-x.neon.tech/db` passes the production guard | The same with `?sslmode=require` passes; without it, refused | The old string allowed an unencrypted connection (D-SEC-05). Neon's own strings carry `sslmode=require`. Stricter, not weaker |
| `test_blob.py` `BASE` and `gmail_only` | `postgresql://x` passes | `postgresql://x/db?sslmode=require` | The same reason |
| `qa/vercel_rehearsal.py` stand-ins | Plain PostgreSQL and SMTP | TLS on both, with a new step that proves it | Production now refuses both unencrypted (D-QA-05) |

No assertion was removed or weakened. Tests added in M13: the cross-tenant sweep, log redaction, SMTP TLS, database TLS, production start-up without a database, directory scanning, email subject line breaks, the open-redirect unit tests, and the delivery-token replay.

## Deployment rehearsal: 5 November 2026
| Attempt | Result | Cause |
|---|---|---|
| First | Failed before any step | The script never built its image (D-QA-04) |
| Second, after the fixes | **15/15 passed** | Image built from the working tree; TLS stand-ins (D-QA-05). The first rehearsal on the Phase 2–15 code |

Log: [evidence/v4/2026-11-05_vercel_rehearsal.txt](../qa/evidence/v4/2026-11-05_vercel_rehearsal.txt).

## Baseline: 5 November 2026
| Suite | Result |
|---|---|
| PostgreSQL 16 | 182 passed (on the code before V4.0) |
| SQLite | 184 passed, 1 skipped (started after the first V4.0 changes) |

Detail: [BASELINE.md](BASELINE.md).
