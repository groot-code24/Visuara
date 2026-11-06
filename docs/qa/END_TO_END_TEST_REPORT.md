# End-to-end test report

**Date:** 5 November 2026, on the final code, with FFmpeg 9.0.2. **Where:** this development machine, against the real API (FastAPI and SQLite, inline worker) and the production build of the web app (`next build`, then `next start`) in headless Chrome. **Not tested end to end:** the live Vercel deployment, which does not start (B-2); the Vercel container rehearsal, because Docker does not respond on this machine (B-9).

## 1. Marketplace walk: `qa/browser_qa_marketplace.py`, 12 of 12 passed
Built on the demonstration environment (`ops/demo.py`). The seed creates, through the real API:
- A publisher with two uploaded, analysed, rights-verified and listed videos.
- A brand with a product, a submitted campaign, a reservation and a placement request.
- A platform administrator.

Each person signs in by the code emailed to the outbox folder, the real sign-in path. No session is forged.

| # | Who | What the browser did | Checked | Result |
|---|---|---|---|---|
| 1 | — | Loaded `/`, `/login`, `/pricing`, `/dashboard` | Each response's Content-Security-Policy carries a nonce with `'strict-dynamic'` and no `'unsafe-inline'` for scripts; every script on the page carries that nonce; four requests, four different nonces | Pass |
| 2 | New person | Signed up, chose "A publisher or studio" | The workspace is a publisher; publisher navigation (Analyses, Inventory) shown | Pass |
| 3 | Publisher | Analyses list, opened an analysis, inventory | Both videos listed with an Open link; the stage list, scenes and opportunities shown; the reserved item reads `RESERVED` | Pass |
| 4 | Publisher | Approved the placement request | Status `APPROVED`; "Start production" offered | Pass |
| 5 | Brand | Marketplace, campaigns, the campaign page | Campaign listed; recommendations and report sections shown | Pass |
| 6 | Brand | Accepted the quote | Invoice `VIS-…` shown in Finance | Pass |
| 7 | Admin | Recorded payment | "Mark paid" stays disabled without a bank reference; after one is typed it records; the trial balance reports `balanced` | Pass |
| 8 | Publisher | Started production; quality check, approval, render | The placement page validated frames, rendered, offered "Download video" with "Output validated frame by frame"; the request reached `DELIVERED`; earnings shown | Pass |
| 9 | Brand | Accepted the delivery; organizations page | Status `ACCEPTED`; the organizations page loads | Pass |
| 10 | Publisher | Library: deleted the sold video, then the other one | The sold video is refused with "cannot be deleted"; the other is removed after confirmation and leaves the list | Pass |
| 11 | Brand | The same pages at 390 px | No sideways scrolling on requests, campaigns, finance | Pass |
| 12 | — | Console | No page errors in the whole walk | Pass |

Log: [evidence/2026-11-05_browser_qa_marketplace.json](evidence/2026-11-05_browser_qa_marketplace.json). Screenshots of every step: [evidence/screenshots-marketplace/](evidence/screenshots-marketplace/). Production with a real render took 147 s on this machine; every other step under 11 s. The walk found two problems on its first runs, both fixed:
- **A misleading label.** The account box read "Demo Studio · publisher · Agency", where "Agency" was the name of the plan. It now reads "… · Agency plan".
- **Wrong expectations in the walk itself.** It waited too little for each row's request, assumed the final request status was called `COMPLETED` (it is `ACCEPTED`), and tried to record payment without a bank reference. Each was corrected to what the product really does.

## 2. Original product walk: `qa/browser_qa_web.py`, 12 of 12 passed
Covers the product flows from before Phase 1:
- Landing and pricing pages.
- Refusal of unauthenticated and forged sessions.
- Sign-up by emailed code with onboarding, then sign-in again by single-use magic link.
- Products and videos added through the UI.
- The decision engine.
- Placement quality check, approval, render and download.
- The plan seat limit.
- Mobile layout, and the hero at 320–1440 px.
- The Google flow against a local stand-in.
- No page errors.

Log: [evidence/2026-11-05_browser_qa_web.json](evidence/2026-11-05_browser_qa_web.json). It matches the baseline (12/12), so the Phase 1–15 changes did not break the existing product.

## 3. Backend end to end: `tests/test_marketplace.py::test_the_whole_marketplace_flow`
Runs through the API from analysis to payout:
- Analysis, review, rights verification and listing.
- Reservation, the placement request, approval and the quote.
- A real render with validation.
- Delivery evidence and the signed brand download.
- Acceptance, payment and payout.
- A balanced trial balance, and campaign report counts.

It passed on SQLite and PostgreSQL in the final run ([evidence](evidence/)).

## What was run on which code
Everything in this report ran on the final code on 5 November 2026: both walks, and the full backend suites (182 tests; the one failure in the release run was an outdated test expectation, fixed and re-run, see [REGRESSION_LOG.md](../execution/REGRESSION_LOG.md)). An earlier full run was invalidated when FFmpeg was removed from the machine mid-run ([REGRESSION_REPORT.md](REGRESSION_REPORT.md)).

## How to run
```
cd web && CC_API_ORIGIN=http://127.0.0.1:18000 npx next build
python qa/browser_qa_marketplace.py --api-port 18000 --web-port 13000 --out data/qa-marketplace
python qa/browser_qa_web.py        --api-port 18000 --web-port 13000 --out data/qa
```
Use free ports. On this machine, Docker already holds 3000 and 8000. FFmpeg, FFprobe and Chrome must be installed.
