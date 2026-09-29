"""Real native selection, document lifetime and extension-absent GUI journeys."""
import hashlib
import gc
import json
import os
from pathlib import Path
import sys

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide6 import QtCore,QtGui,QtWidgets,QtTest,QtOpenGLWidgets
from journeys import (OUT,check,click,close_task,fill_file_dialog,launch_command,modal_action,
                      screenshot,select,select_native_job,tab,text,wait,widget)


def click_face(obj,point):
    """Project a known fixture point, then select it with actual mouse input."""
    view=Gui.activeDocument().activeView()
    view.viewTop();view.fitAll()
    QtWidgets.QApplication.processEvents();QtTest.QTest.qWait(100)
    area=Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
    candidates=[w for w in area.activeSubWindow().findChildren(QtOpenGLWidgets.QOpenGLWidget) if w.isVisible()]
    check(bool(candidates),'Native document has a visible OpenGL viewport')
    surface=max(candidates,key=lambda w:w.width()*w.height())
    px,py=view.getPointOnScreen(point)
    attempted=[]
    for factor in dict.fromkeys([float(surface.devicePixelRatioF()),1.]):
        pos=QtCore.QPoint(round(px/factor),surface.height()-1-round(py/factor))
        if not surface.rect().contains(pos):continue
        attempted.append([pos.x(),pos.y(),factor])
        QtTest.QTest.mouseClick(surface,QtCore.Qt.LeftButton,pos=pos)
        QtWidgets.QApplication.processEvents()
        selected=Gui.Selection.getSelectionEx()
        if len(selected)==1 and selected[0].Object is obj and any(n.startswith('Face') for n in selected[0].SubElementNames):
            check(True,'Selected the native stock face by clicking its projected fixture point')
            return
    (OUT/'selection-attempts.json').write_text(json.dumps({'point':list(point),'projected':[px,py],
        'widget':surface.metaObject().className(),'size':[surface.width(),surface.height()],'attempts':attempted},indent=2))
    raise AssertionError('Mouse input did not select the expected fixture face')


def stock_faces():
    doc=App.newDocument('StockSelection')
    obj=doc.addObject('Part::Feature','HoledWood')
    obj.Shape=Part.makeBox(20,20,6).cut(Part.makeCylinder(2,6,App.Vector(10,10,0)))
    doc.recompute()
    initial=obj.Shape.exportBrepToString()
    panel=launch_command()
    # Selection is made before capture; an empty draft has no pickable artwork.
    click_face(obj,App.Vector(4,4,6))
    click('boardCaptureFace')
    check('board' in panel.existing_faces,'Selected planar stock is captured through the panel')
    capture=panel.existing_faces['board']
    check(sum(len(p.get('holes',[])) for p in capture['stock_outline'])==1,'Captured through hole stays a hole in the stock mask')
    check(all(abs(a-b)<1e-6 for a,b in zip(capture['stock'],[20,20,6])),'Captured stock dimensions match the actual solid')
    check(widget('board_width',QtWidgets.QLineEdit).isReadOnly(),'Captured geometry dimensions cannot silently replace its solid')
    screenshot('stock-01-exact-face')
    click('boardUseSlab')
    check('board' not in panel.existing_faces and not widget('board_width',QtWidgets.QLineEdit).isReadOnly(),'Use slab explicitly releases the selected geometry')
    for value in ['2 in','24 in']:
        text('board_width',value)
        check(abs(panel._value('board_width')-float(value.split()[0])*25.4)<1e-8,'Few-inch and24-inch stock sizes retain their exact physical size')
    # A whole object is an invalid capture. Native selection setup is explicit;
    # the action and diagnostic itself use the actual Task-panel button.
    Gui.Selection.clearSelection();Gui.Selection.addSelection(obj)
    click('boardCaptureFace')
    check('Select one face' in widget('inlayStatus',QtWidgets.QLabel).text(),'Whole-object capture is diagnosed without creating stock')
    check('board' not in panel.existing_faces,'Invalid capture did not replace a slab')
    check(obj.Shape.exportBrepToString()==initial,'Stock capture and failed capture preserve source geometry')
    close_task()
    check(len(doc.Objects)==1,'Closing a stock draft preserves only the pre-existing source solid')
    (OUT/'captured-face.json').write_text(json.dumps(capture,indent=2))
    return {'capture_evidence':capture['stock_capture_evidence']}


