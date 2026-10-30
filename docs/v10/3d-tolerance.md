# 3D: pose-aware plane tolerance (V10)

Code: `plane_tolerance()` and `scene_consistency(..., pose_aware_plane=True)` in [app/render3d.py](../../app/render3d.py). Policy `tracked3d-v0.3-pose-aware-plane`.

## The V9 defect
The scene check classifies a pixel as "on the tracked surface" when its optical flow agrees, within a fixed **1 px**, with the plane homography derived from the two solved camera poses. That prediction carries the poses' own error. On V9's held-out clip `topdown_user_fov` s5, poses with up to 0.93 px corner reprojection error put table pixels about 1 px off the predicted plane. They were then triangulated a few millimetres *beneath* the surface, ruled physically impossible, and counted as foreground motion (0.097 > 0.03). The result was a false rejection.

## The change (designed without tuning)
Flow noise (the V9 1 px allowance) and the error of each pose are independent, so they combine in quadrature:

    tolerance(i, j) = min(2.0, sqrt(1.0² + e_i² + e_j²))      e = surface-corner reprojection RMS of each pose (px)

The cap of 2.0 px is the existing "clearly off the plane" threshold for occluders, so the tolerance can never absorb a candidate occluder. No constant was fitted to data. Measured on the 18 suitable held-out clips, the tolerance ranged from 1.00 to 1.09 px (median per-clip maximum 1.03 px), so it stays close to V9's value except where pose error is high.

## Protocol
1. **Dev, seeds 0–1** (24 clips, as in V9): 24/24 correct, 0 false accepts, 0 false rejects, **no decision changed** compared with V9's fixed tolerance.
2. **Diagnosis of the known failure** (`topdown_user_fov` s5, already seen in V9): V9 FAILED → V10 PASSED (tolerance 1.00–1.44 px). The static and moving occluder clips of that seed are still rejected (tolerance hits the 2.0 px cap; moving fractions 0.37 and 0.49).
3. **Code frozen and committed** (`6d672e4`) before seeds 8–10 were generated for the first time.
4. **Fresh held-out, seeds 8–10** (36 clips, 12 kinds, renders and validation included). Results in `benchmark/results_3d_v10.json`.

## Fresh held-out results (seeds 8–10, never used before)
| Metric | V10 decision | V9 fixed tolerance on the same clips |
|---|---|---|
| Correct decisions | **36/36** | 36/36 |
| False accepts | **0/18** | 0/18 |
| False rejects | **0/18** | 0/18 |
| Rejections carrying the expected code | 11/18 | — |
| Renders passing frame-by-frame validation | 18/18 | — |
| Accepted: focal error / rotation / anchor (max) | ≤ 1.09 % / ≤ 0.97° / ≤ 0.55 px | — |
| Accepted: silhouette IoU (min) | 0.894 | — |
| Median analysis time per clip (both checks run) | 16.8 s | — |

## What this shows, and what it does not
- **No increase in false acceptance** on fresh data: 0/18 unsuitable clips accepted, the same as V9.
- **No measurable improvement on fresh data either:** V9's fixed tolerance would also have scored 36/36 on seeds 8–10. The demonstrated gain is the V9 failure case (s5), which is no longer a clean held-out clip.
- **It exposed a pose-quality gap.** The newly accepted s5 clip is accurate on average (rotation 0.46°, IoU 0.95), but on 4 frames rotation error reaches 2.2° and silhouette IoU 0.75. Those frames have about 0.9 px pose reprojection error, which the pose gate (`max_pose_reproj_px` 1.5) allows. V9 rejected this clip for the wrong reason; V10 accepts it, and the gate does not catch the short accuracy dip. **Open action:** a per-frame pose-error gate calibrated on *new* dev seeds and validated on fresh seeds (s5 must not be used to tune it).
- **Synthetic only.** These scenes have no real sensor noise, motion blur, rolling shutter or compression. Real-footage behaviour is unknown until the real-footage protocol is run.
