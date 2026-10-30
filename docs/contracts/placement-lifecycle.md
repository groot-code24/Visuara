# Placement lifecycle contract

Source of truth: [`app/lifecycle.py`](../../app/lifecycle.py). Every status change goes through `transition()`, which checks the table, updates with a compare-and-set on `(status, version)`, and writes a `placement.transition` audit event.

```
DRAFT ─propose─▶ PROPOSED ─creator─▶ CREATOR_APPROVED ─brand─▶ BRAND_APPROVED
                                                                  │ (automatic, audited)
                                     RIGHTS_VERIFIED ◀────────────┘
                                            │
                                     QUALITY_VERIFIED ─▶ READY_FOR_RENDER ─▶ RENDERING ─▶ RENDERED ─▶ DELIVERED
                                                                ▲                │
                                                                └─ RENDER_FAILED ◀┘
Side exits: REJECTED, REVOKED, EXPIRED, CANCELLED (see table).  Revision: any state except CANCELLED ─▶ DRAFT (version + 1).
```

| From | Allowed targets |
|---|---|
| DRAFT | PROPOSED, REJECTED, CANCELLED |
| PROPOSED | CREATOR_APPROVED, REJECTED, CANCELLED, REVOKED |
| CREATOR_APPROVED | BRAND_APPROVED, REJECTED, CANCELLED, REVOKED |
| BRAND_APPROVED | RIGHTS_VERIFIED, EXPIRED, CANCELLED, REVOKED |
| RIGHTS_VERIFIED | QUALITY_VERIFIED, CANCELLED, REVOKED |
| QUALITY_VERIFIED | READY_FOR_RENDER, CANCELLED, REVOKED |
| READY_FOR_RENDER | RENDERING, EXPIRED, CANCELLED, REVOKED |
| RENDERING | RENDERED, RENDER_FAILED, CANCELLED, REVOKED |
| RENDER_FAILED | READY_FOR_RENDER (after re-verification), CANCELLED, REVOKED |
| RENDERED | DELIVERED, EXPIRED, REVOKED, CANCELLED |
| DELIVERED | EXPIRED, REVOKED |
| REJECTED, REVOKED, EXPIRED, CANCELLED | none (revision creates a new draft version, except from CANCELLED) |

## Gates
| Transition | Gate (server-enforced) |
|---|---|
| DRAFT → PROPOSED | Brand. Current-version analysis `PASSED` **and** fit `ACCEPTED` (confidence ≥ 0.75). |
| PROPOSED → CREATOR_APPROVED | Creator who uploaded the video (rights holder). Current-version consent active. |
| CREATOR_APPROVED → BRAND_APPROVED | Brand. Creator must approve first (`AWAITING_CREATOR_APPROVAL`). |
| → RIGHTS_VERIFIED | Consent active, not revoked, within start/expiry; video licence valid. Expiry moves to EXPIRED. |
| → QUALITY_VERIFIED | Analysis PASSED for this version and content hash; fit accepted; brand and rights-holder approvals recorded with this content hash. |
| → READY_FOR_RENDER | A verified renderer supports the analysed scene class. |
| READY_FOR_RENDER → RENDERING | All gates re-checked, including product image hash and track-file hash. Job records version and analysis. |
| RENDERING → RENDERED | Worker: output passed validation; job still owned (lease token) and not cancelled. |
| RENDERED → DELIVERED | First authorized download; all gates re-checked. |

## Versions
A placement version is an immutable snapshot (`placement_versions`) of video, product (including the image SHA-256), geometry, timing and render options, hashed with SHA-256. Revision increments the version and resets fit, quality and approval flags. It also resets consent and terms, deactivates links, and cancels active jobs. Records for older versions stay in place for audit, but they can never authorize the new version.

Fit review and quality checks are only accepted on `DRAFT`. Rights terms can change only in `DRAFT` or `PROPOSED`. A brand-side change of terms clears creator consent.
