"""Zoomable, selectable artwork/result view plus native viewport overlays."""
from __future__ import annotations

import math

from .qt_compat import QtCore, QtGui, QtWidgets

COLORS = {'source':'#8a96a3', 'cuttable':'#2ca58d', 'blocked':'#dc5a48',
          'excluded':'#b691cc', 'proposal':'#dcab38', 'path':'#367ac8',
          'travel':'#bd93d4', 'selected':'#ffe26f'}


def painter_path(polygons):
    path = QtGui.QPainterPath()
    path.setFillRule(QtCore.Qt.OddEvenFill)
    for polygon in polygons:
        for ring in [polygon['outer']]+polygon.get('holes', []):
            if not ring:
                continue
            path.moveTo(*ring[0])
            for p in ring[1:]:
                path.lineTo(*p)
            path.closeSubpath()
    return path


class ArtworkView(QtWidgets.QGraphicsView):
    picked = QtCore.Signal(str)
    portion = QtCore.Signal(list)
    moved = QtCore.Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('artworkPreview')
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.setMinimumHeight(230)
        self.setRenderHint(QtGui.QPainter.Antialiasing)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag)
        self.mode = 'select'
        self._start = None
        self._rect = None
        self._first = True
        self.stock = [100,100]
        self.setBackgroundBrush(QtGui.QColor('#19232b'))
        self.scale(1,-1)

    def set_mode(self, mode):
        self.mode = mode
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag if mode=='pan' else QtWidgets.QGraphicsView.DragMode.NoDrag)

    def reset_scene(self, stock, bounds, outline=None):
        self.scene().clear()
        self._rect = None
        self.stock = stock
        pen = QtGui.QPen(QtGui.QColor('#d8c8a9'))
        pen.setCosmetic(True)
        outline=outline or [{'outer':[[0,0],[stock[0],0],[stock[0],stock[1]],[0,stock[1]]],'holes':[]}]
        self.scene().addPath(painter_path(outline),pen,QtGui.QBrush(QtGui.QColor('#303a3d')))
        pen = QtGui.QPen(QtGui.QColor('#e8c578'),1,QtCore.Qt.DashLine)
        pen.setCosmetic(True)
        self.scene().addRect(bounds[0],bounds[1],bounds[2]-bounds[0],bounds[3]-bounds[1],pen)
        self.scene().setSceneRect(-stock[0]*.08,-stock[1]*.08,stock[0]*1.16,stock[1]*1.16)

    def add_origin(self, origin):
        size=max(self.stock[:2])*.015
        pen=QtGui.QPen(QtGui.QColor('#ffffff')); pen.setCosmetic(True)
        x,y=origin[:2]
        self.scene().addLine(x-size,y,x+size,y,pen).setZValue(30)
        self.scene().addLine(x,y-size,x,y+size,pen).setZValue(30)

    def show_section(self, spec, result):
        """Display sampled actual surfaces; holes break the material polygons."""
        self.scene().clear()
        self._rect=None
        sections=result.get('preview',{}).get('sections',[])
        if not sections or not sections[0].get('points'):
            return
        points=sections[0]['points']
        x0,x1=points[0]['x'],points[-1]['x']
        seating=result['derived']['seating_depth']
        lower=-spec['faces']['board']['stock'][2]
        upper=spec['faces']['plug']['stock'][2]-seating
        for name,depth_key,bottom,color in [('board','board_depth',lower,'#a97942'),
                                          ('plug','plug_assembled_depth',upper,'#e9ce8a')]:
            runs=[]; run=[]
            for p in points:
                if p.get(name+'_material_present',True):
                    run.append((p['x'],-p[depth_key]))
                elif run:
                    runs.append(run); run=[]
            if run: runs.append(run)
            for run in runs:
                if len(run)<2: continue
                poly={'outer':[[run[0][0],bottom],*run,[run[-1][0],bottom]],'holes':[]}
                pen=QtGui.QPen(QtGui.QColor(color)); pen.setCosmetic(True)
                self.scene().addPath(painter_path([poly]),pen,QtGui.QBrush(QtGui.QColor(color)))
        for depth,color in [(0,'#ffffff'),(-seating,'#77c9dc'),(-result['derived']['depth'],'#dd9191')]:
            pen=QtGui.QPen(QtGui.QColor(color),1,QtCore.Qt.DashLine); pen.setCosmetic(True)
            self.scene().addLine(x0,depth,x1,depth,pen)
        pad=max(x1-x0,upper-lower)*.08
        self.scene().setSceneRect(x0-pad,lower-pad,x1-x0+2*pad,upper-lower+2*pad)
        self.fit_stock()

    def add_polygons(self, polygons, kind, identity=None, opacity=.65, z=1):
        item = QtWidgets.QGraphicsPathItem(painter_path(polygons))
        color = QtGui.QColor(COLORS[kind])
        pen = QtGui.QPen(color)
        pen.setCosmetic(True)
        item.setPen(pen)
        fill = QtGui.QColor(color)
        fill.setAlphaF(opacity)
        item.setBrush(QtGui.QBrush(fill,QtCore.Qt.Dense4Pattern if kind=='excluded' else QtCore.Qt.SolidPattern))
        item.setData(0,identity)
        item.setData(1,kind)
        item.setZValue(z)
        self.scene().addItem(item)
        return item

    def add_operation(self, operation, origin, selected=False):
        paths = {role: QtGui.QPainterPath() for role in ('cut','travel')}
        moves = operation.get('moves', [])
        for a,b in zip(moves,moves[1:]):
            role = 'travel' if b.get('role')=='travel' else 'cut'
            path = paths[role]
            path.moveTo(a['x']+origin[0],a['y']+origin[1])
            path.lineTo(b['x']+origin[0],b['y']+origin[1])
        for role,path in paths.items():
            item = self.scene().addPath(path)
            color = COLORS['selected' if selected else 'path' if role=='cut' else 'travel']
            pen = QtGui.QPen(QtGui.QColor(color),2 if selected else 1,
                            QtCore.Qt.SolidLine if role=='cut' else QtCore.Qt.DotLine)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setZValue(20)
            item.setData(0,'operation:'+operation['id'])
            item.setData(1,'path')

    def fit_stock(self):
        self.fitInView(self.sceneRect(), QtCore.Qt.KeepAspectRatio)
        self._first = False

    def showEvent(self,event):
        super().showEvent(event)
        if self._first:
            self.fit_stock()

    def wheelEvent(self,event):
        factor=1.2 if event.angleDelta().y()>0 else 1/1.2
        current=abs(self.transform().m11())
        if .01 < current*factor < 100000:
            self.scale(factor,factor)
        event.accept()

    def mousePressEvent(self,event):
        if event.button()==QtCore.Qt.LeftButton and self.mode in ('portion','move'):
            self._start = self.mapToScene(event.position().toPoint())
            if self.mode=='portion':
                pen=QtGui.QPen(QtGui.QColor(COLORS['excluded']),1,QtCore.Qt.DashLine)
                pen.setCosmetic(True)
                self._rect=self.scene().addRect(QtCore.QRectF(self._start,self._start),pen)
                self._rect.setZValue(100)
            event.accept()
        elif event.button()==QtCore.Qt.LeftButton and self.mode=='select':
            for item in self.items(event.position().toPoint()):
                identity=item.data(0)
                if identity:
                    self.picked.emit(str(identity))
                    break
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self._start is not None and self._rect is not None:
            self._rect.setRect(QtCore.QRectF(self._start,self.mapToScene(event.position().toPoint())).normalized())
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if self._start is not None:
            end=self.mapToScene(event.position().toPoint())
            if self.mode=='portion' and self._rect:
                rect=QtCore.QRectF(self._start,end).normalized()
                self.scene().removeItem(self._rect)
                self._rect=None
                if rect.width()>0 and rect.height()>0:
                    self.portion.emit([rect.left(),rect.top(),rect.right(),rect.bottom()])
            elif self.mode=='move':
                self.moved.emit(end.x()-self._start.x(),end.y()-self._start.y())
            self._start=None
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class NativeOverlay:
    """Transient Coin geometry attached only to the owning document's view."""
    def __init__(self, document, picked=None):
        self.document=document
        self.picked=picked
        self.root=self.graph=self.view=self.callback=None
        self.identities={}

    def clear(self):
        if self.callback is not None and self.view is not None:
            try:
                from pivy import coin
                self.view.removeEventCallbackPivy(coin.SoMouseButtonEvent.getClassTypeId(),self.callback)
            except (RuntimeError,ReferenceError):
                pass
        if self.root is not None and self.graph is not None:
            try:
                self.graph.removeChild(self.root)
            except (RuntimeError,ReferenceError):
                pass
        self.root=self.graph=self.view=self.callback=None
        self.identities={}

    def _pick(self, callback):
        from pivy import coin
        event=callback.getEvent()
        if event.getButton()!=coin.SoMouseButtonEvent.BUTTON1 or event.getState()!=coin.SoButtonEvent.DOWN:
            return
        if event.wasCtrlDown() or event.wasShiftDown() or event.wasAltDown():
            return
        ray=coin.SoRayPickAction(self.view.getViewer().getSoRenderManager().getViewportRegion())
        ray.setPoint(event.getPosition())
        ray.setRadius(5)
        ray.setPickAll(True)
        # getSceneGraph() on the view is the document subgraph; the render
        # manager's root also supplies the camera needed for a screen ray.
        ray.apply(self.view.getViewer().getSoRenderManager().getSceneGraph())
        for hit in ray.getPickedPointList():
            path=hit.getPath()
            for i in range(path.getLength()-1,-1,-1):
                identity=self.identities.get(str(path.getNode(i).getName()))
                if identity:
                    callback.setHandled()
                    # Rebuilding a selected overlay must not destroy this Coin
                    # callback while Coin is still dispatching the mouse event.
                    QtCore.QTimer.singleShot(0,lambda value=identity:self.picked(value))
                    return

    def show(self, spec, result=None, selected=None, face='board', hidden=(), depth_overlay=False):
        self.clear()
        import FreeCADGui as Gui
        from pivy import coin
        gui=Gui.getDocument(self.document.Name)
        if gui is None or gui.activeView() is None:
            return
        self.view=gui.activeView()
        self.graph=self.view.getSceneGraph()
        self.root=coin.SoSeparator()
        self.graph.addChild(self.root)
        result=result or {}
        faces=('board','plug') if face=='both' else (face,)
        shift=0.
        for name in faces:
            setup=spec['faces'][name]
            w,h,t=setup['stock']
            frame=spec.get('existing_faces',{}).get(name,{}).get('face_to_world') if face!='both' else None
            def xyz(point):
                if frame:
                    return tuple(sum(frame[i][j]*point[j] for j in range(3))+frame[i][3] for i in range(3))
                return (point[0]+shift,point[1],point[2])
            group=coin.SoSeparator()
            self.root.addChild(group)
            def lines(points,color,identity=None,width=1,pattern=None):
                if len(points)<2: return
                node=coin.SoSeparator()
                if identity:
                    key='InlayPreview'+str(len(self.identities))
                    node.setName(key); self.identities[key]=identity
                material=coin.SoBaseColor(); material.rgb=color; node.addChild(material)
                style=coin.SoDrawStyle(); style.lineWidth=width
                if pattern is not None: style.linePattern=pattern
                node.addChild(style)
                coords=coin.SoCoordinate3()
                coords.point.setValues(0,len(points),[xyz(p) for p in points]); node.addChild(coords)
                segments=coin.SoLineSet(); segments.numVertices.setValues(0,1,[len(points)]); node.addChild(segments)
                group.addChild(node)
            def polygon_lines(polygons,color,z=0.02,identity=None):
                for polygon in polygons:
                    for ring in [polygon['outer']]+polygon.get('holes',[]):
                        if ring:
                            lines([(p[0],p[1],z) for p in [*ring,ring[0]]],color,identity)
            outline=setup.get('stock_outline') or [{'outer':[[0,0],[w,0],[w,h],[0,h]],'holes':[]}]
            polygon_lines(outline,(.75,.64,.43),0)
            polygon_lines(outline,(.45,.37,.25),-t)
            for polygon in outline:
                for ring in [polygon['outer']]+polygon.get('holes',[]):
                    # Bound visual detail independently from certified geometry.
                    stride=max(1,len(ring)//32)
                    for x,y in ring[::stride]: lines([(x,y,0),(x,y,-t)],(.45,.37,.25))
            xmin,ymin,xmax,ymax=setup['bounds']
            rectangle=[[xmin,ymin],[xmax,ymin],[xmax,ymax],[xmin,ymax],[xmin,ymin]]
            lines([(x,y,.03) for x,y in rectangle],(.9,.7,.3),pattern=0xcccc)
            ox,oy,oz=setup['origin']; size=max(w,h)*.04
            for axis,color in [(0,(1,.3,.3)),(1,(.3,1,.3)),(2,(.3,.6,1))]:
                end=[ox,oy,oz];end[axis]+=size
                lines([(ox,oy,oz),end],color,width=2)
            matrix=setup['design_to_face']
            def mapped(polygons):
                from .schema import transform_polygons
                return transform_polygons(polygons,matrix)
            for region in spec['artwork']['regions']:
                excluded=region['id'] in spec.get('excluded_region_ids',[])
                polygon_lines(mapped(region['polygons']),(.7,.5,.8) if excluded else (.6,.65,.7),.04,region['id'])
            for key,color in [('cuttable_polygons',(.2,.75,.6)),('blocked_polygons',(.95,.25,.2)),('excluded_polygons',(.7,.5,.8))]:
                polygon_lines(mapped(result.get('preview',{}).get(key,[])),color,.05)
            surface=result.get('preview',{}).get(name,{})
            if surface.get('vertices') and surface.get('faces'):
                mesh=coin.SoSeparator()
                color=coin.SoMaterial(); color.diffuseColor=(.66,.47,.25) if name=='board' else (.85,.7,.42)
                mesh.addChild(color)
                coordinates=coin.SoCoordinate3()
                vertices=[xyz((float(x),float(y),-float(z))) for x,y,z in surface['vertices']]
                coordinates.point.setValues(0,len(vertices),vertices);mesh.addChild(coordinates)
                faces_node=coin.SoIndexedFaceSet()
                indices=[n for triangle in surface['faces'] for n in [*triangle,-1]]
                faces_node.coordIndex.setValues(0,len(indices),indices);mesh.addChild(faces_node)
                group.addChild(mesh)
            for op in result.get('operations',[]):
                if op['face']!=name or op['id'] in hidden: continue
                if depth_overlay and op['id']==selected:
                    tool=next(tool for tool in spec['tools'] if tool['id']==op['tool_id'])
                    settings=dict(tool,**op.get('settings',{}))
                    for depth,color in [(settings['max_stepdown'],(.7,.4,.9)),(tool['cutting_length'],(.9,.35,.25))]:
                        lines([(x,y,-depth) for x,y in rectangle],color,pattern=0xcccc)
                moves=op.get('moves',[])
                # Batch consecutive roles: distinct travel colour and stipple,
                # without a Coin node for each individual machining segment.
                run=[]; last_role=None
                for a,b in zip(moves,moves[1:]):
                    role='travel' if b.get('role')=='travel' else 'cut'
                    if role!=last_role:
                        if run: lines(run,(.74,.58,.84) if last_role=='travel' else ((1,.9,.2) if op['id']==selected else (.15,.65,.9)),
                                      'operation:'+op['id'],2 if op['id']==selected else 1,0xcccc if last_role=='travel' else None)
                        run=[(a['x']+ox,a['y']+oy,a['z']+oz+.02)]
                    run.append((b['x']+ox,b['y']+oy,b['z']+oz+.02));last_role=role
                if run: lines(run,(.74,.58,.84) if last_role=='travel' else ((1,.9,.2) if op['id']==selected else (.15,.65,.9)),
                              'operation:'+op['id'],2 if op['id']==selected else 1,0xcccc if last_role=='travel' else None)
            shift+=w+20
        if self.picked:
            self.callback=self.view.addEventCallbackPivy(coin.SoMouseButtonEvent.getClassTypeId(),self._pick)
