# Dependency map

**Generated** 5 November 2026 from the import statements in `app/` (an AST walk, not by hand). To regenerate it, walk the `ImportFrom` nodes of each `app/*.py` and keep the names that are `app` modules.

## 1. Layers

```
Browser ──► web/ (Next.js 16, proxy.ts: session gate + CSP nonce)
              │  /api/*, /auth/google/*  (vercel.json rewrites; next.config.ts rewrites locally)
              ▼
            api service: app.main (FastAPI) ──► routers: auth, orgs, members, videos, uploads, decisions,
              │                                  intelligence, inventory, profiles, catalogue, campaigns,
              │                                  commercial, delivery, programmatic
              │  POST /api/internal/run-jobs (bearer derived from CC_SIGNING_KEY)
              ▼
            runner service: the same image; app.jobrunner.run_one() ──► decisions, intelligence, render, outbox
              │
              ├──► app.db: PostgreSQL (Neon) or SQLite        ├──► app.storage: Vercel Blob / S3 / local disk
              └──► FFmpeg, FFprobe, OpenCV (in the image)      └──► SMTP, Google OIDC (outbound)
```

## 2. Module imports (`app/`)

| Module | Lines | Imports these `app` modules |
|---|---|---|
| `main` | 1,232 | almost every module: it creates the app, holds the legacy routes, and includes the 14 routers |
| `analysis_versions` | 65 | outbox |
| `assets3d` | 165 | — |
| `auth` | 427 | jobrunner, mailer, main, members |
| `blobstore` | 131 | — |
| `camera` | 104 | — |
| `campaigns` | 409 | catalogue, commercial, compositor, decisions, inventory, main, opportunities, orgs, outbox, storage |
| `catalogue` | 147 | compositor, decisions, inventory, main, orgs, storage |
| `commercial` | 241 | db, inventory, main, orgs, outbox |
| `compositor` | 285 | media |
| `config` | ~120 | — |
| `db` | 189 | — |
| `decisions` | 552 | compositor, jobrunner, limits, main, obs, quality, storage, tracking |
| `delivery` | 167 | inventory, main, members, outbox, storage, video_versions |
| `intelligence` | 485 | analysis_versions, decisions, inventory, jobrunner, limits, main, obs, opportunities, outbox, scoring, storage, videos |
| `inventory` | 370 | db, main, opportunities, orgs, outbox, storage |
| `jobrunner` | 116 | decisions, intelligence, main, maintenance, obs, outbox, storage |
| `lifecycle` | 62 | — |
| `limits` | 205 | main, storage |
| `mailer` | ~62 | config |
| `maintenance` | 54 | inventory, main, obs, storage, videos |
| `media` | 57 | — |
| `members` | 310 | auth, limits, mailer, main |
| `migrations` | 502 | — |
| `obs` | ~190 | jobrunner, main, storage |
| `opportunities` | 125 | — |
| `orgs` | 117 | limits, main, members |
| `outbox` | 144 | main, obs |
| `profiles` | 179 | decisions, inventory, main, orgs, storage |
| `programmatic` | 88 | main, orgs |
| `quality` | 288 | media, tracking |
| `render3d` | 430 | assets3d, camera, compositor |
| `renderers` | 63 | — |
| `scoring` | 107 | decisions, opportunities |
| `storage` | 253 | blobstore, main |
| `tracking` | 246 | compositor, media |
| `uploads` | 119 | blobstore, limits, main, storage |
| `validation` | 129 | compositor, media |
| `video_versions` | 30 | outbox |
| `videos` | 157 | limits, main, obs, outbox, storage, video_versions |

**Most depended on:** `main` (20 importers), `storage` (14), `outbox` (10), then `inventory`, `compositor`, `decisions`, `limits`, `obs` and `orgs` (7 or 8 each). A change to `main.db`, `main.current_user`, `main.owned` or `storage.*` reaches almost every route, so it needs the full suite on both databases.

**Cycles:** `main` imports the routers at the bottom of the file, after defining `db`, `current_user` and the helpers, and the routers import `main`. These are deliberate late imports (`# noqa: E402`). Moving the helpers out of `main` would remove the cycle; that is debt, not a defect.

**Pure modules** (no `app` imports, testable alone): `opportunities`, `migrations`, `db`, `config`, `media`, `lifecycle`, `renderers`, `camera`, `assets3d`, `blobstore`.

