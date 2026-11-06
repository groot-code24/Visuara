# Decision log

Dated record of decisions, newest first. Architecture decisions are written out in [docs/architecture/DECISIONS.md](../architecture/DECISIONS.md); this log records when and why each was taken, and smaller execution choices.

| Date | Decision | Reason |
|---|---|---|
| 5 Nov 2026 | Every page renders per request so that it can carry a Content-Security-Policy nonce | A prerendered page cannot carry a per-request nonce, and without one the policy had to allow inline scripts. The cost is no static caching of public pages, which is acceptable at this scale |
| 5 Nov 2026 | A new workspace chooses its kind at sign-up (brand, publisher, agency, or just its own videos; the default stays `direct`) | Without it, every new account landed outside the marketplace and had to find the kind setting in Organizations |
| 5 Nov 2026 | Campaign reports deliver pending outbox events before reading | On Vercel nothing else wakes the runner between requests, and a report must reflect committed changes |
| 5 Nov 2026 | The secret scanner reports file, line and kind only, never a fragment of the value | It printed the first characters of a match: for a Neon password that is part of the secret |
| 4 Nov 2026 | A scene cut needs the colour histogram to change both as recorded and after gain normalisation (`cut_gain_normalise`, stored in each analysis's configuration) | Measured: ±8% exposure drift produced false cuts (precision 0.33). Normalising alone fixed that but cut plain walls, whose flat histogram sits on a bin edge. Requiring both fixed both, with real cuts unchanged ([AI_EVALUATION_REPORT.md](../qa/AI_EVALUATION_REPORT.md)) |
| 4 Nov 2026 | On PostgreSQL, deferred constraints are checked after every migration (`SET CONSTRAINTS ALL IMMEDIATE`, then back to `DEFERRED`) | Rows written by a backfill left checks pending, and PostgreSQL refuses `ALTER TABLE` on such a table. That broke upgrades of databases that already had data. Checking also stops a bad reference at the migration that wrote it |
| 4 Nov 2026 | Payment is recorded by a platform administrator with a bank reference; no provider is integrated | B-4: no provider credentials. The ledger, invoices and `Idempotency-Key` are ready for an adapter (AD-10) |
| 4 Nov 2026 | Object detection and tracking report `UNAVAILABLE`; scene understanding reports `PARTIAL` | No model has been evaluated for commercial use within the free plan's limits (B-6, AD-5). The spec forbids faking a stage |
| 4 Nov 2026 | Opportunity geometry comes from the first frame where the surface is visible | The placement engine needs its reference frame at or before the start of the interval |
| 4 Nov 2026 | Surfaces are followed by re-detection with IoU ≥ 0.5 every 0.5 s, not by a fixed box | A fixed box failed under camera motion and produced no opportunities |
| 4 Nov 2026 | Production copies the brand's product into the publisher's organization, linked by `source_product_id`, and reuses `create_placement` | The existing engine and its quality checks stay unchanged (spec: preserve the insertion engine), and each organization owns the files it renders |
| 4 Nov 2026 | Money is integer minor units with an ISO currency; the platform fee is `CC_PLATFORM_FEE_BPS` (default 2000) | Exact arithmetic; the ledger must balance to the unit |
| 4 Nov 2026 | Programmatic capabilities are labelled `IMPLEMENTED` or `DESIGNED_ONLY` in the API itself, and the adapters raise `NotConnected` | The spec asks for programmatic as future infrastructure, never presented as working |
| 4 Nov 2026 | Platform administration is granted only from the command line (`ops/platform_admin.py`) | No API path can escalate to it |
| 4 Nov 2026 | Phase 1 tables are written alongside the legacy ones (memberships with users, video and analysis versions with videos and frames), while reads stay on the legacy tables until their phase | The API keeps working unchanged, and the new tables hold real data from the first deployment instead of a backfill per phase |
| 4 Nov 2026 | The baseline schema and `_MIGRATIONS` are frozen; every change goes through `app/migrations.py` | An ordered, checksummed record makes upgrades repeatable and stops a silently edited migration |
| 4 Nov 2026 | New tables declare foreign keys `DEFERRABLE INITIALLY DEFERRED` | The specification asks for database constraints, and deferred checks let `ops/dbtransfer` import tables in any order |
| 4 Nov 2026 | Phase 1 scenes run between keyframes and are marked `LEGACY_KEYFRAME` | They are what the current pipeline really measures; real segmentation is Phase 4, and the label stops anyone mistaking one for the other |
| 4 Nov 2026 | A legacy role value that is no longer valid becomes `viewer` in `memberships` | Least privilege; no such value exists in current data |
| 4 Nov 2026 | AD-1 to AD-11 adopted (stack kept, modular monolith, organization types and memberships, versioned analysis, model-only detection, integer money ledger, outbox, database-enforced reservations, rendering reused, manual invoicing first, structured search first) | Phase 0 audit: see [REPOSITORY_BASELINE.md](../audit/REPOSITORY_BASELINE.md) |
| 4 Nov 2026 | The V11-removed marketplace (`eac6a91`) is a reference, not restored | It stored money as floating-point dollars and had no reservations or concurrency control |
| 4 Nov 2026 | Existing workspaces become `direct` organizations | Keeps today's single-workspace flow working without migration work by users |
| 4 Nov 2026 | Phases run in the specification's order; Phase 1 starts with the migration framework | Every later phase adds tables, so ordered, recorded migrations come first |
