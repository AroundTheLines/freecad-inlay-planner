"""Actual GUI actions; production calculate/apply calls are never bypassed."""
import json
import importlib
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide6 import QtCore,QtGui,QtWidgets,QtTest

OUT=Path(os.environ['INLAY_E2E_OUT'])
ADDON=Path(os.environ['INLAY_E2E_ADDON'])
CHECKS=[]


def check(value,message):
    if not value:
        raise AssertionError(message)
    CHECKS.append(message)
    with (OUT/'events.jsonl').open('a') as stream:
        stream.write(json.dumps({'time':time.time(),'check':message,'ok':True})+'\n')


def wait(predicate,description,timeout=180):
    start=time.monotonic()
    while not predicate():
        if time.monotonic()-start>timeout:
            raise TimeoutError(description)
        QtWidgets.QApplication.processEvents()
        QtTest.QTest.qWait(25)


def widget(name,kind=QtWidgets.QWidget,parent=None):
    found=(parent or Gui.getMainWindow()).findChild(kind,name)
    if found is None:
        raise AssertionError('Missing real widget: '+name)
    return found


def ensure_visible(control):
    parent=control.parentWidget()
    while parent:
        if isinstance(parent,QtWidgets.QScrollArea):
            parent.ensureWidgetVisible(control)
        parent=parent.parentWidget()
    QtWidgets.QApplication.processEvents()


def click(control):
    if isinstance(control,str):
        control=widget(control)
    ensure_visible(control)
    QtTest.QTest.mouseClick(control,QtCore.Qt.LeftButton)
    QtWidgets.QApplication.processEvents()


def text(name,value):
    control=widget(name,QtWidgets.QLineEdit)
    click(control)
    QtTest.QTest.keySequence(control,QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.SelectAll))
    QtTest.QTest.keyClick(control,QtCore.Qt.Key_Backspace)
    QtTest.QTest.keyClicks(control,str(value))
    QtTest.QTest.keyClick(control,QtCore.Qt.Key_Tab)
    check(control.text()==str(value),'Edited '+name+' through keyboard input; got '+repr(control.text()))


def select(name,index):
    control=widget(name,QtWidgets.QComboBox)
    click(control)
    target=control.view() if control.view().isVisible() else control
    QtTest.QTest.keyClick(target,QtCore.Qt.Key_Home)
    for _ in range(index):
        QtTest.QTest.keyClick(target,QtCore.Qt.Key_Down)
    QtTest.QTest.keyClick(target,QtCore.Qt.Key_Return)
    QtWidgets.QApplication.processEvents()
    if control.view().isVisible():
        QtTest.QTest.keyClick(target,QtCore.Qt.Key_Escape)
    wait(lambda:not control.view().isVisible(),'Combo selection closes its popup',timeout=3)
    check(control.currentIndex()==index,'Selected '+name+' through keyboard input')


def tab(index):
    tabs=widget('inlayTabs',QtWidgets.QTabWidget)
    bar=tabs.tabBar()
    ensure_visible(bar)
    QtTest.QTest.mouseClick(bar,QtCore.Qt.LeftButton,pos=bar.tabRect(index).center())
    check(tabs.currentIndex()==index,'Opened task tab '+str(index))


def screenshot(name):
    Gui.updateGui()
    QtWidgets.QApplication.processEvents()
    QtTest.QTest.qWait(150)
    window=Gui.getMainWindow()
    check(window.grab().save(str(OUT/(name+'.png'))),'Saved painted task-window screenshot '+name)
    # QWidget.grab cannot reliably compose this build's OpenGL viewport. Keep
    # an independent native scene render alongside the ordinary UI evidence.
    document=Gui.activeDocument()
    if document is not None:
        document.activeView().saveImage(str(OUT/(name+'-viewport.png')),1200,800,'Current')
    screen=window.screen().grabWindow(window.winId())
    if not screen.isNull():
        screen.save(str(OUT/(name+'-screen.png')))


