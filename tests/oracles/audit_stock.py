"""Actual native-outline solver audits with independent stock-aware probes.

These checks use scalar point/ring and analytic circle calculations for
expected values.  They do not borrow the production stock-mask helpers.
"""

import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from oracles.audit_solver import dependency_versions, source_snapshot
from oracles.fixtures import seal, small_pair
from oracles.motion import (Cutter, apply_affine, assert_air_travel, assert_fixture_reach,
                            assert_probe_stepdown, assert_sweep_bounds, check_result_contract)
from oracles.stock import (PolygonStock, assert_capsule_inside_rectangle, circle_chord_error,
                          local_assembly_gap, material_removal)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mirror_records(records, width):
    return [{"outer": [[width-x, y] for x, y in polygon["outer"]],
             "holes": [[[width-x, y] for x, y in hole] for hole in polygon.get("holes", [])]}
            for polygon in records]


def medallion(cx, cy, radius):
    return {"outer": [[cx+radius*math.cos(i*math.tau/48), cy+radius*math.sin(i*math.tau/48)]
                      for i in range(48)], "holes": []}


def fixture(kind, native):
    spec = small_pair()
    if kind == "native_concave_hole":
        captured = native["spec"]["faces"]["board"]
        art = [{"outer": [[9, 10], [17, 10], [17, 18], [9, 18]], "holes": []}]
        probes = [((13, 14), "material", .2), ((20, 14), "material", .5),
                  ((4.5, 4.5), "air", None), ((32, 27), "air", None),
                  ((-1, 14), "air", None)]
    elif kind == "native_annulus":
        captured = native["spec"]["faces"]["plug"]
        art = [medallion(9, 20, 3), medallion(31, 20, 3)]
        probes = [((9, 20), "material", .2), ((31, 20), "material", .2),
                  ((20, 30), "material", .5), ((20, 20), "air", None),
                  ((1, 1), "air", None), ((40.01, 20), "air", None)]
    else:
        captured = {"stock": [20, 20, 6], "bounds": [2, 2, 18, 18],
                    "stock_outline": [{"outer": [[2, 2], [18, 2], [18, 18], [2, 18]], "holes": []}],
                    "stock_outline_tolerance": 0.}
        art = spec["artwork"]["regions"][0]["polygons"]
        probes = [((10, 10), "material", .2), ((4, 10), "material", .5),
                  ((1, 10), "air", None), ((19, 10), "air", None)]
    width = captured["stock"][0]
    for name, face in spec["faces"].items():
        face.update(stock=deepcopy(captured["stock"]), bounds=deepcopy(captured["bounds"]),
                    stock_outline=deepcopy(captured["stock_outline"]) if name == "board"
                        else mirror_records(captured["stock_outline"], width),
                    stock_outline_tolerance=captured["stock_outline_tolerance"],
                    design_to_face=[1, 0, 0, 1, 0, 0] if name == "board" else [-1, 0, 0, 1, width, 0])
    spec["artwork"]["regions"][0]["polygons"] = art
    return seal(spec), probes


def outline_capture_checks(native):
    checks = []
    board = native["spec"]["faces"]["board"]
    annulus = native["spec"]["faces"]["plug"]
    observed = board["stock_outline"][0]
    expected_outer = [(0, 0), (40, 0), (40, 20), (25, 20), (25, 35), (0, 35)]
    expected_hole = [(3, 3), (6, 3), (6, 6), (3, 6)]
    for label, points, expected in (("native_concave_vertices", observed["outer"], expected_outer),
                                    ("native_square_hole_vertices", observed["holes"][0], expected_hole)):
        checks.append({"id": label, "passed": len(points) == len(expected)
                       and all(min(math.dist(point, target) for target in expected) < 1e-8 for point in points)})
    rings = annulus["stock_outline"][0]
    for label, ring, radius in (("annulus_outer_chord_enclosure", rings["outer"], 20.),
                                ("annulus_inner_chord_enclosure", rings["holes"][0], 5.)):
        error = circle_chord_error(ring, (20, 20), radius)
        checks.append({"id": label, "measured_max_chord_error_mm": error,
                       "declared_allowance_mm": annulus["stock_outline_tolerance"],
                       "passed": error <= annulus["stock_outline_tolerance"]})
    return checks


