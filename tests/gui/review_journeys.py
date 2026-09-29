"""Independent packaged-GUI variants driven through actual Qt controls.

These journeys exercise real Calculate/accept/review controls. Reading panel
state supplies assertions; no production calculation/apply method is called
directly. Individual variants support ledger rows without claiming to close
every required branch of those rows.
"""

from copy import deepcopy
import hashlib
import json
import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide6 import QtCore, QtGui, QtWidgets, QtTest

import journeys as gui


def _write(name, content):
    path = gui.OUT/name
    path.write_text(content, encoding="utf-8")
    return path


def _paste(name, value):
    """Unicode-safe native clipboard input; QTest.keyClicks is ASCII only."""
    control = gui.widget(name, QtWidgets.QLineEdit)
    gui.click(control)
    QtTest.QTest.keySequence(control, QtGui.QKeySequence(QtGui.QKeySequence.SelectAll))
    QtWidgets.QApplication.clipboard().setText(value)
    QtTest.QTest.keySequence(control, QtGui.QKeySequence(QtGui.QKeySequence.Paste))
    QtTest.QTest.keyClick(control, QtCore.Qt.Key_Tab)
    gui.check(control.text() == value, "Unicode/path input survives native paste")


def _import(panel, path):
    gui.tab(1)
    _paste("svgPath", str(path))
    gui.click("importSvg")
    gui.wait(lambda: not panel.calculation.busy, "Packaged SVG worker completes", timeout=120)
    gui.check(panel.imported is not None and path.name in panel.source_info.text(),
              "Imported current fixture " + path.name)
    gui.check(not gui.widget("applyPair").isEnabled(), "Import alone cannot apply machining paths")
    return panel.imported


def _area(polygons):
    def ring(points):
        return abs(sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(points, points[1:]+points[:1])))/2
    return sum(ring(p["outer"])-sum(ring(h) for h in p.get("holes", [])) for p in polygons)


def _checked(name, desired):
    control = gui.widget(name, QtWidgets.QCheckBox)
    if control.isChecked() != desired:
        gui.click(control)
    if control.isChecked() != desired:
        # A stretched QCheckBox's blank centre can be outside hitButton();
        # Space is the native keyboard action for the actual checkbox.
        QtTest.QTest.keyClick(control, QtCore.Qt.Key_Space)
    gui.check(control.isChecked() == desired, "Checkbox " + name + " = " + str(desired))


def _spin(name, value):
    control = gui.widget(name, QtWidgets.QSpinBox)
    gui.click(control)
    QtTest.QTest.keySequence(control, QtGui.QKeySequence(QtGui.QKeySequence.SelectAll))
    QtTest.QTest.keyClicks(control, str(value))
    QtTest.QTest.keyClick(control, QtCore.Qt.Key_Tab)
    gui.check(control.value() == value, "Edited integer control " + name)


def _select_library_flat(panel):
    gui.tab(2)
    table = gui.widget("toolLibrary", QtWidgets.QTableWidget)
    row = next(i for i, tool in enumerate(panel.tools) if tool["shape"] == "flat" and tool.get("supported", True))
    item = table.item(row, 0)
    table.scrollToItem(item)
    QtWidgets.QApplication.processEvents()
    QtTest.QTest.mouseClick(table.viewport(), QtCore.Qt.LeftButton, pos=table.visualItemRect(item).center())
    if item.checkState() != QtCore.Qt.Checked:
        QtTest.QTest.keyClick(table, QtCore.Qt.Key_Space)
    gui.check(item.checkState() == QtCore.Qt.Checked, "Native flat cutter selected through library checkbox")
    return row