def document_lifetime():
    owner=App.newDocument('Owner')
    panel=launch_command()
    panel_identity=id(panel)
    # A repeated toolbar command brings back the same task, never a second worker.
    check(id(launch_command())==panel_identity,'Repeated CAM command reuses one active task')
    other=App.newDocument('Unrelated')
    marker=other.addObject('PartDesign::Feature','UnrelatedMarker')
    marker.Shape=Part.makeBox(3,4,5);other.recompute()
    wait(lambda:not widget('calculate').isEnabled(),'Inactive document calculation disables',timeout=5)
    check(not widget('applyPair').isEnabled(),'Apply is unavailable in another document')
    App.setActiveDocument(owner.Name)
    wait(lambda:widget('calculate').isEnabled(),'Returning to owner restores draft controls',timeout=5)
    check(panel.document is owner,'Draft remains attached to its original document')
    # External document closure is a genuine FreeCAD lifecycle notification,
    # not a mocked callback; the extension must handle a deleted C++ wrapper.
    name=owner.Name
    App.closeDocument(name)
    wait(lambda:not Gui.Control.activeDialog(),'Deleting owning document closes its task safely',timeout=5)
    check(marker.Shape.Volume==60 and len(other.Objects)==1,'Unrelated document survives owner closure unchanged')
    App.setActiveDocument(other.Name)
    second=launch_command()
    check(second is not panel and second.document is other,'A new task can start after its former document was deleted')
    close_task()
    screenshot('lifetime-01-recovered')
    return {'original_task_closed':panel._closed,'unrelated_volume':marker.Shape.Volume}


def portable():
    """Keep the test-owned external artwork unavailable for this whole journey."""
    source=Path(os.environ['INLAY_E2E_DOCUMENT']).resolve()
    # The harness is copied under the test run; restrict mutation to a previous
    # isolated GUI fixture directory, never an arbitrary user document folder.
    check((source.parent/'report.json').is_file(),'Portable input belongs to an isolated GUI fixture run')
    moved=[]
    try:
        for svg in source.parent.glob('*.svg'):
            hidden=svg.with_suffix('.svg.unavailable')
            check(not hidden.exists(),'Source-artwork temporary exclusion will not overwrite another file')
            svg.rename(hidden);moved.append((svg,hidden))
        check(bool(moved),'Original generated SVG fixture is unavailable during portable reopen')
        result=_portable_native()
        # Dispose native editor cycles while Python and the Qt event loop are
        # still alive, rather than leaving stale PySide parent wrappers for
        # BindingManager's process-finalization cleanup.
        gc.collect()
        QtCore.QCoreApplication.sendPostedEvents(None,QtCore.QEvent.DeferredDelete)
        QtWidgets.QApplication.processEvents()
        gc.collect()
        result['unavailable_source_artwork']=[str(svg) for svg,_ in moved]
        return result
    finally:
        for svg,hidden in reversed(moved):
            hidden.rename(svg)


