"""Third process: durable edit and ordinary FreeCAD LinuxCNC postprocessing."""
import json
import os
import sys
from pathlib import Path

import FreeCAD as App

from native_support import check, paths_snapshot, stock_snapshot, assert_native, export_jobs, parse_post


def run(out):
    check(not any(name.startswith("inlay_planner") for name in sys.modules), "Extension loaded in final phase")
    source = Path(os.environ["INLAY_NATIVE_PREVIOUS"])
    expected = json.loads((source / "expected.json").read_text())
    doc = App.openDocument(str(source / "edited.FCStd"))
    jobs = expected["jobs"]
    for side in ("board", "plug"):
        for op in doc.getObject(jobs[side]).Operations.Group:
            op.touch()
    doc.recompute()
    check(stock_snapshot(doc, jobs) == expected["stocks"], "Native recompute changed exact stock or holes")
    check(paths_snapshot(doc, jobs) == expected["snapshot"], "Native GUI edit was not durable")
    outputs = export_jobs(doc, jobs, out)
    reports = {}
    for side in ("board", "plug"):
        replay = parse_post(Path(outputs[side]).read_text())
        check(replay["moves"][0]["supplied_axes"] == ["Z"], "Post moved XY before clearance")
        check(all(move["path_control"] == "G61" for move in replay["moves"]), "Exact-path mode is missing")
        check(replay["tool_changes"] == expected["result"]["per_face_counts"][side]["tool_changes"], "Physical tool transitions changed")
        check(replay["load_count"] == replay["tool_changes"] + 1, "Same-tool spindle/feed change introduced a redundant M6")
        check(any(abs(move["position"].get("X", 0) - 20.123456789) < 1e-9 for move in replay["moves"]), "Qualified metric precision lost endpoint")
        if side == "board":
            check(any(move["feed"] == 660 and move["spindle"] == 12000 for move in replay["moves"] if move["kind"] == "feed"), "Edited native F11 did not export as660 mm/min")
            check(any(move["spindle"] == 13000 for move in replay["moves"]), "Same-tool spindle override was lost")
        reports[side] = {"loads": replay["load_count"], "tool_changes": replay["tool_changes"], "moves": len(replay["moves"])}
    low_precision = export_jobs(doc, jobs, out, precision=3)
    low = parse_post(Path(low_precision["board"]).read_text())
    check(not any(abs(move["position"].get("X", 0) - 20.123456789) < 1e-6 for move in low["moves"]), "Low-precision negative control was not detected")
    inches = export_jobs(doc, jobs, out, inches=True, precision=9)
    imperial = parse_post(Path(inches["board"]).read_text())
    check(any(move["feed"] is not None and abs(move["feed"] - 660) < .003 for move in imperial["moves"]), "Imperial native feed conversion failed")
    check(not any(abs(move["position"].get("X", 0) - 20.123456789) < 1e-6 for move in imperial["moves"]), "Legacy inch four-decimal limitation was not detected")
    unsafe = export_jobs(doc, jobs, out, exact=False)
    blending = parse_post(Path(unsafe["board"]).read_text())
    check(any(move["path_control"] != "G61" for move in blending["moves"]), "G64 negative control undetected")
    # Retain qualified files last so the named metric artifacts are the good ones.
    outputs = export_jobs(doc, jobs, out)
    return {"checks": ["native_edit_survives_second_reopen", "native_linuxcnc_export", "factor60_feed_conversion", "per_use_spindle",
                        "physical_change_counts", "z_before_xy", "exact_path_mode", "metric_precision", "low_precision_rejected",
                        "imperial_feed_conversion", "imperial_precision_limit_detected", "unqualified_g64_detected"],
            "outputs": outputs, "replay": reports, "proxies": assert_native(doc)}
