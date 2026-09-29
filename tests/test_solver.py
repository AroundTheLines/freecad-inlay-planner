import copy
import io
import json
import math

import pytest
from shapely.geometry import GeometryCollection, LineString, Point, Polygon

from inlay_planner.geometry import cutter_profile, cutter_radius, from_records
from inlay_planner.schema import default_spec, seal_spec
from inlay_planner.schema import hash_json
from inlay_planner.motion_storage import PackedMoves
from inlay_planner.solver import solve
from inlay_planner.verify import (SweptStock, subtract_shared_segments,
                                 verify_layer_engagement, verify_plan)


def fixture_spec(style="flat"):
    spec = default_spec()
    for face in spec["faces"].values():
        face.update(stock=[12., 12., 6.], bounds=[0., 0., 12., 12.], clearance_height=2.)
    spec["faces"]["plug"]["design_to_face"] = [-1., 0., 0., 1., 12., 0.]
    ring = [[6+3*math.cos(i*math.tau/48), 6+3*math.sin(i*math.tau/48)] for i in range(48)]
    spec["artwork"]["regions"] = [{"id": "circle", "polygons": [{"outer": ring, "holes": []}]}]
    spec["fit"].update(value=1., glue_gap=.2, side_clearance=.2, backing_clearance=.5)
    spec["resolution"] = {"xy": .05, "z": .05}
    spec["style"] = style
    spec["tools"] = [{"id": "t", "number": 1, "name": "Test cutter", "shape": style,
                      "diameter": 2., "cutting_length": 5., "corner_radius": .25,
                      "included_angle_deg": 90., "tip_diameter": .1,
                      "max_stepdown": .5, "stepover": .8, "feed": 500., "plunge_feed": 200.,
                      "travel_feed": 1000., "spindle": 10000.}]
    spec["selected_tool_ids"] = ["t"]
    return spec


@pytest.fixture(scope="module", params=["flat", "v", "bull", "point_v"])
def solved(request):
    spec = fixture_spec("v" if request.param == "point_v" else request.param)
    if request.param == "point_v":
        spec["tools"][0]["tip_diameter"] = 0.
    spec = seal_spec(spec)
    return spec, solve(spec)


def test_each_family_has_real_paths_and_continuous_stock_evidence(solved):
    spec, result = solved
    assert result["status"] == "needs_acceptance", result["diagnostics"]
    assert result["verification"]["status"] == "passed"
    ids = {c["id"] for c in result["verification"]["checks"] if c["passed"]}
    assert {"board_continuous_coverage", "plug_continuous_coverage", "whole_stock_continuous_insertion",
            "board_predecessor_stock_engagement", "plug_predecessor_stock_engagement"} <= ids
    assert result["derived"] == pytest.approx({"depth": 1., "height": 1.3, "seating_depth": .8})
    assert result["per_face_counts"] == {"board": {"operations": 1, "tool_changes": 0}, "plug": {"operations": 1, "tool_changes": 0}}
    assert all(o["moves"] for o in result["operations"])
    assert all(o["time"]["optimized_s"] <= o["time"]["baseline_s"]+1e-8 for o in result["operations"])


def test_all_output_axes_are_on_their_separate_lattices(solved):
    spec, result = solved
    for op in result["operations"]:
        for move in op["moves"]:
            for axis, q in (("x", spec["resolution"]["xy"]), ("y", spec["resolution"]["xy"]), ("z", spec["resolution"]["z"])):
                assert move[axis]/q == pytest.approx(round(move[axis]/q), abs=1e-7)


def test_profiles_distinguish_actual_cutters():
    tools = {s: fixture_spec(s)["tools"][0] for s in ("flat", "v", "bull")}
    assert cutter_radius(tools["flat"], 0) == 1
    assert cutter_radius(tools["v"], 0) == .05
    assert cutter_radius(tools["bull"], 0) == .75
    assert cutter_profile(tools["bull"], 1) == pytest.approx(.25)
    assert cutter_profile(tools["v"], .55) == pytest.approx(.5)
    assert cutter_profile(tools["flat"], 1.01) == math.inf


