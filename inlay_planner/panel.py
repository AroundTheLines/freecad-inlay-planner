"""Native FreeCAD Tasks workflow. Document writes occur only after review."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import uuid

import FreeCAD as App
import FreeCADGui as Gui

from . import native
from .qt_compat import QtCore, QtGui, QtWidgets
from .process import Calculation
from .preview import ArtworkView, NativeOverlay
from .schema import default_spec, seal_spec, fingerprint, transform_polygons, bounds_of
from .units import quantity, formatted

ACTIVE_PANEL = None


class InlayPanel:
    def __init__(self, document=None):
        self.document = document or App.ActiveDocument or App.newDocument('Inlay')
        self.document_name = self.document.Name
        self.revision = 0
        self.result = None
        self.calculated_spec = None
        self.imported = None
        self.source_text = ''
        self.source_path = ''
        self.source_exclusions = []
        self.excluded_ids = set()
        self.tools = []
        self.selected_tools = set()
        self.overrides = {}
        self.accepted_proposal_hash = None
        self.existing_faces = {}
        self.hidden_operations = set()
        self.selected_operation = None
        self.display_unit = 'mm'
        self._updating = True
        self._closed = False
        self._action = None
        self._pending_calculate = False
        self._reimport = False
        self._tool_row = -1
        self.fields = {}
        self.state = 'Draft'
        self.form = QtWidgets.QScrollArea()
        self.form.setWidgetResizable(True)
        self.form.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.form.setObjectName('InlayTaskPanel')
        self.form.setWindowTitle('Create Inlay')
        content=QtWidgets.QWidget()
        self.form.setWidget(content)
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(4,4,4,4)
        title = QtWidgets.QLabel('<b>Board + plug inlay</b>')
        layout.addWidget(title)
        self.context = QtWidgets.QLabel('Document: '+self.document.Label)
        self.context.setWordWrap(True)
        layout.addWidget(self.context)
        units_row = QtWidgets.QHBoxLayout()
        units_row.addWidget(QtWidgets.QLabel('Display units'))
        self.units_combo = QtWidgets.QComboBox()
        self.units_combo.setObjectName('displayUnits')
        self.units_combo.addItems(['Metric (mm)','Imperial (in)'])
        self.units_combo.currentIndexChanged.connect(self._units_changed)
        units_row.addWidget(self.units_combo)
        layout.addLayout(units_row)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName('inlayTabs')
        self.tabs.setMinimumHeight(210)
        self.tabs.setMaximumHeight(330)
        layout.addWidget(self.tabs)
        self._stock_tab()
        self._artwork_tab()
        self._tools_tab()
        self._fit_tab()
        self._review_tab()
        # Keep the spatial preview visible while switching settings tabs.
        layout.addWidget(self.view)
        self.view.setMinimumHeight(180)
        self.view.setMaximumHeight(240)
        layout.addWidget(self.legend)
        self.status = QtWidgets.QLabel('Import filled SVG artwork to begin.')
        self.status.setObjectName('inlayStatus')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setObjectName('calculationProgress')
        self.progress_bar.setRange(0,100)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        buttons = QtWidgets.QHBoxLayout()
        self.calculate_button = self._button('Calculate','calculate',self.calculate)
        self.cancel_button = self._button('Cancel calculation','cancelCalculation',self.cancel_calculation)
        self.apply_button = self._button('Review and apply pair…','applyPair',self.apply)
        self.cancel_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        buttons.addWidget(self.calculate_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)
        layout.addWidget(self.apply_button)
        self.calculation = Calculation(self.form)
        self.calculation.progress.connect(self._progress)
        self.calculation.completed.connect(self._completed)
        self.calculation.failed.connect(self._failed)
        self.calculation.stopped.connect(self._stopped)
        self.overlay = NativeOverlay(self.document,self._picked)
        self.context_timer = QtCore.QTimer(self.form)
        self.context_timer.setInterval(500)
        self.context_timer.timeout.connect(self._check_document)
        self.context_timer.start()
        self.form.destroyed.connect(self._form_destroyed)
        self._updating = False
        self.refresh_tools()
        self._draw()

    def _button(self,label,name,callback):
        button=QtWidgets.QPushButton(label)
        button.setObjectName(name)
        button.clicked.connect(callback)
        return button

    def _tab(self,label):
        outer=QtWidgets.QWidget()
        container=QtWidgets.QVBoxLayout(outer)
        container.setContentsMargins(2,2,2,2)
        scroll=QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        body=QtWidgets.QWidget()
        content=QtWidgets.QVBoxLayout(body)
        scroll.setWidget(body)
        container.addWidget(scroll)
        self.tabs.addTab(outer,label)
        return content

    def _field(self,form,label,name,value,kind='length'):
        edit=QtWidgets.QLineEdit(formatted(value,kind,self.display_unit))
        edit.setObjectName(name)
        edit.setMinimumWidth(100)
        edit.setToolTip('Units may be mixed. Fractions such as 1/8 in are accepted.' if kind=='length' else 'Enter the value for this machining setup.')
        self.fields[name]=(edit,kind)
        edit.textChanged.connect(self._dirty)
        form.addRow(label,edit)
        return edit

    def _value(self,name):
        edit,kind=self.fields[name]
        try:
            value=quantity(edit.text(),kind,self.display_unit)
            edit.setStyleSheet('')
            return value
        except ValueError as exc:
            edit.setStyleSheet('border:1px solid #c95843;')
            raise ValueError(name.replace('_',' ')+': '+str(exc)) from exc

    def _set(self,name,value):
        edit,kind=self.fields[name]
        edit.setText(formatted(value,kind,self.display_unit))

    def _stock_tab(self):
        page=self._tab('Stock & origins')
        self.stock_bounds={}
        self.origin_presets={}
        self.limit_widgets={}
        for face,label in [('board','Board pocket'),('plug','Plug blank')]:
            group=QtWidgets.QGroupBox(label)
            form=QtWidgets.QFormLayout(group)
            row=QtWidgets.QHBoxLayout()
            row.addWidget(self._button('Use selected face',face+'CaptureFace',lambda checked=False,f=face:self.capture_face(f)))
            row.addWidget(self._button('Use slab',face+'UseSlab',lambda checked=False,f=face:self.use_slab(f)))
            form.addRow(row)
            for key,text,value in [('width','Width',152.4),('height','Height',101.6),('thickness','Thickness',19.05)]:
                self._field(form,text,face+'_'+key,value)
            preset=QtWidgets.QComboBox()
            preset.setObjectName(face+'OriginPreset')
            preset.addItems(['Lower left, top','Centre, top','Upper right, top','Custom origin'])
            preset.currentIndexChanged.connect(lambda i,f=face:self._origin_preset(f,i))
            self.origin_presets[face]=preset
            form.addRow('Work origin',preset)
            for axis in ('x','y','z'):
                self._field(form,'Origin '+axis.upper(),face+'_origin_'+axis,0)
            self._field(form,'Clearance above stock',face+'_clearance',5)
            all_bounds=QtWidgets.QCheckBox('Use full stock machining bounds')
            all_bounds.setObjectName(face+'FullBounds')
            all_bounds.setChecked(True)
            all_bounds.toggled.connect(self._dirty)
            self.stock_bounds[face]=all_bounds
            form.addRow(all_bounds)
            self._field(form,'Edge margin',face+'_margin',0)
            for key,text,value in [('xmin','Minimum X',0),('ymin','Minimum Y',0),('xmax','Maximum X',152.4),('ymax','Maximum Y',101.6)]:
                self._field(form,text,face+'_'+key,value)
            explanation=QtWidgets.QLabel('Bounds limit cutting in wood. The plug also needs clearance around its retained details; a remaining rim may obstruct assembly.')
            explanation.setWordWrap(True)
            form.addRow(explanation)
            limits={}
            for key,label_,maximum in [('operations','Operation limit',9999),('tool_changes','Tool-change limit',999)]:
                check=QtWidgets.QCheckBox(label_)
                check.setObjectName(face+'_'+key+'_enabled')
                spin=QtWidgets.QSpinBox()
                spin.setObjectName(face+'_'+key+'_limit')
                spin.setRange(1 if key=='operations' else 0,maximum)
                spin.setValue(4 if key=='operations' else 2)
                check.toggled.connect(spin.setEnabled)
                check.toggled.connect(self._dirty)
                spin.valueChanged.connect(self._dirty)
                spin.setEnabled(False)
                form.addRow(check,spin)
                limits[key]=(check,spin)
            self.limit_widgets[face]=limits
            page.addWidget(group)
        page.addStretch()

    def _artwork_tab(self):
        page=self._tab('Artwork')
        pathrow=QtWidgets.QHBoxLayout()
        self.svg_path=QtWidgets.QLineEdit()
        self.svg_path.setObjectName('svgPath')
        self.svg_path.setPlaceholderText('Select a filled SVG file')
        pathrow.addWidget(self.svg_path)
        pathrow.addWidget(self._button('Browse…','browseSvg',self.browse_svg))
        page.addLayout(pathrow)
        page.addWidget(self._button('Import SVG','importSvg',self.import_svg))
        self.source_info=QtWidgets.QLabel('Filled vectors only; convert text to outlines.')
        self.source_info.setWordWrap(True)
        page.addWidget(self.source_info)
        form=QtWidgets.QFormLayout()
        self._field(form,'Artwork width','art_width',30)
        self._field(form,'Artwork height','art_height',30)
        self.lock_aspect=QtWidgets.QCheckBox('Keep aspect ratio')
        self.lock_aspect.setObjectName('lockAspect')
        self.lock_aspect.setChecked(True)
        self.lock_aspect.toggled.connect(self._dirty)
        form.addRow(self.lock_aspect)
        self.fields['art_width'][0].editingFinished.connect(lambda:self._aspect('width'))
        self.fields['art_height'][0].editingFinished.connect(lambda:self._aspect('height'))
        self._field(form,'Rotation','art_rotation',0,'angle')
        for face in ('board','plug'):
            self._field(form,face.title()+' design centre X',face+'_design_x',76.2)
            self._field(form,face.title()+' design centre Y',face+'_design_y',50.8)
        page.addLayout(form)
        centers=QtWidgets.QHBoxLayout()
        for axis,label in [('x','Center X'),('y','Center Y'),('both','Center both')]:
            centers.addWidget(self._button(label,'center_'+axis,lambda checked=False,a=axis:self.center(a)))
        page.addLayout(centers)
        page.addWidget(self._button('Fit artwork to board bounds','fitToBounds',self.fit_to_bounds))
        self.regions=QtWidgets.QListWidget()
        self.regions.setObjectName('artworkRegions')
        self.regions.setMaximumHeight(150)
        self.regions.itemChanged.connect(self._region_changed)
        self.regions.currentItemChanged.connect(lambda *_:self._draw())
        page.addWidget(self.regions)
        self.view=ArtworkView()
        self.view.picked.connect(self._picked)
        self.view.portion.connect(self._portion)
        self.view.moved.connect(self._move_artwork)
        page.addWidget(self.view)
        self.legend=QtWidgets.QLabel('Green: cuttable · Red: blocked · Purple hatch: excluded · Gold: proposed change')
        self.legend.setWordWrap(True)
        page.addWidget(self.legend)
        viewrow=QtWidgets.QHBoxLayout()
        self.view_mode=QtWidgets.QComboBox()
        self.view_mode.setObjectName('selectionMode')
        self.view_mode.addItems(['Select region/path','Pan','Exclude rectangular portion','Move artwork'])
        self.view_mode.currentIndexChanged.connect(lambda i:self.view.set_mode(['select','pan','portion','move'][i]))
        viewrow.addWidget(self.view_mode)
        viewrow.addWidget(self._button('Fit view','fitPreview',self.fit_preview))
        page.addLayout(viewrow)
        page.addWidget(self._button('Restore all exclusions','restoreExclusions',self.restore_exclusions))
        page.addStretch()

    def _tools_tab(self):
        page=self._tab('Tools')
        buttons=QtWidgets.QHBoxLayout()
        buttons.addWidget(self._button('Refresh library','refreshTools',self.refresh_tools))
        buttons.addWidget(self._button('Add local cutter…','addTool',self.add_tool))
        page.addLayout(buttons)
        self.tool_table=QtWidgets.QTableWidget(0,4)
        self.tool_table.setObjectName('toolLibrary')
        self.tool_table.setHorizontalHeaderLabels(['Use','Cutter','Shape','Diameter'])
        self.tool_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tool_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tool_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tool_table.setMaximumHeight(210)
        self.tool_table.itemChanged.connect(self._tool_checked)
        self.tool_table.currentCellChanged.connect(self._tool_selected)
        self.tool_table.horizontalHeader().setSectionResizeMode(1,QtWidgets.QHeaderView.Stretch)
        self.tool_table.setColumnWidth(0,40)
        page.addWidget(self.tool_table)
        self.tool_description=QtWidgets.QLabel('Choose cutters from your existing library. These edits are local to this inlay.')
        self.tool_description.setWordWrap(True)
        page.addWidget(self.tool_description)
        form=QtWidgets.QFormLayout()
        self.tool_number=QtWidgets.QSpinBox()
        self.tool_number.setObjectName('toolNumber')
        self.tool_number.setRange(1,99999)
        self.tool_number.valueChanged.connect(self._tool_edited)
        form.addRow('Physical tool number',self.tool_number)
        definitions=[('diameter','Diameter',3.175,'length'),('cutting_length','Cutting length',12,'length'),
                     ('corner_radius','Bull corner radius',.5,'length'),('included_angle_deg','V included angle',60,'angle'),
                     ('tip_diameter','V flat tip diameter',0,'length'),('shank_diameter','Shank diameter',3.175,'length'),
                     ('max_stepdown','Maximum depth per pass',1,'length'),('stepover','Pass spacing',1,'length'),
                     ('feed','Cutting feed',0,'feed'),('plunge_feed','Plunge feed',0,'feed'),
                     ('travel_feed','Travel feed',0,'feed'),('spindle','Spindle speed',0,'rpm')]
        self.tool_field_keys=[]
        for key,label,value,kind in definitions:
            edit=self._field(form,label,'tool_'+key,value,kind)
            edit.editingFinished.connect(self._tool_edited)
            self.tool_field_keys.append(key)
        page.addLayout(form)
        note=QtWidgets.QLabel('Enter feeds and spindle speed for your machine and wood. Travel uses feed-controlled moves; no cutting recipe is inferred. Bull nose means rounded corners with a flat centre, not a ball nose.')
        note.setWordWrap(True)
        page.addWidget(note)
        page.addStretch()

    def _fit_tab(self):
        page=self._tab('Depth & fit')
        form=QtWidgets.QFormLayout()
        self.style=QtWidgets.QComboBox()
        self.style.setObjectName('inlayStyle')
        self.style.addItems(['Automatic from selected cutters','Flat walls','V-carved','Bull-nose profile'])
        self.style.currentIndexChanged.connect(self._dirty)
        form.addRow('Inlay style',self.style)
        self.driver=QtWidgets.QComboBox()
        self.driver.setObjectName('depthDriver')
        self.driver.addItems(['Inlay depth','Plug height'])
        self.driver.currentIndexChanged.connect(self._driver_changed)
        form.addRow('Driving dimension',self.driver)
        self._field(form,'Driving depth / height','fit_value',3)
        self._field(form,'Bottom glue gap','glue_gap',.2)
        self._field(form,'Clearance per mating side','side_clearance',.1)
        self._field(form,'Backing clearance','backing_clearance',1)
        self._field(form,'XY movement increment','resolution_xy',.01)
        self._field(form,'Z movement increment','resolution_z',.01)
        page.addLayout(form)
        self.depth_summary=QtWidgets.QLabel()
        self.depth_summary.setObjectName('derivedDimensions')
        self.depth_summary.setWordWrap(True)
        page.addWidget(self.depth_summary)
        note=QtWidgets.QLabel('Side clearance is measured normal to each wall. Positive values loosen the fit. Machine resolution is the smallest commanded movement, not positioning accuracy. The preview shows realised geometry and any required changes.')
        note.setWordWrap(True)
        page.addWidget(note)
        page.addStretch()

    def _review_tab(self):
        page=self._tab('Review')
        self.review_info=QtWidgets.QLabel('Calculate both halves, inspect the result, then confirm Apply.')
        self.review_info.setObjectName('reviewSummary')
        self.review_info.setWordWrap(True)
        page.addWidget(self.review_info)
        self.preview_face=QtWidgets.QComboBox()
        self.preview_face.setObjectName('previewFace')
        self.preview_face.addItems(['Board','Plug','Both machining setups','Assembly section','Finished board'])
        self.preview_face.currentIndexChanged.connect(self._preview_changed)
        page.addWidget(self.preview_face)
        page.addWidget(self._button('Fit preview views','fitReviewPreview',self.fit_preview))
        self.depth_overlay=QtWidgets.QCheckBox('Show selected tool stepdown and cutting reach')
        self.depth_overlay.setObjectName('showDepthOverlay')
        self.depth_overlay.toggled.connect(self._draw)
        page.addWidget(self.depth_overlay)
        self.operation_info=QtWidgets.QLabel('Select a path in either preview or the operation list.')
        self.operation_info.setObjectName('selectedOperationInfo')
        self.operation_info.setWordWrap(True)
        page.addWidget(self.operation_info)
        self.operations=QtWidgets.QTreeWidget()
        self.operations.setObjectName('operationList')
        self.operations.setHeaderLabels(['Operation / tool','Cut time','Travel'])
        self.operations.setMinimumHeight(160)
        self.operations.itemSelectionChanged.connect(self._operation_selected)
        self.operations.itemChanged.connect(self._operation_visibility)
        page.addWidget(self.operations)
        row=QtWidgets.QHBoxLayout()
        row.addWidget(self._button('Isolate selected','isolateOperation',self.isolate_operation))
        row.addWidget(self._button('Show all paths','showAllOperations',self.show_all_operations))
        page.addLayout(row)
        page.addWidget(self._button('Override selected operation…','overrideOperation',self.override_operation))
        self.diagnostics=QtWidgets.QListWidget()
        self.diagnostics.setObjectName('diagnosticList')
        self.diagnostics.setMinimumHeight(90)
        self.diagnostics.currentItemChanged.connect(self._diagnostic_selected)
        page.addWidget(self.diagnostics)
        self.accept_proposal=self._button('Accept proposed paired geometry and recalculate','acceptProposal',self.accept_modification)
        self.accept_proposal.setEnabled(False)
        page.addWidget(self.accept_proposal)
        self.section=QtWidgets.QLabel('Section depths appear after calculation.')
        self.section.setObjectName('assemblySection')
        self.section.setWordWrap(True)
        page.addWidget(self.section)
        page.addStretch()

    def _dirty(self,*_):
        if self._updating or self._closed:
            return
        self.revision+=1
        self.accepted_proposal_hash=None
        if self.result:
            self.state='Needs recalculation'
            self.apply_button.setEnabled(False)
            self.status.setText('Settings changed. Recalculate both halves before applying.')
        self._update_depth_summary()
        self._draw()

    def _update_depth_summary(self):
        if not hasattr(self,'depth_summary'):
            return
        try:
            v,g,b=(self._value(k) for k in ('fit_value','glue_gap','backing_clearance'))
            d,h=(v,v-g+b) if self.driver.currentIndex()==0 else (v+g-b,v)
            self.depth_summary.setText('Inlay depth: '+formatted(d,display=self.display_unit)+' · Plug height: '+formatted(h,display=self.display_unit))
        except ValueError:
            self.depth_summary.setText('Enter valid dimensions to compute the matching half.')

    def _driver_changed(self,index):
        if self._updating:
            return
        try:
            v,g,b=(self._value(k) for k in ('fit_value','glue_gap','backing_clearance'))
            self._updating=True
            self._set('fit_value',v-g+b if index==1 else v+g-b)
        except ValueError:
            pass
        finally:
            self._updating=False
        self._dirty()

    def _units_changed(self,index):
        if self._updating:
            return
        old=self.display_unit
        new='in' if index else 'mm'
        values={}
        for name,(edit,kind) in self.fields.items():
            try:
                values[name]=quantity(edit.text(),kind,old)
            except ValueError:
                self.units_combo.blockSignals(True)
                self.units_combo.setCurrentIndex(1 if old=='in' else 0)
                self.units_combo.blockSignals(False)
                self.status.setText('Correct invalid fields before changing display units.')
                return
        self._updating=True
        self.display_unit=new
        for name,value in values.items():
            self._set(name,value)
        self._updating=False
        self._populate_tools()
        self._update_depth_summary()

    def _origin_preset(self,face,index):
        if self._updating or index==3:
            return
        try:
            w,h=self._value(face+'_width'),self._value(face+'_height')
            factor=(0,.5,1)[index]
            self._updating=True
            for key,val in [('x',factor*w),('y',factor*h),('z',0)]:
                self._set(face+'_origin_'+key,val)
        except ValueError:
            return
        finally:
            self._updating=False
        self._dirty()

    def capture_face(self,face):
        try:
            selection=Gui.Selection.getSelectionEx()
            tolerance=max(1e-6,min(self._value('resolution_xy'),self._value('resolution_z'))/8)
            record=native.capture_face(selection,tolerance=tolerance)
            self._updating=True
            self.existing_faces[face]=record
            for key,value in zip(('width','height','thickness'),record['stock']):
                self._set(face+'_'+key,value)
                self.fields[face+'_'+key][0].setReadOnly(True)
            for key,value in zip(('xmin','ymin','xmax','ymax'),record['bounds']):
                self._set(face+'_'+key,value)
            self.stock_bounds[face].setChecked(True)
            self._updating=False
            self.center('both')
            self._dirty()
            self.status.setText(face.title()+' setup uses the selected planar face.')
        except BaseException as exc:
            self._updating=False
            self._failed(str(exc))

    def use_slab(self,face):
        self.existing_faces.pop(face,None)
        for key in ('width','height','thickness'):
            self.fields[face+'_'+key][0].setReadOnly(False)
        self._dirty()

    def browse_svg(self):
        dialog=QtWidgets.QFileDialog(self.form,'Import filled SVG')
        dialog.setObjectName('svgFileDialog')
        dialog.setNameFilter('SVG vectors (*.svg)')
        dialog.setFileMode(QtWidgets.QFileDialog.ExistingFile)
        if dialog.exec():
            self.svg_path.setText(dialog.selectedFiles()[0])
            self.import_svg()

    def import_svg(self):
        if self.calculation.busy:
            self.status.setText('Cancel the running calculation before importing another file.')
            return
        try:
            path=Path(self.svg_path.text()).expanduser()
            if path.stat().st_size>16*1024*1024:
                raise ValueError('SVG exceeds the 16 MiB import budget; split the artwork.')
            text=path.read_text(encoding='utf-8-sig')
            self.source_path=str(path)
            self.source_text=text
            self._reimport=False
            self._start_import()
        except BaseException as exc:
            self._failed('Cannot import SVG: '+str(exc))

    def _desired_source_tolerance(self):
        q=min(self._value('resolution_xy'),self._value('resolution_z'))
        if q<=0:
            raise ValueError('Machine movement increments must be positive.')
        stretch=1.
        if self.imported and self._reimport:
            bounds=self.imported['bounds']
            stretch=max(self._value('art_width')/(bounds[2]-bounds[0]),
                        self._value('art_height')/(bounds[3]-bounds[1]))
        if stretch<=0:
            raise ValueError('Artwork dimensions must be positive.')
        return q/(32*stretch)

    def _start_import(self):
        self._action='import'
        self._set_busy(True)
        self.status.setText('Reading SVG filled regions…')
        self.calculation.start({'action':'import_svg','text':self.source_text,'tolerance':self._desired_source_tolerance()})

    def _aspect(self,changed):
        if not self.imported or not self.lock_aspect.isChecked() or self._updating:
            return
        try:
            b=self.imported['bounds']
            w,h=b[2]-b[0],b[3]-b[1]
            if min(w,h)<=0:
                return
            self._updating=True
            if changed=='width':
                self._set('art_height',self._value('art_width')*h/w)
            else:
                self._set('art_width',self._value('art_height')*w/h)
        finally:
            self._updating=False
        self._dirty()

    def center(self,axis='both'):
        try:
            self._updating=True
            for face in ('board','plug'):
                if axis in ('x','both'):
                    self._set(face+'_design_x',self._value(face+'_width')/2)
                if axis in ('y','both'):
                    self._set(face+'_design_y',self._value(face+'_height')/2)
        except ValueError as exc:
            self.status.setText(str(exc))
        finally:
            self._updating=False
        self._dirty()

    def fit_to_bounds(self):
        if not self.imported:
            return
        try:
            spec=self.get_spec(require_tools=False)
            bounds=spec['faces']['board']['bounds']
            current=bounds_of([p for r in spec['artwork']['regions'] for p in r['polygons']])
            factor=min((bounds[2]-bounds[0])/(current[2]-current[0]),(bounds[3]-bounds[1])/(current[3]-current[1]))
            self._updating=True
            self._set('art_width',self._value('art_width')*factor)
            self._set('art_height',self._value('art_height')*factor)
            self._set('board_design_x',(bounds[0]+bounds[2])/2)
            self._set('board_design_y',(bounds[1]+bounds[3])/2)
        except (ValueError,ZeroDivisionError) as exc:
            self.status.setText(str(exc))
        finally:
            self._updating=False
        self._dirty()

    def _art_matrix(self):
        b=self.imported['bounds']
        sx=self._value('art_width')/(b[2]-b[0])
        sy=self._value('art_height')/(b[3]-b[1])
        if min(sx,sy)<=0:
            raise ValueError('Artwork dimensions must be positive.')
        theta=math.radians(self._value('art_rotation'))
        a,bv,c,d=math.cos(theta)*sx,math.sin(theta)*sx,-math.sin(theta)*sy,math.cos(theta)*sy
        x,y=(b[0]+b[2])/2,(b[1]+b[3])/2
        return [a,bv,c,d,-a*x-c*y,-bv*x-d*y]

    def _region_changed(self,item):
        if self._updating:
            return
        rid=item.data(QtCore.Qt.UserRole)
        if item.checkState()==QtCore.Qt.Checked:
            self.excluded_ids.discard(rid)
        else:
            self.excluded_ids.add(rid)
        self._dirty()

    def restore_exclusions(self):
        self.excluded_ids.clear()
        self.source_exclusions.clear()
        self._updating=True
        for i in range(self.regions.count()):
            self.regions.item(i).setCheckState(QtCore.Qt.Checked)
        self._updating=False
        self._dirty()

    def _portion(self,rect):
        if not self.imported:
            return
        try:
            face='plug' if self.preview_face.currentIndex()==1 else 'board'
            cx,cy=self._value(face+'_design_x'),self._value(face+'_design_y')
            corners=[(rect[0]-cx,rect[1]-cy),(rect[2]-cx,rect[1]-cy),
                     (rect[2]-cx,rect[3]-cy),(rect[0]-cx,rect[3]-cy)]
            if face=='plug':
                corners=[(-x,y) for x,y in corners]
            a,b,c,d,e,f=self._art_matrix()
            det=a*d-b*c
            inverse=[d/det,-b/det,-c/det,a/det,(c*f-d*e)/det,(b*e-a*f)/det]
            polygon={'outer':[list(p) for p in corners+[corners[0]]],'holes':[]}
            self.source_exclusions.extend(transform_polygons([polygon],inverse))
            self._dirty()
            self.status.setText('Selected portion excluded from both halves. Recalculate to review the retained design.')
        except (ValueError,ZeroDivisionError) as exc:
            self.status.setText(str(exc))

    def _move_artwork(self,dx,dy):
        face='plug' if self.preview_face.currentIndex()==1 else 'board'
        try:
            self._updating=True
            self._set(face+'_design_x',self._value(face+'_design_x')+dx)
            self._set(face+'_design_y',self._value(face+'_design_y')+dy)
        finally:
            self._updating=False
        self._dirty()

    def _picked(self,identity):
        if identity.startswith('operation:'):
            oid=identity.split(':',1)[1]
            for i in range(self.operations.topLevelItemCount()):
                item=self.operations.topLevelItem(i)
                if item.data(0,QtCore.Qt.UserRole)==oid:
                    self.operations.setCurrentItem(item)
                    break
        else:
            for i in range(self.regions.count()):
                item=self.regions.item(i)
                if item.data(QtCore.Qt.UserRole)==identity:
                    self.regions.setCurrentItem(item)
                    break

    def fit_preview(self):
        self.view.fit_stock()
        gui=Gui.getDocument(self.document.Name)
        if gui is not None and gui.activeView() is not None:
            gui.activeView().fitAll()

    def _preview_changed(self,*_):
        self._draw()
        self.fit_preview()

    def refresh_tools(self):
        if not hasattr(self,'tool_table'):
            return
        try:
            before=json.dumps([self.tools,sorted(self.selected_tools)],sort_keys=True)
            previous={t['id']:t for t in self.tools}
            discovered=native.list_tools()
            self.tools=[]
            for tool in discovered:
                if tool['id'] in previous:
                    saved=previous.pop(tool['id'])
                    if saved.get('native_definition')==tool.get('native_definition'):
                        tool=saved
                    else:
                        # Reload changed physical geometry, keeping user-entered
                        # machining settings. No source library is ever written.
                        for key in ('feed','plunge_feed','travel_feed','spindle','max_stepdown','stepover'):
                            if key in saved:
                                tool[key]=saved[key]
                self.tools.append(self._tool_defaults(tool))
            self.tools.extend(t for t in previous.values() if t.get('local'))
            self.selected_tools.intersection_update(t['id'] for t in self.tools)
            self._populate_tools()
            if before!=json.dumps([self.tools,sorted(self.selected_tools)],sort_keys=True):
                self._dirty()
        except BaseException as exc:
            self.status.setText('Tool library could not be read: '+str(exc))

    def _tool_defaults(self,tool):
        result=copy.deepcopy(tool)
        diameter=float(result.get('diameter',0) or 0)
        for key,value in {'number':len(self.tools)+1,'cutting_length':0,'shank_diameter':diameter,
                          'corner_radius':0,'included_angle_deg':60,'tip_diameter':0,
                          'max_stepdown':min(1,diameter/3) if diameter else 0,
                          'stepover':diameter*.3,'feed':0,'plunge_feed':0,'travel_feed':0,'spindle':0}.items():
            if result.get(key) is None:
                result[key]=value
        return result

    def _populate_tools(self):
        previous=self._updating
        self._updating=True
        self._tool_row=-1
        self.tool_table.setRowCount(len(self.tools))
        for row,tool in enumerate(self.tools):
            check=QtWidgets.QTableWidgetItem()
            check.setFlags(QtCore.Qt.ItemIsEnabled|QtCore.Qt.ItemIsUserCheckable|QtCore.Qt.ItemIsSelectable)
            check.setCheckState(QtCore.Qt.Checked if tool['id'] in self.selected_tools else QtCore.Qt.Unchecked)
            if tool.get('supported') is False:
                check.setFlags(QtCore.Qt.ItemIsSelectable)
            self.tool_table.setItem(row,0,check)
            for col,value in enumerate([tool.get('name',tool['id']),tool.get('shape','unsupported'),formatted(float(tool.get('diameter',0) or 0),display=self.display_unit)],1):
                item=QtWidgets.QTableWidgetItem(value)
                item.setToolTip(str(tool.get('unsupported_reason',tool.get('reason',tool.get('library','')))))
                self.tool_table.setItem(row,col,item)
        self._updating=previous
        if self.tools:
            self.tool_table.setCurrentCell(0,1)
            self._tool_selected(0,1,-1,-1)

    def _tool_checked(self,item):
        if self._updating or item.column()!=0:
            return
        tool=self.tools[item.row()]
        if item.checkState()==QtCore.Qt.Checked:
            self.selected_tools.add(tool['id'])
        else:
            self.selected_tools.discard(tool['id'])
        self._dirty()

    def _tool_selected(self,row,column,oldrow,oldcolumn):
        if self._updating or not 0<=row<len(self.tools):
            return
        self._tool_row=row
        tool=self.tools[row]
        self._updating=True
        self.tool_number.setValue(int(tool.get('number',1)))
        for key in self.tool_field_keys:
            self._set('tool_'+key,float(tool.get(key,0) or 0))
        self._updating=False
        self.tool_description.setText(tool.get('name','Cutter')+' · '+str(tool.get('library','Local snapshot'))+'\n'+str(tool.get('unsupported_reason',tool.get('reason','Changes affect this inlay only.'))))

    def _tool_edited(self,*_):
        if self._updating or not 0<=self._tool_row<len(self.tools):
            return
        try:
            tool=self.tools[self._tool_row]
            values={key:self._value('tool_'+key) for key in self.tool_field_keys}
            values['number']=self.tool_number.value()
            if any(tool.get(key)!=value for key,value in values.items()):
                tool.update(values)
                diameter_item=self.tool_table.item(self._tool_row,3)
                if diameter_item is not None:
                    diameter_item.setText(formatted(tool['diameter'],display=self.display_unit))
                self._dirty()
        except ValueError as exc:
            self.status.setText(str(exc))

    def add_tool(self):
        dialog=QtWidgets.QDialog(self.form)
        dialog.setObjectName('addToolDialog')
        dialog.setWindowTitle('Add a cutter for this inlay')
        form=QtWidgets.QFormLayout(dialog)
        name=QtWidgets.QLineEdit('Local cutter')
        name.setObjectName('newToolName')
        shape=QtWidgets.QComboBox()
        shape.setObjectName('newToolShape')
        shape.addItems(['Flat end mill','V-bit','Bull nose'])
        diameter=QtWidgets.QLineEdit('3.175 mm')
        diameter.setObjectName('newToolDiameter')
        form.addRow('Name',name)
        form.addRow('Shape',shape)
        form.addRow('Diameter',diameter)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok|QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            try:
                d=quantity(diameter.text(),display=self.display_unit)
                if d<=0:
                    raise ValueError('Diameter must be positive.')
                kind=['flat','v','bull'][shape.currentIndex()]
                tool=self._tool_defaults({'id':'local:'+uuid.uuid4().hex,'name':name.text(),
                          'shape':kind,'diameter':d,'cutting_length':max(10,d*3),
                          'corner_radius':d/8 if kind=='bull' else 0,
                          'number':max([t.get('number',0) for t in self.tools]+[0])+1,
                          'supported':True,'local':True})
                self.tools.append(tool)
                self.selected_tools.add(tool['id'])
                self._populate_tools()
                self.tool_table.setCurrentCell(len(self.tools)-1,1)
                self._dirty()
            except ValueError as exc:
                self.status.setText(str(exc))

    def get_spec(self,require_tools=True):
        if not self.imported:
            raise ValueError('Import an SVG before calculating.')
        spec=default_spec()
        spec['revision']=self.revision
        matrix=self._art_matrix()
        spec['artwork']['regions']=[dict(r,polygons=transform_polygons(r['polygons'],matrix)) for r in self.imported['regions']]
        spec['excluded_region_ids']=sorted(self.excluded_ids)
        spec['exclusions']=transform_polygons(self.source_exclusions,matrix)
        spec['accepted_proposal_hash']=self.accepted_proposal_hash
        for face in ('board','plug'):
            stock=[self._value(face+'_'+key) for key in ('width','height','thickness')]
            if min(stock)<=0:
                raise ValueError('Stock dimensions must be positive.')
            margin=self._value(face+'_margin')
            bounds=([margin,margin,stock[0]-margin,stock[1]-margin] if self.stock_bounds[face].isChecked()
                    else [self._value(face+'_'+key) for key in ('xmin','ymin','xmax','ymax')])
            if margin<0 or bounds[2]<=bounds[0] or bounds[3]<=bounds[1]:
                raise ValueError('Machining bounds and margins must leave positive space.')
            x,y=self._value(face+'_design_x'),self._value(face+'_design_y')
            spec['faces'][face].update(stock=stock,bounds=bounds,design_to_face=[-1. if face=='plug' else 1.,0.,0.,1.,x,y],
                origin=[self._value(face+'_origin_'+axis) for axis in ('x','y','z')],
                clearance_height=self._value(face+'_clearance'),
                limits={key:spin.value() if check.isChecked() else None for key,(check,spin) in self.limit_widgets[face].items()})
            if face in self.existing_faces:
                capture=self.existing_faces[face]
                for key in ('stock_outline','stock_outline_tolerance'):
                    if key in capture:
                        spec['faces'][face][key]=copy.deepcopy(capture[key])
        spec['fit']={'driver':'depth' if self.driver.currentIndex()==0 else 'height','value':self._value('fit_value'),
                     **{k:self._value(k) for k in ('glue_gap','side_clearance','backing_clearance')}}
        spec['resolution']={axis:self._value('resolution_'+axis) for axis in ('xy','z')}
        spec['tools']=copy.deepcopy(self.tools)
        spec['selected_tool_ids']=sorted(self.selected_tools)
        spec['overrides']=copy.deepcopy(self.overrides)
        if require_tools and not self.selected_tools:
            raise ValueError('Choose at least one cutter and enter its feeds and spindle speed.')
        selected=[t for t in self.tools if t['id'] in self.selected_tools]
        styles={1:'flat',2:'v',3:'bull'}
        spec['style']=styles.get(self.style.currentIndex(), 'v' if any(t['shape']=='v' for t in selected)
                                else 'flat' if any(t['shape']=='flat' for t in selected) else 'bull')
        spec['source']={'text':self.source_text,'hash':self.imported['source_hash'],'filename':Path(self.source_path).name}
        spec['placement']={'matrix':matrix,'width':self._value('art_width'),'height':self._value('art_height'),
                           'rotation':self._value('art_rotation'),'source_exclusions':copy.deepcopy(self.source_exclusions)}
        if self.existing_faces:
            spec['existing_faces']=copy.deepcopy(self.existing_faces)
        return seal_spec(spec)

    def calculate(self):
        if self.calculation.busy:
            return
        if not self._check_document():
            return
        try:
            self._tool_edited()
            if not self.imported:
                raise ValueError('Import an SVG before calculating.')
            self._reimport=True
            desired=self._desired_source_tolerance()
            if desired<self.imported['flattening_tolerance']:
                self._reimport=True
                self._pending_calculate=True
                self._start_import()
                return
            spec=self.get_spec()
            self.calculated_spec=spec
            self._action='solve'
            self.state='Calculating'
            self._set_busy(True)
            self.status.setText('Calculating and checking both mating halves…')
            self.calculation.start({'action':'solve','spec':spec})
        except BaseException as exc:
            self._failed(str(exc))

    def _set_busy(self,value):
        self.calculate_button.setEnabled(not value)
        self.cancel_button.setEnabled(value)
        self.apply_button.setEnabled(False)
        self.progress_bar.setVisible(value)
        self.progress_bar.setRange(0,0 if value else 100)

    def _progress(self,value):
        if self._closed:
            return
        self.status.setText(str(value.get('message',value.get('stage','Calculating…'))))
        fraction=value.get('fraction')
        if isinstance(fraction,(int,float)):
            self.progress_bar.setRange(0,100)
            self.progress_bar.setValue(round(fraction*100))

    def _completed(self,result):
        if self._closed:
            return
        self._set_busy(False)
        if self._action=='import':
            self.imported=result
            self._updating=True
            if not self._reimport:
                self.source_exclusions=[]
                self.excluded_ids=set()
            self.regions.clear()
            for region in result['regions']:
                label=region.get('name',region['id'])+(' — unsupported' if not region.get('supported',True) else '')
                item=QtWidgets.QListWidgetItem(label)
                item.setData(QtCore.Qt.UserRole,region['id'])
                item.setFlags(item.flags()|QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(QtCore.Qt.Unchecked if region['id'] in self.excluded_ids else QtCore.Qt.Checked)
                self.regions.addItem(item)
            b=result['bounds']
            if not self._reimport:
                self._set('art_width',b[2]-b[0])
                self._set('art_height',b[3]-b[1])
                self.source_exclusions=[]
                self.excluded_ids=set()
            self._updating=False
            self.result=None
            self._dirty()
            count=len(result['regions'])
            self.source_info.setText(f'{Path(self.source_path).name}: {count} regions. '+
                                    ('SVG uses pixel dimensions; set the artwork width to confirm scale.' if result['size_assumed'] else 'Physical SVG dimensions retained.'))
            messages='\n'.join(d['message'] for d in result['diagnostics'])
            self.status.setText(messages or 'SVG imported. Place the artwork, choose tools, and calculate.')
            self._draw()
            self.fit_preview()
            if self._pending_calculate:
                self._pending_calculate=False
                QtCore.QTimer.singleShot(0,self.calculate)
            return
        try:
            current=self.get_spec()
            if result.get('input_hash')!=current['input_hash']:
                raise ValueError('Settings changed during calculation. Recalculate to review the current pair.')
            self.result=result
            self.hidden_operations.clear()
            self._populate_results()
            status=result.get('status')
            self.state={'ready':'Ready for review','needs_acceptance':'Needs acceptance','blocked':'Blocked','cancelled':'Draft'}.get(status,'Blocked')
            self.apply_button.setEnabled(status=='ready')
            self.status.setText(self.state+('. Inspect the pair, then confirm Apply.' if status=='ready' else '. Review the diagnostics and highlighted regions.'))
            self.tabs.setCurrentIndex(4)
            self._draw()
        except BaseException as exc:
            self._failed(str(exc))

    def _populate_results(self):
        self.operations.blockSignals(True)
        self.operations.clear()
        for op in self.result.get('operations',[]):
            timing=op.get('time',{})
            tool=next((t for t in self.tools if t['id']==op['tool_id']),{})
            item=QtWidgets.QTreeWidgetItem([op['face'].title()+' · '+op['purpose']+' · '+tool.get('name',op['tool_id']),
                    f"{timing.get('cut_s',0):.1f} s",f"{timing.get('travel_s',0):.1f} s"])
            item.setData(0,QtCore.Qt.UserRole,op['id'])
            item.setFlags(item.flags()|QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(0,QtCore.Qt.Checked)
            self.operations.addTopLevelItem(item)
        self.operations.blockSignals(False)
        self.diagnostics.clear()
        for diagnostic in self.result.get('diagnostics',[]):
            item=QtWidgets.QListWidgetItem(diagnostic.get('message',diagnostic.get('code','Diagnostic')))
            item.setData(QtCore.Qt.UserRole,diagnostic)
            self.diagnostics.addItem(item)
        counts=self.result.get('per_face_counts',{})
        lines=[]
        for face in ('board','plug'):
            value=counts.get(face,{})
            lines.append(f"{face.title()}: {value.get('operations',0)} operations, {value.get('tool_changes',0)} tool changes")
        lines.append('Estimated times assume constant commanded feeds with no acceleration or controller look-ahead model. Manual tool changes, setup and finishing are excluded.')
        self.review_info.setText('\n'.join(lines))
        proposal=self.result.get('proposal')
        self.accept_proposal.setEnabled(bool(proposal) and self.result.get('status')!='ready')
        realization=proposal.get('realization',{}) if proposal else {}
        if realization:
            lines.append('\nMachine-grid realization (requires explicit acceptance):')
            labels={'requested_side_clearance':'Requested clearance per side',
                    'planning_normal_clearance':'Compensated target clearance per side',
                    'xy_bound':'XY realization bound','z_bound':'Z realization bound'}
            for key,label in labels.items():
                if key in realization:
                    lines.append(label+': '+formatted(float(realization[key]),display=self.display_unit))
            lines.append(str(realization.get('meaning','')))
            self.review_info.setText('\n'.join(lines))
        d=self.result.get('derived',{})
        text=' · '.join(k.replace('_',' ').title()+': '+formatted(float(v),display=self.display_unit)
                      for k,v in d.items() if isinstance(v,(int,float)))
        verification=self.result.get('verification',{})
        contact=verification.get('first_contact',{})
        details=[text,'Nominated assembly pose: '+('clear' if contact.get('nominated_pose_clear') else 'not certified')]
        low,high=contact.get('additional_insertion_lower_mm'),contact.get('additional_insertion_upper_mm')
        if low is not None and high is not None:
            details.append('First rigid contact: additional insertion bracket '+formatted(low,display=self.display_unit)+
                           ' to '+formatted(high,display=self.display_unit)+'. This is not an exact seating depth.')
        for measure in verification.get('clearance_measurements',[]):
            label=measure['kind'].replace('_',' ').capitalize()
            nominal=measure.get('nominal_mm',measure.get('requested_normal_clearance_mm'))
            lower=measure.get('sample_min_mm',measure.get('lower_mm'))
            upper=measure.get('sample_max_mm',measure.get('upper_mm'))
            details.append(label+': requested '+formatted(nominal,display=self.display_unit)+
                           '; measured '+formatted(lower,display=self.display_unit)+' to '+formatted(upper,display=self.display_unit)+
                           '. '+measure.get('scope',''))
        details.append('Backing removal has no generated operation. Display sections are sampled; the full-stock insertion check is separate.')
        self.section.setText('\n'.join(details))

    def _failed(self,message):
        if self._closed:
            return
        self._set_busy(False)
        self.state='Needs recalculation'
        self.status.setText(message)
        self.apply_button.setEnabled(False)

    def cancel_calculation(self):
        self.calculation.cancel()
        self.status.setText('Cancelling calculation; earlier applied CAM work is preserved…')

    def _stopped(self):
        if self._closed:
            return
        self._set_busy(False)
        self.state='Draft'
        self.status.setText('Calculation cancelled. Draft settings and earlier applied CAM work are preserved.')

    def accept_modification(self):
        if not self.result or not self.result.get('proposal'):
            return
        self.accepted_proposal_hash=self.result['proposal']['hash']
        self.revision+=1
        self.calculate()

    def _operation_selected(self):
        items=self.operations.selectedItems()
        self.selected_operation=items[0].data(0,QtCore.Qt.UserRole) if items else None
        if self.selected_operation and self.result:
            op=next(o for o in self.result['operations'] if o['id']==self.selected_operation)
            tool=next(t for t in self.tools if t['id']==op['tool_id'])
            settings=dict(tool,**op.get('settings',{}))
            self.operation_info.setText(op['face'].title()+' · '+tool['name']+'\n'+
                'Stepdown '+formatted(settings['max_stepdown'],display=self.display_unit)+' (purple plane); cutting reach '+
                formatted(tool['cutting_length'],display=self.display_unit)+' (red plane).\n'+
                'Cut '+formatted(settings['feed'],'feed',self.display_unit)+'; plunge '+
                formatted(settings['plunge_feed'],'feed',self.display_unit)+'; travel '+
                formatted(settings['travel_feed'],'feed',self.display_unit)+'; spindle '+str(settings['spindle'])+' rpm.'+
                (' Local operation overrides apply.' if op['id'] in self.overrides else ' Inherits cutter settings.')+'\n'+
                f"Baseline {op['time']['baseline_s']:.2f} s; chosen order {op['time']['optimized_s']:.2f} s. Manual tool change time excluded.")
        self._draw()

    def _operation_visibility(self,item,column):
        oid=item.data(0,QtCore.Qt.UserRole)
        if item.checkState(0)==QtCore.Qt.Checked:
            self.hidden_operations.discard(oid)
        else:
            self.hidden_operations.add(oid)
        self._draw()

    def isolate_operation(self):
        if not self.selected_operation:
            return
        for i in range(self.operations.topLevelItemCount()):
            item=self.operations.topLevelItem(i)
            item.setCheckState(0,QtCore.Qt.Checked if item.data(0,QtCore.Qt.UserRole)==self.selected_operation else QtCore.Qt.Unchecked)

    def show_all_operations(self):
        for i in range(self.operations.topLevelItemCount()):
            self.operations.topLevelItem(i).setCheckState(0,QtCore.Qt.Checked)

    def override_operation(self):
        if not self.result or not self.selected_operation:
            self.status.setText('Select a calculated operation to override its settings.')
            return
        op=next(o for o in self.result['operations'] if o['id']==self.selected_operation)
        tool=next(t for t in self.tools if t['id']==op['tool_id'])
        current={**tool,**op.get('settings',{}),**self.overrides.get(op['id'],{})}
        dialog=QtWidgets.QDialog(self.form)
        dialog.setObjectName('operationOverrideDialog')
        dialog.setWindowTitle('Settings for '+op['face']+' '+op['purpose'])
        layout=QtWidgets.QFormLayout(dialog)
        edits={}
        for key,kind in [('feed','feed'),('plunge_feed','feed'),('travel_feed','feed'),('spindle','rpm'),('max_stepdown','length'),('stepover','length')]:
            edit=QtWidgets.QLineEdit(formatted(current[key],kind,self.display_unit))
            edit.setObjectName('override_'+key)
            edits[key]=(edit,kind)
            layout.addRow(key.replace('_',' ').title(),edit)
        inherit=QtWidgets.QCheckBox('Restore inherited tool settings')
        inherit.setObjectName('restoreInheritedSettings')
        layout.addRow(inherit)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok|QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec():
            try:
                if inherit.isChecked():
                    self.overrides.pop(op['id'],None)
                else:
                    self.overrides[op['id']]={key:quantity(edit.text(),kind,self.display_unit) for key,(edit,kind) in edits.items()}
                self._dirty()
            except ValueError as exc:
                self.status.setText(str(exc))

    def _diagnostic_selected(self,current,previous):
        if current:
            diagnostic=current.data(QtCore.Qt.UserRole)
            ids=diagnostic.get('region_ids',[])
            if ids:
                self._picked(ids[0])

    def _draw(self,*_):
        if self._updating or not hasattr(self,'view') or not hasattr(self,'preview_face'):
            return
        try:
            mode=self.preview_face.currentIndex()
            face='plug' if mode==1 else 'board'
            if self.imported:
                spec=self.get_spec(require_tools=False)
            else:
                spec=default_spec()
                for name in ('board','plug'):
                    setup=spec['faces'][name]
                    setup['stock']=[self._value(name+'_'+k) for k in ('width','height','thickness')]
                    margin=self._value(name+'_margin')
                    setup['bounds']=([margin,margin,setup['stock'][0]-margin,setup['stock'][1]-margin]
                        if self.stock_bounds[name].isChecked() else [self._value(name+'_'+k) for k in ('xmin','ymin','xmax','ymax')])
                    setup['origin']=[self._value(name+'_origin_'+axis) for axis in ('x','y','z')]
                    if name in self.existing_faces:
                        setup['stock_outline']=self.existing_faces[name]['stock_outline']
                spec['existing_faces']=self.existing_faces
            setup=spec['faces'][face]
            self.view.reset_scene(setup['stock'],setup['bounds'],setup.get('stock_outline'))
            valid=self.result and self.result.get('input_hash')==spec.get('input_hash')
            self.view_mode.setEnabled(mode<3)
            self.view.set_mode(['select','pan','portion','move'][self.view_mode.currentIndex()] if mode<3 else 'pan')
            if mode==3 and valid:
                self.view.show_section(spec,self.result)
                self.legend.setText('Assembly centre section: brown board, pale plug. Dashed planes: white board top, blue plug tip, red pocket floor. Sampled display; full stock is checked separately. Backing removal has no generated path.')
            else:
                transform=setup['design_to_face']
                selected=self.regions.currentItem()
                selected_id=selected.data(QtCore.Qt.UserRole) if selected else None
                for region in spec['artwork']['regions']:
                    kind='selected' if region['id']==selected_id else 'excluded' if region['id'] in self.excluded_ids else 'source'
                    self.view.add_polygons(transform_polygons(region['polygons'],transform),kind,region['id'],.3)
                self.view.add_polygons(transform_polygons(spec['exclusions'],transform),'excluded',opacity=.7,z=4)
                if valid:
                    preview=self.result.get('preview',{})
                    if mode==4:
                        proposal=self.result.get('proposal') or {}
                        self.view.add_polygons(transform_polygons(proposal.get('predicted_board_polygons',[]),transform),'blocked',opacity=.35,z=2)
                        self.view.add_polygons(transform_polygons(proposal.get('predicted_plug_polygons',[]),transform),'cuttable',opacity=.95,z=3)
                        self.legend.setText('Expected finished surface after backing removal: green plug, red remaining pocket gap, grey source outline. No backing-removal path is generated.')
                    else:
                        for key,kind in [('cuttable_polygons','cuttable'),('blocked_polygons','blocked'),('excluded_polygons','excluded')]:
                            self.view.add_polygons(transform_polygons(preview.get(key,[]),transform),kind,
                                                   opacity=1. if kind=='blocked' else .5,z=6 if kind=='blocked' else 3)
                        proposal=self.result.get('proposal')
                        if proposal:
                            self.view.add_polygons(transform_polygons(proposal.get('difference_polygons',[]),transform),'proposal',opacity=.75,z=5)
                        for op in self.result.get('operations',[]):
                            if op['face']==face and op['id'] not in self.hidden_operations:
                                self.view.add_operation(op,setup['origin'],op['id']==self.selected_operation)
                if mode!=4 or not valid:
                    self.legend.setText('Green: cuttable · Red: blocked · Purple hatch: excluded · Gold: proposed change · White cross: work origin. Blue: cutting; dotted purple: travel. Material mesh is a coarse display.'+
                        (' Board plan shown here; both setups appear side by side in the 3D view.' if mode==2 else '')+
                        (' Calculate to view this result.' if mode>=3 and not valid else ''))
                self.view.add_origin(setup['origin'])
            if hasattr(self,'overlay'):
                self.overlay.show(spec,self.result if valid else None,self.selected_operation,
                                  'both' if mode==2 else face,self.hidden_operations,self.depth_overlay.isChecked())
        except (ValueError,ZeroDivisionError,KeyError,RuntimeError,ReferenceError):
            # Partly edited dimensions are normal during text entry. A later
            # Calculate displays a concrete validation diagnostic.
            if hasattr(self,'overlay'):
                self.overlay.clear()

    def _check_document(self):
        if self._closed:
            return False
        if App.listDocuments().get(self.document_name) is not self.document:
            self.reject()
            return False
        current=App.ActiveDocument is not None and App.ActiveDocument.Name==self.document_name
        self.context.setText('Document: '+self.document.Label+('' if current else ' — activate this document to calculate or apply.'))
        if not current:
            self.calculate_button.setEnabled(False)
            self.apply_button.setEnabled(False)
        elif not self.calculation.busy:
            self.calculate_button.setEnabled(True)
            if self.result and self.state=='Ready for review':
                self.apply_button.setEnabled(True)
        return current

    def apply(self):
        if not self._check_document() or self.calculation.busy:
            return
        try:
            spec=self.get_spec()
            if not self.result or self.result.get('status')!='ready' or self.result.get('input_hash')!=fingerprint(spec):
                raise ValueError('Calculate and review the current unblocked pair before applying.')
            dialog=QtWidgets.QDialog(self.form)
            dialog.setObjectName('confirmApplyDialog')
            dialog.setWindowTitle('Confirm paired CAM operations')
            layout=QtWidgets.QVBoxLayout(dialog)
            summary=QtWidgets.QPlainTextEdit()
            summary.setReadOnly(True)
            summary.setObjectName('applyReviewText')
            lines=[self.review_info.text(),self.section.text(),
                   'Excluded regions: '+(', '.join(sorted(self.excluded_ids)) or 'none'),
                   f'Excluded portions: {len(self.source_exclusions)}',
                   'Accepted paired modification: '+(self.accepted_proposal_hash or 'none')]
            lines.append('Machine resolution: XY '+formatted(spec['resolution']['xy'],display=self.display_unit)+
                         '; Z '+formatted(spec['resolution']['z'],display=self.display_unit))
            for face,setup in spec['faces'].items():
                lengths=lambda values:', '.join(formatted(v,display=self.display_unit) for v in values)
                lines.append(face.title()+' setup: stock W, H, thickness = '+lengths(setup['stock'])+
                    '\nWork origin X, Y, Z = '+lengths(setup['origin'])+
                    '\nMachining bounds xmin, ymin, xmax, ymax = '+lengths(setup['bounds'])+
                    '\nClearance above stock = '+formatted(setup['clearance_height'],display=self.display_unit)+
                    '\nDesign centre X, Y = '+lengths(setup['design_to_face'][4:])+
                    ('; mirrored X for the assembly flip.' if face=='plug' else '; unmirrored board face.')+
                    ('\nSource: '+spec['existing_faces'][face]['object_name']+'.'+spec['existing_faces'][face]['face_name']
                     if face in spec.get('existing_faces',{}) else '\nSource: rectangular slab.'))
            for op in self.result['operations']:
                tool=next(t for t in spec['tools'] if t['id']==op['tool_id'])
                settings=op['settings']
                setting_text='; '.join(label+' '+formatted(settings[key],kind,self.display_unit)
                    for key,label,kind in [('feed','Cut','feed'),('plunge_feed','Plunge','feed'),('travel_feed','Travel','feed'),
                                          ('spindle','Spindle','rpm'),('max_stepdown','Stepdown','length'),('stepover','Spacing','length')])
                lines.append(op['face'].title()+' / '+op['purpose']+' / T'+str(tool['number'])+' '+tool['name']+'\n'+setting_text)
            lines.append('This creates two native CAM Jobs. Backing removal and machine export remain separate workshop/CAM steps.')
            summary.setPlainText('\n\n'.join(lines))
            layout.addWidget(summary)
            buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok|QtWidgets.QDialogButtonBox.Cancel)
            buttons.setObjectName('confirmApplyButtons')
            buttons.button(QtWidgets.QDialogButtonBox.Ok).setText('Apply both Jobs')
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            dialog.resize(650,500)
            if not dialog.exec():
                return
            if self.get_spec()['input_hash']!=spec['input_hash']:
                raise ValueError('The draft changed during review; recalculate before applying.')
            self.applied=native.apply_pair(self.document,spec,self.result)
            self.overlay.clear()
            self.state='Applied'
            self.apply_button.setEnabled(False)
            self.status.setText('Both native CAM Jobs were applied. Save the document, inspect/edit in CAM, and use normal CAM export.')
        except BaseException as exc:
            self._failed(str(exc))

    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.StandardButton.Close.value

    def isAllowedAlterDocument(self):
        # This is a draft outside the native edit transaction. Other documents
        # may be used; active-document and source-shape checks guard Apply.
        return True

    def isAllowedAlterSelection(self):
        return True

    def isAllowedAlterView(self):
        return True

    def accept(self):
        return self.reject()

    def reject(self):
        global ACTIVE_PANEL
        if self._closed:
            return True
        self._closed=True
        self.context_timer.stop()
        self.overlay.clear()
        if self.calculation.busy:
            self.calculation.cancel()
            process=self.calculation.process
            if process and not process.waitForFinished(2000):
                process.kill()
                process.waitForFinished(1000)
        ACTIVE_PANEL=None
        Gui.Control.closeDialog()
        return True

    def _form_destroyed(self,*_):
        """FreeCAD may close its Tasks widget when the owning document closes."""
        global ACTIVE_PANEL
        self._closed=True
        try:
            self.context_timer.stop()
            self.overlay.clear()
            if self.calculation.busy:
                self.calculation.cancel()
        except (RuntimeError,ReferenceError):
            # Qt may already have destroyed children of the task widget.
            pass
        if ACTIVE_PANEL is self:
            ACTIVE_PANEL=None


def show_panel():
    global ACTIVE_PANEL
    if ACTIVE_PANEL and not ACTIVE_PANEL._closed:
        if App.listDocuments().get(ACTIVE_PANEL.document_name) is ACTIVE_PANEL.document:
            return ACTIVE_PANEL
        ACTIVE_PANEL._form_destroyed()
    if Gui.Control.activeDialog():
        App.Console.PrintWarning('Close the active FreeCAD task before starting an inlay.\n')
        return None
    ACTIVE_PANEL=InlayPanel()
    Gui.Control.showDialog(ACTIVE_PANEL)
    return ACTIVE_PANEL
