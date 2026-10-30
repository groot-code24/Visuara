"""Media probing and frame-interval helpers shared by analysis, rendering and validation."""
import json, math, shutil, subprocess
from pathlib import Path


class MediaError(RuntimeError):
    pass


def probe_info(path):
    """Return stream facts from FFprobe. Raises MediaError on unreadable media."""
    if not shutil.which('ffprobe'):
        raise MediaError('FFprobe is required')
    p = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                        'format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate,nb_frames',
                        '-of', 'json', str(path)], capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise MediaError('Unable to decode media: ' + (p.stderr or '')[-300:])
    info = json.loads(p.stdout or '{}')
    streams = info.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    if not video:
        raise MediaError('No video stream found')
    rate = video.get('avg_frame_rate') or video.get('r_frame_rate') or '0/1'
    num, _, den = rate.partition('/')
    fps = float(num) / max(float(den or 1), 1e-9)
    if not math.isfinite(fps) or fps <= 0:
        raise MediaError('Video frame rate is unknown')
    return {
        'duration': float(info.get('format', {}).get('duration') or 0),
        'width': int(video['width']), 'height': int(video['height']),
        'fps': fps, 'fps_str': rate if '/' in rate else f'{fps}/1',
        'nb_frames': int(video['nb_frames']) if str(video.get('nb_frames', '')).isdigit() else None,
        'has_audio': any(s.get('codec_type') == 'audio' for s in streams),
    }


def interval_frames(fps, frame_count, start_second, end_second):
    """Inclusive frame-index range whose timestamps fall inside [start, end]. Shared by every stage."""
    first = max(0, math.ceil(start_second * fps - 1e-6))
    last = min(frame_count - 1, math.floor(end_second * fps + 1e-6))
    return first, last


def frame_ranges(indices, fps):
    """Compress frame indices into [[start_s, end_s], ...] for human-readable evidence."""
    out = []
    for i in sorted(indices):
        if out and i == out[-1][1] + 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return [[round(a / fps, 3), round(b / fps, 3)] for a, b in out]


def ensure_parent(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
