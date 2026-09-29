"""Bounded independent edge variants using the installed task's real controls.

Assertions read state; calculation, tool editing, exclusion and acceptance use
Qt input. These finite cases support requirement rows, not arbitrary designs.
"""

import hashlib
import json
import math
from copy import deepcopy

import FreeCADGui as Gui

from PySide6 import QtCore, QtGui, QtWidgets, QtTest

import journeys as gui
import review_journeys as review


def _record(panel, label, evidence, code=None):
    result = review._calculate(panel, 'blocked' if code else None)
    codes = [d['code'] for d in result['diagnostics']]
    if code:
        gui.check(code in codes, label+' has the specific '+code+' diagnostic')
        gui.check(not gui.widget('applyPair').isEnabled(), label+' cannot apply a partial pair')
    evidence.append({'case': label, 'status': result['status'], 'input_hash': result['input_hash'],
                     'diagnostics': result['diagnostics'], 'per_face_counts': result['per_face_counts']})
    return result


def _circle(panel):
    points = [(3+3*math.cos(i*math.tau/48), 3+3*math.sin(i*math.tau/48)) for i in range(48)]
    source = review._write('edge-circle.svg',
        '<svg xmlns="http://www.w3.org/2000/svg" width="6mm" height="6mm" viewBox="0 0 6 6">'
        '<polygon id="circle" points="'+' '.join(f'{x:.14g},{y:.14g}' for x,y in points)+'"/></svg>')
    gui.tab(0)
    for side in ('board', 'plug'):
        gui.text(side+'_width', '12 mm'); gui.text(side+'_height', '12 mm')
    review._import(panel, source)
    gui.click('center_both')


def _select_row(panel, row):
    gui.tab(2)
    table = gui.widget('toolLibrary', QtWidgets.QTableWidget)
    item = table.item(row, 1)
    table.scrollToItem(item)
    gui.ensure_visible(table)
    QtTest.QTest.mouseClick(table.viewport(), QtCore.Qt.LeftButton, pos=table.visualItemRect(item).center())
    gui.check(panel._tool_row == row, 'Selected actual cutter row '+str(row))
    return table


def _toggle_tool(panel, row, desired):
    table = _select_row(panel, row)
    item = table.item(row, 0)
    table.scrollToItem(item)
    if (item.checkState() == QtCore.Qt.Checked) != desired:
        QtTest.QTest.mouseClick(table.viewport(), QtCore.Qt.LeftButton, pos=table.visualItemRect(item).center())
    if (item.checkState() == QtCore.Qt.Checked) != desired:
        QtTest.QTest.keyClick(table, QtCore.Qt.Key_Space)
    gui.check((item.checkState() == QtCore.Qt.Checked) == desired, 'Native cutter checkbox matches desired selection')


