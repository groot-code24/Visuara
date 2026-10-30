# V8 acceptance criteria and gate results

Statuses per the brief: `PASS`, `CONDITIONAL`, `BLOCKED`. Each line names its evidence. "Synthetic" means `benchmark/` fixtures with exact ground truth, not real footage.

## Gate A: Technical integrity
| Criterion | Result | Evidence |
|---|---|---|
| Existing V6 tests still pass | PASS | 24/24 baseline; all retained in the final suite |
| App builds, imports and starts | PASS | E2E and browser QA start uvicorn and workers from scratch |
| Dependency advisories addressed | **CONDITIONAL** | Starlette and pytest fixed and tested; Pillow fix needs Python ≥ 3.11 (pinned for the Docker image; **local 3.10 venv still on 12.3.0**) |

## Gate B: Visual credibility (2D)
| Criterion | Result | Evidence |
|---|---|---|
| Measurable improvement on supported scenes | PASS (synthetic) | Held-out ΔE 5.50 → 4.72 (−14%); exposure drift 7.81 → 4.90; worse on 0/21 clips |
| Unsuitable scenes still rejected | PASS (synthetic) | Frozen gate: 0/21 false accepts on held-out after the tracker change |
| Product identity protected | PASS | Gains clamped to the policy range with a ±2% deadband; original asset untouched; output validator checks the product region on every interval frame |
| Real-footage evidence | **BLOCKED** | No authorized real footage was supplied |

## Gate C: 3D correctness
| Criterion | Result | Evidence |
|---|---|---|
| Genuine 3D path on a defined subset | PASS (synthetic) | GLB on a tracked plane, estimated camera, rasterised, validated; see the [benchmark report](benchmark-report.md) |
| Camera alignment, pose and stability measured | PASS (synthetic) | Focal, rotation, anchor and IoU errors vs true camera |
| Unsupported scenes refused | PASS (synthetic) | Unobservable focal, off-surface, foreground motion, cut |
| Material/lighting realism on real footage | **BLOCKED** | Requires real footage and human rating |
| Occlusion behaviour | PASS (refusal only) | Occluded scenes are rejected; no occlusion-aware rendering |

## Gate D: Rights and security
| Criterion | Result | Evidence |
|---|---|---|
| Tenant isolation incl. cross-org marketplace | PASS | `test_marketplace_licence_workflow_conflicts_and_takedown`, V6 isolation tests |
| Consent, version-bound approvals, revocation, takedown | PASS | Integrity and V8 tests |
| Controlled downloads | PASS | Session download + signed short-lived links, re-checked at use, redacted from logs |
| Private object storage | **BLOCKED** | Local disk only; no Docker or cloud credentials |
| Backup/restore | PASS (local) | Round-trip test with corruption detection |

## Gate E: Commercial usability
| Criterion | Result | Evidence |
|---|---|---|
| Brand and creator complete the workflow on persisted data | PASS | `qa/browser_qa_v8.py`, 6/6 steps through the real UI across two organizations |
| Measurement does not over-claim | PASS | Report separates measured from not measured; no payments processed |

## Gate F: Release decision
See [release-readiness.md](release-readiness.md): **BLOCKED** for pilot or production.
