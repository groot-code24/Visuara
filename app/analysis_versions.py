"""Analysis versions, scenes and scene frames (AD-4): every analysis adds a version and nothing is overwritten.

Phase 1 records what the existing keyframe analysis produces. Its scene boundaries are the keyframes themselves
(the strongest histogram changes, or fixed points of the video when OpenCV finds none), marked
`boundary_source='LEGACY_KEYFRAME'`, and scene i runs from keyframe i to keyframe i+1. Phase 4 replaces this with
the staged pipeline and real segmentation; the tables stay the same.
"""
import json

from . import outbox

PIPELINE = 'legacy-keyframes-v1'


def _scenes(frames, duration):
    frames = sorted({round(float(f[0]), 3): f for f in frames}.values(), key=lambda f: f[0])
    out = []
    for i, f in enumerate(frames):
        end = frames[i + 1][0] if i + 1 < len(frames) else max(float(duration), float(f[0]) + 0.01)
        out.append((i, float(f[0]), float(end), f))
    return out


def _next_version(c, video_id):
    c.lock(f'analysis-version-{video_id}')
    return (c.execute('SELECT MAX(version) FROM analysis_versions WHERE video_id=?', (video_id,)).fetchone()[0] or 0) + 1


def _video_version_id(c, video_id):
    r = c.execute('SELECT id FROM video_versions WHERE video_id=? ORDER BY version DESC LIMIT 1', (video_id,)).fetchone()
    if not r: raise RuntimeError(f'Video {video_id} has no recorded version')
    return r[0]


def record_completed(c, video, frames, user_id, config=None, started_at=None, model_versions=None):
    """Record a completed analysis of `video` (a row with id, org_id, duration) from keyframes [(second, path, description, score)]."""
    vvid = _video_version_id(c, video['id']); version = _next_version(c, video['id'])
    aid = c.execute("INSERT INTO analysis_versions(org_id,video_id,video_version_id,version,status,pipeline,model_versions_json,config_json,outputs_json,created_by,started_at,finished_at) "
                    "VALUES(?,?,?,?,'COMPLETED',?,?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP),CURRENT_TIMESTAMP)",
                    (video['org_id'], video['id'], vvid, version, PIPELINE, json.dumps(model_versions or {}), json.dumps(config or {}),
                     json.dumps({'keyframes': len(frames)}), user_id, started_at)).lastrowid
    scene_ids = []
    for i, start, end, (sec, path, desc, score) in _scenes(frames, video['duration']):
        sid = c.execute("INSERT INTO scenes(org_id,video_id,video_version_id,analysis_version_id,scene_index,start_second,end_second,boundary_source,description) "
                        "VALUES(?,?,?,?,?,?,?,'LEGACY_KEYFRAME',?)", (video['org_id'], video['id'], vvid, aid, i, start, end, desc or '')).lastrowid
        c.execute('INSERT INTO scene_frames(scene_id,analysis_version_id,second,image_path,description,score,representative) VALUES(?,?,?,?,?,?,1)',
                  (sid, aid, float(sec), str(path), desc or '', float(score)))
        scene_ids.append(sid)
    outbox.emit(c, 'analysis.completed', 'video', video['id'], {'video_id': video['id'], 'video_version_id': vvid, 'analysis_version_id': aid,
                                                                'version': version, 'scenes': len(scene_ids), 'pipeline': PIPELINE}, org_id=video['org_id'])
    return aid


def record_failed(c, video, user_id, error, config=None, started_at=None):
    vvid = _video_version_id(c, video['id']); version = _next_version(c, video['id'])
    aid = c.execute("INSERT INTO analysis_versions(org_id,video_id,video_version_id,version,status,pipeline,config_json,error_json,created_by,started_at,finished_at) "
                    "VALUES(?,?,?,?,'FAILED',?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP),CURRENT_TIMESTAMP)",
                    (video['org_id'], video['id'], vvid, version, PIPELINE, json.dumps(config or {}),
                     json.dumps({'type': type(error).__name__, 'message': str(error)[:500]}), user_id, started_at)).lastrowid
    outbox.emit(c, 'analysis.failed', 'video', video['id'], {'video_id': video['id'], 'analysis_version_id': aid, 'version': version}, org_id=video['org_id'])
    return aid


def latest_completed(c, video_id):
    return c.execute("SELECT * FROM analysis_versions WHERE video_id=? AND status='COMPLETED' ORDER BY version DESC LIMIT 1", (video_id,)).fetchone()