def process_memory():
    """Measure this real FreeCAD process without depending on installed extras."""
    high_water=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    try:
        rss_kib=int(subprocess.check_output(['/bin/ps','-o','rss=','-p',str(os.getpid())],text=True).strip())
        return {'pid':os.getpid(),'rss_bytes':rss_kib*1024,'high_water_rss_bytes':high_water,
                'source':'macOS ps RSS in KiB; getrusage ru_maxrss in bytes'}
    except PermissionError:
        # macOS disallows the entitled ps executable under sandbox-exec.
        # The parent harness still independently samples current process-tree
        # RSS; a high-water sample must never be mislabeled as current RSS.
        return {'pid':os.getpid(),'rss_bytes':None,'high_water_rss_bytes':high_water,
                'source':'getrusage ru_maxrss in bytes; current RSS unavailable inside offline sandbox'}


def modal_action(action,handler,timeout=15):
    """Answer real modal widgets from their running Qt event loop."""
    errors=[]
    handled=[]
    start=time.monotonic()
    timer=QtCore.QTimer()
    timer.setInterval(50)
    last_dialog=[None]
    def poll():
        dialog=QtWidgets.QApplication.activeModalWidget()
        if dialog is None:
            visible=[w for w in QtWidgets.QApplication.topLevelWidgets()
                     if isinstance(w,QtWidgets.QDialog) and w.isVisible() and w.isModal()]
            dialog=visible[-1] if visible else None
        if dialog is None:
            return
        try:
            identity=(dialog.objectName(),dialog.windowTitle())
            if identity!=last_dialog[0]:
                with (OUT/'modals.jsonl').open('a') as stream:
                    stream.write(json.dumps({'time':time.time(),'dialog':identity})+'\n')
                last_dialog[0]=identity
            if time.monotonic()-start>timeout:
                raise TimeoutError('Modal GUI action exceeded its deadline')
            handled.append(handler(dialog))
        except BaseException:
            errors.append(traceback.format_exc())
            (OUT/'modal-error.txt').write_text(errors[-1])
            timer.stop()
            if isinstance(dialog,QtWidgets.QDialog):
                dialog.reject()
    timer.timeout.connect(poll)
    timer.start()
    try:
        action()
    finally:
        timer.stop()
    if errors:
        raise AssertionError(errors[0])
    check(bool(handled),'A real modal dialog was handled through Qt input')
    return handled


def fill_file_dialog(dialog,path):
    check(isinstance(dialog,QtWidgets.QFileDialog),'Native file chooser is visible')
    edit=dialog.findChild(QtWidgets.QLineEdit,'fileNameEdit')
    check(edit is not None,'Native file chooser has a filename field')
    click(edit)
    QtTest.QTest.keySequence(edit,QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.SelectAll))
    QtTest.QTest.keyClicks(edit,str(path))
    buttons=dialog.findChild(QtWidgets.QDialogButtonBox)
    role=QtWidgets.QDialogButtonBox.Save if dialog.acceptMode()==QtWidgets.QFileDialog.AcceptSave else QtWidgets.QDialogButtonBox.Open
    click(buttons.button(role))
    return str(path)


def close_task():
    boxes=Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox)
    buttons=[box.button(QtWidgets.QDialogButtonBox.Close) for box in boxes if box.button(QtWidgets.QDialogButtonBox.Close)]
    check(bool(buttons),'Native task Close button exists')
    click(buttons[-1])
    wait(lambda:not Gui.Control.activeDialog(),'Task closes',timeout=10)


def select_native_job(job):
    trees=Gui.getMainWindow().findChildren(QtWidgets.QTreeWidget)
    for tree in trees:
        items=tree.findItems(job.Label,QtCore.Qt.MatchExactly|QtCore.Qt.MatchRecursive,0)
        if items and tree.isVisible():
            tree.scrollToItem(items[0])
            QtWidgets.QApplication.processEvents()
            QtTest.QTest.mouseClick(tree.viewport(),QtCore.Qt.LeftButton,pos=tree.visualItemRect(items[0]).center())
            wait(lambda:job in Gui.Selection.getSelection(),'Native job selected in document tree',timeout=5)
            check(job in Gui.Selection.getSelection(),'Selected '+job.Label+' through the native document tree')
            return
    raise AssertionError('Native Job is not available in the document tree')


