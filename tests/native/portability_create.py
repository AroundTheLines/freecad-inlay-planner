"""First process: native output, atomicity, face capture, tool snapshots."""
import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import FreeCAD as App
import FreeCADGui as Gui
import Part

from native_support import check, file_hashes, paths_snapshot, stock_snapshot, assert_native


def bind_synthetic_motion(result):
    """Adapter integrity fixture only; no geometric qualification is claimed."""
    result["verification"]["motion_hash"] = hashlib.sha256(json.dumps(
        result["operations"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def fixture():
    from inlay_planner.schema import default_spec, seal_spec
    spec = default_spec()
    spec["revision"] = 7
    spec["source"] = {"text": '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>', "filename": "missing.svg"}
    tools = [dict(id="flat", number=1, name="1/8 inch endmill", shape="flat", diameter=3.175, cutting_length=12),
             dict(id="v", number=2, name="60 degree V", shape="v", diameter=12.7, cutting_length=10, included_angle_deg=60, tip_diameter=.1),
             dict(id="bull", number=3, name="6mm bull", shape="bull", diameter=6, cutting_length=12, corner_radius=1)]
    for tool in tools:
        tool.update(max_stepdown=.5, stepover=.5, feed=600, plunge_feed=120, travel_feed=1800, spindle=12000)
    spec["tools"] = tools
    spec["selected_tool_ids"] = [tool["id"] for tool in tools]
    spec = seal_spec(spec)
    operations = []
    for side, sequence in (("board", ["flat", "flat", "v", "flat"]), ("plug", ["bull"])):
        for index, tool in enumerate(sequence):
            feed = 600 + index * 60
            spindle = 12000 + index * 1000
            operations.append({"id": f"{side}_{index}", "face": side, "tool_id": tool, "purpose": "portability proof",
                               "predecessors": [f"{side}_{index - 1}"] if index else [],
                               "settings": dict(feed=feed, plunge_feed=120, travel_feed=1800, spindle=spindle, max_stepdown=.5, stepover=.5),
                               "moves": [dict(kind="rapid", role="travel", x=10, y=10, z=5, feed_mm_min=1800),
                                         dict(kind="feed", role="plunge", x=10, y=10, z=-.5, feed_mm_min=120),
                                         dict(kind="feed", role="cut", x=20.123456789, y=10, z=-.5, feed_mm_min=feed),
                                         dict(kind="rapid", role="travel", x=20.123456789, y=10, z=5, feed_mm_min=1800)],
                               "time": dict(cut_s=1.0, travel_s=.2, baseline_s=1.2, optimized_s=1.2)})
    result = {"schema_version": 1, "revision": spec["revision"], "input_hash": spec["input_hash"], "status": "ready",
              "operations": operations, "derived": dict(depth=3, height=3.8, seating_depth=2.8),
              "per_face_counts": {"board": {"operations": 4, "tool_changes": 2}, "plug": {"operations": 1, "tool_changes": 0}},
              "verification": {"status": "passed", "method": "synthetic adapter fixture (not geometry qualification)"}, "diagnostics": [], "proposal": None}
    bind_synthetic_motion(result)
    return spec, result


def run(out):
    sys.path.insert(0, os.environ["INLAY_NATIVE_REPO"])
    from inlay_planner import native
    from inlay_planner.schema import seal_spec
    checks = []
    from Path import Preferences
    library_path = Preferences.getAssetPath()
    personal = {"version": 2, "name": "Portability 1/8 inch endmill", "shape": "endmill.fcstd", "shape-type": "Endmill",
                "parameter": {"Diameter": "0.125 in", "CuttingEdgeHeight": "12 mm", "Length": "30 mm", "ShankDiameter": "3.175 mm"},
                "attribute": {}}
    personal_path = Preferences.getToolBitPath() / "Portability.fctb"
    personal_path.write_text(json.dumps(personal))
    personal_library = library_path / "Tools" / "Library" / "Portability.fctl"
    personal_library.write_text(json.dumps({"version": 1, "label": "Portability fixture", "tools": [{"nr": 27, "path": "Portability.fctb"}]}))
    before_library = file_hashes(library_path)
    tools = native.list_tools()
    check(tools, "No native tools were read")
    check({"flat", "v", "bull"} <= {tool["shape"] for tool in tools}, "Required builtin shapes not found")
    check(any(not tool["supported"] for tool in tools), "Unsupported tools should remain visible")
    personal_tool = next(tool for tool in tools if tool["name"] == personal["name"])
    check(personal_tool["library"] == ["Portability fixture"] and personal_tool["number"] == 27, "Native tool library identity/number were lost")
    check(abs(personal_tool["diameter"] - 3.175) < 1e-12, "Imperial library dimension was not converted to mm")
    check(file_hashes(library_path) == before_library, "Reading tools changed library files")
    checks.append("native_library_readonly_supported_and_unsupported")
    doc = App.newDocument("NativeInlayPortability")
    doc.UndoMode = 1
    unrelated = doc.addObject("Part::Box", "Unrelated")
    unrelated.Length, unrelated.Width, unrelated.Height = 47, 31, 8
    unrelated.Placement = App.Placement(App.Vector(7, 8, 9), App.Rotation(App.Vector(1, 2, 3), 31))
    doc.recompute()
    captured = native.capture_face(SimpleNamespace(Object=unrelated, SubElementNames=["Face6"]))
    check(abs(captured["stock"][0] * captured["stock"][1] * captured["stock"][2] - unrelated.Shape.Volume) < 1e-6, "Rotated setup volume changed")
    shape = unrelated.Shape.copy()
    shape = native._world_shape(unrelated)
    shape.Placement = App.Placement(native._matrix(captured["face_to_world"])).inverse().multiply(shape.Placement)
    check(abs(shape.BoundBox.ZMax) < 1e-7 and abs(shape.BoundBox.ZMin + captured["stock"][2]) < 1e-7, "Captured setup orientation is wrong")
    checks.append("rotated_planar_prism_capture")
    cylinder = doc.addObject("Part::Feature", "Cylinder")
    cylinder.Shape = Part.makeCylinder(4, 6)
    doc.recompute()
    try:
        native.capture_face(SimpleNamespace(Object=cylinder, SubElementNames=["Face1"]))
    except ValueError:
        pass
    else:
        raise AssertionError("Curved stock face silently approximated")
    checks.append("curved_face_rejected")
    from prism_capture import run as capture_prisms
    generic_records, capture_evidence = capture_prisms(doc, native)
    checks.extend(["rotated_concave_holed_prism", "curved_outline_offset_enclosures", "taper_and_hidden_relief_rejected"])
    spec, result = fixture()
    spec["tools"][0]["native_definition"] = personal_tool["native_definition"]
    spec = seal_spec(spec)
    result["input_hash"] = spec["input_hash"]
    originals = set(obj.Name for obj in doc.Objects)
    existing_spec, existing_result = copy.deepcopy(spec), copy.deepcopy(result)
    existing_spec["existing_faces"] = {"board": captured}
    existing_spec["resolution"]["z"] = .005
    existing_spec["faces"]["board"].update(stock=captured["stock"], bounds=captured["bounds"], origin=[.123, .127, -.025])
    for key in ("stock_outline", "stock_outline_tolerance"):
        existing_spec["faces"]["board"][key] = captured[key]
    for operation in existing_result["operations"]:
        if operation["face"] == "board":
            for move in operation["moves"]:
                move["x"] = round((move["x"] - .123) / .01) * .01
                move["y"] = round((move["y"] - .127) / .01) * .01
                move["z"] += .025
    bind_synthetic_motion(existing_result)
    existing_spec = seal_spec(existing_spec)
    existing_result["input_hash"] = existing_spec["input_hash"]
    original_length = unrelated.Length.Value
    unrelated.Length = original_length + 1
    doc.recompute()
    try:
        native.apply_pair(doc, existing_spec, existing_result)
    except ValueError as error:
        check("geometry changed" in str(error), "Wrong stale face diagnostic")
    else:
        raise AssertionError("Changed selected geometry was applied")
    unrelated.Length = original_length
    doc.recompute()
    # Capture again after restoring because BRep serialization can record topology history.
    existing_spec["existing_faces"]["board"] = native.capture_face(SimpleNamespace(Object=unrelated, SubElementNames=["Face6"]))
    for key in ("stock_outline", "stock_outline_tolerance"):
        existing_spec["faces"]["board"][key] = existing_spec["existing_faces"]["board"][key]
    existing_spec = seal_spec(existing_spec)
    existing_result["input_hash"] = existing_spec["input_hash"]
    source_hash = native._shape_hash(native._world_shape(unrelated))
    mapped_jobs = native.apply_pair(doc, existing_spec, existing_result)
    board_job = doc.getObject(mapped_jobs["board"])
    mapped_stock = board_job.Proxy.baseObjects(board_job)[0].Shape
    check(abs(mapped_stock.BoundBox.ZMax - .025) < 1e-7 and abs(mapped_stock.BoundBox.ZMin + 7.975) < 1e-7, "Selected source was not mapped relative to the Z origin")
    check(abs(mapped_stock.BoundBox.XMin + .123) < 1e-7 and abs(mapped_stock.BoundBox.YMin + .127) < 1e-7, "Chosen origin was not applied to selected stock")
    mapped_motion = [c.Parameters for c in board_job.Operations.Group[0].Path.Commands if c.Name in ("G0", "G1")]
    check(mapped_motion[0] == {"Z": 5.025}, "Clearance Z was transformed twice")
    check(abs(mapped_motion[1]["X"] - 9.88) < 1e-10 and abs(mapped_motion[1]["Y"] - 9.87) < 1e-10, "Motion origin was transformed twice")
    check(abs(mapped_stock.Volume - unrelated.Shape.Volume) < 1e-6, "Existing stock changed volume")
    check(native._shape_hash(native._world_shape(unrelated)) == source_hash, "Existing stock was modified")
    doc.undo()
    check(set(obj.Name for obj in doc.Objects) == originals, "Selected-face Apply did not undo completely")
    checks.extend(["stale_selected_geometry_rejected", "exact_existing_face_apply_and_origin"])
    stale = copy.deepcopy(result)
    stale["input_hash"] = "stale"
    try:
        native.apply_pair(doc, spec, stale)
    except ValueError:
        pass
    else:
        raise AssertionError("Stale result applied")
    check(set(obj.Name for obj in doc.Objects) == originals, "Stale attempt changed document")
    checks.append("stale_apply_no_mutation")
    altered = copy.deepcopy(result)
    altered["operations"][0]["moves"][2]["x"] += .05
    try:
        native.apply_pair(doc, spec, altered)
    except ValueError as error:
        check("certificate" in str(error), "Altered motion produced the wrong diagnostic")
    else:
        raise AssertionError("Altered verified motion was applied")
    check(set(obj.Name for obj in doc.Objects) == originals, "Altered motion changed the document")
    checks.append("motion_certificate_rejection_no_mutation")
    for failure in ("board", "import"):
        try:
            native.apply_pair(doc, spec, result, fail_after_face=failure)
        except RuntimeError as error:
            check("Injected" in str(error), "Rollback failed: " + str(error))
        else:
            raise AssertionError("Failure injection did not fire")
        check(set(obj.Name for obj in doc.Objects) == originals, "Partial pair survived rollback")
    checks.append("atomic_partial_apply_rollback")
    spec["existing_faces"] = {"board": generic_records["ConcaveHoledStock"], "plug": generic_records["CircularHoledStock"]}
    for side, record in spec["existing_faces"].items():
        for key in ("stock", "bounds", "stock_outline", "stock_outline_tolerance"):
            spec["faces"][side][key] = copy.deepcopy(record[key])
    spec = seal_spec(spec)
    result["input_hash"] = spec["input_hash"]
    orphan_outline = copy.deepcopy(spec)
    orphan_outline.pop("existing_faces")
    orphan_outline = seal_spec(orphan_outline)
    orphan_outline_result = copy.deepcopy(result)
    orphan_outline_result["input_hash"] = orphan_outline["input_hash"]
    try:
        native.apply_pair(doc, orphan_outline, orphan_outline_result)
    except ValueError as error:
        check("no native source" in str(error), "Orphan stock mask returned the wrong diagnostic")
    else:
        raise AssertionError("A stock outline without native source became a rectangular slab")
    check(set(obj.Name for obj in doc.Objects) == originals, "Orphan stock mask mutated the document")
    checks.append("orphan_selected_outline_rejected")
    missing_outline = copy.deepcopy(spec)
    missing_outline["faces"]["board"].pop("stock_outline")
    missing_outline = seal_spec(missing_outline)
    missing_outline_result = copy.deepcopy(result)
    missing_outline_result["input_hash"] = missing_outline["input_hash"]
    try:
        native.apply_pair(doc, missing_outline, missing_outline_result)
    except ValueError as error:
        check("stock outline" in str(error), "Missing selected-stock mask returned the wrong diagnostic")
    else:
        raise AssertionError("Selected stock was accepted without its actual outline")
    check(set(obj.Name for obj in doc.Objects) == originals, "Missing stock mask mutated the document")
    checks.append("missing_selected_outline_rejected")
    jobs = native.apply_pair(doc, spec, result)
    check(len(doc.getObject(jobs["board"]).Operations.Group) == 4, "Board native count differs")
    check(len(doc.getObject(jobs["plug"]).Operations.Group) == 1, "Plug native count differs")
    snapshot = paths_snapshot(doc, jobs)
    stocks = stock_snapshot(doc, jobs)
    check(stocks["board"]["top_wires"] == 2 and stocks["plug"]["top_wires"] == 2, "Native stock holes were lost")
    checks.append("exact_native_stock_and_holes")
    for side, operations in snapshot.items():
        for operation in operations:
            motion = [cmd for cmd in operation["commands"] if cmd["name"] in ("G0", "G1")]
            check(set(motion[0]["parameters"]) <= {"Z", "F"}, "First native move can move XY before clearance")
    proxies = assert_native(doc)
    checks.append("native_pair_embedded_builtin_objects")
    doc.undo()
    check(set(obj.Name for obj in doc.Objects) == originals, "One Undo did not remove entire pair")
    doc.redo()
    doc.recompute()
    check(paths_snapshot(doc, jobs) == snapshot, "Redo altered motion")
    checks.append("single_undo_redo_pair")
    Gui.activeDocument().activeView().viewAxonometric()
    Gui.activeDocument().activeView().fitAll()
    doc.saveAs(str(out / "portable.FCStd"))
    (out / "expected.json").write_text(json.dumps({"jobs": jobs, "spec": spec, "result": result, "snapshot": snapshot, "stocks": stocks}, indent=2))
    (out / "source.svg").write_text(spec["source"]["text"])
    return {"checks": checks, "proxies": proxies, "tool_count": len(tools), "jobs": jobs,
            "stock_capture_evidence": capture_evidence, "artifact": str(out / "portable.FCStd")}