### Added in M14
| Module | Imports | Imported by |
|---|---|---|
| `detection` | numpy, OpenCV DNN (lazily) | `intelligence`, `ops/fetch_models.py`, `benchmark/evaluate_detection.py` |
| `objtrack` | numpy | `intelligence`, `benchmark/evaluate_tracking.py` |
| `surfaces` | OpenCV, numpy, `decisions` (quad ordering, orientation, IoU) | `intelligence`, `benchmark/evaluate_intelligence.py` |
| `understanding` | OpenCV, numpy | `intelligence` |
| `vision` | OpenCV (image re-encoding), urllib | `intelligence`, `main._ollama_describe` |
| `scoring` (v2) | `opportunities`, `decisions.CATEGORIES` | `intelligence`, `catalogue` (`CONTEXT_CATEGORIES`) |
| `inventory.require_sellable` | `inventory.eligibility` | `inventory.reserve`, `campaigns.approve_request`, `campaigns.start_production` |

### Added in M15 to M17
| Module | Imports | Imported by |
|---|---|---|
| `payments` | `main`, `outbox`, `orgs`, `commercial` (ledger posting, idempotency), `inventory.notify`, urllib | `main` (router), `commercial.mark_paid`, `config.production_problems` |
| `agencies` | `main`, `members` (roles) | `main.current_user`, `orgs` (switching, listing) |
| `programmatic` (M17) | `main`, `orgs` | `main` (router), `config.production_problems` (test adapters) |

## 3. Source of truth for each entity

| Entity | Table (authoritative) | Written by | Notes |
|---|---|---|---|
| Organization, kind | `organizations` | `auth.complete_signup`, `orgs` | A kind is locked once marketplace records exist |
| Membership and role | `memberships` | `members.sync_membership`, `members.set_role` | `users.org_id` and `member_role` are legacy mirrors kept in sync; access reads `memberships` |
| Session, active organization | `sessions` | `auth._start_session`, `orgs` (switch) | Token stored as SHA-256 |
| Sign-in challenge | `auth_challenges` | `auth.email_start` | HMAC'd code, single use, 5 attempts |
| Video and versions | `videos`, `video_versions` | `main.ingest_video`, `videos` | SHA-256 per version; soft deletion |
| Analysis | `analysis_versions`, `scenes`, `scene_frames` | `intelligence` | Immutable per version; the legacy `frames` table is refilled for older views (debt 19) |
| Opportunity | `opportunities` | `intelligence`, `inventory` (review) | Measured data never changes after creation; reviews are recorded separately |
| Scoring configuration | `scoring_configs` | `scoring` | Versioned; stored with each opportunity |
| Inventory and reservation | `inventory_items`, `reservations` | `inventory` | A conflicting reservation is refused by a unique index (409) |
| Product and assets | `products`, `product_assets` | `main.create_product`, `catalogue` | A campaign copy links back with `source_product_id` |
| Rights | `rights_records` | `profiles`, administrator decision | Evidence type checked from the file's content |
| Campaign | `campaigns`; changes in `status_transitions` | `campaigns`, `inventory.transition` | Optimistic `version` column |
| Placement request | `placement_requests` | `campaigns` | Two-party approval |
| Placement and render | `placements`, `jobs` | `main` | Approval bound to the placement version |
| Money | `ledger_entries`, `invoices`, `revenue_allocations`, `quotes` | `commercial` | Integer minor units; append-only ledger; `Idempotency-Key` |
| Delivery | `delivery_records`, `delivery_evidence` | `delivery` | Evidence carries SHA-256 hashes |
| Events for other modules | `outbox_events` | `outbox.emit` | Delivered at least once; consumers are idempotent (`source_event_id`) |
| Audit and activity | `events` (placement lifecycle, sign-ins, actions); `status_transitions` (marketplace records) | everywhere | Not a substitute for the ledger |
| Schema | `schema_migrations` | `migrations.apply` | Checksummed; never edited after release |

## 4. Workflows and the modules they cross

| Workflow | Path through the code |
|---|---|
| Sign in by email | `web/app/login` → `auth.email_start` → `mailer.send` → `web/app/verify` → `auth.email_link_confirm` → `auth._resolve` → `sessions` |
| Upload a video | `web` uploaders → `uploads.init_video_upload` → Blob/S3 direct PUT → `uploads.complete_upload` → `main.ingest_video` → `videos` |
| Analyse | `intelligence` (POST) → `analysis_versions` job → `jobrunner.run_one` → `intelligence` stages → `opportunities`, `scoring`, `outbox` |
| List inventory | `inventory.list_item` → rights check (`profiles`) → `inventory_items` |
| Buy a placement | `campaigns` → `inventory.reserve` → `campaigns` (request) → publisher approval → `commercial` (quote, invoice) → administrator payment (`commercial`) |
| Produce | `campaigns.start_production` → product copy (`catalogue`) → `main.create_placement` → quality check (`quality`, `tracking`) → approval → render (`compositor`, `validation`) |
| Deliver | `delivery.record_delivery` → evidence → signed link (`main._sign`) → `delivery.delivery_media` → acceptance → `commercial` allocation |
| Report | `delivery.campaign_report` → `outbox.deliver` → `analytics_events` |