def test_review_hash_acceptance_and_invalidation():
    spec = fixture_spec()
    first = solve(seal_spec(spec))
    spec["accepted_proposal_hash"] = first["proposal"]["hash"]
    accepted = solve(seal_spec(spec))
    assert accepted["status"] == "ready", accepted["diagnostics"]
    spec["tools"][0]["feed"] = 501.
    changed = solve(seal_spec(spec))
    assert changed["status"] == "needs_acceptance"
    assert changed["proposal"]["hash"] != first["proposal"]["hash"]


def test_height_driver_and_per_operation_override():
    spec = fixture_spec()
    spec["fit"].update(driver="height", value=1.3)
    spec["overrides"] = {"board:t:finishing": {"max_stepdown": .25, "feed": 123., "spindle": 7000.}}
    result = solve(seal_spec(spec))
    assert result["derived"]["depth"] == pytest.approx(1.)
    board, plug = result["operations"]
    assert board["settings"]["feed"] == 123
    assert board["settings"]["max_stepdown"] == .25
    assert plug["settings"]["feed"] == 500
    assert spec["tools"][0]["feed"] == 500


def test_explicit_partial_exclusion_is_required():
    spec = fixture_spec()
    spec["artwork"]["regions"].append({"id": "outside", "polygons": [{"outer": [[14, 0], [16, 0], [16, 2], [14, 2]], "holes": []}]})
    blocked = solve(seal_spec(spec))
    assert blocked["status"] == "blocked"
    assert any(d["code"] == "ARTWORK_OUT_OF_BOUNDS" for d in blocked["diagnostics"])
    spec["excluded_region_ids"] = ["outside"]
    allowed = solve(seal_spec(spec))
    assert allowed["status"] == "needs_acceptance", allowed["diagnostics"]
    assert allowed["preview"]["excluded_polygons"]


def test_finite_plug_perimeter_is_not_ignored():
    spec = fixture_spec()
    spec["faces"]["plug"]["bounds"] = [1., 1., 11., 11.]
    result = solve(seal_spec(spec))
    assert result["status"] == "blocked"
    assert any(d["code"] == "ASSEMBLY_INTERFERENCE" for d in result["diagnostics"])


def test_bull_root_interference_is_detected():
    spec = fixture_spec("bull")
    spec["fit"].update(backing_clearance=0., side_clearance=0.)
    spec["tools"][0]["corner_radius"] = .75
    result = solve(seal_spec(spec))
    assert result["status"] == "blocked"
    assert any(d["code"] == "ASSEMBLY_INTERFERENCE" for d in result["diagnostics"])


def test_non_grid_origins_preserve_representable_depths_and_lattice():
    spec = fixture_spec()
    spec["fit"]["value"] = 1.025
    for face in spec["faces"].values():
        face["origin"] = [.123, .127, -.025]
    result = solve(seal_spec(spec))
    assert result["status"] == "needs_acceptance", result["diagnostics"]
    assert result["derived"]["depth"] == 1.025
    assert result["verification"]["status"] == "passed"


def test_xy_and_z_resolutions_are_independent():
    spec = fixture_spec()
    spec["resolution"] = {"xy": .03, "z": .07}
    spec["fit"].update(value=.98, glue_gap=.14, backing_clearance=.35)
    result = solve(seal_spec(spec))
    assert result["status"] == "needs_acceptance", result["diagnostics"]
    board = next(o for o in result["operations"] if o["face"] == "board")
    assert min(m["z"] for m in board["moves"]) == pytest.approx(-.98)
    assert all(m["z"]/.07 == pytest.approx(round(m["z"]/.07), abs=1e-7) for m in board["moves"])


def test_two_foot_stock_uses_geometry_not_a_resolution_sized_raster():
    spec = fixture_spec()
    for face in spec["faces"].values():
        face.update(stock=[609.6, 304.8, 19.05], bounds=[0., 0., 609.6, 304.8])
    spec["faces"]["plug"]["design_to_face"] = [-1., 0., 0., 1., 609.6, 0.]
    spec["artwork"]["regions"] = [{"id": "large", "polygons": [{"outer": [[230, 80], [370, 80], [370, 220], [230, 220]], "holes": []}]}]
    spec["fit"].update(value=3., glue_gap=.2, side_clearance=.15, backing_clearance=1.)
    spec["resolution"] = {"xy": .01, "z": .01}
    spec["tools"][0].update(name="1/2 inch", diameter=12.7, cutting_length=20., max_stepdown=1.5, stepover=4.)
    result = solve(seal_spec(spec))
    assert result["status"] == "needs_acceptance", result["diagnostics"]
    assert result["resource_stats"]["motion_count"] < 20000
    assert len(result["preview"]["board"]["vertices"]) <= 441


