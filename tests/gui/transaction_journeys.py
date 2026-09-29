"""Real GUI count boundaries, atomic Apply, native Undo/Redo, and recovery.

Only the documented fault-injection argument is wrapped in the rollback case.
The planner, verification, review dialog, and user Apply path all run normally.
Unrelated CAM work is a native fixture, not a mocked planner result.
"""
import hashlib
import json

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide6 import QtCore, QtGui, QtWidgets, QtTest

import journeys as gui


def _write(name,value):
    (gui.OUT/name).write_text(json.dumps(value,indent=2))


def _checked(name,value):
    control=gui.widget(name,QtWidgets.QCheckBox)
    if control.isChecked()!=value:
        gui.click(control)
    if control.isChecked()!=value:
        QtTest.QTest.keyClick(control,QtCore.Qt.Key_Space)
    gui.check(control.isChecked()==value,'Count-limit checkbox '+name+' is '+str(value))


def _spin(name,value):
    control=gui.widget(name,QtWidgets.QSpinBox)
    gui.click(control)
    QtTest.QTest.keySequence(control,QtGui.QKeySequence(QtGui.QKeySequence.SelectAll))
    QtTest.QTest.keyClicks(control,str(value))
    QtTest.QTest.keyClick(control,QtCore.Qt.Key_Tab)
    gui.check(control.value()==value,'Count-limit spinbox '+name+' is '+str(value))


def _limits(board_ops,plug_ops,board_changes,plug_changes):
    gui.tab(0)
    for side,operations,changes in [('board',board_ops,board_changes),('plug',plug_ops,plug_changes)]:
        for key,value in [('operations',operations),('tool_changes',changes)]:
            _checked(side+'_'+key+'_enabled',True)
            _spin(side+'_'+key+'_limit',value)


def _calculate(panel,expected,artifact):
    gui.click('calculate')
    gui.wait(lambda:not panel.calculation.busy,'Real paired count-boundary calculation',timeout=180)
    gui.check(panel.result is not None,'Count-boundary calculation produced a result')
    if expected=='ready' and panel.result.get('status')=='needs_acceptance':
        gui.click('acceptProposal')
        gui.wait(lambda:not panel.calculation.busy,'Count-boundary proposal acceptance',timeout=180)
    _write(artifact,{'spec':panel.calculated_spec,'result':panel.result})
    gui.check(panel.result.get('status')==expected,'Calculation state is '+expected+' for '+artifact)
    gui.check(gui.widget('applyPair').isEnabled()==(expected=='ready'),
              'Apply availability follows the verified count-boundary state')
    return panel.result


def _confirm_apply(panel):
    def answer(dialog):
        gui.check(dialog.objectName()=='confirmApplyDialog','Real pair confirmation is displayed')
        buttons=gui.widget('confirmApplyButtons',QtWidgets.QDialogButtonBox,dialog)
        gui.click(buttons.button(QtWidgets.QDialogButtonBox.Ok))
        return 'confirmed'
    gui.modal_action(lambda:gui.click('applyPair'),answer,timeout=120)


def _save(doc,name):
    destination=gui.OUT/name
    gui.modal_action(lambda:Gui.runCommand('Std_SaveAs'),lambda dialog:gui.fill_file_dialog(dialog,destination))
    gui.check(destination.exists() and doc.FileName==str(destination),'Native Save As persisted '+name)
    return str(destination)


def _export_jobs(doc,jobs):
    outputs={}
    for side in ('board','plug'):
        gui.select_native_job(doc.getObject(jobs[side]))
        output=gui.OUT/(side+'.ngc')
        def answer(dialog):
            if isinstance(dialog,QtWidgets.QFileDialog):
                return gui.fill_file_dialog(dialog,output)
            editor=dialog.findChild(QtWidgets.QTextEdit)
            gui.check(editor is not None and 'G61' in editor.toPlainText(),
                      'Count-boundary Job opens the native LinuxCNC preview')
            gui.click(dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok))
            return 'native-preview'
        gui.modal_action(lambda:Gui.runCommand('CAM_Post'),answer,timeout=120)
        gui.check(output.exists() and output.stat().st_size>100,
                  'Native CAM exports the three-use '+side+' Job')
        outputs[side]=str(output)
    Gui.runCommand('Std_Save')
    return outputs


