"""Object detection (M14, specification §8.3): a real, evaluated CPU detector behind a small interface.

Models are ONNX files run by OpenCV's own DNN module, so no new runtime dependency is needed. Each model in `MODELS` was
benchmarked before one was chosen (docs/engineering/AI_EVALUATION.md, `benchmark/evaluate_detection.py`); only models whose
code and weights are licensed for commercial use are listed. The file is verified against its SHA-256 before it is loaded,
and a missing or altered file makes detection UNAVAILABLE with the reason; nothing is guessed in its place.

Settings:
  CC_DETECTION_MODEL           a key of MODELS, or `none` to switch detection off (default: DEFAULT_MODEL)
  CC_MODELS_DIR                where the ONNX files are (default: <repo>/models)
  CC_DETECTION_MIN_CONFIDENCE  detections below this are dropped (default 0.35)
  CC_DETECTION_THREADS         OpenCV threads during inference only (default 1: the deployment has one vCPU)
"""
import hashlib, os, threading
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
COCO_CLASSES = (
    'person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus', 'train', 'truck', 'boat', 'traffic light', 'fire hydrant',
    'stop sign', 'parking meter', 'bench', 'bird', 'cat', 'dog', 'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe',
    'backpack', 'umbrella', 'handbag', 'tie', 'suitcase', 'frisbee', 'skis', 'snowboard', 'sports ball', 'kite', 'baseball bat',
    'baseball glove', 'skateboard', 'surfboard', 'tennis racket', 'bottle', 'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl',
    'banana', 'apple', 'sandwich', 'orange', 'broccoli', 'carrot', 'hot dog', 'pizza', 'donut', 'cake', 'chair', 'couch',
    'potted plant', 'bed', 'dining table', 'toilet', 'tv', 'laptop', 'mouse', 'remote', 'keyboard', 'cell phone', 'microwave',
    'oven', 'toaster', 'sink', 'refrigerator', 'book', 'clock', 'vase', 'scissors', 'teddy bear', 'hair drier', 'toothbrush')

_YOLOX = 'https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/'
_NANODET = 'https://github.com/RangiLyu/nanodet/releases/download/v1.0.0-alpha-1/'
# Published COCO val2017 AP (0.5:0.95) is the authors' figure; ours, measured on a fixed subset, is in AI_EVALUATION.md.
MODELS = {
    'yolox-nano': {'file': 'yolox_nano.onnx', 'sha256': 'c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d',
                   'url': _YOLOX + 'yolox_nano.onnx', 'family': 'yolox', 'input': 416, 'licence': 'Apache-2.0',
                   'source': 'Megvii-BaseDetection/YOLOX release 0.1.1rc0', 'published_ap': 25.8},
    'yolox-tiny': {'file': 'yolox_tiny.onnx', 'sha256': '427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7',
                   'url': _YOLOX + 'yolox_tiny.onnx', 'family': 'yolox', 'input': 416, 'licence': 'Apache-2.0',
                   'source': 'Megvii-BaseDetection/YOLOX release 0.1.1rc0', 'published_ap': 32.8},
    'yolox-s': {'file': 'yolox_s.onnx', 'sha256': 'c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063',
                'url': _YOLOX + 'yolox_s.onnx', 'family': 'yolox', 'input': 640, 'licence': 'Apache-2.0',
                'source': 'Megvii-BaseDetection/YOLOX release 0.1.1rc0', 'published_ap': 40.5},
    'nanodet-plus-m-416': {'file': 'nanodet-plus-m_416.onnx', 'sha256': '59d2f166088889c902f523bf08079391993491324f0d84847e3c4016a8f7cc3d',
                           'url': _NANODET + 'nanodet-plus-m_416.onnx', 'family': 'nanodet', 'input': 416, 'licence': 'Apache-2.0',
                           'source': 'RangiLyu/nanodet release v1.0.0-alpha-1', 'published_ap': 30.4},
    'nanodet-plus-m-1.5x-416': {'file': 'nanodet-plus-m-1.5x_416.onnx', 'sha256': 'be3a96588c5a551ebd2133a915acff67034d6c2a03f8fb5f8042ad0c414eff34',
                                'url': _NANODET + 'nanodet-plus-m-1.5x_416.onnx', 'family': 'nanodet', 'input': 416, 'licence': 'Apache-2.0',
                                'source': 'RangiLyu/nanodet release v1.0.0-alpha-1', 'published_ap': 34.1},
}
DEFAULT_MODEL = 'yolox-tiny'   # chosen by benchmark: docs/engineering/AI_EVALUATION.md §2


