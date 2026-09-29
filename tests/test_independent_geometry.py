"""Independent analytic fixtures and fault injections; no solver-derived answers."""

from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import random
import xml.etree.ElementTree as ET

import pytest

from oracles.gcode import UnsupportedProgram, audit_program, compare_endpoints, parse_program
from oracles.fixtures import small_pair
from oracles.motion import (
    Cutter, apply_affine, assert_air_travel, assert_depth_probes, assert_fixture_reach, assert_lattice,
    assert_probe_stepdown, assert_registration, assert_sweep_bounds, face_counts, motion_seconds, operations_removal,
    paired_probe_gap, segment_removal,
)
from oracles.stock import (PolygonStock, assert_capsule_inside_rectangle, circle_chord_error,
                          local_assembly_gap, material_removal)


def move(x, y, z, role="cut", feed=600, kind="feed"):
    return dict(x=x, y=y, z=z, role=role, feed_mm_min=feed, kind=kind)


def test_native_export_comparison_bounds_feed_roundoff_without_hiding_quantization():
    from oracles.audit_export import compare
    snapshot = [{"tool": 1, "commands": [
        {"name": "M3", "parameters": {"S": 10000}},
        {"name": "G0", "parameters": {"Z": 2}},
        {"name": "G0", "parameters": {"X": 0, "Y": 0}},
        {"name": "G1", "parameters": {"Z": -1, "F": 8.333333333333333}}]}]
    code = "G21 G90 G61\nT1 M6\nG43 H1\nS10000 M3\nG0 Z2\nG0 X0 Y0\nG1 Z-1 F500\nM30\n"
    assert compare(snapshot, code)["passed"]
    assert not compare(snapshot, code, feed_tolerance="0")["passed"]
    assert not compare(snapshot, code.replace("F500", "F499.99998"))["passed"]
    assert not compare(snapshot, code.replace("Z-1 F", "Z-1.00001 F"))["passed"]
    assert not compare(snapshot, code.replace("G61", "G64"))["passed"]
    assert not compare(snapshot, code.replace("H1", "H2"))["passed"]


def test_portable_original_contract_detects_coordinate_and_unexplained_feed_mutations(tmp_path):
    from copy import deepcopy
    from oracles.audit_portable_gui import original_contract
    spec = {'resolution': {'xy': .05, 'z': .025},
            'tools': [{'id': 't', 'number': 1, 'diameter': 3.175}]}
    plan = {'operations': [{'id': face+':t', 'face': face, 'tool_id': 't', 'moves': [
        {'x': 0, 'y': 0, 'z': 2, 'kind': 'feed', 'feed_mm_min': 1000},
        {'x': .1, 'y': .2, 'z': -.5, 'kind': 'feed', 'feed_mm_min': 500}]} for face in ('board', 'plug')]}
    (tmp_path/'spec.json').write_text(json.dumps(spec))
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    snapshot = {face: [{'id': face+':t', 'tool': 1, 'diameter': 3.175, 'commands': [
        {'name': 'G1', 'parameters': {'Z': 2, 'F': 9 if face=='board' else 16.666667}},
        {'name': 'G1', 'parameters': {'X': 0, 'Y': 0, 'Z': 2, 'F': 16.666667}},
        {'name': 'G1', 'parameters': {'X': .1, 'Y': .2, 'Z': -.5, 'F': 8.333333}}]}]
        for face in ('board', 'plug')}
    report = {'document': str(tmp_path/'original.FCStd')}
    observed = original_contract(tmp_path, report, {'native.json': snapshot}, [])
    assert not observed['errors']
    stage = observed['stages'][0]
    assert stage['maximum_endpoint_error_mm'] == '0'
    assert not stage['all_unedited_feeds_exact_within_roundoff']
    assert any(d['original_mm_min']=='500' and d['restored_mm_min']=='499.999980'
               and d['difference_mm_min']=='-0.000020' for d in stage['native_serialization_feed_drift'])
    bad = deepcopy(snapshot)
    bad['plug'][0]['commands'][-1]['parameters']['X'] = .125
    errors = original_contract(tmp_path, report, {'native.json': bad}, [])['errors']
    assert any('accepted coordinate changed' in e for e in errors)
    assert any('original x lattice' in e for e in errors)
    bad = deepcopy(snapshot)
    bad['plug'][0]['commands'][-1]['parameters']['F'] = 10
    assert any('unexplained feed change' in e for e in
               original_contract(tmp_path, report, {'native.json': bad}, [])['errors'])


