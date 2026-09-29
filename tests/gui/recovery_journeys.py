"""Real task actions at source-reference and filesystem failure boundaries."""
import json
from pathlib import Path
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide6 import QtCore, QtWidgets

import journeys as gui
import review_journeys as review


def _confirm(dialog):
    gui.check(dialog.objectName() == 'confirmApplyDialog', 'Stale-source attempt still requires native review')
    gui.click(dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok))


def source_recovery():
    doc, panel, _ = review._small_flat()
    gui.tab(0)
    sphere = doc.addObject('Part::Feature', 'NonplanarStock')
    sphere.Shape = Part.makeSphere(4)
    doc.recompute()
    Gui.Selection.clearSelection(); Gui.Selection.addSelection(sphere, 'Face1')
    gui.click('boardCaptureFace')
    gui.check('board' not in panel.existing_faces and 'planar' in panel.status.text().lower(),
              'Actual capture action rejects a curved stock face with a visible diagnostic')
    doc.removeObject(sphere.Name)
    stock = doc.addObject('Part::Feature', 'SelectedWood')
    stock.Shape = Part.makeBox(20, 20, 6, App.Vector(0, 0, -6))
    doc.recompute()
    face_index = next(i+1 for i,f in enumerate(stock.Shape.Faces)
                      if abs(f.CenterOfMass.z) < 1e-9 and f.normalAt(0,0).z > .99)
    Gui.Selection.clearSelection(); Gui.Selection.addSelection(stock, 'Face'+str(face_index))
    gui.click('boardCaptureFace')
    gui.check('board' in panel.existing_faces, 'Valid planar native stock can recover after curved-face rejection')
    original = stock.Shape.copy()
    review._calculate(panel, 'needs_acceptance'); review._accept(panel)
    before = {o.Name for o in doc.Objects}
    stock.Shape = Part.makeBox(21, 20, 6, App.Vector(0,0,-6)); doc.recompute()
    gui.modal_action(lambda: gui.click('applyPair'), _confirm)
    gui.check('changed' in panel.status.text().lower(), 'Changed source geometry is diagnosed before Apply writes')
    gui.check({o.Name for o in doc.Objects} == before, 'Changed source rejection preserves the native document')
    stock.Shape = original; doc.recompute()
    review._calculate(panel, 'ready')
    name = stock.Name
    doc.removeObject(name); doc.recompute()
    gui.modal_action(lambda: gui.click('applyPair'), _confirm)
    gui.check(not gui.widget('applyPair').isEnabled(), 'Deleted source cannot apply a previously reviewed result')
    gui.check(not doc.Objects, 'Deleted source rejection does not create an orphan machining job')
    gui.screenshot('recovery-deleted-source')
    gui.tab(0); gui.click('boardUseSlab')
    review._calculate(panel)
    if panel.result['status'] == 'needs_acceptance': review._accept(panel)
    gui.check(panel.result['status'] == 'ready', 'Explicitly changing to slab recovers the draft')
    gui.close_task()
    return {'supporting_rows':['E04','E21','E29'], 'scope':'Curved, changed and deleted source GUI recovery'}


def filesystem_recovery():
    doc, panel, _ = review._small_flat()
    imported = panel.imported
    gui.tab(1)
    def cancel(dialog):
        gui.check(isinstance(dialog, QtWidgets.QFileDialog), 'Import cancellation uses the native file dialog')
        gui.click(dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Cancel))
    gui.modal_action(lambda: gui.click('browseSvg'), cancel)
    gui.check(panel.imported is imported, 'Cancelling import retains the current artwork')
    # A directory is not a readable SVG file; use real filesystem failure.
    review._paste('svgPath', str(gui.OUT))
    gui.click('importSvg')
    gui.check('cannot import svg' in panel.status.text().lower(), 'Unreadable input reports its failed action')
    source = gui.OUT/'review-key.svg'
    review._import(panel, source)
    gui.click('center_both')
    inaccessible=gui.OUT/'unreadable.svg'
    inaccessible.write_text(source.read_text())
    inaccessible.chmod(0)
    try:
        review._paste('svgPath',str(inaccessible))
        gui.click('importSvg')
        gui.check('cannot import svg' in panel.status.text().lower(), 'Permission-denied SVG input reports its failed action')
    finally:
        inaccessible.chmod(0o600)
    review._import(panel,source);gui.click('center_both')
    unavailable=gui.OUT/'unavailable-runtime'
    (unavailable/'bin').mkdir(parents=True)
    binary=unavailable/'bin/python'
    binary.write_text('Not an executable runtime')
    binary.chmod(0o600)
    with patch('inlay_planner.process.App.getHomePath',return_value=str(unavailable)):
        gui.click('calculate')
        gui.wait(lambda:not panel.calculation.busy,'OS-denied process startup completes',timeout=10)
    gui.check('could not start' in panel.status.text().lower(), 'Actual QProcess startup denial has a useful diagnostic')
    gui.check(not gui.widget('applyPair').isEnabled() and not doc.Objects, 'Worker startup failure cannot write machining output')
    # Deterministic fault injection only at the OS allocation boundary. All
    # calculation, failure rendering and retry actions remain real Qt input.
    with patch('inlay_planner.process.tempfile.mkdtemp', side_effect=PermissionError('Test cache location is read-only')):
        gui.click('calculate')
    gui.check('read-only' in panel.status.text(), 'Unwritable calculation cache produces a visible recoverable error')
    gui.check(not panel.calculation.busy and gui.widget('calculate').isEnabled(), 'Cache failure leaves no worker and permits retry')
    gui.check(not gui.widget('applyPair').isEnabled() and not doc.Objects, 'Cache failure cannot apply a partial result')
    review._calculate(panel)
    if panel.result['status'] == 'needs_acceptance': review._accept(panel)
    gui.check(panel.result['status'] == 'ready', 'Valid cache retry returns to verified review')
    gui.screenshot('recovery-filesystem-retry')
    gui.close_task()
    # Cancellation of native Save As is a separate FreeCAD-owned boundary.
    marker = doc.addObject('App::DocumentObjectGroup','SaveCancellationMarker')
    gui.modal_action(lambda: Gui.runCommand('Std_SaveAs'), cancel)
    gui.check(not doc.FileName and doc.getObject(marker.Name) is marker, 'Native Save As cancellation preserves the unsaved document')
    return {'supporting_rows':['E28','E29'], 'scope':'Actual file-dialog cancellations, unreadable input, injected cache denial and retry'}


CASES = {'source_recovery': source_recovery, 'filesystem_recovery': filesystem_recovery}
