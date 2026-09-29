"""Compare native FreeCAD command snapshots with independently replayed exports."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "tests"))
from oracles.gcode import parse_program


def expected_from_snapshot(operations):
    position = [None, None, None]
    feed = spindle = None
    expected = []
    for operation in operations:
        for command in operation["commands"]:
            name, params = command["name"], command["parameters"]
            if "S" in params:
                spindle = Decimal(str(params["S"]))
            if "F" in params:
                # Native FreeCAD Path velocity quantities are mm/s.
                feed = Decimal(str(params["F"])) * 60
            if name in ("G0", "G1") and any(k in params for k in "XYZ"):
                for index, axis in enumerate("XYZ"):
                    if axis in params:
                        position[index] = Decimal(str(params[axis]))
                expected.append({"kind": "rapid" if name == "G0" else "feed",
                                 "end": tuple(position), "feed": feed, "spindle": spindle,
                                 "tool": operation["tool"]})
    return expected


def compare(snapshot, text, tolerance="0.000000001", feed_tolerance="0.000000001"):
    expected = expected_from_snapshot(snapshot)
    actual = parse_program(text)
    errors = []
    limit = Decimal(tolerance)
    feed_limit = Decimal(feed_tolerance)
    if not limit.is_finite() or not feed_limit.is_finite() or min(limit, feed_limit) < 0:
        raise ValueError("Comparison tolerances must be finite and nonnegative")
    if len(expected) != len(actual.motions):
        errors.append(f"Motion count {len(actual.motions)} differs from native {len(expected)}")
    for index, (a, b) in enumerate(zip(expected, actual.motions)):
        if a["kind"] != b.kind:
            errors.append(f"Motion {index}: movement kind differs")
        for axis, wanted, found in zip("XYZ", a["end"], b.end):
            if wanted is None or found is None:
                if wanted != found:
                    errors.append(f"Motion {index}: {axis} unknown-position mismatch")
            elif abs(wanted-found) > limit:
                errors.append(f"Motion {index}: {axis} expected {wanted}, got {found}")
        if a["kind"] == "feed":
            wanted, found = a["feed"], b.feed_mm_min
            if ((wanted is None or found is None) and wanted != found
                    or wanted is not None and found is not None and abs(wanted-found) > feed_limit):
                errors.append(f"Motion {index}: native feed {wanted} differs from {found}")
        if a["spindle"] != b.spindle_rpm or b.spindle_mode != "M3":
            errors.append(f"Motion {index}: spindle setting differs")
        if a["tool"] != b.tool or b.length_offset != b.tool:
            errors.append(f"Motion {index}: tool or length register differs")
        if b.path_control != "G61":
            errors.append(f"Motion {index}: exact-path mode is absent")
    if actual.motions and any(x is not None for x in actual.motions[0].end[:2]):
        errors.append("Initial command does not establish Z alone before XY")
    transitions = sum(a["tool"] != b["tool"] for a, b in zip(snapshot, snapshot[1:]))
    if actual.tool_changes != transitions:
        errors.append("Exported physical tool-change count differs")
    return {"passed": not errors, "errors": errors, "motion_count": len(actual.motions),
            "physical_tool_changes": actual.tool_changes,
            "endpoint_tolerance_mm": str(limit), "feed_tolerance_mm_min": str(feed_limit),
            "scope": "Native-to-export semantics only; not a GUI/process-completion or geometric certificate"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("native_run", type=Path)
    args = parser.parse_args()
    sources = [Path(__file__), PROJECT / "tests/oracles/gcode.py"]
    source_hashes = {str(path.relative_to(PROJECT)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in sources}
    expected_path = args.native_run / "reopen/expected.json"
    snapshot = json.loads(expected_path.read_text())["snapshot"]
    report = {"schema_version": 1,
              "run_id": "export-replay-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"),
              "completed_utc": datetime.now(timezone.utc).isoformat(),
              "native_run": str(args.native_run.resolve()), "checks": [],
              "source_sha256": source_hashes,
              "expected_sha256": hashlib.sha256(expected_path.read_bytes()).hexdigest()}
    for face in ("board", "plug"):
        path = args.native_run / f"export/{face}_metric_9.ngc"
        check = compare(snapshot[face], path.read_text())
        check.update(file=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), expected_pass=True)
        report["checks"].append(check)
    for suffix in ("metric_3", "inch_9"):
        path = args.native_run / f"export/board_{suffix}.ngc"
        if path.exists():
            check = compare(snapshot["board"], path.read_text())
            check.update(file=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), expected_pass=False)
            report["checks"].append(check)
    report["source_snapshot_stable"] = source_hashes == {
        str(path.relative_to(PROJECT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    report["passed"] = report["source_snapshot_stable"] and all(
        c["passed"] == c["expected_pass"] for c in report["checks"])
    output = PROJECT / "tests/oracles/test-runs" / ("export-"+args.native_run.name+".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    print(json.dumps({"passed": report["passed"], "evidence": str(output), "checks": report["checks"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