def rounded_pocket_motion():
    """Radius-2 pocket: outer size 20x12, floor 3, two independent depth passes."""
    commands = [move(2, 2, 5, "travel", 1200, "rapid")]
    for depth in (1.5, 3.0):
        commands.append(move(2, 2, -depth, "plunge", 180))
        for x, y in ((18, 2), (18, 10), (2, 10), (2, 2), (2, 6), (18, 6), (18, 2), (2, 2)):
            commands.append(move(x, y, -depth))
        commands.append(move(2, 2, 5, "travel", 1200))
    return commands


def test_independent_stock_membership_keeps_concavity_and_holes():
    outline = [{"outer": [[0, 0], [10, 0], [10, 6], [6, 6], [6, 10], [0, 10]],
                "holes": [[[2, 2], [4, 2], [4, 4], [2, 4]]]}]
    stock = PolygonStock(outline)
    assert stock.membership((1, 5)).state == "material"
    assert stock.membership((3, 3)).state == "air"
    assert stock.membership((8, 8)).state == "air"
    assert stock.membership((-1, 3)).state == "air"
    reversed_stock = PolygonStock([{"outer": outline[0]["outer"][::-1],
                                    "holes": [outline[0]["holes"][0][::-1]]}])
    for point in ((1, 5), (3, 3), (8, 8), (-1, 3)):
        assert reversed_stock.membership(point).state == stock.membership(point).state
    # Dropping the inner boundary is a detectable geometry mutation.
    assert PolygonStock([dict(outline[0], holes=[])]).membership((3, 3)).state == "material"


def test_stock_capture_uncertainty_is_neither_material_nor_air_certification():
    stock = PolygonStock([{"outer": [[0, 0], [10, 0], [10, 10], [0, 10]], "holes": []}], .01)
    assert stock.membership((.005, 5)).state == "uncertain"
    assert stock.membership((-.005, 5)).state == "uncertain"
    assert stock.membership((.02, 5)).state == "material"
    assert stock.membership((-.02, 5)).state == "air"


def test_air_sweep_does_not_invent_removed_stock_or_an_assembly_gap():
    stock = PolygonStock([{"outer": [[0, 0], [10, 0], [10, 10], [0, 10]],
                           "holes": [[[3, 3], [7, 3], [7, 7], [3, 7]]]}])
    cutter = Cutter("flat", .25, 5)
    moves = [move(4, 5, 2, "travel"), move(4, 5, -1, "plunge"), move(6, 5, -1), move(6, 5, 2, "travel")]
    operations = [{"tool_id": "t", "moves": moves}]
    assert operations_removal((5, 5), operations, {"t": cutter}).lower == 1
    actual = material_removal((5, 5), stock, operations, {"t": cutter})
    assert actual.state == "air" and actual.removed_depth is None
    assert local_assembly_gap(actual, actual, .8) == {"state": "no_common_material", "gap": None}
    assert_capsule_inside_rectangle((4, 5), (6, 5), .25, (3, 3, 7, 7))
    with pytest.raises(AssertionError):
        assert_capsule_inside_rectangle((4, 3.1), (6, 3.1), .25, (3, 3, 7, 7))


def test_circle_capture_error_uses_complete_chords():
    ring = [[5+4*math.cos(i*math.tau/32), 6+4*math.sin(i*math.tau/32)] for i in range(32)]
    assert circle_chord_error(ring, (5, 6), 4) == pytest.approx(4*(1-math.cos(math.pi/32)))
    ring[0][0] += .01
    with pytest.raises(AssertionError):
        circle_chord_error(ring, (5, 6), 4)


