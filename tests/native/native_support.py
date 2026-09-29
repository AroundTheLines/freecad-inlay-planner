"""GUI-process test utilities; intentionally independent of the add-on."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui
from PySide6 import QtCore, QtTest, QtWidgets


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def wait_until(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        if predicate():
            return
        QtTest.QTest.qWait(20)
    raise AssertionError("Timed out waiting for native GUI state")


def run_record(callback):
    out = Path(os.environ["INLAY_NATIVE_OUT"])
    out.mkdir(parents=True, exist_ok=True)
    result = {"schema_version": 1, "run_id": os.environ["INLAY_NATIVE_RUN_ID"],
              "phase": os.environ["INLAY_NATIVE_PHASE"], "ok": False,
              "version": App.Version(), "python": sys.version, "qt": QtCore.qVersion()}
    try:
        # Shorten transient notification lifetime in this isolated test profile.
        # Quitting Cocoa with a live FreeCAD NotificationLabel crashes in Qt's
        # OpenGL thread-storage destructor in the shipped 1.1.3 runtime.
        notifications = App.ParamGet("User parameter:BaseApp/Preferences/NotificationArea")
        notifications.SetInt("NotificationTime", 1)
        notifications.SetInt("MinimumOnScreenTime", 0)
        result["notification_lifetime_s"] = 1
        Gui.activateWorkbench("CAMWorkbench")
        result.update(callback(out))
        result["ok"] = True
    except BaseException:
        result["error"] = traceback.format_exc()
    try:
        Gui.getMainWindow().grab().save(str(out / "screen.png"))
    except Exception:
        pass
    temporary = out / "result.tmp"
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False))
    temporary.replace(out / "result.json")
    for name in list(App.listDocuments()):
        App.closeDocument(name)
    def quit_cleanly():
        # FreeCAD's notification labels use deleteLater. Drain their Qt events
        # before Cocoa tears down the main thread's OpenGL context storage.
        QtWidgets.QApplication.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        QtWidgets.QApplication.instance().quit()
    QtCore.QTimer.singleShot(1750, quit_cleanly)


def assert_native(doc):
    proxies = set()
    for obj in doc.Objects:
        for proxy in (getattr(obj, "Proxy", None), getattr(obj.ViewObject, "Proxy", None)):
            if proxy is not None:
                module = type(proxy).__module__
                check(not module.startswith("inlay_planner"), f"Extension proxy persists: {module}")
                proxies.add(module)
        for name in obj.PropertiesList:
            check(name == "Proxy" or obj.getTypeIdOfProperty(name) != "App::PropertyPythonObject",
                  "Custom Python object data persisted: " + obj.Name + "." + name)
    return sorted(proxies)


def paths_snapshot(doc, jobs):
    def dimensions(tool):
        values = {}
        for name in ("Diameter", "CuttingEdgeHeight", "Length", "ShankDiameter", "CornerRadius", "TipDiameter", "CuttingEdgeAngle"):
            if hasattr(tool, name):
                value = getattr(tool, name)
                values[name] = float(value.Value if hasattr(value, "Value") else value)
        return values
    return {side: [{"id": op.InlayOperationId, "gcode": list(op.Gcode),
                    "tool": op.ToolController.ToolNumber,
                    "tool_shape": op.ToolController.Tool.ShapeType,
                    "diameter": op.ToolController.Tool.Diameter.Value,
                    "tool_dimensions": dimensions(op.ToolController.Tool),
                    "commands": [{"name": cmd.Name, "parameters": dict(cmd.Parameters)} for cmd in op.Path.Commands]}
                   for op in doc.getObject(jobs[side]).Operations.Group]
            for side in ("board", "plug")}


def stock_snapshot(doc, jobs):
    """Require native Job stock to retain the actual source shape and holes."""
    values = {}
    for side in ("board", "plug"):
        job = doc.getObject(jobs[side])
        shape = job.Stock.Shape
        base = job.Proxy.baseObjects(job)[0].Shape
        check(not shape.cut(base).Solids and not base.cut(shape).Solids,
              "Native stock substituted a bounding box for the exact source")
        box = shape.BoundBox
        top = [face for face in shape.Faces if abs(face.BoundBox.ZMax - box.ZMax) < 1e-6 and face.BoundBox.ZLength < 1e-6]
        values[side] = dict(volume_mm3=round(shape.Volume, 8), area_mm2=round(shape.Area, 8),
                            bounds=[round(value, 8) for value in (box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax)],
                            faces=len(shape.Faces), edges=len(shape.Edges), top_wires=sum(len(face.Wires) for face in top))
    return values


def export_jobs(doc, jobs, directory, inches=False, precision=9, exact=True):
    from Path.Post.Processor import PostProcessorFactory
    outputs = {}
    for side in ("board", "plug"):
        job = doc.getObject(jobs[side])
        job.PostProcessor = "linuxcnc"
        preamble = "G17 G54 G40 G49 G80 G90 G61" if exact else "G17 G54 G40 G49 G80 G90 G64"
        job.PostProcessorArgs = f'--no-show-editor --no-header --precision {precision} --preamble "{preamble}"'
        if inches:
            job.PostProcessorArgs += " --inches"
        sections = PostProcessorFactory.get_post_processor(job, "linuxcnc").export()
        check(sections and len(sections) == 1 and sections[0][1], "Native post returned no motion")
        text = sections[0][1]
        destination = directory / (side + ("_inch" if inches else "_metric") + f"_{precision}.ngc")
        destination.write_text(text)
        outputs[side] = str(destination)
    return outputs


def parse_post(text):
    """Small independent G0/G1 replay, in mm and mm/min after modal units."""
    unit = 1.0
    xyz = {}
    feed = None
    spindle = None
    path_control = None
    tool = None
    load_count = 0
    physical_changes = 0
    moves = []
    for line in text.splitlines():
        line = re.sub(r"\([^)]*\)", "", line).split(";")[0].strip()
        if not line:
            continue
        values = [(name, float(value)) for name, value in re.findall(r"([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))", line)]
        codes = [value for name, value in values if name == "G"]
        fields = dict(values)
        if 20 in codes:
            unit = 25.4
        if 21 in codes:
            unit = 1.0
        if 61 in codes:
            path_control = "G61"
        if 64 in codes:
            path_control = "G64"
        if "S" in fields:
            spindle = fields["S"]
        if "F" in fields:
            feed = fields["F"] * unit
        if "T" in fields:
            new = int(fields["T"])
            physical_changes += tool is not None and tool != new
            tool = new
        if any(name == "M" and value == 6 for name, value in values):
            load_count += 1
        if any(code in (0, 1) for code in codes):
            supplied = {axis: fields[axis] * unit for axis in ("X", "Y", "Z") if axis in fields}
            xyz.update(supplied)
            moves.append({"position": dict(xyz), "supplied_axes": sorted(supplied),
                          "feed": feed, "spindle": spindle, "tool": tool,
                          "kind": "rapid" if 0 in codes else "feed", "path_control": path_control})
    return {"moves": moves, "load_count": load_count, "tool_changes": int(physical_changes)}


def edit_custom(op, replacement):
    """Exercise native Custom editing with real Qt keyboard/button events."""
    Gui.activeDocument().setEdit(op.Name)
    panel = op.ViewObject.Proxy.panel
    check(panel is not None, "Native Custom task panel did not open")
    editor = None
    for page in panel.featurePages:
        if hasattr(page.form, "txtGCode"):
            editor = page.form.txtGCode
            break
    check(editor is not None, "Native G-code editor is missing")
    editor.setFocus()
    from PySide6 import QtGui
    QtTest.QTest.keySequence(editor, QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.SelectAll))
    QtTest.QTest.keyClick(editor, QtCore.Qt.Key_Backspace)
    for index, line in enumerate(replacement.splitlines()):
        if index:
            QtTest.QTest.keyClick(editor, QtCore.Qt.Key_Return)
        QtTest.QTest.keyClicks(editor, line)
    wait_until(lambda: list(op.Gcode) == replacement.splitlines())
    button = panel.buttonBox.button(QtWidgets.QDialogButtonBox.Ok)
    check(button is not None, "Native OK button is missing")
    QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
    wait_until(lambda: not Gui.activeDocument().getInEdit())
    op.Document.recompute()


def file_hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}
