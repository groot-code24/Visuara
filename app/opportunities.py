"""Placement opportunities (specification §6.4): typed, validated records, never raw model output.

`Opportunity` is the canonical schema (version 1). `record()` validates it against the scene it belongs to and stores
it with `validation_status` VALID, or NEEDS_REVIEW when a check could not be made with the data available.
Records that fail validation are refused with `OpportunityInvalid` and never stored. Phase 5 generates
opportunities from analysis and scores them; this module is the contract they are written through.
"""
import json
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SCHEMA_VERSION = 1
CATEGORIES = ('HORIZONTAL_SURFACE', 'VERTICAL_SURFACE', 'ANGLED_SURFACE', 'SCREEN_OVERLAY', 'OBJECT_REPLACEMENT')
REGIONS = ('TOP_LEFT', 'TOP', 'TOP_RIGHT', 'LEFT', 'CENTER', 'RIGHT', 'BOTTOM_LEFT', 'BOTTOM', 'BOTTOM_RIGHT')
ORIENTATION_CATEGORY = {'horizontal': 'HORIZONTAL_SURFACE', 'vertical': 'VERTICAL_SURFACE', 'angled': 'ANGLED_SURFACE'}

_Unit = Field(ge=0.0, le=1.0)


class Geometry(BaseModel):
    """Normalized to the frame: (0,0) top-left, (1,1) bottom-right. `quad` is the surface's four corners, clockwise from top-left."""
    model_config = ConfigDict(extra='forbid')
    frame_second: float = Field(ge=0.0)
    bbox: List[float] = Field(min_length=4, max_length=4)            # x, y, width, height
    quad: Optional[List[List[float]]] = None

    @field_validator('bbox')
    @classmethod
    def _bbox(cls, v):
        x, y, w, h = v
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1 + 1e-6 and y + h <= 1 + 1e-6):
            raise ValueError('bbox must be x, y, width, height inside the unit frame')
        return v

    @field_validator('quad')
    @classmethod
    def _quad(cls, v):
        if v is None: return v
        if len(v) != 4 or any(len(p) != 2 or not (0 <= p[0] <= 1 and 0 <= p[1] <= 1) for p in v):
            raise ValueError('quad must be four [x, y] points inside the unit frame')
        return v


