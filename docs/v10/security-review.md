# V10 security review (internal)

An internal code and configuration review with targeted tests. **No independent penetration test has been done**, and one is still required before a public launch.

## Results by area
| Area | Control | Evidence |
|---|---|---|
| Authentication | PBKDF2-SHA256 passwords (180k iterations); email codes HMAC-hashed, 10 min, single use, 5 attempts; magic link needs an explicit POST with CSRF; Google OIDC with PKCE S256, state and nonce in a sealed cookie, iss/aud/exp/email_verified checks, **RS256 signature verified against Google's JWKS** (new) | `tests/test_auth_db.py` (13), `tests/test_v10.py::test_google_id_token_signature_verification` (tampered payload, wrong key, `alg: none` and unknown `kid` refused) |
| Sessions | Random 256-bit tokens stored hashed; HttpOnly, SameSite=Lax, Secure in production; 7-day expiry; logout deletes the row; suspension and removal delete all of a user's sessions | `test_suspension_removal_and_ownership_transfer` |
| Authorisation | One choke point: `current_user()` → `members.authorize()` (suspension + role table); organization scoping in every query (`owned()`, grants); signed links re-check user status and role at use | Role matrix test, cross-org member actions → 404, 10-organization probes |
| CSRF | Double-submit header on cookie-authenticated writes; `Origin` must be this site or allowlisted; cross-origin token via `/api/auth/csrf` (readable only by allowlisted origins under CORS) | `test_csrf_rejects_missing_token`, `test_health_split_and_cross_origin_protection` |
| CORS | Exact origin list (`CC_CORS_ORIGINS`), credentials, no wildcard; production refuses non-https origins | Production guard test; split-origin browser QA |
| Headers | CSP (frame-ancestors none, object-src none, connect/img/media restricted), nosniff, X-Frame-Options DENY, Referrer-Policy, Permissions-Policy, HSTS under HTTPS, `Cache-Control: no-store` on API responses, Vercel rewrite caching off | Header assertions in tests and QA |
| Uploads | Streamed with size caps; content types limited; Pillow verify and pixel limits; GLB validation (25 MB, 100k triangles); FFprobe metadata and resolution, duration and fps limits; optional ClamAV (fail closed with `CC_CLAMAV_REQUIRED=1`); direct uploads checked for exact size, then scanned and probed like server uploads | Existing V8/V9 tests + direct-upload test |
| Path traversal | Stored names are sanitised (`safe_name`) and prefixed with random tokens; object keys derive from known media directories only | `test_object_keys_are_relative_to_media_directories` |
| Command injection | `subprocess` always with argument lists, never a shell | Code review |
| SQL injection | Parameterised queries throughout; the few table-name interpolations use internal constants | Code review |
| XSS | All user content in the UI passes through `esc()`, including the new team panel. CSP still allows inline scripts (both pages use inline JS): a known residual risk, see technical-debt.md | Code review |
| SSRF | Outbound calls only to configured endpoints (Google, JWKS, Ollama, Replicate with host allowlist, S3) | Code review |
| Tenant isolation | Queries scoped by organization; storage keys are random and only reachable through authorised presigned URLs | `test_org_isolation`, V10 role tests, 10-organization isolation probes (results in multi-tenant-readiness.md) |
| Signed URLs | HMAC download tokens bound to user, job, version and channel; 5-minute TTL; redacted from logs; presigned storage URLs 5 minutes (GET) or ≤ 1 hour (PUT); signature parameters redacted from logs | `test_log_redaction`, V9 delivery tests |
| Resource exhaustion | Per-IP auth rate limits; per-email sign-in limits; invitation, upload-init, analysis and quality-check rate limits per organization; storage, daily-upload and active-job quotas; job timeout; container CPU/memory limits | Quota tests |
| Secrets | `.env` and `deploy/.env` git-ignored; the app refuses production without a strong signing key; the frontend build has no secrets and warns if backend secrets are present; secret scan in CI | `qa/secret_scan.py`: 0 findings; `frontend/check.mjs` scans the build output |
| Errors | Unhandled exceptions return a generic 500 with a request id; health endpoints reveal no hosts, bucket names or credentials | Tests on readiness output |
| Worker permissions | Non-root container user; workers hold the same database and storage credentials as the API (no finer split) | Image check (`id -u` = 10001) |
| Stale approvals | Approvals, consent and analyses are bound to the placement version and content hash; every render and delivery re-checks the full chain | V8/V9 integrity tests |

## Dependency and secret scanning
| Scan | Result |
|---|---|
| `pip-audit` on the development environment (`.venv311`) | No known vulnerabilities |
| `pip-audit -r requirements.txt` | No known vulnerabilities |
| `pip-audit` inside the first V10 image | 12 advisories, **all in `pip` 25.0.1** from the base image. Fixed by upgrading pip during the build; the rebuilt image is re-audited in test-evidence.md |
| `python qa/secret_scan.py` (repository) | 0 findings |

## Residual risks (accepted for a supervised pilot, open for public launch)
1. **No independent penetration test.**
2. CSP allows `'unsafe-inline'` scripts.
3. A presigned PUT does not cap object size at the storage layer. The ≤ 1 h URL, completion size check and expiry cleanup limit the impact.
4. Rate limiting lives in the database, per host and IP. There is no WAF.
5. API and worker share credentials; a compromised worker could read all media.
6. There is no automated workspace-deletion endpoint; deletion is an operator procedure.
7. Live provider configurations (Google consent screen, SMTP SPF/DKIM/DMARC, R2 token scope) cannot be reviewed until they exist.

## SMTP sender authentication (for the provider you choose)
- Publish **SPF** (`v=spf1 include:<provider> ~all`), **DKIM** (the provider's CNAME/TXT records) and **DMARC** (`v=DMARC1; p=quarantine; rua=mailto:dmarc@yourdomain`) for the sending domain.
- Send from a subdomain you control (e.g. `signin@mail.example.com`).
- Verify with the provider's domain check and by sending a code to Gmail and Outlook test inboxes ("show original" → SPF/DKIM/DMARC = PASS).