def positive(kind, native, directory):
    from inlay_planner.solver import solve
    spec, probes = fixture(kind, native)
    result = solve(spec)
    initial_status = result["status"]
    if initial_status == "needs_acceptance":
        spec["accepted_proposal_hash"] = result["proposal"]["hash"]
        spec = seal(spec)
        result = solve(spec)
    errors = check_result_contract(spec, result)
    cutters = {t["id"]: Cutter.from_snapshot(t) for t in spec["tools"]}
    operations = {name: [op for op in result["operations"] if op["face"] == name]
                  for name in ("board", "plug")}
    masks = {name: PolygonStock(face["stock_outline"], face["stock_outline_tolerance"])
             for name, face in spec["faces"].items()}
    for name, face in spec["faces"].items():
        material_points = [apply_affine(point, face["design_to_face"])
                           for point, state, _ in probes if state == "material"]
        try:
            assert_probe_stepdown(material_points, operations[name], cutters, face["origin"])
            for op in operations[name]:
                cutter = cutters[op["tool_id"]]
                assert_air_travel(op["moves"], face["origin"])
                assert_fixture_reach(op["moves"], cutter, face["stock"][2], face["origin"])
                # These fixtures have either full enclosing bounds or an exact inset rectangle.
                rectangle = face["bounds"] if kind == "absent_collar" else (0, 0, *face["stock"][:2])
                assert_sweep_bounds(op["moves"], cutter, face["bounds"], face["origin"], stock_bounds=rectangle)
        except AssertionError as error:
            errors.append(f"{name}: {error}")
    measurements = []
    for point, expected_state, expected_gap in probes:
        material = []
        for name, face in spec["faces"].items():
            value = material_removal(apply_affine(point, face["design_to_face"]), masks[name],
                                     operations[name], cutters, face["origin"])
            material.append(value)
            if value.state != expected_state:
                errors.append(f"{name} stock at {point}: {value.state}, expected {expected_state}")
        pair = local_assembly_gap(*material, .8)
        gap = pair["gap"]
        if expected_gap is None:
            passed = pair["state"] == "no_common_material" and gap is None
        else:
            passed = gap is not None and gap.certified and max(abs(gap.lower-expected_gap), abs(gap.upper-expected_gap)) < 1e-5
        if not passed:
            errors.append(f"Incorrect stock-aware assembly gap at {point}: {pair}")
        measurements.append({"canonical_xy": point, "expected_material": expected_state,
                             "expected_gap_mm": expected_gap, "state": pair["state"],
                             "gap_mm": asdict(gap) if gap else None, "passed": passed})
    preview = {"mesh_probes": 0, "section_probes": 0, "air_section_probes": 0}
    for name, mask in masks.items():
        mesh = result.get("preview", {}).get(name, {})
        vertices = mesh.get("vertices", [])
        # A clipped indexed mesh may retain unused grid vertices. Only indices
        # referenced by a rendered triangle describe displayed material.
        used = {index for triangle in mesh.get("faces", []) for index in triangle}
        points = [vertices[index][:2] for index in sorted(used)]
        points += [tuple(sum(vertices[i][axis] for i in triangle)/3 for axis in (0, 1))
                   for triangle in mesh.get("faces", [])]
        for point in points:
            preview["mesh_probes"] += 1
            if mask.membership(point).state == "air":
                errors.append(f"{name} preview contains an air vertex/centroid at {point}")
                break
    for section in result.get("preview", {}).get("sections", []):
        for sample in section["points"]:
            for name, face in spec["faces"].items():
                point = apply_affine((sample["x"], sample["y"]), face["design_to_face"])
                state = masks[name].membership(point).state
                if state == "uncertain":
                    continue
                preview["section_probes"] += 1
                preview["air_section_probes"] += state == "air"
                if sample[name+"_material_present"] != (state == "material"):
                    errors.append(f"Section invents/misses {name} stock at {point}")
    if kind == "native_annulus" and preview["air_section_probes"] == 0:
        errors.append("The annular fixture did not exercise a section through its hole")
    directory.mkdir()
    for label, value in (("spec", spec), ("result", result)):
        (directory/(label+".json")).write_text(json.dumps(value, allow_nan=False))
    return {"case": kind, "initial_status": initial_status, "accepted_status": result["status"],
            "passed": not errors, "errors": errors, "probes": measurements, "preview": preview,
            "artifacts_sha256": {path.name: digest(path) for path in directory.iterdir()}}


