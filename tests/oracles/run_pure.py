"""Run the independent test suite and retain source-bound evidence.

Run with the test environment's Python. A changed source snapshot makes the
receipt inconclusive even if pytest succeeds. This never promotes GUI cases.
"""

from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


PROJECT = Path(__file__).resolve().parents[2]


def snapshot():
    paths = [PROJECT / "tests/test_independent_geometry.py"]
    paths += list((PROJECT / "tests/oracles").glob("*.py"))
    paths += list((PROJECT / "inlay_planner").rglob("*.py"))
    paths += list((PROJECT / "examples").glob("*"))
    paths += [PROJECT / "requirements-worker.txt", PROJECT / "requirements-test.txt"]
    return {str(path.relative_to(PROJECT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths) if path.is_file()}


def main():
    run_id = "pure-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = PROJECT / "tests/oracles/test-runs" / run_id
    output.mkdir(parents=True)
    sources = snapshot()
    command = [sys.executable, "-m", "pytest", "tests/test_independent_geometry.py",
               "-q", "--junitxml=" + str(output / "pytest.xml")]
    completed = subprocess.run(command, cwd=PROJECT, capture_output=True, text=True)
    (output / "stdout.log").write_text(completed.stdout)
    (output / "stderr.log").write_text(completed.stderr)
    stable = sources == snapshot()
    counts = {}
    if (output / "pytest.xml").is_file():
        suites = ET.parse(output / "pytest.xml").getroot().iter("testsuite")
        for suite in suites:
            for key in ("tests", "failures", "errors", "skipped"):
                counts[key] = counts.get(key, 0) + int(suite.get(key, 0))
    report = {"schema_version": 1, "run_id": run_id,
              "completed_utc": datetime.now(timezone.utc).isoformat(),
              "status": "passed" if completed.returncode == 0 and stable else "failed",
              "scope": "Independent pure tests and actual SVG importer; no GUI or release claim",
              "command": command, "exit_code": completed.returncode,
              "python": sys.version, "source_snapshot_stable": stable,
              "dependencies": {name: version(name) for name in (
                  "numpy", "shapely", "svg.path", "defusedxml", "pytest", "hypothesis", "psutil")},
              "source_sha256": sources, "counts": counts,
              "artifacts_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                   for path in sorted(output.iterdir()) if path.is_file()}}
    receipt = output / "receipt.json"
    receipt.write_text(json.dumps(report, indent=2))
    print(json.dumps({"status": report["status"], "counts": counts, "evidence": str(receipt)}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
