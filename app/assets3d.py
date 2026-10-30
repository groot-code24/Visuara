"""Validated loading of 3D product assets (glTF 2.0 binary, .glb).

Only self-contained GLB files are accepted: external buffer/image URIs and data URIs are rejected,
so loading a model can never read local files or fetch from the network.
"""
import json, struct
import cv2
import numpy as np

MAX_GLB_BYTES = 25 * 1024 * 1024
MAX_TRIANGLES = 100_000
MAX_TEXTURE_SIDE = 4096
_COMP = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NCOMP = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}


class AssetError(ValueError):
    pass


def _read_chunks(data):
    if len(data) > MAX_GLB_BYTES:
        raise AssetError(f'Model exceeds {MAX_GLB_BYTES // 1048576} MB')
    if len(data) < 20 or data[:4] != b'glTF':
        raise AssetError('Not a binary glTF (.glb) file')
    version, length = struct.unpack_from('<II', data, 4)
    if version != 2 or length != len(data):
        raise AssetError('Unsupported glTF version or truncated file')
    off, js, binc = 12, None, b''
    while off + 8 <= len(data):
        clen, ctype = struct.unpack_from('<II', data, off)
        body = data[off + 8:off + 8 + clen]
        if len(body) != clen:
            raise AssetError('Truncated GLB chunk')
        if ctype == 0x4E4F534A: js = json.loads(body.decode('utf-8'))
        elif ctype == 0x004E4942: binc = body
        off += 8 + clen
    if js is None:
        raise AssetError('GLB has no JSON chunk')
    for b in js.get('buffers', []):
        if 'uri' in b:
            raise AssetError('External or data-URI buffers are not accepted; export a self-contained .glb')
    for im in js.get('images', []):
        if 'uri' in im:
            raise AssetError('External texture URIs are not accepted; embed textures in the .glb')
    return js, binc


def _accessor(js, binc, i):
    acc = js['accessors'][i]
    if acc.get('sparse'):
        raise AssetError('Sparse accessors are not supported')
    bv = js['bufferViews'][acc['bufferView']]
    dtype = np.dtype(_COMP[acc['componentType']]); n = _NCOMP[acc['type']]
    start = bv.get('byteOffset', 0) + acc.get('byteOffset', 0)
    stride = bv.get('byteStride') or dtype.itemsize * n
    count = acc['count']
    end = start + stride * (count - 1) + dtype.itemsize * n if count else start
    if end > bv.get('byteOffset', 0) + bv['byteLength'] or end > len(binc):
        raise AssetError('Accessor reads outside its buffer')
    if stride == dtype.itemsize * n:
        arr = np.frombuffer(binc, dtype, count * n, start).reshape(count, n)
    else:
        arr = np.stack([np.frombuffer(binc, dtype, n, start + k * stride) for k in range(count)])
    if acc.get('normalized'):
        arr = arr.astype(np.float32) / np.iinfo(dtype).max
    return arr