def missing_stock_controls(native, directory):
    from inlay_planner.solver import solve
    checks = []
    for kind, centre in (("native_concave_hole", (4.5, 4.5)), ("native_concave_hole", (32, 27)),
                         ("native_annulus", (20, 20))):
        spec, _ = fixture(kind, native)
        spec["artwork"]["regions"][0]["polygons"] = [medallion(*centre, 1.)]
        spec = seal(spec)
        result = solve(spec)
        passed = result["status"] == "blocked" and not result["operations"] and any(
            d["code"] == "ARTWORK_OUT_OF_BOUNDS" for d in result["diagnostics"])
        case = {"case": kind, "art_centre": centre, "passed": passed, "status": result["status"],
                "diagnostics": result["diagnostics"]}
        checks.append(case)
        filename = f"blocked-{kind}-{centre[0]}-{centre[1]}.json"
        (directory/filename).write_text(json.dumps({"spec": spec, "result": result}, allow_nan=False))
    return checks


def air_engagement_controls(native):
    from inlay_planner.verify import SweptStock, verify_layer_engagement
    spec, _ = fixture("native_concave_hole", native)
    face = spec["faces"]["board"]
    face["bounds"] = [0, 0, 3, 35]
    tool = dict(spec["tools"][0], id="air-test", diameter=.5)
    checks = []
    for y, expected_air in ((4.5, True), (2.9, False)):
        if expected_air:
            assert_capsule_inside_rectangle((4, y), (5, y), .25, (3, 3, 6, 6))
        moves = [dict(x=x, y=y, z=z, role=role, kind="feed", feed_mm_min=200)
                 for x, z, role in ((4, 2, "travel"), (4, -1, "plunge"), (5, -1, "cut"), (5, 2, "travel"))]
        operation = {"id": "air-control", "tool_id": tool["id"], "settings": {"max_stepdown": .1}, "moves": moves}
        mask = PolygonStock(face["stock_outline"])
        expected_state = mask.membership((4.5, y)).state
        if expected_state != ("air" if expected_air else "material"):
            raise AssertionError("Literal air/material control fixture is incorrect")
        sweep = SweptStock(face, [operation], {tool["id"]: tool}, .05/32)
        empty = sweep.removal(0, outer=True).is_empty
        engagement = verify_layer_engagement(face, [operation], {tool["id"]: tool}, .05/32, lambda: False)[0]
        checks.append({"id": "entire_capsule_in_hole" if expected_air else "unroughed_material_entry",
                       "expected_air": expected_air, "production_sweep_empty": empty,
                       "production_engagement_passed": engagement,
                       "passed": empty == expected_air and engagement == expected_air})
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("native_run", type=Path)
    args = parser.parse_args()
    input_path = args.native_run/"create/expected.json"
    native = json.loads(input_path.read_text())
    run_id = "stock-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = PROJECT/"tests/oracles/test-runs"/run_id
    directory.mkdir(parents=True)
    extra_sources = [Path(__file__), PROJECT/"tests/oracles/stock.py", PROJECT/"tests/native/prism_capture.py"]
    def snapshot():
        return {**source_snapshot(), **{str(p.relative_to(PROJECT)): digest(p) for p in extra_sources}}
    baseline, started = snapshot(), time.monotonic()
    capture = outline_capture_checks(native)
    cases = []
    for kind in ("native_concave_hole", "native_annulus", "absent_collar"):
        case = positive(kind, native, directory/kind)
        cases.append(case)
        print(json.dumps({"case": kind, "passed": case["passed"], "errors": case["errors"]}), flush=True)
    negative = missing_stock_controls(native, directory)
    air = air_engagement_controls(native)
    stable = baseline == snapshot()
    report = {"schema_version": 1, "run_id": run_id, "completed_utc": datetime.now(timezone.utc).isoformat(),
              "passed": stable and all(c["passed"] for c in capture+cases+negative+air),
              "scope": "Actual native outlines into new solver fixtures; scalar stock/cutter probes, capture chord bounds and finite preview probes. Not GUI completion or an independent whole-surface certificate.",
              "source_sha256": baseline, "source_snapshot_stable": stable,
              "input_native_run": str(args.native_run), "input_expected_sha256": digest(input_path),
              "input_report_sha256": digest(args.native_run/"report.json"),
              "dependencies": dependency_versions(), "python": sys.version, "elapsed_s": time.monotonic()-started,
              "capture": capture, "positive": cases, "missing_stock_controls": negative, "air_controls": air}
    receipt = directory/"audit.json"
    receipt.write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps({"passed": report["passed"], "evidence": str(receipt)}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