@pytest.mark.parametrize("seed", range(32))
def test_seeded_svg_affine_holes_and_equivalent_unit_viewports(seed):
    """Straight-edge analytic invariants; no production helper supplies answers."""
    from inlay_planner.svg_input import parse_svg
    rng = random.Random(20260928+seed)
    width, height = rng.uniform(4, 24), rng.uniform(4, 24)
    theta = rng.uniform(-math.pi, math.pi)
    sx, sy = rng.uniform(.2, 3), rng.uniform(.2, 3)
    sx *= -1 if seed % 2 else 1
    shear = rng.uniform(-.4, .4)
    c, s = math.cos(theta), math.sin(theta)
    # Rotation times a signed/nonuniform scale and shear.
    a, b, cc, d = c*sx, s*sx, c*shear-s*sy, s*shear+c*sy
    e, f = rng.uniform(50, 100), rng.uniform(50, 100)
    determinant = a*d-b*cc
    outer = [(0, 0), (width, 0), (width, height), (0, height)]
    hole = [(width/4, height/4), (3*width/4, height/4),
            (3*width/4, 3*height/4), (width/4, 3*height/4)]
    def path(ring):
        return "M"+" L".join(f"{x:.17g},{y:.17g}" for x, y in ring)+" Z"
    matrix = " ".join(f"{v:.17g}" for v in (a, b, cc, d, e, f))
    physical = [(a*x+cc*y+e, 200-(b*x+d*y+f)) for x, y in outer]
    expected_bounds = [min(x for x, _ in physical), min(y for _, y in physical),
                       max(x for x, _ in physical), max(y for _, y in physical)]
    expected_area = abs(determinant)*width*height*.75
    observed = []
    for canvas in ("200mm", f"{200/25.4:.17g}in"):
        source = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas}" height="{canvas}" '
                  f'viewBox="0 0 200 200"><path id="compound" fill-rule="evenodd" '
                  f'transform="matrix({matrix})" d="{path(outer)} {path(hole)}"/></svg>')
        actual = parse_svg(source, .001)
        assert not actual["diagnostics"], (seed, actual["diagnostics"])
        assert len(actual["regions"]) == 1
        polygons = actual["regions"][0]["polygons"]
        assert len(polygons) == 1 and len(polygons[0]["holes"]) == 1
        def area(ring):
            return abs(sum(p[0]*q[1]-q[0]*p[1] for p, q in zip(ring, ring[1:]+ring[:1])))/2
        measured = area(polygons[0]["outer"])-area(polygons[0]["holes"][0])
        assert measured == pytest.approx(expected_area, rel=1e-10, abs=1e-9), seed
        assert actual["bounds"] == pytest.approx(expected_bounds, abs=1e-9), seed
        observed.append((measured, actual["bounds"]))
    assert observed[0][0] == pytest.approx(observed[1][0], abs=1e-9)
    assert observed[0][1] == pytest.approx(observed[1][1], abs=1e-9)


@pytest.mark.parametrize("distance,height", [(0, 0), (1, 0), (2, 0)])
def test_flat_lower_profile_is_analytic(distance, height):
    assert Cutter("flat", 2, 8).lower_surface(distance) == height


@pytest.mark.parametrize("angle", [30, 60, 90, 120])
def test_v_profile_matches_separate_right_triangle(angle):
    tool = Cutter("v", 4, 20, included_angle_deg=angle, tip_radius=0.2)
    height = 1.25
    radius = 0.2 + height * math.tan(math.radians(angle)/2)
    assert tool.lower_surface(radius) == pytest.approx(height)
    assert tool.radius_at_height(height) == pytest.approx(radius)
    assert tool.lower_surface(0.1) == 0


def test_bull_profile_matches_circle_equation_and_has_flat_centre():
    tool = Cutter("bull", 5, 12, corner_radius=2)
    assert tool.lower_surface(0) == tool.lower_surface(3) == 0
    assert tool.lower_surface(4) == pytest.approx(2-math.sqrt(3))
    assert tool.lower_surface(5) == 2
    assert tool.radius_at_height(1) == pytest.approx(3+math.sqrt(3))
    assert math.isinf(tool.lower_surface(5.001))


