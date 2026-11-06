# Baseline (V4.0, Phase Zero)

**Taken:** 5 November 2026, 06:40–07:20, on this development machine, before any V4.0 change, except where a row says otherwise. Raw logs: [docs/qa/evidence/v4/](../qa/evidence/v4/).

## Repository state
| Item | Value |
|---|---|
| Branch and commit | `main` at `3bf569f` ("fixing backends"); remote `origin` on GitHub |
| Uncommitted work | 25 modified and 46 untracked files (the Phase 1–15 work, plus `result.md`); nothing staged |
| Ignored and must stay so | `.env`, `.env.*` (except `.env.example`), `data/`, `demo-data/`, `.venv*/`, `web/.next/`, `node_modules/` (`git check-ignore` confirmed) |
| Previous audit | [result.md](../../result.md). The `Pasted markdown(20261105-010409).md` named in the specification isn't in the repository; `result.md` is that audit |

## Toolchain
| Tool | Version |
|---|---|
| Python | 3.11.0 (`.venv311`); CI and the image use 3.12 |
| Node | 24.14.1 locally; CI and `engines` use 22.x |
| FFmpeg | 9.0.2 (CI and the image use Debian's) |
| Docker | Server 29.9.2, responding (it hadn't earlier in the session: B-9) |
| PostgreSQL | 16.15 (`docker compose up -d postgres`, 127.0.0.1:5433) |

## Checks
| Area | Check | Result | Notes |
|---|---|---|---|
| Backend | `ruff check app ops worker.py benchmark qa tests` (the CI command) | **Pass** | |
| Backend | `python -m compileall -q app ops worker.py` | **Pass** | |
| Backend | Full suite on PostgreSQL 16 | **182 passed** in 19 min 45 s | The first full PostgreSQL run including the morning's scanner-test fix. It loaded the code from before any V4.0 change |
| Backend | Full suite on SQLite | **184 passed, 1 skipped** in 13 min 32 s | Started at 07:05, after the first V4.0 changes (D-SEC-01, D-SEC-02 and the first version of the cross-tenant sweep). The skip is the PostgreSQL-only `test_blob.py:346`, as in every earlier baseline. Earlier that morning on the untouched code: 180 passed, 1 skipped, 1 failed (D-QA-02) |
| Frontend | `tsc --noEmit` | **Pass** | |
| Frontend | ESLint | **Not configured** | When configured: 6 errors, 12 warnings (D-FE-02) |
| Frontend | Unit tests | **None existed** | (D-FE-03) |
| Frontend | Production build (`VERCEL=1 next build`) | **Pass**: 30 routes, all rendered per request | Warns that `NEXT_PUBLIC_CONTACT_EMAIL` isn't set (a Vercel setting) |
| Security | `qa/secret_scan.py` (files) | **0 findings** in 217 files | |
| Security | `qa/secret_scan.py --history` | **2 findings**, both D-SEC-00 | `c37197a .env.vercel.example:6` |
| Security | `pip-audit -r requirements.txt` | **No known vulnerabilities** | |
| Security | `npm audit --audit-level=high` | **0 vulnerabilities** | After ESLint was added: 8 moderate, from one advisory (D-SEC-08) |
| Security | Container scan | **Not available**: no scanner (Trivy, Grype) installed | Recorded as a gap; CI doesn't scan the image either |
| Infrastructure | `qa/vercel_rehearsal.py` | **Not yet run** at baseline time | Run in M13 (see the regression log) |
| Infrastructure | Health checks | `/api/health/live` touches nothing; `/api/health/ready` checks the database, storage and FFmpeg (503 when not ready) | Covered by `test_health_split_and_cross_origin_protection` |
| Infrastructure | Production configuration | Guard present: production refuses to start without `DATABASE_URL` and other settings | The live deployment fails with exactly that (B-2) |
| Browser | Marketplace walk; original walk | 12/12 and 12/12 (the day before, on the code before V4.0) | Re-run after the V4.0 changes |

## CI pipeline (what "green" means)
`.github/workflows/ci.yml` runs on every push to `main` and on pull requests, in six jobs:

| Job | Steps |
|---|---|
| `lint` | ruff, `compileall` |
| `test-sqlite` | Python 3.12, FFmpeg from apt, `pytest` |
| `test-postgres` | The same suite against a PostgreSQL 16 service |
| `security` | `pip-audit`, `qa/secret_scan.py` |
| `web` | `npm ci`, `npm audit --audit-level=high`, `tsc`, ESLint and `npm test` (both added in V4.0), `next build` |
| `image` | Builds `Dockerfile.vercel`; smoke-tests liveness and FFprobe |

None of these has run on this code: CI runs when the owner pushes.
