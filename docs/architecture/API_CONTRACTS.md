# API contracts

The backend is one FastAPI application. The web app calls it on its own origin under `/api`. This page records the conventions every endpoint follows and the complete inventory. Request and response models are the Pydantic classes in each module, and `/docs` on a running API shows them.

## Conventions
- **Authentication:** an HTTP-only `cc_session` cookie holding a random token. The database stores only its SHA-256. Sign-in is by Google OpenID Connect with PKCE, or by an email magic link or six-digit code.
- **CSRF:** every state-changing request from a signed-in browser sends the `X-CSRF-Token` header equal to the `cc_csrf` cookie. The cookie is set on the first `/api` response.
- **Authorization:** enforced on the server for every request, in `members.authorize()`, by the caller's role in the session's active organization (`memberships`). GET needs viewer and other methods need manager, unless a rule lowers or raises the requirement. See [SECURITY_MODEL.md](../security/SECURITY_MODEL.md). Marketplace endpoints also check the organization kind (403 `ORGANIZATION_KIND_FORBIDDEN`) and the caller's side of a shared record (403 `WRONG_PARTY`).
- **Tenancy:** every resource lookup filters by the caller's organization. Another organization's id answers 404, not 403, so ids reveal nothing.
- **Errors:** `{"detail": {"code": "UPPER_SNAKE", "message": "Sentence for people", ...context}}`. The HTTP status is chosen as follows:
  - 400: the request is malformed;
  - 401: no session;
  - 403: the role or rights forbid it (`ROLE_FORBIDDEN`);
  - 404: missing or not yours;
  - 409: the state forbids it (`INVALID_TRANSITION`, and `INVENTORY_CONFLICT` from Phase 8);
  - 413: too large;
  - 422: a field is invalid;
  - 429: rate limited;
  - 503: a dependency failed.

  Some older endpoints still return a plain string `detail`.
- **Request ids:** every response carries `X-Request-ID`, which also appears in the logs.
- **Rate limits and plan limits** are enforced on the server (`app/limits.py`).
- **Idempotency:** `POST /api/quotes/{id}/accept`, `/api/admin/invoices/{id}/mark-paid` and `/api/admin/allocations/{id}/payout` require `Idempotency-Key` (428 without it). A repeat with the same key returns the first response; a different request under the same key is refused (422 `IDEMPOTENCY_KEY_REUSED`).
- **Money:** integer minor units plus a three-letter currency (`price_minor`, `amount_minor`).

## Inventory
| Area | Endpoints |
|---|---|
| Health | `GET /api/health`, `/api/health/live`, `/api/health/ready` |
| Sign-in | `GET /api/auth/csrf`, `/api/auth/providers`; `POST /api/auth/email/start`, `/email/verify`, `/email/link`, `/email/link/check`; `GET /auth/google/start`, `/auth/google/callback`; `GET`, `POST /api/auth/signup`; `POST /api/auth/signup/cancel`; `POST /api/auth/logout`; `GET /api/me` |
| Members | `GET /api/members`; `PATCH`, `DELETE /api/members/{uid}`; `POST /api/members/{uid}/suspend`, `/reactivate`, `/transfer-ownership`; `GET`, `POST /api/invitations`; `DELETE /api/invitations/{id}`; `GET /api/usage` |
| Products | `GET`, `POST /api/products`; `POST /api/products/{pid}/model`; `GET /api/products/{pid}/image`; `GET /api/categories` |
| Videos | `GET`, `POST /api/videos`; `POST /api/videos/{vid}/analyze`; `GET /api/videos/{vid}/scenes`, `/frames/{ms}`, `/stream` |
| Direct uploads | `POST /api/uploads/video`; `POST /api/uploads/{sid}/complete`, `/cancel`; `GET /api/uploads` |
| Decisions | `GET`, `POST /api/decisions`; `GET /api/decisions/{did}`, `/preview`; `POST /api/decisions/{did}/cancel`, `/apply` |
| Placements | `GET`, `POST /api/placements`; `GET /api/placements/{pid}`, `/preview`; `POST /api/placements/{pid}/quality-check`, `/approve`, `/reject`, `/render`, `/revoke-rights`; `PATCH /api/placements/{pid}/revise` |
| Rendering | `GET /api/jobs`; `POST /api/jobs/{jid}/cancel`, `/download-link`; `GET /api/jobs/{jid}/download`; `GET /api/media/{token}`; `GET /api/renderers` |
| Reporting and operations | `GET /api/analytics`, `/api/events`, `/api/observability`, `/api/ops/metrics` |
| Internal | `POST /api/internal/run-jobs`: the HTTP job runner, authorized by a key derived from `CC_SIGNING_KEY` |

