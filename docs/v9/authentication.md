# Sign-up and sign-in (V9)

Brands and creators join with **Google** or with **email** (a one-time code and a magic link in the same message). Password sign-in from V6 still works for existing accounts. Code: [app/auth.py](../../app/auth.py), [app/mailer.py](../../app/mailer.py); UI in [app/static/index.html](../../app/static/index.html); landing page in [app/static/landing.html](../../app/static/landing.html).

## Journeys
| Step | Email | Google |
|---|---|---|
| Start | Landing page **Join as a brand / creator** opens `/app?join=…`, and the role is pre-selected for onboarding | Same page, **Continue with Google** |
| Prove the email | Enter the 6-digit code, or open the link and press **Continue** | Google account chooser, then a consent redirect back to `/auth/google/callback` |
| First time | Onboarding: name, **brand or creator**, workspace name; or **join a workspace that invited this email** | Same (name pre-filled from Google) |
| Returning | Straight into the workspace | Straight into the workspace (matched by Google account id, so it survives an email change) |

## Security properties
| Risk | Control |
|---|---|
| Account enumeration | `POST /api/auth/email/start` returns the same response for known and unknown addresses; both receive a code (sign-up and sign-in are the same flow) |
| Code guessing | 6 digits, 10-minute expiry, single use, **5 wrong attempts burn the code** (counted in a committed transaction); IP limit of 12/min on the endpoints; per-email limit of 5 sends/hour; per-IP limit of 20 sends/hour |
| Stolen database | Codes are stored only as HMAC-SHA256 with the server key; link tokens only as SHA-256 |
| Mail scanners clicking links | `GET /auth/email/{token}` only shows a confirmation page; signing in needs a `POST` with a CSRF form field matching the `cc_csrf` cookie |
| Replay | Requesting a new code invalidates the previous one; links and codes are consumed atomically (`UPDATE … WHERE consumed_at IS NULL`) |
| OAuth CSRF and code injection | `state` + `nonce` + PKCE (S256) in an HMAC-sealed, 10-minute, HttpOnly cookie scoped to `/auth/google` |
| ID token forgery | The ID token is received directly from Google's token endpoint over verified TLS (OpenID Connect Core §3.1.3.7 step 6); `iss`, `aud`, `exp`, `nonce`, `sub` and `email_verified` are all checked. Rejection is tested for each |
| Account takeover by email | A Google identity links to an existing account only when Google asserts `email_verified`; the link is recorded as an audit event (`auth.identity_linked`) |
| Unfinished sign-up | The pending sign-up lives server-side for 30 minutes behind an HttpOnly cookie; nothing is created until onboarding completes; it can be cancelled |
| Passwordless accounts | Created with an empty password hash that can never verify, so password sign-in is impossible for them |

## Configuration
| Variable | Purpose |
|---|---|
| `CC_PUBLIC_URL` | Base URL in emails and the Google redirect URI (`<CC_PUBLIC_URL>/auth/google/callback`) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google Cloud console → APIs & Services → Credentials → OAuth client ID, type **Web application**. Add the redirect URI above. The button appears only when both are set |
| `CC_SMTP_HOST`, `CC_SMTP_PORT`, `CC_SMTP_TLS`, `CC_SMTP_USER`, `CC_SMTP_PASSWORD`, `CC_MAIL_FROM` | Any SMTP provider (Gmail app password, Resend, SendGrid, Amazon SES, Postmark). Email sign-in appears only when configured |
| `CC_MAIL_BACKEND=outbox` | Development and tests only: messages are written to `CC_DATA_DIR/outbox`, never delivered |
| `CC_SIGNIN_CODE_TTL` | Code and link lifetime (default 600 s) |
| `CC_SIGNING_KEY` | HMAC key for codes and OAuth state (also download links). **Required and identical on every host** in a multi-host deployment |
| `CC_SECURE_COOKIES=1` | Required behind HTTPS |

## API
| Method and path | Notes |
|---|---|
| `GET /api/auth/providers` | `{google, email, email_backend, password}`, used by the UI to show only working options |
| `POST /api/auth/email/start` | `{email}` → `{sent, email, expires_in_seconds}` |
| `POST /api/auth/email/verify` | `{email, code}` → `{status: signed_in | signup_required}` and a cookie |
| `GET/POST /auth/email/{token}` | Confirmation page and confirmation (303 to `/app` or `/app#welcome`) |
| `GET /auth/google/start`, `GET /auth/google/callback` | OIDC authorization-code flow with PKCE |
| `GET /api/auth/signup` | Pending sign-up: `{email, name, provider, invitations[]}` |
| `POST /api/auth/signup` | `{name, role, organization}` or `{name, invitation_id}` → `{user}` and a session |
| `POST /api/auth/signup/cancel` | Discards the pending sign-up |

## Evidence
- `tests/test_auth_db.py`: 13 tests (code sign-up, onboarding, return sign-in, link confirmation and CSRF, attempt burning, expiry, supersession, rate limit, non-enumeration, Google state/claims rejection, identity linking, invitation join, name conflict, cancel). They run on SQLite and PostgreSQL.
- `qa/browser_qa_v9.py`: 6 browser steps (landing page, join screens, email-code sign-up, invitation join, magic link, returning user). Screenshots are in `docs/qa/v9/`.
- **Not verified:** a live Google sign-in and live SMTP delivery. No Google OAuth client or SMTP credentials were available in this environment; the Google flow is tested with the token exchange replaced by a stub.
