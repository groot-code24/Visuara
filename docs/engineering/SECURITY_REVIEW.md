# Security review (M13)

**Date:** 5 November 2026. **Scope:** the working tree on `3bf569f`, which includes the uncommitted Phase 1–15 work. **Method:** I read every authentication, authorization, signing, upload, storage, email and logging path, and grepped for the usual vulnerability classes. Each finding was reproduced before it was fixed. Each fix has a regression test that fails on the old code, either confirmed by running it or by its own reproduction.

Severity uses the specification's scale: **P0** critical, **P1** production-blocking or high, **P2** broken or incomplete, **P3** robustness or maintainability.

## 1. Findings

| ID | Sev. | Finding | Reproduced by | Fix | Test | Status |
|---|---|---|---|---|---|---|
| D-SEC-00 | P0 | The Neon role password is in git history: commit `c37197a`, `.env.vercel.example` line 6, pushed to GitHub | `python qa/secret_scan.py --history` (2 findings, both this one) | Repository side: the working tree is clean, the scanner checks history, `qa/check_env.py` refuses any value found in history. **The fix itself is the owner's:** reset the password ([DEPLOYMENT_RUNBOOK.md §1](DEPLOYMENT_RUNBOOK.md)) | `test_secret_scanner_detects_credentials_and_allows_placeholders` | **BLOCKED** on the owner |
| D-SEC-01 | P1 | **Open redirect after sign-in.** `safeNext` let `/\t/evil.example` (also `\n`, `\r`) through. Browsers drop those characters, so the user landed on `//evil.example` | `node repro_next.mjs`: 3 inputs landed on `evil.example` | Resolve the path with the WHATWG URL parser, require the same origin, refuse a result starting `//`, return the resolved path rather than the input (`web/lib/format.ts`) | `web/tests/format.test.mjs` (11 attack inputs) | TESTED |
| D-SEC-03 | P1 | **Logs could contain secrets.** Structured fields weren't redacted in the JSON format production uses. A connection-string password with characters such as `!` or `%` leaked its first part. Bearer tokens and the configured secrets themselves weren't redacted | `test_logs_never_contain_credentials`, run on the old code: failed on the password, bearer token, signing key and client secret | `obs.redact` removes configured secrets by value, the password in any `scheme://user:pass@`, and bearer tokens. JSON fields are redacted recursively | `tests/test_platform.py::test_logs_never_contain_credentials` | TESTED |
| D-SEC-04 | P1 | **SMTP could send credentials in clear.** Any `CC_SMTP_TLS` other than `starttls` or `ssl`, including a typo such as `tls`, fell through to plain SMTP, and production accepted `none` | `email_problem({... 'CC_SMTP_TLS': 'tls'})` returned `''` (no problem) | `mailer.send` refuses an unknown mode before connecting. `email_problem` reports it. Production refuses `none`. `qa/check_env.py` reports it | `test_smtp_never_sends_credentials_unencrypted` | TESTED |
| D-SEC-05 | P2 | **Database TLS was not enforced.** Without `sslmode`, libpq uses `prefer`, which falls back to no encryption | Read: no `sslmode` check anywhere | Production requires `sslmode=require`, `verify-ca` or `verify-full` for a remote host (`config.database_tls_problem`). `qa/check_env.py` reports it. Neon's own strings already carry `require` | `test_production_requires_an_encrypted_database_connection` | TESTED |
| D-SEC-02 | P3 | Render-download and delivery links share one key. A delivery token sent to `/api/media/` raised `KeyError` (a 500); it granted nothing | Read: `data['j']` on a token without `j` | Each route accepts only its own exact token shape and answers 403 otherwise | `test_the_whole_marketplace_flow` (replays the delivery token at `/api/media/`) | TESTED |
| D-SEC-06 | P3 | A line break in a name (inviter or workspace) made the invitation email raise `ValueError`, so a 500 after the invitation was saved. The library itself blocks header injection | `mailer.send(..., 'Ann\nBcc: …')` raised `ValueError` | Subjects are whitespace-normalised in `mailer.send` | `test_a_line_break_in_a_name_cannot_break_an_email` | TESTED |
| D-SEC-07 | P2 | The API and runner containers run as root, to bind port 80 on Vercel | Read: `Dockerfile.vercel` has no `USER` | **Open.** A non-root user needs either `PORT` support confirmed on Vercel's container runtime (beta) or file capabilities, which Vercel's runtime may not honour. Changing it unverified risks the API not starting. Verify on a preview deployment first | — | IN_PROGRESS (needs a live check) |
| D-SEC-08 | P3 | Development-only advisory: `minimatch` < 11.0.1 (GHSA-g3hv-75wg-m249, moderate, a denial of service from crafted glob patterns), via `typescript-eslint`, added with ESLint | `npm audit` | **Accepted for now.** It isn't shipped, and the patterns come from our own config. The only fixed release is 9 days old and a major version above what the parser declares. Revisit when `typescript-eslint` updates, or on 19 November 2026 | CI gate `npm audit --audit-level=high` passes | Accepted risk |

