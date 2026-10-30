# Parallax- and occlusion-aware 3D scene check (V9)

Code: `scene_consistency()` in [app/render3d.py](../../app/render3d.py); policy `tracked3d-v0.2-geometric` in [app/quality.py](../../app/quality.py).

## Why V8's check had to change
V8 compensated a single full-frame homography and counted every pixel that still changed as "foreground motion". That model cannot tell a wall behind the product (static, but with parallax) from a hand moving in front of it, and the full-frame fit can latch onto whichever surface dominates the view.

V8 attributed its one held-out false rejection (dolly s2) to parallax. **A V9 diagnosis showed that was wrong.** That fixture contains a single plane, and the flagged pixels were thin texture lines in the far, heavily minified part of the plane, flickering because of aliasing in the synthetic renderer (`docs/v8` now carries a correction). The V8 benchmark therefore never contained parallax at all. V9 adds scenes that do.

## Method
For every interval frame *i* the check pairs *i* with *j = i+3* and *k = i+6* (or the frames behind, at the end of a clip). It uses the camera already solved for 3D placement: intrinsics *K* and per-frame poses relative to the tracked surface.
1. **Correspondences.** Dense DIS optical flow from *i* to *j*, and back, inside the object's region plus a margin. Flow that fails the forward-backward check (more than 1 px) is **unreliable** and is never used to explain anything away.
2. **Plane.** The homography of the tracked surface comes from the poses (`K[r1 r2 t]`), not from a dominant-motion fit. Pixels whose flow agrees with it (within 1 px) are on the surface.
3. **Rigid scene off the plane.** A static point at any depth satisfies the epipolar constraint of the known camera motion (`F = K⁻ᵀ[t]ₓRK⁻¹`). Pixels within 1.5 px of their epipolar line are triangulated. Points with less than 0.25° of parallax remain unresolved, and so do points that would lie *beneath* the tracked surface while being seen through it (physically impossible).
4. **Foreground motion.** Pixels that change photometrically after plane compensation (blurred difference above 30) and are explained neither by the plane nor by a triangulated rigid point. Policy: more than **3%** of the object region on any frame gives `FOREGROUND_MOTION_IN_OBJECT_REGION`. The threshold is unchanged from V8, and the classifier is stricter about what it explains away.
5. **Occlusion.** A triangulated static point inside the object's silhouette is an occluder only if it is clearly off the plane (more than 2 px), at least 1 cm above the surface, nearer to the camera than the object's surface along the same ray (by more than 3%), and its 3D position reprojects onto its flow match in the third view *k* (within 1.5 px). Policy: more than **2%** of the silhouette on any frame gives `OBJECT_OCCLUDED`.

Walls and other background behind the object are explained as rigid points and do not count.

**Known limits.** An object moving along the epipolar lines at exactly the speed a static point would have is indistinguishable from one; this is a fundamental monocular ambiguity, and it counts as explained. Untextured occluders produce no flow and are not detected. Static occluders that also cover the tracked surface degrade the camera solve first; they are then rejected by the planar layer (`PARTIAL_OCCLUSION_UNSUPPORTED`) or as unexplained motion, rather than as `OBJECT_OCCLUDED`.

## New benchmark scenes (labels set by construction)
| Kind | Suitable | What it tests |
|---|---|---|
| `orbit_wall`, `dolly_wall` | yes | A textured wall stands behind the table; the bottle's top projects onto the wall, giving genuine parallax inside the object region |
| `static_occluder` | no (`OBJECT_OCCLUDED`) | A static card stands on the table between camera and product. The field of view is supplied, so the occlusion check itself is exercised |
| `occluder_wall` | no (`FOREGROUND_MOTION_IN_OBJECT_REGION`) | The moving occluder of V8 with the wall present: motion must still be caught when parallax is around |

The 8 V8 kinds are unchanged; their fixtures were verified to be byte-identical after the generator change.

## Development (seeds 0–1): design and fixes
Measured on dev only, before freezing:
- **First version.** The motion test separated cleanly (suitable clips at most 0.027; moving occluder 0.43). The occlusion test, however, flagged up to 4.3% of the silhouette on **plane-only** clips: noisy flow on table pixels triangulated slightly "in front".
- **Fix 1:** occluders must be clearly off the plane and physically above it. Remaining plane-only false occlusion: 2.9% on a 4-frame run (dolly s1).
- **Fix 2:** three-view confirmation. Result: **0.0% false occlusion on all 14 suitable dev clips**.
- **Final dev state:**
  - suitable clips: moving fraction at most 0.0248 (dolly s1, mostly unreliable flow in the aliased far field), against the 0.03 limit;
  - static occluder: rejected in 2 of 2 clips, though the occlusion code fired in only 1;
  - moving occluder: rejected wherever a camera could be solved.
- **Fixture change on dev:** the card was moved off the mat (offset 12 cm) after dev showed it broke the camera solve.

## Held-out results (frozen code)
`python -m benchmark.run_3d_benchmark --seeds 5 6 7` → `benchmark/results_3d_v9.json`. Seeds 5–7 were never used before the freeze. 12 kinds × 3 seeds = 36 clips (18 suitable, 18 unsuitable).

| Metric | Seeds 5–7 (fresh held-out) | Seeds 2–4 (V8 held-out, inspected in V9) |
|---|---|---|
| Correct decisions | **35/36** | 36/36 |
| False accepts | **0/18** | 0/18 |
| False rejects | **1/18** (`topdown_user_fov` s5) | 0/18 |
| Rejections with the expected code | 11/18 | 14/18 |
| `OBJECT_OCCLUDED` raised on `static_occluder` | 1/3 (all 3 rejected) | 1/3 (all 3 rejected) |
| Accepted: focal error / rotation / anchor max | ≤ 0.79% / ≤ 0.74° / ≤ 0.62 px | ≤ 1.04% / ≤ 0.86° / ≤ 0.66 px |
| Silhouette IoU (min) | 0.92 | 0.90 |
| Renders passing output validation | 17/17 | 18/18 |
| Median analysis time per clip | 11.0 s | 9.3 s |
| The V8 check on the same clips would have falsely rejected | 0 | 1 (dolly s2) |

**What this does and does not show:**
- **False rejections are not lower.** Across all 72 clips, V9 and the V8 check each have **one** false rejection. The walled scenes did not trouble the V8 check either, because the table still dominated its full-frame fit.
- **What V9 adds:**
  - a geometric account of every flagged pixel (each frame carries a breakdown into unreliable flow, independent motion and unresolved depth);
  - an occlusion test; it fired on 2 of 6 static-occluder clips, and all 6 were rejected;
  - stronger evidence on occluders, with moving fractions of 0.27–0.64 against 0.07–0.18 for the V8 check.
- **V9's held-out failure (topdown s5).** On frames 45–48, table pixels near the bottle had flow about 1 px away from the pose-derived plane homography (the pose's own reprojection error was up to 0.93 px), so they were treated as off-plane. Triangulation then put them a few millimetres *beneath* the surface, the "impossible point" rule marked them unresolved, and they counted as motion (0.097). The cause is a fixed 1 px plane tolerance that ignores pose accuracy. The fix (tolerance scaled by the pose's reprojection error) is a change made after seeing held-out data, so it must be validated on new seeds (8 and up) before adoption. **It is not applied in this release.**
- **Synthetic only.** These scenes share the renderer's shading and contain no real noise, motion blur, rolling shutter or compression, so real-footage behaviour is unknown.
