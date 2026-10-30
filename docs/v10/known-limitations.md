# Known limitations (V10)

V9 and V8 limitations still apply unless listed as resolved in [repository-audit.md](repository-audit.md).

## Not verified, so release gates stay open
- **Live services never connected:** Neon, Cloudflare R2, an SMTP provider, Google OAuth, Vercel. Every integration is implemented and was exercised against local stand-ins:
  - PostgreSQL 16 in Docker for Neon;
  - a SeaweedFS S3 gateway with access keys for R2;
  - Mailpit (real SMTP) for the provider;
  - a stubbed token exchange plus local RSA keys for Google;
  - a router emulator for Vercel.
- **Neon scale-to-zero:** measured as app sessions on local PostgreSQL (6 sessions in 32 idle minutes; no connection for the last ~13.5 min), not in a Neon dashboard.
- **No real creator footage evaluated.** All visual-quality and 3D accuracy numbers are synthetic.
- **No independent penetration test.**
- **CI never ran on GitHub** (no remote); the workflow was written and its steps run locally.
- **Arm64 images** (e.g. Oracle Ampere) not built.

## 3D
- **Pose-quality gap:** the pose-aware tolerance accepted a held-out V9 clip (`topdown_user_fov` s5) that V9 had rejected for the wrong reason. Its mean accuracy matches other accepted clips (rotation 0.46°, IoU 0.95), but on 4 frames rotation error reached 2.2° and IoU 0.75. The pose gate (`max_pose_reproj_px` 1.5) does not catch frames with ~0.9 px reprojection error. A per-frame gate calibrated on new data is the fix (not done). See [3d-tolerance.md](3d-tolerance.md) for the fresh held-out results.
- The V9 limits remain: untextured occluders, monocular ambiguity, static occluders over the tracked surface, CPU rendering time.

## Product
- **Payments:** supervised manual invoicing only. No checkout, card processing, automated payouts, tax or currency handling.
- **One workspace per account:** an email belongs to one workspace. Collaborators across workspaces need separate emails.
- **No self-service workspace deletion;** media deletion on request is an operator procedure.
- **Agency workspaces and ad attribution** are unchanged from V9 (shared workspaces only; attribution is observed, not causal).
- **Quality check and scene analysis are synchronous requests.** Fine on direct API calls; too slow for Vercel's proxy (production uses direct mode).

## Platform
- The worker wake-up file needs the API and workers on one host or volume. Across hosts, new jobs wait up to `CC_WORKER_MAX_IDLE_SECONDS`.
- Direct uploads are a single PUT (≤ 5 GB, no resume). The storage layer does not cap object size; the server deletes mismatches on completion.
- CSP allows inline scripts.
- Rate limits live in the database (fine for 10 organizations; Redis or a gateway for more).
- API and workers share credentials (no least-privilege split for workers).
- A database that hangs **without** dropping connections (simulated with `docker pause`) holds requests until the reverse proxy times out. A database crash or restart fails requests in about 10 s (measured). Network partitions are bounded by TCP keepalives and a 20 s TCP user timeout (configured, not tested).
- The worker heartbeat count can over-report for up to 2 minutes after a crash (a clean stop removes the heartbeat).
- MinIO images could not be pulled here, so the local S3 stand-in is SeaweedFS. R2's exact behaviour (CORS and lifecycle API support, error codes) is unverified until a real bucket is used.