@pytest.mark.parametrize("kwargs", [
    dict(shape="ball", radius=2, cutting_length=6),
    dict(shape="bull", radius=2, cutting_length=6, corner_radius=2),
    dict(shape="v", radius=2, cutting_length=6, included_angle_deg=180),
    dict(shape="flat", radius=math.nan, cutting_length=6),
])
def test_invalid_tool_geometry_cannot_be_certified(kwargs):
    with pytest.raises(ValueError):
        Cutter(**kwargs)


def test_constant_depth_segment_is_checked_between_endpoints():
    tool = Cutter("flat", 1, 6)
    result = segment_removal((5, 0), (0, 0, 3), (10, 0, 3), tool)
    assert result.lower == result.upper == 3
    assert result.certified
    assert segment_removal((5, 2), (0, 0, 3), (10, 0, 3), tool).upper == 0


def test_ramping_sweep_bounds_known_cone_maximum():
    # 90-degree cone, centre x=0..4, tip depth 1..3, query x=2:
    # max(1+x/2-abs(x-2)) occurs at x=2 and equals 2.
    tool = Cutter("v", 5, 8, included_angle_deg=90)
    interval = segment_removal((2, 0), (0, 0, 1), (4, 0, 3), tool, tolerance=1e-6)
    assert interval.certified
    assert interval.lower <= 2 + 1e-12
    assert interval.upper >= 2 - 1e-12
    assert interval.upper-interval.lower <= 1e-6


def test_exhausted_interval_refinement_is_not_reported_as_proof():
    tool = Cutter("bull", 5, 12, corner_radius=2)
    interval = segment_removal((1.1, 4.95), (0, 0, 2), (4, 0, 6), tool,
                               tolerance=1e-12, max_nodes=1)
    assert not interval.certified
    assert interval.upper > interval.lower


def test_complete_flat_pocket_and_origin_translation():
    commands = rounded_pocket_motion()
    tool = Cutter("flat", 2, 8)
    probes = [(2, 2, 3), (10, 6, 3), (10, 0.25, 3), (0.25, 6, 3),
              (19.75, 6, 3), (10, 11.75, 3), (-0.1, 6, 0), (0.1, 0.1, 0)]
    assert_depth_probes(commands, tool, probes)
    assert_sweep_bounds(commands, tool, (0, 0, 20, 12))
    assert_air_travel(commands)
    assert_lattice(commands, 0.01, 0.5)
    origin = (7.5, -3.25, 1.0)
    translated = [dict(m, x=m["x"]-origin[0], y=m["y"]-origin[1], z=m["z"]-origin[2])
                  for m in commands]
    assert_depth_probes(translated, tool, probes, origin)


def test_missing_finishing_pass_mutation_is_rejected():
    commands = rounded_pocket_motion()
    only_roughing = commands[:11]
    with pytest.raises(AssertionError, match="Removal"):
        assert_depth_probes(only_roughing, Cutter("flat", 2, 8), [(10, 6, 3)])


def test_different_cutter_operations_union_without_invented_connections():
    cutters = {"rough": Cutter("flat", 2, 5), "finish": Cutter("flat", 1, 5)}
    operations = [
        {"tool_id": "rough", "moves": [move(0, 0, -.5), move(10, 0, -.5)]},
        {"tool_id": "finish", "moves": [move(0, 0, -1), move(10, 0, -1)]},
    ]
    assert operations_removal((5, 0), operations, cutters).lower == 1
    assert operations_removal((5, 1.5), operations, cutters).lower == .5
    separate = [operations[0], {"tool_id": "finish", "moves": [move(0, 10, -1), move(10, 10, -1)]}]
    assert operations_removal((5, 5), separate, cutters).upper == 0


def test_stepdown_uses_preceding_stock_instead_of_only_layer_labels():
    cutters = {"rough": Cutter("flat", 2, 5), "finish": Cutter("flat", 1, 5)}
    rough = {"tool_id": "rough", "settings": {"max_stepdown": .5},
             "moves": [move(0, 0, -.5), move(10, 0, -.5)]}
    finish = {"tool_id": "finish", "settings": {"max_stepdown": .5},
              "moves": [move(0, 0, -1), move(10, 0, -1)]}
    assert_probe_stepdown([(5, 0), (5, 1.5)], [rough, finish], cutters)
    with pytest.raises(AssertionError, match="new material"):
        assert_probe_stepdown([(5, 0)], [finish], cutters)
    distant_rough = dict(rough, moves=[move(0, 5, -.5), move(10, 5, -.5)])
    with pytest.raises(AssertionError, match="new material"):
        assert_probe_stepdown([(5, 0)], [distant_rough, finish], cutters)


