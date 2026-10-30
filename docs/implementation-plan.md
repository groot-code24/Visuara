# Implementation plan

## Principle
Fix the critical path before widening the product: a placement must either render convincingly or be refused, and nothing may be delivered without verified rights and validated output. Marketplace breadth (agency, discovery, billing) waits until that holds.

## Vertical slice delivered this session (v6)
1. Audit and reproduce defects (G0). Record a baseline of 8 passing tests.
2. One compositor and frame-index convention shared by analysis, rendering and validation (G7).
3. Tracker that measures; policy that decides (G5). Measurements and business rules stay in separate modules.
4. Labelled synthetic benchmark with ground truth; thresholds tuned on the dev split only, frozen, then evaluated on the held-out split (G5/G13).
5. Output validator against the real file (G7).
6. Lifecycle, immutable versions, rights-holder enforcement, version-bound jobs and delivery (G9).
7. Job leases, heartbeat, cancellation, recovery, dead-letter (G12).
8. Corner editor and lifecycle-aware UI (G6/G10).
9. Real-process E2E and browser QA (G13).

## Next slices, in order
1. **Real-footage benchmark (G5/G13).** Licence or record 50–100 short clips across the scene classes. Annotate surfaces with a small labelling tool built on the corner editor. Recalibrate the policy and report false acceptance with confidence intervals.
2. **PostgreSQL + object storage (G12).** Move the schema to versioned migrations (Alembic), use S3-compatible private storage with short-lived signed URLs, and move quality analysis onto the queue.
3. **Backup and restore drill, dependency scanning, structured logs with correlation IDs (G12/G13).**
4. **Cross-organization grants (G2/G10).** Separate brand and creator organizations with explicit per-video licence grants. Then agency workspaces.
5. **Tier C occlusion** only after a segmentation model is integrated behind an adapter and benchmarked; until then, occluded scenes stay rejected.
