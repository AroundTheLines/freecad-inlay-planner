"""Independent native OCCT reference solids; run inside the pinned FreeCAD.

Uses revolved cutter sections and exact extruded line sweeps, not the
production offset/slice implementation. Scope: straight constant-Z segments
and vertical movement, supported convex cutter profiles, small fixtures.
"""

from dataclasses import replace
from datetime import datetime, timezone
import json
import hashlib
import math
from pathlib import Path
import traceback

from oracles.motion import Cutter


def _profile(tool, full=False):
    """Return a closed radial/height outline with optional exact quarter arcs."""
    R, L = tool.radius, tool.cutting_length
    edges = []

    def line(a, b):
        if a != b:
            edges.append(("line", a, b))

    def arc(a, middle, b):
        edges.append(("arc", a, middle, b))

    if tool.shape == "flat":
        vertices = [(-R if full else 0, 0), (R, 0), (R, L), (-R if full else 0, L)]
        for a, b in zip(vertices, vertices[1:]+vertices[:1]):
            line(a, b)
    elif tool.shape == "v":
        r0 = tool.tip_radius
        h = (R-r0)/math.tan(math.radians(tool.included_angle_deg/2))
        if L < h:
            raise ValueError("Reference cone requires cutting length at least cone height")
        vertices = [(-r0 if full else 0, 0), (r0, 0), (R, h), (R, L)]
        vertices += [(-R, L), (-R, h)] if full else [(0, L)]
        for a, b in zip(vertices, vertices[1:]+vertices[:1]):
            line(a, b)
    else:
        r, f = tool.corner_radius, R-tool.corner_radius
        if L < r:
            raise ValueError("Reference bull requires cutting length above the corner")
        line((-f if full else 0, 0), (f, 0))
        arc((f, 0), (f+r/math.sqrt(2), r-r/math.sqrt(2)), (R, r))
        line((R, r), (R, L))
        line((R, L), (-R if full else 0, L))
        if full:
            line((-R, L), (-R, r))
            arc((-R, r), (-f-r/math.sqrt(2), r-r/math.sqrt(2)), (-f, 0))
        else:
            line((0, L), (0, 0))
    return edges


def _face(tool, full=False, origin=(0, 0, 0), radial=(1, 0)):
    import FreeCAD as App
    import Part

    def vector(point):
        return App.Vector(origin[0]+point[0]*radial[0],
                          origin[1]+point[0]*radial[1], origin[2]+point[1])

    edges = []
    for edge in _profile(tool, full):
        if edge[0] == "line":
            edges.append(Part.makeLine(vector(edge[1]), vector(edge[2])))
        else:
            edges.append(Part.Arc(vector(edge[1]), vector(edge[2]), vector(edge[3])).toShape())
    return Part.Face(Part.Wire(edges))


def cutter_solid(tool, tip=(0, 0, 0)):
    import FreeCAD as App
    shape = _face(tool).revolve(App.Vector(), App.Vector(0, 0, 1), 360)
    shape.translate(App.Vector(*tip))
    if not shape.isValid() or len(shape.Solids) != 1:
        raise AssertionError("Native reference cutter is not one valid solid")
    return shape


def line_sweep(tool, start, end):
    import FreeCAD as App
    distance = math.hypot(end[0]-start[0], end[1]-start[1])
    if distance < 1e-12:
        longer = replace(tool, cutting_length=tool.cutting_length+abs(end[2]-start[2]))
        return cutter_solid(longer, (start[0], start[1], min(start[2], end[2])))
    if abs(start[2]-end[2]) > 1e-12:
        raise ValueError("Simultaneous XYZ sweep is outside this native oracle's scope")
    dx, dy = end[0]-start[0], end[1]-start[1]
    middle = _face(tool, full=True, origin=start, radial=(-dy/distance, dx/distance))
    middle = middle.extrude(App.Vector(dx, dy, 0))
    shape = middle.multiFuse([cutter_solid(tool, start), cutter_solid(tool, end)]).removeSplitter()
    if not shape.isValid():
        raise AssertionError("Invalid native line sweep")
    return shape


def removed_solid(tool, moves, stock, maximum_segments=150):
    if len(moves)-1 > maximum_segments:
        raise ValueError("Fixture exceeds bounded native-oracle segment scope")
    segments = []
    for a, b in zip(moves, moves[1:]):
        start, end = tuple(a[k] for k in ("x", "y", "z")), tuple(b[k] for k in ("x", "y", "z"))
        if min(start[2], end[2]) < 0:
            piece = line_sweep(tool, start, end).common(stock)
            if piece.Volume > 1e-12:
                segments.append(piece)
    if not segments:
        import Part
        return Part.Shape()
    return segments[0].multiFuse(segments[1:]).removeSplitter() if len(segments) > 1 else segments[0]


def expected_volume(tool):
    R, L = tool.radius, tool.cutting_length
    if tool.shape == "flat":
        return math.pi*R*R*L
    if tool.shape == "v":
        r0 = tool.tip_radius
        h = (R-r0)/math.tan(math.radians(tool.included_angle_deg/2))
        return math.pi*h*(R*R+R*r0+r0*r0)/3 + math.pi*R*R*(L-h)
    r, f = tool.corner_radius, R-tool.corner_radius
    missing = 2*math.pi*(f*r*r*(1-math.pi/4)+r**3/6)
    return math.pi*R*R*L-missing


def expected_axial_section_area(tool):
    R, L = tool.radius, tool.cutting_length
    if tool.shape == "flat":
        return 2*R*L
    if tool.shape == "v":
        r0 = tool.tip_radius
        h = (R-r0)/math.tan(math.radians(tool.included_angle_deg/2))
        return h*(R+r0)+2*R*(L-h)
    r, f = tool.corner_radius, R-tool.corner_radius
    return 2*(f*r+math.pi*r*r/4+R*(L-r))