def test_wrong_cutter_radius_mutations_are_rejected():
    commands = rounded_pocket_motion()
    with pytest.raises(AssertionError, match="exceeds"):
        assert_sweep_bounds(commands, Cutter("flat", 2.2, 8), (0, 0, 20, 12))
    with pytest.raises(AssertionError, match="Removal"):
        assert_depth_probes(commands, Cutter("flat", 1.7, 8), [(10, 0.2, 3)])


def test_cutter_sweeps_in_air_are_clipped_to_actual_stock_bounds():
    commands = [move(-1, 1, -1), move(-1, 8, -1)]
    assert_sweep_bounds(commands, Cutter("flat", 2, 8), (0, 0, 10, 10),
                        stock_bounds=(0, 0, 10, 10))
    with pytest.raises(AssertionError, match="exceeds"):
        assert_sweep_bounds(commands, Cutter("flat", 2, 8), (2, 2, 8, 8),
                            stock_bounds=(0, 0, 10, 10))


def test_reach_and_retained_backing_use_stock_depth_not_work_z():
    cutter = Cutter("flat", 1, 5)
    moves = [move(2, 2, -4.5)]
    assert_fixture_reach(moves, cutter, 6, origin=(0, 0, 2))  # Depth is 2.5.
    with pytest.raises(AssertionError, match="cutting reach"):
        assert_fixture_reach([move(2, 2, -7.1)], cutter, 9, origin=(0, 0, 2))
    with pytest.raises(AssertionError, match="backing"):
        assert_fixture_reach([move(2, 2, -3)], cutter, 3)


def test_unsafe_link_mutation_is_rejected():
    commands = rounded_pocket_motion()
    commands.extend([move(2, 2, -1), move(18, 10, -1, "travel", 1200, "rapid")])
    with pytest.raises(AssertionError, match="enters stock"):
        assert_air_travel(commands)


def test_v_depth_mutation_changes_cut_between_endpoints():
    tool = Cutter("v", 3, 8, included_angle_deg=90)
    commands = [move(0, 0, -2), move(10, 0, -2)]
    assert_depth_probes(commands, tool, [(5, 1, 1)])
    commands[0]["z"] -= 0.05
    commands[1]["z"] -= 0.05
    with pytest.raises(AssertionError):
        assert_depth_probes(commands, tool, [(5, 1, 1)])


def test_asymmetric_registration_detects_wrong_mirror():
    points = [(1, 2), (7, 3), (4, 9)]
    board_transform = [1, 0, 0, 1, 10, 20]
    plug_transform = [-1, 0, 0, 1, 50, 3]
    board = [apply_affine(p, board_transform) for p in points]
    plug = [apply_affine(p, plug_transform) for p in points]
    assert_registration(points, board, plug, board_transform, plug_transform)
    wrong_plug = [apply_affine(p, [1, 0, 0, 1, 50, 3]) for p in points]
    with pytest.raises(AssertionError, match="registration"):
        assert_registration(points, board, wrong_plug, board_transform, plug_transform)


def test_assembled_bottom_gap_and_backing_clearance_from_actual_motion():
    tool = Cutter("flat", 2, 8)
    board_face = {"design_to_face": [1, 0, 0, 1, 0, 0], "origin": [0, 0, 0]}
    plug_face = {"design_to_face": [-1, 0, 0, 1, 50, 0], "origin": [0, 0, 0]}
    board = rounded_pocket_motion()
    plug = [move(20, 14, 5, "travel", 1200, "rapid"),
            move(20, 14, -3.5, "plunge", 180), move(40, 14, -3.5)]
    bottom = paired_probe_gap((10, 6), board, plug, tool, tool, board_face, plug_face, 2.5)
    backing = paired_probe_gap((10, 14), board, plug, tool, tool, board_face, plug_face, 2.5)
    assert bottom.certified and bottom.lower == bottom.upper == 0.5
    assert backing.certified and backing.lower == backing.upper == 1.0
    missing_relief = paired_probe_gap((10, 14), board, plug[:1], tool, tool,
                                      board_face, plug_face, 2.5)
    assert missing_relief.upper < 0  # Whole plug stock would collide with board top.