def count_boundaries():
    doc,panel=gui.prepare_positive('multi_flat')
    gui.configure_cutter(panel,'flat',4,3)
    _limits(4,4,3,3)
    below=_calculate(panel,'ready','counts-below.json')
    counts={side:{'operations':3,'tool_changes':2} for side in ('board','plug')}
    gui.check(below['per_face_counts']==counts,'Three actual operations and two physical changes are planned for each face')
    gui.check(len(below['operations'])==6,'Six total operations remain allowed by independent four-operation face caps')
    _limits(2,4,3,3)
    above=_calculate(panel,'blocked','counts-board-above.json')
    limits=[d for d in above['diagnostics'] if d['code']=='COUNT_LIMIT']
    gui.check(bool(limits) and all(d.get('face')=='board' for d in limits),
              'Only the board operation cap blocks the paired design')
    gui.tab(0);_checked('board_operations_enabled',False)
    uncapped=_calculate(panel,'ready','counts-board-cap-disabled.json')
    gui.check(uncapped['per_face_counts']==counts and panel.calculated_spec['faces']['board']['limits']['operations'] is None,
              'Disabling only the exceeded board operation cap permits the unchanged three-tool plan')
    _limits(3,3,2,2)
    at=_calculate(panel,'ready','counts-at.json')
    gui.check(at['per_face_counts']==counts,'Actual operation and tool-change counts exactly at each face cap are accepted')
    _limits(3,3,2,1)
    above=_calculate(panel,'blocked','counts-plug-changes-above.json')
    limits=[d for d in above['diagnostics'] if d['code']=='COUNT_LIMIT']
    gui.check(bool(limits) and all(d.get('face')=='plug' for d in limits),
              'Only the plug physical tool-change cap blocks the paired design')
    gui.tab(0);_checked('plug_tool_changes_enabled',False)
    uncapped=_calculate(panel,'ready','counts-plug-cap-disabled.json')
    gui.check(uncapped['per_face_counts']==counts and panel.calculated_spec['faces']['plug']['limits']['tool_changes'] is None,
              'Disabling only the plug change cap retains all selected tools and the board limits')
    _limits(3,3,2,2)
    ready=_calculate(panel,'ready','counts-restored.json')
    shared_tool=ready['operations'][0]['tool_id']
    board_id=ready['operations'][0]['id']
    table=gui.widget('operationList',QtWidgets.QTreeWidget)
    gui.ensure_visible(table)
    table.scrollToItem(table.topLevelItem(0))
    QtTest.QTest.mouseClick(table.viewport(),QtCore.Qt.LeftButton,pos=table.visualItemRect(table.topLevelItem(0)).center())
    def override(dialog):
        gui.check(dialog.objectName()=='operationOverrideDialog','Per-use feed override opens its real dialog')
        gui.text('override_feed','600 mm/min')
        gui.click(dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok))
        return 'feed600'
    gui.modal_action(lambda:gui.click('overrideOperation'),override)
    ready=_calculate(panel,'ready','counts-per-use-feed.json')
    gui.check(ready['per_face_counts']==counts,'A per-use feed change does not add operations or physical changes')
    board=next(op for op in ready['operations'] if op['id']==board_id)
    plug=next(op for op in ready['operations'] if op['face']=='plug' and op['tool_id']==shared_tool)
    gui.check(board['settings']['feed']==600 and plug['settings']['feed']==500,
              'The same physical cutter retains separately configured board and plug feeds')
    _confirm_apply(panel)
    gui.check(panel.state=='Applied','The plan exactly at both face limits applies through its review dialog')
    jobs=dict(panel.applied)
    for side in ('board','plug'):
        native=doc.getObject(jobs[side]).Operations.Group
        gui.check(len(native)==3 and len({op.ToolController.ToolNumber for op in native})==3,
                  side.title()+' native Job contains the three actual cutter uses')
    _write('native-snapshot.json',gui.native_snapshot(doc,jobs))
    _write('spec.json',panel.calculated_spec)
    _write('plan.json',panel.result)
    _write('jobs.json',jobs)
    gui.screenshot('counts-accepted-native-output')
    gui.close_task()
    destination=_save(doc,'paired-inlay.FCStd')
    outputs=_export_jobs(doc,jobs)
    return {'document':destination,'jobs':jobs,'outputs':outputs,'per_face_counts':counts,
            'scope':'Actual per-face below/at/above operation and physical-change caps, independent faces, and per-use feeds; no synthetic planner results'}


def _unrelated_job(doc):
    import Path.Main.Job as Job
    import Path.Main.Gui.Job as JobGui
    import Path.Op.Custom as Custom
    import Path.Op.Gui.Custom as CustomGui
    import Path.Op.Gui.Base as OpGui
    stock=doc.addObject('Part::Feature','UnrelatedStock')
    stock.Shape=Part.makeBox(5,5,2,App.Vector(40,40,-2))
    doc.recompute()
    job=Job.Create('UnrelatedCamJob',[stock])
    job.Label='Existing unrelated CAM work'
    job.ViewObject.Proxy=JobGui.ViewProvider(job.ViewObject)
    job.ViewObject.addExtension('Gui::ViewProviderGroupExtensionPython')
    job.ViewObject.Proxy.deleteOnReject=False
    operation=Custom.Create('UnrelatedStoredPath',parentJob=job)
    operation.ToolController=job.Tools.Group[0]
    operation.Source='Text'
    operation.Gcode=['G90','G0 Z2','G0 X40 Y40','G1 Z-0.5 F5','G1 X41 Y40 F7','G0 Z2']
    operation.ViewObject.Proxy=OpGui.ViewProvider(operation.ViewObject,CustomGui.Command.res)
    operation.ViewObject.Proxy.setDeleteObjectsOnReject(False)
    operation.Proxy.execute(operation)
    doc.recompute()
    return job.Name


