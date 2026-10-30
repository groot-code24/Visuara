# Architecture decisions

## ADR-001: Modular monolith
- **Decision:** FastAPI API, static HTML/CSS/JS frontend, SQLite persistence, local media directories, FFmpeg processing.
- **Context:** Greenfield repo was not supplied, so this package is self-contained and optimized for local startup.
- **Alternatives:** Next.js + PostgreSQL + Redis + S3-compatible storage. Deferred to avoid blocking a first vertical slice on external services.
- **Tradeoffs:** Simple installation, but local SQLite/files and in-process background tasks are not production scaling primitives.
- **Consequences:** Use a persistent `CC_DATA_DIR`; replace background tasks with a durable queue and object storage before production.

## ADR-002: FFmpeg deterministic compositing
- **Decision:** Overlay a normalized product image onto the video for a selected time range.
- **Context:** Core workflow must produce a real playable output without paid AI services.
- **Alternatives:** Generative insertion or computer-vision tracking. Deferred due to complexity and unavailable credentials/models.
- **Tradeoffs:** Real output is testable, but placement is a 2D overlay without perspective, occlusion, or tracking.

## ADR-003: Shared organization boundary (amended by invitations, ADR-010)
- **Decision:** Brand and creator accounts collaborate by registering with the same organization name.
- **Context:** Keep local MVP account setup simple.
- **Tradeoffs:** This is not a full organization invitation or grant model. Production should add membership invitations and explicit per-video licenses.

## ADR-004: Optional planar perspective tracking
- Decision: use OpenCV sparse optical flow plus homography when a user supplies four normalized surface corners; retain static FFmpeg compositing as the deterministic fallback.
- Context: automatic surface discovery and occlusion-aware tracking are larger CV problems and should not be simulated by the MVP.
- Tradeoff: lower dependency/cost and reproducible behavior, but user-assisted surface selection and failure on low-texture or occluded surfaces.
- Evidence: synthetic-video tracking render passes in `tests/test_api.py`.

## ADR-005: Provider adapter boundary for generative editing
- Decision: add an opt-in Replicate SDK adapter with model-specific JSON input mapping, explicit resource limits, HTTPS output-host allowlisting and actual output media validation.
- Context: video-editing models expose different input schemas and capabilities; a provider token alone does not identify a compatible model.
- Tradeoff: flexible integration but not turnkey until a compatible video-to-video model is selected and live-tested. Provider spend controls must also be configured at the provider account.
- Evidence: configuration-missing failure is tested. Live provider output is not verified because no credentials/model were supplied.

## ADR-006: Shared single-host rate limiting and separate worker option
- Decision: persist auth rate-limit hits in SQLite for processes sharing one DB; add a separate worker process using the same local data volume.
- Context: this closes the in-memory/per-process gap for a single host without introducing Redis as a required local dependency.
- Tradeoff: SQLite is not a distributed database or production queue. Multi-host deployments should use Redis/API gateway limiting, PostgreSQL, object storage and a queue with leases/recovery.

## ADR-007: Keep the modular monolith; add explicit internal interfaces (v6)
- **Decision:** Stay on FastAPI + SQLite + local disk + separate `worker.py`. Introduce module boundaries instead of services: `media`, `tracking` (measurement), `quality` (policy), `compositor`, `validation`, `renderers` (capabilities/routing), `lifecycle` (state machine).
- **Alternatives:** Rewrite on PostgreSQL/Redis/S3 first; split CV into a microservice.
- **Evidence:** The critical defects (A1–A8) were logic errors, not scaling limits. They were fixed and verified without new infrastructure, and Docker was unavailable to verify any new infrastructure anyway.
- **Tradeoffs:** Single-host only. The SQLite schema and `ALTER TABLE` migrations are not a production migration system.
- **Migration:** The interfaces are storage-agnostic, except `db()` and file paths. The next step is versioned migrations and an object-store adapter (roadmap days 61–68).

## ADR-008: Measure every frame; separate measurement from policy
- **Decision:** `tracking.track_surface` records per-frame homography, inliers, RMSE, rectified-patch NCC per cell, sharpness, luminance, cut score and visibility. `quality.evaluate_track` applies a versioned threshold policy and returns reason codes with evidence. PASSED requires every interval frame to pass.
- **Alternatives:** A single model confidence score; first-frame checks; sampling.
- **Tradeoffs:** Cost is linear in interval length (~0.44 s for 61 frames at 480×270). The photometric NCC check conflates deformation with drift (see benchmark).
- **Evidence:** Synthetic benchmark, held-out split 42/42.

## ADR-009: One compositor and frame convention for analysis, render and validation
- **Decision:** All stages decode sequentially and use `media.interval_frames`. The renderer consumes exactly the validated per-frame quads. The validator rebuilds the expected composite with the same function and compares luma PSNR.
- **Alternatives:** FFmpeg `overlay` for static mode (timestamp-based `enable`) plus OpenCV for tracked mode.
- **Tradeoffs:** Python per-frame compositing is slower than the FFmpeg overlay filter. In exchange, boundary frames are unambiguous and validation is exact.
- **Evidence:** 35/35 accepted benchmark renders validated. A 3 px misplacement drops product PSNR from ~35 dB to ~11 dB.

## ADR-010: Immutable placement versions with content hashes; rights holder = uploader
- **Decision:** Snapshot every version (geometry, timing, options, product SHA-256) and hash it. Approvals, analyses and jobs reference version and hash. Only the creator who uploaded a video can consent to or approve placements on it.
- **Alternatives:** Mutable row plus version counter (the previous design; it let old renders pass under new approvals).
- **Tradeoffs:** Uploader-as-rights-holder is a simplification. Real rights can belong to a label, an agency, or several people.

## ADR-011: Renderer capability registry; generative tier unavailable until verified
- **Decision:** Providers declare capabilities and a `verified` flag. Routing picks the cheapest verified capable provider or refuses with `NO_CAPABLE_RENDERER`. Generative output cannot be validated deterministically, so it is never marked successful in this release.
- **Alternatives:** Route "high fidelity" requests to any configured generative model (the previous design).
- **Tradeoffs:** Class C and D scenes are always rejected for now.

## ADR-012: SQLite job leases with tokens and heartbeats
- **Decision:** Random lease token per claim; heartbeat renewal conditional on the token; conditional completion; continuous expired-lease recovery; DEAD_LETTER after `max_attempts`.
- **Alternatives:** A Redis or RQ/Celery queue; PostgreSQL `SKIP LOCKED`.
- **Evidence:** A killed worker process mid-render is recovered by another process in the E2E test, and a stale worker cannot overwrite the result (integrity test).
- **Migration:** The same semantics map directly to PostgreSQL `SELECT … FOR UPDATE SKIP LOCKED`.