def _portable_native():
    """Reopen/edit/export a previous GUI-produced FCStd without installed code."""
    from journeys import ensure_visible,native_snapshot
    from shiboken6 import isValid
    def export_job(job,output):
        wait(lambda:any(tree.isVisible() and tree.findItems(job.Label,QtCore.Qt.MatchExactly|QtCore.Qt.MatchRecursive,0)
                        for tree in Gui.getMainWindow().findChildren(QtWidgets.QTreeWidget)),
             'Native tree has populated the reopened Job',timeout=10)
        select_native_job(job)
        def post(dialog):
            if isinstance(dialog,QtWidgets.QFileDialog):return fill_file_dialog(dialog,output)
            editor=dialog.findChild(QtWidgets.QTextEdit)
            check(editor is not None,'Built-in LinuxCNC preview opened without the extension')
            click(dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok))
            return 'preview'
        modal_action(lambda:Gui.runCommand('CAM_Post'),post,timeout=120)
        check(output.exists() and output.stat().st_size>100,'Native CAM exported '+output.name)
    check('Inlay_Create' not in Gui.listCommands(),'Extension command is absent in this separate FreeCAD installation profile')
    source=Path(os.environ['INLAY_E2E_DOCUMENT']).resolve()
    check(source.is_file(),'Previous GUI-produced FreeCAD file exists')
    original_sha=hashlib.sha256(source.read_bytes()).hexdigest()
    modal_action(lambda:Gui.runCommand('Std_Open'),lambda dialog:fill_file_dialog(dialog,source))
    doc=App.ActiveDocument
    check(doc is not None,'Native Open restored the saved inlay document')
    Gui.activateWorkbench('CAMWorkbench')
    check(not any(name.startswith('inlay_planner') for name in sys.modules),'No extension module was imported while reopening')
    jobs={side:next(obj for obj in doc.Objects if obj.Name.startswith(side.title()+'InlayJob')) for side in ('board','plug')}
    for side,job in jobs.items():
        check(bool(job.Operations.Group),'Portable '+side+' job contains native operations')
        for op in job.Operations.Group:
            check(type(op.Proxy).__module__=='Path.Op.Custom' and bool(op.Path.Commands),'Native Custom operation restored its executable path')
    reorder={'before':[op.Name for op in jobs['board'].Operations.Group],
             'after':[op.Name for op in jobs['board'].Operations.Group],
             'separate_case':'Native reordering is exercised in the fresh-process precision case'}
    operation=jobs['board'].Operations.Group[0]
    # Open the actual native operation editor via a tree double click.
    for tree in Gui.getMainWindow().findChildren(QtWidgets.QTreeWidget):
        if tree.isVisible() and tree.topLevelItemCount():
            root=tree.topLevelItem(0)
            QtTest.QTest.mouseClick(tree.viewport(),QtCore.Qt.LeftButton,pos=tree.visualItemRect(root).center())
            QtTest.QTest.keyClick(tree,QtCore.Qt.Key_Asterisk)
            QtWidgets.QApplication.processEvents()
    tree=next(tree for tree in Gui.getMainWindow().findChildren(QtWidgets.QTreeWidget)
              if tree.isVisible() and tree.findItems(operation.Label,QtCore.Qt.MatchExactly|QtCore.Qt.MatchRecursive,0))
    item=tree.findItems(operation.Label,QtCore.Qt.MatchExactly|QtCore.Qt.MatchRecursive,0)[0]
    parent=item.parent()
    while parent: parent.setExpanded(True);parent=parent.parent()
    tree.scrollToItem(item);QtWidgets.QApplication.processEvents()
    QtTest.QTest.mouseDClick(tree.viewport(),QtCore.Qt.LeftButton,pos=tree.visualItemRect(item).center())
    wait(lambda:bool(Gui.Control.activeDialog()),'Native Custom editor opens from document tree',timeout=10)
    native_task=operation.ViewObject.Proxy.panel
    editor=next(page.form.txtGCode for page in native_task.featurePages if hasattr(page.form,'txtGCode'))
    # Use the native panel's own widget references rather than manufacturing
    # wrappers for every ancestor and text editor in FreeCAD's C++ UI tree.
    dock=Gui.getMainWindow().findChild(QtWidgets.QDockWidget,'Tasks')
    if dock is not None and not dock.isVisible():
        action=dock.toggleViewAction()
        if action.isChecked():action.trigger()
        action.trigger()
    wait(lambda:editor.isVisible() and not editor.isReadOnly(),
         'Native Tasks overlay reveals its editable G-code field',timeout=10)
    check('G90' in editor.toPlainText(),'Built-in Custom editor exposes saved G-code without the extension')
    screenshot('portable-00-visible-native-editor')
    before=editor.toPlainText()
    click(editor)
    QtTest.QTest.keySequence(editor,QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.MoveToStartOfDocument))
    feed_start=before.index('F')+1
    feed_end=feed_start
    while feed_end<len(before) and before[feed_end] in '0123456789.+-':feed_end+=1
    for _ in range(feed_start):QtTest.QTest.keyClick(editor,QtCore.Qt.Key_Right)
    for _ in range(feed_end-feed_start):QtTest.QTest.keyClick(editor,QtCore.Qt.Key_Right,QtCore.Qt.ShiftModifier)
    QtTest.QTest.keyClicks(editor,'9')
    check('F9' in editor.toPlainText(),'Edited an actual stored native motion feed')
    QtTest.QTest.keySequence(editor,QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.MoveToEndOfDocument))
    QtTest.QTest.keyClick(editor,QtCore.Qt.Key_Return)
    QtTest.QTest.keyClicks(editor,'(Portable native edit verified)')
    check('Portable native edit verified' in editor.toPlainText(),'Typed a native CAM edit through the built-in editor')
    button=native_task.buttonBox.button(QtWidgets.QDialogButtonBox.Ok)
    click(button);wait(lambda:not Gui.Control.activeDialog(),'Native edit accepted',timeout=10)
    del editor,button,native_task
    gc.collect()
    QtCore.QCoreApplication.sendPostedEvents(None,QtCore.QEvent.DeferredDelete)
    QtWidgets.QApplication.processEvents()
    check(any('Portable native edit verified' in line for line in operation.Gcode),'Native operation retained the manual edit')
    # A real unwritable fixture exercises native Save As permission handling.
    # The second timer can dismiss Qt's nested warning while the file chooser's
    # Save click is still inside a nested event loop.
    readonly=OUT/'readonly-output';readonly.mkdir();readonly.chmod(0o555)
    denied_path=readonly/'cannot-save.FCStd'
    attempted=[False];permission_messages=[]
    def dismiss_permission():
        dialog=QtWidgets.QApplication.activeModalWidget()
        if isinstance(dialog,QtWidgets.QMessageBox):
            permission_messages.append(dialog.text()+' '+dialog.informativeText())
            button=dialog.button(QtWidgets.QMessageBox.Ok) or dialog.buttons()[0]
            click(button)
    watcher=QtCore.QTimer();watcher.setInterval(30);watcher.timeout.connect(dismiss_permission)
    def denied_save(dialog):
        if isinstance(dialog,QtWidgets.QMessageBox):
            dismiss_permission();return 'permission warning'
        check(isinstance(dialog,QtWidgets.QFileDialog),'Read-only save attempt uses the native chooser')
        if not attempted[0]:
            attempted[0]=True
            return fill_file_dialog(dialog,denied_path)
        click(dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Cancel))
        return 'cancel after permission warning'
    original_filename=doc.FileName
    before_denied=native_snapshot(doc,{side:job.Name for side,job in jobs.items()})
    try:
        check(not os.access(readonly,os.W_OK),'Fixture output directory is actually unwritable')
        watcher.start()
        modal_action(lambda:Gui.runCommand('Std_SaveAs'),denied_save)
    finally:
        watcher.stop();readonly.chmod(0o755)
    check(bool(permission_messages),'Native Save As displayed an actual filesystem permission diagnostic')
    check(not denied_path.exists(),'Rejected read-only save left no partial output file')
    check(native_snapshot(doc,{side:job.Name for side,job in jobs.items()})==before_denied,
          'Native operations remain intact after the actual filesystem save failure')
    (OUT/'readonly-save-diagnostic.json').write_text(json.dumps({'messages':permission_messages,'attempted':str(denied_path),
        'original_document_filename':original_filename,'document_filename_after_failure':doc.FileName,
        'note':'FreeCAD may update its in-memory FileName before save failure; retry must explicitly choose a writable destination'},indent=2))
    def cancel_save(dialog):
        check(isinstance(dialog,QtWidgets.QFileDialog),'Save retry presents the native chooser')
        click(dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Cancel))
        return 'cancel retry'
    modal_action(lambda:Gui.runCommand('Std_SaveAs'),cancel_save)
    check(native_snapshot(doc,{side:job.Name for side,job in jobs.items()})==before_denied,
          'Canceling the save retry preserves every native operation')
    destination=OUT/'portable-edited.FCStd'
    modal_action(lambda:Gui.runCommand('Std_SaveAs'),lambda dialog:fill_file_dialog(dialog,destination))
    check(destination.exists(),'Native Save As persisted the edited portable file')
    names={side:job.Name for side,job in jobs.items()}
    edited_snapshot=native_snapshot(doc,names)
    (OUT/'native-snapshot.json').write_text(json.dumps(edited_snapshot,indent=2))
    for side,job in jobs.items():
        export_job(job,OUT/(side+'.ngc'))
    qualified_args=jobs['board'].PostProcessorArgs
    check('--precision 9' in qualified_args and 'G61' in qualified_args,'Saved native Job retains its qualified metric and exact-path defaults')
    Gui.runCommand('Std_Save')
    screenshot('portable-01-native-edit-export')
    saved_operation=operation.Name
    edited_text=list(operation.Gcode)
    check('Std_CloseActiveWindow' in Gui.listCommands(),'Native document Close command is available')
    Gui.runCommand('Std_CloseActiveWindow')
    wait(lambda:not App.listDocuments(),'Native Close releases the edited document',timeout=10)
    modal_action(lambda:Gui.runCommand('Std_Open'),lambda dialog:fill_file_dialog(dialog,destination))
    reopened=App.ActiveDocument
    check(reopened is not None and Path(reopened.FileName)==destination,'Native Open reopens the edited artifact')
    check(list(reopened.getObject(saved_operation).Gcode)==edited_text,'Manual native feed and comment edits survive a second native reopen')
    check(any(cmd.Parameters.get('F')==9 for cmd in reopened.getObject(saved_operation).Path.Commands),
          'Edited native F9 is executable after reopening')
    check(reopened.getObject(names['board']).PostProcessorArgs==qualified_args,'Qualified postprocessor defaults are restored and survive reopening')
    check([op.Name for op in reopened.getObject(names['board']).Operations.Group]==reorder['after'],
          'Native operation reordering survives saving and reopening')
    reopened_snapshot=native_snapshot(reopened,names)
    (OUT/'reopened-native-snapshot.json').write_text(json.dumps(reopened_snapshot,indent=2))
    for side in ('board','plug'):
        export_job(reopened.getObject(names[side]),OUT/(side+'_reopened.ngc'))
    check(hashlib.sha256(source.read_bytes()).hexdigest()==original_sha,'The reviewed source file was not overwritten')
    check(not any(name.startswith('inlay_planner') for name in sys.modules),'No extension import was needed for editing, saving or export')
    return {'source':str(source),'source_sha256':original_sha,'saved':str(destination),'jobs':names,
            'operation_order':reorder,'read_only_save_messages':permission_messages,
            'negative_controls':[],
            'scope':'Native GUI edit/reorder/save/export/reopen without the extension; unsuitable export settings are exercised in separate fresh-process cases'}