## 2. Cross-tenant authorization: every route

`tests/test_security_marketplace.py::test_every_id_route_refuses_an_organization_that_owns_nothing` lists every endpoint from the application's own OpenAPI schema: 151 endpoints, 83 of which take an identifier. It calls each of the 83, as a publisher that owns nothing, with every identifier the other organizations use. Every write gets a minimal body that passes request validation, so the handler's own ownership check is what answers. The test fails when:
- any call returns 2xx;
- any route answers only 422, which would mean the sweep never reached its ownership check (this caught 19 routes on its first version, and the bodies were fixed until it reached all 83).

**Result:** no organization reaches another's records. The one 2xx is `GET /api/publishers/{org_id}/profile`, public by design (what a brand sees about a publisher). Its fields are pinned to public marketing information, so a private column added later fails the test.

A route added later is swept automatically. The hand-written tests in the same file still cover the cases a sweep can't see: wrong party on a shared record, roles, organization kinds, adversarial input, concurrent reservations.

**The earlier "tenant routing" finding.** The specification mentions a previous finding that one tenant could influence another tenant's routing. Neither the repository's documents nor its code record such a finding. What I found related to routing:
- the worker address (`CC_WORKER_URL`) comes only from the environment;
- render routing (`renderers`) is chosen per placement from the analysis;
- the Google start route redirects on `X-Forwarded-Host` only outside production.

None of these takes input from another tenant. The cross-tenant media finding is the one fixed in Phase 14 (signed media checked the role in the wrong organization); the sweep above and `tests/test_assets_3d.py::test_signed_download_links` cover it.

## 3. Reviewed and found sound

| Area | What holds | Where |
|---|---|---|
| Email sign-in | Codes HMAC'd with a per-purpose key; single use under a conditional update; burned after 5 wrong attempts; 10-minute expiry; the magic link travels in the URL fragment and needs an explicit click (mail scanners prefetch links); the same response whether or not an account exists | `app/auth.py` |
| Google sign-in | Authorization code with PKCE (S256); `state` and `nonce` in a sealed, 10-minute cookie scoped to `/auth/google`; ID token signature verified against Google's keys (RS256, ≥2048-bit); issuer, audience, expiry, nonce and `email_verified` checked | `app/auth.py` |
| Sessions | 32-byte random tokens, stored as SHA-256; `HttpOnly`, `SameSite=Lax`, `Secure` in production; 7 days; deleted at sign-out; a new token at every sign-in (no fixation) | `app/auth.py`, `app/main.py` |
| CSRF and origin | Double-submit token on every write with a session; cross-origin writes refused unless allowlisted; CORS only for listed `https://` origins | `security_middleware` |
| Rate limits | Sign-in: 12 a minute per IP, 5 emails an hour per address, 20 an hour per IP; it fails closed when the limiter's store is down | `main.rate_limit`, `auth.email_start` |
| Platform administration | Granted only from the command line (`ops/platform_admin.py`); no API path escalates to it | `ops/platform_admin.py` |
| SQL injection | All values are bound parameters. Interpolated SQL uses only internal table names, placeholder lists, or column names taken from fixed Pydantic fields (extra fields are dropped) | `campaigns.py:116`, `catalogue.py:43` and the other lines `grep 'execute(f'` lists |
| SSRF | No request goes to a user-supplied address: Google, Blob, the runner and the vision model come from settings | `grep httpx/urllib` |
| Uploads and paths | Storage keys are generated on the server (`token_hex` plus a sanitised name); type sniffed from content; probed with FFprobe before acceptance; size checked against the declaration; optional ClamAV | `app/uploads.py`, `main.ingest_video`, `app/profiles.py` |
| XSS | No `dangerouslySetInnerHTML`; React escapes output; a nonce Content-Security-Policy with `'strict-dynamic'` on every page; the sign-in email escapes its link | `web/` |
| Signed links | HMAC, 5-minute expiry, bound to the user and placement version; role and membership re-checked at use; tokens redacted from logs | `main.signed_media`, `delivery.delivery_media` |
| Error disclosure | Unhandled errors return a request id, not a traceback; no API explorer in production; health endpoints show no hostnames | `security_middleware`, `main.health` |
| Production start-up | Refuses SQLite, the email outbox, a missing or short signing key, plain-HTTP cookies or address, an unencrypted database, plain-text SMTP, no way to sign in, and test sign-in servers | `config.production_problems`; `test_production_without_a_database_refuses_to_start` runs the real import |

**Smaller items, recorded and not fixed:**
- Some older routes return the first 120–200 characters of an exception (an invalid image, a failed quality check) as their message. These carry no secrets, but they're inconsistent with the `{code, message}` shape (debt 10).
- `CC_CLAMAV_REQUIRED` errors include the scanner's own message.

