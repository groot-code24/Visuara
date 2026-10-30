# Security review (internal, v6)

Scope: code review of `app/` plus the negative tests listed below. This is **not** an independent penetration test or audit.

## Trust boundaries and assets
Assets: creator source videos, rendered assets, product images, consent records, session tokens. Boundaries: browser ↔ API (cookies, CSRF), API ↔ worker (shared DB and disk), API/worker ↔ FFmpeg (untrusted media), API ↔ optional providers (Replicate, Ollama, ClamAV).

## Controls verified by tests
| Control | Evidence |
|---|---|
| Tenant isolation (ID + organization on every lookup; 404 across organizations) | `test_org_isolation`; cross-org download and detail 404 in the integrity and E2E tests |
| Unauthenticated access refused | E2E: anonymous download → 401 |
| CSRF double-submit on mutations | `test_csrf_rejects_missing_token` |
| Session cookie HttpOnly, SameSite=Lax, Secure when configured | `test_secure_cookie_flag_and_utf8_page` |
| Only the rights holder can consent or approve | `NOT_RIGHTS_HOLDER` assertions |
| Approvals, analyses and jobs bound to version and content hash; superseded renders not deliverable | `test_tracked_workflow_…`, E2E step 16 |
| Tampered analysis track blocks rendering | `test_tampered_track_…` |
| Revocation cancels jobs and blocks delivery and links | `test_tampered_track_and_revocation_…`, original workflow test |
| Fail closed: unverified renderer, unvalidated output, missing gate → refusal | routing tests; worker `GENERATIVE_OUTPUT_UNVALIDATED` |

## Controls present but not independently tested here
PBKDF2-SHA256 (180k iterations) password hashing; hashed session and invitation tokens; auth rate limiting (12/min/IP, SQLite-shared, fails closed on DB error); upload size, format and dimension limits; optional ClamAV with `CC_CLAMAV_REQUIRED=1` fail-closed; SSRF guard on generative output URLs (HTTPS plus host allowlist); tracked links require HTTPS destinations.

## Findings and residual risks
| # | Risk | Severity | Status |
|---|---|---|---|
| S1 | Local disk storage is not encrypted at rest; downloads are served directly by the API rather than via short-lived signed URLs. | High | Open: needs object storage (roadmap). |
| S2 | No dependency vulnerability scan was run (`pip-audit` not installed). | Medium | Open. |
| S3 | Rate limiting is per IP on auth only; no limits on expensive endpoints (`quality-check`, `analyze`, uploads). A member could exhaust CPU. | Medium | Open: add per-user limits and move analysis to the queue. |
| S4 | FFmpeg and OpenCV parse untrusted media in-process with no sandbox, seccomp or container. | Medium | Open: run workers in a constrained container. |
| S5 | Audit events live in the same mutable database as application data. | Medium | Open: append-only or external audit sink. |
| S6 | `rights holder = uploader` may not match legal ownership. | Medium | Accepted for MVP; documented. |
| S7 | Session tokens are not rotated on privilege change, and there is no session listing or revocation UI. | Low | Open. |
| S8 | Logs: the app does not log credentials or tokens; the uvicorn access log records URLs (no signed URLs exist yet). | Info | Re-check when signed URLs are added. |

Secrets: none are committed. `.env.example` contains placeholders only.