def test_same_handed_faces_do_not_claim_a_physical_flip():
    spec = fixture_spec()
    spec["faces"]["plug"]["design_to_face"] = [1., 0., 0., 1., 0., 0.]
    result = solve(seal_spec(spec))
    assert result["status"] == "blocked"
    assert "physical face flip" in result["diagnostics"][0]["message"]


def test_outside_stock_is_not_artwork_even_if_bounds_extend():
    spec = fixture_spec()
    spec["faces"]["board"]["stock"] = [7., 12., 6.]
    result = solve(seal_spec(spec))
    assert result["status"] == "blocked"
    assert any(d["code"] == "ARTWORK_OUT_OF_BOUNDS" for d in result["diagnostics"])


def test_per_face_caps_and_real_physical_tool_changes():
    spec = fixture_spec()
    other = dict(spec["tools"][0], id="large", number=2, diameter=3.)
    spec["tools"].append(other)
    spec["selected_tool_ids"].append("large")
    spec["faces"]["board"]["limits"] = {"operations": 1, "tool_changes": 0}
    result = solve(seal_spec(spec))
    assert result["per_face_counts"]["board"] == {"operations": 2, "tool_changes": 1}
    assert result["per_face_counts"]["plug"] == {"operations": 2, "tool_changes": 1}
    assert result["status"] == "blocked"
    assert all(d.get("face") == "board" for d in result["diagnostics"] if d["code"] == "COUNT_LIMIT")


@pytest.mark.parametrize("change", ["negative_side", "ball_nose", "unreachable", "substep", "stale_hash"])
def test_invalid_settings_never_produce_ready(change):
    spec = fixture_spec("bull" if change == "ball_nose" else "flat")
    if change == "negative_side": spec["fit"]["side_clearance"] = -.1
    if change == "ball_nose": spec["tools"][0]["corner_radius"] = 1.
    if change == "unreachable": spec["tools"][0]["cutting_length"] = .5
    if change == "substep": spec["tools"][0]["max_stepdown"] = .01
    spec = seal_spec(spec)
    if change == "stale_hash": spec["fit"]["value"] = 2.
    result = solve(spec)
    assert result["status"] == "blocked"
    assert result["diagnostics"]


def test_cancellation_returns_no_partial_applicable_output():
    result = solve(seal_spec(fixture_spec()), cancelled=lambda: True)
    assert result["status"] == "cancelled"
    assert result["operations"] == []


def test_mutated_motion_is_rejected():
    spec = seal_spec(fixture_spec())
    result = solve(spec)
    damaged = copy.deepcopy(result)
    move = next(m for m in damaged["operations"][0]["moves"] if m["role"] == "cut")
    move["x"] += .023
    verification, _ = verify_plan(spec, damaged, .05/32)
    assert verification["status"] == "failed"
    assert any(d["code"] == "OFF_LATTICE" for d in verification["diagnostics"])


def test_review_digest_rejects_even_geometrically_identical_feed_edits():
    spec = seal_spec(fixture_spec())
    result = solve(spec)
    changed = copy.deepcopy(result)
    changed["operations"][0]["moves"][1]["feed_mm_min"] += 1.
    verification, _ = verify_plan(spec, changed, .05/32)
    assert verification["status"] == "failed"
    assert any(d["code"] == "REVIEWED_MOTION_CHANGED" for d in verification["diagnostics"])


def test_extreme_spacing_fails_recoverably_without_allocating_a_global_grid():
    spec = fixture_spec()
    spec["tools"][0]["stepover"] = 1e-8
    result = solve(seal_spec(spec))
    assert result["status"] == "blocked"
    assert any(d["code"] == "RESOURCE_LIMIT" for d in result["diagnostics"])
    assert result["operations"] == []