def edge_bounds():
    doc, panel, row = review._small_flat()
    evidence = []
    for axis, value, area, label in (('x', 3, 8, 'left'), ('x', 17, 3, 'right'),
                                     ('y', 3, 6, 'bottom'), ('y', 17, 8, 'top')):
        gui.tab(1)
        gui.text('board_design_x', '10 mm'); gui.text('board_design_y', '10 mm')
        gui.text('board_design_'+axis, str(value)+' mm')
        result = _record(panel, 'Artwork crossing '+label+' edge', evidence, 'ARTWORK_OUT_OF_BOUNDS')
        preview = result['preview']
        gui.check(abs(review._area(preview['blocked_polygons'])-area) < 1e-8,
                  label+' overlay equals independently known outside artwork area')
        gui.check(abs(review._area(preview['cuttable_polygons'])-(54-area)) < 1e-8,
                  label+' overlay also preserves the independently known cuttable area')
        painted = {item.data(1) for item in panel.view.scene().items()
                   if hasattr(item, 'path') and not item.path().isEmpty()}
        gui.check({'blocked', 'cuttable'} <= painted, label+' preview paints both blocked and cuttable regions')
    gui.screenshot('edge-bounds-crossing')
    gui.tab(1); gui.click('center_both')
    gui.tab(0); gui.text('board_margin', '7 mm')
    result = _record(panel, 'Positive edge margin restricts artwork', evidence, 'ARTWORK_OUT_OF_BOUNDS')
    gui.check(panel.calculated_spec['faces']['board']['bounds'] == [7, 7, 13, 13],
              'Margin produces the independently expected 6-by-6 machining rectangle')
    gui.check(result['preview']['cuttable_polygons'] and result['preview']['blocked_polygons'],
              'Margin leaves separate visible cuttable and blocked portions')
    gui.tab(0); gui.text('board_margin', '0 mm')
    _circle(panel)
    gui.tab(0); review._checked('plugFullBounds', False)
    for key, value in (('xmin', 1), ('ymin', 1), ('xmax', 11), ('ymax', 11)):
        gui.text('plug_'+key, str(value)+' mm')
    result = _record(panel, 'Finite cutter relief leaves interfering stock collar', evidence, 'ASSEMBLY_INTERFERENCE')
    gui.check('ARTWORK_OUT_OF_BOUNDS' not in [d['code'] for d in result['diagnostics']],
              'The artwork itself fits while retained perimeter material blocks assembly')
    gui.screenshot('edge-bounds-retained-collar')
    gui.tab(0); review._checked('plugFullBounds', True)
    _select_row(panel, row)
    gui.text('tool_diameter', '20 mm'); gui.text('tool_shank_diameter', '20 mm')
    result = _record(panel, 'Finishing cutter cannot reach any retained component', evidence, 'TOOL_TOO_LARGE')
    gui.check(not result['preview']['cuttable_polygons'] and result['preview']['blocked_polygons'],
              'Entirely unreachable artwork has red blocked geometry and no contradictory green cuttable geometry')
    painted = {item.data(1) for item in panel.view.scene().items()
               if hasattr(item, 'path') and not item.path().isEmpty()}
    gui.check('blocked' in painted and 'cuttable' not in painted, 'Actual all-unreachable preview paints only the blocked classification')
    _select_row(panel, row)
    gui.text('tool_diameter', '2 mm'); gui.text('tool_shank_diameter', '2 mm')
    _record(panel, 'Restored cutter permits circle review', evidence); review._accept(panel)
    operations = gui.widget('operationList', QtWidgets.QTreeWidget)
    item = next(operations.topLevelItem(i) for i in range(operations.topLevelItemCount())
                if operations.topLevelItem(i).data(0,QtCore.Qt.UserRole).startswith('board:'))
    operations.scrollToItem(item); gui.ensure_visible(operations)
    QtTest.QTest.mouseClick(operations.viewport(),QtCore.Qt.LeftButton,pos=operations.visualItemRect(item).center())
    def coarse(dialog):
        gui.check(dialog.objectName() == 'operationOverrideDialog', 'Uncut residue fixture uses the actual per-use spacing control')
        gui.text('override_stepover','4 mm')
        gui.click(dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok))
    gui.modal_action(lambda:gui.click('overrideOperation'),coarse)
    result = _record(panel, 'Board-only coarse raster leaves a known central residue', evidence, 'UNCUTTABLE_RESIDUE')
    polygons = [p for d in result['diagnostics'] if d['code']=='UNCUTTABLE_RESIDUE' and d.get('face')=='board'
                for p in d['polygons']]
    path = QtGui.QPainterPath(); path.setFillRule(QtCore.Qt.OddEvenFill)
    for polygon in polygons:
        for ring in [polygon['outer']]+polygon.get('holes',[]):
            path.moveTo(*ring[0])
            for point in ring[1:]: path.lineTo(*point)
            path.closeSubpath()
    gui.check(path.contains(QtCore.QPointF(0,0)) and not path.contains(QtCore.QPointF(6,6)) and
              all(abs(value)<=3+1e-8 for polygon in polygons for point in polygon['outer'] for value in point),
              'Verifier residue is expressed once in canonical circle coordinates, with a known centre witness')
    blocked_paths = [i.path() for i in panel.view.scene().items() if i.data(1)=='blocked' and hasattr(i,'path')]
    gui.check(any(p.contains(QtCore.QPointF(6,6)) for p in blocked_paths) and
              not any(p.contains(QtCore.QPointF(12,12)) for p in blocked_paths),
              'Actual red overlay locates central residue on the chosen face without applying its translation twice')
    witness = QtCore.QPointF(6,6)
    fills = [item for item in panel.view.scene().items(witness)
             if item.data(1) in ('blocked','proposal','cuttable','source','selected') and
             hasattr(item,'path') and item.path().contains(witness)]
    gui.check(fills and fills[0].data(1)=='blocked' and fills[0].brush().color().alpha()==255,
              'Blocked centre is the topmost opaque classification fill, above any overlapping gold proposal')
    gui.check(review._reveal_and_measure(panel.view)['visible_fraction']==1,
              'Blocked residue preview is fully visible for the rendered-colour check')
    point = panel.view.mapFromScene(witness)
    rendered = panel.view.viewport().grab().toImage()
    ratio = rendered.devicePixelRatio()
    colour = rendered.pixelColor(round(point.x()*ratio),round(point.y()*ratio))
    gui.check(colour.name().lower()=='#dc5a48', 'Actual rendered blocked-centre pixel is red, not gold or green')
    evidence.append({'case':'blocked visual priority','scene_witness':[6,6],
                     'topmost_fill':fills[0].data(1),'rendered_rgba':[colour.red(),colour.green(),colour.blue(),colour.alpha()]})
    gui.screenshot('edge-bounds-canonical-residue')
    # Restore through the same native per-use dialog before the next fixture.
    item = next(operations.topLevelItem(i) for i in range(operations.topLevelItemCount())
                if operations.topLevelItem(i).data(0,QtCore.Qt.UserRole).startswith('board:'))
    operations.scrollToItem(item); gui.ensure_visible(operations)
    QtTest.QTest.mouseClick(operations.viewport(),QtCore.Qt.LeftButton,pos=operations.visualItemRect(item).center())
    gui.check(panel.selected_operation == item.data(0,QtCore.Qt.UserRole),
              'Recalculated blocked board operation is explicitly reselected before restoring inheritance')
    def inherit(dialog):
        review._checked('restoreInheritedSettings',True)
        gui.click(dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok))
    gui.modal_action(lambda:gui.click('overrideOperation'),inherit)
    # A whole tiny component cannot silently disappear during cutter conditioning.
    source = review._write('edge-small-component.svg',
        '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="8mm" viewBox="0 0 10 8">'
        '<rect id="body" width="8" height="8"/><rect id="tiny" x="9" y="0" width=".5" height=".5"/></svg>')
    gui.tab(0)
    for side in ('board', 'plug'):
        gui.text(side+'_width', '20 mm'); gui.text(side+'_height', '20 mm')
    review._import(panel, source); gui.click('center_both')
    _record(panel, 'Retained tiny component would be destroyed', evidence, 'TOOL_TOO_LARGE')
    gui.tab(1)
    regions = gui.widget('artworkRegions', QtWidgets.QListWidget)
    item = next(regions.item(i) for i in range(regions.count()) if 'tiny' in regions.item(i).text())
    regions.scrollToItem(item)
    QtTest.QTest.mouseClick(regions.viewport(), QtCore.Qt.LeftButton, pos=regions.visualItemRect(item).center())
    if item.checkState() != QtCore.Qt.Unchecked:
        QtTest.QTest.keyClick(regions, QtCore.Qt.Key_Space)
    gui.check(item.checkState() == QtCore.Qt.Unchecked, 'User explicitly excludes the unreachable tiny component')
    result = _record(panel, 'Explicit paired exclusion recovers the retained body', evidence)
    gui.check(result['status'] == 'needs_acceptance' and result['preview']['excluded_polygons'],
              'Excluded component is preserved visibly and the retained pair requires review')
    review._accept(panel)
    gui.check(panel.calculated_spec['excluded_region_ids'] == ['tiny'], 'One exclusion is shared by both mating faces')
    gui.check(not doc.Objects, 'All bounds and exclusion reviews leave native output untouched')
    review._write('edge-bounds-evidence.json', json.dumps(evidence, indent=2))
    gui.close_task()
    return {'supporting_rows': ['E08', 'E09'], 'scope': 'Four analytic edge areas, margin, retained collar and explicit component-loss recovery'}


