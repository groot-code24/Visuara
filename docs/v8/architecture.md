# V8 architecture

A modular monolith: one FastAPI process plus durable SQLite-queue workers sharing a data directory. No new services were introduced; the Docker daemon was unavailable and no requirement demanded them yet (ADR-007 still holds).

```
Browser (app/static/index.html: Studio, Commerce, Marketplace)
   │  session cookie + CSRF; signed links for share/download
FastAPI app/main.py ── app/commerce.py (listings, campaigns, offers, grants, reports)
   │                ── app/lifecycle.py (placement state machine, versions)
   │                ── app/renderers.py (capability registry, routing)
   ├─ quality check (sync, ≤60 s span) ─ app/tracking.py (LK + RANSAC + ECC re-anchoring, per-frame measurements)
   │                                    ├ app/quality.py (planar policy + 3D policy → PASSED/FAILED with reasons)
   │                                    └ app/camera.py + app/render3d.py (3D: focal, pose, foreground motion)
   └─ jobs (SQLite: leases, heartbeat, recovery, dead-letter) ─ worker.py / inline thread
          └ plan = Planar2DPlan | Tracked3DPlan  (app/compositor.py, app/render3d.py)
              ├ render_plan → FFmpeg (single encode, audio kept)
              └ validate_plan → decode output, compare every interval frame with the plan, exposure
Storage: SQLite + local data directory (uploads, derived assets, analyses, outputs); ops/backup.py
```

## Key decisions (V8)
| ADR | Decision | Why |
|---|---|---|
| ADR-013 | **Render plans.** Every renderer is a plan object (`indices`, `apply`, `mask`) used identically by preview, export and validation. | Preview/export parity is structural, not a convention; validation needs no renderer-specific code. |
| ADR-014 | **Linear-light premultiplied compositing**, area-prefiltered before perspective warp. | Measured: removes edge darkening; −0.25 ΔE on every clip. |
| ADR-015 | **Colour integration measured from the footage only.** Per-channel surface gain relative to the reference frame, smoothed, clamped, with a ±2% deadband. Absolute exposure/temperature are reviewer settings. | The footage reveals *relative* lighting change; absolute scene illuminant is not recoverable without calibration. |
| ADR-016 | **3D = GLB on a tracked plane with known real size.** Focal from planar calibration over many views, rejected when unobservable; pose by IPPE with continuity; software rasteriser (2× supersampled, back-face culled, depth-ordered faces) with Lambert + Blinn and planar shadows. | No GPU or 3D libraries were available; this is deterministic, testable against ground truth, and honest about its scope. A GPU/PBR renderer can replace `Tracked3DPlan` behind the same plan interface. |
| ADR-017 | **Cross-organization licensing via grants.** Access to another org's video only through an accepted offer's grant; `owned()` centralises the check. | One choke point instead of per-endpoint special cases; revocation cascades through it. |
| ADR-018 | **Signed links are HMAC tokens re-checked at use**, redacted from access logs. | Short-lived sharing without weakening revocation. |
| ADR-019 | Reference-anchored ECC in the shared tracker. | Fixes drift for 2D and 3D; benchmarked against the frozen V6 gate. |

## Not changed (deliberately)
PostgreSQL migration and S3-compatible storage (blocked: no Docker or credentials), generative rendering (no verified provider), occlusion-aware compositing (no segmentation or depth model available offline).
