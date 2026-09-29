"""Real packaged GUI resource boundary and successful smaller-draft recovery.

All artwork, settings, Calculate, acceptance and Apply actions use task widgets.
No result, native preflight, memory limit or production method is substituted.
"""
from copy import deepcopy
import json
import time

import FreeCAD as App
from PySide6 import QtWidgets

import journeys as gui
from review_journeys import _accept, _calculate, _import


def _record(name, value):
    with (gui.OUT/name).open('w') as stream:
        json.dump(value, stream, indent=2)


def _settings(panel, dimensions, cutter, fit):
    gui.tab(0)
    for side in ('board', 'plug'):
        for key, value in zip(('width', 'height', 'thickness'), dimensions):
            gui.text(side+'_'+key, str(value)+' mm')
        gui.text(side+'_clearance', '2 mm')
    gui.tab(1)
    gui.click('center_both')
    gui.tab(2)
    for key, value in cutter.items():
        gui.text('tool_'+key, str(value)+' mm')
    gui.tab(3)
    gui.select('inlayStyle', 1)
    for key, value in fit.items():
        gui.text(key, str(value)+' mm')


def _plan_summary(panel):
    return {'status': panel.result['status'],
            'motion_count': sum(len(op['moves']) for op in panel.result['operations']),
            'counts': panel.result['per_face_counts'],
            'verification': panel.result['verification'],
            'worker_resource_stats': panel.result['resource_stats'],
            'input_hash': panel.result['input_hash'],
            'source_region_count': len(panel.imported['regions']),
            'gui_memory': gui.process_memory()}


def _confirm():
    def answer(dialog):
        gui.check(dialog.objectName() == 'confirmApplyDialog', 'Actual paired review dialog is visible')
        gui.click(gui.widget('confirmApplyButtons', QtWidgets.QDialogButtonBox, dialog).button(QtWidgets.QDialogButtonBox.Ok))
        return 'confirmed through GUI'
    gui.modal_action(lambda: gui.click('applyPair'), answer, timeout=120)