class Opportunity(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scene_id: int
    candidate_kind: Literal['SURFACE', 'OBJECT']
    candidate_label: str = Field(min_length=1, max_length=120)
    placement_category: Literal[CATEGORIES]
    geometry: Geometry
    start_second: float = Field(ge=0.0)
    end_second: float
    visible_seconds: float = Field(ge=0.0)
    feasibility_score: float = _Unit
    product_compatibility: Dict[str, float]                          # product category -> affinity in [0, 1]
    brand_safety: Literal['SAFE', 'REVIEW', 'UNSAFE', 'NOT_EVALUATED']
    rendering_complexity: Literal['LOW', 'MEDIUM', 'HIGH']
    confidence: float = _Unit
    confidence_factors: Dict[str, float] = {}                         # what the confidence was made of, each in [0, 1]
    explanation: str = Field(min_length=1, max_length=2000)

    @field_validator('product_compatibility', 'confidence_factors')
    @classmethod
    def _unit_values(cls, v):
        bad = [k for k, x in v.items() if not (0.0 <= float(x) <= 1.0)]
        if bad: raise ValueError(f'values must be between 0 and 1: {bad}')
        return v

    @model_validator(mode='after')
    def _times(self):
        if self.end_second <= self.start_second: raise ValueError('end_second must be after start_second')
        if self.visible_seconds > self.end_second - self.start_second + 1e-6: raise ValueError('visible_seconds cannot exceed the time window')
        if not (self.start_second - 1e-6 <= self.geometry.frame_second <= self.end_second + 1e-6): raise ValueError('geometry.frame_second must be inside the time window')
        return self


class OpportunityInvalid(ValueError):
    def __init__(self, errors):
        super().__init__('; '.join(errors)); self.errors = errors


def region_of(bbox):
    cx, cy = bbox[0] + bbox[2] / 2, bbox[1] + bbox[3] / 2
    col = 0 if cx < 1 / 3 else (1 if cx < 2 / 3 else 2); row = 0 if cy < 1 / 3 else (1 if cy < 2 / 3 else 2)
    return [['TOP_LEFT', 'TOP', 'TOP_RIGHT'], ['LEFT', 'CENTER', 'RIGHT'], ['BOTTOM_LEFT', 'BOTTOM', 'BOTTOM_RIGHT']][row][col]


def validate(c, data, org_id, writing_analysis_id=None):
    """Parse and check `data` against its scene. Returns (Opportunity, scene row, status, notes). The analysis that is writing
    the opportunity (`writing_analysis_id`) may still be RUNNING; any other must be COMPLETED."""
    try: opp = data if isinstance(data, Opportunity) else Opportunity.model_validate(data)
    except ValidationError as e: raise OpportunityInvalid([f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()])
    scene = c.execute('SELECT s.*, a.status AS analysis_status FROM scenes s JOIN analysis_versions a ON a.id=s.analysis_version_id WHERE s.id=? AND s.org_id=?',
                      (opp.scene_id, org_id)).fetchone()
    if not scene: raise OpportunityInvalid([f'scene {opp.scene_id} does not exist in this organization'])
    errors = []
    if scene['analysis_status'] != 'COMPLETED' and not (scene['analysis_status'] == 'RUNNING' and scene['analysis_version_id'] == writing_analysis_id):
        errors.append('the scene belongs to an analysis that did not complete')
    if opp.start_second < scene['start_second'] - 1e-6 or opp.end_second > scene['end_second'] + 1e-6:
        errors.append(f"time window {opp.start_second}-{opp.end_second} s is outside the scene ({scene['start_second']}-{scene['end_second']} s)")
    if errors: raise OpportunityInvalid(errors)
    notes = []
    if opp.brand_safety == 'NOT_EVALUATED': notes.append('brand safety was not evaluated')
    if opp.brand_safety in ('REVIEW', 'UNSAFE'): notes.append(f'brand safety is {opp.brand_safety}')
    return opp, scene, ('NEEDS_REVIEW' if notes else 'VALID'), notes


def record(c, data, org_id, writing_analysis_id=None):
    opp, scene, status, notes = validate(c, data, org_id, writing_analysis_id)
    return c.execute('INSERT INTO opportunities(org_id,video_id,video_version_id,analysis_version_id,scene_id,schema_version,candidate_kind,candidate_label,placement_category,'
                     'geometry_json,region,start_second,end_second,visible_seconds,feasibility_score,product_compatibility_json,brand_safety,rendering_complexity,confidence,'
                     'confidence_json,explanation,validation_status,validation_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (org_id, scene['video_id'], scene['video_version_id'], scene['analysis_version_id'], scene['id'], SCHEMA_VERSION, opp.candidate_kind,
                      opp.candidate_label, opp.placement_category, opp.geometry.model_dump_json(), region_of(opp.geometry.bbox), opp.start_second, opp.end_second,
                      opp.visible_seconds, opp.feasibility_score, json.dumps(opp.product_compatibility, sort_keys=True), opp.brand_safety, opp.rendering_complexity,
                      opp.confidence, json.dumps(opp.confidence_factors, sort_keys=True), opp.explanation, status, json.dumps({'notes': notes}))).lastrowid


def load(row):
    """A stored row as the API shape, with JSON columns decoded."""
    d = dict(row)
    for k, out in (('geometry_json', 'geometry'), ('product_compatibility_json', 'product_compatibility'), ('confidence_json', 'confidence_factors'),
                   ('validation_json', 'validation'), ('score_breakdown_json', 'score_breakdown')):
        if k in d: d[out] = json.loads(d.pop(k) or '{}')
    return d
