# Rendering contract

## Provider interface
Every renderer is declared in [`app/renderers.py`](../../app/renderers.py) with: supported scene classes, required inputs, occlusion capability, maximum duration and resolution, input and output formats, estimated cost and time, cancellation and retry behaviour, version, `verified` flag and known limitations. `GET /api/renderers` returns the live registry.

| Provider | Tier | Scene classes | Verified | Available |
|---|---|---|---|---|
| `overlay_2d` | A | OVERLAY_SCREEN_SPACE | yes | yes |
| `planar_tracked` | B | A_STATIC_PLANAR, B_MOVING_PLANAR | yes | yes |
| `occlusion_aware` | C | C_PARTIAL_OCCLUSION | no | no (not implemented) |
| `replicate_generative` | D | D_NON_PLANAR_OR_UNSTABLE | only with `CC_REPLICATE_VERIFIED=1` | no |

## Routing
`route(scene_class, duration, width, height, max_cost)` returns the least expensive provider that is available, verified, supports the scene class, and fits the limits. Otherwise it returns `NO_CAPABLE_RENDERER` with each provider's reason. It never falls back silently to a weaker tier. The requested `quality` parameter on `/route-render` is accepted for compatibility but does not change routing.

## Render input
Exactly the geometry the quality check validated: `{frame_index: product quad in pixels}`, built from the stored analysis (overlay rectangle, or reference-frame product quad mapped through each frame's homography). The product is fitted, centred and aspect-preserving, inside the surface quad.

## Render output and validation
MP4, H.264 (CRF 18, yuv420p), AAC audio when the source has audio, same resolution and frame rate. Written to a temp file named per lease, validated, then moved into place atomically.

A job is `SUCCEEDED` only if [`validate_render`](../../app/validation.py) passes on the real file:
- decodes; duration within 0.15 s; resolution equal; fps within 0.5%; audio preserved; frame count within 1;
- **every interval frame**: luma PSNR ≥ 28 dB against the expected composite inside the product region;
- interval frames outside the product region, and every 5th other frame: luma PSNR ≥ 28 dB against the source;
- interval frames checked == interval frames expected.

The validation report is stored on the job and shown in the UI. Generative outputs cannot be validated this way, so the worker fails them closed.