def resource_guard():
    started = time.monotonic()
    doc = App.newDocument('InlayResourceBoundary')
    panel = gui.launch_command()
    square = gui.OUT/'large-square.svg'
    square.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="140mm" height="140mm" viewBox="0 0 140 140"><rect width="140" height="140"/></svg>')
    _import(panel, square)
    gui.configure_cutter(panel, 'flat', 12.7, 1)
    _settings(panel, [609.6, 609.6, 19.05],
              {'diameter': 12.7, 'cutting_length': 20, 'shank_diameter': 12.7,
               'max_stepdown': 1.5, 'stepover': 4},
              {'fit_value': 3, 'glue_gap': .2, 'side_clearance': .15, 'backing_clearance': 1,
               'resolution_xy': .01, 'resolution_z': .01})
    large_start = time.monotonic()
    _calculate(panel, 'needs_acceptance')
    _accept(panel)
    large = _plan_summary(panel)
    large['calculate_and_accept_seconds'] = time.monotonic()-large_start
    points = [point for region in panel.calculated_spec['artwork']['regions']
              for polygon in region['polygons'] for point in polygon['outer']]
    gui.check(all(abs(max(p[axis] for p in points)-min(p[axis] for p in points)-140) < 1e-8
                  for axis in (0, 1)), 'Large fixture retains its physical 140mm artwork extent')
    gui.check(panel.calculated_spec['faces']['board']['stock'] == [609.6, 609.6, 19.05],
              'Real GUI calculated and accepted a 24-inch stock pair')
    gui.check(large['verification']['status'] == 'passed', 'Accepted 24-inch result passes continuous verification')
    gui.check(not doc.Objects, 'Calculating the 24-inch pair creates no native document objects')
    _record('large-gui.json', large)
    gui.screenshot('resource-01-large-ready')

    _import(panel, gui.ADDON/'examples/dense_decorative_field.svg')
    _settings(panel, [200, 160, 6],
              {'diameter': 1, 'cutting_length': 5, 'shank_diameter': 1,
               'max_stepdown': .5, 'stepover': .4},
              {'fit_value': .5, 'glue_gap': .1, 'side_clearance': .1, 'backing_clearance': .3,
               'resolution_xy': .05, 'resolution_z': .05})
    gui.check(len(panel.imported['regions']) == 500, 'Actual dense SVG imports all 500 filled regions')
    dense_start = time.monotonic()
    _calculate(panel, 'needs_acceptance')
    _accept(panel)
    dense = _plan_summary(panel)
    dense['calculate_and_accept_seconds'] = time.monotonic()-dense_start
    gui.check(dense['motion_count'] > 150000, 'Dense GUI result contains the actual large motion set')
    gui.check(dense['verification']['status'] == 'passed', 'Dense accepted result passes continuous verification')
    _record('dense-gui.json', dense)
    _record('dense-spec.json', panel.calculated_spec)
    gui.screenshot('resource-02-dense-ready')

    draft = deepcopy(panel.get_spec())
    objects_before = sorted(obj.Name for obj in doc.Objects)
    documents_before = sorted(App.listDocuments())
    memory_before = gui.process_memory()
    _confirm()
    message = panel.status.text()
    memory_after = gui.process_memory()
    outcome = {'message': message, 'state': panel.state,
               'before_memory': memory_before, 'after_memory': memory_after,
               'motion_count': dense['motion_count'], 'objects_before': objects_before,
               'objects_after': sorted(obj.Name for obj in doc.Objects),
               'draft_preserved': panel.get_spec() == draft}
    if panel.state == 'Applied':
        outcome['outcome'] = 'Actual native preflight admitted and applied the dense pair'
        outcome['jobs'] = dict(panel.applied)
        outcome['native_commands'] = sum(len(op.Path.Commands) for side in ('board', 'plug')
                                          for op in doc.getObject(panel.applied[side]).Operations.Group)
        gui.check(outcome['native_commands'] >= dense['motion_count'], 'Admitted dense pair contains its native commands')
    else:
        gui.check('2 GiB application budget' in message, 'Actual native Apply memory preflight rejects the oversized pair visibly')
        gui.check(outcome['objects_after'] == objects_before, 'Memory preflight leaves all native document objects untouched')
        gui.check(sorted(App.listDocuments()) == documents_before, 'Memory preflight leaves no native staging document')
        gui.check(not App.getActiveTransaction(), 'Memory rejection leaves no active native document transaction')
        gui.check(outcome['draft_preserved'], 'Memory rejection preserves the complete dense draft')
        gui.check(not gui.widget('applyPair').isEnabled(), 'Memory rejection cannot leave stale Apply enabled')
        outcome['outcome'] = 'Expected preflight rejection without native writes'
    _record('native-memory-boundary.json', outcome)
    gui.screenshot('resource-03-memory-boundary')

    small = gui.OUT/'recovery-key.svg'
    small.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="8mm" height="8mm" viewBox="0 0 8 8"><path d="M0 0 H8 V3 H6 V8 H0 Z"/></svg>')
    _import(panel, small)
    gui.check(panel.imported['bounds'] == [0., 0., 8., 8.], 'Recovery key retains its physical 8mm extent')
    _settings(panel, [20, 20, 6],
              {'diameter': 2, 'cutting_length': 5, 'shank_diameter': 2,
               'max_stepdown': .5, 'stepover': .8},
              {'fit_value': 1, 'glue_gap': .2, 'side_clearance': .2, 'backing_clearance': .5,
               'resolution_xy': .05, 'resolution_z': .05})
    _calculate(panel, 'needs_acceptance')
    _accept(panel)
    recovery = _plan_summary(panel)
    _confirm()
    gui.check(panel.state == 'Applied', 'Same task recovers and applies a smaller pair through actual review')
    gui.check(all(doc.getObject(panel.applied[side]).Operations.Group for side in ('board', 'plug')),
              'Recovered smaller board and plug both contain native machining operations')
    recovery['jobs'] = dict(panel.applied)
    recovery['after_apply_memory'] = gui.process_memory()
    _record('resource-recovery.json', recovery)
    gui.screenshot('resource-04-smaller-native-output')
    gui.close_task()
    return {'supporting_rows': ['E28', 'E30'], 'large': large, 'dense': dense,
            'native_boundary': outcome, 'recovery': recovery,
            'elapsed_case_seconds': time.monotonic()-started,
            'scope': 'Actual packaged UI 24-inch and 500-region calculation, acceptance, native memory admission/rejection and smaller-pair recovery; no 4 GiB whole-machine claim.'}


CASES = {'resource_guard': resource_guard}