class Unavailable(Exception):
    """Detection cannot run; the message says why (shown to the user as the stage's reason)."""


def model_name():
    return (os.getenv('CC_DETECTION_MODEL') or DEFAULT_MODEL).strip()


def models_dir():
    return Path(os.getenv('CC_MODELS_DIR') or ROOT / 'models')


def min_confidence():
    return float(os.getenv('CC_DETECTION_MIN_CONFIDENCE') or 0.35)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''): h.update(block)
    return h.hexdigest()


def _nms(boxes, scores, classes, iou):
    """Class-aware non-maximum suppression. boxes: (N, 4) x0,y0,x1,y1."""
    import cv2
    keep = []
    for k in np.unique(classes):
        idx = np.where(classes == k)[0]
        b = boxes[idx]
        rects = np.stack([b[:, 0], b[:, 1], b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]], axis=1).tolist()
        sel = cv2.dnn.NMSBoxes(rects, scores[idx].tolist(), 0.0, iou)
        keep.extend(idx[np.asarray(sel, dtype=int).ravel()].tolist())
    return sorted(keep, key=lambda i: -scores[i])


class Detector:
    """One loaded model. `detect` is thread-safe (OpenCV nets are not, so calls are serialised)."""

    def __init__(self, name, spec, path):
        import cv2
        self.name, self.spec, self.path = name, spec, Path(path)
        self.version = f"{name}@{spec['sha256'][:12]}"
        self.size = int(spec['input'])
        self.net = cv2.dnn.readNetFromONNX(str(path))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV); self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self._lock = threading.Lock()
        self._grids = None
        self.threads = int(os.getenv('CC_DETECTION_THREADS') or 1)

    def describe(self):
        return {'model': self.name, 'version': self.version, 'licence': self.spec['licence'], 'source': self.spec['source'],
                'input': self.size, 'classes': 'COCO-80'}

    # ------------------------------------------------------------ pre- and post-processing per family
    def _yolox_input(self, img):
        s = self.size; h, w = img.shape[:2]; r = min(s / h, s / w)
        import cv2
        canvas = np.full((s, s, 3), 114, np.uint8)
        canvas[:int(round(h * r)), :int(round(w * r))] = cv2.resize(img, (int(round(w * r)), int(round(h * r))), interpolation=cv2.INTER_LINEAR)
        return canvas.transpose(2, 0, 1)[None].astype(np.float32), (r, r)

    def _yolox_decode(self, out):
        if self._grids is None:
            grids, strides = [], []
            for st in (8, 16, 32):
                n = self.size // st
                yv, xv = np.meshgrid(np.arange(n), np.arange(n), indexing='ij')
                grids.append(np.stack((xv, yv), 2).reshape(-1, 2)); strides.append(np.full((n * n, 1), st))
            self._grids = (np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32))
        g, st = self._grids
        p = out.reshape(-1, out.shape[-1])
        xy = (p[:, :2] + g) * st; wh = np.exp(np.clip(p[:, 2:4], -10, 10)) * st
        boxes = np.concatenate([xy - wh / 2, xy + wh / 2], axis=1)
        scores = p[:, 4:5] * p[:, 5:]
        return boxes, scores

    def _nanodet_input(self, img):
        import cv2
        s = self.size; h, w = img.shape[:2]
        x = cv2.resize(img, (s, s), interpolation=cv2.INTER_LINEAR).astype(np.float32)
        x = (x - np.array([103.53, 116.28, 123.675], np.float32)) / np.array([57.375, 57.12, 58.395], np.float32)
        return x.transpose(2, 0, 1)[None], (s / w, s / h)

    def _nanodet_decode(self, out):
        if self._grids is None:
            centres, strides = [], []
            for st in (8, 16, 32, 64):
                n = int(np.ceil(self.size / st))
                yv, xv = np.meshgrid(np.arange(n), np.arange(n), indexing='ij')
                centres.append(np.stack((xv, yv), 2).reshape(-1, 2) * st); strides.append(np.full((n * n, 1), st))
            self._grids = (np.concatenate(centres).astype(np.float32), np.concatenate(strides).astype(np.float32))
        c, st = self._grids
        p = out.reshape(-1, out.shape[-1])
        scores = p[:, :80]
        reg = p[:, 80:].reshape(-1, 4, 8)
        reg = np.exp(reg - reg.max(axis=2, keepdims=True)); reg /= reg.sum(axis=2, keepdims=True)
        dist = (reg * np.arange(8, dtype=np.float32)).sum(axis=2) * st
        boxes = np.concatenate([c - dist[:, :2], c + dist[:, 2:]], axis=1)
        return boxes, scores

    def detect(self, img, min_conf=None, nms_iou=0.45, max_out=100):
        """Detections in a BGR image: [{label, class_id, confidence, bbox: [x0, y0, x1, y1] in pixels}], best first."""
        min_conf = min_confidence() if min_conf is None else min_conf
        fam = self.spec['family']
        blob, (rx, ry) = (self._yolox_input if fam == 'yolox' else self._nanodet_input)(img)
        import cv2
        with self._lock:
            # OpenCV's thread count is process-wide: limit it for inference only, so the rest of the process keeps its own
            prev = cv2.getNumThreads(); cv2.setNumThreads(self.threads)
            try:
                self.net.setInput(blob); out = self.net.forward()
            finally:
                cv2.setNumThreads(prev)
        boxes, scores = (self._yolox_decode if fam == 'yolox' else self._nanodet_decode)(out)
        cls = scores.argmax(axis=1); conf = scores[np.arange(len(cls)), cls]
        m = conf >= min_conf
        if not m.any(): return []
        boxes, conf, cls = boxes[m], conf[m], cls[m]
        h, w = img.shape[:2]
        boxes = boxes / np.array([rx, ry, rx, ry], np.float32)
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, w); boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, h)
        ok = (boxes[:, 2] - boxes[:, 0] >= 2) & (boxes[:, 3] - boxes[:, 1] >= 2)
        boxes, conf, cls = boxes[ok], conf[ok], cls[ok]
        keep = _nms(boxes, conf, cls, nms_iou)[:max_out]
        return [{'label': COCO_CLASSES[int(cls[i])], 'class_id': int(cls[i]), 'confidence': round(float(conf[i]), 4),
                 'bbox': [round(float(v), 1) for v in boxes[i]]} for i in keep]


