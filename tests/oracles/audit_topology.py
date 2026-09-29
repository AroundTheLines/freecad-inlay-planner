"""Small actual-solver hole/disconnected/mixed-tool checks with independent sweeps."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from oracles.fixtures import seal, small_pair
from oracles.audit_solver import dependency_versions, source_snapshot
from oracles.motion import (Cutter, apply_affine, assert_air_travel, assert_fixture_reach,
                            assert_probe_stepdown, assert_sweep_bounds,
                            check_result_contract, operations_removal)


def fixture(kind):
    spec = small_pair("flat")
    if kind == "holes":
        polygons = [{"outer": [[3, 3], [17, 3], [17, 17], [3, 17], [3, 3]],
                     "holes": [[[8, 8], [12, 8], [12, 12], [8, 12], [8, 8]]]}]
        probes = [((5.5, 10), .2), ((14.5, 10), .2), ((10, 5.5), .2),
                  ((10, 14.5), .2), ((10, 10), .5), ((1.5, 10), .5)]
    else:
        polygons = [{"outer": [[3, 4], [8, 4], [8, 10], [3, 10], [3, 4]], "holes": []},
                    {"outer": [[12, 11], [17, 11], [17, 17], [12, 17], [12, 11]], "holes": []}]
        probes = [((5.5, 7), .2), ((14.5, 14), .2), ((10, 10), .5), ((1.5, 10), .5)]
    spec["artwork"]["regions"][0]["polygons"] = polygons
    large = deepcopy(spec["tools"][0])
    large.update(id="oracle-large-flat", number=2, name="4 mm clearing", diameter=4., stepover=1.6)
    spec["tools"].insert(0, large)
    spec["selected_tool_ids"].insert(0, large["id"])
    if kind == "mixed_v":
        spec["style"] = "v"
        finish = spec["tools"][1]
        finish.update(shape="v", name="Finite-tip V finishing", diameter=4.,
                      included_angle_deg=90., tip_diameter=.1)
        large.update(diameter=2., stepover=.8, name="2 mm flat clearing")
    for face in spec["faces"].values():
        face["limits"] = {"operations": 2, "tool_changes": 1}
    return seal(spec), probes


def audit(kind, output, baseline):
    started = time.monotonic()
    from inlay_planner.solver import solve
    spec, probes = fixture(kind)
    result = solve(spec)
    initial = result["status"]
    if initial == "needs_acceptance":
        spec["accepted_proposal_hash"] = result["proposal"]["hash"]
        spec = seal(spec)
        result = solve(spec)
    errors = check_result_contract(spec, result)
    cutters = {tool["id"]: Cutter.from_snapshot(tool) for tool in spec["tools"]}
    by_face = {name: [op for op in result["operations"] if op["face"] == name]
               for name in ("board", "plug")}
    measures = []
    for name, operations in by_face.items():
        face = spec["faces"][name]
        local_points = [apply_affine(point, face["design_to_face"]) for point, _ in probes]
        try:
            assert_probe_stepdown(local_points, operations, cutters, face["origin"])
        except AssertionError as error:
            errors.append(f"{name}: {error}")
    for operation in result["operations"]:
        face, cutter = spec["faces"][operation["face"]], cutters[operation["tool_id"]]
        try:
            assert_air_travel(operation["moves"], face["origin"])
            assert_fixture_reach(operation["moves"], cutter, face["stock"][2], face["origin"])
            assert_sweep_bounds(operation["moves"], cutter, face["bounds"], face["origin"],
                                stock_bounds=(0, 0, *face["stock"][:2]))
        except AssertionError as error:
            errors.append(f"{operation['id']}: {error}")
    for point, expected in probes:
        intervals = []
        for name in ("board", "plug"):
            face = spec["faces"][name]
            intervals.append(operations_removal(apply_affine(point, face["design_to_face"]),
                                               by_face[name], cutters, face["origin"]))
        lower, upper = (sum(getattr(value, bound) for value in intervals)-.8
                        for bound in ("lower", "upper"))
        passed = all(value.certified for value in intervals) and max(abs(lower-expected), abs(upper-expected)) < 1e-5
        measures.append({"canonical_xy": point, "gap_mm": [lower, upper], "expected_mm": expected,
                         "passed": passed})
        if not passed:
            errors.append(f"Assembly gap {point} expected {expected}, found [{lower}, {upper}]")
    stable = baseline == source_snapshot()
    if not stable:
        errors.append("Executed source snapshot changed during this audit")
    output.mkdir(parents=True)
    for name, value in (("spec", spec), ("result", result)):
        (output / (name+".json")).write_text(json.dumps(value, allow_nan=False))
    report = {"schema_version": 1, "run_id": output.name, "case": kind,
              "completed_utc": datetime.now(timezone.utc).isoformat(),
              "status": "passed" if not errors else "failed", "errors": errors,
              "initial_status": initial, "accepted_status": result["status"],
              "scope": "Actual topology/mixed-tool fixture, complete command invariants and finite assembly probes; not GUI or independent whole-surface proof",
              "source_sha256": baseline, "source_snapshot_stable": stable,
              "python": sys.version, "dependencies": dependency_versions(),
              "elapsed_s": time.monotonic()-started,
              "oracle_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "probes": measures, "counts": result["per_face_counts"],
              "tool_uses": {name: [op["tool_id"] for op in values] for name, values in by_face.items()},
              "diagnostics": result["diagnostics"],
              "artifacts_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                   for path in output.iterdir() if path.is_file()}}
    receipt = output / "audit.json"
    receipt.write_text(json.dumps(report, indent=2))
    print(json.dumps({"case": kind, "status": report["status"], "errors": errors,
                      "evidence": str(receipt)}), flush=True)
    return report


def main():
    run_id = "topology-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    baseline = source_snapshot()
    failed = False
    for kind in ("holes", "disconnected", "mixed_v"):
        report = audit(kind, PROJECT / "tests/oracles/test-runs" / (run_id+"-"+kind), baseline)
        failed |= report["status"] != "passed"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
