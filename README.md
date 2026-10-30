# Context Commerce

Authorized product placement in creator videos. Brands and creators join from the landing page with Google or a one-time email code. Creators list their videos at a fixed price. Brands find scenes that fit a campaign and place a product image or 3D model on a surface tracked through the shot. Every frame of the placement is checked, the creator consents to that exact version, both sides approve, and a deterministic renderer produces a video that is validated frame by frame before it can be downloaded.

**The system renders only what it can render convincingly, and refuses everything else with a reason.**

**Release status (V9): BLOCKED for pilot and production.** Nothing has been validated on real creator footage, and the Neon, Google, email and object-storage integrations have been tested only against local stand-ins. Suitable for supervised demonstrations on synthetic or self-owned footage. See [docs/v9/release-readiness.md](docs/v9/release-readiness.md).

## What it does today
| Capability | State |
|---|---|
| **Landing page; sign-up and sign-in with Google or an emailed one-time code / magic link; onboarding as brand or creator; joining by invitation** | **New in V9.** Working; tested with a stubbed Google token exchange and a local mail outbox (no live Google or SMTP credentials here) |
| **Neon PostgreSQL** (`DATABASE_URL`), SQLite fallback; verified SQLite → PostgreSQL migration | **New in V9.** Full test suite passes on PostgreSQL 16 locally; not yet run against a Neon project |
| **Private S3-compatible object storage**: write-through, restore on any host, presigned delivery | **New in V9.** Tested with an in-process S3 mock |
| Screen overlay (Tier A) and tracked planar decal (Tier B) with temporal colour matching | Working; validated on the synthetic benchmark |
| 3D product (GLB) on a tracked surface: estimated camera, shading, cast and contact shadows | Working on the synthetic benchmark; requires the surface's real size |
| **Parallax- and occlusion-aware 3D scene check** (solved camera + epipolar geometry + triangulated depth) | **New in V9.** Replaces the V8 single-homography check; see [docs/v9/3d-scene-check.md](docs/v9/3d-scene-check.md) |
| Curved, reflective, motion-blurred and occluded scenes | **Refused with a reason**, not rendered |
| Real-footage benchmark: consent-checked manifest, creator-level splits, blind human ratings, agreement statistics | **New in V9.** Tooling tested; **no real footage evaluated** |
| Commerce: listings, campaigns, fixed-price offers, licences, takedown, delivery, earnings records, reports | Working; no payments processed |
| Generative video editing | Unavailable (no verified provider) |

## Setup
Requirements: **Python 3.11+**, plus FFmpeg and FFprobe on `PATH`.

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt          # add -r requirements-dev.txt for tests
cp .env.example .env                     # then fill in what you use (see below)
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000 for the landing page (the app is at `/app`). The schema is created and migrated on startup. Without any configuration the app uses SQLite and local disk, and offers password sign-in only.

### Connect the services (`.env`)
| Service | Variables | Docs |
|---|---|---|
| Neon PostgreSQL | `DATABASE_URL` (Neon console → Connect) | [production-runtime](docs/v9/production-runtime.md) |
| Sign in with Google | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `CC_PUBLIC_URL`; redirect URI `<CC_PUBLIC_URL>/auth/google/callback` | [authentication](docs/v9/authentication.md) |
| Email code / magic link | `CC_SMTP_HOST`, `CC_SMTP_PORT`, `CC_SMTP_USER`, `CC_SMTP_PASSWORD`, `CC_MAIL_FROM` (any SMTP provider) | [authentication](docs/v9/authentication.md) |
| Object storage | `CC_S3_BUCKET`, credentials, `CC_S3_ENDPOINT_URL` for R2, B2 or MinIO | [production-runtime](docs/v9/production-runtime.md) |
| Multi-host | `CC_SIGNING_KEY` (same on every host), `CC_SECURE_COOKIES=1` behind HTTPS | |

Moving an existing SQLite database to Neon: `python -m ops.dbtransfer migrate --sqlite ./data/context_commerce.sqlite3`.

Separate worker process: `CC_INLINE_WORKER=0 uvicorn app.main:app` plus `python worker.py`, or `docker compose up --build` (both read `.env`).

## Workflow
1. Brand and creator each join from the landing page (Google or email code), choosing their role and workspace, or accepting an invitation.
2. Creator uploads a video they hold the rights to, analyses scenes, and lists it at a fixed price.
3. Brand creates a campaign, finds matching videos in the marketplace, and sends an offer. The creator accepts, which creates a licence.
4. Brand places a product image (2D) or a `.glb` model (3D) on a surface. The quality check runs over every frame and either passes, with a preview from the production renderer, or explains the refusal.
5. Creator consents to that exact version; both approve; the render is validated frame by frame; delivery uses access-checked, short-lived links. Revoking the licence stops everything.

## Tests and evidence
```bash
PYTHONPATH=. python -m pytest -q                                   # SQLite
CC_TEST_DATABASE_URL=postgresql://... PYTHONPATH=. python -m pytest -q   # same suite on PostgreSQL (a fresh schema per test)
python -m benchmark.run_benchmark --split all                     # planar gate (synthetic)
python -m benchmark.run_2d_quality --seeds 2 3 4                  # 2D compositing vs ground truth
python -m benchmark.run_3d_benchmark --seeds 5 6 7                # 3D incl. the V9 scene check (fresh held-out seeds)
python -m benchmark.real_footage validate --manifest real/manifest.json   # real-footage protocol (needs consented clips)
python qa/browser_qa.py; python qa/browser_qa_v8.py; python qa/browser_qa_v9.py
```
Observed results: [docs/v9/test-evidence.md](docs/v9/test-evidence.md).

## Engineering documents
**V9:** [release readiness](docs/v9/release-readiness.md) · [authentication](docs/v9/authentication.md) · [production runtime](docs/v9/production-runtime.md) · [3D scene check](docs/v9/3d-scene-check.md) · [real-footage protocol](docs/v9/real-footage-protocol.md) · [test evidence](docs/v9/test-evidence.md) · [security](docs/v9/security-review.md) · [limitations](docs/v9/known-limitations.md)

**V8:** [audit](docs/v8/repository-audit.md) · [architecture](docs/v8/architecture.md) · [capabilities](docs/v8/rendering-capability-matrix.md) · [2D/3D compositing](docs/v8/compositing-2d-3d.md) · [benchmarks](docs/v8/benchmark-report.md) · [API & config](docs/v8/api-and-config.md) · [roadmap](docs/v8/implementation-roadmap.md)

**V6:** [Dependency graph](docs/engineering-graph.md) · [ADRs](docs/architecture-decisions.md) · [Contracts](docs/contracts/) · [Security review](docs/security-review.md)

Only upload media you own or have explicit permission to process and license.
