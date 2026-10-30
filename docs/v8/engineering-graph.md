# V8 engineering graph

Node fields: purpose → inputs/outputs → security invariants → acceptance → tests → failure behaviour → observability → status. Statuses: PASSED (verified here), PARTIAL, BLOCKED, NOT_STARTED.

```
Audit ─▶ Baseline+Security ─▶ Data model & media validation ─▶ Scene understanding ─▶ Feasibility (quality policy)
                                                                                         ├─▶ 2D geometry & tracking ─▶ 2D compositing ─┐
                                                                                         └─▶ 3D camera & pose ───────▶ 3D rendering ───┤
Commerce: listings ─▶ campaigns ─▶ offers ─▶ grants ──────────────────────────────▶ (licensed access) ─┤
                                                                         Temporal quality evaluation ◀─┘
                                                                                  ▼
                                    Human review (fit, consent, approvals) ─▶ Durable queue ─▶ Output & audio validation
                                                                                  ▼
                                    Rights & licence delivery check ─▶ Signed delivery ─▶ Campaign delivery & report ─▶ Benchmarks & release
Storage & backup and Security cut across every node.
```

| Node | Purpose | Key security invariant | Acceptance & tests | Failure behaviour | Observability | Status |
|---|---|---|---|---|---|---|
| N0 Audit | Know the real starting state | No changes before baseline | 24/24 baseline; audit doc | n/a | [repository-audit.md](repository-audit.md) | PASSED |
| N1 Security baseline | Known-vulnerable deps | Untrusted media parsers patched | pip-audit run; upgrades tested | Pillow blocked on Py 3.10 | audit log in docs | PARTIAL |
| N2 Media validation | Safe assets | Originals immutable; limits; GLB has no external URIs | `test_prepare_asset_*`, `test_glb_loader_*` | 400 with reason | asset/model reports | PASSED |
| N3 Scene understanding | Keyframes, cuts, surfaces | — | V6 tests | — | events | PASSED (geometric only) |
| N4 Feasibility | Per-frame decision with reasons | Never lower thresholds to pass | Gate benchmark 0 FA held-out | `FAILED` + reason codes | quality reports | PASSED (synthetic) |
| N5 2D tracking | Stable geometry | — | ECC; orbit 0.82 px | `TRACKING_DRIFT` etc. | per-frame measurements | PASSED (synthetic) |
| N6 2D compositing | Credible decal | Product identity preserved (bounded gains) | 2D benchmark; fringe test | — | `plan.color_match` | PASSED (synthetic) |
| N7 3D camera/pose | Metric camera | Refuse when unobservable | 3D benchmark; `test_3d_refuses_*` | `FOCAL_UNOBSERVABLE`, `POSE_*` | `report.camera` | PASSED (synthetic) |
| N8 3D rendering | Object on surface | Same plan for preview/export | `test_3d_placement_*` | `OBJECT_*`, `FOREGROUND_*` | job validation | PASSED (synthetic) |
| N9 Temporal QA | Full-clip validation | All interval frames | V6 + V8 tests | `INCOMPLETE_VALIDATION` | reports | PASSED |
| N10 Human review | Fit, consent, approvals | Rights holder only; version-bound | integrity + V8 tests | 403/409 codes | audit trail | PASSED |
| N11 Queue | Durable renders | Stale worker cannot publish | V6 lease tests, E2E crash test | DEAD_LETTER | queue events | PASSED (single host) |
| N12 Output validation | Real file checked | Undeliverable unless PASSED | all render tests | `OUTPUT_VALIDATION_FAILED` | `validation`, `exposure` | PASSED |
| N13 Commerce | Listings → offers → grants | Footage private until licence; licence bounds rights | `test_marketplace_*`, browser QA v8 | `LISTING_BLOCKED`, `CATEGORY_CONFLICT`, `OUTSIDE_LICENCE` | offer transitions in events | PASSED |
| N14 Delivery | Controlled downloads | Re-check all gates at use; tokens not logged | `test_signed_download_links` | 403/410 | download events | PASSED |
| N15 Measurement | Honest reports | No unmeasured claims | report test; methodology block | "not measured" list | CSV export | PASSED (limited metrics) |
| N16 Storage | Private object storage | Private by default | — | — | — | **BLOCKED** (no Docker/credentials) |
| N17 Backup | Recoverable data | Hash-verified restore | `test_backup_restore_round_trip` | refuse corrupt/missing | manifest | PASSED (local) |
| N18 PostgreSQL | Concurrency, multi-host | — | — | — | — | **BLOCKED** |
| N19 Real-footage benchmark | Calibrated thresholds | Authorized footage only | — | — | — | **BLOCKED** (no footage) |
| N20 Payments | Real billing | — | — | — | — | NOT_STARTED (by design: no provider) |
| N21 Release | Decision | Evidence only | [release-readiness.md](release-readiness.md) | BLOCKED | — | DONE (decision: BLOCKED) |
