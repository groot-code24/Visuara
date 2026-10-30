# Domain model

- **Organization:** collaboration and tenancy boundary.
- **User:** email, PBKDF2-SHA256 password hash, role (`brand` | `creator`), organization. Joins an existing organization only by a hashed, single-use, email- and role-bound invitation.
- **Session:** SHA-256 of a random token; 7-day expiry.
- **Product:** organization-owned image (PNG/JPEG/WebP, validated) with SHA-256.
- **Video:** organization-owned source, FFprobe metadata, licence declaration and optional expiry; `created_by` is the **rights holder**.
- **Frame:** analysed keyframe (second, JPEG, heuristic score).
- **Surface proposal:** heuristic quadrilateral on a keyframe with review status.
- **Placement:** video and product, mode (tracked quad or overlay), reference/start/end, `status` (see [placement-lifecycle.md](placement-lifecycle.md)), `version`, `content_hash`, `fit_status`, `quality_status`, `analysis_id`.
- **Placement version:** immutable snapshot and SHA-256 per version.
- **Placement analysis:** quality report, scene class, policy version, track file and its SHA-256, bound to version and content hash.
- **Placement rights:** current-version consent, channels, territories, start/expiry, revocation.
- **Placement approval:** user, role, version, content hash.
- **Job:** status, attempts, lease token and expiry, cancel flag, placement version, analysis, renderer, output path, validation report.
- **Queue event / Event:** append-only operational and audit records.
- **Tracked link / Conversion:** version- and channel-bound links; imported conversions deduplicated per organization key.
