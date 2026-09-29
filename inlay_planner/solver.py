"""Local paired-inlay planner.  The only entry point is :func:`solve`."""
from __future__ import annotations

import hashlib
import math
import resource
import time

from shapely.geometry import GeometryCollection, Point, box
from shapely.errors import GEOSException
from shapely.ops import unary_union

from .geometry import (Target, from_records, offset, polygons, preview_mesh, records,
                       stock_enclosure, stock_outline, transform)
from .motion import coverage, emit_layers, timing
from .schema import finite_tree, fingerprint, hash_json
from .verify import diagnostic, verify_layer_engagement, verify_plan


class PlanningLimit(Exception):
    pass


def _validate(spec):
    finite_tree(spec)
    if spec.get("schema_version") != 1:
        raise ValueError("Unsupported planning schema version")
    if spec.get("input_hash") and spec["input_hash"] != fingerprint(spec):
        raise ValueError("Input hash does not match the current settings")
    if spec["style"] not in ("flat", "v", "bull"):
        raise ValueError("Choose flat, V or bull-nose inlay style")
    for value in spec["resolution"].values():
        if value <= 0:
            raise ValueError("Machine resolution must be positive")
    for name in ("board", "plug"):
        face = spec["faces"][name]
        if len(face["stock"]) != 3 or min(face["stock"]) <= 0:
            raise ValueError("Stock dimensions must be positive")
        stock_outline(face)
        if face.get("stock_outline_tolerance", 0.) < 0:
            raise ValueError("Stock outline tolerance cannot be negative")
        a, b, c, d, _, _ = face["design_to_face"]
        if max(abs(a*a+b*b-1), abs(c*c+d*d-1), abs(a*c+b*d)) > 1e-7:
            raise ValueError("Face transforms must preserve physical scale; scale the artwork instead")
        bounds = face["bounds"]
        if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            raise ValueError("Face bounds must have positive width and height")
        if face["clearance_height"] <= 0:
            raise ValueError("Travel clearance must be above the stock")
        for kind, count in face.get("limits", {}).items():
            if count is not None and (not isinstance(count, int) or count < (1 if kind == "operations" else 0)):
                raise ValueError("Operation/tool-change limits must be valid whole numbers")
    bm = spec["faces"]["board"]["design_to_face"]
    pm = spec["faces"]["plug"]["design_to_face"]
    if (bm[0]*bm[3]-bm[1]*bm[2])*(pm[0]*pm[3]-pm[1]*pm[2]) >= 0:
        raise ValueError("The matching plug needs a physical face flip: board and plug planar transforms must have opposite handedness")
    fit = spec["fit"]
    if fit["driver"] not in ("depth", "height") or fit["value"] <= 0:
        raise ValueError("Choose a positive driving depth or plug height")
    if fit["glue_gap"] < 0 or fit["backing_clearance"] < 0:
        raise ValueError("Bottom glue gap and backing clearance cannot be negative")
    tools = {tool["id"]: tool for tool in spec["tools"]}
    if len(tools) != len(spec["tools"]):
        raise ValueError("Physical cutter identifiers must be unique")
    selected = []
    for tool_id in spec["selected_tool_ids"]:
        if tool_id not in tools:
            raise ValueError("A selected cutter is missing from the tool snapshot")
        if tool_id in {t["id"] for t in selected}:
            continue
        tool = tools[tool_id]
        if not isinstance(tool.get("number"), int) or tool["number"] <= 0:
            raise ValueError(f"Tool {tool_id} needs a positive integer tool number")
        for key in ("diameter", "cutting_length", "max_stepdown", "stepover", "feed", "plunge_feed", "travel_feed", "spindle"):
            if tool.get(key, 0) <= 0:
                raise ValueError(f"Tool {tool_id} needs a positive {key}")
        if tool["shape"] not in ("flat", "v", "bull"):
            raise ValueError(f"Tool {tool_id} has an unsupported cutter shape")
        if tool["shape"] == "bull" and not 0 < tool.get("corner_radius", 0) < tool["diameter"]/2:
            raise ValueError("A bull nose needs a corner radius smaller than its outer radius; ball noses are unsupported")
        if tool["shape"] == "v" and ("tip_diameter" not in tool or not 0 < tool.get("included_angle_deg", 0) < 180 or
                                      not 0 <= tool.get("tip_diameter", 0) < tool["diameter"]):
            raise ValueError("A V-bit needs a valid included angle and actual tip diameter")
        selected.append(tool)
    finishers = [t for t in selected if t["shape"] == spec["style"]]
    if not finishers:
        raise ValueError("Select a finishing cutter matching the chosen inlay style")
    finisher = min(finishers, key=lambda t: (t["diameter"], t["id"]))
    return selected, finisher


