# Annotation guide for the real-footage evaluation

How to collect and label real clips so `python -m benchmark.real_footage` can evaluate Visuara on them (blocker B-7). Labels are written **before** any run and never changed after seeing results; a correction is a new clip id.

## 1. Collecting clips
- **Permission first.** Every clip needs written consent from the person who owns it, whose scope includes `benchmark`. Keep the signed document (`consent.reference`). Clips without consent are refused by the tool.
- **Variety.** Aim for 20–50 clips of 3–15 s from at least 8 different creators:
  - indoor and outdoor;
  - static, panning and handheld;
  - daylight, artificial light and low light;
  - plain and textured surfaces: walls, tables, posters, screens, shelves;
  - some clips where placement should be refused: a hand crossing the surface, a cut, the surface leaving the frame, motion blur.
- **Storage.** Footage stays in `benchmark/real/clips/`, which git ignores. The tool never copies or uploads it.
- **Creators decide the split.** All of one creator's clips are in the same split, chosen from the creator id and `split_salt`, so thresholds tuned on `dev` are tested on people the tuning never saw. Choose `split_salt` once and never change it.

## 2. The manifest (`benchmark/real/manifest.json`)
Copy `manifest.example.json`. Each clip needs every field below:

| Field | What to write |
|---|---|
| `clip_id` | A unique, stable name, for example `c014_office_desk` |
| `file` | Path under `benchmark/real/`, for example `clips/c014.mp4` |
| `sha256` | `python -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" clips/c014.mp4` |
| `creator_id` | A pseudonymous id; the same person always gets the same id |
| `device` | Camera, resolution and frame rate, and any app that re-encoded it |
| `consent` | `granted_by`, `date`, `reference` (the signed document), `scope` (must include `benchmark`) |
| `license` | What you may do with it, and when it must be deleted |
| `label` | Section 3 |

`python -m benchmark.real_footage validate --manifest benchmark/real/manifest.json` checks all of this, and the hashes, before anything runs.

## 3. The placement label (used by the tool today)
For each clip, one candidate surface and the judgement a skilled editor would make:

| Field | How to decide it |
|---|---|
| `quad_norm` | The surface's four corners at `ref_second`, clockwise from top-left, as fractions of width and height. Measure them on the exported frame (`ffmpeg -ss <ref_second> -i clip.mp4 -frames:v 1 ref.png`), at the surface's real edge |
| `ref_second`, `start_second`, `end_second` | The frame the corners are measured on, and the interval a product should appear in |
| `suitable` | `true` if an editor would place a product there for the whole interval |
| `expected_code` | When `suitable` is false, the main reason, using the product's codes: `PARTIAL_OCCLUSION_UNSUPPORTED`, `SCENE_CUT_IN_INTERVAL`, `PRODUCT_OUT_OF_FRAME`, `LOW_TEXTURE`, `NON_PLANAR_OR_UNSTABLE`, `MOTION_BLUR_UNSUPPORTED`, `LIGHTING_CHANGE_UNSUPPORTED` |
| `mode` | `2d` with `product_image`, or `3d` with `model` and `model3d` |

**Disagreements.** Two people label each clip independently. Where they disagree on `suitable`, a third decides, and the clip is marked in a notes file. Agreement rate is reported with the results.

## 4. Labels for the other evaluations (collect now; not read by the tool yet)
These let the M14 evaluations of segmentation, detection and tracking be repeated on real video once the clips exist. Add them under `label.extra` (the tool ignores unknown keys):
- **`scene_boundaries`:** each cut or transition as `{"frame": n, "type": "cut" | "dissolve" | "fade", "end_frame": m}`, from frame-stepping in a video editor. A dissolve's frame is its first blended frame.
- **`objects`:** for every 12th frame, each visible object of the 80 COCO classes as `{"frame": n, "label": "cup", "bbox": [x0, y0, x1, y1], "track": "cup-1"}` in pixels. The same physical object keeps its `track` id through the clip, including after it's hidden. Leave out objects smaller than 16 px.
- **`surface_corners`:** the same four corners as `quad_norm`, every 12th frame through the interval, to measure corner error after homography.

Wiring these into `benchmark/evaluate_segmentation.py`, `evaluate_detection.py` and `evaluate_tracking.py` is a small change once real clips exist. Until then, every result in docs/engineering/AI_EVALUATION.md is from synthetic video or real photographs, and is labelled as such.

## 5. Running
```
python -m benchmark.real_footage run --manifest benchmark/real/manifest.json --split dev  --out benchmark/real/runs/dev-1
python -m benchmark.real_footage sheet  --run benchmark/real/runs/dev-1          # ratings.html for blind human raters
python -m benchmark.real_footage report --run benchmark/real/runs/dev-1 --ratings r1.json r2.json
```
Tune only on `dev`. Run `test` once per change of method, and report it whatever it shows.