def test_per_face_counts_and_feed_changes_are_independent():
    ops = [dict(face=f, tool_id=t) for f, t in [
        ("board", "A"), ("board", "B"), ("board", "A"),
        ("plug", "A"), ("plug", "A"), ("plug", "B")]]
    assert face_counts(ops) == {"board": {"operations": 3, "tool_changes": 2},
                               "plug": {"operations": 3, "tool_changes": 1}}


def test_timing_uses_mm_per_minute_and_excludes_manual_changes():
    commands = [move(0, 0, 5, "travel", 1200, "rapid"),
                move(60, 0, 5, "travel", 1200, "rapid"),
                move(60, 0, -5, "plunge", 120), move(120, 0, -5, "cut", 600)]
    assert motion_seconds(commands) == {"cut_s": 11.0, "travel_s": 3.0, "total_s": 14.0}


def test_separate_xy_z_lattices_reject_one_axis_error():
    commands = [move(1.25, 2.5, -0.3)]
    assert_lattice(commands, 0.025, 0.1)
    with pytest.raises(AssertionError, match="z"):
        assert_lattice([dict(commands[0], z=-0.31)], 0.025, 0.1)


def test_modal_gcode_units_speeds_tools_and_endpoints():
    text = """(ordinary CAM output)
G21 G90 G17 G54 G40 G49 G80 G61
T2 M6
G43 H2
S12000 M3
G0 Z5
G0 X0 Y0
G1 Z-2 F120
G1 X25.4 F600
G20
G1 X2 F10
T2 M6
T3 M6
T2 M6
M5
M2
"""
    program = parse_program(text)
    assert program.tool_loads == [2, 2, 3, 2]
    assert program.tool_changes == 2
    assert program.motions[-1].feed_mm_min == Decimal("254.0")
    assert program.motions[-1].spindle_rpm == 12000
    assert program.motions[-1].spindle_mode == "M3"
    assert program.motions[-1].length_offset == 2
    compare_endpoints([(None, None, 5), (0, 0, 5), (0, 0, -2),
                       (25.4, 0, -2), (50.8, 0, -2)], program)
    assert audit_program(program, "0.01", "0.01") == []


def test_unrestricted_blending_is_not_certified_as_linear_motion():
    program = parse_program("G21 G90 G64\nG0 X0 Y0 Z5\nG1 Z-1 F200\n")
    assert any("path control" in issue for issue in audit_program(program, .01, .01))


def test_inch_precision_loss_is_detected_without_redefining_resolution():
    # 1 mm rounded to four inch decimals becomes 1.00076 mm.
    program = parse_program("G20 G90 G61\nG0 X0.0394 Y0 Z0.1969\n")
    assert any("off" in issue for issue in audit_program(program, .001, .001))
    with pytest.raises(AssertionError, match="differs"):
        compare_endpoints([(1, 0, 5)], program, tolerance_mm=.00001)


def test_six_decimal_serialization_can_lose_a_finer_lattice():
    coordinate = Decimal("0.0000004")
    text = f"G21 G90 G61\nG0 X{coordinate:.6f} Y0 Z1\n"
    program = parse_program(text)
    with pytest.raises(AssertionError, match="differs"):
        compare_endpoints([(coordinate, 0, 1)], program, tolerance_mm="0.00000001")


@pytest.mark.parametrize("code", ["G2 X5 Y5 I1 J0", "G53 G0 Z0", "G93 G1 X1 F2",
                                  "G1 X[#1]", "G10 L2 P1 X0"])
def test_unmodelled_controller_features_are_not_silently_accepted(code):
    with pytest.raises(UnsupportedProgram):
        parse_program("G21 G90 G61\n"+code)


