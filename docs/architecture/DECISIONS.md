# Architecture decisions

Each decision states the problem, the choice and its cost. A later decision that changes one adds a new entry rather than editing the old one.

## AD-1: Keep the existing stack
**Decision:** FastAPI with hand-written SQL on SQLite and PostgreSQL, Next.js, Vercel Services, Neon and private Vercel Blob.
**Why:** it works end to end and is tested on both databases (see the baseline). An ORM or a different framework would mean rewriting working, tested code without fixing anything the specification needs.
**Cost:** every query is written by hand and must run on both engines, so tests run on both.

## AD-2: A modular monolith, one router per domain
**Decision:** new domains are FastAPI routers in their own modules inside `app/`, sharing one database and one authorization layer. They are mounted like `app/decisions.py` and `app/members.py`. No new services.
**Why:** Vercel runs one API container and one runner container, and separate services would multiply cold starts and secrets. The specification asks for one shared domain model.
**Rule:** a domain module may import `app.db`, `app.main` helpers and the authorization layer, never another domain's private functions. Cross-domain effects go through explicit domain-service functions or outbox events (AD-7).

## AD-3: Organizations get a type, people get memberships
**Problem:** the specification needs brand, publisher and agency organizations, people in several of them, and platform administrators. Today a user has one `org_id`.
**Decision:**
- `organizations.kind` is one of `direct`, `brand`, `publisher` or `agency`. Every existing workspace becomes `direct`, and the current flow (own products in own videos, one approval) keeps working unchanged.
- A `memberships` table (`user_id`, `org_id`, `member_role`) is the source of truth for access. `users.org_id` stays as the default organization, so existing code and sessions keep working during migration.
- A session carries the active organization, and every request checks membership in it on the backend.
- The existing five ranks stay. The specification's roles are derived from them: `*_OWNER` means owner or admin rank, `*_MEMBER` means the rest, by organization kind. `AGENCY_USER` is any member of an agency organization.
- `PLATFORM_ADMIN` is a flag on the user, set only by an operator command, never through the API.

**Cost:** two places hold an organization id until every query reads memberships. The migration keeps them consistent and tests check that they agree.

## AD-4: Analysis is versioned and never overwritten
**Decision:** an `analysis_versions` row records the source video version, pipeline and model versions, configuration, status, timings and error. Scenes, frames, detections and opportunities belong to one analysis version. Re-analysis adds a version. The latest completed version is the default view, and campaigns reference the version they were approved on.
**Why:** specification §5. Approved work must not change when a video is reprocessed.

## AD-5: Detection only from a model that is really there
**Decision:** Intelligence stages that need a model (object detection, environment classification, semantic search) run only when a model is installed and evaluated. Otherwise the stage records `unavailable` with the reason, and the score reweights over the factors that were measured. The current contour, tracking and quality pipeline stays the source of surface opportunities.
**Candidates to evaluate in Phase 4:** CPU models that OpenCV's DNN module can load, with licences that permit commercial use, for example SSD-MobileNet (Apache 2.0). YOLO models under AGPL are excluded unless their licence is resolved.
**Constraint:** on the free Vercel plan a function has 1 vCPU, 2 GB and 300 s. Any model must fit inside the measured analysis budget (see `docs/vercel.md`).

## AD-6: Money as integer minor units with a currency, in an append-only ledger
**Decision:** every amount is an integer of the currency's minor unit (for example cents) with an ISO 4217 code. Ledger entries are never updated or deleted, and corrections are compensating entries. Every write that moves money carries an idempotency key with a unique constraint. Budgets, quotes, authorised payments, captured payments, platform revenue, publisher payable and payouts are separate entry types.
**Why:** specification §7.10. The earlier marketplace used floating-point dollars, which this replaces.

## AD-7: A transactional outbox for work that follows a state change
**Decision:** a domain change and its `outbox_events` row are written in one transaction. The runner delivers pending events in order with retries, and each consumer records the event ids it has processed, so handling an event twice is harmless.
**Why:** specification §11. The existing `events` table remains the human-readable audit log.

## AD-8: Reservations are enforced by the database
**Decision:** an inventory row moves from `AVAILABLE` to `RESERVED` with a conditional update (`... WHERE id=? AND status='AVAILABLE'`), checking the affected row count. Exclusive reservations also have a unique index. This works the same on SQLite and PostgreSQL. The losing request gets HTTP 409 `INVENTORY_CONFLICT`.
**Why:** specification §8 requires exactly one winner between concurrent brands.

## AD-9: Rendering is reused, behind its existing adapter
**Decision:** campaigns create placements through the existing placement, quality and render code (`app/renderers.py`, `app/compositor.py`, `app/render3d.py`, `app/validation.py`). Marketplace placements need two approvals, from the advertiser and the publisher. The existing `placement_approvals.role` holds which party approved.
**Why:** specification §7.8. The engine is tested, and changing its output would alter existing behaviour.

## AD-10: Payments start as manual invoicing
**Decision:** with no payment-provider credentials, payments use an administrator-recorded invoice workflow, labelled as such in the interface. Nothing is marked paid without an administrator's recorded evidence. A provider adapter (for example Stripe in test mode) is designed but marked `DESIGNED_ONLY` until credentials exist.
**Constraint:** Vercel's free plan forbids commercial use, so real money also needs Vercel Pro (see [BLOCKERS.md](../execution/BLOCKERS.md)).

## AD-11: Search starts structured
**Decision:** scene and inventory search use structured filters and keyword matching that work on both databases. Embedding retrieval is designed behind an interface and marked `DESIGNED_ONLY` until an embedding model is available and evaluated. Mandatory eligibility (rights, brand safety, availability) is applied as a filter before any ranking, never as a score.