def _node_matrix(node):
    if 'matrix' in node:
        return np.asarray(node['matrix'], np.float64).reshape(4, 4).T
    T = np.eye(4); T[:3, 3] = node.get('translation', [0, 0, 0])
    x, y, z, w = node.get('rotation', [0, 0, 0, 1])
    R = np.eye(4); R[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    S = np.diag(list(node.get('scale', [1, 1, 1])) + [1])
    return T @ R @ S


def load_glb(data):
    """Parse and validate a GLB. Returns a mesh dict in glTF coordinates (metres, +Y up, +Z front) and a report."""
    js, binc = _read_chunks(data)
    textures = []
    for im in js.get('images', []):
        bv = js['bufferViews'][im['bufferView']]
        raw = binc[bv.get('byteOffset', 0):bv.get('byteOffset', 0) + bv['byteLength']]
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise AssetError('Embedded texture could not be decoded (PNG or JPEG required)')
        if max(img.shape[:2]) > MAX_TEXTURE_SIDE:
            raise AssetError(f'Texture larger than {MAX_TEXTURE_SIDE}px')
        if img.ndim == 2: img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
        elif img.shape[2] == 3: img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        textures.append(img)
    materials = []
    for m in js.get('materials', []) or [{}]:
        pbr = m.get('pbrMetallicRoughness', {})
        tex = pbr.get('baseColorTexture')
        tex_img = textures[js['textures'][tex['index']]['source']] if tex is not None else None
        if tex is not None and tex.get('texCoord', 0) != 0:
            raise AssetError('Only TEXCOORD_0 is supported for base colour textures')
        materials.append({'base_color': [float(c) for c in pbr.get('baseColorFactor', [1, 1, 1, 1])], 'texture': tex_img,
                          'metallic': float(pbr.get('metallicFactor', 1.0)), 'roughness': float(pbr.get('roughnessFactor', 1.0)),
                          'name': m.get('name', '')})
    pos, nor, uv, faces, fmat = [], [], [], [], []
    has_uv = True; warnings = []
    scene = js.get('scenes', [{}])[js.get('scene', 0)] if js.get('scenes') else {'nodes': list(range(len(js.get('nodes', []))))}

    def visit(ni, parent, depth=0):
        nonlocal has_uv
        if depth > 32: raise AssetError('Node hierarchy too deep')
        node = js['nodes'][ni]; M = parent @ _node_matrix(node)
        if 'mesh' in node:
            for prim in js['meshes'][node['mesh']]['primitives']:
                if prim.get('mode', 4) != 4:
                    raise AssetError('Only triangle primitives are supported')
                P = _accessor(js, binc, prim['attributes']['POSITION']).astype(np.float64)
                if P.shape[1] != 3 or not np.isfinite(P).all(): raise AssetError('Invalid vertex positions')
                Pw = (np.c_[P, np.ones(len(P))] @ M.T)[:, :3]
                N = _accessor(js, binc, prim['attributes']['NORMAL']).astype(np.float64) if 'NORMAL' in prim['attributes'] else None
                Nw = None if N is None else N @ np.linalg.inv(M[:3, :3]).T
                T = _accessor(js, binc, prim['attributes']['TEXCOORD_0']).astype(np.float64) if 'TEXCOORD_0' in prim['attributes'] else None
                I = _accessor(js, binc, prim['indices']).astype(np.int64).reshape(-1, 3) if 'indices' in prim else np.arange(len(P)).reshape(-1, 3)
                if I.size and (I.min() < 0 or I.max() >= len(P)): raise AssetError('Triangle index out of range')
                base = sum(len(x) for x in pos)
                pos.append(Pw); faces.append(I + base); fmat.append(np.full(len(I), prim.get('material', 0)))
                nor.append(Nw if Nw is not None else np.zeros_like(Pw)); uv.append(T if T is not None else np.zeros((len(P), 2)))
                if N is None: warnings.append('Mesh without normals: flat normals computed')
                if T is None: has_uv = False
        for ch in node.get('children', []):
            visit(ch, M, depth + 1)
    for ni in scene.get('nodes', []):
        visit(ni, np.eye(4))
    if not pos:
        raise AssetError('The model contains no triangle meshes')
    V = np.vstack(pos).astype(np.float32); F = np.vstack(faces).astype(np.int32); FM = np.concatenate(fmat).astype(np.int32)
    if len(F) > MAX_TRIANGLES: raise AssetError(f'Model has {len(F)} triangles; the limit is {MAX_TRIANGLES}')
    if FM.max() >= len(materials): raise AssetError('Primitive references a missing material')
    Nn = np.vstack(nor).astype(np.float32)
    Nn = _ensure_normals(V, F, Nn)
    ext = V.max(axis=0) - V.min(axis=0)
    if not np.isfinite(ext).all() or ext.max() <= 1e-4:
        raise AssetError('Model has degenerate size')
    if ext.max() > 5:
        warnings.append(f'Model is {ext.max():.2f} m across; check that it was exported in metres')
    report = {'triangles': int(len(F)), 'vertices': int(len(V)), 'materials': len(materials),
              'textured': any(m['texture'] is not None for m in materials) and has_uv,
              'size_m': [round(float(x), 4) for x in ext], 'warnings': sorted(set(warnings)),
              'metallic_materials': sum(1 for m in materials if m['metallic'] > .5)}
    if report['metallic_materials']:
        report['warnings'].append('Metallic materials are shaded as dielectric (no environment reflections)')
    return {'positions': V, 'normals': Nn, 'uv': np.vstack(uv).astype(np.float32), 'faces': F, 'face_material': FM,
            'materials': materials, 'has_uv': has_uv}, report


def _ensure_normals(V, F, N):
    missing = np.linalg.norm(N, axis=1) < 1e-8
    if missing.any():
        fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
        acc = np.zeros_like(V)
        for k in range(3): np.add.at(acc, F[:, k], fn)
        N = N.copy(); N[missing] = acc[missing]
    return N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-8)
