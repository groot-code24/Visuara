# Known limitations (V8)

## Evidence
- **No real footage has been evaluated.** Every quality, 2D and 3D number comes from synthetic, procedurally generated scenes with exact ground truth. Synthetic success does not establish real-world false-acceptance rates. Small samples (21–24 held-out clips per benchmark) give wide uncertainty: 0 errors in 21 still allows up to about 14%.
- Realism (does it *look* real to a person?) has not been rated by humans. The 3D renderer's shading is visibly CG-like (Lambert + Blinn, no environment light, grain or lens effects).

## 2D
- Colour matching follows *relative* changes on the tracked surface. It cannot know the scene's absolute illuminant; absolute exposure and temperature are reviewer settings.
- No automatic background removal; non-transparent product photos are composited as rectangles (warned).
- No grain or noise matching, no motion-blur synthesis (blurred intervals are rejected), no relighting beyond the gain range.
- Edge quality did not measurably change on the benchmark; the premultiplied fix matters for assets with dark transparent pixels.

## 3D
- One object on one tracked plane. The surface's real size must be entered; a wrong size scales the object wrongly and cannot be detected.
- Focal length must be observable from perspective change, or supplied as a field of view.
- Light direction is reviewer-set or a default; nothing is estimated from the footage. Shadows are planar (cast + contact) and clipped to the tracked surface.
- Faces are ordered by depth rather than z-buffered: fine for closed product meshes, but self-intersecting or complex concave models may show ordering artefacts.
- No scene occlusion, depth estimation, reflections, metals (shaded as dielectric), transparency, animation, or deformable objects.
- The foreground-motion check uses a single full-frame homography. Parallax behind the object (walls, depth) is treated as motion and **causes rejections**; one held-out false rejection was caused this way. *(Correction, V9: that rejection was caused by renderer aliasing in a single-plane fixture, not by parallax. V9 replaces the check; see docs/v9/3d-scene-check.md.)*
- Software rendering takes roughly 1–5 s per second of 640×360 footage on this machine. It is CPU-bound with no GPU path.

## Platform and commerce
- SQLite and local disk, single host; no PostgreSQL, object storage, encryption at rest or retention automation.
- Quality analysis runs synchronously inside the API request (≤ 60 s span); 2D tracking now costs ~80 ms per frame because of ECC refinement.
- Payments are not processed; earnings are agreed-fee records. No agency workspaces. No ad-platform analytics: impressions, reach and lift are not measured.
- Marketplace matching is rule-based (category, channels, territories, budget, analysed scenes). There is no audience data, no auctions and no pricing guidance.
- Pillow 12.3.0 with known advisories remains in the local Python 3.10 environment.
- Browser QA: headless Chrome at 1366 px and 390 px only; no assistive-technology testing; no Safari or Firefox.
