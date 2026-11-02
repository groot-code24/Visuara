# Pricing and competitive landscape (V11)

Competitor research: October 2026, refreshed on 31 October 2026 from the vendors' own websites and pricing pages where they publish one. Everything else is from third-party reports, marked "reported". Re-check before quoting to customers or investors.

## Who else solves "put a product into a video"

| Category | Examples | How they charge | How we differ |
|---|---|---|---|
| Virtual product placement networks | **Rembrand** (it bought Mirriad's US business in 2025). **Mirriad**'s site says the company entered administration on 13 May 2026 and has ceased trading. **Amazon Ads** virtual placement (beta). | **No public prices.** The sites route to "Contact us" or "Book a demo". Campaigns are priced per thousand impressions (CPM), with a revenue share to the content owner (Rembrand reportedly pays creators about 75% of net). | Sales-led, network inventory, weeks of cycle time. We are self-serve: your products, your videos, today. |
| AI ad and UGC generators | **Creatify**, **Topview**, **Arcads** | **Creatify** (pricing page; annual prices are 30% lower):<br>• Standard $15/month ($10 annual)<br>• Pro $55 ($38)<br>• Max $95 ($66)<br>• Business $195 ($136) for 10 seats<br>• API from $99/month<br>• Free: 10 credits, watermarked<br>**Topview** (annual billing):<br>• Plus $36/month<br>• Business $44<br>• Ultra $50<br>• Team $56 per seat<br>**Arcads:** its pricing page returns 404, and no price list is published. | They generate new ads with AI actors and stock scenes. We put the real product into footage the customer already owns. |
| Generative video editors | Runway Aleph, Kling, Pika | **Runway Aleph API** reported at 28 credits per second at $0.01 per credit (≈ $0.28 per edited second, ≈ $2.80 for 10 s). **Pika** reported at $8–10/month to $76–95/month. | Prompt-driven regeneration, with no guarantee that the product stays exact or stable, and no per-frame check. We never redraw the product and measure every frame. |

YC companies in adjacent spaces:
- **AI ad production:** Claybird (F2025, studio-quality ads in under 72 hours) and Absurd (F2025, AI brand and performance ads).
- **AI video editing:** Craze (S2022, AI video platform for creatives) and Palmier (S2024, an open-source video editor that agents can operate).
- **Other:** Syncly (W2023, social listening for video).

None does self-serve product placement into existing footage.

**Positioning.** No self-serve tool decides *which* of several products fits *which* scene in a customer's own library and proves the result frame by frame:
- placement networks have the decision but not self-serve, and no public prices;
- generators and editors are self-serve but don't preserve the product or the footage.

### What we took from their websites
- **Creatify:** a live product demo in the hero (its "Ad Agent" chat mockup). We built an interactive decision demo instead of a static screenshot.
- **Placement networks:** a before/after visual (Mirriad's animated scene GIFs). We made it a draggable slider.
- **Creatify and Topview:** published, tiered prices with an annual discount and a team tier for agencies. The networks hide their prices; we publish ours.
- **Creatify's agency page:** pitched to agencies on throughput ("From client link to deliverable"). We pitch on margin and on footage clients already own.
- **What we did not copy:** customer logos, testimonials and traction numbers. We have none yet and will not invent them. The landing page uses only claims the product can back.

## Our plans

| | Free | Pro | Agency (`team`) | Enterprise |
|---|---|---|---|---|
| Price per month: monthly / annual billing | $0 | $39 / $29 | $159 / $119 | Custom |
| Decisions per month | 5 | 60 | 300 | Custom |
| Rendered videos per month | 3 | 40 | 200 | Custom |
| Videos per decision (products: up to 4) | 3 | 10 | 20 | 50 |
| Longest video (1080p, 30 fps) | 45 s | 3 min | 4 min | Platform limit |
| Largest video file | 200 MB | 500 MB | 1 GB | Platform limit |
| Video uploads per day | 10 | 50 | 200 | Platform limit |
| Renders queued or running at once | 2 | 4 | 10 | Platform limit |
| Storage | 2 GB | 50 GB | 250 GB | Custom |
| Seats (owner/admin/manager/editor/viewer roles) | 1 | 3 | 15 | Custom |
| Price per rendered video at full use: monthly / annual | — | $0.98 / $0.73 | $0.80 / $0.60 | Volume |

The API enforces these allowances (`app/limits.py` `PLANS`; the plan id `team` is shown as "Agency"):
- **Decisions, renders and video length:** over the allowance, the API answers `402` with a `PLAN_*` code, and the app asks the customer to upgrade.
- **Storage, seats, file size, daily uploads and active renders:** the plan value applies. The environment quotas are ceilings above every plan (`.env.example`).
- **Plan changes:** an operator action (`python -m ops.plans set --org … --plan pro`). Billing is by invoice during the pilot, and no card payments are taken in the app.

## Why these numbers (changed on 31 October 2026)

The previous prices were Pro $29 ($23 annual) and Team $99 ($79). The refreshed competitor data showed both were below the self-serve market for less valuable output.

**Pro: $39 a month, or $29 billed annually.**
- **Market:** Creatify Pro is $55 ($38 annual) and Topview's entry plan is $36 annual.
- **Anchor:** annual billing keeps the "$29" anchor.
- **Value:** every paid plan stays under $1 per rendered video. Compare about $11 per Arcads video (reported) and about $2.80 for 10 s of Runway Aleph.

**Agency (was Team): $159 a month, or $119 billed annually.**
- **Market:** this is where agencies buy. Creatify Business is $195 ($136 annual) for 10 seats, and Topview Team is $56 per seat (two seats are $112).
- **What agencies get:**
  - 15 seats with roles, so clients can be viewers;
  - manager approval before any render;
  - 20 videos per decision;
  - $0.60 per rendered video.
- **Name:** renamed to say who it is for. The landing page sells it to agencies.

**Annual discount: 25%.** Creatify gives 30% and Topview up to 67%. A single round number is easy to say.

**Free: unchanged** (5 decisions, 3 renders, 60 s videos). That is enough for one real decision on your own footage, which is the moment people understand the product. Free renders carry no watermark, unlike Creatify's free plan. Keep watching whether that converts.

**Labels.** No plan is labelled "Most popular", because there is no data behind such a label yet. The Agency card says "Best for agencies".

**Unit cost:**
- rendering is CPU-only on our own workers (FFmpeg + OpenCV; no GPU, no per-call model fees);
- a 4 vCPU worker rendered the synthetic test clips in 2–7 s each (V10 load test);
- real 1080p footage will be slower, and its cost per minute has not been measured yet (see known-limitations.md);
- free-tier abuse is bounded by the monthly allowances and the 60-second cap.

## What to validate with the first customers
1. **Willingness to pay.** Ask 10 pilot workspaces, at least half of them agencies, to choose a plan after the free month. Record the conversion rate and which limit they hit first.
2. **Value per render.** Measure how many decisions it takes to get one approved render on real footage. If it is high, price decisions more generously and charge for renders.
3. **Agency resale.** Ask agencies what they charge clients per placement. If it is far above $0.60, test a higher Agency price or a per-client add-on.
4. **Revenue share.** Brands placing products in creators' videos may prefer paying per impression. A CPM add-on, as the placement networks charge, can come later without changing the plans above.

## Sources
- [Rembrand](https://www.rembrand.com/) (homepage, read 31 October 2026)
- [Mirriad](https://www.mirriad.com/) (administration notice, read 31 October 2026)
- [Creatify pricing](https://creatify.ai/pricing) and [Creatify for agencies](https://creatify.ai/use-cases/marketing-agencies) (read 31 October 2026)
- [Topview pricing](https://www.topview.ai/pricing) (read 31 October 2026)
- [Arcads](https://www.arcads.ai) and [Arcads for agencies](https://www.arcads.ai/industries/marketing-agencies) (pricing page returned 404 on 31 October 2026)
- [Sacra: Rembrand](https://sacra.com/c/rembrand/)
- [Maginative: Rembrand raises $23M](https://www.maginative.com/article/rembrand-raises-23m-to-bring-virtual-product-placement-to-connected-tv/)
- [eesel.ai: Arcads pricing 2026](https://www.eesel.ai/blog/arcads-ai-pricing)
- [pixverse.ai: Runway Aleph alternatives](https://pixverse.ai/en/blog/runway-aleph-alternatives)
- [eesel.ai: Pika pricing 2026](https://eesel.ai/blog/pika-ai-pricing)
- YC directory: [Advertising](https://www.ycombinator.com/companies/industry/advertising), [Video](https://www.ycombinator.com/companies/industry/video); [Palmier launch](https://www.ycombinator.com/launches/QtT-palmier-pro-an-open-source-video-editor-your-agents-can-operate); [Absurd launch](https://www.ycombinator.com/launches/OcY-absurd-ai-ads-powered-by-agents-and-creatives)
