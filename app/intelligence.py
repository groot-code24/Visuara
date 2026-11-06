"""Intelligence pipeline (Phase 4, specification §6.2–6.3): analysis as a staged background job.

An analysis is an `analysis_versions` row. Queueing it is cheap; the worker loop or the HTTP job runner claims it with a
lease and runs the stages in order, recording each in `stages_json` (status, timings, detail). A stage that finished is
not run again when a job resumes after a lost lease or a request's time limit, and a stage that did not finish has its
rows removed before it runs again. A completed analysis is never changed (AD-4).

Stages and what really happens in each:
  VALIDATING            the video exists, is not deleted, its licence is valid and its file is readable
  EXTRACTING_METADATA   FFprobe reads codec, container, audio and aspect ratio from the file
  SEGMENTING            colour-histogram distance between frames sampled every `sample_seconds`; a cut where it exceeds
                        `cut_threshold`, scenes shorter than `min_scene_seconds` merged into their neighbour
  EXTRACTING_FRAMES     `frames_per_scene` frames per scene, stored under this analysis version (each version owns its images)
  DETECTING_OBJECTS     an evaluated COCO detector (`app/detection.py`) on frames every `detection_step_seconds`, at most
                        `detection_max_frames` per analysis; UNAVAILABLE with the reason when no model file is installed
  TRACKING_OBJECTS      tracks per scene from those detections (`app/objtrack.py`): stable ids, occlusion gaps, recovery
  UNDERSTANDING_SCENES  measured camera motion, lighting, object context, relations and safety signals (`app/understanding.py`);
                        a validated description and classification from the vision model when CC_VISION_MODEL is set
                        (`app/vision.py`), otherwise none, and the stage is PARTIAL
  DETECTING_PLACEMENTS  planar surfaces from contours and straight edges (`app/surfaces.py`) on the representative frame,
                        each followed through the scene by homography; occlusion from the track and from detected people;
                        candidates that cannot be tracked, are not planar or are seen too briefly are rejected with a reason
  SCORING_OPPORTUNITIES deterministic, explained scoring (`app/scoring.py`), typed opportunities, draft inventory
Only when every required stage finished is the analysis COMPLETED. UNAVAILABLE stages are reported, not hidden.
"""
import json, os, secrets, time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import main as m, storage, obs, outbox, analysis_versions, videos as video_assets

router = APIRouter()
PIPELINE = 'visuara-pipeline-v1'
STAGES = ('VALIDATING', 'EXTRACTING_METADATA', 'SEGMENTING', 'EXTRACTING_FRAMES', 'DETECTING_OBJECTS', 'TRACKING_OBJECTS',
          'UNDERSTANDING_SCENES', 'DETECTING_PLACEMENTS', 'SCORING_OPPORTUNITIES')
LEASE_SECONDS = 300
DEFAULT_CONFIG = {'sample_seconds': 0.25, 'cut_threshold': 0.35, 'min_scene_seconds': 1.0, 'frames_per_scene': 3,
                  'max_surfaces_per_scene': 3,
                  # 1: a cut needs the colour histogram to change both as recorded and after scaling each sample to the same
                  # mean brightness (gain 0.4x to 2.5x). A lighting drift changes only the first, an amplified plain wall only
                  # the second, a real cut both. Measured in docs/qa/AI_EVALUATION_REPORT.md.
                  'cut_gain_normalise': 1,
                  # a boundary whose largest single-frame change is under half the threshold is a gradual transition
                  'gradual_ratio': 0.5, 'gradual_spread': 0.4,
                  # M14: detection every half second, at most 240 frames per analysis (about 50 s of inference on one vCPU,
                  # measured in docs/engineering/AI_EVALUATION.md); a longer video is sampled more sparsely, never past the cap
                  'detection_step_seconds': 0.5, 'detection_max_frames': 240, 'detection_model': '',
                  'track_step_seconds': 0.12, 'min_visible_seconds': 1.0, 'min_planarity': 0.6}


class Cancelled(Exception): pass
class OutOfTime(Exception): pass


def config():
    cfg = dict(DEFAULT_CONFIG)
    for k in cfg:
        v = os.getenv(f'CC_ANALYSIS_{k.upper()}')
        if v: cfg[k] = type(cfg[k])(v)
    return cfg


# ---------------------------------------------------------------- queueing
def queue(c, video, user_id):
    """Queue an analysis of the video's current version. An analysis already queued or running for it is returned instead."""
    vv = c.execute('SELECT id FROM video_versions WHERE video_id=? AND version=?', (video['id'], video['current_version'])).fetchone()
    if not vv: raise HTTPException(409, {'code': 'NO_VIDEO_VERSION', 'message': 'This video has no recorded version'})
    c.lock(f'analysis-version-{video["id"]}')
    existing = c.execute("SELECT * FROM analysis_versions WHERE video_version_id=? AND status IN ('QUEUED','RUNNING')", (vv['id'],)).fetchone()
    if existing: return dict(existing), False
    version = (c.execute('SELECT MAX(version) FROM analysis_versions WHERE video_id=?', (video['id'],)).fetchone()[0] or 0) + 1
    from . import scoring
    cfg = dict(config(), scoring_version=scoring.current(c)['version'])
    aid = c.execute("INSERT INTO analysis_versions(org_id,video_id,video_version_id,version,status,pipeline,config_json,model_versions_json,created_by,stage) "
                    "VALUES(?,?,?,?,'QUEUED',?,?,?,?,'QUEUED')",
                    (video['org_id'], video['id'], vv['id'], version, PIPELINE, json.dumps(cfg),
                     json.dumps(model_versions(cfg)), user_id)).lastrowid
    return dict(c.execute('SELECT * FROM analysis_versions WHERE id=?', (aid,)).fetchone()), True


def model_versions(cfg):
    """What will run, recorded with the analysis so a result can be traced to the models and algorithms that made it."""
    from . import detection, objtrack, surfaces, understanding, vision
    det = detection.status() if not cfg.get('detection_model') else (lambda: dict(detection.load(cfg['detection_model']).describe(), status='AVAILABLE'))()
    return {'object_detection': det.get('version') if det['status'] == 'AVAILABLE' else None,
            'object_detection_status': det if det['status'] != 'AVAILABLE' else {'status': 'AVAILABLE', 'licence': det['licence'], 'source': det['source']},
            'object_tracker': objtrack.ALGORITHM, 'vision': vision.model(), 'vision_schema': vision.SCHEMA_VERSION,
            'surface_detector': f'opencv-contours-v1+{surfaces.ALGORITHM}', 'surface_tracker': surfaces.TRACK_ALGORITHM,
            'camera_motion': understanding.CAMERA_ALGORITHM, 'scene_context': understanding.CONTEXT_ALGORITHM}


