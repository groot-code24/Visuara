# Object-detection models

The weights are not kept in git. `python ops/fetch_models.py` downloads the configured model (`CC_DETECTION_MODEL`, default
`yolox-tiny`) into this folder and verifies it against the SHA-256 pinned in `app/detection.py` before it is used. The
production image does this at build time (`Dockerfile.vercel`). A file that is missing or does not match its checksum makes
detection `UNAVAILABLE`; nothing is detected in its place.

| Model | File | Source | Licence |
|---|---|---|---|
| `yolox-tiny` (default) | `yolox_tiny.onnx` | [Megvii-BaseDetection/YOLOX](https://github.com/Megvii-BaseDetection/YOLOX) release 0.1.1rc0 | Apache-2.0, [licenses/YOLOX-LICENSE.txt](licenses/YOLOX-LICENSE.txt) |
| `yolox-nano`, `yolox-s` | `yolox_nano.onnx`, `yolox_s.onnx` | the same release | Apache-2.0 |
| `nanodet-plus-m-416`, `nanodet-plus-m-1.5x-416` | `nanodet-plus-m_416.onnx`, `nanodet-plus-m-1.5x_416.onnx` | [RangiLyu/nanodet](https://github.com/RangiLyu/nanodet) release v1.0.0-alpha-1 | Apache-2.0, [licenses/NanoDet-LICENSE.txt](licenses/NanoDet-LICENSE.txt) |

Only models whose code and weights are released for commercial use are listed. Ultralytics YOLOv5, v8 and v11 (AGPL-3.0),
YOLOv6, v7 and v9 (GPL-3.0) and YOLO-NAS (weights under a non-commercial licence) were excluded for that reason.

All five were trained on COCO 2017 and detect its 80 classes. Why `yolox-tiny` was chosen, and what was measured, is in
[docs/engineering/AI_EVALUATION.md](../docs/engineering/AI_EVALUATION.md) §2.
