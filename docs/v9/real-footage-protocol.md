# Real-footage benchmark protocol (V9)

**Status: tooling built and tested; no real footage evaluated.** No authorised clips were supplied, so every quality number in this repository still comes from synthetic scenes. This document is the procedure that will produce real evidence once footage exists. Tooling: [benchmark/real_footage.py](../../benchmark/real_footage.py).

## 1. Collect
- **Target:** 50–100 clips from at least 10 creators and at least 4 phone or camera models. Include indoor and outdoor footage, daylight and tungsten, tripod and handheld shots, and the compression of the platforms you deliver to.
- **Consent first.** Each clip needs a written consent record whose scope explicitly includes `benchmark`. The tool refuses clips without one. Never use scraped or "public" creator footage.
- **Deliberate negatives** (at least 40% of clips): hands or people crossing the surface, objects standing in front of the product position, cuts, fast pans with motion blur, low-texture surfaces, mirrors and glass, lighting changes, and surfaces leaving the frame.

## 2. Label before running anything
For each clip, a reviewer opens it in the app's corner editor and records the following (`benchmark/real/manifest.example.json` shows the format):
- the surface corners at a reference time, the interval, and the mode (2D image or 3D model);
- the product asset, and for 3D the real surface size in metres;
- `suitable` (would a careful editor accept a placement here?) and, for negatives, the expected reason code.

Labels are frozen with the file's SHA-256: if the file changes, validation fails.

## 3. Split by creator
`split_salt` in the manifest assigns each creator's clips entirely to `dev` (about 40%) or `test`. Choose the salt once, before the first run, and never change it. Thresholds may be tuned only on `dev`. Run `test` once per frozen version and record the version.

## 4. Run
```
python -m benchmark.real_footage validate --manifest real/manifest.json
python -m benchmark.real_footage run --manifest real/manifest.json --split dev --out real/runs/dev-1
```
- **Recorded per clip:** decision, reason codes, analysis time, and for accepted clips a render plus its output validation.
- **Crashes** are recorded as `ERROR` and count against the product.
- **Copying:** footage stays where the manifest points; the tool never copies or uploads it. Renders are written into the run directory, so keep run directories as private as the footage.

## 5. Human realism ratings (blind)
```
python -m benchmark.real_footage sheet --run real/runs/test-1        # ratings.html + anonymised item1.mp4, item2.mp4, ...
python -m benchmark.real_footage report --run real/runs/test-1 --ratings ratings_ana.json ratings_ben.json ratings_chi.json
```
- **Raters:** at least 3 who did not build the placements, each working independently with the sheet.
- **Blinding:** the sheet shows no clip ids, labels or pipeline scores; files are renamed and the order shuffled. Keep `sheet_key.json` away from raters.
- **Controls:** optionally mix in unedited clips. The sheet tells raters that some clips may contain no edits.
- **Rubric** (1 = clearly fake, 5 = indistinguishable from filmed):

| Criterion | Question |
|---|---|
| lighting | Lighting and colour match the scene |
| edges | Edges look natural (no halo, cut-out or flicker) |
| contact | Contact and shadows make the product sit on the surface |
| stability | Stable over time (no swimming or jitter) |
| fidelity | Product looks like the real product (shape, label, colour) |
| overall | Could this have been filmed? |

## 6. Report
`report` writes `report.json` with the following:
- **Gate accuracy** on real clips only: false accepts and false rejects, each with a Wilson 95% interval (0 errors in 21 clips still allows up to 15%). It also reports the share of rejections carrying the expected reason, crashes, and output validation.
- **Ratings** per criterion: mean with a 95% interval, share rated 4 or 5, and Krippendorff's alpha (interval) for inter-rater agreement. Alpha below about 0.67 means the raters disagree too much to draw conclusions.
- **Synthetic rows** (`"synthetic": true`, used only to smoke-test the tooling) are reported separately as "not evidence" and excluded from ratings.

## Acceptance targets for a controlled pilot (proposed)
- **Test split:** 0 false accepts, with the upper 95% bound of the false-accept rate at or below 10%. That needs about 30 or more negatives.
- **False rejects:** at or below 25% of suitable clips.
- **Ratings:** median `overall` of 4 or more, with Krippendorff's alpha of 0.67 or more.
- **Crashes:** none.

These targets are proposals for you to confirm. They are not yet measured.
