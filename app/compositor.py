"""Deterministic compositor shared by every non-generative renderer tier.

A render is fully described by a *plan*: an object with ``indices`` (frames it changes),
``apply(idx, frame)`` (returns the composited frame) and ``mask(idx, shape)`` (product region).
The same plan drives the preview, the final render and the output validator, so validation checks
that the encoded file contains exactly the intended composite on every frame.
"""
import io, os, subprocess, tempfile
import cv2
import numpy as np

from .media import probe_info, MediaError


class RenderCancelled(RuntimeError):
    pass


# ---------------------------------------------------------------- colour helpers
_LUT = np.array([((i / 255) / 12.92) if i / 255 <= .04045 else (((i / 255) + .055) / 1.055) ** 2.4 for i in range(256)], np.float32)


def srgb_to_linear(img_u8):
    return _LUT[img_u8]


def linear_to_srgb(lin):
    lin = np.clip(lin, 0, 1)
    return np.where(lin <= .0031308, lin * 12.92, 1.055 * np.power(lin, 1 / 2.4) - .055) * 255


# ---------------------------------------------------------------- product assets
MAX_ASSET_PIXELS = 40_000_000


def prepare_asset(src_path, out_path):
    """Create a derived sRGB RGBA PNG (orientation applied, ICC converted). The original is never modified.

    Returns an asset report with measurements and warnings for the reviewer.
    """
    from PIL import Image, ImageOps, ImageCms
    Image.MAX_IMAGE_PIXELS = MAX_ASSET_PIXELS
    report = {'warnings': []}
    with Image.open(src_path) as im:
        report['format'] = im.format
        if im.width * im.height > MAX_ASSET_PIXELS or max(im.size) > 8000:
            raise ValueError('Image dimensions exceed safety limits')
        exif_orientation = (im.getexif() or {}).get(274, 1)
        im = ImageOps.exif_transpose(im)
        report['orientation_corrected'] = exif_orientation not in (None, 1)
        icc = im.info.get('icc_profile')
        report['icc_converted'] = False
        if icc:
            try:
                src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                mode = 'RGBA' if 'A' in im.getbands() else 'RGB'
                im = ImageCms.profileToProfile(im.convert(mode), src, ImageCms.createProfile('sRGB'), outputMode=mode)
                report['icc_converted'] = True
            except Exception as e:  # unusable profile: keep pixels, warn
                report['warnings'].append({'code': 'ICC_PROFILE_IGNORED', 'message': f'Embedded colour profile could not be applied ({str(e)[:80]}); pixels treated as sRGB.'})
        rgba = np.asarray(im.convert('RGBA')).copy()
    h, w = rgba.shape[:2]
    a = rgba[:, :, 3]
    report.update(width=w, height=h, aspect_ratio=round(w / h, 4))
    report['has_transparency'] = bool((a < 250).any())
    report['transparent_fraction'] = round(float((a < 8).mean()), 4)
    if min(w, h) < 64:
        report['warnings'].append({'code': 'LOW_RESOLUTION', 'message': f'Asset is only {w}x{h}px; it will look soft when enlarged.'})
    if not report['has_transparency']:
        report['warnings'].append({'code': 'NO_TRANSPARENCY', 'message': 'The asset has no transparent background, so the whole rectangle will be composited. Upload a cut-out PNG/WebP for objects.'})
    # Halo check: colour of semi-transparent edge pixels vs the opaque pixels just inside them.
    edge = (a > 8) & (a < 247)
    if edge.sum() >= 20 and (a >= 247).any():
        inner = cv2.erode((a >= 247).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        ring = cv2.dilate(edge.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        ref = inner & ring
        if ref.sum() >= 10:
            lum = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2GRAY).astype(np.float32)
            halo = float(abs(lum[edge].mean() - lum[ref].mean()))
            report['edge_halo_score'] = round(halo, 2)
            if halo > 60:
                report['warnings'].append({'code': 'EDGE_HALO', 'message': f'Edge pixels differ strongly from the product interior (score {halo:.0f}); the cut-out may carry a background fringe.'})
    cv2.imwrite(str(out_path), cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    return report


def load_product(path):
    prod = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if prod is None:
        raise MediaError('Could not read product image')
    if prod.ndim == 2:
        prod = cv2.cvtColor(prod, cv2.COLOR_GRAY2BGRA)
    elif prod.shape[2] == 3:
        prod = cv2.cvtColor(prod, cv2.COLOR_BGR2BGRA)
    return prod


def premultiply(prod_bgra):
    """Product as linear-light premultiplied float32 BGRA (0..1)."""
    a = prod_bgra[:, :, 3:4].astype(np.float32) / 255.0
    return np.concatenate([srgb_to_linear(prod_bgra[:, :, :3]) * a, a], axis=2)


# ---------------------------------------------------------------- geometry
def product_quad_in_surface(surface_quad, pw, ph):
    """Aspect-preserving, centred fit of a pw x ph product inside a TL,TR,BR,BL surface quad."""
    q = np.asarray(surface_quad, dtype=np.float64)
    qw = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    qh = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    if qw <= 0 or qh <= 0:
        raise ValueError('Surface quad is degenerate')
    ar = pw / ph
    fw, fh = (1.0, (qw / ar) / qh) if ar >= qw / qh else ((qh * ar) / qw, 1.0)
    u0, v0 = (1 - fw) / 2, (1 - fh) / 2
    unit = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
    to_quad = cv2.getPerspectiveTransform(unit, q.astype(np.float32))
    sub = np.array([[u0, v0], [u0 + fw, v0], [u0 + fw, v0 + fh], [u0, v0 + fh]], dtype=np.float32)
    return cv2.perspectiveTransform(sub.reshape(-1, 1, 2), to_quad).reshape(4, 2)


def overlay_quad(x, y, width, frame_w, frame_h, pw, ph):
    """Screen-space rectangle for the Tier A static overlay (normalised x, y, width)."""
    ow = max(8.0, frame_w * width)
    oh = ow * ph / pw
    x0 = min(max(0.0, frame_w * x), frame_w - ow)
    y0 = min(max(0.0, frame_h * y), frame_h - oh)
    return np.array([[x0, y0], [x0 + ow, y0], [x0 + ow, y0 + oh], [x0, y0 + oh]], dtype=np.float32)


def product_mask(shape, quad):
    mask = np.zeros(shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(mask, np.round(np.asarray(quad)).astype(np.int32), 255)
    return mask


def _prefilter(premul, quad):
    """Area-downscale the premultiplied product towards its on-screen size before the perspective warp (anti-aliasing)."""
    q = np.asarray(quad, dtype=np.float64)
    target = max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3]), np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1]))
    ph, pw = premul.shape[:2]
    scale = target * 1.5 / max(pw, ph)
    if scale >= 1 or scale <= 0:
        return premul
    return cv2.resize(premul, (max(2, round(pw * scale)), max(2, round(ph * scale))), interpolation=cv2.INTER_AREA)


