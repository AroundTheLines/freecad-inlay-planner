"""Independent negative controls applied to actual asymmetric solver output."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from oracles.fixtures import seal, small_pair
from oracles.audit_solver import dependency_versions, source_snapshot
from oracles.motion import (Cutter, assert_air_travel, assert_depth_probes,
                            check_result_contract, paired_probe_gap)


def main():
    sources = source_snapshot()
    sources["tests/oracles/audit_mutations.py"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    from inlay_planner.solver import solve  # Only the actual output under test.

    spec = small_pair("flat")
    spec["artwork"]["regions"][0]["polygons"][0]["outer"] = [
        [3, 5], [9, 5], [9, 13], [3, 13], [3, 5]]
    spec = seal(spec)
    result = solve(spec)
    if result["status"] == "needs_acceptance":
        spec["accepted_proposal_hash"] = result["proposal"]["hash"]
        spec = seal(spec)
        result = solve(spec)
    run_id = "mutations-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = PROJECT / "tests/oracles/test-runs" / run_id
    output.mkdir(parents=True)
    (output / "spec.json").write_text(json.dumps(spec, indent=2))
    (output / "result.json").write_text(json.dumps(result))
    errors, checks = check_result_contract(spec, result), []
    operations = {op["face"]: op for op in result["operations"]}
    tool = Cutter("flat", 1, 5)
    board, plug = operations["board"]["moves"], operations["plug"]["moves"]

    def gap(point, board_moves=board, plug_moves=plug):
        return paired_probe_gap(point, board_moves, plug_moves, tool, tool,
                                spec["faces"]["board"], spec["faces"]["plug"], .8)

    for point, expected in (((6, 9), .2), ((14, 9), .5)):
        observed = gap(point)
        passed = observed.certified and abs(observed.lower-expected) < 1e-6 and abs(observed.upper-expected) < 1e-6
        checks.append({"name": "nominal_assembly", "point": point, "expected_mm": expected,
                       "interval_mm": [observed.lower, observed.upper], "passed": passed})
    assert_depth_probes(board, tool, [(6, 9, 1), (2, 9, 0)])

    missing = [dict(move, z=max(move["z"], -.5)) for move in board]
    observed = gap((6, 9), board_moves=missing)
    checks.append({"name": "missing_finishing_depth", "interval_mm": [observed.lower, observed.upper],
                   "passed": observed.certified and observed.upper < -.29})
    mirrored = [dict(move, x=20-move["x"]) for move in plug]
    observed = gap((14, 9), plug_moves=mirrored)
    checks.append({"name": "wrong_physical_mirror", "interval_mm": [observed.lower, observed.upper],
                   "passed": observed.certified and observed.upper < -.79})

    def must_reject(name, function):
        try:
            function()
        except AssertionError as error:
            checks.append({"name": name, "passed": True, "detected": str(error)})
        else:
            checks.append({"name": name, "passed": False, "detected": None})

    must_reject("wrong_cutter_radius", lambda: assert_depth_probes(
        board, Cutter("flat", 3, 5), [(2, 9, 0)]))
    unsafe = deepcopy(board)
    unsafe[0]["z"] = unsafe[1]["z"] = -.5
    must_reject("unsafe_lateral_link", lambda: assert_air_travel(unsafe))
    wrong_feed = deepcopy(result)
    for move in wrong_feed["operations"][0]["moves"]:
        move["feed_mm_min"] *= 60
    detected = check_result_contract(spec, wrong_feed)
    checks.append({"name": "native_feed_factor60", "passed": any("time" in x.lower() for x in detected),
                   "detected": detected})
    errors.extend(check["name"] for check in checks if not check["passed"])
    final_sources = source_snapshot()
    final_sources["tests/oracles/audit_mutations.py"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    source_stable = sources == final_sources
    if not source_stable:
        errors.append("Executed source snapshot changed during this audit")
    report = {"schema_version": 1, "run_id": run_id,
              "completed_utc": datetime.now(timezone.utc).isoformat(),
              "status": "passed" if not errors else "failed", "checks": checks, "errors": errors,
              "scope": "Actual asymmetric flat solver output and deliberately mutated motions; finite probes, not GUI or whole-surface proof",
              "source_sha256": sources, "source_snapshot_stable": source_stable,
              "python": sys.version, "dependencies": dependency_versions(),
              "artifacts_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                   for path in output.iterdir() if path.is_file()}}
    receipt = output / "audit.json"
    receipt.write_text(json.dumps(report, indent=2))
    print(json.dumps({"status": report["status"], "errors": errors, "evidence": str(receipt)}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
