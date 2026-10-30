# V9 security review (internal)

Scope: code review of the V9 changes plus automated checks run in this environment. This is not an independent audit or penetration test. V8 controls are unchanged unless noted.

## Dependency scan
| Environment | Command | Result |
|---|---|---|
| Python 3.11.0 (`.venv311`, the supported runtime) | `python -m pip_audit` (installed environment, 79 packages) | **No known vulnerabilities**, after upgrading the venv's own setuptools 65.5.0 → 84.1+ |
| Python 3.10 (`.venv`, legacy) | same | Pillow 12.3.0 and filelock 4.1.0 advisories remain. 3.10 is no longer a supported runtime |

V9 fixes: Pillow 13.0.0 (13 advisories) and filelock 4.2.4 via the Python 3.11 runtime. New runtime dependencies: psycopg 3.3, psycopg-pool 3.3 and boto3 1.43, all with no advisories.

## New attack surface and controls
| Surface | Control | Evidence |
|---|---|---|
| Email sign-in | Non-enumerating responses; codes are HMAC-hashed with a 10-minute TTL, single use and burned after 5 failures; link tokens are SHA-256 hashed; links need an explicit POST with a form CSRF token (resists mail-scanner prefetch and login CSRF); rate limits per address, per IP and per minute | `tests/test_auth_db.py` (6 email tests) |
| Google OIDC | PKCE S256, state and nonce in an HMAC-sealed HttpOnly cookie scoped to `/auth/google`; ID token taken only from the token endpoint over TLS; `iss`/`aud`/`exp`/`nonce`/`sub`/`email_verified` validated; identities keyed by Google `sub` | `test_google_rejects_bad_state_and_bad_claims` and two linking tests |
| Account linking | Only when Google asserts `email_verified`; audited as `auth.identity_linked` | `test_google_links_existing_password_account_by_verified_email` |
| Onboarding | Nothing is created before onboarding completes; the pending state is server-side with a 30-minute TTL; invitations accepted only for the verified email; workspace names unique case-insensitively | `test_google_signup_links_identity_and_joins_invitation`, `test_signup_workspace_name_conflict_and_cancel` |
| PostgreSQL | All SQL parameterised (no string interpolation of values); TLS required by the Neon URL; no server-side prepared statements; per-test isolation proves no cross-schema leakage in tests | Full suite on PostgreSQL 16 |
| Object storage | Private bucket; access checks run **before** a presigned URL is issued (5-minute TTL); SSE-AES256 by default; keys derived from fixed media directories only (no user-controlled key paths) | `tests/test_storage.py` (cross-org 404 before redirect) |
| `.env` loading | Never overrides real environment variables; disabled in tests so a developer's Neon or SMTP credentials cannot be touched by the suite | `tests/conftest.py` |
| Public health endpoint | Reports the engine and storage type only. Database host and bucket name were removed after review | Code review |
| Logical backups | SHA-256 manifest; refuses a tampered dump or a populated target; verifies every table after import | `test_tampered_dump_is_refused`, round-trip tests |
| Container | Runs as a non-root user; healthcheck; `--proxy-headers` | Dockerfile (image **not built**: Docker unavailable) |

## Residual risks
- **S1** (unchanged): no per-user rate limits on CPU-heavy analysis endpoints. The 3D scene check adds about 2 s per clip.
- **S2:** sign-in emails are only as trustworthy as the SMTP provider and domain configuration (SPF, DKIM, DMARC). These are not configured or verified here.
- **S3:** sessions are not invalidated when a Google identity is linked to an existing account; the user is notified only through the audit log. Consider an email notice on first link.
- **S4:** a stolen magic link is usable by anyone within its 10-minute TTL, as with any magic link. It is not bound to the requesting browser, by design, so that sign-in works across devices.
- **S5:** presigned URLs are bearer URLs for their TTL.
- **S6** (unchanged): media parsers run in-process without a sandbox; audit events live in the same database.
- **S7:** none of the live integrations (Neon, Google, SMTP, bucket) has been exercised with real credentials.
