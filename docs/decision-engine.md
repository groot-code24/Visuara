# Decision engine

Given one to four products (two to four is the intended use) and one or more videos, the engine decides which product fits where and explains why. It runs on the render workers as a queued job (`app/decisions.py`), so the web request returns at once and the page shows progress.

## Steps for each video
1. **Keyframes.**
   - If the video has not been analysed yet, scene-change detection extracts up to 8 keyframes.
   - If a local vision model is configured (`CC_VISION_MODEL`, Ollama), each keyframe also gets a one-sentence description.
2. **Surfaces.** Each keyframe is searched for flat quadrilaterals:
   - method: edges, then contours, then 4-point convex polygons;
   - kept: polygons covering 2–55% of the frame, with plausible proportions;
   - ranked by size and texture, because texture is what makes tracking possible;
   - near-full-frame shapes are skipped, since they are usually the background itself.
   - The best `CC_DECISION_CANDIDATES_PER_VIDEO` (4 by default) across keyframes, at most 2 per keyframe, go on.
   - Each is classified by its shape as **upright** (wall, sign, screen), **horizontal** (table, counter or floor seen at an angle) or **angled**.
3. **Tracking.**
   - Each candidate is tracked for `CC_DECISION_WINDOW_SECONDS` (5 s by default) with the same tracker the renderer uses.
   - The tracker uses optical flow and RANSAC homography, with every frame refined against the reference frame.
   - The corners are rounded exactly as a placement will store them. A placement made from a recommendation therefore repeats the identical computation, and its quality check reaches the same verdict (regression-tested).
4. **Scoring every product on every candidate.** The product is fitted into the surface at its true proportions, then evaluated with the placement quality policy (`quality.evaluate_track`):
   - **Trimming:** if it fails part-way, the window is cut to the frames before the first failure and re-checked, never extended.
   - **Minimum length:** a window shorter than 1.2 s is not trimmed further, and the option keeps its failing result.

   Weighted factors:

   | Factor | Weight | Measure |
   |---|---|---|
   | Screen time | 25% | Validated seconds, full marks at 4 s |
   | On-screen size | 20% | Product area as a share of the frame: full marks between 3% and 35% |
   | Shape fit | 15% | How much of the surface the product fills at its own aspect ratio |
   | Stability | 15% | Median tracking match (NCC) and reprojection error |
   | Visibility | 10% | Colour difference (CIELAB ΔE) between the product and the surface |
   | Scene context | 15% | How well the product's category suits the surface type; with a vision model, keyword hints from the keyframe description |

   An option that would fail the quality check keeps only 40% of its score and is never recommended.
5. **Assignment across videos.**
   - Dynamic programming finds the exact assignment of at most one product per video that maximises the number of confident placements, then the total score.
   - With **spread** on (the default), each product is used at most ceil(videos ÷ products) times, so several products don't all land on the same video.
   - A video needs a passing option scoring at least **0.55** to get a recommendation. Otherwise it is marked "No confident fit", with an explanation and the closest options.
   - Ties go to earlier videos (deterministic).
6. **Previews.** The engine composites each product's best option into its keyframe, so people can see every option.

## What a person does with the result
Turning a recommendation into a video takes these steps; nothing is rendered without them:
1. **Create placement:** "Create placement" (or "Use this instead" for another product's option) makes a **draft** tracked placement from the stored geometry and time window.
2. **Quality check:** the placement goes through its own frame-by-frame quality check.
3. **Approval:** a manager approves it, attesting a fit confidence of at least 0.75. The approval is bound to the version and content hash.
4. **Render and validation:** the placement is rendered, and the output is validated frame by frame before download.

## Honest limits
- **Planar surfaces only.**
  - Products appear on flat, rigid surfaces such as posters, signs, screens, packaging faces and tabletops, as a flat graphic. With only a product photo, a bottle cannot be stood up in 3D. That needs a `.glb` model and the 3D placement path (API only in V11).
  - Detection is contour-based. It finds clearly bounded rectangles and misses soft-edged or heavily cluttered surfaces.
- **Not calibrated.**
  - Category-to-surface affinities and the weights are hand-set heuristics, not learned from data.
  - Without a vision model, "context" means surface type only. The engine does not know that a scene is a kitchen.
- **Synthetic validation only.**
  - **Tests:** synthetic "room" clips (`benchmark/fixtures_scene.py`):
    - the poster and tabletop are found;
    - a bare room gets "no confident fit";
    - the assignment is exact;
    - recommendations pass their own placement check and render.
  - **Not measured:** real-footage accuracy.
- **Runtime.** On the test machine, a 4-second 640×360 clip takes about 30 seconds per video (61.6 s for two videos in browser QA). Long or high-resolution videos take longer, roughly in proportion to the candidates × the window × the resolution.
