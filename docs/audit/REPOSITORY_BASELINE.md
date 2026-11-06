# Repository baseline

Audited 4 November 2026 at commit `3bf569f` on `main`. Everything here is taken from the source files and from test runs on this machine; file and line references are to that commit.

## Stack
| Layer | What is there | Evidence |
|---|---|---|
| API | Python 3.12, FastAPI 0.162, uvicorn. One process serves every route. | `requirements.txt`, `app/main.py` |
| Data access | Hand-written SQL, no ORM. One connector runs the same SQL on SQLite (development, tests) and PostgreSQL (Neon), translating `?` placeholders. | `app/db.py` |
| Schema changes | `CREATE TABLE IF NOT EXISTS` at start-up, plus additive column migrations in `_MIGRATIONS`. A fingerprint skips the work when nothing changed. There are no down-migrations. | `app/main.py:201-240` |
| Database | 22 tables; see [Data model](#data-model). | `app/main.py`, `app/auth.py`, `app/decisions.py` |
| Media storage | Private Vercel Blob or any S3-compatible bucket, with local disk for development. Signed, short-lived download links. | `app/storage.py`, `app/blobstore.py` |
| Video processing | FFmpeg and FFprobe for decoding, metadata and encoding. OpenCV for scene cuts, contour surfaces, optical-flow and homography tracking, colour matching and validation. | `app/main.py:310-370`, `app/tracking.py`, `app/compositor.py`, `app/validation.py` |
| AI models | None are bundled. An optional local vision model (Ollama, `CC_VISION_MODEL`) writes one sentence per keyframe. Without it, scene context is the surface type only. | `app/main.py:325-335`, `docs/decision-engine.md` |
| 3D | Optional `.glb` models rendered with a software rasteriser and estimated camera pose from a tracked plane. | `app/render3d.py`, `app/camera.py`, `app/assets3d.py` |
| Background work | Jobs and decisions are database rows with leases, retries and dead-lettering. On Vercel they run on demand through `/api/internal/run-jobs`; elsewhere `worker.py` polls. | `app/jobrunner.py`, `app/main.py:96-125`, `app/decisions.py:385-430` |
| Auth | Google OpenID Connect with PKCE, and email magic link or six-digit code. No passwords. Sessions are hashed tokens in HTTP-only cookies, with a CSRF double-submit token. | `app/auth.py` |
| Web app | Next.js 16 (App Router), React 19, Tailwind 4, TypeScript 6. The browser only talks to its own origin; `/api` is routed to the API. | `web/` |
| Hosting | One Vercel project with three services: `web`, `api` and `runner` (both containers from `Dockerfile.vercel`). | `vercel.json`, `docs/vercel.md` |
| CI | GitHub Actions: lint, tests on SQLite and PostgreSQL 16, audits, secret scan, web build, image smoke test. Never confirmed to have run. | `.github/workflows/ci.yml` |

## Size
About 5,900 lines of Python in `app/`, `ops/` and `worker.py`; `app/main.py` alone has 1,179. There are 69 API routes in five routers and 15 web pages.

## Data model
| Table | Holds |
|---|---|
| `organizations` | A workspace: id, name, plan |
| `users` | A person; **belongs to exactly one organization** (`users.org_id`) with one `member_role` |
| `sessions`, `auth_challenges`, `pending_signups`, `user_identities` | Sign-in state and linked Google identities |
| `invitations` | Pending workspace invitations |
| `products` | Product image, optional 3D model, category, SHA-256 of the source image |
| `videos` | Uploaded video, metadata, rights confirmation and licence expiry |
| `frames` | Keyframes from scene-change detection: second, image, description, score |
| `decisions` | Decision-engine runs: inputs, progress, checkpoint, result JSON, lease |
| `placements` | A product on a surface of a video for a time window, with status and version |
| `placement_versions` | Immutable snapshot and content hash of every placement version |
| `placement_analyses` | Quality-check report and stored track for a version |
| `placement_approvals` | Approvals bound to placement and version |
| `placement_rights` | Channels, territories, dates, consent and revocation for a placement version |
| `jobs`, `queue_events` | Render jobs and their queue history |
| `upload_sessions` | Direct-to-storage uploads and their outcome |
| `events` | Workspace audit log |
| `rate_limits`, `schema_fingerprint` | Infrastructure |

## Workflows that work today
Each is covered by the browser walkthrough (`qa/browser_qa_web.py`) and the Vercel rehearsal (`qa/vercel_rehearsal.py`), in addition to the backend tests:
1. Sign up by Google or magic link, onboarding to a workspace, and returning sign-in.
2. Add product images and upload videos, including straight to storage in parts.
3. Run the decision engine across 1–4 products and many videos, and see ranked, explained options with previews.
4. Turn a recommendation into a placement, run the frame-by-frame quality check, approve, render, validate and download through a signed link.
5. Invite members with roles. Plans and usage limits are enforced by the API.

## Test baseline
See [REGRESSION_LOG.md](../execution/REGRESSION_LOG.md) for the exact list of results recorded before any Phase 1 change.

## History that matters
V11 removed a creator marketplace (`app/commerce.py`: listings, campaigns, offers, licence grants, earnings) and an invoice ledger (`app/payments.py`) to narrow the MVP. Both are in commit `eac6a91`. They are useful references for the Marketplace workflows. They are not reused as-is: they stored money as floating-point dollars and had no reservations or concurrency control.

## Live deployment
`https://www.visuara.si` serves the web app. The API there fails to start because the project's environment variables are not set in Vercel (its log reports `DATABASE_URL is not set`). See [BLOCKERS.md](../execution/BLOCKERS.md).
