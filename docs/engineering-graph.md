# Engineering dependency graph

Snapshot: 2026-10-30 (v6 session). States: BLOCKED, READY, IN_PROGRESS, VERIFYING, PASSED, FAILED, DEFERRED.
`PASSED` means the acceptance criteria were verified **in this local environment** (Windows 11, Python 3.10, FFmpeg 8.1, OpenCV 4.11). It is not production certification. Evidence is in [test-evidence.md](test-evidence.md).

Standard verification command (referred to as `pytest-all` below): `PYTHONPATH=. python -m pytest -q`

## Summary

| ID | Objective | Depends on | Status | Risk |
|---|---|---|---|---|
| G0 | Repository audit and baseline tests | none | PASSED | low |
| G1 | Architecture, data contracts, threat model | G0 | PASSED | medium |
| G2 | Authentication, membership, tenant isolation | G1 | PASSED (single-org model) | high |
| G3 | Product catalog, media ingestion, storage | G2 | PASSED locally; object storage DEFERRED | high |
| G4 | Scene segmentation and candidate surfaces | G3 | PASSED (geometric only) | medium |
| G5 | Tracking, geometry, occlusion, quality evaluation | G3, G4 | PASSED on synthetic benchmark; real-footage validation BLOCKED | high |
| G6 | Human correction interface | G5 | PASSED (browser QA) | medium |
| G7 | Deterministic and perspective-aware rendering | G5, G6 | PASSED | high |
| G8 | Generative adapter and output validation | G7 | DEFERRED (no verified provider) | high |
| G9 | Version-bound approvals, licensing, revocation | G2, G3, G5 | PASSED | high |
| G10 | Brand, creator, agency workflows | G9 | IN_PROGRESS (brand+creator core done; agency not started) | medium |
| G11 | Campaign tracking and attribution | G9 | PASSED for links + manual imports; integrations DEFERRED | medium |
| G12 | Durable jobs, persistence, observability, recovery | G1 | IN_PROGRESS (SQLite leases done; PostgreSQL BLOCKED) | high |
| G13 | Visual, functional, security, performance verification | G5–G12 | IN_PROGRESS | high |
| G14 | Release candidate and operational docs | G13 | BLOCKED | high |

Critical path: G0 → G1 → G3 → G5 → G7 → G9 → G13 → G14. The next blocking items on it are real-footage benchmark data (G5/G13) and PostgreSQL plus object storage (G12/G13).

## Task details

