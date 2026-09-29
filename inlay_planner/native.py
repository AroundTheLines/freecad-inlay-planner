"""The FreeCAD boundary: snapshots in, built-in CAM objects out.

No class in this module is assigned as a persistent document proxy.  Imports
of FreeCAD are deliberately local so input checks can also run in plain Python.
"""

from __future__ import annotations

import hashlib
import json
import math

from .schema import fingerprint, finite_tree, hash_json


def input_hash(spec):
    return fingerprint(spec)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _positive(value, name, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    if not math.isfinite(value) or (value < 0 if zero else value <= 0):
        raise ValueError(f"{name} must be {'nonnegative' if zero else 'positive'}")
    return float(value)


def validate_pair(spec, result):
    """Reject stale, uncertified, incomplete or unserializable results pre-write."""
    if spec.get("schema_version") != 1 or result.get("schema_version") != 1:
        raise ValueError("Unsupported inlay schema version")
    expected = input_hash(spec)
    if spec.get("input_hash") != expected or result.get("input_hash") != expected:
        raise ValueError("Inputs changed: recalculate before applying paths")
    if result.get("revision") != spec.get("revision"):
        raise ValueError("Calculated revision is stale")
    if result.get("status") != "ready":
        raise ValueError("Only a ready, accepted calculation can be applied")
    if result.get("verification", {}).get("status") not in ("passed", "certified"):
        raise ValueError("Both mating halves require successful motion verification")
    if any(d.get("severity") in ("error", "blocked") for d in result.get("diagnostics", [])):
        raise ValueError("Included machining diagnostics still block application")
    proposal = result.get("proposal")
    if proposal and proposal.get("hash") != spec.get("accepted_proposal_hash"):
        raise ValueError("The proposed artwork modification has not been accepted")
    finite_tree(spec)
    finite_tree(result)  # Reject NaN even in metadata before touching the document.
    motion_hash = hash_json(result.get("operations"))
    if result["verification"].get("motion_hash") != motion_hash:
        raise ValueError("The motion verification certificate is missing or no longer matches; recalculate before applying")
    tools = {tool["id"]: tool for tool in spec["tools"]}
    if len(tools) != len(spec["tools"]):
        raise ValueError("Physical tool identifiers must be unique")
    selected = set(spec["selected_tool_ids"])
    if not selected <= tools.keys():
        raise ValueError("A selected tool is missing from the input snapshot")
    for key in ("xy", "z"):
        _positive(spec["resolution"][key], f"{key.upper()} resolution")
    seen = set()
    ordered = []
    counts = {}
    for side in ("board", "plug"):
        face = spec["faces"][side]
        for value in face["stock"]:
            _positive(value, f"{side} stock dimension")
        if len(face["stock"]) != 3 or len(face["origin"]) != 3:
            raise ValueError("Stock dimensions and origin must contain three values")
        operations = [op for op in result["operations"] if op["face"] == side]
        if not operations:
            raise ValueError(f"No {side} operations were calculated")
        changes, previous = 0, None
        numbers = {}
        for op in operations:
            if op["id"] in seen:
                raise ValueError("Operation identifiers must be unique")
            if not set(op.get("predecessors", [])) <= set(ordered):
                raise ValueError("Operation order violates its machining prerequisites")
            seen.add(op["id"])
            ordered.append(op["id"])
            if op["tool_id"] not in selected:
                raise ValueError("An operation uses an unselected cutter")
            tool = tools[op["tool_id"]]
            if tool["shape"] not in ("flat", "v", "bull"):
                raise ValueError("Unsupported cutter shape")
            for key in ("diameter", "cutting_length"):
                _positive(tool[key], f"Cutter {key}")
            number = tool.get("number")
            if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 10000:
                raise ValueError("Native CAM tool numbers must be integers from 1 to 10000")
            if number in numbers and numbers[number] != tool["id"]:
                raise ValueError("Different physical cutters cannot share a tool number in a setup")
            numbers[number] = tool["id"]
            changes += previous is not None and previous != tool["id"]
            previous = tool["id"]
            _positive(op["settings"]["spindle"], "Spindle speed", zero=True)
            moves = op.get("moves", [])
            if not moves:
                raise ValueError("An operation has no motion")
            clearance = float(face["clearance_height"]) - float(face["origin"][2])
            if moves[0]["z"] < clearance - 1e-9 or moves[-1]["z"] < clearance - 1e-9:
                raise ValueError("Each operation must begin and end at clearance")
            for move in moves:
                if move.get("kind") not in ("feed", "rapid"):
                    raise ValueError("Only linear feed and rapid moves are supported")
                if move["kind"] == "feed":
                    _positive(move["feed_mm_min"], "Motion feed")
                for axis in ("x", "y", "z"):
                    value = move[axis]
                    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                        raise ValueError("Motion coordinates must be finite numbers")
                    resolution = spec["resolution"]["z" if axis == "z" else "xy"]
                    # FreeCAD 1.1 writes native Path data with six decimal places.
                    if abs(value - round(value, 6)) > resolution * 0.01 + 1e-12:
                        raise ValueError("Native CAM save precision is insufficient for this machine resolution")
        counts[side] = {"operations": len(operations), "tool_changes": int(changes)}
        if result.get("per_face_counts", {}).get(side) != counts[side]:
            raise ValueError(f"The reviewed {side} counts do not match its operations")
        for kind, actual in counts[side].items():
            limit = face.get("limits", {}).get(kind)
            if limit is not None and actual > limit:
                raise ValueError(f"The {side} {kind.replace('_', ' ')} limit is exceeded")
    if len(seen) != len(result["operations"]):
        raise ValueError("An operation refers to an unknown face")
    return counts


def _number(value):
    # Path.Command's parser does not understand scientific notation.
    return format(float(value), ".12f").rstrip("0").rstrip(".") or "0"


def motion_text(operation, clearance):
    """Serialize native CAM motion; F is FreeCAD mm/s, not posted mm/min."""
    first = operation["moves"][0]
    if first["z"] < clearance - 1e-9:
        raise ValueError("The operation does not start at its required clearance")
    lines = ["G90", "M3 S" + _number(operation["settings"]["spindle"])]
    # Establish Z before any XY motion from an unknown machine position.
    # Retain the solver's lattice-snapped Z rather than rounding it a second time.
    if first["kind"] == "rapid":
        lines.append("G0 Z" + _number(first["z"]))
    else:
        lines.append("G1 Z" + _number(first["z"]) + " F" + _number(first["feed_mm_min"] / 60))
    for move in operation["moves"]:
        line = "G0" if move["kind"] == "rapid" else "G1"
        line += " " + " ".join(axis.upper() + _number(move[axis]) for axis in ("x", "y", "z"))
        if move["kind"] == "feed":
            line += " F" + _number(move["feed_mm_min"] / 60)
        lines.append(line)
    return lines


def _tool_definition(snapshot):
    """Use builtin templates and copied values, never a personal shape URI."""
    shape = {"flat": "endmill", "v": "v-bit", "bull": "bullnose"}[snapshot["shape"]]
    parameters = {
        "Diameter": snapshot["diameter"],
        "CuttingEdgeHeight": snapshot["cutting_length"],
        "Length": max(snapshot.get("length", 0), snapshot["cutting_length"] + snapshot["diameter"]),
        "ShankDiameter": snapshot.get("shank_diameter", snapshot["diameter"]),
        # Flute count does not participate in the analytic cutter envelope.
        # Native V-bit templates omit it, but ToolBit requires an integer.
        "Flutes": 0,
    }
    if shape == "v-bit":
        parameters.update(CuttingEdgeAngle=snapshot["included_angle_deg"], TipDiameter=snapshot["tip_diameter"])
    elif shape == "bullnose":
        parameters["CornerRadius"] = snapshot["corner_radius"]
    original = snapshot.get("native_definition", {})
    if "Flutes" in original.get("parameter", {}):
        parameters["Flutes"] = original["parameter"]["Flutes"]
    shape_type = {"flat": "Endmill", "v": "VBit", "bull": "Bullnose"}[snapshot["shape"]]
    return {"name": snapshot["name"], "shape": shape + ".fcstd", "shape-type": shape_type,
            "parameter": parameters, "attribute": {}}


def apply_pair(doc, spec, result, fail_after_face=None):
    """Apply one portable pair with a single destination-document undo step.

    Native tool templates open documents and close FreeCAD transactions. Build
    them in a hidden staging document first; only the completed built-in graph
    is imported within the destination transaction.
    """
    validate_pair(spec, result)
    # Stage + destination + undo coexist during the atomic import. This
    # deliberately conservative allowance covers native objects and strings,
    # not merely the compact serialized coordinates. A measured resource audit
    # accompanies the release; exceeding this budget leaves the draft intact.
    import resource
    import sys
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss=int(rss if sys.platform=='darwin' else rss*1024)
    moves=sum(len(op.get('moves',[])) for op in result.get('operations',[]))
    predicted=rss+128*1024*1024+moves*8192
    if predicted>2*1024*1024*1024:
        raise MemoryError('Applying this pair would exceed the 2 GiB application budget. The draft is preserved; reduce design complexity or revise the selected tools and pass spacing.')
    import FreeCAD as App
    import FreeCADGui as Gui

    if App.ActiveDocument != doc:
        raise ValueError("The reviewed document is no longer active")
    if App.getActiveTransaction():
        raise ValueError("Finish the current document edit before applying inlay paths")
    if not doc.UndoMode:
        raise ValueError("Enable document undo before applying the paired paths")
    if any("stock_outline" in face and side not in spec.get("existing_faces", {})
           for side, face in spec["faces"].items()):
        raise ValueError("A selected stock outline has no native source; select the face and recalculate")
    originals = set(obj.Name for obj in doc.Objects)
    shapes = {}
    for side, existing in spec.get("existing_faces", {}).items():
        if existing.get("document_name") != doc.Name:
            raise ValueError("The selected stock belongs to a different document")
        source = doc.getObject(existing["object_name"])
        if source is None:
            raise ValueError("The selected stock object no longer exists")
        shape = _world_shape(source)
        if _shape_hash(shape) != existing["source_shape_hash"]:
            raise ValueError("The selected stock geometry changed; select the face and recalculate")
        for key in ("stock_outline", "stock_outline_tolerance"):
            if key not in existing or spec["faces"][side].get(key) != existing[key]:
                raise ValueError("The selected stock outline changed or is missing; select the face and recalculate")
        shape.Placement = App.Placement(_matrix(existing["face_to_world"])).inverse().multiply(shape.Placement)
        shape.translate(-App.Vector(*spec["faces"][side]["origin"]))
        shapes[side] = shape
    staging = App.newDocument("InlayStaging", hidden=True)
    staging.UndoMode = 0
    transaction = False
    try:
        staged = _build_pair(staging, spec, result, shapes, fail_after_face)
        App.setActiveDocument(doc.Name)
        App.setActiveTransaction("Apply board and plug inlay paths", True)
        transaction = True
        metadata = doc.copyObject(staging.getObject(staged["metadata"]), True)
        jobs = {side: job.Name for side, job in zip(("board", "plug"), metadata.Group)}
        jobs["metadata"] = metadata.Name
        doc.recompute()
        if fail_after_face == "import":
            raise RuntimeError("Injected paired Apply failure after import")
        if not App.getActiveTransaction():
            raise RuntimeError("FreeCAD unexpectedly closed the paired import transaction")
        App.closeActiveTransaction()
        transaction = False
        Gui.Selection.clearSelection()
        return jobs
    except BaseException:
        if transaction:
            App.closeActiveTransaction(True)
        if set(obj.Name for obj in doc.Objects) != originals:
            remaining = sorted(set(obj.Name for obj in doc.Objects) - originals)
            raise RuntimeError("FreeCAD did not restore the paired transaction completely: " + repr(remaining))
        raise
    finally:
        App.closeDocument(staging.Name)
        App.setActiveDocument(doc.Name)


def _build_pair(doc, spec, result, shapes, fail_after_face=None):
    """Build a complete native object graph away from the destination document."""
    import FreeCAD as App
    import FreeCADGui as Gui
    import Part
    import Path.Main.Job as Job
    import Path.Main.Stock as Stock
    import Path.Main.Gui.Job as JobGui
    import Path.Op.Custom as Custom
    import Path.Op.Gui.Custom as CustomGui
    import Path.Op.Gui.Base as OpGui
    import Path.Tool.Controller as Controller
    from Path.Tool.toolbit import ToolBit

    originals = set(obj.Name for obj in doc.Objects)
    tools = {tool["id"]: tool for tool in spec["tools"]}
    jobs = {}
    for side in ("board", "plug"):
        face = spec["faces"][side]
        origin = App.Vector(*face["origin"])
        stock = doc.addObject("Part::Feature", side.title() + "InlayStock")
        if side in shapes:
            stock.Shape = shapes[side]
        else:
            width, height, thickness = face["stock"]
            stock.Shape = Part.makeBox(width, height, thickness, App.Vector(-origin.x, -origin.y, -thickness - origin.z))
        stock.Label = f"{side.title()} inlay stock"
        doc.recompute()
        job = Job.Create(side.title() + "InlayJob", [stock])
        job.ViewObject.Proxy = JobGui.ViewProvider(job.ViewObject)
        job.ViewObject.addExtension("Gui::ViewProviderGroupExtensionPython")
        job.ViewObject.Proxy.deleteOnReject = False
        # Use CAM's native "existing stock" flow: default FromBase stock is
        # merely an enclosing box and would erase concavities and through holes.
        default_stock = job.Stock
        exact_stock = Job.createResourceClone(job, stock, "Stock", "Stock")
        Stock.SetupStockObject(exact_stock, Stock.StockType.Unknown)
        exact_stock.Proxy.execute(exact_stock)
        job.Stock = exact_stock
        doc.removeObject(default_stock.Name)
        job.Label = f"{side.title()} — inlay"
        job.Description = "Stored inlay motion. Edit with native Custom; controller feed changes do not rewrite F words. Export precision and path-control mode require CAM review."
        job.PostProcessor = "linuxcnc"
        job.PostProcessorArgs = '--precision 9 --preamble "G17 G54 G40 G49 G80 G90 G61"'
        job.OrderOutputBy = "Fixture"
        job.Fixtures = ["G54"]
        job.SplitOutput = False
        controllers = {}
        operations = [op for op in result["operations"] if op["face"] == side]
        # Reuse the first default controller to avoid unused library tools.
        default_tc = job.Tools.Group[0] if job.Tools.Group else None
        for planned in operations:
            tool_id = planned["tool_id"]
            if tool_id not in controllers:
                tool = tools[tool_id]
                bit = ToolBit.from_dict(_tool_definition(tool))
                native_tool = bit.attach_to_doc(doc)
                native_tool.ViewObject.Visibility = False
                if default_tc is not None:
                    tc = default_tc
                    default_tc = None
                    old_tool = tc.Tool
                    tc.Tool = native_tool
                    tc.ToolNumber = tool["number"]
                    if old_tool and old_tool.Name not in originals:
                        body = getattr(old_tool, "BitBody", None)
                        if body:
                            body.removeObjectsFromDocument()
                            doc.removeObject(body.Name)
                        doc.removeObject(old_tool.Name)
                else:
                    tc = Controller.Create("TC_" + side + "_" + str(tool["number"]), native_tool, tool["number"])
                    job.Proxy.addToolController(tc)
                tc.Label = f"T{tool['number']} — {tool['name']}"
                tc.HorizFeed = float(tool["feed"]) / 60
                tc.VertFeed = float(tool["plunge_feed"]) / 60
                for key in ("HorizRapid", "VertRapid"):
                    tc.setExpression(key, None)
                    setattr(tc, key, float(tool["travel_feed"]) / 60)
                tc.SpindleSpeed = float(planned["settings"]["spindle"])
                controllers[tool_id] = tc
            operation = Custom.Create(side.title() + "InlayPath", parentJob=job)
            operation.ToolController = controllers[tool_id]
            operation.Label = f"{side.title()} — {planned['purpose']} — {tools[tool_id]['name']}"
            operation.Source = "Text"
            operation.Gcode = motion_text(planned, face["clearance_height"] - face["origin"][2])
            operation.ViewObject.Proxy = OpGui.ViewProvider(operation.ViewObject, CustomGui.Command.res)
            operation.ViewObject.Proxy.setDeleteObjectsOnReject(False)
            operation.addProperty("App::PropertyString", "InlayOperationId", "Inlay")
            operation.InlayOperationId = planned["id"]
            operation.addProperty("App::PropertyString", "InlayInputHash", "Inlay")
            operation.InlayInputHash = result["input_hash"]
            operation.addProperty("App::PropertyString", "InlaySettings", "Inlay")
            operation.InlaySettings = _json(planned["settings"])
            operation.addProperty("App::PropertyString", "InlayEstimate", "Inlay")
            operation.InlayEstimate = _json(planned["time"])
        doc.recompute()
        if any(not operation.Path.Commands for operation in job.Operations.Group):
            raise RuntimeError("FreeCAD failed to create a native machining path")
        stock.ViewObject.Visibility = False
        jobs[side] = job.Name
        if fail_after_face == side:
            raise RuntimeError("Injected paired Apply failure after " + side)
    metadata = doc.addObject("App::DocumentObjectGroup", "InlayPlan")
    metadata.Label = "Inlay planning data"
    for name in ("SchemaVersion", "InputHash", "InputJSON", "ResultSummary", "NativeMotionHash"):
        metadata.addProperty("App::PropertyString", name, "Inlay")
    metadata.SchemaVersion = "1"
    metadata.InputHash = result["input_hash"]
    metadata.InputJSON = _json(spec)
    metadata.ResultSummary = _json({key: result[key] for key in ("revision", "derived", "per_face_counts", "verification")})
    metadata.NativeMotionHash = _motion_hash(doc, jobs)
    metadata.Group = [doc.getObject(jobs[side]) for side in ("board", "plug")]
    jobs["metadata"] = metadata.Name
    doc.recompute()
    return jobs

def _motion_hash(doc, jobs):
    content = {side: [list(op.Gcode) for op in doc.getObject(jobs[side]).Operations.Group] for side in ("board", "plug")}
    return hashlib.sha256(_json(content).encode()).hexdigest()


def list_tools():
    """Read the current native libraries; no file or library edits are made."""
    from Path.Tool.camassets import cam_assets

    records = {}
    for store in ("local", "builtin"):
        for bit in cam_assets.fetch(asset_type="toolbit", store=store, depth=0):
            identity = str(bit.get_id())
            if identity in records:
                continue
            definition = bit.to_dict()
            obj = bit.obj
            shape_name = str(getattr(obj, "ShapeType", definition.get("shape-type", ""))).lower()
            shape = {"endmill": "flat", "vbit": "v", "v-bit": "v", "bullnose": "bull"}.get(shape_name)
            def quantity(name):
                value = getattr(obj, name, None)
                return float(value.Value if hasattr(value, "Value") else value) if value is not None else None
            record = {"id": identity, "name": bit.label, "shape": shape or shape_name, "supported": shape is not None,
                      "library": [], "number": None, "native_definition": definition, "asset_uri": str(bit.get_uri()), "store": store}
            for name, field in (("Diameter", "diameter"), ("CuttingEdgeHeight", "cutting_length"), ("Length", "length"),
                                ("ShankDiameter", "shank_diameter"), ("CornerRadius", "corner_radius"),
                                ("CuttingEdgeAngle", "included_angle_deg"), ("TipDiameter", "tip_diameter")):
                value = quantity(name)
                if value is not None:
                    record[field] = value
            if shape is None:
                record["unsupported_reason"] = "This inlay workflow supports flat end mills, V-bits, and bull-nose cutters"
            records[identity] = record
        for library in cam_assets.fetch(asset_type="toolbitlibrary", store=store, depth=1):
            for bit in library.get_bits():
                record = records.get(str(bit.get_id()))
                if record:
                    label = str(library.label)
                    if label not in record["library"]:
                        record["library"].append(label)
                    if record["number"] is None:
                        record["number"] = library.get_bit_no_from_bit(bit)
    return sorted(records.values(), key=lambda tool: (not tool["supported"], tool["name"], tool["id"]))


def _world_shape(obj):
    shape = obj.Shape.copy()
    shape.Placement = obj.getGlobalPlacement()
    return shape


def _shape_hash(shape):
    return hashlib.sha256(shape.exportBrepToString().encode()).hexdigest()


def _matrix(values):
    import FreeCAD as App
    return App.Matrix(*[float(value) for row in values for value in row])


def _planar_outline(face, tolerance):
    """Polygonize one local XY face and prove its inward/outward enclosure."""
    import FreeCAD as App
    import Part

    straight = all(isinstance(edge.Curve, (Part.Line, Part.LineSegment)) for edge in face.Edges)
    wires = [face.OuterWire] + [wire for wire in face.Wires if not wire.isSame(face.OuterWire)]
    rings = []
    for wire in wires:
        points = ([vertex.Point for vertex in wire.OrderedVertexes] if straight
                  else wire.discretize(Deflection=tolerance / 4))
        ring = []
        for point in points:
            xy = [float(point.x), float(point.y)]
            if not ring or math.dist(xy, ring[-1]) > 1e-10:
                ring.append(xy)
        if len(ring) > 1 and math.dist(ring[0], ring[-1]) < 1e-9:
            ring.pop()
        if len(ring) < 3:
            raise ValueError("The selected face has a degenerate boundary")
        rings.append(ring)
    if sum(map(len, rings)) > 20000:
        raise ValueError("The selected face needs too many boundary segments at this tolerance; increase the capture tolerance")

    def ring_face(ring):
        points = [App.Vector(x, y, 0) for x, y in ring]
        return Part.Face(Part.makePolygon(points + [points[0]]))

    polygon = ring_face(rings[0])
    for hole in rings[1:]:
        polygon = polygon.cut(ring_face(hole))
    if not polygon.isValid() or len(polygon.Faces) != 1:
        raise ValueError("The selected face's polygonized boundary is invalid or ambiguous")
    polygon = polygon.Faces[0]
    if polygon.normalAt(0, 0).z < 0:
        polygon.reverse()
    if straight:
        inner, outer = polygon, polygon
        allowance = 0.0
    else:
        try:
            inner = polygon.makeOffset2D(-tolerance, join=0, fill=False, intersection=True)
            outer = polygon.makeOffset2D(tolerance, join=0, fill=False, intersection=True)
        except Exception as error:
            raise ValueError("The curved stock boundary could not be enclosed at this tolerance") from error
        allowance = float(tolerance)
    inner_excess = inner.cut(face).Area if not inner.isNull() else 0.0
    outer_missing = face.cut(outer).Area
    # This is an OCC boolean emptiness proof, not a sampled-distance estimate.
    if inner_excess > 1e-9 or outer_missing > 1e-9:
        raise ValueError("The stock boundary approximation could not be certified; use a finer capture tolerance")
    return [{"outer": rings[0], "holes": rings[1:]}], allowance, {
        "method": "OCC polygon inward/outward offset containment",
        "inner_excess_area_mm2": inner_excess, "outer_missing_area_mm2": outer_missing,
        "segments": sum(map(len, rings)), "straight_boundary": straight}


def capture_face(selection, tolerance=0.001):
    """Capture a planar face of one constant-section solid in its setup frame.

    The source solid is retained exactly. Curved boundaries carry an explicit
    certified polygon enclosure tolerance; hidden recesses and varying sections
    are rejected instead of becoming rectangular or constant-section stock.
    """
    import FreeCAD as App
    import Part

    tolerance = _positive(tolerance, "Stock outline capture tolerance")
    if tolerance < 1e-6:
        raise ValueError("Stock outline capture below 0.000001 mm cannot be certified by the CAD kernel")
    if isinstance(selection, (list, tuple)):
        if len(selection) != 1:
            raise ValueError("Select exactly one planar stock face")
        selection = selection[0]
    obj = selection.Object
    names = list(selection.SubElementNames)
    if len(names) != 1 or not names[0].startswith("Face") or not names[0][4:].isdigit():
        raise ValueError("Select one face, not the whole object or an edge")
    if not hasattr(obj, "Shape"):
        raise ValueError("The selected object has no solid geometry")
    shape = _world_shape(obj)
    index = int(names[0][4:]) - 1
    if not 0 <= index < len(shape.Faces):
        raise ValueError("The selected stock face no longer exists")
    face = shape.Faces[index]
    if not isinstance(face.Surface, Part.Plane):
        raise ValueError("The selected face is not planar")
    if len(shape.Solids) != 1 or not shape.isValid() or not face.isValid():
        raise ValueError("Select the outside face of one valid solid stock object")
    normal = face.normalAt(0, 0)
    normal.normalize()
    # Keep rectangular setups' familiar first-edge orientation; curved outlines
    # instead use the plane's X direction, projected into its actual orientation.
    u = None
    for edge in face.OuterWire.Edges:
        if isinstance(edge.Curve, (Part.Line, Part.LineSegment)) and len(edge.Vertexes) == 2:
            direction = edge.Vertexes[-1].Point - edge.Vertexes[0].Point
            if direction.Length > 1e-9:
                u = direction
                break
    if u is None:
        u = face.tangentAt(0, 0)[0]
    u = u - normal * u.dot(normal)
    u.normalize()
    v = normal.cross(u)
    v.normalize()
    frame = [[u.x, v.x, normal.x, 0], [u.y, v.y, normal.y, 0],
             [u.z, v.z, normal.z, 0], [0, 0, 0, 1]]
    rotation = App.Placement(_matrix(frame)).inverse()
    aligned = face.copy()
    aligned.Placement = rotation.multiply(aligned.Placement)
    bb = aligned.BoundBox
    width, height = bb.XLength, bb.YLength
    if width <= 1e-7 or height <= 1e-7 or bb.ZLength > 1e-6:
        raise ValueError("The selected face has no certifiable planar stock extent")
    plane_z = aligned.Vertexes[0].Point.z
    zero = u * bb.XMin + v * bb.YMin + normal * plane_z
    for row, value in zip(frame[:3], (zero.x, zero.y, zero.z)):
        row[3] = value
    inverse = App.Placement(_matrix(frame)).inverse()
    local_shape = shape.copy()
    local_shape.Placement = inverse.multiply(local_shape.Placement)
    local_face = face.copy()
    local_face.Placement = inverse.multiply(local_face.Placement)
    thickness = -local_shape.BoundBox.ZMin
    if thickness <= 1e-7 or local_shape.BoundBox.ZMax > 1e-6:
        raise ValueError("The selected face must be the outside of the stock")
    prism = local_face.extrude(App.Vector(0, 0, -thickness))
    missing = prism.cut(local_shape)
    extra = local_shape.cut(prism)
    if missing.Solids or extra.Solids or abs(missing.Volume) > 1e-9 or abs(extra.Volume) > 1e-9:
        raise ValueError("The selected stock is not a constant-section prism; changing sections or hidden recesses are uncertified")
    outline, allowance, evidence = _planar_outline(local_face, tolerance)
    evidence.update(prism_missing_volume_mm3=abs(missing.Volume), prism_extra_volume_mm3=abs(extra.Volume))
    return {"document_name": obj.Document.Name, "object_name": obj.Name, "face_name": names[0],
            "source_shape_hash": _shape_hash(shape), "face_to_world": frame,
            "stock": [width, height, thickness], "bounds": [0, 0, width, height], "origin": [0, 0, 0],
            "stock_outline": outline, "stock_outline_tolerance": allowance, "stock_capture_evidence": evidence}
