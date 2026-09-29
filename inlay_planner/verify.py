"""Conservative verification of final quantized, piecewise linear motions.

Horizontal cuts and vertical entries have analytic swept cross-sections.
Inner/outer polygon enclosures, not sampled preview pixels, determine status.
Adaptive depth intervals enclose complete assembly motion for 2.5D stocks.
"""
from __future__ import annotations

from collections import defaultdict
import struct

from shapely.geometry import GeometryCollection, LineString, Point, box

from .geometry import (cutter_profile, cutter_radius, disk_buffer, from_records, records,
                       stock_enclosure, stock_outline, transform)
from .schema import hash_json


def bounded_linework(value, segment_budget=128):
    """Split sweeps before GEOS allocates a global offset/intersection graph.

    Unioning buffers of contiguous subsegments has the same swept-disc set.
    Shared endpoints add no new cutter position, so both enclosure directions
    remain valid without relaxing the geometric tolerance.
    """
    batch, count = [], 0

    def pieces(shape):
        if shape.geom_type == 'Point':
            yield shape, 1
        elif shape.geom_type in ('LineString', 'LinearRing'):
            coordinates = iter(shape.coords)
            chunk = [next(coordinates)]
            for point in coordinates:
                chunk.append(point)
                if len(chunk) > segment_budget:
                    yield LineString(chunk), len(chunk)-1
                    chunk = [point]
            if len(chunk) > 1:
                yield LineString(chunk), len(chunk)-1
        else:
            for part in getattr(shape, 'geoms', ()):
                yield from pieces(part)

    for part, size in pieces(value):
        if batch and count+size > segment_budget:
            yield GeometryCollection(batch)
            batch, count = [], 0
        batch.append(part)
        count += size
    if batch:
        yield GeometryCollection(batch)


def subtract_shared_segments(value, previous):
    """Remove only exactly shared cutter-centre segments, without noding.

    Noding two full raster graphs creates every crossing even though the
    verifier only needs their identical centre positions. Packed endpoint
    keys establish that exact subset cheaply; other segments retain the full
    stock-interval proof and are never assumed cleared.
    """
    def parts(shape):
        if shape.geom_type in ('LineString', 'LinearRing', 'Point'):
            yield shape
        else:
            for child in getattr(shape, 'geoms', ()):
                yield from parts(child)

    def key(a, b):
        if b < a:
            a, b = b, a
        return struct.pack('<4d', *a, *b)

    point_targets = {tuple(part.coords[0]) for part in parts(value) if part.geom_type == 'Point'}
    matched_points, segments = set(), set()
    for part in parts(previous):
        iterator = iter(part.coords)
        a = next(iterator)
        if a in point_targets:
            matched_points.add(a)
        for b in iterator:
            segments.add(key(a, b))
            if b in point_targets:
                matched_points.add(b)
            a = b
    retained = []
    for part in parts(value):
        if part.geom_type == 'Point':
            if tuple(part.coords[0]) not in matched_points:
                retained.append(part)
            continue
        iterator = iter(part.coords)
        a = next(iterator)
        run = []
        for b in iterator:
            if key(a, b) in segments:
                if len(run) > 1:
                    retained.append(LineString(run))
                run = []
            elif run:
                run.append(b)
            else:
                run = [a, b]
            a = b
        if len(run) > 1:
            retained.append(LineString(run))
    return GeometryCollection(retained)


