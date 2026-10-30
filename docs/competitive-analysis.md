# Competitive analysis and product strategy

Research snapshot: 2026-10-29. This is a public-feature comparison, not a benchmark of private systems. Pricing, access and product status can change.

## Competitor landscape

| Product | Publicly described strength | Implication for Context Commerce |
|---|---|---|
| Runway Dev | Video-to-video editing, product-swap recipe, product-ad generation, async task APIs and finishing workflows. Product Swap is specifically designed to replace an existing product while preserving camera motion and scene composition. Sources: https://docs.dev.runwayml.com/api/ and https://dev.runwayml.com/learn/edit-and-finish-video-api | Do not try to win on raw generative video model quality alone. Integrate specialist providers behind an adapter and own the rights, approval, versioning, quality-control and campaign workflow. |
| AdWeft | Public beta pitch around creator-video analysis, contextual product placement that moves with a shot, affiliate matching, spoken-read timestamps and publish-ready links/disclosures. Source: https://www.adweft.com/ | This is the closest visible product-positioning overlap. Context Commerce needs stronger brand/creator collaboration, licensing, approvals, batch operations and measurable campaign reporting, not just insertion. |
| Mirriad | Historically focused on virtual product placement in premium TV, streaming, music and influencer content, with supply/demand partnerships. Its public site states administrators were appointed on 13 May 2026. Sources: https://www.mirriad.com/ and https://www.mirriad.com/business-model | The category is established, but current commercial status and partner availability should be independently checked before treating it as an active competitor or prospective partner. |
| Manual post-production / agencies | Human-led tracking, compositing, approvals and export; flexible but labor-intensive. | Compete on turnaround time, workflow automation, transparent review, rights tracking and predictable per-asset cost. Preserve a manual-review path for high-value campaigns. |

## Current product position

Context Commerce's strongest potential wedge is a workflow system for authorized, measurable creator-content placements: rights declarations, organization membership, placement versions, creator/brand approvals, rendering jobs, delivery authorization and event history in one product.

It is not yet defensible on visual realism. The included renderer supports deterministic 2D compositing and a CV-based planar-surface tracking mode when the user supplies four reference-frame corners. It does not automatically detect the intended object or surface, reason about occlusion, reconstruct 3D geometry, or guarantee frame-to-frame identity. Generative insertion is a configurable adapter, not a pre-verified turnkey provider integration.

## Recommended competitive priorities

### P0: Reliability and trust
- Persist an immutable rights/consent record: who granted permission, what content and placements it covers, permitted channels/territories, start/end dates, revocation and audit trail.
- Make every approval bind to an immutable placement version and invalidate approvals after any material edit.
- Enforce license and consent checks before processing, after rendering and before every download.
- Add cancellation, retries, idempotency keys, job leases, dead-letter handling, per-tenant quotas and explicit cost ceilings.
- Build media safety controls: MIME sniffing, codec/container allowlists, size/duration/resolution limits, sandboxed FFmpeg, malware scanning and retention/deletion policy.
- Move production data to PostgreSQL and object storage; use Redis-backed queue/rate limiting or a managed queue. The included SQLite mode is for local MVP/single-host evaluation.

### P1: Quality advantage
- Automatic surface/object proposals with confidence scores, then creator/editor correction. Never silently claim accurate tracking.
- Tracking quality diagnostics: inlier ratio, drift, occlusion, surface visibility, reprojection error and a confidence timeline.
- Multi-method renderer: static overlay, planar homography tracking, segmentation/occlusion-aware compositing, and provider-based generative edits.
- Human review with side-by-side source/output, frame scrubbing, frame-level rejection, version diff and re-render from the last approved state.
- Build a labeled evaluation set with varied camera motion, lighting, occlusion, product sizes, skin/brand safety cases and video codecs. Track product identity consistency, placement stability, scene preservation, visual artifacts and reviewer acceptance.

### P1: Go-to-market advantage
- Batch campaign setup across SKUs and creator libraries.
- Creator marketplace search, audience/context fit and content rights provenance.
- Campaign-level delivery links, UTM/affiliate tracking and downstream conversion imports.
- Brand/agency multi-workspace controls, exports, SSO and billing only after workflow quality is proven.
- Offer a paid pilot around a measurable outcome: turnaround time, cost per approved placement, creator acceptance rate, and tracked clicks/conversions. Do not claim ROI without attribution evidence.

### P2: Scale and moat
- Provider router selecting a model based on task type, clip duration, quality requirements, expected cost and latency.
- Placement inventory graph connecting creators, scenes, products, rights, approvals and outcomes.
- Consent-aware reuse, rights expiration monitoring and auditable provenance.
- Experiment design for holdouts and incrementality. Separate attributed conversions from causal lift.

## Product metrics to instrument

- Median time from upload to first candidate and to approved asset.
- Candidate acceptance rate and creator rejection rate.
- Tracking failure rate and manual-correction minutes per minute of video.
- Render success rate, retry rate, latency and cost per finished minute.
- Approval turnaround by brand/creator and license-expiration blocks.
- Asset download rate, tracked clicks, conversion events and incremental lift where experiments permit.
- Incidents: unauthorized delivery, cross-tenant access, malware detection, and provider-cost overruns.

## Architecture recommendation

Keep a modular monolith for product APIs and domain logic, with a separate worker service. Use PostgreSQL for authoritative state, object storage for source and output media, Redis or a managed queue for job dispatch and distributed rate limiting, and a provider adapter boundary for external generative systems. Maintain explicit state transitions and append-only audit events. Store provider task IDs and cost/usage data. Add OpenTelemetry traces across upload, analysis, render and delivery.

The included `docker-compose.yml` demonstrates a separate API and worker sharing one local data volume. It is a development stepping stone, not a horizontally scalable production topology: SQLite and local media files remain single-host constraints.

## Release gates before claiming production readiness

1. Cross-tenant negative tests for every entity and media endpoint.
2. Concurrency tests for job claims, duplicate requests, retries and worker crashes.
3. CSRF, session, rate-limit, upload abuse and malware-scanner failure tests.
4. Browser E2E on desktop/mobile with screenshots and console/network checks.
5. Tracking evaluation on a representative labeled set; publish limits and confidence behavior.
6. Generative provider contract tests with a real configured account, known model, cost cap and output quality review.
7. Backup/restore, retention deletion, audit export, monitoring and incident playbooks.

## Sources

- Runway Dev API and product-swap / video-to-video capabilities: https://docs.dev.runwayml.com/api/ and https://dev.runwayml.com/learn/edit-and-finish-video-api
- AdWeft public product positioning: https://www.adweft.com/
- Mirriad public positioning and business model: https://www.mirriad.com/ and https://www.mirriad.com/business-model
