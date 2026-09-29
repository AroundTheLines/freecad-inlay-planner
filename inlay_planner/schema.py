"""Small shared JSON contracts; imports safely in FreeCAD and the worker."""
from __future__ import annotations

import copy
import hashlib
import json
import math

def hash_json(value):
    """Hash canonical JSON without allocating a second complete motion string."""
    digest=hashlib.sha256()
    encoder=json.JSONEncoder(sort_keys=True,separators=(',',':'),allow_nan=False)
    for chunk in encoder.iterencode(value):
        digest.update(chunk.encode())
    return digest.hexdigest()


def fingerprint(spec: dict) -> str:
    value = {k: v for k, v in spec.items() if k != "input_hash"}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def seal_spec(spec: dict) -> dict:
    sealed = copy.deepcopy(spec)
    sealed["input_hash"] = fingerprint(sealed)
    return sealed


def default_spec() -> dict:
    faces = {}
    for name in ("board", "plug"):
        faces[name] = {"stock": [152.4, 101.6, 19.05],
                       "bounds": [0., 0., 152.4, 101.6],
                       "design_to_face": [1., 0., 0., 1., 0., 0.],
                       "origin": [0., 0., 0.], "clearance_height": 5.,
                       "limits": {"operations": None, "tool_changes": None}}
    faces['plug']['design_to_face'] = [-1., 0., 0., 1., 152.4, 0.]
    return {"schema_version": 1, "revision": 0, "style": "flat",
            "artwork": {"regions": []}, "excluded_region_ids": [],
            "exclusions": [], "accepted_proposal_hash": None,
            "faces": faces, "resolution": {"xy": .01, "z": .01},
            "fit": {"driver": "depth", "value": 3., "glue_gap": .2,
                    "side_clearance": .1, "backing_clearance": 1.},
            "tools": [], "selected_tool_ids": [], "overrides": {},
            "travel_mode": "feed"}


def transform_point(point, matrix):
    x, y = point
    a, b, c, d, e, f = matrix
    return [a*x+c*y+e, b*x+d*y+f]


def transform_polygons(polygons, matrix):
    return [{"outer": [transform_point(p, matrix) for p in poly["outer"]],
             "holes": [[transform_point(p, matrix) for p in ring]
                       for ring in poly.get("holes", [])]} for poly in polygons]


def bounds_of(polygons):
    points = [p for poly in polygons for p in poly["outer"]]
    if not points:
        return [0., 0., 0., 0.]
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]


def finite_tree(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("All dimensions and motion values must be finite.")
    if isinstance(value, dict):
        for v in value.values():
            finite_tree(v)
    elif isinstance(value, (tuple, list)):
        for v in value:
            finite_tree(v)