def test_a_path_after_program_end_cannot_supply_missing_cutting_coverage():
    with pytest.raises(UnsupportedProgram, match="after program end"):
        parse_program("G21 G90 G61\nG0 X0 Y0 Z5\nM2\nG1 Z-3 F200\n")


def test_initial_position_and_work_offset_change_are_not_invented():
    program = parse_program("G21 G90 G61\nG0 Z5\nG0 X2 Y3\nG55\nG0 Z5\n")
    assert not program.motions[0].complete
    assert program.motions[0].end == (None, None, Decimal(5))
    assert program.motions[-1].end == (None, None, Decimal(5))


def test_tool_change_stops_the_spindle_until_explicit_restart():
    program = parse_program("G21 G90 G61\nM3 S12000\nT2 M6\n"
                            "G0 Z5\nG0 X1 Y2\nG1 Z-1 F100\nM3\nG1 X3\n")
    assert program.motions[-2].spindle_mode == "M5"
    assert program.motions[-1].spindle_mode == "M3"
    assert program.motions[-1].spindle_rpm == Decimal(12000)


@pytest.mark.parametrize("mutation", ["coordinate", "feed", "settings", "timing", "ordering", "missing"])
def test_native_apply_gate_requires_the_exact_verified_motion_binding(mutation):
    from inlay_planner.native import validate_pair  # Actual boundary under test.
    spec = small_pair("flat")
    tool = spec["tools"][0]
    operations = [{"id": side+"-binding-fixture", "face": side, "tool_id": tool["id"],
                   "purpose": "certificate binding only", "predecessors": [],
                   "settings": {"spindle": 10000},
                   "moves": [move(7, 7, 2, "travel", 1000), move(7, 7, -.5, "plunge", 200),
                             move(8, 7, -.5, "cut", 500), move(8, 7, 2, "travel", 1000)],
                   "time": {"optimized_s": 1, "baseline_s": 1}}
                  for side in ("board", "plug")]
    binding = hashlib.sha256(json.dumps(operations, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode()).hexdigest()
    result = {"schema_version": 1, "revision": spec["revision"], "input_hash": spec["input_hash"],
              "status": "ready", "operations": operations, "proposal": None, "diagnostics": [],
              "per_face_counts": {side: {"operations": 1, "tool_changes": 0} for side in ("board", "plug")},
              "verification": {"status": "passed", "motion_hash": binding,
                               "method": "synthetic binding fixture; no geometric qualification"}}
    validate_pair(spec, result)
    if mutation == "coordinate":
        operations[0]["moves"][2]["x"] += .05
    elif mutation == "feed":
        operations[0]["moves"][2]["feed_mm_min"] += 1
    elif mutation == "settings":
        operations[0]["settings"]["spindle"] += 1
    elif mutation == "timing":
        operations[0]["time"]["optimized_s"] += .01
    elif mutation == "ordering":
        operations.reverse()
    else:
        del result["verification"]["motion_hash"]
    with pytest.raises(ValueError, match="(?i)motion|certificate|verified|binding"):
        validate_pair(spec, result)


def test_six_example_families_are_valid_local_filled_vectors():
    folder = Path(__file__).resolve().parents[1] / "examples"
    manifest = json.loads((folder / "fixture_manifest.json").read_text())
    assert len(manifest["fixtures"]) == 6
    for fixture in manifest["fixtures"]:
        source = (folder / fixture["filename"]).read_text()
        root = ET.fromstring(source)
        tags = {node.tag.rsplit("}", 1)[-1] for node in root.iter()}
        assert not {"image", "text", "script", "foreignObject"} & tags
        assert root.attrib["width"].endswith("mm")
        assert root.attrib["height"].endswith("mm")


def _shoelace(ring):
    # Independent closed-polygon area; never calls the importer's geometry code.
    return abs(sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(ring, ring[1:]+ring[:1])))/2


@pytest.mark.parametrize("filename", ["asymmetric_leaf.svg", "outlined_letterform.svg",
    "scattered_motif.svg", "fine_detail_ladder.svg", "boundary_crossing_ornament.svg",
    "dense_decorative_field.svg"])
