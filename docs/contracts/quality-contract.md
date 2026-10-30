# Quality evaluation contract

Modules: [`app/tracking.py`](../../app/tracking.py) (measurements only) and [`app/quality.py`](../../app/quality.py) (policy and decision). Endpoint: `POST /api/placements/{id}/quality-check` (DRAFT only). The result is stored in `placement_analyses`, bound to placement version and content hash. The per-frame track is stored as JSON with its SHA-256.

## Inputs
Source video; placement mode (tracked planar quad or screen-space overlay); reference, start and end seconds; product image size. Tracked analyses are limited to `CC_MAX_ANALYSIS_SECONDS` (default 60) from reference to end.

## Frame convention
Frame `i` is in the interval iff `start ≤ i/fps ≤ end` (`media.interval_frames`). Analysis, rendering and validation all decode sequentially from frame 0 and use this definition.

## Output (always present)
`status` PASSED or FAILED · `scene_class` · `policy_version` · `thresholds_calibrated: false` · `frames_in_interval` · `frames_validated` · `measurements` (summary statistics) · `thresholds` · `failures[]` · `warnings[]` · `recommended_action` · `alternative_scenes` (on failure).

Each failure has `code`, `message`, `metric`, `observed`, `threshold`, `timestamps` (second ranges), `frame_count` and `recommended_action`.

**PASSED requires every interval frame to be measured and to pass.** A frame lost by the tracker is never extrapolated.

## Reason codes (policy `planar-v0.1-heuristic`)
| Code | Triggered when |
|---|---|
| LOW_TEXTURE | < 25 reference features or < 6 of 16 textured cells |
| TRACKING_LOST | Homography could not be estimated on a frame |
| INCOMPLETE_VALIDATION | Frames missing or undecodable |
| SCENE_CUT_IN_INTERVAL | Consecutive-frame histogram distance > 0.45 between reference and end |
| NON_PLANAR_OR_UNSTABLE | Inliers < 12, inlier ratio < 0.5 or RMSE > 2 px |
| PARTIAL_OCCLUSION_UNSUPPORTED | Some cell NCC < 0.40 while the median cell NCC ≥ 0.70 |
| TRACKING_DRIFT | Registered-surface NCC < 0.70 (or low cells without the occlusion pattern) |
| DEGENERATE_GEOMETRY | Non-convex tracked quad or area ratio outside 0.2–5 |
| MOTION_BLUR_UNSUPPORTED | Surface sharpness < 0.30 × reference |
| LIGHTING_CHANGE_UNSUPPORTED | Surface luminance ratio outside 0.75–1.33 |
| PRODUCT_OUT_OF_FRAME | < 85% of the product quad inside the frame |
| PRODUCT_TOO_SMALL | Product side < 16 px |
| OVERLAY_OUT_OF_FRAME | Overlay < 98% inside the frame |

## Scene classes
`OVERLAY_SCREEN_SPACE` (Tier A), `A_STATIC_PLANAR` (max product motion ≤ 1 px), `B_MOVING_PLANAR`, `C_PARTIAL_OCCLUSION` (reported, never rendered), `D_NON_PLANAR_OR_UNSTABLE` (reported, never rendered).

## What PASSED does not mean
It does not mean the product suits the scene, matches its lighting, casts plausible shadows, or is legal or appropriate. Human fit review (confidence ≥ 0.75, a workflow attestation rather than a probability) is required in addition.
