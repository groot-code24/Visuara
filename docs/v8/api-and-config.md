# V8 API and configuration additions

V6 endpoints are unchanged; see [../contracts/api-contract.md](../contracts/api-contract.md). Every endpoint needs a session cookie. Errors use `{"detail": {"code", "message"}}` or the FastAPI 422 shape.

## Products and 3D models
| Method and path | Role | Notes |
|---|---|---|
| `POST /api/products` | brand | Now also writes a derived sRGB PNG (EXIF orientation, ICC conversion). The response `asset_report.warnings` may include `NO_TRANSPARENCY`, `LOW_RESOLUTION`, `EDGE_HALO` or `ICC_PROFILE_IGNORED` |
| `POST /api/products/{pid}/model` | brand | Multipart `file`: binary `.glb` only, ≤ 25 MB, ≤ 100k triangles, textures ≤ 4096 px, no external or data URIs. Returns `model_report` |

## Placements
`POST /api/placements` and `PATCH /api/placements/{pid}/revise` accept these new fields:

| Field | Values |
|---|---|
| `color_match` | `true` (default): temporal per-channel gains measured on the tracked surface |
| `adjust` | `{exposure_ev: -1..1, temperature: -1..1}` |
| `model3d` | `{surface_size_m: [w, h] (0.02–20 m), anchor_uv: [u, v] (0–1), yaw_deg, scale (0.05–20), light?: {azimuth_deg, elevation_deg (0–89)}, camera_hfov_deg?: 10–150}`. Requires a product model (`MODEL_REQUIRED`) |

`GET /api/placements/{pid}/preview?t=<seconds>&original=<bool>` returns a watermarked JPEG rendered by the exact production plan. Response header: `X-Preview: unapproved`.

3D quality reason codes (from `quality.evaluate_3d`): `FOCAL_UNOBSERVABLE`, `POSE_AMBIGUOUS`, `POSE_INCONSISTENT`, `OBJECT_OFF_SURFACE`, `OBJECT_OUT_OF_FRAME`, `OBJECT_TOO_SMALL` and `FOREGROUND_MOTION_IN_OBJECT_REGION`, plus the planar tracking codes (`TRACKING_LOST`, `TRACKING_DRIFT`, `SCENE_CUT_IN_INTERVAL` and others).

## Marketplace and commerce (`app/commerce.py`)
| Method and path | Role | Notes |
|---|---|---|
| `PUT /api/videos/{vid}/listing` | creator (owner) | `{listed, description, categories[], excluded_categories[], price_usd, channels[], territories[]}` |
| `GET /api/videos/{vid}/listing` | owning org | Returns `{listed: false}` when there is no listing |
| `POST /api/campaigns` | brand | `{name, objective, product_id?, category, budget_usd, channels[], territories[], brand_safety_exclusions[], starts_on?, ends_on?}` |
| `GET /api/campaigns` | own org | Includes committed spend and offer counts by status |
| `GET /api/marketplace?campaign_id=&q=&category=&max_price=&include_blocked=` | brand | Listed, analysed videos from other organizations with match score and blockers (category conflict, brand safety, channels, territories, budget). Metadata and keyframes only |
| `GET /api/marketplace/{vid}/frames/{ms}` | any signed-in user | Analysed keyframe of a *listed* video only (404 otherwise); no stream before a licence. See security review S7 |
| `POST /api/offers` | brand | `{campaign_id, video_id, channels?, deliverables, message}`. Fixed price taken from the listing; no auctions |
| `GET /api/offers` | both | Offers on the caller's side |
| `POST /api/offers/{id}/accept` | creator | Creates the video grant (licence). Grant terms bound placement rights |
| `POST /api/offers/{id}/decline` | creator | |
| `POST /api/offers/{id}/withdraw` | brand | Only before acceptance |
| `POST /api/offers/{id}/complete` | brand | After delivery |
| `POST /api/offers/{id}/revoke-grant` | creator | Takedown: revokes the brand's placements, cancels jobs, blocks delivery and links |
| `GET /api/earnings` | creator | Agreed-fee records; no payments are processed |
| `GET /api/campaigns/{cid}/report[?format=csv]` | brand | Deliveries, exposure seconds and tracked-link clicks (measured), alongside explicitly *not measured* impressions, reach and lift |

Offer states: `OFFERED → ACCEPTED → DELIVERED → COMPLETED`; `OFFERED → DECLINED | WITHDRAWN`; `ACCEPTED | DELIVERED → REVOKED`.

## Delivery
| Method and path | Notes |
|---|---|
| `POST /api/jobs/{jid}/download-link?channel=` | Returns `{url, expires_at, ttl_seconds}`. HMAC-SHA256 token bound to job, user, placement version and channel |
| `GET /api/media/{token}` | Re-checks every delivery gate (rights, licence, revocation, validation) at use. Tokens are redacted from access logs |

## Configuration
| Variable | Default | Meaning |
|---|---|---|
| `CC_SIGNING_KEY` | unset | HMAC key for download links. If unset, a random key is created owner-only at `CC_DATA_DIR/.signing_key` (excluded from backups and archives) |
| `CC_DOWNLOAD_LINK_TTL` | `300` | Signed link lifetime in seconds |

## Operations
```
python -m ops.backup backup  --data <CC_DATA_DIR> --out <dir>
python -m ops.backup verify  --backup <dir>
python -m ops.backup restore --backup <dir> --data <empty dir>
```
See [storage-and-recovery.md](storage-and-recovery.md).