def test_sorted_depths_alone_do_not_prove_safe_entry_into_prior_stock():
    spec = fixture_spec()
    tool = spec["tools"][0]
    moves = []
    for x, y, z, role in [(3., 3., 2., "travel"), (3., 3., -.5, "plunge"),
                           (3., 3., 2., "travel"), (6., 3., 2., "travel"),
                           (6., 3., -1., "plunge"), (7., 3., -1., "cut"),
                           (7., 3., 2., "travel")]:
        moves.append({"x": x, "y": y, "z": z, "kind": "feed", "role": role, "feed_mm_min": 200.})
    operation = {"id": "board:t:finishing", "tool_id": "t", "settings": {"max_stepdown": .5}, "moves": moves}
    passed, intervals, region, op_id = verify_layer_engagement(
        spec["faces"]["board"], [operation], {"t": tool}, .05/32, lambda: False)
    assert not passed
    assert intervals > 0
    assert region.area > 0
    assert op_id == operation["id"]


def set_matching_outlines(spec, outline, tolerance=0.):
    spec["faces"]["board"]["stock_outline"] = copy.deepcopy(outline)
    mirror = lambda ring: [[12.-x, y] for x, y in ring]
    spec["faces"]["plug"]["stock_outline"] = [
        {"outer": mirror(p["outer"]), "holes": [mirror(h) for h in p.get("holes", [])]} for p in outline]
    for face in spec["faces"].values():
        face["stock_outline_tolerance"] = tolerance


@pytest.mark.parametrize("style", ["flat", "v", "bull"])
def test_irregular_constant_prism_has_real_sweeps_and_clipped_preview(style):
    spec = fixture_spec(style)
    outline = [{"outer": [[0., 0.], [12., 0.], [12., 8.], [8., 12.], [0., 12.]], "holes": []}]
    set_matching_outlines(spec, outline)
    result = solve(seal_spec(spec))
    assert result["status"] == "needs_acceptance", result["diagnostics"]
    assert result["verification"]["status"] == "passed"
    for name in ("board", "plug"):
        stock = from_records(spec["faces"][name]["stock_outline"])
        mesh = result["preview"][name]
        assert mesh["faces"]
        assert all(stock.covers(Polygon([mesh["vertices"][i][:2] for i in triangle])) for triangle in mesh["faces"])
        assert not stock.equals(Polygon([[0., 0.], [12., 0.], [12., 12.], [0., 12.]]))


def test_holed_stock_keeps_real_holes_in_preview_and_depth_section():
    spec = fixture_spec()
    outer = [[0., 0.], [12., 0.], [12., 12.], [0., 12.]]
    hole = [[5., 5.], [7., 5.], [7., 7.], [5., 7.]]
    set_matching_outlines(spec, [{"outer": outer, "holes": [hole]}])
    spec["artwork"]["regions"] = [{"id": str(x), "polygons": [{
        "outer": [[x+1.2*math.cos(i*math.tau/32), 6+1.2*math.sin(i*math.tau/32)] for i in range(32)],
        "holes": []}]} for x in (3., 9.)]
    spec["tools"][0].update(diameter=.5, stepover=.2)
    spec["fit"].update(value=.5, glue_gap=.1, side_clearance=.1, backing_clearance=.3)
    result = solve(seal_spec(spec))
    assert result["status"] == "needs_acceptance", result["diagnostics"]
    mesh = result["preview"]["board"]
    assert mesh["stock_outline"][0]["holes"]
    assert all(Polygon([mesh["vertices"][i][:2] for i in t]).intersection(Polygon(hole)).area == 0 for t in mesh["faces"])
    middle = min(result["preview"]["sections"][0]["points"], key=lambda p: abs(p["x"]-6.))
    assert not middle["board_material_present"]
    assert not middle["plug_material_present"]


def test_artwork_over_missing_material_is_blocked_even_inside_box_bounds():
    spec = fixture_spec()
    outline = [{"outer": [[0., 0.], [12., 0.], [12., 12.], [0., 12.]],
                "holes": [[[5., 5.], [7., 5.], [7., 7.], [5., 7.]]]}]
    set_matching_outlines(spec, outline)
    result = solve(seal_spec(spec))
    assert result["status"] == "blocked"
    assert any(d["code"] == "ARTWORK_OUT_OF_BOUNDS" for d in result["diagnostics"])
    assert from_records(result["preview"]["blocked_polygons"]).covers(Point(6., 6.))