def _library_hashes():
    from Path import Preferences
    roots = [Preferences.getAssetPath(), Preferences.getBuiltinAssetPath()]
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for root in roots for pattern in ('*.fctb', '*.fctl') for path in root.rglob(pattern)}


def _save_export(doc, jobs):
    destination = gui.OUT/'paired-inlay.FCStd'
    gui.modal_action(lambda: Gui.runCommand('Std_SaveAs'), lambda dialog: gui.fill_file_dialog(dialog, destination))
    gui.check(destination.exists(), 'Native Save As persists the accepted mixed-unit lattice pair')
    outputs = {}
    for side in ('board', 'plug'):
        gui.select_native_job(doc.getObject(jobs[side]))
        output = gui.OUT/(side+'.ngc')
        def post(dialog):
            if isinstance(dialog, QtWidgets.QFileDialog):
                return gui.fill_file_dialog(dialog, output)
            editor = dialog.findChild(QtWidgets.QTextEdit)
            gui.check(editor is not None and 'G61' in editor.toPlainText(), 'Ordinary CAM shows qualified LinuxCNC output')
            gui.click(dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok))
            return 'native-preview'
        gui.modal_action(lambda: Gui.runCommand('CAM_Post'), post, timeout=120)
        gui.check(output.exists() and output.stat().st_size > 100, 'Actual native CAM exports '+side+' with independent per-use settings')
        outputs[side] = str(output)
    Gui.runCommand('Std_Save')
    return {'document': str(destination), 'jobs': jobs, 'outputs': outputs}