def _depths(total, maximum, quantum, origin_z=0.):
    step = math.floor(maximum/quantum+1e-9)*quantum
    if step < quantum/2:
        raise ValueError("Maximum stepdown is smaller than the Z resolution")
    if math.ceil(total/step) > 10000:
        raise MemoryError("The required depth layers exceed the bounded calculation budget; the requested resolution has not been relaxed")
    depths = []
    previous = 0.
    while previous < total-1e-9:
        next_depth = math.floor((previous+maximum+origin_z)/quantum+1e-8)*quantum-origin_z
        next_depth = round(min(total, next_depth), 12)
        if next_depth <= previous+1e-10:
            raise ValueError("No safe depth increment can be represented at this origin and Z resolution")
        depths.append(next_depth)
        previous = next_depth
    return depths


def solve(spec, progress=None, cancelled=None):
    progress = progress or (lambda _: None)
    cancelled = cancelled or (lambda: False)
    started = time.monotonic()
    result = {"schema_version": 1, "input_hash": spec.get("input_hash", ""),
              "revision": spec.get("revision", 0), "status": "blocked", "derived": {},
              "operations": [], "per_face_counts": {}, "diagnostics": [], "proposal": None,
              "preview": {"source_polygons": [], "retained_polygons": [], "cuttable_polygons": [],
                          "blocked_polygons": [], "excluded_polygons": [], "board": {"vertices": [], "faces": []},
                          "plug": {"vertices": [], "faces": []}, "sections": []},
              "verification": {"status": "not_run", "checks": []}, "resource_stats": {}}

    def check():
        if cancelled():
            raise InterruptedError()

    try:
        selected, finisher = _validate(spec)
        result["input_hash"] = spec.get("input_hash") or fingerprint(spec)
        qxy, qz = spec["resolution"]["xy"], spec["resolution"]["z"]
        error = min(qxy, qz)/32
        fit = spec["fit"]
        g, b, side = fit["glue_gap"], fit["backing_clearance"], fit["side_clearance"]
        D = fit["value"] if fit["driver"] == "depth" else fit["value"]+g-b
        H = D-g+b
        if min(D, H, D-g) <= 0:
            raise ValueError("The depth and fit settings leave no positive mating height")
        result["derived"] = {"depth": D, "height": H, "seating_depth": D-g}
        for name, total in (("board", D), ("plug", H)):
            if total >= spec["faces"][name]["stock"][2]:
                raise ValueError(f"The {name} machining depth would remove its entire stock thickness")
            oz = spec["faces"][name]["origin"][2]
            if abs((total+oz)/qz-round((total+oz)/qz)) > 1e-7:
                raise ValueError(f"The {name} final depth is not representable at the chosen Z resolution and origin")
        if side < 0:
            raise ValueError("Negative side clearance requests rigid interference; wood compression is not modeled")
        regions = spec["artwork"]["regions"]
        all_art = from_records([p for r in regions for p in r["polygons"]])
        included = from_records([p for r in regions if r["id"] not in spec.get("excluded_region_ids", []) for p in r["polygons"]])
        included = included.difference(from_records(spec.get("exclusions", [])))
        result["preview"]["source_polygons"] = records(all_art)
        result["preview"]["excluded_polygons"] = records(all_art.difference(included))
        if included.is_empty:
            raise ValueError("Select at least one nonempty filled artwork region")
        blocked = GeometryCollection()
        for name, face in spec["faces"].items():
            available = box(*face["bounds"]).intersection(stock_enclosure(face, error, outer=False))
            outside = transform(included, face["design_to_face"]).difference(available)
            if not outside.is_empty and outside.area > 0:
                canonical = transform(outside, face["design_to_face"], inverse=True)
                blocked = blocked.union(canonical)
                result["diagnostics"].append(diagnostic("ARTWORK_OUT_OF_BOUNDS", "Included artwork extends beyond this face's machining bounds or guaranteed stock material (including holes); move, resize or explicitly exclude it", name, canonical))
        result["preview"]["blocked_polygons"] = records(blocked)
        result["preview"]["cuttable_polygons"] = records(included.difference(blocked))
        if not blocked.is_empty:
            return result

        # Shared corner conditioning is a proposal, never an implicit edit.
        radius = finisher["diameter"]/2 if spec["style"] != "v" else finisher.get("tip_diameter", 0.)/2
        art = included
        if radius:
            art = offset(offset(art, -radius, error/4), radius, error/4)
            art = offset(offset(art, radius, error/4), -radius, error/4)
        if art.is_empty:
            result["preview"]["blocked_polygons"] = records(included)
            result["preview"]["cuttable_polygons"] = []
            result["diagnostics"].append(diagnostic("TOOL_TOO_LARGE", "No retained artwork can be reached with the selected finishing cutter", geometry=included))
            return result
        lost_components = [p for p in polygons(included) if p.intersection(art).area <= error*error]
        if lost_components:
            lost = unary_union(lost_components)
            result["preview"]["blocked_polygons"] = records(lost)
            result["preview"]["cuttable_polygons"] = records(included.difference(lost))
            result["diagnostics"].append(diagnostic("TOOL_TOO_LARGE", "The selected tools would completely remove an included artwork component; explicitly exclude it or select a smaller cutter", geometry=lost))
            return result

        # Reported grid compensation is reviewed with the actual pair.  It is
        # intentionally in the proposal hash, not a hidden edit of user fit.
        grid_reserve = math.sqrt(2)*qxy+4*error
        realized_side = side+2*grid_reserve
        if spec["style"] == "v":
            theta = math.radians(finisher["included_angle_deg"])/2
            realized_side += 2*qz*math.sin(theta)
        if spec["style"] == "bull":
            # Axial interval construction's maximum radial change is recorded;
            # small enough intervals below bound it by the machine grid.
            realized_side += 2*qxy
        proposal_key = {k: v for k, v in spec.items() if k not in ("input_hash", "accepted_proposal_hash", "revision")}
        proposal_key["conditioned_artwork"] = records(art)
        proposal_key["realized_side_compensation"] = realized_side
        proposal_hash = hash_json(proposal_key)
        result["proposal"] = {"hash": proposal_hash, "polygons": records(art),
                              "difference_polygons": records(included.symmetric_difference(art)),
                              "reason": "Matched corner conditioning and quantified machine-grid realization",
                              "realization": {"requested_side_clearance": side, "planning_normal_clearance": realized_side,
                                              "maximum_compensation_mm": realized_side-side,
                                              "xy_resolution": qxy, "z_resolution": qz}}
        realization = {"xy_bound": realized_side-side+2*qxy,
                       "z_bound": qz+(qxy/math.tan(math.radians(finisher["included_angle_deg"])/2)
                                       if spec["style"] == "v" else qz)}
        result["proposal"]["realization"].update(realization)
        result["proposal"]["realization"]["meaning"] = (
            "Nominal inputs are preserved. This review accepts bounded final geometry deviations "
            "caused by the movement lattice and conservative cutter compensation; these are not hidden fit edits.")
        result["preview"]["retained_polygons"] = records(art)
        result["preview"]["cuttable_polygons"] = records(art)
        for name, face in spec["faces"].items():
            outside = transform(art, face["design_to_face"]).difference(
                stock_enclosure(face, error, outer=False).intersection(box(*face["bounds"])))
            if outside.area > 0:
                canonical = transform(outside, face["design_to_face"], inverse=True)
                result["diagnostics"].append(diagnostic("MODIFIED_ARTWORK_OUTSIDE_STOCK", "The matched corner proposal needs material outside this face's guaranteed stock; adjust placement, stock, or selected cutter", name, canonical))
                result["preview"]["blocked_polygons"] = records(canonical)
                result["preview"]["cuttable_polygons"] = records(art.difference(canonical))
        if any(d["severity"] == "error" for d in result["diagnostics"]):
            return result
        ordered_tools = sorted([t for t in selected if t["id"] != finisher["id"]], key=lambda t: (-t["diameter"], t["id"])) + [finisher]
        targets = {}
        progress({"stage": "geometry", "fraction": .05, "message": "Building matching cutter-aware targets"})
        for face_index, name in enumerate(("board", "plug")):
            face = spec["faces"][name]
            target = Target(art, name, face, spec["style"], D, H, D-g, realized_side, finisher, error)
            targets[name] = target
            if name == "plug":
                required_surface = offset(target.art, target.boundary_offset(D-g), error/4)
                missing_stock = required_surface.difference(target.guaranteed_stock)
                if missing_stock.area > 0:
                    result["diagnostics"].append(diagnostic("MATING_MATERIAL_UNAVAILABLE", "The mating plug surface requires material outside the guaranteed captured stock; change placement, stock, fit, or cutter", "plug", transform(missing_stock, face["design_to_face"], inverse=True)))
                retained_at_surface = transform(target.stock.difference(target.removable(D-g)), face["design_to_face"], inverse=True)
                lost = [p for p in polygons(art) if p.intersection(retained_at_surface).area <= error*error]
                if lost:
                    result["diagnostics"].append(diagnostic("NO_MATING_MATERIAL", "The chosen depth/fit leaves no plug material at the finished board face for an included component", "plug", unary_union(lost)))
            predecessor = []
            for tool_index, tool in enumerate(ordered_tools):
                check()
                purpose = "finishing" if tool["id"] == finisher["id"] else "clearing"
                operation_id = f"{name}:{tool['id']}:{purpose}"
                settings = {k: tool[k] for k in ("feed", "plunge_feed", "travel_feed", "spindle", "max_stepdown", "stepover")}
                override = spec.get("overrides", {}).get(operation_id, {})
                if set(override)-set(settings):
                    raise ValueError("An operation override contains an unsupported setting")
                settings.update(override)
                if any(v <= 0 for v in settings.values()):
                    raise ValueError("Operation overrides must have positive feeds, speeds and pass dimensions")
                if target.limit > tool["cutting_length"]+error:
                    result["diagnostics"].append(diagnostic("CUTTING_REACH", f"{tool['name']} cannot reach the {name} depth", name))
                    continue
                clearing_step = settings["max_stepdown"]
                if tool["shape"] == "v":
                    # Leave room for the explicitly bounded floor scallop of
                    # the preceding clearing layer when descending again.
                    clearing_step -= qz
                    if clearing_step < qz:
                        clearing_step = qz
                depths = _depths(target.limit, clearing_step, qz, face["origin"][2])
                clearing_depths = set(depths)
                if spec["style"] != "flat":
                    # Boundary finish layers resolve taper and corner transitions;
                    # capped broad areas receive coverage only at main layers.
                    delta = max(qz, min(settings["max_stepdown"], max(qxy*2, qz)))
                    depths = sorted(set(depths + _depths(target.limit, delta, qz, face["origin"][2])))
                layers = []
                approximate_points = 0
                for layer_index, h in enumerate(depths):
                    check()
                    axial_step = max(qz/4, min(settings["max_stepdown"], qxy/2))
                    # Constant-radius cutters need only one deepest slice.
                    domain = target.centres(tool, h, qxy/math.sqrt(2)+error, axial_step)
                    if domain.is_empty:
                        continue
                    spacing = settings["stepover"]
                    if tool["shape"] == "bull":
                        spacing = min(spacing, max(qxy, tool["diameter"]-2*tool["corner_radius"]-qxy))
                    elif tool["shape"] == "v":
                        theta = math.radians(tool["included_angle_deg"])/2
                        allowance = max(error, min(qz/2, max(g, b, qz)/16))
                        spacing = min(spacing, max(qxy, tool.get("tip_diameter", 0.)+2*allowance*math.tan(theta)))
                    horizontal = (domain.bounds[2]-domain.bounds[0]) >= (domain.bounds[3]-domain.bounds[1])
                    paths = coverage(domain, spacing, horizontal,
                                     raster=(spec["style"] == "flat" or h in clearing_depths),
                                     point_budget=350000-approximate_points)
                    approximate_points += sum(len(p) for p in paths)
                    if approximate_points > 350000:
                        raise PlanningLimit("This tool/stepover combination exceeds the bounded in-memory motion budget; select a clearing tool, increase the permitted spacing or reduce the design")
                    layers.append((h, paths, domain))
                    if layer_index % 8 == 0:
                        progress({"stage": "paths", "fraction": .1+.55*(face_index+(tool_index+(layer_index+1)/len(depths))/len(ordered_tools))/2,
                                  "message": f"Planning {name} / {tool['name']} layer {layer_index+1}/{len(depths)}"})
                if not layers:
                    result["diagnostics"].append({"code": "UNUSED_TOOL", "severity": "info", "face": name, "region_ids": [], "polygons": [],
                                                  "message": f"{tool['name']} has no reachable work on this face"})
                    continue
                baseline = emit_layers(layers, face, settings, spec["resolution"], spec.get("travel_mode", "feed"))
                candidate = emit_layers(layers, face, settings, spec["resolution"], spec.get("travel_mode", "feed"), ordered=True)
                bt, ct = timing(baseline), timing(candidate)
                candidate_ok = ct["total_s"] <= bt["total_s"]
                if candidate_ok:
                    # Reordering changes feed links even when the principal
                    # coverage curves are identical. Only select the candidate
                    # when earlier actual sweeps establish its entry stock.
                    prior = [o for o in result["operations"] if o["face"] == name]
                    trial = {"id": operation_id, "tool_id": tool["id"],
                             "settings": settings, "moves": candidate}
                    candidate_ok, _, _, _ = verify_layer_engagement(
                        face, prior+[trial], {t["id"]: t for t in selected}, error, cancelled)
                moves, best = (candidate, ct) if candidate_ok else (baseline, bt)
                operation = {"id": operation_id, "face": name, "tool_id": tool["id"], "purpose": purpose,
                             "predecessors": predecessor[-1:], "settings": settings, "moves": moves,
                             "time": {"cut_s": best["cut_s"], "travel_s": best["travel_s"], "baseline_s": bt["total_s"],
                                      "optimized_s": best["total_s"]},
                             "optimization": {"candidate_accepted": candidate_ok,
                                              "criterion": "no greater motion time and verified predecessor-stock engagement"}}
                result["operations"].append(operation)
                predecessor.append(operation_id)
                del baseline, candidate, layers
            ops = [o for o in result["operations"] if o["face"] == name]
            counts = {"operations": len(ops), "tool_changes": sum(a["tool_id"] != b["tool_id"] for a, b in zip(ops, ops[1:]))}
            result["per_face_counts"][name] = counts
            for kind, limit in face.get("limits", {}).items():
                if limit is not None and counts[kind] > limit:
                    result["diagnostics"].append(diagnostic("COUNT_LIMIT", f"{name} requires {counts[kind]} {kind.replace('_', ' ')}, exceeding its independent limit of {limit}", name))
            if not ops:
                result["diagnostics"].append(diagnostic("NO_PATHS", f"The selected tools cannot produce any {name} machining paths", name))
        check()
        progress({"stage": "verification", "fraction": .7, "message": "Checking final cutter sweeps and whole-stock assembly"})
        verification, stocks = verify_plan(spec, result, error, cancelled, targets, realization)
        result["verification"] = verification
        result["diagnostics"].extend(verification.pop("diagnostics"))
        motion_hash = hash_json(result["operations"])
        result["verification"]["motion_hash"] = motion_hash
        proposal_hash = hashlib.sha256((proposal_hash+motion_hash).encode()).hexdigest()
        result["proposal"]["hash"] = proposal_hash
        actual_board = transform(stocks["board"].removal(0.), spec["faces"]["board"]["design_to_face"], inverse=True)
        actual_plug = transform(stocks["plug"].remaining(D-g), spec["faces"]["plug"]["design_to_face"], inverse=True)
        board_extent = transform(stocks["board"].stock, spec["faces"]["board"]["design_to_face"], inverse=True)
        actual_plug = actual_plug.intersection(board_extent)
        result["proposal"]["predicted_board_polygons"] = records(actual_board)
        result["proposal"]["predicted_plug_polygons"] = records(actual_plug)
        result["proposal"]["difference_polygons"] = records(included.symmetric_difference(actual_board).union(included.symmetric_difference(actual_plug)))
        for name, stock in stocks.items():
            check()
            result["preview"][name] = preview_mesh(stock.surface, spec["faces"][name]["stock"], max_axis=20,
                                                  outline=stock.nominal_stock)
            result["preview"][name]["stock_outline"] = records(stock.nominal_stock)
            result["preview"][name]["stock_outline_tolerance"] = spec["faces"][name].get("stock_outline_tolerance", 0.)
        xmin, ymin, xmax, ymax = art.bounds
        y = (ymin+ymax)/2
        section = {"name": "Centre section", "points": [], "nominal_glue_gap": g,
                   "nominal_backing_clearance": b, "nominal_side_clearance": side,
                   "assembly_reference": "nominated pose; positive clearance does not imply unique self-seating"}
        contact_upper = math.inf
        plug_extent = transform(stocks["plug"].guaranteed_stock, spec["faces"]["plug"]["design_to_face"], inverse=True)
        guaranteed_board_extent = transform(stocks["board"].guaranteed_stock, spec["faces"]["board"]["design_to_face"], inverse=True)
        overlap_extent = guaranteed_board_extent.intersection(plug_extent)
        floor_gaps = []
        for i in range(65):
            x = xmin+(xmax-xmin)*i/64
            samples = {}
            present = {}
            for name in ("board", "plug"):
                a, bb, c, d, e, f = spec["faces"][name]["design_to_face"]
                px, py = a*x+c*y+e, bb*x+d*y+f
                samples[name] = stocks[name].surface(px, py)
                present[name] = stocks[name].nominal_stock.covers(Point(px, py))
            if overlap_extent.covers(Point(x, y)):
                contact_upper = min(contact_upper, samples["board"]+samples["plug"])
                if samples["board"] >= D-realization["z_bound"] and samples["plug"] <= realization["z_bound"]:
                    floor_gaps.append(samples["board"]+samples["plug"]-(D-g))
            section["points"].append({"x": x, "y": y, "board_depth": samples["board"],
                                      "plug_assembled_depth": D-g-samples["plug"],
                                      "board_material_present": present["board"],
                                      "plug_material_present": present["plug"]})
        assembly_clear = next(c["passed"] for c in result["verification"]["checks"] if c["id"] == "whole_stock_continuous_insertion")
        contact = {"kind": "conservative_contact_bracket" if assembly_clear else "assembly_not_clear",
                   "nominated_pose_clear": assembly_clear,
                   "additional_insertion_lower_mm": 0. if assembly_clear else None,
                   "additional_insertion_upper_mm": max(0., contact_upper-(D-g)+2*error) if math.isfinite(contact_upper) else None,
                   "explanation": "The nominated pose has the continuous-stock clearance check. Analytic surface values at section points bound first contact from above; this is a bracket, not an exact contact location or a prediction of wood compression."}
        section["first_contact"] = contact
        result["verification"]["first_contact"] = contact
        measurements = []
        if floor_gaps:
            measurements.append({"kind": "bottom_glue_gap", "scope": "analytic samples on the displayed floor section",
                                 "nominal_mm": g, "sample_min_mm": min(floor_gaps), "sample_max_mm": max(floor_gaps)})
        relief = overlap_extent.difference(offset(art, finisher["diameter"], error))
        if not relief.is_empty:
            point = relief.representative_point()
            a, bb, c, d, e, f = spec["faces"]["plug"]["design_to_face"]
            sample = stocks["plug"].surface(a*point.x+c*point.y+e, bb*point.x+d*point.y+f)-(D-g)
            measurements.append({"kind": "backing_clearance", "scope": "analytic sample on surrounding relief",
                                 "nominal_mm": b, "sample_min_mm": sample, "sample_max_mm": sample})
        wall_depth = (D-g)/2
        vertical_wall = spec["style"] == "flat"
        if spec["style"] == "bull":
            r = finisher["corner_radius"]
            low, high = max(0., r-b), min(D-r, D-g-r)
            if low < high:
                wall_depth = (low+high)/2
                vertical_wall = True
        b_inner = transform(stocks["board"].removal(wall_depth), spec["faces"]["board"]["design_to_face"], inverse=True)
        b_outer = transform(stocks["board"].removal(wall_depth, outer=True), spec["faces"]["board"]["design_to_face"], inverse=True)
        q = D-g-wall_depth
        p_outer = transform(stocks["plug"].remaining(q), spec["faces"]["plug"]["design_to_face"], inverse=True).intersection(board_extent)
        p_inner = transform(stocks["plug"].guaranteed_stock.difference(stocks["plug"].removal(q, outer=True)), spec["faces"]["plug"]["design_to_face"], inverse=True).intersection(board_extent)
        if not any(shape.is_empty for shape in (b_inner, b_outer, p_inner, p_outer)) and b_inner.covers(p_outer):
            measurements.append({"kind": "normal_side_clearance" if vertical_wall else "horizontal_wall_separation",
                                 "scope": "conservative polygon enclosure at one section, not a global wall-distance extremum",
                                 "board_depth_mm": wall_depth, "requested_normal_clearance_mm": side,
                                 "lower_mm": max(0., p_outer.boundary.distance(b_inner.boundary)-2*error),
                                 "upper_mm": p_inner.boundary.distance(b_outer.boundary)+2*error})
        section["clearance_measurements"] = measurements
        result["verification"]["clearance_measurements"] = measurements
        result["preview"]["sections"] = [section]
        errors = [d for d in result["diagnostics"] if d["severity"] == "error"]
        if errors:
            result["status"] = "blocked"
            marked = unary_union([from_records(d["polygons"]) for d in errors if d.get("polygons")])
            result["preview"]["blocked_polygons"] = records(marked)
            result["preview"]["cuttable_polygons"] = records(art.difference(marked))
        else:
            result["status"] = "ready" if spec.get("accepted_proposal_hash") == proposal_hash else "needs_acceptance"
        progress({"stage": "complete", "fraction": 1., "message": result["status"].replace("_", " ")})
    except InterruptedError:
        result["status"] = "cancelled"
        result["operations"] = []
    except (ValueError, KeyError, TypeError, GEOSException, MemoryError, PlanningLimit) as exc:
        result["diagnostics"].append(diagnostic("RESOURCE_LIMIT" if isinstance(exc, (PlanningLimit, MemoryError)) else "INVALID_INPUT", str(exc)))
        result["status"] = "blocked"
        if isinstance(exc, (PlanningLimit, MemoryError)):
            # A failed calculation has no applicable motion. Release potentially
            # large partial arrays before the worker serializes the diagnostic.
            result["operations"] = []
            result["per_face_counts"] = {}
    finally:
        result["resource_stats"] = {"elapsed_seconds": time.monotonic()-started,
                                    "peak_rss_native_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                                    "motion_count": sum(len(o["moves"]) for o in result["operations"]),
                                    "motion_storage": "temporary-file-backed 40-byte records; streamed JSON output",
                                    "motion_storage_bytes": sum(getattr(o["moves"], "storage_bytes", 0) for o in result["operations"])}
    return result
