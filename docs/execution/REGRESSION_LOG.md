# Regression log

Every full test run is recorded here, newest first. A test that passed in the baseline and fails later is a regression and is fixed before new work continues. A test's expected result is never changed to make it pass unless this log records why the old expectation was wrong.

## Release run after the deployment hardening: 5 November 2026
This run covers the hardening that followed the final run below: the Content-Security-Policy nonce, the secret scanner reporting no fragment of a value, choosing the workspace kind at sign-up, Library deletion, and reports delivering pending outbox events. FFmpeg 9.0.2.

| Suite | Result |
|---|---|
| PostgreSQL 16 | 181 passed, **1 failed** |
| SQLite | 180 passed, 1 skipped (the baseline's PostgreSQL-only test), **1 failed** |
| `qa/browser_qa_marketplace.py` | **12/12** (the walk now also covers sign-up with a workspace kind, the nonce on every page, payment by reference, acceptance of the delivery, and deletion) |
| `qa/browser_qa_web.py` | **12/12** |
| `qa/vercel_rehearsal.py` | Not run (B-9) |
| Ruff, `tsc --noEmit` | Pass |
| `qa/secret_scan.py` (files) | 217 files, 0 findings |
| `qa/secret_scan.py --history` | 2 findings, both B-1: `.env.vercel.example` line 6 in commit `c37197a` |

**The failure** was the same on both databases: `tests/test_platform.py::test_secret_scanner_detects_credentials_and_allows_placeholders` raised `ValueError: not enough values to unpack (expected 3, got 2)`. The test unpacked the scanner's old result, which carried a fragment of the matched value. Leaking that fragment was the defect fixed on purpose (Decision log, 5 November 2026), so **the old expectation was wrong**. The test now checks the new contract: each finding gives the file and line plus the kind, and no finding contains any part of a value. The change touches the test only. No product code changed, so the 181 other results stand. After the fix, `tests/test_platform.py` passed on SQLite (19 passed). The re-run on PostgreSQL did not complete: the local PostgreSQL server, which runs in Docker, stopped accepting connections after the full run (B-9). The changed test takes no database fixture and reads no database, so its result cannot differ between the two; every other test in the file passed on PostgreSQL in the full run.

One check outside the suites: run without FFmpeg on `PATH`, `test_health_split_and_cross_origin_protection` fails because `/api/health` answers 503. That is the intended behaviour, since the API reports itself unhealthy when it cannot render. With FFmpeg on `PATH` it passes.

Logs: [docs/qa/evidence](../qa/evidence/) (`2026-11-05_final3_*`, `2026-11-05_browser_qa_*.json`, `screenshots-marketplace/`).

## Final run on the final code: 5 November 2026
FFmpeg 9.0.2 (gyan.dev essentials build), installed by the owner after 8.1 was removed. Every earlier run used 8.1.

| Suite | Result |
|---|---|
| PostgreSQL 16 | **181 passed** |
| SQLite | **180 passed, 1 skipped** (`tests/test_blob.py::test_schema_setup_is_skipped_when_already_applied`, which needs PostgreSQL, as in the baseline) |
| `qa/browser_qa_marketplace.py` | **9/9** |
| `qa/browser_qa_web.py` | **12/12** |
| `qa/vercel_rehearsal.py` | Not run (B-9) |

All 115 baseline tests are present and pass; the SQLite skip is the baseline's own. No regressions. Logs: [docs/qa/evidence](../qa/evidence/) (`2026-11-05_final_run_*`). The 9/9 marketplace log and its screenshots were replaced by the release run's 12/12 log of the same name.

## Re-run on the final code: 4–5 November 2026, invalidated by the environment
FFmpeg was removed from this machine during the run: the folder on `PATH` no longer exists. Every failure is attributed from its own traceback, by `ffmpeg is required`, `WinError 2` on starting `ffmpeg`, or the health check answering 503 because FFmpeg is missing.

| Suite | Result |
|---|---|
| PostgreSQL | 132 passed, 35 failed, 13 errors, 1 skipped. The first 65 tests passed before FFmpeg disappeared. All 48 failures and errors are FFmpeg; every non-media test passed |
| SQLite | 118 passed, 60 skipped (media tests skip themselves without FFmpeg), 2 failed, 1 error, all FFmpeg |

This run neither showed nor ruled out a regression in the media tests. The final run above, with FFmpeg reinstalled, settles it. Logs: [docs/qa/evidence](../qa/evidence/) (`*_ffmpeg_removed.txt`).

## Checks on the final code before FFmpeg was removed
| Change | Check | Result |
|---|---|---|
| `migrations.apply` clears deferred checks after each migration (PostgreSQL) | `tests/test_foundations.py` on PostgreSQL | 26 passed |
| Cut detection (`cut_gain_normalise`) | `tests/test_intelligence.py` on SQLite | 15 passed |
| Cut detection | `benchmark.evaluate_intelligence` | 48 of 48 analyses completed; [AI_EVALUATION_REPORT.md](../qa/AI_EVALUATION_REPORT.md) |
| Lint and types | `ruff check app ops worker.py benchmark qa tests`; `tsc --noEmit`; `next build` | Clean; build succeeds |

## Full run after Phases 1–15: 4 November 2026, working tree on `3bf569f`
| Suite | Result |
|---|---|
| PostgreSQL | 170 passed, 4 failed |
| SQLite | 175 passed, 1 skipped (the same PostgreSQL-only test as the baseline) |
| `qa/browser_qa_web.py` | 12/12 |
| `qa/browser_qa_marketplace.py` (new) | 9/9 |

Every one of the 115 baseline tests was present. Three baseline tests failed on PostgreSQL. With the one new failure, all four were fixed and passed again on PostgreSQL:

| Test | Cause | Fix |
|---|---|---|
| `test_assets_3d.py::test_backup_restore_round_trip` (baseline) | Restoring a dump violated the unique key on `brand_safety_rules`, which migrations seed | `ops/dbtransfer` clears the seeded tables (`scoring_configs`, `brand_safety_rules`) on the target before import. Code fix, not a test change |
| `test_dbtransfer.py::test_round_trip_between_engines_preserves_every_row` (baseline) | The same | The same |
| `test_auth.py::test_engine_matches_configuration` (baseline) | The test asserted that no `campaigns` table exists, because V11 had removed the old commerce tables | **Expectation changed.** Phase 9 deliberately reintroduces `campaigns` with a new schema (integer money, audited transitions). The old assertion guarded against the V11 tables coming back, and the three that did not come back are still asserted absent. The test now also checks the new table's shape (`placement_requests.campaign_id`) |
| `test_foundations.py::test_upgrading_a_pre_phase1_database_backfills_everything` (new) | Two causes. (1) The test dropped only the Phase 1 tables, but later tables reference them, and PostgreSQL refused. (2) A real bug: a backfill left deferred foreign-key checks pending, and PostgreSQL refuses `ALTER TABLE` on such a table (`ObjectInUse`), so **upgrading a PostgreSQL database that already has data failed** | (1) The test drops every table the migrations create. (2) `migrations.apply` runs `SET CONSTRAINTS ALL IMMEDIATE` then `DEFERRED` after each migration on PostgreSQL |

During this work the outbox also briefly broke `test_blob.py`, because delivery left work behind and the runner restarted itself. `outbox.deliver` was fixed to loop over claim rounds before the full run.

## Baseline: 4 November 2026, commit `3bf569f`
| Suite | Result |
|---|---|
| Backend, PostgreSQL 16 | 115 passed |
| Backend, SQLite | 114 passed, 1 skipped (`tests/test_blob.py:346` needs PostgreSQL) |
| `qa/browser_qa_web.py` | 12/12 (2 November 2026, on the code `3bf569f` was committed from) |
| `qa/vercel_rehearsal.py` | 14/14 (same) |

The test-by-test list is in [baseline_tests.txt](baseline_tests.txt).

**Local setup:** another project on this machine uses ports 3000, 5432 and 8000. This project's PostgreSQL runs on 5433, and browser runs need `--web-port` and `--api-port` set to free ports.