def _small_flat():
    doc = App.newDocument("InlayReviewLifecycle")
    panel = gui.launch_command()
    for side in ("board", "plug"):
        for key, value in (("width", "20 mm"), ("height", "20 mm"), ("thickness", "6 mm"), ("clearance", "2 mm")):
            gui.text(side+"_"+key, value)
        _checked(side+"_operations_enabled", True)
        _spin(side+"_operations_limit", 1)
        _checked(side+"_tool_changes_enabled", True)
        _spin(side+"_tool_changes_limit", 0)
    source = _write("review-key.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="8mm" height="8mm" viewBox="0 0 8 8"><path id="key" d="M0 0 H8 V3 H6 V8 H0 Z"/></svg>')
    _import(panel, source)
    gui.click("center_both")
    row = _select_library_flat(panel)
    for key, value in (("diameter", "2 mm"), ("cutting_length", "5 mm"), ("shank_diameter", "2 mm"),
                       ("max_stepdown", "0.5 mm"), ("stepover", "0.8 mm"), ("feed", "500 mm/min"),
                       ("plunge_feed", "200 mm/min"), ("travel_feed", "1000 mm/min"), ("spindle", "10000 rpm")):
        gui.text("tool_"+key, value)
    gui.tab(3)
    gui.select("inlayStyle", 1)
    for key, value in (("fit_value", "1 mm"), ("glue_gap", "0.2 mm"), ("side_clearance", "0.2 mm"),
                       ("backing_clearance", "0.5 mm"), ("resolution_xy", "0.05 mm"), ("resolution_z", "0.05 mm")):
        gui.text(key, value)
    return doc, panel, row


def _calculate(panel, expected=None):
    gui.click("calculate")
    gui.wait(lambda: not panel.calculation.busy and not panel._pending_calculate,
             "Paired calculation including tighter SVG reimport completes", timeout=240)
    gui.check(panel.result is not None, "Actual Calculate produced a result")
    if expected is not None:
        gui.check(panel.result["status"] == expected, "Calculated status is " + expected)
    return panel.result


def _accept(panel):
    gui.check(panel.result["status"] == "needs_acceptance", "Current change requires explicit acceptance")
    gui.click("acceptProposal")
    gui.wait(lambda: not panel.calculation.busy and not panel._pending_calculate,
             "Accepted pair is recalculated", timeout=240)
    gui.check(panel.result["status"] == "ready", "Current accepted pair is ready")
    gui.check(gui.widget("applyPair").isEnabled(), "Ready pair enables final review")


def review_svg():
    doc = App.newDocument("InlayReviewSVG")
    panel = gui.launch_command()
    manifest = json.loads((gui.ADDON/"examples/fixture_manifest.json").read_text())
    evidence = []
    for fixture in manifest["fixtures"]:
        imported = _import(panel, gui.ADDON/"examples"/fixture["filename"])
        polygons = [p for region in imported["regions"] for p in region["polygons"]]
        holes = sum(len(p.get("holes", [])) for p in polygons)
        gui.check(not imported["diagnostics"], "Supported corpus has no diagnostics: " + fixture["filename"])
        gui.check(len(polygons) == fixture["components"] and holes == fixture["holes"],
                  "Independent component/counter count: " + fixture["filename"])
        gui.check(imported["size"] == fixture["canvas_mm"] and not imported["size_assumed"],
                  "Physical SVG canvas dimensions retained: " + fixture["filename"])
        if fixture.get("area_mm2") is not None:
            gui.check(abs(_area(polygons)-fixture["area_mm2"]) <= fixture["area_abs_tolerance"],
                      "Independent shoelace area: " + fixture["filename"])
        if fixture.get("expected_bounds_canonical_mm"):
            gui.check(max(abs(a-b) for a, b in zip(imported["bounds"], fixture["expected_bounds_canonical_mm"])) < 1e-5,
                      "Independent physical bounds: " + fixture["filename"])
        if fixture['filename'] == 'outlined_letterform.svg':
            # The outlined B has a square upper-left and chamfered upper-right
            # corner. Literal witnesses distinguish a physical X reflection.
            for side in ('board','plug'):
                gui.text(side+'_design_x','75 mm'); gui.text(side+'_design_y','50 mm')
            for mode,filled,empty,hole in ((0,(52,80.5),(98,80.5),(70,64.5)),
                                           (1,(98,80.5),(52,80.5),(80,64.5))):
                gui.select('previewFace',mode)
                paths=[item.path() for item in panel.view.scene().items()
                       if item.data(0)=='outlined_b' and hasattr(item,'path')]
                gui.check(paths and any(path.contains(QtCore.QPointF(*filled)) for path in paths) and
                          not any(path.contains(QtCore.QPointF(*empty)) for path in paths) and
                          not any(path.contains(QtCore.QPointF(*hole)) for path in paths),
                          ('Board' if mode==0 else 'Plug')+' outlined-letter preview has the independently expected handed corner and open counter')
            gui.check(all(panel.get_spec(require_tools=False)['faces'][side]['origin']==[0,0,0]
                          for side in ('board','plug')), 'Outlined-letter preview switching preserves both work origins')
            gui.screenshot('review-svg-outlined-letter-flip')
            gui.select('previewFace',0)
            gui.tab(1)
        evidence.append({"file": fixture["filename"], "components": len(polygons), "holes": holes,
                         "source_sha256": hashlib.sha256((gui.ADDON/"examples"/fixture["filename"]).read_bytes()).hexdigest(),
                         "area_mm2": _area(polygons), "flattening_tolerance": imported["flattening_tolerance"]})
    mixed = _write("mixed unsupported.svg", '''<svg xmlns="http://www.w3.org/2000/svg" width="30mm" height="30mm" viewBox="0 0 30 30">
<rect id="good" x="20" y="20" width="5" height="5"/>
<g visibility="hidden"><rect id="visible-child" x="10" y="20" width="3" height="3" visibility="visible"/></g>
<g id="opacity-parent" opacity=".5"><rect width="4" height="4" opacity="1"/></g>
<g id="clip-parent" clip-path="url(#a)"><rect width="4" height="4" clip-path="none"/></g>
<g id="filter-parent" filter="blur(2px)"><rect width="4" height="4" filter="none"/></g>
<rect id="bad-transform" width="4" height="4" transform="translate(1 potato 2)"/>
<polygon id="bad-list" points="0,0 4,potato 4,4"/>
<rect id="css-geometry" width="4" height="4" style="transform: translate(2px,3px)"/>
<text id="live-text" x="0" y="10">Wood</text><image id="raster" href="https://example.invalid/image.png" width="4" height="4"/>
<path id="stroke-only" d="M0 0 L5 5" fill="none" stroke="black"/>
<use id="external-use" href="https://example.invalid/art.svg#shape"/>
</svg>''')
    imported = _import(panel, mixed)
    polygons = [p for region in imported["regions"] for p in region["polygons"]]
    gui.check(abs(_area(polygons)-34) < 1e-9, "Mixed SVG retains exactly the 25+9 square-mm supported fills")
    gui.check(len(imported["diagnostics"]) >= 10, "Mixed unsupported SVG reports each affected construct")
    gui.check("unsupported" in panel.status.text().lower() or "support" in panel.status.text().lower(),
              "Unsupported content has visible status text")
    gui.screenshot("review-svg-mixed-diagnostics")
    # Unicode, spaces and literal shell characters must survive ordinary input.
    unicode_path = _write("木 工作 $(literal).svg", '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" viewBox="0 0 10 10"><rect width="4" height="3"/></svg>')
    imported = _import(panel, unicode_path)
    gui.check(abs(_area([p for r in imported["regions"] for p in r["polygons"]])-12) < 1e-9,
              "Unicode/space/shell-character filename imports the intended content")
    bad = _write("invalid-viewbox.svg", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 nope 10 10"><rect width="4" height="3"/></svg>')
    _paste("svgPath", str(bad))
    gui.click("importSvg")
    gui.wait(lambda: not panel.calculation.busy, "Malformed root is diagnosed")
    gui.check("viewbox" in panel.status.text().lower(), "Malformed viewBox is visibly diagnosed")
    gui.check(not gui.widget("applyPair").isEnabled(), "Malformed input cannot enable Apply")
    _paste("svgPath", str(gui.OUT/"missing input.svg"))
    gui.click("importSvg")
    gui.check("cannot import" in panel.status.text().lower(), "Missing-file import produces a recoverable action-specific error")
    _import(panel, unicode_path)
    gui.check(not doc.Objects, "Input/recovery journeys create no document objects")
    gui.screenshot("review-svg-recovered")
    gui.close_task()
    _write("svg-variant-evidence.json", json.dumps(evidence, indent=2))
    return {"supporting_rows": ["E02", "E03", "E07", "E29"], "variants": len(evidence)+3,
            "scope": "Packaged six-family/mixed/invalid/path-recovery import variants; not complete ledger rows"}


def review_placement():
    doc = App.newDocument("InlayReviewPlacement")
    panel = gui.launch_command()
    for side in ("board", "plug"):
        gui.text(side+"_width", "100 mm")
        gui.text(side+"_height", "80 mm")
        gui.select(side+"OriginPreset", 3)
        for axis, value in zip(("x", "y", "z"), ("7 mm", "9 mm", "1 mm")):
            gui.text(side+"_origin_"+axis, value)
    source = _write("placement.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="10mm" viewBox="0 0 20 10"><rect id="left" width="8" height="10"/><rect id="right" x="12" width="8" height="10"/></svg>')
    _import(panel, source)
    gui.click("center_x")
    gui.check(panel._value("board_design_x") == 50 and panel._value("plug_design_x") == 50,
              "Horizontal centering uses each slab X axis")
    gui.click("center_y")
    gui.check(panel._value("board_design_y") == 40 and panel._value("plug_design_y") == 40,
              "Vertical centering uses each slab Y axis")
    gui.check(panel.get_spec(require_tools=False)["faces"]["board"]["origin"] == [7, 9, 1],
              "Centering artwork preserves independently selected work origin")
    # Width edit with aspect lock must update height physically.
    gui.text("art_width", "50.8 mm")
    gui.check(abs(panel._value("art_height")-25.4) < 1e-9, "Aspect-locked scaling preserves the known two-to-one ratio")
    physical = {name: panel._value(name) for name in ("art_width", "art_height", "board_origin_x", "board_origin_y", "board_origin_z")}
    gui.select("displayUnits", 1)
    gui.check(all(abs(panel._value(k)-v) < 1e-8 for k, v in physical.items()), "Metric-to-imperial display preserves physical geometry and origin")
    gui.text("art_width", "2 in")
    gui.check(abs(panel._value("art_width")-50.8) < 1e-9, "Explicit imperial artwork width converts independently to 50.8 mm")
    gui.select("displayUnits", 0)
    gui.text("art_rotation", "90 deg")
    gui.click("center_both")
    spec = panel.get_spec(require_tools=False)
    coordinates = [p for r in spec["artwork"]["regions"] for polygon in r["polygons"] for p in polygon["outer"]]
    gui.check(abs(max(p[0] for p in coordinates)-min(p[0] for p in coordinates)-25.4) < 1e-8,
              "Ninety-degree rotation swaps physical artwork extents")
    gui.check(spec["faces"]["plug"]["design_to_face"][0] == -1, "Plug setup retains the paired physical reflection")
    gui.text("art_rotation", "0 deg")
    gui.click("fitPreview")
    regions = gui.widget("artworkRegions", QtWidgets.QListWidget)
    item = regions.item(0)
    regions.scrollToItem(item)
    QtTest.QTest.mouseClick(regions.viewport(), QtCore.Qt.LeftButton, pos=regions.visualItemRect(item).center())
    QtTest.QTest.keyClick(regions, QtCore.Qt.Key_Space)
    gui.check(len(panel.excluded_ids) == 1, "Region checkbox explicitly excludes one source region")
    gui.click("restoreExclusions")
    gui.check(not panel.excluded_ids, "Restore returns the excluded source region")
    view = gui.widget("artworkPreview", QtWidgets.QGraphicsView)
    zoom_before = abs(view.transform().m11())
    centre = view.viewport().rect().center()
    event = QtGui.QWheelEvent(QtCore.QPointF(centre), QtCore.QPointF(view.viewport().mapToGlobal(centre)),
                             QtCore.QPoint(), QtCore.QPoint(0, 120), QtCore.Qt.NoButton,
                             QtCore.Qt.NoModifier, QtCore.Qt.NoScrollPhase, False)
    QtWidgets.QApplication.sendEvent(view.viewport(), event)
    gui.check(abs(view.transform().m11()) > zoom_before, "Mouse wheel zoom increases fine-selection scale")
    gui.click("fitPreview")
    gui.select("selectionMode", 2)
    start = view.mapFromScene(QtCore.QPointF(27, 29))
    end = view.mapFromScene(QtCore.QPointF(32, 34))
    gui.check(view.viewport().rect().contains(start) and view.viewport().rect().contains(end), "Fine portion drag lies within the visible preview")
    QtTest.QTest.mousePress(view.viewport(), QtCore.Qt.LeftButton, pos=start)
    QtTest.QTest.mouseMove(view.viewport(), end, delay=30)
    QtTest.QTest.mouseRelease(view.viewport(), QtCore.Qt.LeftButton, pos=end)
    gui.check(len(panel.source_exclusions) == 1, "Dragging a portion records an explicit paired exclusion")
    gui.check(len(panel.get_spec(require_tools=False)["exclusions"]) == 1, "Paired specification retains the selected exclusion")
    gui.screenshot("review-placement-excluded-portion")
    gui.click("restoreExclusions")
    gui.select("selectionMode", 3)
    before = (panel._value("board_design_x"), panel._value("board_design_y"))
    start = view.mapFromScene(QtCore.QPointF(50, 40))
    end = view.mapFromScene(QtCore.QPointF(55, 43))
    real_start, real_end = view.mapToScene(start), view.mapToScene(end)
    QtTest.QTest.mousePress(view.viewport(), QtCore.Qt.LeftButton, pos=start)
    QtTest.QTest.mouseMove(view.viewport(), end, delay=30)
    QtTest.QTest.mouseRelease(view.viewport(), QtCore.Qt.LeftButton, pos=end)
    gui.check(abs(panel._value("board_design_x")-before[0]-(real_end.x()-real_start.x())) < 1e-7,
              "Preview drag uses the actual visible coordinate transform")
    final_origin = panel.get_spec(require_tools=False)["faces"]["board"]["origin"]
    gui.check(all(abs(a-b) < 1e-10 for a, b in zip(final_origin, [7, 9, 1])),
              "Artwork dragging preserves the work origin within scalar arithmetic error: " + repr(final_origin))
    gui.tab(3)
    gui.text("fit_value", "3 mm")
    gui.text("glue_gap", "0.2 mm")
    gui.text("backing_clearance", "0.5 mm")
    gui.select("depthDriver", 1)
    gui.check(abs(panel._value("fit_value")-3.3) < 1e-9, "Depth-to-height switch follows H = D - g + b")
    gui.select("depthDriver", 0)
    gui.check(abs(panel._value("fit_value")-3) < 1e-9, "Height-to-depth switch preserves the same paired geometry")
    gui.text("resolution_xy", "0.05 mm")
    gui.text("resolution_z", "0.02 mm")
    gui.check(panel.get_spec(require_tools=False)["resolution"] == {"xy": .05, "z": .02}, "XY and Z increments remain separate")
    gui.screenshot("review-placement-metric-fit")
    gui.check(not doc.Objects, "Placement/exclusion/fit editing does not create native paths")
    gui.close_task()
    return {"supporting_rows": ["E05", "E06", "E09", "E14", "E15", "E32"],
            "scope": "Packaged physical-unit, centering, scaling, reflection, exclusion, drag and depth-driver variants; no machining certificate"}


def review_lifecycle():
    doc, panel, row = _small_flat()
    _calculate(panel, "needs_acceptance")
    gui.check(all(v == {"operations": 1, "tool_changes": 0} for v in panel.result["per_face_counts"].values()),
              "Independent per-face one-operation/zero-change caps allow both separate setups")
    first_hash = panel.result["proposal"]["hash"]
    _accept(panel)
    gui.check(panel.accepted_proposal_hash == first_hash, "Acceptance binds the exact proposed paired revision")
    operations = gui.widget("operationList", QtWidgets.QTreeWidget)
    item = operations.topLevelItem(0)
    operations.scrollToItem(item)
    QtTest.QTest.mouseClick(operations.viewport(), QtCore.Qt.LeftButton, pos=operations.visualItemRect(item).center())
    gui.check("baseline" in panel.operation_info.text().lower(), "Selected operation exposes baseline versus chosen time")
    _checked("showDepthOverlay", True)
    for mode in range(5):
        gui.select("previewFace", mode)
        gui.check(bool(panel.view.scene().items()), "Preview mode has display geometry: " + str(mode))
    gui.check("First rigid contact" in panel.section.text() and "measured" in panel.section.text(),
              "Review exposes contact bracket and requested-versus-measured clearances")
    gui.screenshot("review-lifecycle-finished-surface")
    baseline_tools = deepcopy(panel.tools)
    selected_id = panel.selected_operation
    def override(dialog):
        gui.check(dialog.objectName() == "operationOverrideDialog", "Operation override uses its native modal control")
        gui.text("override_feed", "600 mm/min")
        buttons = dialog.findChild(QtWidgets.QDialogButtonBox)
        gui.click(buttons.button(QtWidgets.QDialogButtonBox.Ok))
        return "feed override"
    gui.modal_action(lambda: gui.click("overrideOperation"), override)
    gui.check(not gui.widget("applyPair").isEnabled() and panel.accepted_proposal_hash is None,
              "Editing a use invalidates old result and geometry acceptance")
    gui.check(panel.tools == baseline_tools, "Per-operation edit leaves all shared tool snapshots unchanged")
    _calculate(panel, "needs_acceptance")
    overridden = next(op for op in panel.result["operations"] if op["id"] == selected_id)
    other = next(op for op in panel.result["operations"] if op["id"] != selected_id)
    gui.check(overridden["settings"]["feed"] == 600 and other["settings"]["feed"] == 500,
              "Feed override changes only the selected physical use")
    gui.check(panel.result["proposal"]["hash"] != first_hash, "Changed motion/settings need a new acceptance identity")
    _accept(panel)
    before = {obj.Name for obj in doc.Objects}
    def cancel_review(dialog):
        gui.check(dialog.objectName() == "confirmApplyDialog", "Final pair review is visible")
        content = gui.widget("applyReviewText", QtWidgets.QPlainTextEdit, dialog).toPlainText()
        gui.check(all(word in content.lower() for word in ("origin", "bounds", "resolution", "600", "500")),
                  "Final review contains both setups, machine increments and effective feeds")
        gui.screenshot("review-lifecycle-confirmation")
        gui.click(dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Cancel))
        return "cancelled review"
    gui.modal_action(lambda: gui.click("applyPair"), cancel_review)
    gui.check({obj.Name for obj in doc.Objects} == before, "Cancelling final review makes no document change")
    # Native document context switches must disable the owning task's actions.
    another = App.newDocument("UnrelatedReviewDocument")
    gui.wait(lambda: not gui.widget("calculate").isEnabled(), "Inactive owning document disables calculation", timeout=5)
    gui.check(not gui.widget("applyPair").isEnabled(), "Inactive owning document disables Apply")
    App.setActiveDocument(doc.Name)
    gui.wait(lambda: gui.widget("applyPair").isEnabled(), "Owning document restores current ready review", timeout=5)
    App.closeDocument(another.Name)
    # A real running worker is cancelled through its button before a retry.
    gui.tab(3)
    gui.text("side_clearance", "0.25 mm")
    gui.check(not gui.widget("applyPair").isEnabled(), "Changing fit disables stale Apply immediately")
    gui.click("calculate")
    gui.check(panel.calculation.busy, "Actual worker is active before cancellation")
    gui.click("cancelCalculation")
    gui.wait(lambda: not panel.calculation.busy, "Cancelled worker terminates", timeout=15)
    gui.check(not gui.widget("applyPair").isEnabled(), "Cancelled calculation cannot apply a stale pair")
    gui.check({obj.Name for obj in doc.Objects} == before, "Cancellation preserves unrelated document state")
    _calculate(panel, "needs_acceptance")
    _accept(panel)
    _write("lifecycle-spec.json", json.dumps(panel.calculated_spec, indent=2))
    _write("lifecycle-result.json", json.dumps(panel.result, indent=2))
    gui.screenshot("review-lifecycle-recovered")
    gui.close_task()
    return {"supporting_rows": ["E12", "E13", "E14", "E16", "E17", "E18", "E19", "E20", "E21", "E28", "E32"],
            "scope": "Packaged actual calculation/review/override/cancellation variants; no native Apply/export in this journey"}


def _reveal_and_measure(control):
    """Scroll every native ancestor; test actual clipped on-window geometry."""
    reveal_log = []
    parent = control.parentWidget()
    while parent:
        if isinstance(parent, QtWidgets.QScrollArea):
            bar = parent.verticalScrollBar()
            before = [bar.minimum(), bar.value(), bar.maximum()]
            parent.ensureWidgetVisible(control)
            QtWidgets.QApplication.processEvents()
            reveal_log.append({"name": parent.objectName(), "class": parent.metaObject().className(),
                               "scroll_before": before, "scroll_after": [bar.minimum(), bar.value(), bar.maximum()]})
        parent = parent.parentWidget()
    # ensureWidgetVisible can reveal only QGraphicsView's focus rectangle.
    # A user can continue scrolling; exercise the actual native scrollbar
    # keyboard actions before deciding that the whole target is unreachable.
    parent = control.parentWidget()
    while parent:
        if isinstance(parent, QtWidgets.QScrollArea):
            bar = parent.verticalScrollBar()
            for _ in range(64):
                target = QtCore.QRect(control.mapToGlobal(QtCore.QPoint()), control.size())
                viewport = parent.viewport()
                available = QtCore.QRect(viewport.mapToGlobal(QtCore.QPoint()), viewport.size())
                if target.top() < available.top():
                    key = QtCore.Qt.Key_Up
                elif target.bottom() > available.bottom():
                    key = QtCore.Qt.Key_Down
                else:
                    break
                before = bar.value()
                QtTest.QTest.keyClick(bar, key)
                QtWidgets.QApplication.processEvents()
                reveal_log.append({"name": parent.objectName(), "class": parent.metaObject().className(),
                                   "native_key": "Up" if key == QtCore.Qt.Key_Up else "Down",
                                   "value_before": before, "value_after": bar.value()})
                if before == bar.value():
                    break
        parent = parent.parentWidget()
    full = QtCore.QRect(control.mapToGlobal(QtCore.QPoint()), control.size())
    visible = QtCore.QRect(full)
    ancestors = []
    parent = control.parentWidget()
    while parent:
        rectangle = QtCore.QRect(parent.mapToGlobal(QtCore.QPoint()), parent.size())
        item = {"class": parent.metaObject().className(), "name": parent.objectName(),
                "rect": [rectangle.x(), rectangle.y(), rectangle.width(), rectangle.height()],
                "minimum_height": parent.minimumHeight(), "maximum_height": parent.maximumHeight(),
                "qt_inherits_scroll_area": parent.inherits("QScrollArea"),
                "python_scroll_area": isinstance(parent, QtWidgets.QScrollArea)}
        if isinstance(parent, QtWidgets.QScrollArea):
            bar = parent.verticalScrollBar()
            item["vertical_scroll"] = [bar.minimum(), bar.value(), bar.maximum()]
            viewport = parent.viewport()
            point = viewport.mapToGlobal(QtCore.QPoint())
            item["viewport_rect"] = [point.x(), point.y(), viewport.width(), viewport.height()]
            item["content_height"] = parent.widget().height() if parent.widget() is not None else None
        ancestors.append(item)
        visible = visible.intersected(rectangle)
        parent = parent.parentWidget()
    screen = Gui.getMainWindow().screen()
    visible = visible.intersected(screen.availableGeometry())
    area = max(0, visible.width())*max(0, visible.height())
    total = max(1, full.width()*full.height())
    return {"name": control.objectName(), "visible_fraction": area/total,
            "centre_visible": visible.contains(full.center()),
            "full_rect": [full.x(), full.y(), full.width(), full.height()],
            "visible_rect": [visible.x(), visible.y(), visible.width(), visible.height()],
            "ancestors": ancestors, "reveal_log": reveal_log}


def review_layout():
    doc = App.newDocument("InlayReviewLayout")
    panel = gui.launch_command()
    _import(panel, gui.ADDON/"examples/outlined_letterform.svg")
    measures = []
    for width, height in ((1400, 850), (1100, 760)):
        Gui.getMainWindow().resize(width, height)
        QtTest.QTest.qWait(150)
        for index, names in ((0, ("board_width", "plug_tool_changes_limit")),
                             (1, ("svgPath", "restoreExclusions")),
                             (2, ("toolLibrary", "tool_spindle")),
                             (3, ("fit_value", "resolution_z")),
                             (4, ("previewFace", "acceptProposal"))):
            _reveal_and_measure(gui.widget("inlayTabs", QtWidgets.QTabWidget).tabBar())
            gui.tab(index)
            for name in names:
                measure = _reveal_and_measure(gui.widget(name))
                measure["window_size"] = [Gui.getMainWindow().width(), Gui.getMainWindow().height()]
                measures.append(measure)
                _write("layout-visibility.json", json.dumps(measures, indent=2))
                gui.check(measure["centre_visible"] and measure["visible_fraction"] >= .8,
                          "Required control is reachable after native scrolling: " + json.dumps(measure))
        for name in ("artworkPreview", "calculate", "cancelCalculation", "applyPair"):
            measure = _reveal_and_measure(gui.widget(name))
            measure["window_size"] = [Gui.getMainWindow().width(), Gui.getMainWindow().height()]
            measures.append(measure)
            _write("layout-visibility.json", json.dumps(measures, indent=2))
            gui.check(measure["centre_visible"] and measure["visible_fraction"] >= .8,
                      "Preview/action is reachable within actual window: " + json.dumps(measure))
        gui.screenshot("review-layout-"+str(width)+"-actions")
        _reveal_and_measure(gui.widget("artworkPreview"))
        gui.screenshot("review-layout-"+str(width)+"-preview")
    _write("layout-visibility.json", json.dumps(measures, indent=2))
    gui.close_task()
    gui.check(not doc.Objects, "Layout/entry verification creates no native machining objects")
    panel = gui.launch_command()
    gui.check(panel.document.Name == doc.Name, "Repeated toolbar entry reattaches to the same native document")
    gui.close_task()
    return {"supporting_rows": ["E00", "E32"],
            "scope": "Finite window-size reachability via actual ancestor clipping, scroll and screen/viewport capture; no universal display claim"}


def review_failures():
    doc, panel, _ = _small_flat()
    _calculate(panel, "needs_acceptance")
    _accept(panel)
    def confirm(dialog):
        gui.check(dialog.objectName() == "confirmApplyDialog", "Existing work is first applied through actual confirmation")
        gui.click(dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok))
        return "apply baseline"
    gui.modal_action(lambda: gui.click("applyPair"), confirm)
    gui.check(panel.applied is not None, "Baseline native pair exists before fault injection")
    jobs = deepcopy(panel.applied)
    def native_state():
        return {"objects": sorted(obj.Name for obj in doc.Objects), "paths": gui.native_snapshot(doc, jobs)}
    baseline = native_state()
    baseline_digest = hashlib.sha256(json.dumps(baseline, sort_keys=True).encode()).hexdigest()
    _write("worker-fault-native-baseline.json", json.dumps(baseline, indent=2))
    outcomes = []
    faults = (("crash", "complete result"), ("dependency", "dependency"), ("memory", "memory"),
              ("partial", "complete result"), ("hash", "settings changed"), ("version", "version"),
              ("timeout", "time budget"))
    for fault, expected_message in faults:
        draft = deepcopy(panel.get_spec())
        os.environ["INLAY_TEST_FAULT"] = fault
        if fault == "timeout":
            os.environ["INLAY_TEST_TIMEOUT"] = "0.1"
        try:
            gui.click("calculate")
            gui.wait(lambda: not panel.calculation.busy, "Injected " + fault + " worker terminates", timeout=120)
            message = panel.status.text()
            after = hashlib.sha256(json.dumps(native_state(), sort_keys=True).encode()).hexdigest()
            outcomes.append({"fault": fault, "diagnostic": message, "draft_preserved": panel.get_spec() == draft,
                             "apply_disabled": not gui.widget("applyPair").isEnabled(),
                             "native_before_sha256": baseline_digest, "native_after_sha256": after})
            _write("worker-fault-evidence.json", json.dumps(outcomes, indent=2))
            gui.check(expected_message in message.lower(), "Useful final " + fault + " diagnostic is visible: " + message)
            gui.check(not gui.widget("applyPair").isEnabled(), "Injected " + fault + " cannot apply stale/partial output")
            gui.check(panel.get_spec() == draft, "Injected " + fault + " preserves the complete draft")
            gui.check(after == baseline_digest, "Injected " + fault + " preserves prior native jobs and commands")
            gui.screenshot("review-failure-"+fault)
        finally:
            os.environ.pop("INLAY_TEST_FAULT", None)
            os.environ.pop("INLAY_TEST_TIMEOUT", None)
        _calculate(panel)
        if panel.result["status"] == "needs_acceptance":
            _accept(panel)
        gui.check(panel.result["status"] == "ready", "Actual retry succeeds after " + fault)
        gui.check(native_state() == baseline, "Retry computes new review data without changing prior accepted native jobs")
    _write("worker-fault-evidence.json", json.dumps(outcomes, indent=2))
    gui.close_task()
    return {"supporting_rows": ["E20", "E28", "E29"],
            "scope": "Actual test-mode worker failures, UI diagnostics, prior native command preservation and genuine successful retries; no physical-memory-exhaustion claim"}


CASES = {name: value for name, value in globals().copy().items()
         if name in ("review_svg", "review_placement", "review_lifecycle", "review_layout", "review_failures")}
