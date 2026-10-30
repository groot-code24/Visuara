# Technical debt (after V10)

| Item | Why it matters | Suggested fix | Priority |
|---|---|---|---|
| `app/main.py` is about 1,500 lines of dense one-line style | Hard to review; merge conflicts | Split by domain (videos, placements, jobs, delivery) behind the existing routers; keep behaviour identical, guarded by the test suite | P2 |
| Database-backed rate limiter | Each limited request writes a row. Fine at pilot scale; wakes Neon on auth traffic | Move to Redis or a gateway limiter when traffic grows | P2 |
| Quality check and scene analysis are synchronous HTTP requests (up to about 60 s of tracking) | Ties up a web worker. Vercel's proxy times out, which is why production uses direct API calls | Move them to the job queue with progress polling, like renders | P2 |
| Pose-quality gate (`max_pose_reproj_px` 1.5) is loose for a few frames | V10's tolerance fix accepted a clip with a short accuracy dip (see 3d-tolerance.md) | Per-frame pose-error gate calibrated on new dev seeds, then fresh held-out seeds | P1 |
| Worker wake-up uses a file on the shared data volume | Instant only when API and workers share a host or volume; cross-host enqueues wait for the idle poll (≤ 15 min) | PostgreSQL LISTEN/NOTIFY or a small queue (only when the deployment grows beyond one host) | P3 |
| One account belongs to exactly one workspace | A freelancer working for two brands needs two emails | Memberships table (user ↔ organization with role); `users.org_id` becomes the default workspace | P2 |
| Direct uploads use a single presigned PUT (≤ 5 GB, no resume) | Large uploads on bad connections restart from zero | Multipart upload with per-part presigned URLs | P3 |
| No size limit enforced by the presigned PUT itself | A holder of a fresh upload URL can store a larger object until completion or expiry deletes it | Short TTL (≤ 1 h) and deletion on size mismatch are in place; add bucket-level object size policy if the provider supports it | P3 |
| Frontend is two large HTML files with inline scripts | CSP needs `'unsafe-inline'`; harder to test | Extract JS into files; then drop `'unsafe-inline'` from `script-src` | P2 |
| Software 3D rasteriser | CPU-only; slow for long clips | GPU/PBR renderer behind the same `Tracked3DPlan` interface | P3 |
| Starlette test client deprecation warning (`httpx` → `httpx2`) | Noise only | Switch when FastAPI's test client moves | P3 |
