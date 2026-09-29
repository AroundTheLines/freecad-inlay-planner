#!/usr/bin/env python3
"""Run real FreeCAD GUI portability checks in three independent profiles."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid


def launch(macro, out, run_id, phase, repo, previous=None, timeout=120, app=Path("/Applications/FreeCAD.app")):
    resources = app / "Contents" / "Resources"
    profile = out / "profile"
    for suffix in ("home", "data", "temp"):
        (profile / suffix).mkdir(parents=True, exist_ok=True)
    env = {key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "DISPLAY") if key in os.environ}
    env.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", PREFIX=str(resources), PYTHONHOME=str(resources), PYTHONPATH=str(resources),
               LD_LIBRARY_PATH=str(resources / "lib"), SSL_CERT_FILE=str(resources / "ssl" / "cacert.pem"),
               GIT_SSL_CAINFO=str(resources / "ssl" / "cacert.pem"), MallocNanoZone="0",
               FONTCONFIG_FILE="/etc/fonts/fonts.conf", FONTCONFIG_PATH="/etc/fonts",
               FREECAD_USER_HOME=str(profile / "home"), FREECAD_USER_DATA=str(profile / "data"), FREECAD_USER_TEMP=str(profile / "temp"),
               INLAY_NATIVE_OUT=str(out), INLAY_NATIVE_RUN_ID=run_id, INLAY_NATIVE_PHASE=phase,
               INLAY_NATIVE_REPO=str(repo), INLAY_NATIVE_PREVIOUS=str(previous or ""))
    command = [str(resources / "bin" / "freecad"), "--user-cfg", str(profile / "user.cfg"),
               "--system-cfg", str(profile / "system.cfg"), "--log-file", str(out / "freecad.log"), str(macro)]
    with (out / "stdout.log").open("w") as log:
        completed = subprocess.run(command, env=env, cwd=out, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    result_path = out / "result.json"
    if not result_path.exists():
        raise RuntimeError(f"{phase}: FreeCAD exited {completed.returncode} without a completion record; see {out}")
    result = json.loads(result_path.read_text())
    if result.get("run_id") != run_id or result.get("phase") != phase or not result.get("ok"):
        raise RuntimeError(f"{phase}: {result.get('error', 'invalid completion record')}")
    if completed.returncode:
        raise RuntimeError(f"{phase}: process failed after reporting completion ({completed.returncode})")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--app", type=Path, default=Path("/Applications/FreeCAD.app"))
    parser.add_argument("--timeout", type=int, default=120)
    options = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    run_id = str(uuid.uuid4())
    root = (options.out or repo / "artifacts" / ("native-" + time.strftime("%Y%m%d-%H%M%S"))).resolve()
    root.mkdir(parents=True, exist_ok=False)
    # Copies, not checkout imports, supply test utilities to the absent-addon phases.
    harness = root / "harness"
    harness.mkdir()
    for source in (repo / "tests" / "native").glob("*.py"):
        shutil.copy2(source, harness / source.name)
    report = {"schema_version": 1, "run_id": run_id, "ok": False, "phases": [], "source_hashes": {}}
    for path in [repo / "inlay_planner" / "native.py", *sorted(harness.glob("*.py"))]:
        report["source_hashes"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    previous = None
    try:
        for phase in ("create", "reopen", "export"):
            out = root / phase
            out.mkdir()
            macro = out / "run.FCMacro"
            macro.write_text("import sys\nfrom PySide6 import QtCore\nsys.path.insert(0, " + repr(str(harness)) + ")\n"
                             "from native_support import run_record\nfrom portability_" + phase + " import run\n"
                             "QtCore.QTimer.singleShot(400, lambda: run_record(run))\n")
            result = launch(macro, out, run_id, phase, repo, previous, options.timeout, options.app)
            report["phases"].append(result)
            print(f"{phase}: passed ({len(result['checks'])} checks)", flush=True)
            if phase == "create":
                (out / "source.svg").unlink()
                shutil.rmtree(out / "profile")
            previous = out
        report["ok"] = True
    except BaseException as error:
        report["error"] = str(error)
        raise
    finally:
        (root / "report.json").write_text(json.dumps(report, indent=2))
        print(f"Evidence: {root / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