def configure_cutter(panel,shape,diameter,number,use_library=False):
    """Choose or create the actual cutter through the installed task widgets."""
    tab(2)
    if use_library:
        row=next(index for index,tool in enumerate(panel.tools) if tool['shape']==shape and tool.get('supported',True))
        table=widget('toolLibrary',QtWidgets.QTableWidget)
        item=table.item(row,0)
        table.scrollToItem(item)
        QtTest.QTest.mouseClick(table.viewport(),QtCore.Qt.LeftButton,pos=table.visualItemRect(item).center())
        if item.checkState()!=QtCore.Qt.Checked:
            QtTest.QTest.keyClick(table,QtCore.Qt.Key_Space)
        check(item.checkState()==QtCore.Qt.Checked,'Selected an actual native library cutter through its checkbox')
    else:
        def add(dialog):
            check(dialog.objectName()=='addToolDialog','Local cutter dialog is visible')
            text('newToolName','GUI '+shape+' '+str(diameter)+' mm')
            select('newToolShape',{'flat':0,'v':1,'bull':2}[shape])
            text('newToolDiameter',str(diameter)+' mm')
            click(dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok))
            return shape
        previous=len(panel.tools)
        modal_action(lambda:click('addTool'),add)
        check(len(panel.tools)==previous+1 and panel.tools[-1]['id'] in panel.selected_tools,
              'GUI created and selected the requested '+shape+' cutter')
    spin=widget('toolNumber',QtWidgets.QSpinBox)
    click(spin)
    QtTest.QTest.keySequence(spin,QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.SelectAll))
    QtTest.QTest.keyClicks(spin,str(number))
    QtTest.QTest.keyClick(spin,QtCore.Qt.Key_Tab)
    check(spin.value()==number,'Entered physical cutter number through keyboard input')
    values=[('diameter',str(diameter)+' mm'),('cutting_length','5 mm'),('shank_diameter',str(diameter)+' mm'),
            ('max_stepdown','0.5 mm'),('stepover','0.8 mm'),('feed','500 mm/min'),
            ('plunge_feed','200 mm/min'),('travel_feed','1000 mm/min'),('spindle','10000 rpm')]
    if shape=='v':
        values.extend([('included_angle_deg','90 deg'),('tip_diameter','0.1 mm')])
    if shape=='bull':
        values.append(('corner_radius','0.25 mm'))
    for key,value in values:
        text('tool_'+key,value)


def native_snapshot(doc,jobs):
    return {side:[{'id':op.InlayOperationId,'tool':op.ToolController.ToolNumber,
                   'tool_shape':op.ToolController.Tool.ShapeType,
                   'diameter':op.ToolController.Tool.Diameter.Value,
                   'settings':json.loads(op.InlaySettings),
                   'commands':[{'name':cmd.Name,'parameters':dict(cmd.Parameters)} for cmd in op.Path.Commands]}
                  for op in doc.getObject(jobs[side]).Operations.Group]
            for side in ('board','plug')}


