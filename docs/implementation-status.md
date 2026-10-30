# Implementation status

**Status: INCOMPLETE. Suitable for a supervised local demonstration. Not pilot-ready or production-ready.** See [release-readiness.md](release-readiness.md).

## Audit findings (baseline)

Found by reading the code and reproducing behaviour before changing anything. All were present in the code that the previous docs described as passing.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| A1 | Critical | The tracked renderer projected the product image's *own pixel corners* through the homography, so the product was drawn near the frame's top-left, not on the selected surface. Reproduced: product pixels at x≈2–289, y≈0–173 for a quad at x 128–256, y 96–192. The existing test only checked that a file existed. | Fixed. Regression test asserts ≤3 px from ground truth on every interval frame. |
| A2 | Critical | No automated quality gate: tracking "succeeded" on a non-planar animated source, and fit was a single self-attested number. | Fixed: per-frame quality evaluator plus human fit review. |
| A3 | Critical | Any creator in the organization could consent to and approve placements on another creator's video. | Fixed: only the video's uploader (rights holder). |
| A4 | Critical | Jobs were not bound to a placement version, so a render of version 1 could be downloaded under version 2's approvals. | Fixed: `jobs.placement_version`; delivery rejects `SUPERSEDED_VERSION`. |
| A5 | High | No output validation beyond `ffprobe` metadata. | Fixed: frame-level validation of the actual file. |
| A6 | High | Cancellation or revocation during a render could be overwritten by a later `SUCCEEDED`. | Fixed: conditional completion on lease token and cancel flag. |
| A7 | High | Lease recovery only at API startup; no heartbeat; no dead-letter. | Fixed. |
| A8 | High | Fit review after approval did not invalidate approvals. | Fixed: fit review only on drafts. |
| A9 | Medium | `CC_SECURE_COOKIES=1` did not add `Secure` to the session cookie. | Fixed. |
| A10 | Medium | Tracked-link deactivation was rolled back (raise inside the transaction). `placement_id` was wrongly required in the body. | Fixed. |
| A11 | Medium | Surface-proposal texture score used the whole frame (identical for all candidates). | Fixed. |
| A12 | Medium | `index.html` read with the platform encoding (mojibake risk on Windows). | Fixed (UTF-8). |
| A13 | Medium | Invited users could not register through the UI without inventing an organization name (found by browser QA). | Fixed. |

## Implemented and verified this session
- Placement lifecycle (`app/lifecycle.py`): explicit transition table, atomic compare-and-set, audit event per transition.
- Immutable placement versions with SHA-256 content hashes (including the product image hash). Approvals, analyses and jobs bind to version and hash.
- Planar tracking measurements (`app/tracking.py`) and quality policy `planar-v0.1-heuristic` (`app/quality.py`) over every interval frame, with 13 reason codes.
- Single compositor for screen-space overlay and tracked planar insertion (`app/compositor.py`); single-pass H.264 encode with audio.
- Output validation of the real file (`app/validation.py`).
- Renderer capability registry and routing that refuses rather than falls back (`app/renderers.py`).
- Jobs: lease tokens, heartbeat, mid-render cancellation, continuous recovery, dead-letter, conditional completion.
- Browser UI: corner editor, quality report, consent screen, lifecycle-aware actions, render validation display, audit trail, phone layout fix.
- Synthetic labelled benchmark with ground truth (`benchmark/`), real-process E2E test, headless-Chrome browser QA (`qa/browser_qa.py`).

## Present but not verified or not implemented
- PostgreSQL, S3-compatible object storage, backup/restore drill: not implemented (Docker daemon unavailable here).
- Real-footage benchmark; calibrated thresholds: not available.
- Occlusion-aware (Tier C) and generative (Tier D) rendering: unavailable by design until verified.
- Agency workspaces, cross-organization grants, discovery, pricing, earnings, billing: not implemented.
- Relighting, shadows, motion-blur synthesis: not implemented, so those scenes are rejected.
- Quality analysis runs synchronously in the API request (capped at 60 s of footage from reference to end); it should move to the job queue.