def test_production_svg_import_matches_independent_fixture_topology_and_area(filename):
    from inlay_planner.svg_input import parse_svg  # Actual output under test only.
    folder = Path(__file__).resolve().parents[1] / "examples"
    fixture = next(x for x in json.loads((folder/"fixture_manifest.json").read_text())["fixtures"]
                   if x["filename"] == filename)
    result = parse_svg((folder/filename).read_text(), tolerance=0.0003125)
    assert result["diagnostics"] == []
    polygons = [polygon for region in result["regions"] for polygon in region["polygons"]]
    assert len(polygons) == fixture["components"]
    assert sum(len(p["holes"]) for p in polygons) == fixture["holes"]
    assert result["size"] == fixture["canvas_mm"]
    expected_bounds = fixture.get("expected_bounds_canonical_mm")
    if expected_bounds:
        assert result["bounds"] == pytest.approx(expected_bounds, abs=0.0004)
    if "area_mm2" in fixture:
        measured = sum(_shoelace(p["outer"])-sum(_shoelace(h) for h in p["holes"])
                       for p in polygons)
        assert measured == pytest.approx(fixture["area_mm2"], abs=fixture.get("area_abs_tolerance", 1e-5))
    if "boundary_vertices" in fixture:
        assert sum(len(p["outer"])-1 for p in polygons) == fixture["boundary_vertices"]


def _svg(body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="20mm" '
            'viewBox="0 0 20 20">'+body+'</svg>')


def test_svg_visible_child_can_override_inherited_visibility():
    # SVG2 render model §3.2.3: visibility has no direct effect on the container.
    from inlay_planner.svg_input import parse_svg
    result = parse_svg(_svg('<g visibility="hidden"><rect id="visible" '
                           'visibility="visible" width="5" height="5"/></g>'))
    assert not result["diagnostics"]
    assert any(r["id"] == "visible" and r["polygons"] for r in result["regions"])
    assert result["bounds"] == pytest.approx([0, 15, 5, 20])


@pytest.mark.parametrize("parent,child", [('opacity=".5"', 'opacity="1"'),
    ('clip-path="url(#clip)"', 'clip-path="none"'),
    ('filter="url(#effect)"', 'filter="none"')])
def test_svg_group_compositing_cannot_be_undone_by_child(parent, child):
    # Opacity/clipping/filtering apply after the group's children are composed.
    from inlay_planner.svg_input import parse_svg
    result = parse_svg(_svg(f'<g {parent}><rect id="child" {child} width="5" height="5"/></g>'
                           '<rect id="good" x="10" width="3" height="3"/>'))
    assert result["diagnostics"]
    assert not any(r["id"] == "child" and r["supported"] for r in result["regions"])
    assert any(r["id"] == "good" and r["supported"] for r in result["regions"])


def test_svg_invalid_transform_tokens_cannot_be_silently_removed():
    from inlay_planner.svg_input import parse_svg
    result = parse_svg(_svg('<rect width="5" height="5" transform="translate(1garbage 2)"/>'))
    assert result["diagnostics"]
    assert not any(r["supported"] for r in result["regions"])


@pytest.mark.parametrize("source", [
    _svg('<polygon points="0,0 5garbage,0 5,5 0,5"/>'),
    _svg('<rect width="5" height="5"/>').replace('viewBox="0 0 20 20"',
                                               'viewBox="0 0 20garbage 20"'),
])
def test_svg_invalid_coordinate_lists_cannot_be_silently_repaired(source):
    from inlay_planner.svg_input import parse_svg
    try:
        result = parse_svg(source)
    except ValueError:
        return
    assert result["diagnostics"]
    assert not any(r["supported"] for r in result["regions"])


def test_requirement_ledger_does_not_claim_gui_passes_from_unit_tests():
    path = Path(__file__).with_name("requirement_ledger.json")
    ledger = json.loads(path.read_text())
    assert [item["id"] for item in ledger["cases"]] == [f"E{i:02d}" for i in range(33)]
    for case in ledger["cases"]:
        if case["status"] == "passed":
            assert case["evidence"]
            assert all(item.get("artifact_sha256") and item.get("run_id")
                       for item in case["evidence"])
