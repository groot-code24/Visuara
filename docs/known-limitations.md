# Known limitations

**Release status: INCOMPLETE.** Suitable for a supervised local demonstration. See [release-readiness.md](release-readiness.md).

## Supported operating envelope (what the system will render)
- **Tier A, screen-space overlay:** any analysed video up to 600 s and 4K. The product is fixed on screen and is *not* an in-scene insertion.
- **Tier B, tracked planar surface:** a flat, rigid, textured surface (≥ 25 trackable features) that stays ≥ 85% in frame, with no scene cut, no occlusion, no strong blur and brightness within ±25–33% of the reference frame. Reference to end must be ≤ 60 s.
- Everything else is **refused** with a reason code: occlusion, curved or deforming surfaces, cuts, heavy blur, lighting changes, low texture, out of frame, too small.

## Visual realism
- No relighting, shadows, reflections or motion-blur synthesis. That is why the corresponding scenes are rejected rather than rendered.
- The product is a flat decal fitted inside the surface quad. Free-standing 3D objects (for example a bottle standing on a table) are **not** supported. Rendering such an object as a decal on the tabletop will look wrong, and only human fit review catches it.
- The quality check proves geometric stability, not that the product belongs in the scene. Human fit review (≥ 0.75, an attestation rather than a probability) is mandatory.

## Measurement
- Thresholds (`planar-v0.1-heuristic`) are **uncalibrated**. They were validated only on a synthetic benchmark of 70 clips. Zero false accepts on 21 held-out unsuitable clips gives a 95% upper bound of about 14%, and says nothing about real footage.
- Deforming surfaces are rejected but often labelled `TRACKING_DRIFT` instead of `NON_PLANAR_OR_UNSTABLE`.
- Output validation assumes constant frame rate and sequential decode. Variable-frame-rate sources and rotation metadata (phone portrait video) are not handled specially and may fail validation (fail-closed).
- Corners are set on one reference frame. There is no multi-keyframe correction of a drifting track; the user shortens the interval instead.

## Platform
- SQLite and local disk; single host. No PostgreSQL, object storage, encryption at rest, signed URLs, backup/restore drill, or multi-host queue guarantees.
- Quality analysis runs synchronously inside the API request.
- Brands and creators share one organization (by invitation). No cross-organization licence grants, agency workspaces, discovery, pricing, earnings or billing.
- Campaign analytics count observed clicks and imported conversions only. No attribution windows, refunds or causal-lift measurement.
- Generative rendering (Replicate adapter) is unverified and unavailable. Occlusion-aware rendering is not implemented.
- Browser QA covered headless Chrome at 1366 px and 390 px. Not tested: Safari, Firefox, screen readers. The placements table scrolls horizontally on phones.