def edge_tools():
    doc, panel, row = review._small_flat()
    evidence = []
    before = _library_hashes()
    gui.check(bool(before), 'Actual native library definitions are available for source-hash comparison')
    for shape, expected in (('v', {'diameter': 10., 'included_angle_deg': 60., 'tip_diameter': .1}),
                             ('bull', {'diameter': 6., 'corner_radius': 1.5})):
        native_row = next(i for i,t in enumerate(panel.tools) if t['shape'] == shape and
                          (shape != 'v' or t.get('included_angle_deg') == 60))
        _toggle_tool(panel, native_row, True)
        gui.check(all(abs(panel.tools[native_row][key]-value) < 1e-9 for key,value in expected.items()),
                  'Actual native '+shape+' dimensions match the independently read bundled definition')
        _toggle_tool(panel, native_row, False)
    gui.check(panel.selected_tools == {panel.tools[row]['id']},
              'Browsing/selecting/deselecting native V and bull cutters preserves the chosen flat cutter')
    unsupported = next(i for i,t in enumerate(panel.tools) if t.get('supported') is False)
    table = _select_row(panel, unsupported)
    item = table.item(unsupported, 0)
    gui.check(not item.flags() & QtCore.Qt.ItemIsEnabled, 'Unsupported native cutter has a disabled selection control')
    QtTest.QTest.mouseClick(table.viewport(), QtCore.Qt.LeftButton, pos=table.visualItemRect(item).center())
    gui.check(panel.tools[unsupported]['id'] not in panel.selected_tools,
              'Clicking an unsupported library cutter cannot add it to the machining selection')
    gui.check('supports flat' in panel.tool_description.text().lower(), 'Unsupported cutter has a visible shape-scope explanation')
    _select_row(panel, row)
    gui.text('tool_feed', '0 mm/min')
    result = _record(panel, 'Incomplete feed setting', evidence, 'INVALID_INPUT')
    gui.check(any('positive feed' in d['message'] for d in result['diagnostics']), 'Missing speed names the specific incomplete parameter')
    gui.tab(2); gui.text('tool_feed', '500 mm/min')
    gui.text('tool_cutting_length', '0.5 mm')
    _record(panel, 'Insufficient cutting reach', evidence, 'CUTTING_REACH')
    gui.tab(2); gui.text('tool_cutting_length', '5 mm')
    gui.text('tool_diameter', '0.125 in'); gui.text('tool_shank_diameter', '1/8 in')
    gui.check(abs(panel.tools[row]['diameter']-3.175) < 1e-12 and
              abs(panel.tools[row]['shank_diameter']-3.175) < 1e-12,
              'Decimal and fractional imperial dimensions both represent the independent 3.175 mm bit')
    gui.configure_cutter(panel, 'flat', 3.175, 77)
    chosen = [t for t in panel.tools if t['id'] in panel.selected_tools]
    gui.check(len(chosen) == 2 and len({t['id'] for t in chosen}) == 2 and
              len({t['number'] for t in chosen}) == 2 and all(abs(t['diameter']-3.175) < 1e-12 for t in chosen),
              'Equal physical diameters retain two distinct tool identities and tool numbers')
    gui.tab(0)
    review._spin('plug_operations_limit', 2); review._spin('plug_tool_changes_limit', 1)
    result = _record(panel, 'Only board operation and change caps exceeded', evidence, 'COUNT_LIMIT')
    limited = [d for d in result['diagnostics'] if d['code'] == 'COUNT_LIMIT']
    gui.check(limited and all(d['face'] == 'board' for d in limited), 'Per-face caps diagnose only the independently limited board')
    gui.check(all(c == {'operations': 2, 'tool_changes': 1} for c in result['per_face_counts'].values()),
              'Two equal-diameter physical cutters still produce two uses and one change on each face')
    gui.tab(0)
    review._checked('board_operations_enabled', False)
    result = _record(panel, 'Disabling only the operation cap preserves the change cap', evidence, 'COUNT_LIMIT')
    limited = [d for d in result['diagnostics'] if d['code'] == 'COUNT_LIMIT']
    gui.check(len(limited) == 1 and 'tool changes' in limited[0]['message'],
              'An independently disabled operation cap does not disable the physical-change cap')
    gui.tab(0); review._checked('board_tool_changes_enabled', False)
    result = _record(panel, 'Both board caps explicitly disabled while plug caps remain active', evidence)
    gui.check(result['status'] == 'needs_acceptance' and
              all(v is None for v in panel.calculated_spec['faces']['board']['limits'].values()),
              'Explicitly disabling the two board caps recovers the requested pair without dropping tools')
    gui.tab(0)
    review._checked('board_operations_enabled', True); review._checked('board_tool_changes_enabled', True)
    review._spin('board_operations_limit', 2); review._spin('board_tool_changes_limit', 1)
    result = _record(panel, 'Independent cap increase permits the requested tools', evidence)
    gui.check(result['status'] == 'needs_acceptance', 'Permitted equal-diameter uses still require explicit geometry acceptance')
    review._accept(panel)
    gui.check({o['tool_id'] for o in panel.result['operations']} == panel.selected_tools,
              'Both distinct selected cutters contribute to the accepted pair')
    gui.check(_library_hashes() == before, 'Local geometry/speed edits and calculations preserve every actual library file hash')
    gui.check(not doc.Objects, 'Tool review does not create document objects')
    review._write('edge-tools-library-hashes.json', json.dumps(before, indent=2))
    review._write('edge-tools-evidence.json', json.dumps(evidence, indent=2))
    gui.screenshot('edge-tools-accepted-distinct-tools')
    gui.close_task()
    return {'supporting_rows': ['E05', 'E10', 'E11', 'E16'], 'scope': 'Unsupported/incomplete/reach gates, explicit imperial bits, equal-diameter identities, independent caps and untouched source library'}