def prepare_positive(family='flat',document=None):
    """Reach an accepted paired plan exclusively through the task-panel UI."""
    families={'flat':([('flat',2)],1),'v':([('v',2)],2),'bull':([('bull',2)],3),
              'flat_v':([('flat',3),('v',2)],2),'multi_flat':([('flat',3),('flat',2)],1)}
    cutters,style=families[family]
    doc=document or App.newDocument('InlayGuiPositive')
    panel=launch_command()
    if family=='flat':
        source=OUT/'asymmetric_key.svg'
        source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="8mm" height="8mm" viewBox="0 0 8 8">'
                          '<path id="key" fill="#333333" d="M0 0 H8 V3 H6 V8 H0 Z"/></svg>')
        expected_area=54
    else:
        source=OUT/'outlined_circle.svg'
        points=[(3+3*math.cos(i*2*math.pi/48),3+3*math.sin(i*2*math.pi/48)) for i in range(48)]
        source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="6mm" height="6mm" viewBox="0 0 6 6">'
                          '<polygon id="medallion" fill="#333333" points="'+
                          ' '.join(format(x,'.14g')+','+format(y,'.14g') for x,y in points)+'"/></svg>')
        expected_area=48*9*math.sin(2*math.pi/48)/2
    for side in ('board','plug'):
        text(side+'_width','20 mm')
        text(side+'_height','20 mm')
        text(side+'_thickness','6 mm')
        text(side+'_clearance','2 mm')
    tab(1)
    modal_action(lambda:click('browseSvg'),lambda dialog:fill_file_dialog(dialog,source))
    wait(lambda:not panel.calculation.busy and panel.imported is not None,'Real file chooser SVG import completes')
    check(len(panel.imported['regions'])==1,'Analytic SVG imports as one filled region')
    polygons=panel.imported['regions'][0]['polygons']
    area=sum(abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(p['outer'],p['outer'][1:]+p['outer'][:1])))/2 for p in polygons)
    check(abs(area-expected_area)<1e-8,'Imported SVG matches its independently calculated area')
    click('center_both')
    screenshot('positive-01-placed-artwork')
    for index,(shape,diameter) in enumerate(cutters):
        configure_cutter(panel,shape,diameter,index+1,use_library=family=='flat')
    tab(3)
    select('inlayStyle',style)
    for key,value in [('fit_value','1 mm'),('glue_gap','0.2 mm'),('side_clearance','0.2 mm'),
                      ('backing_clearance','0.5 mm'),('resolution_xy','0.05 mm'),('resolution_z','0.05 mm')]:
        text(key,value)
    screenshot('positive-02-user-entered-settings')
    click('calculate')
    wait(lambda:not panel.calculation.busy,'Both mating halves finish calculation',timeout=240)
    check(panel.result is not None,'Real GUI calculation produced a paired result')
    (OUT/'initial-plan.json').write_text(json.dumps(panel.result,indent=2))
    check(panel.result['status']=='needs_acceptance','Paired machine-grid modification requires explicit acceptance')
    check(not widget('applyPair').isEnabled(),'Apply remains disabled before paired modification acceptance')
    screenshot('positive-03-proposed-modification')
    click('acceptProposal')
    wait(lambda:not panel.calculation.busy,'Accepted geometry recalculates',timeout=240)
    check(panel.result.get('status')=='ready','Accepted paired geometry becomes ready for review')
    check(panel.result['verification']['status']=='passed','Actual final motions passed production verification')
    used={op['tool_id'] for op in panel.result['operations']}
    check(used==panel.selected_tools,'Every selected cutter contributes to this qualified fixture')
    check(widget('applyPair').isEnabled(),'Review and Apply becomes available for the accepted pair')
    return doc,panel


