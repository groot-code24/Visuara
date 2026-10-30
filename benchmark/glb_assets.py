"""Write small, self-contained glTF 2.0 binary (.glb) test products: a labelled bottle and a box.

These are original, procedurally generated assets (no third-party content).
"""
import json, struct
from pathlib import Path
import cv2
import numpy as np


def _pack(parts):
    """parts: list of (array, target) -> (bin bytes, bufferViews, accessors)."""
    blob, views, accs = b'', [], []
    for arr, kind in parts:
        arr = np.ascontiguousarray(arr)
        pad = (-len(blob)) % 4; blob += b'\0' * pad
        views.append({'buffer': 0, 'byteOffset': len(blob), 'byteLength': arr.nbytes, **({'target': 34963} if kind == 'idx' else {'target': 34962} if kind else {})})
        if kind is not None:
            comp = 5125 if kind == 'idx' else 5126
            typ = {'idx': 'SCALAR', 'v3': 'VEC3', 'v2': 'VEC2'}[kind]
            acc = {'bufferView': len(views) - 1, 'componentType': comp, 'count': int(arr.shape[0] if kind != 'idx' else arr.size), 'type': typ}
            if kind == 'v3':
                acc['min'] = arr.min(axis=0).tolist(); acc['max'] = arr.max(axis=0).tolist()
            accs.append(acc)
        blob += arr.tobytes()
    return blob, views, accs


def write_glb(path, primitives, texture_png=None):
    """primitives: list of dict(positions, normals, uv, indices, color, textured)."""
    parts = []
    for p in primitives:
        parts += [(p['positions'].astype(np.float32), 'v3'), (p['normals'].astype(np.float32), 'v3'),
                  (p['uv'].astype(np.float32), 'v2'), (p['indices'].astype(np.uint32).ravel(), 'idx')]
    if texture_png is not None:
        parts.append((np.frombuffer(texture_png, np.uint8), None))
    blob, views, accs = _pack(parts)
    blob += b'\0' * ((-len(blob)) % 4)
    mats, meshes = [], []
    for k, p in enumerate(primitives):
        pbr = {'baseColorFactor': p['color'], 'metallicFactor': 0.0, 'roughnessFactor': p.get('roughness', .5)}
        if p.get('textured'): pbr['baseColorTexture'] = {'index': 0}
        mats.append({'name': p.get('name', f'm{k}'), 'pbrMetallicRoughness': pbr})
        meshes.append({'attributes': {'POSITION': 4 * k, 'NORMAL': 4 * k + 1, 'TEXCOORD_0': 4 * k + 2}, 'indices': 4 * k + 3, 'material': k})
    doc = {'asset': {'version': '2.0', 'generator': 'context-commerce test assets'}, 'scene': 0, 'scenes': [{'nodes': [0]}],
           'nodes': [{'mesh': 0}], 'meshes': [{'primitives': meshes}], 'materials': mats, 'accessors': accs, 'bufferViews': views,
           'buffers': [{'byteLength': len(blob)}]}
    if texture_png is not None:
        doc['images'] = [{'bufferView': len(views) - 1, 'mimeType': 'image/png'}]
        doc['textures'] = [{'source': 0, 'sampler': 0}]; doc['samplers'] = [{'magFilter': 9729, 'minFilter': 9729}]
    js = json.dumps(doc, separators=(',', ':')).encode(); js += b' ' * ((-len(js)) % 4)
    out = b'glTF' + struct.pack('<II', 2, 12 + 8 + len(js) + 8 + len(blob)) + struct.pack('<II', len(js), 0x4E4F534A) + js + struct.pack('<II', len(blob), 0x004E4942) + blob
    Path(path).write_bytes(out)
    return str(path)


def _cylinder(r, h, y0=0.0, seg=32, uv_band=(0, 1)):
    a = np.linspace(0, 2 * np.pi, seg + 1)
    ring = np.c_[np.cos(a) * r, np.zeros_like(a), np.sin(a) * r]
    pos = np.vstack([ring + [0, y0, 0], ring + [0, y0 + h, 0]])
    nor = np.vstack([np.c_[np.cos(a), np.zeros_like(a), np.sin(a)]] * 2)
    u = 1 - a / (2 * np.pi)
    uv = np.vstack([np.c_[u, np.full_like(a, uv_band[1])], np.c_[u, np.full_like(a, uv_band[0])]])
    idx = [[i, i + seg + 1, i + 1] for i in range(seg)] + [[i + 1, i + seg + 1, i + seg + 2] for i in range(seg)]
    # Caps
    for yy, ny, flip in ((y0, -1, True), (y0 + h, 1, False)):
        c = len(pos); pos = np.vstack([pos, [[0, yy, 0]], ring[:-1] + [0, yy, 0]])
        nor = np.vstack([nor, np.tile([0, ny, 0], (seg + 1, 1))]); uv = np.vstack([uv, np.full((seg + 1, 2), .02)])
        for i in range(seg):
            t = [c, c + 1 + i, c + 1 + (i + 1) % seg]
            idx.append(t[::-1] if not flip else t)
    return pos, nor, uv, np.asarray(idx)


def bottle_glb(path):
    """20 cm bottle, 3.6 cm radius, labelled body texture, dark cap."""
    tex = np.full((128, 512, 3), (40, 60, 205), np.uint8)
    cv2.rectangle(tex, (0, 0), (511, 127), (245, 245, 245), 10)
    for x in (40, 296):
        cv2.putText(tex, 'CONTEXT', (x, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 4, cv2.LINE_AA)
    png = cv2.imencode('.png', tex)[1].tobytes()
    b = _cylinder(.036, .16, 0.0)
    c = _cylinder(.016, .04, .16)
    return write_glb(path, [
        {'positions': b[0], 'normals': b[1], 'uv': b[2], 'indices': b[3], 'color': [1, 1, 1, 1], 'textured': True, 'name': 'label', 'roughness': .4},
        {'positions': c[0], 'normals': c[1], 'uv': c[2], 'indices': c[3], 'color': [.08, .08, .09, 1], 'name': 'cap', 'roughness': .6}], png)


def box_glb(path, size=(.12, .08, .05), color=(.85, .55, .15, 1)):
    w, h, d = size
    faces = [((0, 0, 1), [(-1, 0, 1), (1, 0, 1), (1, 1, 1), (-1, 1, 1)]), ((0, 0, -1), [(1, 0, -1), (-1, 0, -1), (-1, 1, -1), (1, 1, -1)]),
             ((1, 0, 0), [(1, 0, 1), (1, 0, -1), (1, 1, -1), (1, 1, 1)]), ((-1, 0, 0), [(-1, 0, -1), (-1, 0, 1), (-1, 1, 1), (-1, 1, -1)]),
             ((0, 1, 0), [(-1, 1, 1), (1, 1, 1), (1, 1, -1), (-1, 1, -1)]), ((0, -1, 0), [(-1, 0, -1), (1, 0, -1), (1, 0, 1), (-1, 0, 1)])]
    pos, nor, idx = [], [], []
    for n, quad in faces:
        c = len(pos)
        pos += [(x * w / 2, y * h, z * d / 2) for x, y, z in quad]; nor += [n] * 4
        idx += [[c, c + 1, c + 2], [c, c + 2, c + 3]]
    pos = np.asarray(pos, float)
    return write_glb(path, [{'positions': pos, 'normals': np.asarray(nor, float), 'uv': np.zeros((len(pos), 2)), 'indices': np.asarray(idx), 'color': list(color)}])
