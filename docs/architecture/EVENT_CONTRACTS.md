# Event contracts

Events are written to the transactional outbox (`app/outbox.py`, AD-7) in the same transaction as the change they describe. An event exists exactly when its change was committed.

## Envelope
Every event a consumer receives has:

| Field | Type | Meaning |
|---|---|---|
| `event_id` | UUID string | Unique and stable across retries. Use it for idempotency. |
| `event_type` | string | One of the types below |
| `event_version` | integer | Version of this type's payload contract |
| `org_id` | integer or null | Organization the event belongs to |
| `aggregate_type`, `aggregate_id` | string | The entity the event is about. Ordering is guaranteed per aggregate. |
| `payload` | object | Defined per type below |
| `trace_id` | string or null | Correlation id, when the emitter had one |
| `attempts` | integer | Failed delivery attempts before this one |
| `created_at` | UTC text | When the change was committed |

## Delivery
- **Who delivers:** the worker loop and the HTTP job runner call `outbox.deliver()`.
  - With a worker process, delivery follows within one poll interval.
  - In HTTP mode on Vercel, it happens on the next runner request. A consumer that needs prompt delivery must arrange a runner request after commit. None does yet.
- **At least once.** Each event is leased, so only one worker delivers it at a time. An expired lease can be taken over.
- **Idempotent per consumer.** A handler runs inside a transaction that also records `(consumer, event_id)`. Once that commits, the handler never runs again for that event, even if the event is redelivered. Handlers with effects outside the database must be idempotent themselves, keyed on `event_id`.
- **Ordered per aggregate.** An event is held while an earlier event of the same aggregate is still pending.
- **Retries:** when a handler raises, the event waits `5 s × 2^(attempts−1)`, capped at one hour, and tries again. After `max_attempts` (default 8) it becomes `DEAD` and is logged as an error. A dead event doesn't hold later events back, so consumers must tolerate a gap. To replay a dead event, an operator sets it back to `PENDING`.

## Versioning rules
- **Additive changes keep the version:** a new optional payload field, for example.
- **Breaking changes add a version:** a removed or renamed field, or a changed meaning or type. During the change, the emitter writes the new version and consumers accept both.
- **Contracts are code:** `outbox.EVENT_TYPES` lists every type with its current version. Emitting an unlisted type raises `UnknownEvent`.

## Event types

### `video.version.created` v1
Emitted when an upload records a video version.

| Field | Type |
|---|---|
| `video_id` | integer |
| `video_version_id` | integer |
| `version` | integer |
| `content_sha256` | hex string |
| `size_bytes` | integer |

Aggregate: `video`, the video id.

### `analysis.completed` v1
Emitted when an analysis version is recorded as `COMPLETED`.

| Field | Type |
|---|---|
| `video_id` | integer |
| `video_version_id` | integer |
| `analysis_version_id` | integer |
| `version` | integer |
| `scenes` | integer, the number of scenes recorded |
| `pipeline` | string, for example `legacy-keyframes-v1` |

Aggregate: `video`, the video id.

### `analysis.failed` v1
Emitted when an analysis fails. The error is in `analysis_versions.error_json`.

| Field | Type |
|---|---|
| `video_id` | integer |
| `analysis_version_id` | integer |
| `version` | integer |

Aggregate: `video`, the video id.

### Phases 3 to 12
All version 1. The payload always carries the ids named in the second column.

| Type | Payload | Aggregate |
|---|---|---|
| `video.deleted` | `video_id` | video |
| `reservation.created` | `reservation_id`, `inventory_id`, `brand_org_id`, `campaign_id`, `price_minor`, `currency` | inventory |
| `reservation.released`, `reservation.expired` | `reservation_id`, `inventory_id` | inventory |
| `campaign.status_changed` | `campaign_id`, `from`, `to` | campaign |
| `placement_request.created` | `placement_request_id`, `campaign_id`, `inventory_id` | placement_request |
| `placement_request.approved` | `placement_request_id`, `quote_id`, `campaign_id` | placement_request |
| `placement_request.rejected` | `placement_request_id` | placement_request |
| `invoice.issued` | `invoice_id`, `number`, `amount_minor`, `currency`, `placement_request_id` | invoice |
| `invoice.paid` | `invoice_id`, `amount_minor`, `currency`, `placement_request_id` | invoice |
| `delivery.recorded` | `delivery_id`, `placement_request_id`, `campaign_id`, `job_id`, `output_sha256` | placement_request |
| `delivery.accepted` | `placement_request_id`, `delivery_id`, `campaign_id` | placement_request |
| `analysis.completed` (pipeline) | as above; `pipeline` is `visuara-pipeline-v1` | video |

## Consumers
| Consumer | Types | Effect |
|---|---|---|
| `analytics` (`delivery.register_consumers`) | `reservation.created`, `placement_request.created`, `placement_request.approved`, `delivery.recorded`, `delivery.accepted`, `invoice.issued`, `invoice.paid` | One `analytics_events` row per event (unique `source_event_id`), attributed to the campaign; campaign reports count them |

Events without a consumer are only marked `DELIVERED`.
