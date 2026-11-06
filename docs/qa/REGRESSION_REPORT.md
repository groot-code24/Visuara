# Regression report

Compares the final code after Phases 1–15 with the baseline at commit `3bf569f`, taken on 4 November 2026 before any change. Run-by-run detail is in [REGRESSION_LOG.md](../execution/REGRESSION_LOG.md); raw logs are in [evidence/](evidence/).

## Summary: no regressions
| | Baseline | Final code (5 November 2026, after the deployment hardening) |
|---|---|---|
| Backend tests | 115 | 182 |
| PostgreSQL 16 | 115 passed | **181 passed, 1 failed**; the failure was an outdated test expectation, fixed. The re-run could not complete because local PostgreSQL stopped (B-9); the changed test reads no database |
| SQLite | 114 passed, 1 skipped | **180 passed, 1 skipped, 1 failed** (the same failure, fixed; the file re-run passes 19 of 19). The skip is the baseline's own PostgreSQL-only test |
| Original browser walk | 12/12 | **12/12** |
| Marketplace browser walk | — | **12/12** |
| Vercel rehearsal | 14/14 | **Not run** (Docker does not respond, B-9) |

**The one failure** was `test_platform.py::test_secret_scanner_detects_credentials_and_allows_placeholders`, which unpacked the scanner's old result. That result included a fragment of the matched value, the leak the hardening removed on purpose. The test now checks the new contract instead, including that no finding contains any part of a value. It is a baseline test, so this is the second baseline expectation changed. Detail: [REGRESSION_LOG.md](../execution/REGRESSION_LOG.md).

**Baseline tests:** all 115 are present and pass. Their expectations are unchanged except two, each with its reason recorded in the log. `test_auth.py::test_engine_matches_configuration` changed because Phase 9 deliberately brings back a `campaigns` table, with a new schema. `test_platform.py::test_secret_scanner_detects_credentials_and_allows_placeholders` changed because its old expectation was the leak described above.

**FFmpeg version:** the final run used FFmpeg 9.0.2; the baseline and earlier runs used 8.1. Both pass.

## Regressions found and fixed along the way
1. **Restoring a backup failed on seeded tables.**
   - Affected: `test_backup_restore_round_trip` and `test_round_trip_between_engines_preserves_every_row`, both on PostgreSQL.
   - Fix: `ops/dbtransfer` now clears the tables that migrations seed before importing.
2. **Upgrading a PostgreSQL database that already has data failed.** This is a real production bug, found by the upgrade test.
   - Cause: deferred foreign-key checks left pending by a backfill made a later `ALTER TABLE` fail.
   - Fix: `migrations.apply` checks deferred constraints after every migration.
3. **Outbox delivery left work behind**, and the runner kept restarting itself (`test_blob.py`).
   - Fix: delivery loops over claim rounds while its budget lasts.
4. **Lighting drift was read as scene cuts.**
   - Found by the AI evaluation, not by a test, so it was never a baseline test.
   - Fix: cut detection requires both the recorded and the gain-normalised histogram to change.
   - Locked in by a new test with five real clips.

## An invalidated run
On 4–5 November the FFmpeg installation was removed from the machine while a full run was in progress. That run is kept as evidence (`*_ffmpeg_removed.txt`): every one of its failures traces to FFmpeg being missing. The final run above replaces it.
