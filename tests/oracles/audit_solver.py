"""Run real solver fixtures through independent command and material probes.

Run from the repository root with .venv/bin/python tests/oracles/audit_solver.py.
This records supporting software evidence; it cannot pass a GUI ledger row.
"""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import sys
import time


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

from oracles.fixtures import seal, small_pair
from oracles.motion import (Cutter, apply_affine, assert_air_travel, assert_fixture_reach,
                            assert_probe_stepdown, assert_sweep_bounds,
                            check_result_contract, paired_probe_gap)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_snapshot():
    production = ("__init__", "geometry", "motion", "motion_storage", "schema", "solver", "verify")
    oracle = ("__init__", "audit_solver", "fixtures", "motion")
    paths = [PROJECT / "inlay_planner" / (name+".py") for name in production]
    paths += [PROJECT / "tests/oracles" / (name+".py") for name in oracle]
    paths += [PROJECT / "requirements-worker.txt", PROJECT / "requirements-test.txt"]
    return {str(path.relative_to(PROJECT)): digest(path) for path in paths}


def dependency_versions():
    return {name: version(name) for name in ("numpy", "shapely", "svg.path", "defusedxml")}


def audit(style, directory, baseline_sources=None):
    source_hashes = baseline_sources or source_snapshot()
    from inlay_planner.solver import solve

    directory.mkdir(parents=True, exist_ok=True)
    spec = small_pair(style)
    started = time.monotonic()
    result = solve(spec)
    initial_status = result["status"]
    if result["status"] == "needs_acceptance":
        spec["accepted_proposal_hash"] = result["proposal"]["hash"]
        spec = seal(spec)
        result = solve(spec)
    errors = check_result_contract(spec, result)
    cutters = {t["id"]: Cutter.from_snapshot(t) for t in spec["tools"]}
    for op in result["operations"]:
        face = spec["faces"][op["face"]]
        try:
            assert_air_travel(op["moves"], face["origin"])
            assert_fixture_reach(op["moves"], cutters[op["tool_id"]], face["stock"][2], face["origin"])
            assert_sweep_bounds(op["moves"], cutters[op["tool_id"]], face["bounds"],
                                face["origin"], stock_bounds=(0, 0, *face["stock"][:2]))
        except AssertionError as error:
            errors.append(f"{op['id']}: {error}")
    operations = {o["face"]: o for o in result["operations"]}
    probes = []
    if set(operations) == {"board", "plug"}:
        size = spec["faces"]["board"]["stock"][0]
        points = [(size*(i+.5)/5, size*(j+.5)/5) for i in range(5) for j in range(5)]
        centre = (size/2, size/2)
        outside = (4, 10) if style == "flat" else (1.5, 6)
        points.extend([centre, outside])
        for name, operation in operations.items():
            face = spec["faces"][name]
            local_points = [apply_affine(point, face["design_to_face"]) for point in points]
            try:
                assert_probe_stepdown(local_points, [operation], cutters, face["origin"])
            except AssertionError as error:
                errors.append(f"{name}: {error}")
        for point in points:
            gap = paired_probe_gap(point, operations["board"]["moves"], operations["plug"]["moves"],
                cutters[operations["board"]["tool_id"]], cutters[operations["plug"]["tool_id"]],
                spec["faces"]["board"], spec["faces"]["plug"], 0.8, tolerance=1e-6)
            probes.append({"canonical_xy": point, "gap_mm": asdict(gap)})
            if not gap.certified or gap.lower < -1e-6:
                errors.append(f"Unresolved or interfering assembly probe at {point}: {gap}")
        for point, nominal in ((centre, .2), (outside, .5)):
            values = next(x["gap_mm"] for x in probes if tuple(x["canonical_xy"]) == point)
            if not (math.isclose(values["lower"], nominal, abs_tol=1e-5)
                    and math.isclose(values["upper"], nominal, abs_tol=1e-5)):
                errors.append(f"Known flat reference plane at {point} differs from {nominal}: {values}")
    else:
        errors.append("Known fixture did not produce one operation for each face")
    spec_path, result_path = directory / "spec.json", directory / "result.json"
    spec_path.write_text(json.dumps(spec, indent=2, allow_nan=False))
    result_path.write_text(json.dumps(result, allow_nan=False))
    source_stable = source_hashes == source_snapshot()
    if not source_stable:
        errors.append("Executed source snapshot changed during this audit")
    report = {"schema_version": 1, "run_id": directory.name,
              "completed_utc": datetime.now(timezone.utc).isoformat(), "style": style,
              "status": "passed" if not errors else "failed", "errors": errors,
              "scope": "Actual solver, command invariants, conservative rectangular stock bounds, 27 sampled engagement/assembly probes; not GUI or whole-surface independent proof",
              "initial_status": initial_status, "accepted_status": result["status"],
              "elapsed_s": time.monotonic()-started, "python": sys.version,
              "dependencies": dependency_versions(),
              "source_sha256": source_hashes, "source_snapshot_stable": source_stable,
              "spec_sha256": digest(spec_path),
              "result_sha256": digest(result_path), "probes": probes,
              "reported_realization": (result.get("proposal") or {}).get("realization"),
              "motion_counts": {o["face"]: len(o["moves"]) for o in result["operations"]}}
    (directory / "audit.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--styles", nargs="+", choices=("flat", "v", "bull"), default=["flat"])
    args = parser.parse_args()
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    baseline_sources = source_snapshot()
    failed = False
    for style in args.styles:
        print(f"Auditing actual {style} solver output", flush=True)
        output = PROJECT / "tests/oracles/test-runs" / (run+"-"+style)
        report = audit(style, output, baseline_sources)
        print(json.dumps({"style": style, "status": report["status"], "errors": report["errors"],
                          "evidence": str(output / "audit.json")}), flush=True)
        failed |= report["status"] != "passed"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