def composite_premultiplied(frame, premul, quad, gain=None):
    """Composite a linear premultiplied BGRA product into quad. gain: per-channel (b,g,r) multiplier in linear light."""
    h, w = frame.shape[:2]
    quad = np.asarray(quad, dtype=np.float32)
    x0, y0 = np.floor(quad.min(axis=0)).astype(int)
    x1, y1 = np.ceil(quad.max(axis=0)).astype(int)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1 + 1), min(h, y1 + 1)
    if x1 <= x0 or y1 <= y0:
        return frame
    src_img = _prefilter(premul, quad)
    ph, pw = src_img.shape[:2]
    src = np.array([[0, 0], [pw, 0], [pw, ph], [0, ph]], dtype=np.float32)
    P = cv2.getPerspectiveTransform(src, quad - np.array([x0, y0], dtype=np.float32))
    warped = cv2.warpPerspective(src_img, P, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    alpha = warped[:, :, 3:4]
    color = warped[:, :, :3] if gain is None else warped[:, :, :3] * np.asarray(gain, np.float32).reshape(1, 1, 3)
    out = frame.copy()
    bg = srgb_to_linear(out[y0:y1, x0:x1])
    out[y0:y1, x0:x1] = linear_to_srgb(bg * (1 - alpha) + color).round().clip(0, 255).astype(np.uint8)
    return out


def composite_frame(frame, prod, quad, gain=None):
    """Composite a BGRA uint8 product (convenience wrapper; premultiplies per call)."""
    return composite_premultiplied(frame, premultiply(prod), quad, gain)


# ---------------------------------------------------------------- plans
class Planar2DPlan:
    """Product decal on a per-frame quad (Tier A overlay or Tier B tracked planar)."""
    def __init__(self, product_path, frame_quads, gains=None, adjust=None):
        self.prod = load_product(product_path)
        base = premultiply(self.prod)
        adj = np.ones(3, np.float32)
        if adjust:
            adj *= 2.0 ** float(adjust.get('exposure_ev', 0))
            t = float(adjust.get('temperature', 0))     # -1 cool .. +1 warm, applied as +-20% max
            adj *= np.array([1 - .2 * t, 1.0, 1 + .2 * t], np.float32)
        base[:, :, :3] *= adj
        self.premul = base
        self.quads = {int(k): v for k, v in frame_quads.items()}
        self.gains = {int(k): v for k, v in (gains or {}).items()}
        self.indices = set(self.quads)

    def apply(self, idx, frame):
        q = self.quads.get(idx)
        return frame if q is None else composite_premultiplied(frame, self.premul, q, self.gains.get(idx))

    def mask(self, idx, shape):
        q = self.quads.get(idx)
        return None if q is None else product_mask(shape, q)


def temporal_gains(track, start_idx, end_idx, clamp=(0.75, 1.33), deadband=0.02):
    """Per-channel linear-light gain of the tracked surface relative to the reference frame, smoothed over time.

    Measured from the footage itself; returns ({idx: [b,g,r]}, summary). Frames without colour data get no gain.
    """
    ref = next((f for f in track['frames'] if f['idx'] == track['ref_idx']), None)
    if not ref or not ref.get('chroma'):
        return {}, {'applied': False, 'reason': 'analysis has no colour measurements (re-run the quality check)'}
    base = np.asarray(ref['chroma'], np.float64)
    raw = {f['idx']: np.asarray(f['chroma']) / np.maximum(base, 1e-4) for f in track['frames'] if f.get('chroma') and start_idx <= f['idx'] <= end_idx}
    if not raw:
        return {}, {'applied': False, 'reason': 'no measured frames'}
    idx = sorted(raw); arr = np.stack([raw[i] for i in idx])
    k = 2  # centred median over 5 frames, then mean over 3: removes flicker without lagging real changes
    med = np.stack([np.median(arr[max(0, i - k):i + k + 1], axis=0) for i in range(len(arr))])
    sm = np.stack([med[max(0, i - 1):i + 2].mean(axis=0) for i in range(len(med))])
    sm = np.clip(sm, *clamp)
    if float(np.abs(sm - 1).max()) < deadband:
        # No lighting change beyond measurement noise: applying gains would only add noise (dev split, seeds 0-1).
        return {}, {'applied': False, 'reason': f'surface colour stable within +-{deadband:.0%}', 'max_deviation': round(float(np.abs(sm - 1).max()), 4)}
    flicker = float(np.abs(np.diff(sm, axis=0)).max()) if len(sm) > 1 else 0.0
    return ({i: sm[j].round(5).tolist() for j, i in enumerate(idx)},
            {'applied': True, 'method': 'surface linear-RGB ratio vs reference, median5+mean3, clamped',
             'min': sm.min(axis=0).round(4).tolist(), 'max': sm.max(axis=0).round(4).tolist(), 'max_frame_to_frame_change': round(flicker, 5)})


# ---------------------------------------------------------------- encode
def render_plan(source, plan, output, progress=None, crf=18):
    """Decode source sequentially, apply the plan, encode once with FFmpeg (audio re-encoded to AAC)."""
    info = probe_info(source)
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise MediaError('Could not open source video')
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    log = tempfile.TemporaryFile()
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{w}x{h}',
           '-r', info['fps_str'], '-i', '-', '-i', str(source), '-map', '0:v:0', '-map', '1:a?',
           '-c:v', 'libx264', '-preset', os.getenv('CC_RENDER_PRESET', 'veryfast'), '-crf', str(crf), '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(output)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
    idx = composited = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx in plan.indices:
                frame = plan.apply(idx, frame); composited += 1
            proc.stdin.write(frame.tobytes())
            idx += 1
            if progress and idx % 15 == 0:
                progress(idx)
        proc.stdin.close()
        code = proc.wait(timeout=600)
    except BaseException:
        proc.kill(); proc.wait()
        try: os.remove(output)
        except OSError: pass
        raise
    finally:
        cap.release()
    log.seek(0); err = log.read().decode('utf-8', 'replace')[-600:]; log.close()
    if code:
        raise MediaError('FFmpeg encoding failed: ' + err)
    if idx < 2:
        raise MediaError('Source produced too few frames')
    return {'frames_written': idx, 'frames_composited': composited, 'width': w, 'height': h, 'fps': info['fps']}


def render_video(source, product_path, output, frame_quads, progress=None, crf=18):
    """Backward-compatible wrapper: plain planar decal without colour matching."""
    return render_plan(source, Planar2DPlan(product_path, frame_quads), output, progress, crf)


def render_frame(source, plan, idx):
    """Single composited frame for previews: same plan and compositor as the final render."""
    cap = cv2.VideoCapture(str(source)); frame = None; i = 0
    while i <= idx:
        ok = cap.grab()
        if ok and i == idx: ok, frame = cap.retrieve()
        if not ok: break
        i += 1
    cap.release()
    if frame is None:
        raise MediaError('Frame is outside the video')
    return frame, (plan.apply(idx, frame) if idx in plan.indices else frame)