def _claim():
    now = time.time()
    with m.db() as c:
        r = c.execute("SELECT id FROM analysis_versions WHERE pipeline=? AND (status='QUEUED' OR (status='RUNNING' AND (lease_until IS NULL OR lease_until<?))) ORDER BY id LIMIT 1",
                      (PIPELINE, now)).fetchone()
    if not r: return None, None
    token = secrets.token_hex(8)
    with m.db() as c:
        ok = c.execute("UPDATE analysis_versions SET status='RUNNING', lease_until=?, lease_token=?, attempts=attempts+1, started_at=COALESCE(started_at,CURRENT_TIMESTAMP) "
                       "WHERE id=? AND (status='QUEUED' OR (status='RUNNING' AND (lease_until IS NULL OR lease_until<?)))", (now + LEASE_SECONDS, token, r['id'], now)).rowcount
    return (r['id'], token) if ok == 1 else (None, None)


# ---------------------------------------------------------------- running
class _Run:
    def __init__(self, aid, token, stop):
        self.aid, self.token, self.stop = aid, token, stop
        with m.db() as c: self.row = dict(c.execute('SELECT * FROM analysis_versions WHERE id=?', (aid,)).fetchone())
        self.cfg = json.loads(self.row['config_json']); self.stages = json.loads(self.row['stages_json'] or '{}')
        with m.db() as c:
            self.video = dict(c.execute('SELECT * FROM videos WHERE id=?', (self.row['video_id'],)).fetchone())
            self.vv = dict(c.execute('SELECT * FROM video_versions WHERE id=?', (self.row['video_version_id'],)).fetchone())
        self.src = None

    def save(self, stage, progress):
        with m.db() as c:
            if c.execute('UPDATE analysis_versions SET stage=?, stages_json=?, progress=?, lease_until=? WHERE id=? AND lease_token=?',
                         (stage, json.dumps(self.stages), progress, time.time() + LEASE_SECONDS, self.aid, self.token)).rowcount != 1:
                raise Cancelled('lease lost')
            if c.execute('SELECT cancel_requested FROM analysis_versions WHERE id=?', (self.aid,)).fetchone()[0]: raise Cancelled('cancelled')

    def source(self):
        if self.src is None: self.src = storage.local(self.vv['source_path'])
        return self.src


def _stage_validating(r):
    v = r.video
    if v.get('deleted_at'): raise ValueError('the video was deleted')
    if not m._license_ok(v): raise ValueError('the video licence is not confirmed or has expired')
    if not Path(r.source()).exists(): raise ValueError('the video file is not available in storage')
    return {'status': 'DONE', 'detail': {'licence_expires': v.get('license_expires')}}


def _stage_metadata(r):
    d = video_assets.details(r.source())
    return {'status': 'DONE', 'detail': dict(d, duration=r.vv['duration'], width=r.vv['width'], height=r.vv['height'], fps=r.vv['fps'], size_bytes=r.vv['size_bytes'])}


def _colour_hist(cv2, img):
    h = cv2.calcHist([img], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256]); cv2.normalize(h, h)
    return h


def _hists(cv2, np, frame, cfg):
    small = cv2.resize(frame, (96, 54))
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    hist = [_colour_hist(cv2, small)]
    if cfg.get('cut_gain_normalise'):
        gain = min(2.5, max(0.4, 128.0 / max(float(gray.mean()), 1.0)))
        hist.append(_colour_hist(cv2, np.clip(small.astype(np.float32) * gain, 0, 255).astype(np.uint8)))
    return small, gray, hist


def _hist_dist(cv2, a, b):
    return min(float(cv2.compareHist(x, y, cv2.HISTCMP_BHATTACHARYYA)) for x, y in zip(a, b))


def _samples(source, cfg):
    """Every `sample_seconds`: the histogram change since the previous sample ('dist'); between the samples either side of
    it ('across'); and to the sample two back ('back'). The last two are small when a change is brief and the picture comes
    back (a camera flash, a passing glare)."""
    import cv2, numpy as np
    cap = cv2.VideoCapture(str(source)); fps = cap.get(cv2.CAP_PROP_FPS) or 25.0; total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(round(fps * cfg['sample_seconds']))); out = []; hists = []; prev_gray = None
    for idx in range(total):
        if not cap.grab(): break
        if idx % step: continue
        ok, frame = cap.retrieve()
        if not ok: break
        small, gray, hist = _hists(cv2, np, frame, cfg)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        dist = _hist_dist(cv2, hists[-1], hist) if hists else 0.0
        out.append({'t': round(idx / fps, 3), 'dist': dist, 'frame': idx,
                    'motion': float(np.abs(gray - prev_gray).mean()) if prev_gray is not None else 0.0,
                    'brightness': float(gray.mean() / 255), 'saturation': float(hsv[..., 1].mean() / 255)})
        hists.append(hist); prev_gray = gray
    cap.release()
    for k in range(1, len(out)):
        if k + 1 < len(out): out[k]['across'] = _hist_dist(cv2, hists[k - 1], hists[k + 1])     # the change is undone next
        if k >= 2: out[k]['back'] = _hist_dist(cv2, hists[k - 2], hists[k])                    # the picture of before is back
    return out


def segment(samples, duration, cut_threshold, min_scene):
    """Scene boundaries [(start, end)] from sampled histogram distances. Pure: tested without video.
    A brief change is not a boundary: neither its start (the next sample matches the one before, 'across') nor its end
    (this sample matches the one two back, 'back'), each below half the threshold."""
    def brief(s): return any(s.get(k) is not None and s[k] < cut_threshold / 2 for k in ('across', 'back'))
    cuts = [s['t'] for s in samples if s['dist'] > cut_threshold and not brief(s)]
    bounds = [0.0]
    for t in cuts:
        if t - bounds[-1] >= min_scene: bounds.append(t)
    if duration - bounds[-1] < min_scene and len(bounds) > 1: bounds.pop()          # a short tail joins the previous scene
    bounds.append(float(duration))
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1) if bounds[i + 1] > bounds[i]]


