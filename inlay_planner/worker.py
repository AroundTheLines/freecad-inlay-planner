"""One offline calculation process, launched with FreeCAD's bundled Python -I."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import resource
import signal
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

_cancelled = False


def cancel(signum=None, frame=None):
    global _cancelled
    _cancelled = True


def progress(value):
    print(json.dumps({"type": "progress", "value": value}, allow_nan=False), flush=True)


def peak_rss():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value*1024)


def memory_guard(done):
    while not done.wait(.2):
        if peak_rss() > 768*1024*1024:
            print(json.dumps({"type": "error", "message": "Calculation exceeded its 768 MiB memory budget. Reduce artwork complexity and recalculate."}), flush=True)
            os._exit(74)


def run(request):
    from inlay_planner.schema import fingerprint, finite_tree
    finite_tree(request)
    if request.get("schema_version") != 1:
        raise ValueError("Unsupported request version; reopen the inlay task.")
    action = request["action"]
    if action == "import_svg":
        from inlay_planner.svg_input import parse_svg
        return parse_svg(request["text"], request["tolerance"])
    if action != "solve":
        raise ValueError("Unknown calculation request.")
    spec = request["spec"]
    if spec.get("input_hash") != fingerprint(spec):
        raise ValueError("The calculation input changed or is incomplete; recalculate.")
    excluded = set(spec.get("excluded_region_ids", []))
    unresolved = [r for r in spec["artwork"]["regions"]
                  if r.get("supported") is False and r["id"] not in excluded]
    if unresolved:
        return {"schema_version": 1, "input_hash": spec["input_hash"], "revision": spec["revision"],
                "status": "blocked", "operations": [], "per_face_counts": {}, "preview": {},
                "verification": {"status": "blocked"}, "proposal": None,
                "diagnostics": [{"code": "SVG_UNSUPPORTED", "severity": "error",
                    "region_ids": [r["id"]], "polygons": [],
                    "message": "Explicitly exclude unsupported artwork or replace it with a filled outline: "+r["id"]}
                    for r in unresolved]}
    from inlay_planner.solver import solve
    result = solve(spec, progress=progress, cancelled=lambda: _cancelled)
    if _cancelled:
        result["status"] = "cancelled"
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    request_path, result_path = Path(args.request).resolve(), Path(args.result).resolve()
    if request_path.parent != result_path.parent:
        raise ValueError("Result must be inside the calculation directory.")
    if request_path.stat().st_size > 64*1024*1024:
        raise ValueError("Calculation request exceeds the bounded working set.")
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    done = threading.Event()
    threading.Thread(target=memory_guard, args=(done,), daemon=True).start()
    started = time.monotonic()
    status = 0
    request = {}
    try:
        request = json.loads(request_path.read_text())
        fault = os.environ.get("INLAY_TEST_FAULT", "") if os.environ.get("INLAY_TEST_MODE") == "1" else ""
        if fault == "crash":
            os._exit(73)
        if fault == "dependency":
            raise ImportError("Injected unavailable private geometry dependency")
        if fault == "memory":
            raise MemoryError("Injected bounded memory exhaustion")
        if fault == "timeout":
            while not _cancelled:
                time.sleep(.02)
        result = run(request)
        result.setdefault("resource_stats", {}).update(peak_worker_rss=peak_rss(),
                                                       elapsed_s=time.monotonic()-started)
        payload = {"run_id": request.get("run_id"), "ok": True, "result": result}
        if fault == "hash" and "input_hash" in result:
            result["input_hash"] = "injected-stale-result"
        if fault == "version":
            result["schema_version"] = 999
        if fault == "partial":
            result_path.with_suffix(".tmp").write_text('{"incomplete":')
            os._exit(75)
    except BaseException as exc:
        payload = {"run_id": request.get("run_id"), "ok": False,
                   "error": str(exc), "traceback": traceback.format_exc()}
        status = 1
    finally:
        done.set()
    tmp = result_path.with_suffix(".tmp")
    with tmp.open("w") as stream:
        json.dump(payload, stream, allow_nan=False, separators=(",", ":"))
    tmp.replace(result_path)
    print(json.dumps({"type": "complete", "ok": payload["ok"]}), flush=True)
    return status


if __name__ == "__main__":
    raise SystemExit(main())