def edge_fit():
    doc, panel, row = review._small_flat()
    evidence = []
    for field, invalid, restored, reason in (
        ('glue_gap', '1 mm', '0.2 mm', 'no positive mating height'),
        ('side_clearance', '-0.01 mm', '0.2 mm', 'rigid interference'),
        ('backing_clearance', '-0.01 mm', '0.5 mm', 'cannot be negative')):
        gui.tab(3); gui.text(field, invalid)
        result = _record(panel, 'Infeasible independent '+field, evidence, 'INVALID_INPUT')
        gui.check(any(reason in d['message'] for d in result['diagnostics']), field+' explains its physical incompatibility')
        gui.tab(3); gui.text(field, restored)
    gui.text('resolution_z', '0.07 mm')
    result = _record(panel, 'Final depth is off the Z movement lattice', evidence, 'INVALID_INPUT')
    gui.check(any('not representable' in d['message'] for d in result['diagnostics']),
              'Off-lattice final depth is rejected rather than silently moved')
    gui.tab(3); gui.text('resolution_z', '0.025 mm'); gui.text('side_clearance', '0 mm')
    result = _record(panel, 'Zero nominal side fit at distinct XY and Z resolutions', evidence)
    gui.check(result['status'] == 'needs_acceptance', 'Zero nominal fit is not silently certified as exact contact')
    review._accept(panel)
    first_hash = panel.result['proposal']['hash']
    library_before = _library_hashes()
    gui.tab(2)
    gui.text('tool_diameter', '1/8 in'); gui.text('tool_shank_diameter', '0.125 in')
    gui.text('tool_cutting_length', '1/4 in')
    gui.check(abs(panel.tools[row]['diameter']-3.175) < 1e-12 and
              abs(panel.tools[row]['cutting_length']-6.35) < 1e-12,
              'Mixed imperial cutter dimensions retain independently known physical quantities')
    gui.tab(1); gui.text('board_design_x', '10.025 mm'); gui.text('plug_design_x', '10.025 mm')
    gui.check(not gui.widget('applyPair').isEnabled(), 'Half-XY-step placement immediately invalidates accepted paths')
    result = _record(panel, 'Half-XY-step placement requires a new reviewed realization', evidence)
    gui.check(result['status'] == 'needs_acceptance' and result['proposal']['hash'] != first_hash,
              'Cutter and placement phase changes require a newly accepted realization identity')
    review._accept(panel)
    original_tools = deepcopy(panel.tools)
    other_use = deepcopy(next(o for o in panel.result['operations'] if o['face'] == 'plug'))
    operations = gui.widget('operationList', QtWidgets.QTreeWidget)
    item = next(operations.topLevelItem(i) for i in range(operations.topLevelItemCount())
                if operations.topLevelItem(i).data(0, QtCore.Qt.UserRole).startswith('board:'))
    operations.scrollToItem(item); gui.ensure_visible(operations)
    QtTest.QTest.mouseClick(operations.viewport(), QtCore.Qt.LeftButton, pos=operations.visualItemRect(item).center())
    def override(dialog):
        gui.check(dialog.objectName() == 'operationOverrideDialog', 'Independent pass/speed edits use the actual per-operation dialog')
        for key, value in (('feed', '20 in/min'), ('plunge_feed', '6 in/min'), ('travel_feed', '40 in/min'),
                           ('spindle', '9000 rpm'), ('max_stepdown', '0.25 mm'), ('stepover', '0.4 mm')):
            gui.text('override_'+key, value)
        gui.click(dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok))
        return 'all-six-settings'
    gui.modal_action(lambda: gui.click('overrideOperation'), override)
    gui.check(panel.tools == original_tools and not gui.widget('applyPair').isEnabled(),
              'Per-use pass and speed edits preserve shared cutter snapshots and invalidate old acceptance')
    _record(panel, 'Six independent settings apply only to the selected board use', evidence)
    review._accept(panel)
    board = next(o for o in panel.result['operations'] if o['face'] == 'board')
    plug = next(o for o in panel.result['operations'] if o['face'] == 'plug')
    expected_settings = {'feed': 508., 'plunge_feed': 152.4, 'travel_feed': 1016., 'spindle': 9000.,
                         'max_stepdown': .25, 'stepover': .4}
    gui.check(all(abs(board['settings'][key]-value) < 1e-10 for key,value in expected_settings.items()),
              'All six effective override values match independent physical unit conversions')
    gui.check(plug == other_use, 'The same physical cutter on the plug retains every original setting, motion and estimate')
    gui.check(all(c == {'operations': 1, 'tool_changes': 0} for c in panel.result['per_face_counts'].values()),
              'Six per-use changes do not invent a second tool or cross-face tool change')
    depths = sorted({round(-move['z'], 10) for move in board['moves'] if move['role'] == 'cut' and move['z'] < 0})
    gui.check(depths == [.25, .5, .75, 1.], 'Complete board cutting-depth set respects the independent quarter-mm override')
    gui.check(_library_hashes() == library_before, 'Per-use overrides and imperial dimension edits leave actual library files unchanged')
    spec = panel.calculated_spec
    gui.check(spec['resolution'] == {'xy': .05, 'z': .025} and spec['fit']['side_clearance'] == 0,
              'Certification retains the user resolutions and zero nominal fit without overwriting them')
    lattice_error = {axis: max(abs(move[axis]/quantum-round(move[axis]/quantum))
                              for operation in panel.result['operations'] for move in operation['moves'])
                     for axis, quantum in (('x', .05), ('y', .05), ('z', .025))}
    for axis, error in lattice_error.items():
        gui.check(error < 1e-7, 'Every accepted '+axis.upper()+' motion remains on its exact requested lattice')
    evidence.append({'case': 'Complete final move lattice', 'maximum_step_residual': lattice_error,
                     'move_count': sum(len(o['moves']) for o in panel.result['operations'])})
    review._write('edge-fit-lattice-spec.json', json.dumps(spec, indent=2))
    review._write('edge-fit-lattice-plan.json', json.dumps(panel.result, indent=2))
    review._write('spec.json', json.dumps(spec, indent=2))
    review._write('plan.json', json.dumps(panel.result, indent=2))
    gui.screenshot('edge-fit-zero-nominal-reviewed')
    def confirm(dialog):
        gui.check(dialog.objectName() == 'confirmApplyDialog', 'Mixed-unit lattice pair reaches the actual final confirmation')
        text = gui.widget('applyReviewText', QtWidgets.QPlainTextEdit, dialog).toPlainText()
        gui.check(all(str(value) in text for value in (508, 152.4, 1016, 9000, 500, 200, 1000, 10000)),
                  'Final review exposes distinct cutting/plunge/travel/spindle values for both uses')
        gui.click(dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok))
        return 'confirmed'
    gui.modal_action(lambda: gui.click('applyPair'), confirm, timeout=120)
    gui.check(panel.state == 'Applied', 'Actual confirmation creates the accepted mixed-unit lattice pair')
    jobs = dict(panel.applied)
    native_before = gui.native_snapshot(doc, jobs)
    review._write('native-snapshot.json', json.dumps(native_before, indent=2))
    review._write('jobs.json', json.dumps(jobs, indent=2))
    # A broad bull-nose root with no side/backing relief must remain blocked.
    _circle(panel)
    _toggle_tool(panel, row, False)
    gui.configure_cutter(panel, 'bull', 2, 17)
    gui.text('tool_corner_radius', '0.75 mm')
    gui.tab(3); gui.select('inlayStyle', 3)
    gui.text('resolution_z', '0.05 mm'); gui.text('backing_clearance', '0 mm')
    result = _record(panel, 'Bull root interferes despite positive commanded depths', evidence, 'ASSEMBLY_INTERFERENCE')
    gui.check(result['derived']['depth'] == 1 and abs(result['derived']['height']-.8) < 1e-12,
              'Root failure is evaluated at independently derived D1 and H0.8, not a silently altered fit')
    gui.screenshot('edge-fit-bull-root-rejection')
    gui.check(gui.native_snapshot(doc, jobs) == native_before,
              'Later incompatible bull-root draft preserves every previously applied native command')
    review._write('edge-fit-evidence.json', json.dumps(evidence, indent=2))
    gui.close_task()
    outputs = _save_export(doc, jobs)
    return dict(outputs, supporting_rows=['E05', 'E11', 'E12', 'E13', 'E14', 'E15', 'E16', 'E19', 'E26'],
                scope='Independent fit and root gates; mixed imperial cutter/feed units; six isolated per-use settings; distinct XY/Z and shifted zero-side-fit realization through native Apply/save/export')


