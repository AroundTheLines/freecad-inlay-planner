"""Run the independent OCCT small-case suite in an isolated native process."""

from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
from tools.run_native import launch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--actual-flat-directory", type=Path)
    args = parser.parse_args()
    actual = str(args.actual_flat_directory.resolve()) if args.actual_flat_directory else None
    output = PROJECT / "tests/oracles/test-runs" / (
        "native-occt-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"))
    output.mkdir(parents=True)
    macro = output / "run.FCMacro"
    macro.write_text(
        "import sys\nfrom PySide6 import QtCore\n"
        "sys.path.insert(0, "+repr(str(PROJECT/"tests"))+")\n"
        "sys.path.insert(0, "+repr(str(PROJECT/"tests/native"))+")\n"
        "from native_support import run_record\nfrom oracles.native_occt import run_suite\n"
        "def run(out):\n    result=run_suite(out, "+repr(actual)+")\n"
        "    if result['status'] != 'passed':\n        raise AssertionError(result.get('error', 'OCC checks failed'))\n"
        "    return result\nQtCore.QTimer.singleShot(400, lambda: run_record(run))\n")
    receipt = {"run_id": str(uuid.uuid4()), "status": "failed", "scope": "native OCCT only",
               "sources_sha256": {str(p.relative_to(PROJECT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted((PROJECT/"tests/oracles").glob("*.py"))}}
    try:
        result = launch(macro, output, receipt["run_id"], "independent-occt", PROJECT, timeout=150)
        receipt.update(status="passed", checks=len(result["checks"]))
    except BaseException as error:
        receipt["error"] = str(error)
        raise
    finally:
        (output/"runner.json").write_text(json.dumps(receipt, indent=2))
        print(json.dumps({"status": receipt["status"], "evidence": str(output/"runner.json")}))


if __name__ == "__main__":
    main()