## Added in Phases 2 to 13
| Area | Endpoints |
|---|---|
| Organizations | `GET`, `POST /api/organizations`; `POST /api/organizations/switch`; `PATCH /api/organizations/current`; `GET /api/invitations/mine`; `POST /api/invitations/{id}/accept`; `GET /api/admin/organizations` |
| Video assets | `GET`, `POST /api/videos/{id}/versions`; `DELETE /api/videos/{id}`; `reject_duplicates` on `POST /api/videos` |
| Analysis | `POST`, `GET /api/videos/{id}/analyses`; `GET /api/analyses/{id}`, `/scenes`; `POST /api/analyses/{id}/cancel`; `GET /api/scenes/{id}`; `GET /api/scene-frames/{id}/image` |
| Opportunities and safety | `GET /api/opportunities`, `/api/opportunities/{id}`; `POST /api/opportunities/{id}/review`; `GET`, `POST /api/brand-safety/rules`; `DELETE /api/brand-safety/rules/{id}` |
| Inventory (publisher) | `GET /api/inventory`; `POST /api/inventory/{id}/list`, `/withdraw`; `GET /api/inventory/{id}/preview` |
| Marketplace (brand) | `GET /api/marketplace/inventory`, `/api/marketplace/inventory/{id}`, `/preview`; `POST /api/marketplace/inventory/{id}/reserve` (409 `INVENTORY_CONFLICT`); `GET /api/reservations`; `POST /api/reservations/{id}/release` |
| Profiles and rights | `GET`, `PUT /api/profile`; `GET /api/publishers/{org}/profile`; `POST`, `GET /api/videos/{id}/rights`; `POST /api/rights/{id}/revoke`; `GET /api/admin/rights`, `/api/admin/rights/{id}/evidence`; `POST /api/admin/rights/{id}/decision` |
| Catalogue and matching | `PATCH /api/products/{id}`; `GET`, `POST /api/products/{id}/assets`; `POST /api/matching/recommendations`; `GET /api/matching/recommendations/{run}` |
| Campaigns | `POST`, `GET /api/campaigns`; `GET`, `PATCH /api/campaigns/{id}`; `PUT /api/campaigns/{id}/products`, `/targeting`; `POST /api/campaigns/{id}/transition`; `GET /api/campaigns/{id}/recommendations`, `/report` |
| Placement requests | `POST /api/campaigns/{id}/placement-requests`; `GET /api/placement-requests`, `/api/placement-requests/{id}`; `POST /api/placement-requests/{id}/approve`, `/reject`, `/cancel`, `/start-production`, `/accept` |
| Commercial | `GET /api/quotes`; `POST /api/quotes/{id}/accept`; `GET /api/invoices`, `/api/invoices/{id}`, `/api/ledger`, `/api/earnings`; `GET /api/admin/invoices`, `/api/admin/allocations`, `/api/admin/ledger/trial-balance`; `POST /api/admin/invoices/{id}/mark-paid`, `/api/admin/allocations/{id}/payout` |
| Delivery | `GET /api/deliveries/{id}`; `POST /api/deliveries/{id}/download-link`; `GET /api/delivery-media/{token}` |
| Notifications | `GET /api/notifications`; `POST /api/notifications/{id}/read` |
| Programmatic | `GET /api/programmatic/capabilities`, `/api/programmatic/inventory-feed` |

## Added in V4.0 (M14 to M17)
| Area | Endpoints |
|---|---|
| Analysis | `GET /api/analyses/{id}/objects`: confirmed object tracks per scene, observed boxes only. Analyses record `model_versions` (detector, tracker, surface and camera algorithms) |
| Health | `GET /api/health` adds `object_detection: {model, installed}` |
| Opportunities | The list now returns `score_breakdown` decoded, with an `evidence` string per factor (scoring v2) |
| Matching | Results add `reasons` and `breakdown.context_fit`; responses add `left_out` (count per reason) and `version: match-v2` |
| Sale steps | Reserve, approve and start production answer 409 `NOT_SELLABLE` with `reasons` when rights, licence or review have lapsed since listing |
| Payments | `GET /api/payments/config`; `POST /api/invoices/{id}/checkout` (needs `Idempotency-Key`; 409 `NO_PAYMENT_PROVIDER`); `GET /api/invoices/{id}/payments`; `POST /api/payments/webhooks/{provider}` (signed; no session); `GET /api/payments/sandbox/{ref}`, `POST /api/payments/sandbox/{ref}/complete` (test mode only); `GET /api/admin/payments/events` |
| Agencies | `POST`, `GET /api/agency/grants`; `POST /api/agency/grants/{id}/revoke`; `GET /api/agency/clients`; `POST /api/agency/clients/{id}/leave`. `GET /api/organizations` lists client organizations with `status: DELEGATED` and `via_agency`; switching into one returns `via_agency` |
| Uploads | `POST /api/uploads/video` accepts `video_id` to upload a new version straight to storage |
| Programmatic | `GET /api/programmatic/capabilities` (states DESIGNED_ONLY, NOT_CONNECTED, CONNECTED, VERIFIED; `external` per integration); `GET /api/programmatic/contracts`; `POST /api/admin/programmatic/verify` |

