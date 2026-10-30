# V10 implementation plan (as executed)

The order follows the V10 brief. Each step was followed by targeted tests and then the affected regression tests.

| # | Step | Outcome |
|---|---|---|
| 1 | Audit the repository; reproduce the V9 tests on an untouched copy | 55/55 on SQLite (250.6 s). Findings in [repository-audit.md](repository-audit.md) |
| 2 | Git: initialise, baseline commit of V9 unmodified, `.gitignore` and `.gitattributes` | Done (local history; no remote) |
| 3 | P0 fixes: streamed uploads, health split, production guard, security headers, CORS/Origin/CSRF for split origins | Done |
| 4 | Neon idle behaviour: worker backoff + wake file, pool idle closure, DB-free liveness | Done; live Neon dashboard not verified |
| 5 | Media cache cleanup, upload-session expiry, maintenance scheduling | Done |
| 6 | Multi-tenant: member roles, central authorisation, invitations, suspension, removal, ownership; quotas and fair claiming | Done |
| 7 | Direct-to-bucket uploads with cancel, retry and expiry | Done; browser-verified against the S3 gateway |
| 8 | Docker image, local compose stack, production compose, Caddy/Tunnel | Image built and run; production compose not run on a VM |
| 9 | Vercel build (Build Output API), emulator, split-origin browser QA | 10/10 in the browser; no Vercel deployment |
| 10 | Observability: JSON logs, request/job ids, redaction, metrics, monitor/alerts | Done |
| 11 | Google ID-token signature verification (JWKS) | Done; tested with local RSA keys, not live Google |
| 12 | 3D pose-aware plane tolerance: designed on dev seeds 0–1, frozen, validated on fresh seeds 8–10 | See [3d-tolerance.md](3d-tolerance.md) |
| 13 | Supervised payments (option A): ledger, operator CLI/API, status on offers | Done |
| 14 | 10-organization concurrency and isolation test | See [multi-tenant-readiness.md](multi-tenant-readiness.md) |
| 15 | CI workflow, deploy and rollback scripts | Written; not run on GitHub |
| 16 | Documentation, final full test runs, archive | See [test-evidence.md](test-evidence.md) |

**Not possible in this environment:**
- live Neon, R2, SMTP provider, Google OAuth and Vercel (no accounts or credentials);
- real creator footage (none supplied);
- an independent penetration test;
- GitHub Actions execution (no remote).

Each is a release gate that stays open ([release-readiness.md](release-readiness.md)).
