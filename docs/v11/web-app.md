# Web app (Next.js) and sign-in

`web/` is the whole user interface:
- Next.js 16.4 (App Router) with React 19 and Tailwind CSS 4;
- no UI library, and no other runtime dependencies.

It replaced the V6–V10 single-file HTML frontend (`app/static/`) and its Vercel build script.

## Routes
| Route | Access | What it is |
|---|---|---|
| `/` | public | Landing page: interactive decision demo, before/after slider, agency section with a savings calculator, pricing, FAQ (`components/marketing/`) |
| `/pricing` | public | Plans, comparison table, competitive landscape |
| `/login` | public; signed-in people are sent to `/dashboard` | "Continue with Google", or email → magic link + 6-digit code |
| `/verify#token=…` | public | Magic-link landing: "Continue as you@…", one explicit click |
| `/welcome` | needs a pending sign-up | First sign-in: your name and a new workspace, or join an invitation |
| `/dashboard/**` | **signed in only** | Overview, New decision, Decisions, Library, Placements, Renders, Settings |

## How the browser reaches the API
- **Same origin.** `next.config.ts` rewrites `/api/*` and `/auth/google/*` to the FastAPI backend (`CC_API_ORIGIN`). The browser only ever talks to the app's own origin, so:
  - the session cookie (`cc_session`: HttpOnly, SameSite=Lax, Secure in production) is first-party;
  - no CORS is involved;
  - the double-submit CSRF token (`cc_csrf` cookie → `X-CSRF-Token` header) works as before.
- **Uploads bypass the app.** Large uploads go from the browser straight to the bucket by presigned PUT, and only the small completion call goes through the app.
- **Build-time destination.** Rewrites are fixed when the app is built, so `CC_API_ORIGIN` must be set at build time (and at run time, for the server-side session check).

## Keeping signed-out people out of the dashboard
The dashboard is protected in three layers:
1. **Proxy (`web/proxy.ts`).** Next 16 calls middleware "proxy". Every `/dashboard/**` request without a session cookie is redirected to `/login?next=<path>` before any page code runs.
2. **Server-side check (`app/dashboard/layout.tsx`).**
   - On every dashboard request, the server asks the API who the cookie belongs to (`GET /api/me`).
   - A missing, expired, forged or suspended session redirects to `/login`, so the dashboard HTML is never rendered for it.
   - The `next` path brings people back where they were after signing in.
3. **The API.** Every data request is authenticated and authorised by workspace role on the server. If a session expires while a page is open, the next request answers 401, and the app sends the person to `/login`.

Browser QA checks layers 1 and 2: unauthenticated visits to three dashboard pages, and a forged cookie, all end at `/login`.

## Sign-in flows
- **Google.** The flow is OpenID Connect with PKCE, state and nonce, and the ID token's RS256 signature is checked against Google's keys.
  - The button links to `/auth/google/start`, which the app proxies to the API.
  - Google redirects back to `<app origin>/auth/google/callback`, also proxied to the API.
  - New people land on `/welcome`, returning people on `/dashboard`.
  - Failures land on `/login?error=…` with a plain message.
  - The button is hidden when no Google client is configured.
- **Magic link and code.** Requesting either sends one email containing:
  - a link: `/verify#token=…`. The token is in the URL fragment, so it never reaches a server or a log;
  - a 6-digit code.

  Rules for both:
  - **Mail scanners:** opening the link alone does nothing, because scanners prefetch links. The page shows "Continue as you@…" and signs in on click.
  - **Expiry:** links and codes work once and expire after 10 minutes.
  - **Wrong codes:** five wrong codes burn the challenge.
  - **Rate limits:** 5 emails per address and 20 per IP per hour (`CC_SIGNIN_EMAILS_PER_IP_HOUR`).
  - **No account enumeration:** the response is identical whether or not an account exists.
- **No passwords.** The V6–V10 password register and login endpoints are gone.

## Design
- **Look:** a warm off-white canvas, near-black ink and a violet accent.
- **Type:** Inter for text and Instrument Serif for headings, self-hosted by `next/font`.
- **Responsive:** every page works at 390 px without horizontal scrolling (checked in browser QA).
- **Icons:** inline SVGs, with no icon library.

## Commands
```bash
cd web
npm ci
CC_API_ORIGIN=http://127.0.0.1:8000 npm run dev      # http://localhost:3000
npm run typecheck && npm run build
```
The API must run with `CC_PUBLIC_URL=http://localhost:3000`, because sign-in links and the Google callback use the app origin.

## Brand: colour and type

The tokens live in `web/app/globals.css` (`@theme`).

| Role | Choice | Why, for this product |
|---|---|---|
| Base | Near-black `#0a0a12` (marketing), warm off-white `#faf9f6` (app) | Black reads as cinema and premium craft, the world the footage comes from. A warm off-white keeps the working app calm for long sessions. |
| Primary | Violet `#7c6cff` (app accent `#5b4fe9`) | Violet signals imagination and premium creative work, and it stands apart from the blue most ad-tech and SaaS use. |
| Secondary | Teal `#2dd4bf` | Teal signals precision and trust, the "every frame checked" half of the promise. Violet into teal is creativity you can rely on. |
| Status | Green, amber, red only for pass / attention / fail | Colour means something in the app, so it is not spent on decoration. |
| Display type | Instrument Serif, with real italics | Editorial, like film titles. It gives headlines a crafted feel that the many all-sans AI tools lack. |
| UI and body type | Inter | Neutral and highly legible at small sizes, so data-heavy screens stay readable. |
| Data type | JetBrains Mono | Timestamps, scores and file names in mono read as measured and exact. |

The marketing header is a black notch hanging from the top edge (`components/site.tsx`), the same on dark and light pages.