def test_capture_uncertainty_cannot_invent_required_mating_stock():
    spec = fixture_spec()
    set_matching_outlines(spec, [{"outer": [[2.9, 2.9], [9.1, 2.9], [9.1, 9.1], [2.9, 9.1]], "holes": []}], tolerance=.2)
    result = solve(seal_spec(spec))
    assert result["status"] == "blocked"
    assert any(d["code"] == "ARTWORK_OUT_OF_BOUNDS" for d in result["diagnostics"])


def test_curved_outline_tolerance_is_carried_into_conservative_material_checks():
    spec = fixture_spec()
    # A radius-six circle is enclosed by the 32-chord outline expanded .03mm:
    # its maximum analytic chord sagitta is 6*(1-cos(pi/32)) < .03mm.
    ring = [[6+6*math.cos(i*math.tau/32), 6+6*math.sin(i*math.tau/32)] for i in range(32)]
    set_matching_outlines(spec, [{"outer": ring, "holes": []}], tolerance=.03)
    result = solve(seal_spec(spec))
    assert result["status"] == "needs_acceptance", result["diagnostics"]
    assert result["verification"]["stock_outline_tolerances"] == {"board": .03, "plug": .03}
    assert result["preview"]["board"]["stock_outline_tolerance"] == .03


def test_cutting_air_in_a_through_hole_has_no_engagement_or_stock_bounds_violation():
    spec = fixture_spec()
    face = spec["faces"]["board"]
    face["stock_outline"] = [{"outer": [[0., 0.], [12., 0.], [12., 12.], [0., 12.]],
                              "holes": [[[4., 4.], [8., 4.], [8., 8.], [4., 8.]]]}]
    face["bounds"] = [0., 0., 4., 12.]
    tool = dict(spec["tools"][0], diameter=.5)
    moves = [{"x": x, "y": 6., "z": z, "kind": "feed", "role": role, "feed_mm_min": 200.}
             for x, z, role in [(5., 2., "travel"), (5., -1., "plunge"), (7., -1., "cut"), (7., 2., "travel")]]
    operation = {"id": "air", "tool_id": "t", "settings": {"max_stepdown": .1}, "moves": moves}
    swept = SweptStock(face, [operation], {"t": tool}, .05/32)
    assert swept.removal(0., outer=True).is_empty
    assert verify_layer_engagement(face, [operation], {"t": tool}, .05/32, lambda: False)[0]


def test_absent_stock_collar_does_not_create_false_assembly_interference():
    spec = fixture_spec()
    set_matching_outlines(spec, [{"outer": [[1., 1.], [11., 1.], [11., 11.], [1., 11.]], "holes": []}])
    for face in spec["faces"].values():
        face["bounds"] = [1., 1., 11., 11.]
    result = solve(seal_spec(spec))
    assert result["status"] == "needs_acceptance", result["diagnostics"]
    assert any(c["id"] == "whole_stock_continuous_insertion" and c["passed"] for c in result["verification"]["checks"])


def test_packed_motion_survives_growth_slices_mutation_and_canonical_json():
    expected = [{'x': float(i), 'y': 0., 'z': -1., 'kind': 'feed', 'role': 'cut', 'feed_mm_min': 100}
                for i in range(4100)]
    packed = PackedMoves()
    packed.extend(expected)
    assert packed.storage_bytes <= 2*len(expected)*40
    assert packed[-1] == expected[-1]
    assert packed[4090::2] == expected[4090::2]
    assert hash_json(packed) == hash_json(expected)
    packed[4096]['x'] = 1.125
    expected[4096]['x'] = 1.125
    packed[2].update(feed_mm_min=150.5)
    expected[2]['feed_mm_min'] = 150.5
    stream = io.StringIO()
    json.dump(packed, stream, allow_nan=False)
    assert json.loads(stream.getvalue()) == expected
    assert json.loads(json.dumps(packed)) == expected
    copied = copy.deepcopy(packed)
    copied[0]['x'] = -20
    assert packed[0]['x'] == 0.
    packed.close()


def test_exact_segment_reuse_never_discards_crossings_or_partial_overlap():
    previous = GeometryCollection([LineString([(0., 0.), (2., 0.)])])
    current = GeometryCollection([LineString([(2., 0.), (0., 0.)]),
                                  LineString([(1., -1.), (1., 1.)]),
                                  LineString([(0., 0.), (3., 0.)])])
    remaining = subtract_shared_segments(current, previous)
    assert remaining.length == pytest.approx(5.)
    assert remaining.covers(Point(1., 1.))
    assert remaining.covers(Point(3., 0.))


