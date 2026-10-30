# API contract

All endpoints except `/`, `/static/*`, `/api/health`, `/api/auth/register`, `/api/auth/login` and `/r/{slug}` require a session cookie. Mutations need `X-CSRF-Token` matching the `cc_csrf` cookie.

Errors: `{"detail": "message"}` or, for workflow refusals, `{"detail": {"code": "REASON_CODE", "message": "…", …}}`. Codes used: `INVALID_TRANSITION`, `QUALITY_NOT_PASSED`, `FIT_NOT_ACCEPTED`, `FIT_CONFIDENCE_TOO_LOW`, `NOT_DRAFT`, `NOT_RIGHTS_HOLDER`, `CONSENT_REQUIRED`, `AWAITING_CREATOR_APPROVAL`, `RIGHTS_LOCKED`, `NOT_READY_FOR_RENDER`, `NO_CAPABLE_RENDERER`, `RIGHTS_NOT_ACTIVE`, `APPROVALS_NOT_BOUND`, `QUALITY_NOT_VERIFIED`, `PRODUCT_CHANGED`, `TRACK_INTEGRITY`, `VERSION_MISMATCH`, `SUPERSEDED_VERSION`, `NOT_DELIVERABLE`, `OUTPUT_NOT_VALIDATED`, `CHANNEL_NOT_PERMITTED`, `GENERATIVE_UNAVAILABLE`, `CHANGED_DURING_ANALYSIS`.

## Auth and organization
`POST /api/auth/register` (organization required unless `invitation_token` is given) · `POST /api/auth/login` · `POST /api/auth/logout` · `GET /api/me` · `POST|GET /api/invitations`

## Media
`POST|GET /api/products` · `GET /api/products/{id}/image` · `POST|GET /api/videos` · `GET /api/videos/{id}/stream` · `POST /api/videos/{id}/analyze` · `GET /api/videos/{id}/scenes` · `GET /api/videos/{id}/frames/{ms}` · `POST /api/videos/{id}/surface-proposals` · `POST /api/surface-proposals/{id}/review`

## Placements
- `POST /api/placements`: `{video_id, product_id, frame_second, x, y, width, start_second, end_second, tracking_quad?}`, giving DRAFT v1.
- `GET /api/placements` · `GET /api/placements/{id}` (versions, approvals, rights, current quality report, jobs, audit history)
- `POST /api/placements/{id}/quality-check`: see [quality-contract.md](quality-contract.md).
- `POST /api/placements/{id}/fit-review` (form `decision`, `confidence`, `notes`)
- `POST /api/placements/{id}/propose` · `POST /api/placements/{id}/approve` (brand approval returns `verification[]`) · `POST /api/placements/{id}/reject` (form `reason`) · `POST /api/placements/{id}/verify`
- `PATCH /api/placements/{id}/revise` (form fields, `tracking_quad` JSON or empty), giving a new DRAFT version.
- `GET|PUT /api/placements/{id}/rights` · `POST /api/placements/{id}/revoke-rights` (form `reason`)
- `POST /api/placements/{id}/render` · `POST /api/placements/{id}/route-render` (compatibility) · `POST /api/placements/{id}/generative-render` (503 unless verified)

## Jobs and delivery
`GET /api/jobs` (includes `validation`) · `GET /api/jobs/{id}/download[?channel=]` · `POST /api/jobs/{id}/cancel`

## Campaigns and operations
`POST /api/placements/{id}/tracked-links` `{destination_url, campaign?, channel?}` · `GET /r/{slug}` · `POST /api/conversions/import` · `GET /api/campaign-analytics` · `GET /api/analytics` · `GET /api/events` · `GET /api/observability` · `GET /api/renderers` · `GET /api/health`
