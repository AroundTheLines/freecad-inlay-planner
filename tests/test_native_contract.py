"""Pre-write checks and FreeCAD's mm/s serialization boundary, without CAD."""
import copy
import hashlib
import json

import pytest

from inlay_planner import native
from inlay_planner.schema import default_spec, seal_spec


def bind_synthetic_motion(result):
    """Bind adapter-only fixtures; this does not assert geometric qualification."""
    result["verification"]["motion_hash"] = hashlib.sha256(json.dumps(
        result["operations"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def pair():
    spec = default_spec()
    spec["tools"] = [dict(id="endmill", number=1, name="1/8 inch", shape="flat", diameter=3.175,
                          cutting_length=10, feed=600, plunge_feed=120, travel_feed=1800, spindle=12000)]
    spec["selected_tool_ids"] = ["endmill"]
    spec = seal_spec(spec)
    operations = []
    for side in ("board", "plug"):
        operations.append(dict(id=side + "_finish", face=side, tool_id="endmill", predecessors=[],
                               settings=dict(spindle=12345), moves=[
                                   dict(kind="feed", x=10, y=20, z=5, feed_mm_min=1800),
                                   dict(kind="feed", x=10, y=20, z=-1, feed_mm_min=120),
                                   dict(kind="feed", x=30, y=20, z=-1, feed_mm_min=600),
                                   dict(kind="rapid", x=30, y=20, z=5)]))
    result = dict(schema_version=1, input_hash=spec["input_hash"], revision=spec["revision"], status="ready",
                  verification=dict(status="passed"), operations=operations,
                  per_face_counts={side: dict(operations=1, tool_changes=0) for side in ("board", "plug")})
    bind_synthetic_motion(result)
    return spec, result


def reseal(spec, result):
    spec = seal_spec(spec)
    result["input_hash"] = spec["input_hash"]
    bind_synthetic_motion(result)
    return spec, result


def test_reviewed_pair_counts_are_per_face():
    spec, result = pair()
    for face in spec["faces"].values():
        face["limits"] = dict(operations=1, tool_changes=0)
    spec, result = reseal(spec, result)
    assert native.validate_pair(spec, result) == result["per_face_counts"]


@pytest.mark.parametrize("mutation, message", [
    (lambda s, r: r.update(input_hash="old"), "Inputs changed"),
    (lambda s, r: r.update(revision=-1), "revision"),
    (lambda s, r: r.update(status="blocked"), "ready"),
    (lambda s, r: r.update(verification={"status": "failed"}), "verification"),
    (lambda s, r: r.update(proposal={"hash": "unaccepted"}), "accepted"),
    (lambda s, r: r.update(diagnostics=[{"severity": "error"}]), "diagnostics"),
    (lambda s, r: r["operations"][0].update(tool_id="not-selected"), "unselected"),
    (lambda s, r: r["operations"][0].update(predecessors=["plug_finish"]), "prerequisites"),
    (lambda s, r: r["operations"][0]["moves"][0].update(z=-1), "clearance"),
    (lambda s, r: r["operations"][0]["moves"][1].update(feed_mm_min=0), "positive"),
        (lambda s, r: r["operations"][0]["moves"][1].update(x=float("nan")), "finite|JSON"),
    (lambda s, r: r["per_face_counts"]["board"].update(tool_changes=1), "counts"),
])
def test_unreviewed_or_unmachinable_result_rejected(mutation, message):
    spec, result = pair()
    mutation(spec, result)
    # These cases isolate structural validation from certificate invalidation.
    try:
        bind_synthetic_motion(result)
    except ValueError:
        pass  # NaN remains intentionally unserializable.
    with pytest.raises(ValueError, match=message):
        native.validate_pair(spec, result)


def test_native_six_decimal_save_is_bounded_by_machine_resolution():
    spec, result = pair()
    result["operations"][0]["moves"][1]["x"] = 1.0000004
    bind_synthetic_motion(result)
    assert native.validate_pair(spec, result)
    spec["resolution"]["xy"] = .000001
    spec, result = reseal(spec, result)
    with pytest.raises(ValueError, match="save precision"):
        native.validate_pair(spec, result)


def test_feed_units_spindle_and_initial_z_only():
    spec, result = pair()
    lines = native.motion_text(result["operations"][0], 5)
    assert lines[:3] == ["G90", "M3 S12345", "G1 Z5 F30"]
    assert lines[3] == "G1 X10 Y20 Z5 F30"
    assert lines[4].endswith("F2")
    assert lines[5].endswith("F10")
    assert lines[-1] == "G0 X30 Y20 Z5"
    assert not any("G20" in line or "G21" in line for line in lines)


def test_explicit_decimal_numbers_do_not_use_parser_unsupported_exponents():
    assert native._number(.000000001) == "0.000000001"
    assert native._number(-.000000001) == "-0.000000001"


def test_initial_clearance_keeps_the_solver_lattice_coordinate():
    _, result = pair()
    result["operations"][0]["moves"][0]["z"] = 5.004
    assert native.motion_text(result["operations"][0], 5)[2] == "G1 Z5.004 F30"


@pytest.mark.parametrize("shape, filename, shape_type, extra", [
    ("flat", "endmill.fcstd", "Endmill", {}),
    ("v", "v-bit.fcstd", "VBit", {"included_angle_deg": 60, "tip_diameter": .1}),
    ("bull", "bullnose.fcstd", "Bullnose", {"corner_radius": 1}),
])
def test_supported_tool_snapshots_use_builtin_template_names(shape, filename, shape_type, extra):
    spec, _ = pair()
    tool = copy.deepcopy(spec["tools"][0])
    tool.update(shape=shape, **extra)
    tool["native_definition"] = {"shape": "/missing/personal-shape.fcstd"}
    definition = native._tool_definition(tool)
    assert definition["shape"] == filename
    assert definition["shape-type"] == shape_type
    assert definition["parameter"]["Diameter"] == 3.175