def _refine(source, times, cfg):
    """The exact frame of each boundary found between two samples: the frames in between are decoded and the largest
    frame-to-frame change is taken. The boundary is a HISTOGRAM_CUT when that change is concentrated in one frame (at least
    `gradual_ratio` of the threshold, and the next largest under `gradual_spread` of it) and no frame is near black;
    otherwise a GRADUAL_TRANSITION (dissolve or fade), placed where it changed most. A hard cut into a very dark shot is
    therefore labelled gradual; its position is unaffected."""
    import cv2, numpy as np
    cap = cv2.VideoCapture(str(source)); fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(fps * cfg['sample_seconds']))); out = []
    try:
        for t in times:
            k = int(round(t * fps)); lo = max(0, k - step)
            cap.set(cv2.CAP_PROP_POS_FRAMES, lo)
            prev, changes, darkest = None, [], 1.0
            for i in range(lo, k + 1):
                ok, f = cap.read()
                if not ok: break
                _, gray, h = _hists(cv2, np, f, cfg)
                darkest = min(darkest, float(gray.mean()) / 255)
                if prev is not None: changes.append((_hist_dist(cv2, prev, h), i))
                prev = h
            changes.sort(reverse=True)
            best, at = changes[0] if changes else (0.0, k)
            second = changes[1][0] if len(changes) > 1 else 0.0
            # a hard cut puts the change in one frame; a dissolve or fade spreads it over several, and a fade passes through black
            concentrated = second < float(cfg.get('gradual_spread', 0.4)) * best
            sharp = best >= cfg['cut_threshold'] * float(cfg.get('gradual_ratio', 0.5)) and concentrated and darkest >= .06
            out.append({'t': round(at / fps, 3), 'source': 'HISTOGRAM_CUT' if sharp else 'GRADUAL_TRANSITION', 'frame_change': round(best, 3),
                        'second_change': round(second, 3)})
    finally:
        cap.release()
    return out


def boundaries(source, duration, cfg):
    """Scene boundaries of a video, [{'t': seconds, 'source', 'frame_change'}], and the samples they came from."""
    samples = _samples(source, cfg)
    scenes = segment(samples, duration, cfg['cut_threshold'], cfg['min_scene_seconds'])
    return _refine(source, [a for a, _ in scenes[1:]], cfg), samples


def _stage_segmenting(r):
    found, samples = boundaries(r.source(), r.vv['duration'], r.cfg)
    starts = [0.0] + [b['t'] for b in found]; ends = starts[1:] + [float(r.vv['duration'])]
    sources = ['HISTOGRAM_CUT'] + [b['source'] for b in found]
    scenes = [(a, b, src) for a, b, src in zip(starts, ends, sources) if b > a]
    with m.db() as c:
        c.execute('DELETE FROM scenes WHERE analysis_version_id=?', (r.aid,))
        for i, (a, b, src) in enumerate(scenes):
            inside = [s for s in samples if a <= s['t'] < b] or samples[:1]
            meta = {'motion': round(sum(s['motion'] for s in inside) / max(1, len(inside)), 3), 'brightness': round(sum(s['brightness'] for s in inside) / max(1, len(inside)), 3),
                    'colourfulness': round(sum(s['saturation'] for s in inside) / max(1, len(inside)), 3), 'samples': len(inside)}
            c.execute("INSERT INTO scenes(org_id,video_id,video_version_id,analysis_version_id,scene_index,start_second,end_second,boundary_source,metadata_json) VALUES(?,?,?,?,?,?,?,?,?)",
                      (r.row['org_id'], r.row['video_id'], r.row['video_version_id'], r.aid, i, a, b, src, json.dumps(meta)))
    return {'status': 'DONE', 'detail': {'scenes': len(scenes), 'cuts': sum(1 for b in found if b['source'] == 'HISTOGRAM_CUT'),
            'gradual_transitions': sum(1 for b in found if b['source'] == 'GRADUAL_TRANSITION'), 'samples': len(samples)}}


def _scenes(r):
    with m.db() as c: return [dict(s) for s in c.execute('SELECT * FROM scenes WHERE analysis_version_id=? ORDER BY scene_index', (r.aid,)).fetchall()]


def _frame_at(cap, t):
    import cv2
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000); ok, frame = cap.read()
    return frame if ok else None