class SweptStock:
    def __init__(self, face, operations, tools, error):
        self.face, self.error = face, error
        self.stock = stock_enclosure(face, error, outer=True)
        self.nominal_stock = stock_outline(face)
        self.guaranteed_stock = stock_enclosure(face, error, outer=False)
        grouped = defaultdict(list)
        points = defaultdict(set)
        ox, oy, oz = face["origin"]
        self.unsupported = []
        for op in operations:
            run_key = None
            run = []

            def flush():
                nonlocal run, run_key
                if len(run) > 1:
                    grouped[run_key].append(LineString(run))
                run = []
                run_key = None

            for a, b in zip(op["moves"], op["moves"][1:]):
                if b["role"] == "travel":
                    flush()
                    continue
                h = -b["z"]-oz
                if h <= 0:
                    continue
                same_xy = abs(a["x"]-b["x"])+abs(a["y"]-b["y"]) < 1e-9
                same_z = abs(a["z"]-b["z"]) < 1e-9
                if not same_xy and not same_z:
                    self.unsupported.append("An unverified simultaneous XYZ cutting move was emitted")
                    continue
                key = (op["tool_id"], round(h, 10))
                if same_xy:
                    flush()
                    points[key].add((b["x"]+ox, b["y"]+oy))
                else:
                    start = (a["x"]+ox, a["y"]+oy)
                    end = (b["x"]+ox, b["y"]+oy)
                    if run_key == key and run and run[-1] == start:
                        run.append(end)
                    else:
                        flush()
                        run_key, run = key, [start, end]
            flush()
        for key, pts in points.items():
            endpoints = {tuple(p) for line in grouped[key] for p in (line.coords[0], line.coords[-1])}
            grouped[key].extend(Point(p) for p in pts-endpoints)
        # Keep linework unsplit. A noded unary union of a dense raster creates
        # large numbers of unnecessary intersections before buffering.
        self.groups = sorted([(tools[t], h, GeometryCollection(parts)) for (t, h), parts in grouped.items()],
                             key=lambda item: -item[1])
        self._flat_sections = all(tool["shape"] == "flat" for tool, _, _ in self.groups)
        self._cache = {}

    def removal(self, depth, outer=False):
        if depth < 0:
            return self.stock
        # A flat cutter has a depth-invariant cross-section until its tip
        # passes the requested slice. All depths with the same active group
        # count therefore share precisely the same immutable swept set.
        section = sum(h+1e-10 >= depth for _, h, _ in self.groups) if self._flat_sections else round(depth, 9)
        key = (section, outer)
        if key in self._cache:
            return self._cache[key]
        value = GeometryCollection()
        covered_paths = []
        for tool, h, path in self.groups:
            if h+1e-10 < depth:
                continue
            radius = cutter_radius(tool, max(0., h-depth))
            if radius > 0:
                # Merge each bounded group immediately; do not retain every
                # large intermediate layer buffer at once.
                active = path
                for previous_radius, previous_path in covered_paths:
                    if previous_radius >= radius:
                        # A larger disc at an identical centre already
                        # contains this entire true cutter section. Exact
                        # endpoint membership avoids eroding and noding a
                        # complete, densely perforated removal polygon.
                        active = subtract_shared_segments(active, previous_path)
                        if active.is_empty:
                            break
                if not active.is_empty:
                    for batch in bounded_linework(active):
                        # Clip once after the complete union. Repeated Boolean
                        # clipping at a curved captured-stock edge can create
                        # floating-point seams between otherwise overlapping
                        # safe sweeps; keeping their air halo avoids that loss.
                        sweep = disk_buffer(batch, radius, self.error, outer)
                        value = value.union(sweep)
                covered_paths.append((radius, path))
        value = value.intersection(self.stock)
        if len(self._cache) >= 6:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = value
        return value

    def remaining(self, depth):
        if depth < -self.error or depth > self.face["stock"][2]+self.error:
            return GeometryCollection()
        return self.stock.difference(self.removal(max(0., depth), outer=False))

    def surface(self, x, y):
        point = Point(x, y)
        return max([0.] + [h-cutter_profile(tool, path.distance(point))
                          for tool, h, path in self.groups])

    def subset(self, groups):
        """A bounded slice view sharing immutable motion geometry."""
        result = object.__new__(SweptStock)
        result.face, result.error, result.stock = self.face, self.error, self.stock
        result.nominal_stock, result.guaranteed_stock = self.nominal_stock, self.guaranteed_stock
        result.groups = sorted(groups, key=lambda item: -item[1])
        result._flat_sections = all(tool["shape"] == "flat" for tool, _, _ in result.groups)
        result.unsupported, result._cache = [], {}
        return result


