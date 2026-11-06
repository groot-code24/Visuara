# Visuara Marketplace

The Marketplace connects publishers, who sell scene inventory, with brands and agencies, who buy placements. Everything here is implemented and covered by `tests/test_marketplace.py`, `tests/test_security_marketplace.py` and `tests/test_demo.py`.

## Parties
- **Publisher** organizations list inventory, approve or reject requests, and produce placements.
- **Brand** and **agency** organizations browse, reserve, run campaigns, request placements and accept deliveries.
- **Platform administrators** verify rights, record payments and record payouts.

Each organization has a profile (`PUT /api/profile`). A brand profile can list excluded terms, and matching leaves out scenes that mention them.

## Publisher workflow
1. Upload and analyse videos (see [VISUARA_INTELLIGENCE.md](VISUARA_INTELLIGENCE.md)). Valid opportunities become draft items.
2. Review brand safety per opportunity.
3. Submit a rights record per video, with evidence (PDF, PNG or JPEG), holder, channels, territories and dates. A platform administrator verifies or rejects it.
4. List an item with a price in EUR, USD or GBP. Listing is refused, with every reason given, until:
   - the opportunity is valid and SAFE;
   - the analysis completed;
   - the licence is valid;
   - a rights record is verified and unexpired.
5. Approve or reject incoming placement requests. Approval confirms the reservation, marks the item sold and issues a quote.
6. Start production. The brand's product, at the asset version requested, is copied into the publisher's organization as a linked product, and a placement is created from the opportunity's geometry. The publisher then runs the existing quality check, approval and render.
7. When the render passes validation, a delivery is recorded automatically with its evidence.

## Brand workflow
1. Add products. A new image is a new asset version, and requests keep the version they were made with.
2. Create a campaign with a budget, then set its products and targeting:
   - product categories and placement categories;
   - publishers;
   - minimum score;
   - brand safe only;
   - maximum price.
3. See recommended scenes, ranked by category fit × feasibility, or run matching for one product (`POST /api/matching/recommendations`). Every run is stored.
4. Reserve an item. It is held for 15 minutes (`CC_RESERVATION_HOLD_SECONDS`), and only one brand can hold it.
5. Request the placement. A manager's request is the brand's approval, and the hold is then extended for the publisher's decision (72 hours by default).
6. Accept the quote, which issues the invoice.
7. Download the delivered render through a signed link, and accept the delivery.

## Money
Money is held as integer minor units, and invoicing is manual (blocker B-4: no payment provider).
- **Platform fee:** `CC_PLATFORM_FEE_BPS` basis points, 2000 = 20% by default. The fee is floored, and the publisher receives the rest.
- **Ledger:** every step is a balanced double-entry transaction. The ledger is append-only, enforced in the database.
- **Idempotency:** accepting a quote, recording a payment and recording a payout need `Idempotency-Key`.

## Reports
`GET /api/campaigns/{id}/report` is built from stored records only:
- requests by status;
- committed, invoiced and paid amounts;
- deliveries;
- delivered visible seconds;
- event counts filled by the outbox consumer.

Views and reach aren't measured, because no delivery platform reports them back.

## Not implemented
- Payment capture. It's blocked by B-4, and charging real customers is also blocked by B-5 (Vercel's free plan forbids commercial use).
- Negotiation of prices.
- Exclusivity windows beyond a single reservation per item.