def test_flat_section_cache_preserves_changes_at_actual_tip_depths():
    spec = fixture_spec()
    moves = [{"x": x, "y": y, "z": z, "kind": "feed", "role": role, "feed_mm_min": 200.}
             for x, y, z, role in [(2., 2., 2., "travel"), (2., 2., -1., "plunge"),
                                   (4., 2., -1., "cut"), (4., 2., 2., "travel"),
                                   (8., 8., 2., "travel"), (8., 8., -2., "plunge"),
                                   (10., 8., -2., "cut"), (10., 8., 2., "travel")]]
    operation = {"id": "two-slices", "tool_id": "t", "moves": moves}
    stock = SweptStock(spec["faces"]["board"], [operation], {"t": spec["tools"][0]}, .0001)
    shallow = stock.removal(.1)
    assert stock.removal(.9) is shallow
    deep = stock.removal(1.1)
    assert stock.removal(1.9) is deep
    assert shallow.covers(Point(3., 2.)) and not deep.covers(Point(3., 2.))
    assert deep.covers(Point(9., 8.))
    assert stock.removal(2.1).is_empty
    assert stock.removal(.1, outer=True).covers(shallow)


def test_entirely_unreachable_artwork_has_no_cuttable_overlay():
    spec = fixture_spec()
    spec["tools"][0]["diameter"] = 20.
    result = solve(seal_spec(spec))
    assert result["status"] == "blocked"
    assert any(d["code"] == "TOOL_TOO_LARGE" for d in result["diagnostics"])
    source = from_records(result["preview"]["source_polygons"])
    assert source.area > 0
    assert from_records(result["preview"]["cuttable_polygons"]).is_empty
    assert from_records(result["preview"]["blocked_polygons"]).equals(source)


def test_shifted_board_residue_is_reported_in_canonical_artwork_coordinates():
    spec = fixture_spec()
    for polygon in spec["artwork"]["regions"][0]["polygons"]:
        polygon["outer"] = [[x-6., y-6.] for x, y in polygon["outer"]]
    spec["faces"]["board"]["design_to_face"] = [1., 0., 0., 1., 6., 6.]
    spec["faces"]["plug"]["design_to_face"] = [-1., 0., 0., 1., 6., 6.]
    spec["overrides"] = {"board:t:finishing": {"stepover": 4.}}
    result = solve(seal_spec(spec))
    assert result["status"] == "blocked"
    item = next(d for d in result["diagnostics"] if d["code"] == "UNCUTTABLE_RESIDUE" and d.get("face") == "board")
    residual = from_records(item["polygons"])
    # A cutter following only the circle's inner rim cannot remove its
    # centre. The face's translation must not move that marker off artwork.
    assert residual.covers(Point(0., 0.))
    assert not residual.covers(Point(6., 6.))
    assert all(-3. <= value <= 3. for value in residual.bounds)
    assert not from_records(result["preview"]["cuttable_polygons"]).covers(Point(0., 0.))


def test_reflected_plug_bounds_diagnostic_is_normalized_exactly_once():
    spec = fixture_spec()
    spec["faces"]["board"]["design_to_face"] = [1., 0., 0., 1., 6., 6.]
    spec["faces"]["plug"]["design_to_face"] = [-1., 0., 0., 1., 6., 6.]
    spec["faces"]["plug"]["bounds"] = [0., 0., 10., 12.]
    moves = [{"x": x, "y": 3., "z": z, "kind": "feed", "role": role, "feed_mm_min": 200.}
             for x, z, role in [(11., 2., "travel"), (11., -.1, "plunge"),
                                (11.1, -.1, "cut"), (11.1, 2., "travel")]]
    result = {"operations": [{"id": "outside", "face": "plug", "tool_id": "t",
                              "settings": {"max_stepdown": .5}, "moves": moves}],
              "derived": {"seating_depth": .8}}
    verification, _ = verify_plan(spec, result, .05/32)
    item = next(d for d in verification["diagnostics"] if d["code"] == "CUTTER_OUT_OF_BOUNDS")
    region = from_records(item["polygons"])
    # Local stock point (11,3) maps to canonical (6-11,3-6).
    assert item["face"] == "plug"
    assert region.covers(Point(-5., -3.))
    assert not region.covers(Point(11., 3.))