def _stage_frames(r):
    import cv2
    out_dir = m.FRAMES / 'analyses' / f'a{r.aid}'; out_dir.mkdir(parents=True, exist_ok=True)
    n = max(1, int(r.cfg['frames_per_scene'])); written = 0
    cap = cv2.VideoCapture(str(r.source()))
    with m.db() as c: c.execute('DELETE FROM scene_frames WHERE analysis_version_id=?', (r.aid,))
    try:
        for s in _scenes(r):
            span = s['end_second'] - s['start_second']
            times = sorted({round(s['start_second'] + span * (k + 1) / (n + 1), 3) for k in range(n)})
            mid = times[len(times) // 2]
            for t in times:
                frame = _frame_at(cap, min(t, max(0.0, r.vv['duration'] - 0.05)))
                if frame is None: continue
                path = out_dir / f's{s["scene_index"]}_{int(t * 1000)}.jpg'
                cv2.imwrite(str(path), cv2.resize(frame, (640, int(640 * frame.shape[0] / frame.shape[1]) // 2 * 2)), [cv2.IMWRITE_JPEG_QUALITY, 85]); storage.persist(path)
                with m.db() as c:
                    c.execute('INSERT INTO scene_frames(scene_id,analysis_version_id,second,image_path,representative) VALUES(?,?,?,?,?)', (s['id'], r.aid, t, str(path), int(t == mid)))
                written += 1
    finally:
        cap.release()
    return {'status': 'DONE', 'detail': {'frames': written, 'per_scene': n}}


def _appearance(frame, box):
    """A small HSV histogram of a detected box (8 hue x 4 saturation bins, L1-normalised), used to recover a track."""
    import cv2, numpy as np
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    crop = frame[max(0, y0):max(y0 + 1, y1), max(0, x0):max(x0 + 1, x1)]
    if crop.size == 0: return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1], None, [8, 4], [0, 180, 0, 256]).ravel()
    return (h / max(float(h.sum()), 1.0)).round(4).tolist() if h.sum() else None


def _detection_times(r, scenes):
    """Sample times per scene: every `detection_step_seconds`, spread sparser when the video would exceed the frame cap."""
    span = sum(s['end_second'] - s['start_second'] for s in scenes)
    step = max(float(r.cfg['detection_step_seconds']), span / max(1, int(r.cfg['detection_max_frames'])))
    out = {}
    for s in scenes:
        n = max(1, int((s['end_second'] - s['start_second']) / step))
        out[s['id']] = [round(s['start_second'] + step * (k + .5), 3) for k in range(n) if s['start_second'] + step * (k + .5) < s['end_second']] or [round((s['start_second'] + s['end_second']) / 2, 3)]
    return out, step


def _stage_detecting(r):
    import cv2, time as _t
    from . import detection
    try:
        det = detection.load(r.cfg.get('detection_model') or None)
    except detection.Unavailable as e:
        return {'status': 'UNAVAILABLE', 'detail': {'reason': str(e)}}
    scenes = _scenes(r)
    times, step = _detection_times(r, scenes)
    cap = cv2.VideoCapture(str(r.source())); fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    targets = sorted({int(round(t * fps)): (sid, t) for sid, ts in times.items() for t in ts}.items())
    with m.db() as c: c.execute('DELETE FROM detected_objects WHERE analysis_version_id=?', (r.aid,))
    labels, n_det, frames, spent = {}, 0, 0, 0.0
    idx = 0
    try:
        for target, (sid, t) in targets:
            if r.stop is not None and r.stop.is_set(): raise OutOfTime()
            if frames and frames % 20 == 0:                       # keep the lease and notice a cancellation during a long stage
                r.save('DETECTING_OBJECTS', (STAGES.index('DETECTING_OBJECTS') + frames / max(1, len(targets))) / len(STAGES))
            while idx < target:
                if not cap.grab(): break
                idx += 1
            ok, frame = cap.read(); idx += 1
            if not ok: break
            began = _t.perf_counter(); found = det.detect(frame); spent += _t.perf_counter() - began; frames += 1
            fh, fw = frame.shape[:2]
            rows = []
            for d in found:
                x0, y0, x1, y1 = d['bbox']
                rows.append((r.aid, sid, t, d['label'], min(1.0, d['confidence']),
                             json.dumps([round(x0 / fw, 5), round(y0 / fh, 5), round(x1 / fw, 5), round(y1 / fh, 5)]), det.version,
                             json.dumps(_appearance(frame, d['bbox']))))
                labels[d['label']] = labels.get(d['label'], 0) + 1; n_det += 1
            if rows:
                with m.db() as c:
                    for row in rows:
                        c.execute('INSERT INTO detected_objects(analysis_version_id,scene_id,frame_second,label,confidence,bbox_json,model,appearance_json) VALUES(?,?,?,?,?,?,?,?)', row)
    finally:
        cap.release()
    return {'status': 'DONE', 'detail': {'model': det.version, 'licence': det.spec['licence'], 'min_confidence': detection.min_confidence(),
            'frames': frames, 'step_seconds': round(step, 3), 'detections': n_det,
            'ms_per_frame': round(spent / max(1, frames) * 1000, 1), 'labels': dict(sorted(labels.items(), key=lambda kv: -kv[1])[:20])}}


def _scene_tracks(c, aid, scene_id, w, h):
    """Confirmed tracks of a scene as `objtrack` dicts (pixel boxes), rebuilt from the stored detections."""
    tracks = {}
    for tr in c.execute('SELECT * FROM object_tracks WHERE analysis_version_id=? AND scene_id=? ORDER BY track_key', (aid, scene_id)).fetchall():
        tracks[tr['id']] = {'track_id': tr['track_key'], 'label': tr['label'], 'start': tr['start_second'], 'end': tr['end_second'],
                            'confidence': tr['confidence'], 'observed_fraction': tr['observed_fraction'], 'gaps': json.loads(tr['gaps_json'] or '[]'), 'observations': []}
    for d in c.execute('SELECT track_id, frame_second, confidence, bbox_json FROM detected_objects WHERE analysis_version_id=? AND scene_id=? AND track_id IS NOT NULL ORDER BY frame_second',
                       (aid, scene_id)).fetchall():
        if d['track_id'] in tracks:
            b = json.loads(d['bbox_json'])
            tracks[d['track_id']]['observations'].append({'t': d['frame_second'], 'confidence': d['confidence'], 'bbox': [b[0] * w, b[1] * h, b[2] * w, b[3] * h]})
    return list(tracks.values())


def _stage_tracking(r):
    import numpy as np
    from . import objtrack
    det = r.stages.get('DETECTING_OBJECTS', {})
    if det.get('status') != 'DONE':
        return {'status': 'UNAVAILABLE', 'detail': {'reason': 'object tracking needs object detection: ' + det.get('detail', {}).get('reason', 'not run')}}
    w, h = float(r.vv['width'] or 1), float(r.vv['height'] or 1)
    made, recovered, by_label = 0, 0, {}
    with m.db() as c:
        c.execute('DELETE FROM object_tracks WHERE analysis_version_id=?', (r.aid,))
        c.execute('UPDATE detected_objects SET track_id=NULL WHERE analysis_version_id=?', (r.aid,))
    for s in _scenes(r):
        with m.db() as c:
            rows = c.execute('SELECT * FROM detected_objects WHERE analysis_version_id=? AND scene_id=? ORDER BY frame_second, id', (r.aid, s['id'])).fetchall()
        frames, by_key, app = {}, {}, {}
        for d in rows:
            b = json.loads(d['bbox_json']); box = [round(b[0] * w, 1), round(b[1] * h, 1), round(b[2] * w, 1), round(b[3] * h, 1)]
            frames.setdefault(d['frame_second'], []).append({'label': d['label'], 'confidence': d['confidence'], 'bbox': box})
            key = (d['frame_second'], d['label'], tuple(box)); by_key[key] = d['id']
            if d['appearance_json']:
                v = json.loads(d['appearance_json'])
                if v: app[(d['frame_second'], tuple(box))] = np.asarray(v)
        tracks = objtrack.track(sorted(frames.items()), appearance=lambda t, b: app.get((t, tuple(b))))
        with m.db() as c:
            for tr in tracks:
                tid = c.execute('INSERT INTO object_tracks(analysis_version_id,scene_id,label,start_second,end_second,frames,model,track_key,confidence,observed_fraction,gaps_json) '
                                'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (r.aid, s['id'], tr['label'], tr['start'], tr['end'], len(tr['observations']),
                                                                 f"{objtrack.ALGORITHM}+{det['detail']['model']}", tr['track_id'], tr['confidence'],
                                                                 tr['observed_fraction'], json.dumps(tr['gaps']))).lastrowid
                for o in tr['observations']:
                    rid = by_key.get((o['t'], tr['label'], tuple(o['bbox'])))
                    if rid: c.execute('UPDATE detected_objects SET track_id=? WHERE id=?', (tid, rid))
                made += 1; recovered += bool(tr['gaps']); by_label[tr['label']] = by_label.get(tr['label'], 0) + 1
    return {'status': 'DONE', 'detail': {'algorithm': objtrack.ALGORITHM, 'tracks': made, 'tracks_with_gaps': recovered,
            'labels': dict(sorted(by_label.items(), key=lambda kv: -kv[1])[:20])}}


def _stage_understanding(r):
    import cv2
    from . import surfaces, understanding, vision
    name = vision.model(); described = failed = 0; reasons = set()
    w, h = float(r.vv['width'] or 1), float(r.vv['height'] or 1)
    detected = r.stages.get('DETECTING_OBJECTS', {}).get('status') == 'DONE'
    times, _ = _detection_times(r, _scenes(r)) if detected else ({}, 0)
    for s in _scenes(r):
        with m.db() as c:
            frames = c.execute('SELECT * FROM scene_frames WHERE scene_id=? ORDER BY second', (s['id'],)).fetchall()
            tracks = _scene_tracks(c, r.aid, s['id'], w, h) if detected else []
        rep = next((f for f in frames if f['representative']), frames[0] if frames else None)
        meta = json.loads(s['metadata_json']); meta.pop('environment', None)
        grey, _ = surfaces._scene_frames(r.source(), s['start_second'], s['end_second'], step_seconds=0.25, max_samples=40, max_width=320)
        meta['camera'] = understanding.classify_camera(understanding.global_motion(grey))
        imgs = [img for f in frames if (img := cv2.imread(str(storage.local(f['image_path'])))) is not None]
        meta['lighting'] = understanding.lighting(imgs)
        meta['objects_detected'] = detected
        if detected:
            objs = {}
            for t in tracks:
                o = objs.setdefault(t['label'], {'tracks': 0, 'confidence': 0.0}); o['tracks'] += 1; o['confidence'] = round(max(o['confidence'], t['confidence']), 3)
            meta['objects'] = objs
            meta['context'] = understanding.context(tracks)
            meta['relations'] = understanding.relations(tracks, rep['second']) if rep else []
            meta['safety_signals'] = understanding.safety_signals(tracks)
            meta['people'] = understanding.people(tracks, times.get(s['id'], []))
        else:
            meta['context'] = {'status': 'UNAVAILABLE', 'reason': 'object detection did not run'}
        desc = ''
        if name and rep:
            res = vision.describe(storage.local(rep['image_path']))
            meta['vision'] = {k: v for k, v in res.as_dict().items() if k != 'description'}
            if res.status == 'AVAILABLE': desc = res.description; described += 1
            else: failed += 1; reasons.add(res.reason)
        with m.db() as c:
            c.execute('UPDATE scenes SET description=?, metadata_json=? WHERE id=?', (desc, json.dumps(meta), s['id']))
            if desc and rep: c.execute('UPDATE scene_frames SET description=? WHERE id=?', (desc, rep['id']))
    n = len(_scenes(r))
    if not name: note = 'measured attributes and object context only; no vision model is configured, so scenes have no description'
    elif failed: note = f'the vision model answered for {described} of {n} scenes: ' + '; '.join(sorted(reasons))[:300]
    else: note = None
    return {'status': 'DONE' if name and not failed else 'PARTIAL', 'detail': {'described_scenes': described, 'vision_model': name,
            'camera_algorithm': understanding.CAMERA_ALGORITHM, 'context_algorithm': understanding.CONTEXT_ALGORITHM, 'note': note}}


IDENTIFIABLE = {'tv': 'screen', 'laptop': 'laptop screen', 'cell phone': 'phone screen', 'book': 'book cover', 'refrigerator': 'refrigerator door',
                'dining table': 'table top', 'bed': 'bed', 'couch': 'couch'}


# classes that stand or move in front of a surface: when one coincides with a surface, it covers it
FOREGROUND = {'person', 'bicycle', 'car', 'motorcycle', 'bus', 'truck', 'bird', 'cat', 'dog', 'horse', 'sheep', 'cow', 'elephant', 'bear',
              'zebra', 'giraffe'}


def _surface_objects(tracks, ref_second, ref_box, samples, w, h):
    """What the detected objects say about a tracked surface: (identified_as, largest covered fraction, covering labels,
    labels lying on it). Pure: tested without video.
    An object whose box coincides with the surface's (IoU >= 0.6 at the reference frame) is the surface itself when it is
    a screen-like class, covers it when it is something that stands in front (FOREGROUND), and otherwise lies on it.
    Any other overlap during the run counts as covering the surface, as a fraction of the surface's box."""
    import numpy as np
    from .decisions import _iou
    identified, overlap, overlapping, on_surface = None, 0.0, set(), set()
    for tr in tracks:
        near = min(tr['observations'], key=lambda o: abs(o['t'] - ref_second))
        coincident = abs(near['t'] - ref_second) <= 1.0 and _iou(near['bbox'], ref_box) >= .6
        if coincident and tr['label'] in IDENTIFIABLE:
            identified = IDENTIFIABLE[tr['label']]; continue
        if coincident and tr['label'] not in FOREGROUND:
            on_surface.add(tr['label']); continue
        if tr['label'] in IDENTIFIABLE and tr['label'] not in ('couch', 'bed'): continue
        for smp in samples:
            q = np.asarray(smp['quad']) * [w, h]; b = (*q.min(axis=0), *q.max(axis=0))
            for ob in tr['observations']:
                if abs(ob['t'] - smp['t']) > .3: continue
                ov = _overlap(b, ob['bbox'])
                if ov > .02: overlapping.add(tr['label']); overlap = max(overlap, ov)
    return identified, round(float(overlap), 3), sorted(overlapping), sorted(on_surface)


def _frontality(quad):
    """1 when the surface is seen square-on, falling with foreshortening: the ratios of opposite sides."""
    import numpy as np
    q = np.asarray(quad, float); side = [float(np.linalg.norm(q[(i + 1) % 4] - q[i])) for i in range(4)]
    r1 = min(side[0], side[2]) / max(side[0], side[2], 1e-9); r2 = min(side[1], side[3]) / max(side[1], side[3], 1e-9)
    return round(float((r1 * r2) ** .5), 3)


def _overlap(box, other):
    ix = max(0.0, min(box[2], other[2]) - max(box[0], other[0])); iy = max(0.0, min(box[3], other[3]) - max(box[1], other[1]))
    return ix * iy / max((box[2] - box[0]) * (box[3] - box[1]), 1e-9)


def _stage_placements(r):
    import cv2, numpy as np
    from . import surfaces
    found, rejected = [], []
    cap = cv2.VideoCapture(str(r.source()))
    detected = r.stages.get('DETECTING_OBJECTS', {}).get('status') == 'DONE'
    try:
        for s in _scenes(r):
            with m.db() as c:
                f = c.execute('SELECT * FROM scene_frames WHERE scene_id=? AND representative=1', (s['id'],)).fetchone()
            if not f: continue
            ref = _frame_at(cap, f['second'])
            if ref is None: continue
            h, w = ref.shape[:2]
            with m.db() as c: tracks = _scene_tracks(c, r.aid, s['id'], w, h) if detected else []
            cands = surfaces.detect(ref, max_out=int(r.cfg['max_surfaces_per_scene']))
            if not cands: continue
            runs = surfaces.follow(r.source(), s['start_second'], s['end_second'], f['second'], [c['quad'] for c in cands], step_seconds=float(r.cfg['track_step_seconds']))
            motion = json.loads(s['metadata_json']).get('motion', 0.0)
            for cand, run in zip(cands, runs):
                base = {'scene_id': s['id'], 'orientation': cand['orientation'], 'method': cand.get('method'), 'area_fraction': round(cand['area_fraction'], 4)}
                if run['status'] != 'TRACKED':
                    rejected.append(dict(base, reason='NOT_TRACKABLE', detail=run.get('stopped'))); continue
                visible = round(run['end'] - run['start'], 3)
                if run['planarity'] < float(r.cfg['min_planarity']):
                    rejected.append(dict(base, reason='NOT_PLANAR', planarity=run['planarity'])); continue
                if visible < float(r.cfg['min_visible_seconds']):
                    rejected.append(dict(base, reason='SHORT_EXPOSURE', visible_seconds=visible, stopped=run['stopped'])); continue
                first = run['samples'][0]
                qp = np.asarray(first['quad']) * [w, h]; x0, y0 = qp.min(axis=0); x1, y1 = qp.max(axis=0)
                surf_box = (float(x0), float(y0), float(x1), float(y1))
                ref_box = tuple(float(v) for v in cand['bbox'])
                identified, overlap, overlapping, on_surface = _surface_objects(tracks, f['second'], ref_box, run['samples'], w, h)
                step = float(np.median(np.diff([x['t'] for x in run['samples']]))) if len(run['samples']) > 1 else float(r.cfg['track_step_seconds'])
                found.append({'scene_id': s['id'], 'frame_second': run['start'], 'quad': first['quad'], 'orientation': cand['orientation'], 'method': cand.get('method'),
                              'bbox': [x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h], 'area_fraction': cand['area_fraction'], 'texture': cand['texture'],
                              'detector_confidence': min(1.0, cand['confidence']), 'stability': run['stability'], 'planarity': run['planarity'], 'jitter': run['jitter'],
                              'occlusion': run['occlusion'], 'object_overlap': overlap, 'overlapping_objects': overlapping,
                              'objects_on_surface': on_surface,
                              'identified_as': identified, 'frontality': _frontality(cand['quad_px']),
                              'start_second': run['start'], 'end_second': run['end'], 'visible_seconds': visible, 'tracked_frames': run['tracked_samples'],
                              'track_step_seconds': round(step, 3), 'stopped': run['stopped'], 'scene_motion': motion,
                              'path': [{'t': x['t'], 'quad': x['quad']} for x in run['samples'][::max(1, len(run['samples']) // 24)]]})
    finally:
        cap.release()
    return {'status': 'DONE', 'detail': {'candidates': found, 'rejected': rejected,
            'method': f"surfaces from contours and straight edges, followed by homography every {r.cfg['track_step_seconds']} s"}}


def _stage_scoring(r):
    from . import scoring, inventory
    cands = r.stages.get('DETECTING_PLACEMENTS', {}).get('detail', {}).get('candidates', [])
    with m.db() as c:
        c.execute('DELETE FROM inventory_items WHERE opportunity_id IN (SELECT id FROM opportunities WHERE analysis_version_id=?)', (r.aid,))
        c.execute('DELETE FROM opportunities WHERE analysis_version_id=?', (r.aid,))
        cfg = scoring.by_version(c, r.cfg['scoring_version'])
        ids, refused = [], []
        for cand in cands:
            scene = dict(c.execute('SELECT * FROM scenes WHERE id=?', (cand['scene_id'],)).fetchone())
            opp = scoring.build(c, cfg, cand, scene, r.video)
            try:
                oid = scoring.record(c, opp, r.row['org_id'], cfg, writing_analysis_id=r.aid)
                ids.append(oid); inventory.create_draft(c, oid)
            except Exception as e:
                refused.append(str(e)[:200])
    return {'status': 'DONE', 'detail': {'opportunities': len(ids), 'refused': refused, 'scoring_version': cfg['version']}}


RUNNERS = {
    'VALIDATING': _stage_validating, 'EXTRACTING_METADATA': _stage_metadata, 'SEGMENTING': _stage_segmenting, 'EXTRACTING_FRAMES': _stage_frames,
    'DETECTING_OBJECTS': _stage_detecting, 'TRACKING_OBJECTS': _stage_tracking,
    'UNDERSTANDING_SCENES': _stage_understanding, 'DETECTING_PLACEMENTS': _stage_placements, 'SCORING_OPPORTUNITIES': _stage_scoring,
}


def _complete(r):
    with m.db() as c:
        scenes = c.execute('SELECT COUNT(*) FROM scenes WHERE analysis_version_id=?', (r.aid,)).fetchone()[0]
        opps = c.execute('SELECT COUNT(*) FROM opportunities WHERE analysis_version_id=?', (r.aid,)).fetchone()[0]
        tracks = c.execute('SELECT COUNT(*) FROM object_tracks WHERE analysis_version_id=?', (r.aid,)).fetchone()[0]
        outputs = {'scenes': scenes, 'opportunities': opps, 'object_tracks': tracks,
                   'unavailable_stages': [k for k, v in r.stages.items() if v['status'] == 'UNAVAILABLE']}
        if c.execute("UPDATE analysis_versions SET status='COMPLETED', stage='COMPLETED', progress=1, outputs_json=?, stages_json=?, finished_at=CURRENT_TIMESTAMP, "
                     "lease_until=NULL WHERE id=? AND lease_token=? AND status='RUNNING'", (json.dumps(outputs), json.dumps(r.stages), r.aid, r.token)).rowcount != 1:
            raise Cancelled('lease lost')
        # The current view (frames, read by the existing screens and the decision engine) shows this analysis.
        if r.video['current_version'] == r.vv['version']:
            c.execute('DELETE FROM frames WHERE video_id=?', (r.row['video_id'],))
            for f in c.execute('SELECT f.second, f.image_path, f.description, s.metadata_json FROM scene_frames f JOIN scenes s ON s.id=f.scene_id WHERE f.analysis_version_id=? AND f.representative=1 ORDER BY f.second', (r.aid,)).fetchall():
                c.execute('INSERT INTO frames(video_id,second,image_path,description,score) VALUES(?,?,?,?,?)', (r.row['video_id'], f['second'], f['image_path'], f['description'] or '', 0.5))
            c.execute('UPDATE videos SET analyzed=1 WHERE id=?', (r.row['video_id'],))
        outbox.emit(c, 'analysis.completed', 'video', r.row['video_id'], {'video_id': r.row['video_id'], 'video_version_id': r.row['video_version_id'], 'analysis_version_id': r.aid,
                                                                          'version': r.row['version'], 'scenes': scenes, 'pipeline': PIPELINE}, org_id=r.row['org_id'])
    return outputs


def run(aid, token, stop=None):
    r = _Run(aid, token, stop)
    for i, stage in enumerate(STAGES):
        if r.stages.get(stage, {}).get('status') in ('DONE', 'UNAVAILABLE', 'PARTIAL'): continue
        if stop is not None and stop.is_set(): raise OutOfTime()
        r.save(stage, i / len(STAGES))
        t = time.time()
        r.stages[stage] = dict(RUNNERS[stage](r), seconds=round(time.time() - t, 3), finished_at=time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()))
        r.save(stage, (i + 1) / len(STAGES))
    return _complete(r)


def run_next(stop=None):
    """Claim and run one queued analysis. Returns True when one was processed."""
    aid, token = _claim()
    if aid is None: return False
    obs.worker_heartbeat('analysing')
    try:
        run(aid, token, stop)
    except OutOfTime:
        with m.db() as c: c.execute("UPDATE analysis_versions SET status='QUEUED', lease_token=NULL, lease_until=NULL, attempts=attempts-1 WHERE id=? AND lease_token=?", (aid, token))
    except Cancelled as e:
        with m.db() as c:
            if str(e) == 'cancelled':
                c.execute("UPDATE analysis_versions SET status='CANCELLED', finished_at=CURRENT_TIMESTAMP, lease_until=NULL WHERE id=? AND lease_token=?", (aid, token))
    except Exception as e:
        obs.error('analysis failed', exc_info=True, analysis=aid)
        with m.db() as c:
            row = c.execute('SELECT attempts, max_attempts, stage, stages_json FROM analysis_versions WHERE id=?', (aid,)).fetchone()
            stages = json.loads(row['stages_json'] or '{}'); stages[row['stage']] = {'status': 'FAILED', 'detail': {'error': str(e)[:300]}}
            final = row['attempts'] >= row['max_attempts'] or isinstance(e, ValueError)
            c.execute('UPDATE analysis_versions SET status=?, stages_json=?, error_json=?, lease_until=NULL, lease_token=NULL, finished_at=CASE WHEN ?=1 THEN CURRENT_TIMESTAMP ELSE NULL END '
                      'WHERE id=? AND lease_token=?', ('FAILED' if final else 'QUEUED', json.dumps(stages),
                                                     json.dumps({'stage': row['stage'], 'type': type(e).__name__, 'message': str(e)[:500]}), int(final), aid, token))
            if final:
                v = c.execute('SELECT org_id, video_id, version FROM analysis_versions WHERE id=?', (aid,)).fetchone()
                outbox.emit(c, 'analysis.failed', 'video', v['video_id'], {'video_id': v['video_id'], 'analysis_version_id': aid, 'version': v['version']}, org_id=v['org_id'])
    return True


# ---------------------------------------------------------------- API
def _summary(r):
    d = {k: r[k] for k in ('id', 'video_id', 'video_version_id', 'version', 'status', 'stage', 'progress', 'pipeline', 'attempts', 'created_at', 'started_at', 'finished_at')}
    d['stages'] = json.loads(r['stages_json'] or '{}')
    if 'DETECTING_PLACEMENTS' in d['stages']:
        det = d['stages']['DETECTING_PLACEMENTS'].get('detail', {}); rejected = {}
        for x in det.get('rejected', []): rejected[x['reason']] = rejected.get(x['reason'], 0) + 1
        d['stages']['DETECTING_PLACEMENTS'] = dict(d['stages']['DETECTING_PLACEMENTS'], detail={'candidates': len(det.get('candidates', [])), 'rejected': rejected})
    for k in ('config_json', 'model_versions_json', 'outputs_json', 'error_json'):
        d[k[:-5]] = json.loads(r[k]) if r[k] else None
    return d


def _analysis(c, aid, org_id):
    r = c.execute('SELECT * FROM analysis_versions WHERE id=? AND org_id=?', (aid, org_id)).fetchone()
    if not r: raise HTTPException(404, 'Resource not found')
    return r


class QueueIn(BaseModel):
    pass


@router.post('/api/videos/{vid}/analyses')
def queue_analysis(vid: int, request: Request):
    u = m.current_user(request)
    from . import limits
    with m.db() as c:
        v = m.owned(c, 'videos', vid, u['org_id'])
        if not m._license_ok(v): raise HTTPException(409, {'code': 'LICENCE_INVALID', 'message': 'The video licence is not confirmed or has expired'})
    limits.check_rate(u['org_id'], 'analyses', limits.org_analyses_per_hour())
    with m.db() as c:
        row, created = queue(c, v, u['id'])
        if created: m.event(c, u, 'analysis.queued', 'video', vid, {'analysis_version_id': row['id']})
    from . import jobrunner
    if created: obs.wake_workers()
    if created and jobrunner.enabled(): jobrunner.kick()
    return dict(_summary(row), created=created)


@router.get('/api/videos/{vid}/analyses')
def list_analyses(vid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        m.owned(c, 'videos', vid, u['org_id'])
        rows = c.execute('SELECT * FROM analysis_versions WHERE video_id=? ORDER BY version DESC', (vid,)).fetchall()
        from . import jobrunner
        jobrunner.nudge(c, u['org_id'])
    return [_summary(r) for r in rows]


@router.get('/api/analyses/{aid}')
def get_analysis(aid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c: r = _analysis(c, aid, u['org_id'])
    return _summary(r)


@router.post('/api/analyses/{aid}/cancel')
def cancel_analysis(aid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        r = _analysis(c, aid, u['org_id'])
        if r['status'] not in ('QUEUED', 'RUNNING'): raise HTTPException(409, {'code': 'NOT_RUNNING', 'message': f'The analysis is {r["status"]}'})
        c.execute("UPDATE analysis_versions SET cancel_requested=1 WHERE id=?", (aid,))
        c.execute("UPDATE analysis_versions SET status='CANCELLED', finished_at=CURRENT_TIMESTAMP WHERE id=? AND status='QUEUED'", (aid,))
        m.event(c, u, 'analysis.cancel_requested', 'analysis', aid, {})
        return _summary(c.execute('SELECT * FROM analysis_versions WHERE id=?', (aid,)).fetchone())


def _frame_url(f): return f'/api/scene-frames/{f["id"]}/image'


def scene_view(c, s):
    frames = c.execute('SELECT id, second, description, score, representative FROM scene_frames WHERE scene_id=? ORDER BY second', (s['id'],)).fetchall()
    opps = c.execute('SELECT COUNT(*) FROM opportunities WHERE scene_id=?', (s['id'],)).fetchone()[0]
    d = {k: s[k] for k in ('id', 'video_id', 'video_version_id', 'analysis_version_id', 'scene_index', 'start_second', 'end_second', 'boundary_source', 'description')}
    d['metadata'] = json.loads(s['metadata_json'] or '{}')
    d['frames'] = [dict(f, representative=bool(f['representative']), image_url=_frame_url(f)) for f in frames]
    d['opportunities'] = opps
    return d


@router.get('/api/analyses/{aid}/scenes')
def analysis_scenes(aid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        _analysis(c, aid, u['org_id'])
        return [scene_view(c, s) for s in c.execute('SELECT * FROM scenes WHERE analysis_version_id=? ORDER BY scene_index', (aid,)).fetchall()]


@router.get('/api/analyses/{aid}/objects')
def analysis_objects(aid: int, request: Request):
    """Confirmed object tracks per scene, with their observed boxes (normalised to the frame). Gaps are times the object
    was hidden; nothing is reported for them."""
    u = m.current_user(request)
    with m.db() as c:
        _analysis(c, aid, u['org_id'])
        out = []
        for s in c.execute('SELECT id, scene_index, start_second, end_second FROM scenes WHERE analysis_version_id=? ORDER BY scene_index', (aid,)).fetchall():
            tracks = []
            for tr in c.execute('SELECT * FROM object_tracks WHERE analysis_version_id=? AND scene_id=? ORDER BY start_second, track_key', (aid, s['id'])).fetchall():
                obs = c.execute('SELECT frame_second, confidence, bbox_json FROM detected_objects WHERE track_id=? ORDER BY frame_second', (tr['id'],)).fetchall()
                step = max(1, len(obs) // 30)
                tracks.append({'track_id': tr['track_key'], 'label': tr['label'], 'start_second': tr['start_second'], 'end_second': tr['end_second'],
                               'confidence': tr['confidence'], 'observed_fraction': tr['observed_fraction'], 'gaps': json.loads(tr['gaps_json'] or '[]'),
                               'observations': [{'t': o['frame_second'], 'confidence': o['confidence'], 'bbox': json.loads(o['bbox_json'])} for o in obs[::step]]})
            out.append({'scene_id': s['id'], 'scene_index': s['scene_index'], 'start_second': s['start_second'], 'end_second': s['end_second'], 'tracks': tracks})
    return out


@router.get('/api/scenes/{sid}')
def get_scene(sid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        s = c.execute('SELECT * FROM scenes WHERE id=? AND org_id=?', (sid, u['org_id'])).fetchone()
        if not s: raise HTTPException(404, 'Resource not found')
        from . import opportunities
        d = scene_view(c, s)
        d['opportunity_list'] = [opportunities.load(o) for o in c.execute('SELECT * FROM opportunities WHERE scene_id=? ORDER BY feasibility_score DESC', (sid,)).fetchall()]
    return d


@router.get('/api/scene-frames/{fid}/image')
def scene_frame_image(fid: int, request: Request):
    u = m.current_user(request)
    with m.db() as c:
        f = c.execute('SELECT f.image_path FROM scene_frames f JOIN scenes s ON s.id=f.scene_id WHERE f.id=? AND s.org_id=?', (fid, u['org_id'])).fetchone()
    if not f: raise HTTPException(404, 'Frame not found')
    return storage.response(f['image_path'], media_type='image/jpeg')


def has_work(c, now):
    return c.execute("SELECT 1 FROM analysis_versions WHERE pipeline=? AND (status='QUEUED' OR (status='RUNNING' AND (lease_until IS NULL OR lease_until<?))) LIMIT 1",
                     (PIPELINE, now)).fetchone() is not None


__all__ = ['router', 'queue', 'run_next', 'segment', 'analysis_versions']