def run_suite(output_directory, actual_flat_directory=None):
    import FreeCAD as App
    import Part
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    checks = []
    report = {"schema_version": 1, "status": "failed", "checks": checks,
              "scope": "Independent native cutter solids and bounded small exact sweeps; not GUI E2E",
              "runtime": App.Version(), "completed_utc": None}

    def near(name, actual, expected, tolerance=1e-6):
        passed = abs(actual-expected) <= tolerance
        checks.append({"name": name, "actual": actual, "expected": expected,
                       "absolute_tolerance": tolerance, "passed": passed})
        if not passed:
            raise AssertionError(f"{name}: {actual} != {expected}")

    try:
        for tool in (Cutter("flat", 2, 6), Cutter("v", 2, 6, included_angle_deg=90, tip_radius=.1),
                     Cutter("bull", 2, 6, corner_radius=.5)):
            near(tool.shape+"_volume", cutter_solid(tool).Volume, expected_volume(tool))
            sweep = line_sweep(tool, (1, 2, -3), (4, 6, -3))
            near(tool.shape+"_line_sweep_volume", sweep.Volume,
                 expected_volume(tool)+5*expected_axial_section_area(tool), 1e-5)
            near(tool.shape+"_vertical_sweep_volume", line_sweep(tool, (0, 0, -3), (0, 0, 1)).Volume,
                 expected_volume(tool)+4*math.pi*tool.radius**2, 1e-5)
        tool = Cutter("flat", 2, 8)
        points = [(2, 2), (18, 2), (18, 10), (2, 10), (2, 2), (2, 6), (18, 6)]
        moves = [dict(x=x, y=y, z=-3) for x, y in points]
        stock = Part.makeBox(30, 20, 6, App.Vector(0, 0, -6))
        removed = removed_solid(tool, moves, stock)
        near("complete_flat_pocket_volume", removed.Volume, 3*(20*12-(4-math.pi)*4), 1e-5)
        removed.exportBrep(str(output / "analytic_flat_pocket.brep"))
        wrong = removed_solid(Cutter("flat", 2.2, 8), moves, stock)
        extra = wrong.cut(removed).Volume
        checks.append({"name": "wrong_radius_mutation", "extra_volume": extra, "passed": extra > 1})
        if extra <= 1:
            raise AssertionError("Wrong-radius mutation did not create detectable extra material removal")
        rough = removed_solid(tool, [dict(m, z=-1.5) for m in moves], stock)
        near("missing_finishing_pass_mutation", removed.cut(rough).Volume, removed.Volume/2, 1e-5)
        if actual_flat_directory:
            fixture = Path(actual_flat_directory)
            spec, result = (json.loads((fixture / filename).read_text())
                            for filename in ("spec.json", "result.json"))
            # The accepted source is a fixed analytic case, not an expectation
            # synthesized from the production target/offset implementation.
            assert spec["style"] == "flat"
            assert spec["artwork"]["regions"][0]["polygons"] == [
                {"outer": [[6, 6], [14, 6], [14, 14], [6, 14], [6, 6]], "holes": []}]
            assert spec["faces"]["board"]["stock"] == [20, 20, 6]
            assert spec["faces"]["board"]["origin"] == [0, 0, 0]
            assert spec["fit"]["driver"] == "depth" and spec["fit"]["value"] == 1
            assert result["status"] == "ready" and result["input_hash"] == spec["input_hash"]
            operations = [op for op in result["operations"] if op["face"] == "board"]
            assert len(operations) == 1
            actual_moves = operations[0]["moves"]
            actual_tool = Cutter("flat", 1, 5)
            stock = Part.makeBox(20, 20, 6, App.Vector(0, 0, -6))
            core = Part.makeBox(5, 5, 1, App.Vector(7.5, 7.5, -1))
            exterior = stock.cut(Part.makeBox(9, 9, 6, App.Vector(5.5, 5.5, -6)))
            actual = removed_solid(actual_tool, actual_moves, stock)
            near("actual_solver_entire_inner_core_removed", core.cut(actual).Volume, 0)
            near("actual_solver_entire_exterior_preserved", actual.common(exterior).Volume, 0)
            actual.exportBrep(str(output / "actual_solver_flat_board.brep"))
            rough = removed_solid(actual_tool, [dict(m, z=max(m["z"], -.5)) for m in actual_moves], stock)
            near("actual_missing_finish_core_volume", core.cut(rough).Volume, 12.5, 1e-5)
            oversized = removed_solid(Cutter("flat", 3, 5), actual_moves, stock)
            extra = oversized.common(exterior).Volume
            if extra <= 1:
                raise AssertionError("Actual oversized-cutter mutation escaped independent exterior volume check")
            checks.append({"name": "actual_oversized_cutter_exterior", "extra_volume": extra, "passed": True})
            report["actual_fixture_sha256"] = {}
            for filename in ("spec.json", "result.json"):
                source = fixture / filename
                report["actual_fixture_sha256"][filename] = hashlib.sha256(source.read_bytes()).hexdigest()
                (output / ("actual_"+filename)).write_bytes(source.read_bytes())
            report["actual_scope"] = "Complete 5x5x1 mm inner core and exterior of a fixed 9x9 mm envelope; does not certify the intervening boundary band or the plug"
        report["status"] = "passed"
    except BaseException:
        report["error"] = traceback.format_exc()
    report["completed_utc"] = datetime.now(timezone.utc).isoformat()
    (output / "native_occt.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    return report
