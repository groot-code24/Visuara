# Visuara Intelligence

Intelligence turns an uploaded video into scenes, placement opportunities and draft advertising inventory. Everything described here is implemented and tested, unless it is labelled otherwise.

## Ingestion
- **Uploads:** single upload (`POST /api/videos`) or direct-to-storage in parts (`/api/uploads/*`), with progress, cancellation and retry in the web app.
- **Checks on the file itself:** FFprobe reads it, and a file that isn't a real video is refused with 400 whatever its name or type. Size, duration and plan limits apply, and ClamAV scans it when configured.
- **Metadata:** duration, resolution, frame rate, codec, container, audio presence, aspect ratio and size.
- **Duplicates:** a SHA-256 of every upload is recorded. An upload with the same content as another video in the organization reports `duplicate_of`; with `reject_duplicates` it is refused (409 `DUPLICATE_VIDEO`).
- **Versions and deletion:**
  - `POST /api/videos/{id}/versions` adds a version and keeps every earlier version and its analyses.
  - `DELETE /api/videos/{id}` is a soft delete that removes all stored files.
  - Both are refused while a placement or reservation uses the video (409 `VIDEO_IN_USE`).

## The analysis job
`POST /api/videos/{id}/analyses` queues an analysis version. Only one can be queued or running per video version: the database enforces it, and asking again returns the existing job. The worker, or on Vercel the HTTP job runner, claims it with a lease and runs these stages, recording each one's status, duration and detail:

| Stage | What happens | Status today |
|---|---|---|
| Validating | Video present, not deleted, licence valid, file readable | Done |
| Extracting metadata | FFprobe on the file | Done |
| Segmenting | Colour-histogram distance every 0.25 s, the smaller of the distance as recorded and after scaling each sample to the same mean brightness, so lighting drift is not a cut; a boundary where it exceeds 0.35, unless the picture comes back by the next sample (a flash); each boundary then placed on its exact frame and typed as a hard cut or a gradual transition (dissolve, fade); scenes shorter than 1 s merged | Done |
| Extracting frames | 3 frames per scene, stored under the analysis version | Done |
| Detecting objects | YOLOX-Tiny (Apache-2.0, COCO's 80 classes) on a frame every 0.5 s, at most 240 frames per analysis; chosen by benchmark ([AI_EVALUATION.md](../engineering/AI_EVALUATION.md) §2). `UNAVAILABLE` with the reason when the model file is missing or fails its checksum | Done |
| Tracking objects | Detections linked per scene: stable identities, gaps recorded when an object is hidden (never filled in), recovery by appearance | Done |
| Understanding scenes | Measured camera motion, lighting, the setting suggested by detected objects, relations between objects, brand-safety signals; a validated description from the vision model when `CC_VISION_MODEL` is set | **Partial** without a vision model |
| Detecting placements | Surfaces from closed contours and from straight edges on the representative frame, each followed through the scene by homography. This gives the visible window, planarity, stability, occlusion (from the track and from detected people) and why it stopped. Candidates that can't be tracked, aren't planar or are seen for under 1 s are rejected with the reason | Done |
| Scoring opportunities | Deterministic scoring and typed opportunities, then draft inventory | Done |

- **Resuming:** a job that runs out of time continues in the next request without repeating finished stages.
- **Failures:** retried once, then recorded as FAILED with the stage and error. A validation failure isn't retried.
- **Cancellation:** cancelled jobs are recorded.
- **Versions are never overwritten.** The latest completed analysis also fills the legacy keyframe view that the decision engine reads.
- **Configuration:** settings come from environment variables (`CC_ANALYSIS_*`) and are stored with each version.

## Opportunities
Each opportunity is validated against the typed schema in `app/opportunities.py` (specification §6.4) before it is stored. It has:
- the scene and its lineage (video, video version, analysis version);
- the candidate surface and its category (horizontal, vertical or angled surface);
- normalised geometry (bounding box and the four corners at its first visible frame) and a screen region;
- the visible window;
- the feasibility score, product-category compatibility, brand safety, rendering complexity, confidence and its factors, and an explanation.

### Scoring
Scoring follows `app/scoring.py`, configuration `v1`:
- **Factors:** visual feasibility 25%, available area 15%, visibility 15%, exposure 15%, contextual relevance 10%, brand safety 10%, content rights 10%.
- **Unmeasured factors are left out:** a factor that couldn't be measured is marked unavailable and the other weights are rescaled. Without a vision model, contextual relevance is never measured.
- **It's a heuristic:** every explanation says so.

### Brand safety
- Platform terms and the organization's own terms (`/api/brand-safety/rules`) are matched as whole words against the scene description and the video title.
- Without a description, brand safety is `NOT_EVALUATED`, and a manager reviews it (`POST /api/opportunities/{id}/review`). The review is recorded with who made it.

### Inventory
An opportunity that is valid or needs review, and isn't unsafe, becomes a DRAFT inventory item. Listing it is covered in [VISUARA_MARKETPLACE.md](VISUARA_MARKETPLACE.md).

## Measured quality
See [AI_EVALUATION_REPORT.md](../qa/AI_EVALUATION_REPORT.md). The figures come from synthetic footage only, because no licensed, labelled real footage exists yet (B-7).

## Not implemented
- Object detection and tracking.
- Environment classification.
- Semantic or embedding search (designed only, AD-11).
- Occlusion analysis as a separate stage. The quality check of a placement still measures occlusion frame by frame before a render.
