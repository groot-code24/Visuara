# Rendering capability matrix (V8, as built)

| Scenario | Target capability | V8 behaviour | Evidence |
|---|---|---|---|
| Product on a table or wall (flat decal) | 2D planar | **Supported**: `planar_tracked`, linear-light premultiplied, temporal colour match | 2D benchmark; gate benchmark |
| Product on a moving planar surface | Tracked planar | **Supported**: LK + RANSAC + ECC re-anchoring; every frame validated | Gate benchmark; orbit corner error 0.82 px |
| Changing camera perspective | Camera-aware | **Supported** for planar surfaces (homography) and for 3D (estimated camera) | 3D benchmark |
| Product standing on a surface (3D object) | 3D scene integration | **Supported (new)**: `tracked_3d`, GLB, estimated focal + pose, shading, cast and contact shadows; requires real surface size; rejected if focal unobservable unless FOV supplied | 3D benchmark |
| Significant surface curvature | Advanced geometry | **Rejected** (`NON_PLANAR_OR_UNSTABLE` / `TRACKING_DRIFT`) | Gate benchmark (deforming) |
| Product behind/in front of scene objects | Occlusion-aware | **Rejected** (`PARTIAL_OCCLUSION_UNSUPPORTED`, `FOREGROUND_MOTION_IN_OBJECT_REGION`) | Gate + 3D benchmarks |
| Reflections on glass or water | Optical integration | **Not detected specifically**; reflective surfaces usually fail photometric checks; metals are shaded as dielectric. Treated as unsupported. | None (gap) |
| Product held by a person | Articulated interaction | **Unsupported**; usually rejected as non-planar or foreground motion | None (gap) |
| Strong motion blur | Motion-aware | **Rejected** (`MOTION_BLUR_UNSUPPORTED`); no blur synthesis | Gate benchmark |
| Across scene cuts | Multi-shot | **Rejected** within one placement (`SCENE_CUT_IN_INTERVAL`); split into per-shot placements | Gate + 3D benchmarks |
| Lighting change | Relighting | **Moderate change matched** (≤ ±25–33% surface luminance); larger rejected | 2D benchmark (exposure drift −37% ΔE) |
| Screen-space sticker | Overlay | Supported (`overlay_2d`); labelled "not scene-anchored" | Tests |
| Generative editing | `GENERATIVE_ASSISTED` | **Unavailable** (no verified provider; outputs not automatically validatable) | Routing tests |

Renderer modes as declared in `GET /api/renderers`: `overlay_2d` (A), `planar_tracked` (B), `tracked_3d` (B3D), `occlusion_aware` (C, not implemented), `replicate_generative` (D, unverified). Routing picks the cheapest verified provider supporting the analysed scene class and never falls back silently.
