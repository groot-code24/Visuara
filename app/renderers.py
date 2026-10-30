"""Renderer capability declarations and routing. Routing never silently falls back to a weaker tier."""
import os

PROVIDERS = [
    {
        'id': 'overlay_2d', 'tier': 'A', 'name': 'Deterministic screen-space overlay',
        'scene_classes': ['OVERLAY_SCREEN_SPACE'], 'requires': ['overlay_geometry'], 'occlusion': False,
        'max_seconds': 600, 'max_resolution': [3840, 2160], 'input_formats': ['mp4', 'mov', 'webm'], 'output_format': 'mp4/h264+aac',
        'est_cost_usd': 0.0, 'est_seconds_per_video_second': 1.0, 'cancellable': True, 'retry': 'bounded, transient errors only',
        'version': 'compositor-1', 'verified': True,
        'limitations': 'Product is fixed in screen space; it does not follow the scene and is not an in-scene insertion.',
    },
    {
        'id': 'planar_tracked', 'tier': 'B', 'name': 'Perspective-aware planar compositing',
        'scene_classes': ['A_STATIC_PLANAR', 'B_MOVING_PLANAR'], 'requires': ['validated_track'], 'occlusion': False,
        'max_seconds': 600, 'max_resolution': [3840, 2160], 'input_formats': ['mp4', 'mov', 'webm'], 'output_format': 'mp4/h264+aac',
        'est_cost_usd': 0.0, 'est_seconds_per_video_second': 2.0, 'cancellable': True, 'retry': 'bounded, transient errors only',
        'version': 'compositor-1+planar-lk-ransac-1', 'verified': True,
        'limitations': 'Rigid planar surfaces only. No relighting, shadows, motion blur or occlusion handling.',
    },
    {
        'id': 'tracked_3d', 'tier': 'B3D', 'name': 'Tracked 3D object on a planar surface',
        'scene_classes': ['E_3D_ON_TRACKED_PLANE'], 'requires': ['validated_track', 'glb_model', 'surface_size_m', 'observable_or_supplied_focal'],
        'occlusion': False, 'max_seconds': 600, 'max_resolution': [1920, 1080], 'input_formats': ['mp4', 'mov', 'webm', 'glb'],
        'output_format': 'mp4/h264+aac', 'est_cost_usd': 0.0, 'est_seconds_per_video_second': 30.0, 'cancellable': True,
        'retry': 'bounded, transient errors only', 'version': 'render3d-1 (software rasteriser, Lambert+Blinn, planar shadows)', 'verified': True,
        'limitations': 'Object stands on one tracked plane. Light direction is reviewer-set, not estimated. No occlusion by scene objects, '
                       'no reflections or environment lighting, metals shaded as dielectric. Verified on synthetic footage only.',
    },
    {
        'id': 'occlusion_aware', 'tier': 'C', 'name': 'Occlusion-aware compositing',
        'scene_classes': ['C_PARTIAL_OCCLUSION'], 'requires': ['validated_masks'], 'occlusion': True,
        'verified': False, 'available': False, 'est_cost_usd': None, 'version': None,
        'limitations': 'Not implemented. No segmentation model is integrated.',
    },
    {
        'id': 'replicate_generative', 'tier': 'D', 'name': 'Generative video editing (Replicate adapter)',
        'scene_classes': ['D_NON_PLANAR_OR_UNSTABLE'], 'requires': ['provider_config', 'live_integration_check'], 'occlusion': True,
        'verified': False, 'est_cost_usd': None, 'version': None,
        'limitations': 'Adapter exists but has never been run against a live compatible model. Output cannot be checked by the deterministic validator.',
    },
]


def generative_configured():
    return bool(os.getenv('CC_REPLICATE_API_TOKEN') and os.getenv('CC_REPLICATE_MODEL') and os.getenv('CC_REPLICATE_INPUT_TEMPLATE'))


def capabilities():
    out = []
    for p in PROVIDERS:
        p = dict(p)
        if p['id'] == 'replicate_generative':
            p['configured'] = generative_configured()
            p['verified'] = p['configured'] and os.getenv('CC_REPLICATE_VERIFIED') == '1'
            p['available'] = p['verified']
        else:
            p.setdefault('available', True)
        out.append(p)
    return out


def route(scene_class, duration_s, width, height, max_cost_usd=None):
    """Least-expensive available, verified provider that supports the scene class, else a structured refusal."""
    considered = []
    for p in sorted(capabilities(), key=lambda p: (p.get('est_cost_usd') is None, p.get('est_cost_usd') or 0)):
        why = None
        if scene_class not in p['scene_classes']: why = 'scene class unsupported'
        elif not p.get('available'):
            configured = p.get('configured')
            why = 'not implemented' if configured is None else 'not verified by a live integration check' if configured else 'not configured'
        elif not p.get('verified'): why = 'not verified by a live integration check'
        elif p.get('max_seconds') and duration_s > p['max_seconds']: why = 'duration exceeds provider limit'
        elif p.get('max_resolution') and (width > p['max_resolution'][0] or height > p['max_resolution'][1]): why = 'resolution exceeds provider limit'
        elif max_cost_usd is not None and (p.get('est_cost_usd') or 0) > max_cost_usd: why = 'exceeds cost ceiling'
        considered.append({'provider': p['id'], 'tier': p['tier'], 'eligible': why is None, 'reason': why or 'eligible'})
        if why is None:
            return {'selected': p['id'], 'tier': p['tier'], 'considered': considered}
    return {'selected': None, 'tier': None, 'considered': considered,
            'reason_code': 'NO_CAPABLE_RENDERER', 'message': f'No verified renderer supports scene class {scene_class}. The placement must be revised or rejected.'}
