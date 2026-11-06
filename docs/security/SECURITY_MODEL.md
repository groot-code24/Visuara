# Security model

## Identity and sessions
- **Sign-in:** Google OpenID Connect with PKCE, or an email magic link or six-digit code. There are no passwords.
- **Sessions:**
  - A random token sits in an HTTP-only `cc_session` cookie, and the database stores only its SHA-256.
  - Each session records its active organization (`sessions.active_org_id`).
- **CSRF:** every state-changing request from a signed-in browser carries `X-CSRF-Token`, equal to the `cc_csrf` cookie.
- **Suspension and removal:**
  - Suspending someone ends all their sessions.
  - Sign-in is refused once no membership is active.
  - An account-level suspension (`users.suspended_at`) covers the person's default organization.

## Organizations and roles
- **Memberships:** a person can belong to several organizations, each with its own role (`memberships`). Every request acts in the session's active organization, with the role held there (`main.current_user`).
- **Roles, from least to most:** viewer, editor, manager, admin, owner.
  - The rules live in one place, `members._RULES`, and the first match wins.
  - Without a matching rule, a GET needs viewer and anything else needs manager.

| Action | Minimum role |
|---|---|
| Read anything in the organization | viewer |
| Accept an invitation; mark a notification read; create or switch organizations | viewer |
| Upload videos, products, rights evidence; queue or cancel analyses; create placements and quality checks; draft campaigns, set their products and targeting; run matching; start production | editor |
| Review brand safety; list or withdraw inventory; reserve; request, approve, reject or cancel placements; accept quotes and deliveries; download; campaign transitions; profiles | manager |
| Members and invitations | admin |
| Ownership transfer; change the organization's kind | owner |

**Organization kinds** (`orgs.require_kind`) decide the side of the marketplace:
- `publisher` organizations list inventory, approve requests and produce placements.
- `brand` and `agency` organizations browse, reserve, run campaigns and request placements.
- `direct` organizations do neither: they use Intelligence and place their own products.

A kind can't change once the organization has marketplace records.

**Platform administrators** (`users.platform_admin`):
- The flag is granted only from the command line (`python -m ops.platform_admin grant`). No API can grant it.
- Administrators verify rights evidence, record payments and payouts, and read the trial balance.
- They can't verify rights submitted by their own organization.

## Tenancy
- Every tenant-owned row has `org_id`, and every lookup filters by the caller's organization.
- Another organization's id answers 404, so ids reveal nothing.
- Cross-organization records (reservations, placement requests, quotes, invoices, deliveries) carry both parties' organization ids. Each endpoint checks which side the caller is on (`WRONG_PARTY`) before acting.
- What a brand sees of a publisher is limited to:
  - listed inventory and its preview frame;
  - the publisher's public profile;
  - the items it has reserved.

  The brand never sees the publisher's videos, analyses or linked product copies.
- Signed download links are bound to the user, the organization and the job or delivery. Every delivery check runs again when a link is used.

## Data integrity
- **Money:** integer minor units with a currency code. The ledger is double-entry, and `post_transaction` refuses an unbalanced transaction. The database refuses UPDATE and DELETE on `ledger_entries`: a trigger on SQLite and on PostgreSQL.
- **Idempotency:** accepting a quote, recording a payment and recording a payout require an `Idempotency-Key`. A key reused for a different request is refused.
- **Concurrency:** one reservation per inventory item, enforced by a conditional update and a unique partial index. One active analysis per video version, enforced by a unique partial index. Optimistic versions protect inventory and campaign edits.
- **Foreign keys:** every table added from Phase 1 declares them, and they're checked at commit.

## Uploads
- **Videos:** read by FFprobe, never trusted by name or browser type, and scanned by ClamAV when it's configured.
- **Product images:** verified by Pillow and limited to PNG, JPEG or WebP.
- **Rights evidence:** must start with a PDF, PNG or JPEG signature and is limited to 10 MB. It's stored under a generated name and served only to platform administrators.

## Browser
- **Content-Security-Policy with a nonce:** `web/proxy.ts` gives every page request a fresh 128-bit nonce. The policy goes on the request, where Next.js reads it and marks its own scripts, and on the response.
  - `script-src` is `'self' 'nonce-…' 'strict-dynamic'`, with no `'unsafe-inline'`, so an injected script does not run.
  - Styles allow inline (React style attributes), and `object-src`, `base-uri`, `form-action` and `frame-ancestors` are locked down.
  - Every page renders per request so that it can carry the nonce.
  - Checked in `qa/browser_qa_marketplace.py`: four pages, four different nonces, the page's scripts carry them, and neither walk reports a page error.
- **Other headers** (`web/next.config.ts`): HSTS, `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`.
- **Nothing secret reaches the browser:** the only `NEXT_PUBLIC_` settings are the site address, the contact email and the legal name and jurisdiction. Sessions are HTTP-only cookies.

## Secrets
- **Where they live:** only in `.env.vercel` (and `.env` locally), which git ignores, and in Vercel's environment variables. `.env.example` holds placeholders only.
- **Scanning:** `qa/secret_scan.py` looks for private keys, cloud and provider tokens, database URLs with passwords, Neon passwords and high-entropy assignments. It covers Python, TypeScript and configuration files, and reports the file, line and kind, never any part of a value.
  - `--history` scans every commit, which is what a push publishes.
  - CI runs it on every push. `.githooks/pre-commit` runs it before each commit once enabled with `git config core.hooksPath .githooks`.
- **Before deploying:** `qa/check_env.py` refuses any secret in `.env.vercel` that appears anywhere in the git history, naming the setting only. A value that was ever committed must be replaced at its service.
- **Production refuses unsafe settings at start-up** (`app/config.production_problems`): SQLite, local media, the email outbox, a short signing key, insecure cookies, a non-HTTPS public address, no way to sign in, test sign-in servers.

## Known gaps
See [TENANT_ISOLATION_REPORT.md](TENANT_ISOLATION_REPORT.md) and blockers B-1 (leaked database password) and B-8 (legal review) in [BLOCKERS.md](../execution/BLOCKERS.md).
- **B-1:** commit `c37197a`, already on GitHub, contains the Neon database URL with its password in `.env.vercel.example` (found by `secret_scan.py --history`). Removing it from history does not make it secret again: the password must be reset in Neon.
- Rate limits use the database (technical debt 12).