def _document_snapshot(doc):
    values={}
    for obj in doc.Objects:
        item={'type':obj.TypeId,'label':obj.Label,'properties':list(obj.PropertiesList)}
        if hasattr(obj,'Shape') and not obj.Shape.isNull():
            item['shape_sha256']=hashlib.sha256(obj.Shape.exportBrepToString().encode()).hexdigest()
        if hasattr(obj,'Path'):
            item['commands']=[{'name':command.Name,'parameters':dict(command.Parameters)} for command in obj.Path.Commands]
        for key in ('Group','Model','Stock','Tools','Operations','ToolController','Tool'):
            if key not in obj.PropertiesList:
                continue
            value=getattr(obj,key)
            if isinstance(value,(list,tuple)):
                item[key]=[v.Name for v in value]
            elif value is not None:
                item[key]=value.Name
        for key in ('InlaySettings','InlayEstimate','InlayInputHash','InlayOperationId','PostProcessor','PostProcessorArgs'):
            if key in obj.PropertiesList:
                item[key]=getattr(obj,key)
        values[obj.Name]=item
    return values


def transaction_recovery():
    doc=App.newDocument('InlayGuiTransactions')
    doc.UndoMode=1
    unrelated=_unrelated_job(doc)
    baseline=_document_snapshot(doc)
    _write('unrelated-before.json',baseline)
    doc,panel=gui.prepare_positive('flat',document=doc)
    _confirm_apply(panel)
    gui.check(panel.state=='Applied','First paired plan is created through normal GUI Apply')
    first_jobs=dict(panel.applied)
    applied=_document_snapshot(doc)
    gui.check({name:applied[name] for name in baseline}==baseline,'First Apply preserves every unrelated CAM object and stored path')
    first_native=gui.native_snapshot(doc,first_jobs)
    _write('first-pair-native.json',first_native)
    gui.close_task()
    Gui.runCommand('Std_Undo')
    Gui.updateGui()
    gui.wait(lambda:set(o.Name for o in doc.Objects)==set(baseline),'One native Undo removes the entire pair',timeout=10)
    gui.check(_document_snapshot(doc)==baseline,'One real native Undo restores exactly the unrelated CAM document')
    Gui.runCommand('Std_Redo')
    Gui.updateGui()
    gui.wait(lambda:set(o.Name for o in doc.Objects)==set(applied),'One native Redo restores the entire pair',timeout=10)
    gui.check(gui.native_snapshot(doc,first_jobs)==first_native,'One real native Redo restores both Jobs and exact stored commands')
    prior=_document_snapshot(doc)
    gui.check({name:prior[name] for name in baseline}==baseline,'Undo/Redo leaves the earlier unrelated Job unchanged')
    _write('earlier-work-before-failures.json',prior)
    doc,panel=gui.prepare_positive('flat',document=doc)
    from inlay_planner import native
    real_apply=native.apply_pair
    failures=[]
    for point in ('board','import'):
        def fail(*args,**kwargs):
            return real_apply(*args,fail_after_face=point,**kwargs)
        native.apply_pair=fail
        try:
            _confirm_apply(panel)
        finally:
            native.apply_pair=real_apply
        gui.check('Injected paired Apply failure' in panel.status.text(),
                  'Real Apply reports its injected '+point+' failure')
        gui.check(panel.state=='Needs recalculation' and not gui.widget('applyPair').isEnabled(),
                  'Failed Apply requires an explicit fresh calculation')
        after=_document_snapshot(doc)
        gui.check(after==prior,'Failure after '+point+' preserves the earlier pair and every unrelated native object')
        gui.check(not any(name.startswith('InlayStaging') for name in App.listDocuments()),
                  'Failure after '+point+' closes its temporary staging document')
        failures.append({'point':point,'message':panel.status.text(),'unchanged':after==prior})
        _write('failure-'+point+'.json',after)
        _calculate(panel,'ready','retry-after-'+point+'.json')
    _confirm_apply(panel)
    gui.check(panel.state=='Applied','A normal GUI retry succeeds after both injected failure points')
    second_jobs=dict(panel.applied)
    final=_document_snapshot(doc)
    gui.check({name:final[name] for name in prior}==prior,'Successful retry also preserves all earlier work')
    gui.check(all(second_jobs[side]!=first_jobs[side] for side in ('board','plug')),
              'Retry creates two separate new native Jobs without replacing the first pair')
    _write('transaction-evidence.json',{'unrelated_job':unrelated,'first_jobs':first_jobs,
            'second_jobs':second_jobs,'failures':failures,'original_objects':len(baseline),
            'first_pair_document_objects':len(prior),'final_document_objects':len(final)})
    gui.screenshot('transaction-recovery-native-jobs')
    gui.close_task()
    destination=_save(doc,'transaction-recovery.FCStd')
    return {'document':destination,'unrelated_job':unrelated,'first_jobs':first_jobs,
            'second_jobs':second_jobs,'failures':failures,
            'scope':'Native GUI Undo/Redo and real Apply with staged-board and post-import injected failures; all earlier CAM work remains byte-equivalent in stored motion and exact source shapes'}


CASES={'count_boundaries':count_boundaries,'transaction_recovery':transaction_recovery}
