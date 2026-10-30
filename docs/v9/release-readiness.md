# V9 release readiness

## Decision: **BLOCKED** (pilot and production)

### What changed since V8
Of V8's three blockers, two are resolved in code and one remains:

| V8 blocker | V9 state |
|---|---|
| No production database or private object storage | **Implemented:** Neon/PostgreSQL and S3-compatible storage, with migration, backup and restore. The full suite passes on PostgreSQL 16. **Not yet verified live** (no Neon project, bucket or credentials available here) |
| Known-vulnerable image decoder | **Resolved:** Python 3.11+ runtime, Pillow 13; `pip-audit` reports no known vulnerabilities |
| No real-footage evidence | **Still open:** protocol and tooling are ready and tested; no consented footage was supplied |

### Why it is still BLOCKED
1. **No real-footage evidence.** Every visual-quality, false-acceptance and 3D accuracy number is synthetic. Shipping to brands or creators requires the [real-footage protocol](real-footage-protocol.md) run on the test split, with human ratings.
2. **The live integrations are unverified.** Neon, Google sign-in, SMTP delivery and a real bucket have each been tested only against local stand-ins. This becomes PASS for the platform layer once the [checks below](#to-move-the-platform-layer-to-pass) succeed on your credentials. That is configuration work, not engineering.

Acceptable today: supervised demonstrations on synthetic or self-owned footage, on a trusted machine.

## Gate summary
| Gate | Status | Basis |
|---|---|---|
| A Technical integrity | **PASS (local)** | 55/55 on SQLite and 55/55 on PostgreSQL 16; browser QA V6 10/10, V8 6/6, V9 6/6; no known vulnerable dependencies |
| B Visual credibility (2D) | CONDITIONAL | Synthetic improvement retained; real footage missing |
| C 3D correctness | CONDITIONAL | Fresh held-out: 0/18 false accepts, 1/18 false rejects; occlusion check new; real footage missing |
| D Rights and security | CONDITIONAL | Private storage, presigned delivery, passwordless sign-in and security controls tested; live integrations and an independent review pending |
| E Commercial usability | PASS (local) | Landing → sign-up → onboarding → marketplace → licence → render, verified in the browser |

## To move the platform layer to PASS
With `.env` filled in (`DATABASE_URL`, `CC_S3_*`, `GOOGLE_*`, `CC_SMTP_*`, `CC_PUBLIC_URL`, `CC_SIGNING_KEY`):
1. **Health:** start the app; `GET /api/health` must report `database.engine=postgres`, `neon=true`, `reachable=true` and `object_storage.backend=s3_compatible`.
2. **Sign-up:** on the landing page, **Join as a brand** → **Continue with Google** must end in onboarding and then the workspace. **Join as a creator** with a real inbox must deliver the code within a minute, and the link must work once.
3. **Media:** upload a self-owned video and check that the object appears in the bucket; render; download, which must redirect to a presigned URL.
4. **Recovery:** `python ops/backup.py backup --data ./data --out ./backups/drill`, then restore it into an **empty** Neon branch (`DATABASE_URL` pointing at the branch) and compare counts. Also restore from Neon point-in-time to a branch.
5. **Container:** `docker compose up --build` on a host with Docker.

## Next three actions
1. **Run the real-footage benchmark:** 50–100 consented clips, at least 10 creators, a creator-level split and 3 or more blind raters. Then recalibrate on dev only and report test results with confidence intervals.
2. **Verify the live platform** with your Neon project, Google OAuth client, SMTP sender and bucket (the five checks above).
3. **Fix the 3D plane tolerance and re-run the 3D benchmark on new seeds.** Scale the tolerance with pose error (the held-out topdown false rejection), then validate on fresh seeds 8 and up and, once available, on real footage.