def verify_layer_engagement(face, operations, tools, error, cancelled):
    """Bound new axial material engagement using the earlier emitted sweeps.

    New removal at depth d must already be removed at d - stepdown. This
    covers vertical entry as well as lateral cuts. Coincident centre lines
    have an exact translated-profile proof and avoid a false uncertainty
    band where two otherwise identical passes touch the same wall.
    """
    prior_groups = []
    intervals = 0
    for operation in operations:
        emitted = SweptStock(face, [operation], tools, error)
        maximum = operation["settings"]["max_stepdown"]
        for tool, depth, path in reversed(emitted.groups):
            if cancelled():
                raise InterruptedError()
            original_group = (tool, depth, path)
            if depth > maximum+1e-10:
                active = path
                for previous_tool, previous_depth, previous_path in prior_groups:
                    if previous_tool["id"] == tool["id"] and previous_depth >= depth-maximum-1e-10:
                        active = subtract_shared_segments(active, previous_path)
                        if active.is_empty:
                            break
                if not active.is_empty:
                    current = emitted.subset([(tool, depth, active)])
                    previous = emitted.subset(prior_groups)
                    pending = [(maximum, depth)]
                    while pending:
                        if cancelled():
                            raise InterruptedError()
                        lo, hi = pending.pop()
                        intervals += 1
                        possible_new = current.removal(lo, outer=True)
                        guaranteed_old = previous.removal(hi-maximum)
                        uncertain = possible_new.difference(guaranteed_old)
                        if uncertain.area == 0:
                            continue
                        if hi-lo <= 2*error or intervals >= 1024:
                            return False, intervals, uncertain, operation["id"]
                        middle = (lo+hi)/2
                        pending.extend([(lo, middle), (middle, hi)])
            prior_groups.append(original_group)
    return True, intervals, GeometryCollection(), None


def diagnostic(code, message, face=None, geometry=None):
    value = {"code": code, "severity": "error", "message": message,
             "region_ids": [], "polygons": records(geometry) if geometry is not None else []}
    if face:
        value["face"] = face
    return value