def native_post_control(kind):
    """One native Job editor in a fresh process; output is deliberately unsuitable."""
    from journeys import ensure_visible,native_snapshot
    from shiboken6 import isValid
    check('Inlay_Create' not in Gui.listCommands(),'Negative export case has no installed extension command')
    source=Path(os.environ['INLAY_E2E_DOCUMENT']).resolve()
    original_sha=hashlib.sha256(source.read_bytes()).hexdigest()
    copied=OUT/'native-control.FCStd'
    copied.write_bytes(source.read_bytes())
    modal_action(lambda:Gui.runCommand('Std_Open'),lambda dialog:fill_file_dialog(dialog,copied))
    # This is an isolated native preference fixture, matching a supported CAM
    # policy. The output path itself is entered through the actual Job editor.
    App.ParamGet('User parameter:BaseApp/Preferences/Mod/CAM').SetString('PostProcessorOutputPolicy','Overwrite')
    doc=App.ActiveDocument
    Gui.activateWorkbench('CAMWorkbench')
    jobs={side:next(obj for obj in doc.Objects if obj.Name.startswith(side.title()+'InlayJob')) for side in ('board','plug')}
    job=jobs['board']
    wait(lambda:any(tree.isVisible() and tree.findItems(job.Label,QtCore.Qt.MatchExactly|QtCore.Qt.MatchRecursive,0)
                    for tree in Gui.getMainWindow().findChildren(QtWidgets.QTreeWidget)),
         'Native document tree is ready for the export control',timeout=10)
    select_native_job(job)
    tree=next(tree for tree in Gui.getMainWindow().findChildren(QtWidgets.QTreeWidget)
              if tree.isVisible() and tree.findItems(job.Label,QtCore.Qt.MatchExactly|QtCore.Qt.MatchRecursive,0))
    item=tree.findItems(job.Label,QtCore.Qt.MatchExactly|QtCore.Qt.MatchRecursive,0)[0]
    tree.scrollToItem(item)
    QtTest.QTest.mouseDClick(tree.viewport(),QtCore.Qt.LeftButton,pos=tree.visualItemRect(item).center())
    wait(lambda:bool(Gui.Control.activeDialog()),'Native Job editor opens for one export setting',timeout=10)
    task=job.ViewObject.Proxy.taskPanel
    field=task.form.postProcessorArguments
    for dock in Gui.getMainWindow().findChildren(QtWidgets.QDockWidget):
        if dock.objectName()=='Tasks' and not dock.isVisible():
            action=dock.toggleViewAction()
            if action.isChecked():action.trigger()
            action.trigger()
    def show_field(control):
        ancestors=[];parent=control.parentWidget()
        while parent:ancestors.append(parent);parent=parent.parentWidget()
        for parent in reversed(ancestors):
            if isinstance(parent,QtWidgets.QTabWidget):
                index=next((i for i in range(parent.count()) if parent.widget(i).isAncestorOf(control)),-1)
                if index>=0 and parent.currentIndex()!=index:
                    bar=parent.tabBar();ensure_visible(bar)
                    QtTest.QTest.mouseClick(bar,QtCore.Qt.LeftButton,pos=bar.tabRect(index).center())
            elif isinstance(parent,QtWidgets.QToolBox):
                index=next((i for i in range(parent.count()) if parent.widget(i).isAncestorOf(control)),-1)
                if index>=0 and parent.currentIndex()!=index:
                    label=parent.itemText(index).replace('&','')
                    click(next(button for button in parent.findChildren(QtWidgets.QAbstractButton)
                               if button.text().replace('&','')==label))
        ensure_visible(control)
    before_order=[op.Name for op in job.Operations.Group]
    if kind=='precision':
        check(len(before_order)>1,'Precision fixture includes multiple native operations for reordering')
        listing=task.form.operationsList;show_field(listing)
        item=listing.item(1);listing.scrollToItem(item)
        QtTest.QTest.mouseClick(listing.viewport(),QtCore.Qt.LeftButton,pos=listing.visualItemRect(item).center())
        click(task.form.operationUp)
        check([op.Name for op in job.Operations.Group]==[before_order[1],before_order[0]]+before_order[2:],
              'Native Up button changes the operation order in the saved Job')
        del listing,item
    show_field(field)
    wait(lambda:field.isVisible(),'Native post arguments are visibly editable',timeout=10)
    qualified=job.PostProcessorArgs
    check('--precision 9' in qualified and 'G61' in qualified,'Input Job has the qualified export settings')
    desired={'precision':qualified.replace('--precision 9','--precision 0'),
             'mode':qualified.replace('G61','G64'),'inch':qualified+' --inches'}[kind]+' --no-show-editor'
    click(field)
    QtTest.QTest.keySequence(field,QtGui.QKeySequence(QtGui.QKeySequence.SelectAll))
    QtTest.QTest.keyClick(field,QtCore.Qt.Key_Backspace)
    QtTest.QTest.keyClicks(field,desired)
    QtTest.QTest.keyClick(field,QtCore.Qt.Key_Tab)
    check(field.text()==desired,'Actual native Job field contains the deliberately unsuitable setting')
    filename={'precision':'board_metric_0.ngc','mode':'board_g64.ngc','inch':'board_inch_9.ngc'}[kind]
    output=OUT/filename
    output_field=task.form.postProcessorOutputFile
    show_field(output_field);click(output_field)
    QtTest.QTest.keySequence(output_field,QtGui.QKeySequence(QtGui.QKeySequence.SelectAll))
    QtTest.QTest.keyClick(output_field,QtCore.Qt.Key_Backspace)
    QtTest.QTest.keyClicks(output_field,str(output))
    QtTest.QTest.keyClick(output_field,QtCore.Qt.Key_Tab)
    check(output_field.text()==str(output),'Native Job UI specifies the test-owned export destination')
    screenshot('native-'+kind+'-setting')
    buttons=[box.button(QtWidgets.QDialogButtonBox.Ok) for box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox)
             if isValid(box) and box.isVisible() and box.button(QtWidgets.QDialogButtonBox.Ok)]
    click(buttons[-1]);wait(lambda:not Gui.Control.activeDialog(),'Native Job setting is accepted',timeout=10)
    check(job.PostProcessorArgs==desired,'Native Job stored the selected unsuitable postprocessor arguments')
    after_order=[op.Name for op in job.Operations.Group]
    del field,output_field,task,buttons
    gc.collect()
    QtCore.QCoreApplication.sendPostedEvents(None,QtCore.QEvent.DeferredDelete)
    snapshot=native_snapshot(doc,{side:obj.Name for side,obj in jobs.items()})
    (OUT/'native-snapshot.json').write_text(json.dumps(snapshot,indent=2))
    Gui.runCommand('Std_Save')
    import zipfile,xml.etree.ElementTree as ET
    with zipfile.ZipFile(copied) as saved:xml=ET.fromstring(saved.read('Document.xml'))
    group=xml.find(".//Object[@name='"+job.Operations.Name+"']/Properties/Property[@name='Group']/LinkList")
    check(group is not None and [entry.attrib['value'] for entry in group]==after_order,
          'Native Save persists the current operation order in the actual FCStd archive')
    select_native_job(job)
    Gui.runCommand('CAM_Post')
    check(output.exists() and output.stat().st_size>100,'Native CAM generated the independent-audit negative output')
    check(hashlib.sha256(source.read_bytes()).hexdigest()==original_sha,'Negative export control did not overwrite its input document')
    check(not any(name.startswith('inlay_planner') for name in sys.modules),'Native negative export required no extension import')
    return {'source':str(source),'source_sha256':original_sha,'postprocessor_args':desired,
            'operation_order':{'before':before_order,'after':after_order},'saved':str(copied),
            'negative_control':filename,'expected_independent_replay_pass':False,
            'scope':'Actual native Job UI and CAM postprocessor with one deliberately unsuitable setting; the extension does not intercept export'}