def _flat_depth_at(point, operations, radius):
    """Independent exact flat-cutter contact for this axial/constant-Z fixture."""
    deepest = 0.
    for operation in operations:
        for a,b in zip(operation['moves'], operation['moves'][1:]):
            if min(a['z'], b['z']) >= 0:
                continue
            dx,dy = b['x']-a['x'], b['y']-a['y']
            length2 = dx*dx+dy*dy
            if length2 and abs(a['z']-b['z']) > 1e-10:
                raise AssertionError('This independent flat fixture oracle does not certify ramps')
            t = max(0., min(1., ((point[0]-a['x'])*dx+(point[1]-a['y'])*dy)/length2)) if length2 else 0.
            distance2 = (point[0]-a['x']-t*dx)**2+(point[1]-a['y']-t*dy)**2
            if distance2 <= radius*radius:
                deepest = max(deepest, -min(a['z'], b['z']))
    return deepest


def edge_inputs_depth():
    doc, panel, _ = review._small_flat()
    evidence = []
    for index, canvas in enumerate(('20mm', f'{20/25.4:.17g}in')):
        source = review._write('edge-nested-'+str(index)+'.svg',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas}" height="{canvas}" viewBox="0 0 20 20">'
            '<g transform="translate(2 3)" fill="#123456" fill-rule="evenodd"><g transform="scale(2 1)">'
            '<path id="compound" d="M0 0 H4 V6 H0 Z M1 2 H3 V4 H1 Z"/></g></g></svg>')
        imported = review._import(panel, source)
        gui.check(not imported['diagnostics'], 'Nested transforms and inherited opaque fill are supported through the real import control')
        region = imported['regions'][0]
        gui.check(region['id'] == 'compound' and region['fill'] == '#123456' and
                  len(region['polygons']) == 1 and len(region['polygons'][0]['holes']) == 1,
                  'Nested inherited compound preserves its source ID, fill and counter')
        gui.check(abs(review._area(region['polygons'])-40) < 1e-9 and
                  all(abs(a-b) < 1e-9 for a,b in zip(imported['bounds'], [2,11,10,17])),
                  'Millimetre and inch canvases both match independently transformed area40 and bounds2,11,10,17')
        evidence.append({'case': 'nested units '+canvas, 'area': review._area(region['polygons']), 'bounds': imported['bounds']})
    gui.click('center_both'); gui.click('fitPreview')
    original = deepcopy(panel.imported)
    view = gui.widget('artworkPreview', QtWidgets.QGraphicsView)
    gui.check(review._reveal_and_measure(view)['visible_fraction'] == 1,
              'Counter preview is fully visible after native scrolling')
    source_items = [i for i in view.scene().items() if i.data(1) == 'source' and hasattr(i, 'path')]
    gui.check(any(i.path().contains(QtCore.QPointF(6.5,7.5)) for i in source_items) and
              not any(i.path().contains(QtCore.QPointF(10,10)) for i in source_items),
              'Visible nested compound has material at a known corner and air at its counter centre')
    centre = view.viewport().rect().center()
    scale = abs(view.transform().m11())
    event = QtGui.QWheelEvent(QtCore.QPointF(centre), QtCore.QPointF(view.viewport().mapToGlobal(centre)),
                             QtCore.QPoint(), QtCore.QPoint(0,120), QtCore.Qt.NoButton,
                             QtCore.Qt.NoModifier, QtCore.Qt.NoScrollPhase, False)
    QtWidgets.QApplication.sendEvent(view.viewport(), event)
    gui.check(abs(view.transform().m11()) > scale, 'Actual wheel zoom makes counter-edge portion selection finer')
    gui.select('selectionMode', 2)
    gui.check(review._reveal_and_measure(view)['visible_fraction'] == 1,
              'Counter preview remains fully visible for actual drag selection')
    start,end = [view.mapFromScene(QtCore.QPointF(*p)) for p in ((7.5,9.5),(8.5,10.5))]
    gui.check(view.viewport().rect().contains(start) and view.viewport().rect().contains(end),
              'Fine exclusion drag straddles the independently known counter boundary within the visible preview')
    real_start,real_end = view.mapToScene(start),view.mapToScene(end)
    QtTest.QTest.mousePress(view.viewport(), QtCore.Qt.LeftButton, pos=start)
    QtTest.QTest.mouseMove(view.viewport(), end, delay=30)
    QtTest.QTest.mouseRelease(view.viewport(), QtCore.Qt.LeftButton, pos=end)
    spec = panel.get_spec()
    gui.check(len(spec['exclusions']) == 1 and panel.imported == original,
              'Counter-edge portion selection creates one paired exclusion without modifying the source hole')
    actual = spec['exclusions'][0]['outer']
    expected = [min(real_start.x(),real_end.x())-10, min(real_start.y(),real_end.y())-10,
                max(real_start.x(),real_end.x())-10, max(real_start.y(),real_end.y())-10]
    bounds = [min(p[0] for p in actual), min(p[1] for p in actual), max(p[0] for p in actual), max(p[1] for p in actual)]
    gui.check(all(abs(a-b) < 1e-8 for a,b in zip(bounds, expected)),
              'Recorded paired exclusion matches the actual mouse-to-scene rectangle independently of pixel rounding')
    gui.screenshot('edge-inputs-counter-portion')
    gui.click('restoreExclusions')
    gui.check(not panel.source_exclusions and not panel.excluded_ids and panel.imported == original,
              'Restoring exclusions recovers the unchanged compound and counter')
    bad = review._write('edge-self-crossing.svg',
        '<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="20mm" viewBox="0 0 20 20">'
        '<path id="crossing" d="M0 0 L4 4 L0 4 L4 0 Z"/><rect id="supported" x="10" y="10" width="4" height="4"/></svg>')
    imported = review._import(panel, bad)
    gui.check(any('Self-intersecting' in d['message'] and d['region_ids'] == ['crossing'] for d in imported['diagnostics']),
              'Self-intersecting outline is diagnosed by its exact source region')
    gui.check(abs(review._area([p for r in imported['regions'] for p in r['polygons']])-16) < 1e-9,
              'Supported companion square remains selectable at its independently known area')
    gui.click('center_both')
    _record(panel, 'Unresolved self-intersecting source prevents paired Apply', evidence, 'SVG_UNSUPPORTED')
    gui.tab(1)
    regions = gui.widget('artworkRegions', QtWidgets.QListWidget)
    item = next(regions.item(i) for i in range(regions.count()) if 'crossing' in regions.item(i).text())
    regions.scrollToItem(item)
    QtTest.QTest.mouseClick(regions.viewport(), QtCore.Qt.LeftButton, pos=regions.visualItemRect(item).center())
    if item.checkState() != QtCore.Qt.Unchecked: QtTest.QTest.keyClick(regions, QtCore.Qt.Key_Space)
    gui.check(item.checkState() == QtCore.Qt.Unchecked, 'Unsupported source is excluded explicitly through the region control')
    result = _record(panel, 'Explicit unsupported-source exclusion recovers supported artwork', evidence)
    gui.check(result['status'] == 'needs_acceptance', 'The retained supported square can be calculated only after explicit exclusion')

    # Restore the literal asymmetric fixture for independent depth/clearance probes.
    review._import(panel, gui.OUT/'review-key.svg'); gui.click('center_both')
    def settle(label, glue, backing, height):
        result = _record(panel, label, evidence)
        if result['status'] == 'needs_acceptance': review._accept(panel)
        gui.check(panel.result['status'] == 'ready', label+' is a fully accepted calculated pair')
        spec,result = panel.calculated_spec,panel.result
        gui.check(abs(result['derived']['depth']-1) < 1e-10 and abs(result['derived']['height']-height) < 1e-10,
                  label+' agrees with independent H = D - glue + backing')
        operations = {face:[o for o in result['operations'] if o['face'] == face] for face in ('board','plug')}
        measured = []
        for canonical, wanted in (((0,2), glue), ((-6,-6), backing)):
            board_xy = (10+canonical[0],10+canonical[1])
            plug_xy = (10-canonical[0],10+canonical[1])
            gap = (_flat_depth_at(board_xy,operations['board'],1)+
                   _flat_depth_at(plug_xy,operations['plug'],1)-(1-glue))
            gui.check(abs(gap-wanted) < 1e-9, label+' independently measured scalar gap = '+str(wanted)+' mm')
            measured.append({'canonical':canonical, 'expected_gap_mm':wanted, 'measured_gap_mm':gap})
        evidence.append({'case':label+' physical references', 'fit':spec['fit'], 'measurements':measured})
        name = label.lower().replace(' ','-')
        review._write(name+'-spec.json',json.dumps(spec,indent=2))
        review._write(name+'-plan.json',json.dumps(result,indent=2))
        return deepcopy(result['operations'])
    baseline = settle('Depth baseline', .2, .5, 1.3)
    gui.tab(3); gui.select('depthDriver', 1)
    gui.check(abs(panel._value('fit_value')-1.3) < 1e-10, 'Switching to H chooses the existing physical plug height')
    from_height = settle('Equivalent height driver', .2, .5, 1.3)
    gui.check(from_height == baseline, 'Actual D-to-H recalculation preserves every motion, setting and estimate')
    gui.tab(3); gui.select('depthDriver', 0); gui.text('glue_gap', '0.3 mm')
    settle('Independent glue increase', .3, .5, 1.2)
    gui.tab(3); gui.text('backing_clearance', '0.6 mm')
    settle('Independent backing increase', .3, .6, 1.3)
    gui.tab(3); gui.text('side_clearance', '0.25 mm')
    settle('Independent side increase', .3, .6, 1.3)
    gui.check(panel.calculated_spec['fit']['side_clearance'] == .25 and
              panel.calculated_spec['fit']['glue_gap'] == .3 and panel.calculated_spec['fit']['backing_clearance'] == .6,
              'Three independent fit inputs retain their exact chosen values')
    gui.check(not doc.Objects, 'Input/selection/depth review creates no native machining objects')
    gui.screenshot('edge-inputs-independent-depth-review')
    review._write('edge-inputs-depth-evidence.json',json.dumps(evidence,indent=2))
    gui.close_task()
    return {'supporting_rows':['E02','E03','E09','E14'],
            'scope':'Nested/inherited mixed-unit input, unresolved self-crossing gate, counter-adjacent portion selection, actual D/H equivalence and independent full-segment flat-tool bottom/backing probes'}


CASES = {'edge_bounds': edge_bounds, 'edge_tools': edge_tools, 'edge_fit': edge_fit,
         'edge_inputs_depth': edge_inputs_depth}