def verify_plan(spec, result, error, cancelled=lambda: False, targets=None, realization=None):
    tools = {t["id"]: t for t in spec["tools"]}
    diagnostics = []
    checks = []
    recorded_hash = result.get("verification", {}).get("motion_hash")
    if recorded_hash:
        current_hash = hash_json(result["operations"])
        if current_hash != recorded_hash:
            diagnostics.append(diagnostic("REVIEWED_MOTION_CHANGED", "The motions or settings differ from the reviewed calculation; recalculate and review before applying"))
        checks.append({"id": "reviewed_motion_integrity", "passed": current_hash == recorded_hash})
    stocks = {}
    qxy, qz = spec["resolution"]["xy"], spec["resolution"]["z"]
    for name, face in spec["faces"].items():
        operations = [o for o in result["operations"] if o["face"] == name]
        stock = SweptStock(face, operations, tools, error)
        stocks[name] = stock
        for reason in stock.unsupported:
            diagnostics.append(diagnostic("UNVERIFIED_MOTION", reason, name))
        for op in operations:
            last_depth = 0.
            last_emitted_depth = 0.
            depths = []
            for i, move in enumerate(op["moves"]):
                for axis, quantum in (("x", qxy), ("y", qxy), ("z", qz)):
                    if abs(move[axis]/quantum-round(move[axis]/quantum)) > 1e-6:
                        diagnostics.append(diagnostic("OFF_LATTICE", "A final motion is outside the machine resolution lattice", name))
                        break
                d = -move["z"]-face["origin"][2]
                if move["role"] != "travel" and d > 0:
                    depths.append(d)
                    if d < last_emitted_depth-error:
                        diagnostics.append(diagnostic("NONMONOTONE_CUT_DEPTH", "Cutting layers must descend in emitted order for predecessor-stock verification", name))
                    last_emitted_depth = max(last_emitted_depth, d)
                    if d > tools[op["tool_id"]]["cutting_length"]+error:
                        diagnostics.append(diagnostic("CUTTING_REACH", "The cutter cannot reach the emitted depth", name))
                if move["role"] == "travel" and i:
                    previous = op["moves"][i-1]
                    lateral = abs(move["x"]-previous["x"])+abs(move["y"]-previous["y"]) > error
                    if lateral and min(move["z"], previous["z"])+face["origin"][2] < face["clearance_height"]-qz-error:
                        diagnostics.append(diagnostic("STOCK_CROSSING_TRAVEL", "A noncutting lateral move crosses below the clearance plane", name))
            for depth in sorted(set(round(d, 8) for d in depths)):
                if depth-last_depth > op["settings"]["max_stepdown"]+error:
                    diagnostics.append(diagnostic("STEPDOWN", "A generated layer exceeds the effective maximum stepdown", name))
                last_depth = depth
        beyond = stock.removal(0., outer=True).difference(box(*face["bounds"]))
        if beyond.area > 0:
            diagnostics.append(diagnostic("CUTTER_OUT_OF_BOUNDS", "The stock-engaged cutter sweep exceeds this face's machining bounds", name, beyond))
        checks.append({"id": name+"_motion_and_bounds", "passed": not any(d.get("face") == name for d in diagnostics)})

        engaged, engagement_intervals, engagement_region, operation_id = verify_layer_engagement(
            face, operations, tools, error, cancelled)
        if not engaged:
            item = diagnostic("ENTRY_STOCK_UNRESOLVED", "The earlier emitted sweeps do not establish the effective maximum stepdown for every point of this layer; reduce pass depth or change clearing coverage", name, engagement_region)
            item["operation_id"] = operation_id
            diagnostics.append(item)
        checks.append({"id": name+"_predecessor_stock_engagement", "passed": engaged,
                       "depth_intervals": engagement_intervals,
                       "includes_vertical_entries": True})

        if targets is not None:
            # The accepted proposal states finite XY/Z realization bounds.
            # Enclose every depth interval using monotonic removal: target at
            # lo is largest, actual inner sweep at hi-dz is smallest. Erosion
            # by dx is a *reported* boundary-deviation limit, not exactness.
            dx, dz = realization["xy_bound"], realization["z_bound"]
            todo = [(0., targets[name].limit)]
            coverage_intervals = 0
            residual = GeometryCollection()
            gouge = GeometryCollection()
            while todo:
                if cancelled():
                    raise InterruptedError()
                lo, hi = todo.pop()
                coverage_intervals += 1
                expected = targets[name].removable(lo).buffer(-dx)
                actual = stock.removal(max(0., hi-dz))
                missing = expected.difference(actual)
                allowed = targets[name].removable(max(0., hi-dz)).buffer(dx)
                excess = stock.removal(lo, outer=True).difference(allowed)
                if missing.area == 0 and excess.area == 0:
                    continue
                if hi-lo <= error*2 or coverage_intervals >= 512:
                    residual = missing
                    gouge = excess
                    break
                mid = (lo+hi)/2
                todo.extend([(lo, mid), (mid, hi)])
            if not residual.is_empty:
                diagnostics.append(diagnostic("UNCUTTABLE_RESIDUE", "Unremoved material exceeds the explicitly reviewed XY/Z realization bounds; choose tools/settings that reach this area", name, residual))
            if not gouge.is_empty:
                diagnostics.append(diagnostic("UNEXPECTED_REMOVAL", "A final cutter sweep removes material beyond the explicitly reviewed target realization bounds", name, gouge))
            checks.append({"id": name+"_continuous_coverage", "passed": residual.is_empty and gouge.is_empty,
                           "depth_intervals": coverage_intervals, "xy_deviation_bound": dx,
                           "depth_deviation_bound": dz, "checks_both_residue_and_gouge": True})

    # A 2.5D plug's remaining cross-section grows with local depth. During
    # insertion it therefore occupies a subset of its final-position section.
    # Over board-depth interval [lo,hi], use plug section at s-lo (largest)
    # and board guaranteed-removal section at hi (smallest).
    s = result["derived"]["seating_depth"]
    board, plug = stocks["board"], stocks["plug"]
    board_matrix = spec["faces"]["board"]["design_to_face"]
    plug_matrix = spec["faces"]["plug"]["design_to_face"]
    board_stock = transform(board.stock, board_matrix, inverse=True)
    guaranteed_board_stock = transform(board.guaranteed_stock, board_matrix, inverse=True)
    pending = [(0., min(s, spec["faces"]["board"]["stock"][2]))]
    intervals = 0
    unresolved = GeometryCollection()
    collision = GeometryCollection()
    while pending:
        if cancelled():
            raise InterruptedError()
        lo, hi = pending.pop()
        if hi < lo:
            continue
        intervals += 1
        p = transform(plug.remaining(s-lo), plug_matrix, inverse=True).intersection(board_stock)
        cavity = transform(board.removal(hi), board_matrix, inverse=True)
        overlap = p.difference(cavity)
        if overlap.area == 0:
            continue
        mid = (lo+hi)/2
        # Inner remaining stock uses outer removal. An intersection of these
        # solids is a positive collision witness, independent of display data.
        p_inner = transform(plug.guaranteed_stock.difference(plug.removal(s-mid, outer=True)), plug_matrix, inverse=True)
        b_inner = guaranteed_board_stock.difference(transform(board.removal(mid, outer=True), board_matrix, inverse=True))
        witness = p_inner.intersection(b_inner)
        if witness.area > 0:
            collision = collision.union(witness)
            break
        if hi-lo <= error*2 or intervals >= 2048:
            unresolved = unresolved.union(overlap)
            break
        pending.extend([(lo, mid), (mid, hi)])
    if not collision.is_empty:
        diagnostics.append(diagnostic("ASSEMBLY_INTERFERENCE", "The remaining plug stock intersects the board at the nominated assembly pose; check relief bounds, root geometry, fit and tools", geometry=collision))
    elif not unresolved.is_empty:
        diagnostics.append(diagnostic("ASSEMBLY_UNRESOLVED", "Conservative swept-stock intervals cannot establish clearance at this resolution; change fit, tools or resolution", geometry=unresolved))
    checks.append({"id": "whole_stock_continuous_insertion", "passed": collision.is_empty and unresolved.is_empty,
                   "depth_intervals": intervals, "includes_perimeter_backing_and_roots": True})
    # Motion/coverage diagnostics above use each face's local stock frame.
    # The public result and preview use canonical artwork XY, so normalize
    # once at this boundary. Assembly diagnostics are already canonical and
    # deliberately have no face. Solver input diagnostics bypass this step.
    for item in diagnostics:
        if item.get("face") and item.get("polygons"):
            item["polygons"] = records(transform(from_records(item["polygons"]),
                                                  spec["faces"][item["face"]]["design_to_face"],
                                                  inverse=True))
    return {"status": "passed" if not diagnostics else "failed",
            "method": "analytic linear-move slices, circular inner/outer enclosures, predecessor-stock and monotone assembly depth intervals",
            "error_budget": error, "checks": checks, "diagnostics": diagnostics,
            "stock_outline_tolerances": {name: face.get("stock_outline_tolerance", 0.) for name, face in spec["faces"].items()},
            "coverage": "finite constant-section stock including captured holes; possible outer material; planar moves and vertical entries; no physical machine accuracy claim"}, stocks
