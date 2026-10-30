# Release criteria (gates A–H)

A gate passes only with evidence recorded in [test-evidence.md](test-evidence.md). "Stand-in" means it was verified against a local substitute (PostgreSQL 16 for Neon, an S3 gateway for R2, Mailpit for an SMTP provider, a stubbed token exchange for Google). Those items stay open for gate D.

| Gate | Requirement | Evidence that counts |
|---|---|---|
| **A Local development** | Fresh setup documented; app, database, worker and media processing work; clean test run; browser tests | Test runs on a fresh database per test; compose stack from empty volumes; browser QA |
| **B Vercel frontend** | Build, assets, routing, API reachability, cross-domain auth with CORS and cookies, no secrets in the bundle | `frontend/build.mjs` + `check.mjs`; split-origin browser QA through the router emulator. **A real Vercel deployment is required for PASS** |
| **C Backend deployment** | Image builds, container starts, API serves, worker runs, FFmpeg present, jobs process, safe restarts, health checks | `docker build`, the compose stack, renders by containerised workers, shutdown hand-back test |
| **D Live infrastructure** | Real Neon, R2, SMTP, Google OAuth; backup/restore; Neon idle behaviour | Only runs against the real services count |
| **E Visual reliability** | 2D and 3D preserved; 3D tolerance fixed or blocked; real-footage validation for advertised scenes; unsupported scenes refused | Synthetic benchmarks + **real-footage protocol results** |
| **F Security** | No critical unresolved vulnerabilities; cross-tenant tests pass; consent and licence checks; media access controlled; secrets protected | Tests, dependency audit, secret scan, internal review; an independent pen test for public launch |
| **G Ten organizations** | 10 independent organizations, invitations, roles, concurrent workflows, limits, measured queue performance, no leakage | `qa/load_10_orgs.py` report + `tests/test_v10.py` |
| **H Launch decision** | Exactly one of PASS / CONDITIONAL / BLOCKED | PASS only when A–G are all PASS |