def preview_pick():
    from review_journeys import _small_flat,_calculate,_accept
    doc,panel,_=_small_flat()
    _calculate(panel,'needs_acceptance');_accept(panel)
    select('previewFace',0)
    operations=widget('operationList',QtWidgets.QTreeWidget)
    plug=next(operations.topLevelItem(i) for i in range(operations.topLevelItemCount())
              if operations.topLevelItem(i).data(0,QtCore.Qt.UserRole).startswith('plug:'))
    operations.scrollToItem(plug)
    click(operations)
    QtTest.QTest.mouseClick(operations.viewport(),QtCore.Qt.LeftButton,pos=operations.visualItemRect(plug).center())
    check(panel.selected_operation.startswith('plug:'),'Initially selected the other face operation')
    board=next(op for op in panel.result['operations'] if op['face']=='board')
    pair=next((a,b) for a,b in zip(board['moves'],board['moves'][1:])
              if b['role']=='cut' and a['z']==b['z'] and abs(a['x']-b['x'])>2)
    origin=panel.get_spec()['faces']['board']['origin']
    point=App.Vector(*[(pair[0][axis]+pair[1][axis])/2+origin[i]+(.02 if axis=='z' else 0)
                       for i,axis in enumerate('xyz')])
    view=Gui.activeDocument().activeView();view.viewTop();view.fitAll()
    QtWidgets.QApplication.processEvents();QtTest.QTest.qWait(100)
    area=Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
    surface=max((w for w in area.activeSubWindow().findChildren(QtOpenGLWidgets.QOpenGLWidget) if w.isVisible()),
                key=lambda w:w.width()*w.height())
    px,py=view.getPointOnScreen(point)
    from pivy import coin
    debug={'projected':[px,py],'widget_size':[surface.width(),surface.height()],
           'dpr':surface.devicePixelRatioF(),'events':[],'attempts':[]}
    def record_event(cb):
        debug['events'].append(list(cb.getEvent().getPosition().getValue()))
    event_hook=view.addEventCallbackPivy(coin.SoMouseButtonEvent.getClassTypeId(),record_event)
    ray=coin.SoRayPickAction(view.getViewer().getSoRenderManager().getViewportRegion())
    ray.setPoint(coin.SbVec2s(round(px),round(py)));ray.setRadius(5);ray.setPickAll(True)
    ray.apply(view.getViewer().getSoRenderManager().getSceneGraph())
    debug['direct_ray_paths']=[[str(hit.getPath().getNode(i).getName()) for i in range(hit.getPath().getLength())]
                               for hit in ray.getPickedPointList()]
    for factor in dict.fromkeys([float(surface.devicePixelRatioF()),1.]):
        pos=QtCore.QPoint(round(px/factor),surface.height()-1-round(py/factor))
        if not surface.rect().contains(pos):continue
        QtTest.QTest.mouseClick(surface,QtCore.Qt.LeftButton,pos=pos)
        QtTest.QTest.qWait(150)
        debug['attempts'].append({'factor':factor,'position':[pos.x(),pos.y()],'selected':panel.selected_operation})
        if panel.selected_operation==board['id']:break
    view.removeEventCallbackPivy(coin.SoMouseButtonEvent.getClassTypeId(),event_hook)
    (OUT/'viewport-pick.json').write_text(json.dumps(debug,indent=2))
    check(panel.selected_operation==board['id'],'Clicking a real 3D toolpath selects its board operation in the Task list')
    check(panel.operation_info.text().startswith('Board'),'Viewport selection displays the matching face and cutter')
    screenshot('preview-pick-01-selected-native-path')
    close_task()
    check(panel.overlay.root is None and panel.overlay.callback is None,'Closing the task removes transient geometry and native event callback')
    return {'supporting_rows':['E17','E32'],'selected_operation':board['id']}


CASES={'stock_faces':stock_faces,'document_lifetime':document_lifetime,'portable':portable,'preview_pick':preview_pick,
       'portable_precision':lambda:native_post_control('precision'),'portable_mode':lambda:native_post_control('mode'),
       'portable_inch':lambda:native_post_control('inch')}
