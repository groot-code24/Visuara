"""Vision-model access behind one interface (M14, specification §8.6).

`describe(image_path)` returns a `VisionResult` whatever happens: AVAILABLE with a validated answer, UNAVAILABLE when no
model is configured or the server cannot be reached, or FAILED when the model answered with something that is not a valid
answer. A malformed or empty answer is never turned into a description.

Provider: an Ollama-compatible `/api/generate` endpoint (a local or separately deployed model server). The model is asked
for JSON with a fixed set of fields, and the answer is checked against that schema; unknown values are dropped.

Settings:
  CC_VISION_MODEL        the model name; unset means UNAVAILABLE (the deployment's default: no model is bundled)
  CC_OLLAMA_URL          endpoint (default http://127.0.0.1:11434/api/generate)
  CC_VISION_TIMEOUT      seconds per attempt (default 20)
  CC_VISION_RETRIES      extra attempts after a timeout or connection error (default 1), with 1 s then 2 s back-off
  CC_VISION_MAX_PIXELS   the longest image side sent to the model (default 768); larger frames are scaled down
"""
import base64, json, os, time, urllib.error, urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 'vision-answer-v1'
SCENE_CLASSES = ('kitchen', 'dining', 'living room', 'workspace', 'bedroom', 'bathroom', 'street', 'shop', 'outdoors', 'sports',
                 'studio', 'vehicle interior', 'other')
LIGHTING = ('daylight', 'indoor artificial', 'low light', 'mixed', 'unknown')
PROMPT = ('You are describing one frame of a video for a product-placement editor. Answer with JSON only, with these keys: '
          '"description" (one sentence, visible things only, no identities), '
          f'"scene_class" (one of {", ".join(SCENE_CLASSES)}), '
          '"objects" (up to 10 visible object names), '
          f'"lighting" (one of {", ".join(LIGHTING)}), '
          '"placement_areas" (up to 3 short phrases naming flat, unobstructed areas where a product could appear, or an empty list), '
          '"sensitive_content" (a list of anything a brand would avoid: weapons, alcohol, drugs, violence, nudity, gambling, tobacco; '
          'or an empty list).')


@dataclass
class VisionResult:
    status: str                       # AVAILABLE | UNAVAILABLE | FAILED
    model: str = None
    description: str = None
    scene_class: str = None
    objects: list = field(default_factory=list)
    lighting: str = None
    placement_areas: list = field(default_factory=list)
    sensitive_content: list = field(default_factory=list)
    reason: str = None
    attempts: int = 0
    seconds: float = 0.0
    schema: str = SCHEMA_VERSION

    def as_dict(self): return asdict(self)


def model():
    return (os.getenv('CC_VISION_MODEL') or '').strip() or None


def _image_b64(path, max_side):
    import cv2
    img = cv2.imread(str(path))
    if img is None: raise ValueError('the frame image could not be read')
    h, w = img.shape[:2]; s = min(1.0, max_side / max(h, w))
    if s < 1.0: img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok: raise ValueError('the frame image could not be encoded')
    return base64.b64encode(buf.tobytes()).decode()


def _strings(v, limit, allowed=None):
    if not isinstance(v, list): return []
    out = []
    for x in v:
        if isinstance(x, str) and x.strip():
            x = ' '.join(x.split())[:80].lower()
            if allowed is None or x in allowed: out.append(x)
    return out[:limit]


def parse(text, name):
    """Validate a model answer. Returns a VisionResult (AVAILABLE or FAILED). Pure: tested without a model."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return VisionResult('FAILED', model=name, reason='the model did not answer with JSON')
    if not isinstance(data, dict): return VisionResult('FAILED', model=name, reason='the model answer is not a JSON object')
    desc = data.get('description')
    if not isinstance(desc, str) or len(desc.strip()) < 3:
        return VisionResult('FAILED', model=name, reason='the model answer has no description')
    sc = data.get('scene_class'); sc = sc.strip().lower() if isinstance(sc, str) else None
    li = data.get('lighting'); li = li.strip().lower() if isinstance(li, str) else None
    return VisionResult('AVAILABLE', model=name, description=' '.join(desc.split())[:500],
                        scene_class=sc if sc in SCENE_CLASSES else None, lighting=li if li in LIGHTING else None,
                        objects=_strings(data.get('objects'), 10), placement_areas=_strings(data.get('placement_areas'), 3),
                        sensitive_content=_strings(data.get('sensitive_content'), 10))


def describe(image_path, opener=None):
    """Ask the configured model about one frame. `opener` replaces urllib's for tests."""
    name = model()
    if not name: return VisionResult('UNAVAILABLE', reason='no vision model is configured (CC_VISION_MODEL)')
    url = os.getenv('CC_OLLAMA_URL', 'http://127.0.0.1:11434/api/generate')
    timeout = float(os.getenv('CC_VISION_TIMEOUT') or 20); retries = int(os.getenv('CC_VISION_RETRIES') or 1)
    try:
        img = _image_b64(Path(image_path), int(os.getenv('CC_VISION_MAX_PIXELS') or 768))
    except ValueError as e:
        return VisionResult('FAILED', model=name, reason=str(e))
    body = json.dumps({'model': name, 'prompt': PROMPT, 'images': [img], 'stream': False, 'format': 'json',
                       'options': {'temperature': 0}}).encode()
    open_ = opener or urllib.request.urlopen
    started = time.time(); last = None
    for attempt in range(1, retries + 2):
        req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with open_(req, timeout=timeout) as r:
                raw = r.read(1 << 20)                                      # an answer is a few hundred bytes; cap it at 1 MB
            outer = json.loads(raw.decode('utf-8', 'replace'))
            res = parse(outer.get('response', '') if isinstance(outer, dict) else '', name)
            res.attempts, res.seconds = attempt, round(time.time() - started, 3)
            return res
        except urllib.error.HTTPError as e:
            # the server answered: a missing model or a bad request will not improve on retry
            return VisionResult('UNAVAILABLE', model=name, reason=f'the vision server refused the request (HTTP {e.code})',
                                attempts=attempt, seconds=round(time.time() - started, 3))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
            if attempt <= retries: time.sleep(attempt)
        except ValueError:
            return VisionResult('FAILED', model=name, reason='the vision server answer is not JSON', attempts=attempt, seconds=round(time.time() - started, 3))
    kind = 'timed out' if isinstance(last, TimeoutError) or 'timed out' in str(last) else 'could not be reached'
    return VisionResult('UNAVAILABLE', model=name, reason=f'the vision server {kind}', attempts=retries + 1, seconds=round(time.time() - started, 3))
