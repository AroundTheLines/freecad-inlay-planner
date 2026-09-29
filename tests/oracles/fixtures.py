"""Literal small inputs for independent planner verification, in millimetres."""

import hashlib
import json
import math


def seal(spec):
    spec = json.loads(json.dumps(spec, allow_nan=False))
    spec.pop("input_hash", None)
    spec["input_hash"] = hashlib.sha256(json.dumps(
        spec, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return spec


def small_pair(style="flat"):
    stock_size = 20 if style == "flat" else 12
    if style == "flat":
        outer = [[6, 6], [14, 6], [14, 14], [6, 14], [6, 6]]
    else:
        outer = [[6+3*math.cos(2*math.pi*i/48), 6+3*math.sin(2*math.pi*i/48)] for i in range(48)]
        outer.append(outer[0])
    tool = {"id": "oracle-"+style, "number": 1, "name": "Analytic "+style,
            "shape": style, "diameter": 2., "cutting_length": 5.,
            "max_stepdown": .5, "stepover": .8, "feed": 500.,
            "plunge_feed": 200., "travel_feed": 1000., "spindle": 10000.}
    if style == "v":
        tool.update(included_angle_deg=90., tip_diameter=.1)
    if style == "bull":
        tool["corner_radius"] = .25
    faces = {name: {"stock": [stock_size, stock_size, 6.],
                    "bounds": [0., 0., stock_size, stock_size],
                    "design_to_face": [1., 0., 0., 1., 0., 0.] if name == "board"
                        else [-1., 0., 0., 1., stock_size, 0.],
                    "origin": [0., 0., 0.], "clearance_height": 2.,
                    "limits": {"operations": None, "tool_changes": None}}
             for name in ("board", "plug")}
    return seal({"schema_version": 1, "revision": 1, "style": style,
                 "artwork": {"regions": [{"id": "analytic", "polygons": [{"outer": outer, "holes": []}]}]},
                 "excluded_region_ids": [], "exclusions": [], "accepted_proposal_hash": None,
                 "faces": faces, "resolution": {"xy": .05, "z": .05},
                 "fit": {"driver": "depth", "value": 1., "glue_gap": .2,
                         "side_clearance": .2, "backing_clearance": .5},
                 "tools": [tool], "selected_tool_ids": [tool["id"]], "overrides": {},
                 "travel_mode": "feed"})