### G0 — Repository audit and baseline
- **Objective:** Understand the existing system before changing it.
- **Changes:** none (audit only); baseline snapshot kept outside the repo.
- **Acceptance:** baseline suite runs; defects identified with evidence.
- **Verification:** `pytest-all` on the untouched code → **8 passed**.
- **Evidence:** audit findings A1–A12 in [implementation-status.md](implementation-status.md#audit-findings-baseline). A1 (tracked renderer placed the product at the frame's top-left instead of on the selected surface) was reproduced with a pixel-location script before any fix.
- **Status:** PASSED.

### G1 — Contracts, architecture, threat model
- **Objective:** Explicit interfaces for quality evaluation, rendering, lifecycle and jobs.
- **Changes:** `app/lifecycle.py`, `app/renderers.py`, `app/quality.py` (policy), `docs/contracts/*`, ADR-007…ADR-012.
- **Interface contracts:** [placement-lifecycle.md](contracts/placement-lifecycle.md), [quality-contract.md](contracts/quality-contract.md), [rendering-contract.md](contracts/rendering-contract.md), [job-lifecycle.md](contracts/job-lifecycle.md).
- **Acceptance:** every status change goes through the transition table; every renderer declares its capabilities.
- **Tests:** `test_lifecycle_rejects_invalid_transitions`, `test_renderer_routing_refuses_unsupported_and_unverified`.
- **Status:** PASSED. The threat model is summarised in [security-review.md](security-review.md).

### G2 — Authentication, membership, tenant isolation
- **Objective:** Only members of an organization reach its resources; only rights holders consent.
- **Changes (this session):** Secure cookie flag honoured; invitation sign-up without an organization name (bug found by browser QA); creator consent and approval restricted to the video's uploader.
- **Acceptance:** cross-org reads return 404; non-rights-holder creators get 403 `NOT_RIGHTS_HOLDER`; CSRF enforced.
- **Tests:** `test_org_isolation`, `test_csrf_rejects_missing_token`, `test_secure_cookie_flag_and_utf8_page`, `test_invited_user_needs_no_organization_but_new_workspace_does`, rights-holder steps in `test_tracked_workflow_…`, unauthorized steps in the E2E test.
- **Blocking issue:** brands and creators must share one organization (via invitation). Cross-organization grants are not modelled (see roadmap).
- **Status:** PASSED for the current single-organization model.

### G3 — Catalog, ingestion, storage
- **Objective:** Validated product images and videos with real metadata.
- **Changes:** product SHA-256 recorded at upload and bound into placement versions; new `GET /api/videos/{id}/stream` for the editor.
- **Acceptance:** invalid media rejected; product image changes after approval block rendering (`PRODUCT_CHANGED`).
- **Status:** PASSED locally. Private object storage (S3-compatible) is DEFERRED: Docker is not running in this environment, so MinIO or S3 could not be exercised.

### G4 — Scene segmentation and candidate surfaces
- **Objective:** Keyframes and geometric surface candidates.
- **Changes:** fixed proposal texture scoring (it was computed over the whole frame, so every candidate got the same score); proposals now report the keyframe they were computed on.
- **Acceptance:** proposals carry `requires_human_review` and heuristic confidence; editor can load them.
- **Status:** PASSED (geometric heuristics only; no semantic detection claimed).

### G5 — Tracking, geometry, occlusion, quality evaluation
- **Objective:** Measure every frame of the interval; reject what the renderer cannot do well.
- **Changes:** `app/tracking.py` (forward-backward LK, RANSAC homography from the reference frame, re-seeding, per-frame photometric NCC on a rectified 4×4 grid, sharpness, luminance, scene-cut score, visibility); `app/quality.py` (policy `planar-v0.1-heuristic`, 13 reason codes); `POST /api/placements/{id}/quality-check`.
- **Acceptance:** suitable synthetic clips pass on all interval frames; unsuitable clips fail with a reason code, timestamps, observed vs threshold, recommended action; thresholds labelled uncalibrated.
- **Verification:** `python -m benchmark.run_benchmark --split all`.
- **Evidence:** held-out test split 42/42 correct decisions, 0/21 false accepts, 0/21 false rejects ([visual-quality-benchmark.md](visual-quality-benchmark.md)).
- **Blocking issue:** no legally usable real-footage labelled dataset is available; synthetic results do not establish real-world false-acceptance rates.
- **Status:** PASSED (synthetic). Real-world calibration BLOCKED on data.

### G6 — Human correction interface
- **Objective:** Correct surfaces on the actual video without editing JSON.
- **Changes:** corner editor in `app/static/index.html`: video stage, draggable and keyboard-movable corner handles, frame stepping, undo/redo, detected-surface suggestions, live perspective preview (original vs proposed toggle), reference/start/end timestamps, save as a new version and re-run quality checks.
- **Verification:** `python qa/browser_qa.py` → 10/10 steps, including a real pointer drag, undo, and arrow-key nudge.
- **Status:** PASSED. Tracking keyframes (multi-keyframe correction) are not implemented.

### G7 — Deterministic and perspective-aware rendering
- **Objective:** Render exactly the validated geometry; never claim success without validation.
- **Changes:** `app/compositor.py` (one compositor for both tiers, single-pass FFmpeg encode, audio preserved); `app/validation.py` (decode output, check container facts, compare every interval frame with the expected composite and every 5th other frame with the source).
- **Acceptance:** job succeeds only if validation passes; the product lies on the selected surface (≤3 px from ground truth in the regression test).
- **Tests:** `test_tracking_quad_validation_and_real_perspective_render`, workflow tests; benchmark 35/35 accepted renders validated.
- **Status:** PASSED.

### G8 — Generative adapter
- **Objective:** Use a generative provider only when it is verified.
- **Changes:** generative tier declared `verified: false`; routing excludes it; `/generative-render` returns 503 with the capability record; the worker fails closed because generative output cannot be validated automatically.
- **Status:** DEFERRED. No compatible model, credentials or budget were supplied. The Replicate adapter code is kept.

### G9 — Version-bound approvals, licensing, revocation
- **Objective:** Approvals and rights bind to an immutable version and content hash.
- **Changes:** `placement_versions` (snapshot and SHA-256), approvals store `content_hash`, jobs store `placement_version` and `analysis_id`, delivery rejects superseded versions, consent locked after creator approval, brand term changes reset consent, revocation cancels jobs and deactivates links, track-file integrity hash.
- **Tests:** `test_tracked_workflow_…`, `test_tampered_track_and_revocation_…`, `test_brand_term_change_resets_creator_consent`, E2E step 16.
- **Status:** PASSED.

### G10 — Brand, creator and agency workflows
- **Status:** IN_PROGRESS. The brand and creator core loop works end to end in the browser. Agency workspaces, discovery, pricing and earnings are not started (deliberately: the brief says not to expand the marketplace before the core is solid).

### G11 — Campaign tracking and attribution
- **Changes:** links carry a licensed channel; redirect checks version, status and channel; a deactivation-rollback bug was fixed.
- **Status:** PASSED for tracked links and deduplicated manual imports. Affiliate integrations, attribution windows, refunds and billing are DEFERRED.

### G12 — Durable jobs, persistence, observability, recovery
- **Changes:** lease tokens, heartbeat renewal every 15 frames, mid-render cancellation, continuous lease recovery, `DEAD_LETTER` state, conditional completion (a stale worker cannot overwrite), unique temp files per lease, queue events in `/api/observability`.
- **Tests:** `test_cancel_mid_render_lease_recovery_and_dead_letter`, E2E kill-worker recovery.
- **Blocking issue:** PostgreSQL migration, object storage and backup/restore drill need Docker or managed services (Docker daemon not running).
- **Status:** IN_PROGRESS.

### G13 — Verification
- **Done:** unit, integration, real-process E2E, synthetic benchmark, browser QA (desktop and 390 px).
- **Missing:** real-footage benchmark, load/performance envelope beyond 24 s 720p, dependency vulnerability scan (`pip-audit` not installed), penetration test, accessibility audit with assistive technology.
- **Status:** IN_PROGRESS.

### G14 — Release candidate
- **Status:** BLOCKED by G12 and G13. See [release-readiness.md](release-readiness.md).