## 4. Verified in the deployment rehearsal
`qa/vercel_rehearsal.py` (15/15) runs the production image with production settings, against stand-ins that use TLS as Neon and an email provider do. It checks that:
- every database connection the application opens is encrypted (`pg_stat_ssl`);
- the email stand-in refuses plain-text SMTP;
- the job runner refuses outsiders;
- the dashboard needs a session;
- HSTS and the Content-Security-Policy are on every page;
- Google sign-in starts with PKCE and the right callback;
- the filesystem is read-only outside `/tmp`;
- the browser reports no CSP violation in the whole run.

## 5. Not verified, and why

| What | Why not | How to verify |
|---|---|---|
| The deployed site's headers, cookies and TLS | Nothing is deployed with settings (B-2) | `python qa/vercel_check.py https://www.visuara.si` |
| Live Google and SMTP | No credentials on this machine | `python qa/check_env.py --mail …`, then sign in |
| An external penetration test | Out of scope for this work | Before handling real money |

## 6. M14 additions (5 November 2026)
| Area | What holds | Where |
|---|---|---|
| **Inventory sale after rights lapse (D-MKT-01, P1)** | Rights, licence and review are re-checked at browse, reservation, publisher approval and production start, not only at listing; a lapsed item answers 409 `NOT_SELLABLE` | `inventory.require_sellable`; `test_inventory_whose_rights_lapse_cannot_be_bought` |
| Model weights (supply chain) | Not in git. Downloaded at image build from the upstream release and installed only if the SHA-256 pinned in `app/detection.py` matches; checked again before every load. A mismatch makes detection `UNAVAILABLE`; nothing else runs in its place | `ops/fetch_models.py`, `detection.load`; `test_detection_refuses_rather_than_guesses`, `test_the_fetcher_installs_only_a_file_with_the_pinned_checksum` |
| Model licences | Only Apache-2.0 models are listed; the licence ships with the weights in the image | `models/README.md`, `models/licenses/`; `test_only_commercially_licensed_models_are_listed` |
| Vision model calls (SSRF, resource use) | The server address comes only from `CC_OLLAMA_URL`; nothing from a request reaches it. The frame is re-encoded and capped at 768 px; the reply is read up to 1 MB and schema-checked; timeouts and retries are bounded | `app/vision.py`; `tests/test_understanding.py` |
| New endpoint `GET /api/analyses/{id}/objects` | Checks the analysis belongs to the caller's organization; included automatically in the cross-tenant sweep (now 84 identifier routes) | `intelligence.analysis_objects`; `test_every_id_route_refuses_an_organization_that_owns_nothing` |
| Score evidence in the brand view | The brand's inventory view copies an allowlist of fields; the publisher's score breakdown is not on it | `inventory.item_view` |

## 7. M15 to M17 additions (6 November 2026)
| Area | What holds | Where |
|---|---|---|
| Payment webhooks (no session, server to server) | Authenticated by an HMAC-SHA256 signature over the timestamp and the exact body (Stripe's scheme), compared in constant time, rejected when over 300 s old (replays) or for a provider that isn't configured. Bodies over 1 MB are refused. Raw payloads are not stored, only their SHA-256 | `payments.verify`, `payment_webhook`; `test_signatures_follow_the_stripe_scheme_and_refuse_tampering`, `test_webhooks_refuse_forgery_staleness_mismatch_and_strangers` |
| Money moves once | Each provider event id is applied at most once (unique index, a lock per event); a payment is applied only when amount, currency and invoice match the checkout; the invoice update is conditional, so a bank transfer and a provider payment can't both apply. The checkout endpoint needs an `Idempotency-Key` | `payments.apply_event`, `record_payment`; `test_a_test_mode_payment_settles_the_invoice_exactly_once` |
| Live money | A live Stripe key is refused unless the owner sets `CC_PAYMENT_LIVE=1`; the sandbox simulator is refused in production | `payments.config_problem`, `config.production_problems` |
| Who can pay | Only the invoice's own brand organization, with the manager role or higher; publishers and other brands get 403 or 404 | `payments.create_checkout`; the same tests |
| Agency delegation | Granted only by the client's owner or admin; capped at the lower of the grant's role and the person's agency role, never above manager; checked on every request, so a revocation is immediate; owner and admin actions (members, invitations, ownership, the organization's kind and name, agency access) stay with the client; switches record the agency | `agencies.delegated_orgs`, `main.current_user`; `tests/test_agencies.py` |
| Cross-tenant sweep | The new identifier routes (invoice checkout and payments, grant revocation, leaving a client, sandbox sessions, version uploads) are included automatically and refuse an organization that owns nothing | `test_every_id_route_refuses_an_organization_that_owns_nothing` |
| Programmatic test adapters | In-process only, refused in production, always reported `external: false`; verification is for platform administrators only | `programmatic.adapters`; `tests/test_programmatic.py` |
| Container image | Debian security updates applied at build; CI scans the image with Trivy and fails on fixed CRITICAL or HIGH findings (D-QA-06). Not yet run: CI runs on the owner's push | `Dockerfile.vercel`, `.github/workflows/ci.yml` |

