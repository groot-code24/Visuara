# V9 test evidence

Observed in this session (2026-10-30) on Windows 11, **Python 3.11.0** (`.venv311`), FFmpeg 8.1, OpenCV 4.11 and headless Chrome. PostgreSQL runs are on PostgreSQL 16.2, served locally by `pgserver` as a stand-in for Neon. Commands run from the repository root.

## Automated tests
| Command | Observed |
|---|---|
| Baseline before V9 changes, Python 3.11: `PYTHONPATH=. python -m pytest -q -p no:cacheprovider` | 33 passed |
| Final, SQLite: same command | **55 passed**, 1 warning, 241 s |
| Final, PostgreSQL: `CC_TEST_DATABASE_URL=postgresql://… PYTHONPATH=. python -m pytest -q -p no:cacheprovider` | **55 passed**, 1 warning, 244 s. Each test runs in a fresh schema; the multi-process E2E test shares one schema between the API and worker processes |

The warning is Starlette's test-client deprecation notice (`httpx` → `httpx2`); it does not affect runtime behaviour.

An earlier PostgreSQL run failed 1 of 46 tests: `test_backup_restore_round_trip` correctly refused to back up a non-existent SQLite file. Fixed by making backup and restore engine-aware (logical export for PostgreSQL).

New test files (22 tests):

| File | Tests | Covers |
|---|---|---|
| `tests/test_auth_db.py` | 13 | Email code and magic link (sign-up, return, single use, CSRF on link confirmation, attempt burning, expiry, supersession, rate limit, non-enumeration); Google (state, iss/aud/exp/nonce/email_verified rejection, identity linking, invitation join, subject-based return); onboarding conflicts and cancel; landing and `/app` routes; SQL translation unit tests |
| `tests/test_dbtransfer.py` | 2 | Live database → SQLite → fresh PostgreSQL schema, verified per table, with ids continuing after migration; tampered dump refused |
| `tests/test_storage.py` | 3 | Key derivation; write-through, restore on an empty disk, presigned delivery and cross-org 404 before redirect; `push-media` |
| `tests/test_real_footage.py` | 4 | Consent and hash validation; deterministic creator split; Wilson and Krippendorff statistics; run → blind sheet → report (synthetic rows never counted as real) |

Bugs found by these tests and fixed:
- **Rating sheet:** leaked clip ids through video file names; renders are now copied to anonymous names.
- **`checkHealth`:** null dereference (found in V8 QA).
- **Health endpoint:** disclosed the database host and bucket name (found in code review).
- **Logical dump:** a newline escape in the digest code was mangled.

## Browser QA (headless Chrome, real server)
| Command | Observed |
|---|---|
| `PYTHONPATH=. python qa/browser_qa.py` | 10/10, no console errors |
| `PYTHONPATH=. python qa/browser_qa_v8.py` | 6/6, including a 3D placement through the new scene check |
| `PYTHONPATH=. python qa/browser_qa_v9.py` | **6/6**: landing (desktop and 390 px), join screens and Google redirect (PKCE, state, redirect URI), brand sign-up by email code and onboarding, invited teammate joins by code, creator sign-up by magic link (confirmation page, single use, 390 px), returning user. Screenshots: `docs/qa/v9/01–08` |

Visual review of the screenshots led to one fix: the mobile landing header wrapped the logo and button onto two lines.

## Benchmarks (all synthetic)
| Command | Observed |
|---|---|
| `python -m benchmark.run_benchmark --split all` | Held-out (test): 42/42, 0/21 false accepts, 0/21 false rejects, 21/21 validated. Identical to V8 |
| `python -m benchmark.run_2d_quality --seeds 2 3 4` | ΔE 5.50 → 4.72 over 21 clips, 0 regressions. Identical to V8 |
| `python -m benchmark.run_3d_benchmark --seeds 5 6 7` | **Fresh held-out:** 35/36 correct, 0/18 false accepts, 1/18 false rejects, 17/17 validated → `benchmark/results_3d_v9.json` |
| `python -m benchmark.run_3d_benchmark --seeds 2 3 4 --out benchmark/results_3d_v9_seeds234.json` | 36/36 correct (these seeds were inspected in V9, so this is not a clean held-out) |

Sample sizes: 70 planar + 21 2D + 72 3D clips (plus 24 3D dev clips). **0 real clips.** Details are in [3d-scene-check.md](3d-scene-check.md).

## Infrastructure drills (local stand-ins)
- **CLI migration:** `python -m ops.dbtransfer migrate --sqlite <copy of data/context_commerce.sqlite3>` into a PostgreSQL schema verified 27 tables and 6 rows. The local development database is nearly empty.
- **Backup and restore on PostgreSQL:** logical backup and restore into a fresh schema, refusal of a corrupted backup and of a populated target (`test_backup_restore_round_trip` in PostgreSQL mode).

## Dependency audit
| Command | Observed |
|---|---|
| `python -m pip_audit` in `.venv311` (79 packages) | Initially 1 finding (setuptools 65.5.0, venv tooling); after upgrading setuptools: **No known vulnerabilities found** |

## Not executed
- **Live integrations:** Neon, Google OAuth, SMTP delivery and a real S3 bucket (no credentials available).
- **Docker:** the image build (no Docker daemon).
- **Real footage:** none supplied.
- **Security review:** no independent penetration test.