def positive(family='flat'):
    doc,panel=prepare_positive(family)
    operations=widget('operationList',QtWidgets.QTreeWidget)
    check(operations.topLevelItemCount()==len(panel.result['operations']),'Every actual operation is individually selectable')
    QtTest.QTest.mouseClick(operations.viewport(),QtCore.Qt.LeftButton,pos=operations.visualItemRect(operations.topLevelItem(0)).center())
    click('isolateOperation')
    check(len(panel.hidden_operations)==len(panel.result['operations'])-1,'Isolate changes visible paths without excluding artwork')
    click('showAllOperations')
    check(not panel.hidden_operations,'Show all restores every path')
    select('previewFace',3)
    screenshot('positive-04-assembly-section')
    select('previewFace',2)
    Gui.runCommand('Std_ViewIsometric')
    Gui.runCommand('Std_ViewFitAll')
    screenshot('positive-05-paired-paths')
    originals={obj.Name for obj in doc.Objects}
    def confirm(dialog,accept):
        check(dialog.objectName()=='confirmApplyDialog','Pair confirmation is a real modal review dialog')
        review=widget('applyReviewText',QtWidgets.QPlainTextEdit,dialog).toPlainText()
        check('500' in review and '10000' in review,'Confirmation includes the effective cutting feed and spindle settings')
        screenshot('positive-06-confirmation-'+('accept' if accept else 'cancel'))
        buttons=widget('confirmApplyButtons',QtWidgets.QDialogButtonBox,dialog)
        click(buttons.button(QtWidgets.QDialogButtonBox.Ok if accept else QtWidgets.QDialogButtonBox.Cancel))
        return accept
    modal_action(lambda:click('applyPair'),lambda dialog:confirm(dialog,False))
    check({obj.Name for obj in doc.Objects}==originals,'Cancelling confirmation creates no document objects')
    before_apply=process_memory()
    modal_action(lambda:click('applyPair'),lambda dialog:confirm(dialog,True),timeout=120)
    check(panel.state=='Applied','Confirmed GUI action applied both native CAM Jobs')
    after_apply=process_memory()
    jobs=dict(panel.applied)
    for side in ('board','plug'):
        job=doc.getObject(jobs[side])
        check(job is not None and bool(job.Operations.Group),'Native '+side+' Job contains machining operations')
        check(all(type(op.Proxy).__module__=='Path.Op.Custom' for op in job.Operations.Group),'Output uses editable built-in Custom operations')
    native_counts={side:sum(len(op.Path.Commands) for op in doc.getObject(jobs[side]).Operations.Group)
                   for side in ('board','plug')}
    memory={'before_apply':before_apply,'after_apply':after_apply,
            'rss_delta_bytes':(after_apply['rss_bytes']-before_apply['rss_bytes']
                               if None not in (after_apply['rss_bytes'],before_apply['rss_bytes']) else None),
            'verified_move_count':sum(len(op['moves']) for op in panel.result['operations']),
            'native_command_count':native_counts,'native_total_commands':sum(native_counts.values()),
            'native_document_objects':len(doc.Objects)}
    (OUT/'apply-memory.json').write_text(json.dumps(memory,indent=2))
    (OUT/'spec.json').write_text(json.dumps(panel.calculated_spec,indent=2))
    (OUT/'plan.json').write_text(json.dumps(panel.result,indent=2))
    (OUT/'jobs.json').write_text(json.dumps(jobs,indent=2))
    (OUT/'native-snapshot.json').write_text(json.dumps(native_snapshot(doc,jobs),indent=2))
    close_task()
    destination=OUT/'paired-inlay.FCStd'
    modal_action(lambda:Gui.runCommand('Std_SaveAs'),lambda dialog:fill_file_dialog(dialog,destination))
    check(destination.exists() and Path(doc.FileName)==destination,'Native Save As persisted the paired FreeCAD document')
    for side in ('board','plug'):
        job=doc.getObject(jobs[side])
        select_native_job(job)
        output=OUT/(side+'.ngc')
        def post_dialog(dialog):
            if isinstance(dialog,QtWidgets.QFileDialog):
                return fill_file_dialog(dialog,output)
            editor=dialog.findChild(QtWidgets.QTextEdit)
            check(editor is not None and 'G61' in editor.toPlainText(),'Native CAM opens the qualified LinuxCNC G-code preview')
            buttons=dialog.findChild(QtWidgets.QDialogButtonBox)
            click(buttons.button(QtWidgets.QDialogButtonBox.Ok))
            return 'native_gcode_preview'
        modal_action(lambda:Gui.runCommand('CAM_Post'),post_dialog,timeout=120)
        check(output.exists() and output.stat().st_size>100,'Native CAM exported '+side+' G-code through its real dialogs')
    screenshot('positive-07-native-cam-output')
    Gui.runCommand('Std_Save')
    check(Path(doc.FileName).exists(),'Native Save retained the postprocessing properties')
    return {'family':family,'document':str(destination),'jobs':jobs,'apply_memory':memory,
            'outputs':{side:str(OUT/(side+'.ngc')) for side in ('board','plug')}}


def launch_command():
    Gui.activateWorkbench('CAMWorkbench')
    check('Inlay_Create' in Gui.listCommands(),'Installed package registered its command')
    def buttons():
        return [b for b in Gui.getMainWindow().findChildren(QtWidgets.QToolButton)
                if b.defaultAction() and b.defaultAction().objectName()=='Inlay_Create'
                and b.isVisible() and b.isEnabled() and b.defaultAction().isEnabled()]
    Gui.updateGui()
    try:
        wait(lambda:bool(buttons()),'Visible enabled Create Inlay toolbar action',timeout=10)
    except TimeoutError:
        (OUT/'actions.json').write_text(json.dumps([(a.objectName(),a.text()) for a in Gui.getMainWindow().findChildren(QtGui.QAction)],indent=2))
        raise AssertionError('Create Inlay has no visible enabled CAM toolbar entry')
    click(buttons()[0])
    wait(lambda: Gui.getMainWindow().findChild(QtWidgets.QWidget,'InlayTaskPanel') is not None,'Task panel opened',timeout=10)
    import inlay_planner.panel as panel
    check(Path(panel.__file__).resolve().is_relative_to(ADDON.resolve()),'Production imports came from installed archive')
    return panel.ACTIVE_PANEL