_loaded = {}
_load_lock = threading.Lock()


def load(name=None):
    """The detector for `name` (default: the configured one). Raises Unavailable with the reason when it cannot run."""
    name = name or model_name()
    if name.lower() in ('none', 'off', ''): raise Unavailable('object detection is switched off (CC_DETECTION_MODEL=none)')
    spec = MODELS.get(name)
    if not spec: raise Unavailable(f'unknown detection model {name!r}; known: {", ".join(sorted(MODELS))}')
    path = models_dir() / spec['file']
    with _load_lock:
        if name in _loaded and _loaded[name].path == path: return _loaded[name]
        if not path.exists(): raise Unavailable(f'the {name} model file is not installed ({spec["file"]}; run `python ops/fetch_models.py {name}`)')
        if sha256(path) != spec['sha256']: raise Unavailable(f'the {name} model file does not match its published checksum and was not loaded')
        try:
            det = Detector(name, spec, path)
        except Exception as e:  # an OpenCV build that cannot run the graph
            raise Unavailable(f'the {name} model could not be loaded by OpenCV: {str(e)[:160]}')
        _loaded[name] = det
        return det


def installed():
    """For the health check: which model is configured and whether its file is present. Cheap: nothing is hashed or loaded."""
    name = model_name()
    if name.lower() in ('none', 'off', ''): return {'model': None, 'installed': False, 'switched_off': True}
    spec = MODELS.get(name)
    return {'model': name, 'installed': bool(spec) and (models_dir() / spec['file']).exists()}


def status():
    """For health and the analysis record: which model would run, or why none would."""
    try:
        return dict(load().describe(), status='AVAILABLE')
    except Unavailable as e:
        return {'status': 'UNAVAILABLE', 'model': model_name(), 'reason': str(e)}
