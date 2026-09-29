"""Second process: no add-on or source data; native GUI edit and re-save."""
import json
import os
import sys
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui

from native_support import check, assert_native, paths_snapshot, stock_snapshot, edit_custom


def run(out):
    check(not any(name.startswith("inlay_planner") for name in sys.modules), "Extension was loaded in absent-extension phase")
    source = Path(os.environ["INLAY_NATIVE_PREVIOUS"])
    expected = json.loads((source / "expected.json").read_text())
    check(not (source / "source.svg").exists(), "Source SVG still exists")
    doc = App.openDocument(str(source / "portable.FCStd"))
    jobs = expected["jobs"]
    check(stock_snapshot(doc, jobs) == expected["stocks"], "Exact stock or holes changed without the extension")
    initial = paths_snapshot(doc, jobs)
    precision_errors = []
    for side in ("board", "plug"):
        for before, after in zip(expected["snapshot"][side], initial[side]):
            check(before["gcode"] == after["gcode"], "Embedded Custom commands changed on restore")
            check(before["diameter"] == after["diameter"], "Tool dimensions changed without source library")
            check(before["tool_dimensions"] == after["tool_dimensions"], "Cutter envelope changed without source library")
            for command1, command2 in zip(before["commands"], after["commands"]):
                for axis in ("X", "Y", "Z"):
                    if axis in command1["parameters"]:
                        precision_errors.append(abs(command1["parameters"][axis] - command2["parameters"][axis]))
    check(max(precision_errors) <= .00000051, "Native restore lost more than expected six-decimal precision")
    for side in ("board", "plug"):
        for op in doc.getObject(jobs[side]).Operations.Group:
            op.touch()
    doc.recompute()
    restored = paths_snapshot(doc, jobs)
    check(restored == expected["snapshot"], "Custom recompute did not recover embedded full precision")
    op = doc.getObject(jobs["board"]).Operations.Group[0]
    replacement = "\n".join(op.Gcode).replace("F10", "F11")
    edit_custom(op, replacement)
    check(any(cmd.Parameters.get("F") == 11 for cmd in op.Path.Commands), "Native editor did not change feed")
    op.ToolController.HorizFeed = "900 mm/min"
    doc.recompute()
    check(any(cmd.Parameters.get("F") == 11 for cmd in op.Path.Commands), "ToolController falsely rewrote embedded feed")
    doc.saveAs(str(out / "edited.FCStd"))
    expected["snapshot"] = paths_snapshot(doc, jobs)
    (out / "expected.json").write_text(json.dumps(expected, indent=2))
    check(not any(name.startswith("inlay_planner") for name in sys.modules), "Opening native output imported extension")
    return {"checks": ["extension_absent_reopen", "svg_library_absent", "exact_stock_survives_reopen", "native_precision_measured", "native_recompute_recovers_custom_text",
                        "native_custom_gui_edit", "controller_feed_does_not_rewrite_stored_motion", "edited_resave"],
            "max_initial_restore_error_mm": max(precision_errors), "proxies": assert_native(doc), "artifact": str(out / "edited.FCStd")}