def smoke():
    doc=App.newDocument('InlayGuiSmoke')
    panel=launch_command()
    check(panel.document.Name==doc.Name,'Task is attached to the active document')
    screenshot('01-empty-task')
    tab(1)
    source=ADDON/'examples'/'asymmetric_leaf.svg'
    if not source.exists():
        source=next((ADDON/'examples').glob('*.svg'))
    text('svgPath',str(source))
    click('importSvg')
    wait(lambda:not panel.calculation.busy,'SVG import completes')
    check(panel.imported is not None,'Real worker imported SVG')
    check(widget('artworkRegions',QtWidgets.QListWidget).count()>0,'Imported regions are selectable')
    check(panel.view.scene().items(),'Visible preview contains geometry')
    screenshot('02-imported-svg')
    tab(0)
    text('board_width','60 mm')
    text('board_height','40 mm')
    tab(1)
    click('center_both')
    check(abs(panel._value('board_design_x')-30)<1e-10,'Horizontal centre follows slab dimensions')
    check(abs(panel._value('board_design_y')-20)<1e-10,'Vertical centre follows slab dimensions')
    tab(3)
    text('resolution_xy','0.05 mm')
    text('resolution_z','0.025 mm')
    screenshot('03-fit-settings')
    # Normal task Close action; no direct panel.reject bypass.
    dialog=Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox)
    close_buttons=[b.button(QtWidgets.QDialogButtonBox.Close) for b in dialog if b.button(QtWidgets.QDialogButtonBox.Close)]
    check(bool(close_buttons),'Native task Close button exists')
    click(close_buttons[-1])
    wait(lambda:not Gui.Control.activeDialog(),'Task closes',timeout=10)
    check(not doc.Objects,'Cancelling draft created no machining objects')
    App.closeDocument(doc.Name)


def execute():
    result={'run_id':os.environ['INLAY_E2E_RUN_ID'],'case':os.environ['INLAY_E2E_CASE'],'ok':False,'checks':CHECKS}
    try:
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_DontUseNativeDialogs,True)
        notifications=App.ParamGet('User parameter:BaseApp/Preferences/NotificationArea')
        notifications.SetInt('NotificationTime',1)
        notifications.SetInt('MinimumOnScreenTime',0)
        Gui.getMainWindow().resize(1400,950)
        functions={'smoke':smoke,'positive':positive,'positive_flat':positive,
                   'positive_v':lambda:positive('v'),'positive_bull':lambda:positive('bull'),
                   'positive_flat_v':lambda:positive('flat_v'),'positive_multi_flat':lambda:positive('multi_flat')}
        for module_name in ('review_journeys','stock_journeys','transaction_journeys','recovery_journeys','edge_journeys','resource_journeys'):
            if (Path(__file__).parent/(module_name+'.py')).exists():
                module=importlib.import_module(module_name)
                extra=getattr(module,'CASES',None)
                if extra is None and module_name=='review_journeys':
                    extra={name:getattr(module,name) for name in ('review_svg','review_placement','review_lifecycle')}
                if not isinstance(extra,dict) or set(extra)&set(functions):
                    raise ValueError('Invalid or duplicate GUI case registry in '+module_name)
                functions.update(extra)
        details=functions[result['case']]()
        if details:
            result.update(details)
        result.update(ok=True,version=App.Version(),python=sys.version,qt=QtCore.qVersion())
    except BaseException:
        result['error']=traceback.format_exc()
        try:
            screenshot('failure')
        except BaseException:
            result['screenshot_error']=traceback.format_exc()
    (OUT/'result.json').write_text(json.dumps(result,indent=2))
    if Gui.Control.activeDialog():
        try:
            close_task()
        except BaseException:
            result['cleanup_error']=traceback.format_exc()
            (OUT/'result.json').write_text(json.dumps(result,indent=2))
    for doc in list(App.listDocuments().values()):
        # Test-owned documents only, in a process/profile created for this run.
        App.closeDocument(doc.Name)
    # Close the initial Start view while PySide is still alive. Its tool-button
    # wrappers otherwise outlive the binding registry in this bundled Qt build.
    Gui.runCommand('Std_CloseAllWindows')
    def quit_cleanly():
        import gc
        QtWidgets.QApplication.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(None,QtCore.QEvent.DeferredDelete)
        gc.collect()
        QtCore.QCoreApplication.sendPostedEvents(None,QtCore.QEvent.DeferredDelete)
        QtWidgets.QApplication.instance().quit()
    QtCore.QTimer.singleShot(1750,quit_cleanly)
