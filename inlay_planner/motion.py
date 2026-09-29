"""Deterministic planar coverage, quantized motion, and per-use timing."""
from __future__ import annotations

import math

from shapely.geometry import LineString
from shapely.prepared import prep

from .geometry import polygons
from .motion_storage import PackedMoves


def line_parts(value):
    if value.is_empty:
        return []
    if value.geom_type in ("LineString", "LinearRing"):
        return [list(value.coords)] if value.length > 1e-10 else []
    return [p for g in getattr(value, "geoms", ()) for p in line_parts(g)]


def coverage(domain, spacing, horizontal=True, raster=True, point_budget=350000):
    paths = []
    count = 0

    def append(path):
        nonlocal count
        count += len(path)
        if count > point_budget:
            raise MemoryError("This coverage would exceed the bounded motion budget; no paths were omitted or resolution relaxed")
        paths.append(path)

    for poly in polygons(domain):
        append(list(poly.exterior.coords))
        for ring in poly.interiors:
            append(list(ring.coords))
        if not raster:
            continue
        xmin, ymin, xmax, ymax = poly.bounds
        lo, hi = (ymin, ymax) if horizontal else (xmin, xmax)
        n = max(1, math.ceil((hi-lo)/spacing))
        if 2*n > point_budget-count:
            raise MemoryError("The requested pass spacing exceeds the bounded motion budget; use a clearing cutter or revise the user-selected settings")
        for i in range(n+1):
            v = lo+(hi-lo)*i/n
            line = LineString([(xmin-1, v), (xmax+1, v)]) if horizontal else LineString(
                [(v, ymin-1), (v, ymax+1)])
            parts = line_parts(line.intersection(poly))
            if i % 2:
                parts = [list(reversed(p)) for p in reversed(parts)]
            for part in parts:
                append(part)
    return paths


def snap(v, quantum):
    return round(round(v/quantum)*quantum, 12)


def quantized_paths(paths, origin, xy):
    result = []
    ox, oy = origin[:2]
    for path in paths:
        p = []
        for x, y in path:
            point = (snap(x-ox, xy)+ox, snap(y-oy, xy)+oy)
            if not p or p[-1] != point:
                p.append(point)
        if len(p) > 1:
            result.append(p)
    return result


def ordered_paths(paths, start):
    """Bounded deterministic nearest-end ordering; never changes coverage."""
    if len(paths) > 1500:
        return paths
    todo = list(paths)
    result = []
    current = start
    while todo:
        best = min(range(len(todo)), key=lambda i: min(
            math.dist(current, todo[i][0]), math.dist(current, todo[i][-1])))
        path = todo.pop(best)
        if math.dist(current, path[-1]) < math.dist(current, path[0]):
            path = list(reversed(path))
        result.append(path)
        current = path[-1]
    return result


def emit_layers(layers, face, settings, resolution, travel_mode="feed", ordered=False):
    origin = face["origin"]
    clearance = math.ceil((face["clearance_height"]-origin[2])/resolution["z"])*resolution["z"]
    clearance = round(clearance, 12)
    moves = PackedMoves()
    last = None

    def emit(x, y, z, role):
        nonlocal last
        point = (round(x, 12), round(y, 12), round(z, 12))
        if point == last:
            return
        feed = settings["plunge_feed"] if role == "plunge" else settings[
            "travel_feed" if role == "travel" else "feed"]
        moves.append({"kind": "rapid" if role == "travel" and travel_mode == "controller_rapid" else "feed",
                      "role": role, "x": point[0], "y": point[1], "z": point[2],
                      "feed_mm_min": feed})
        last = point

    emit(0., 0., clearance, "travel")
    for depth, paths, domain in layers:
        paths = quantized_paths(paths, origin, resolution["xy"])
        legal_workspace = prep(domain)
        if ordered:
            paths = ordered_paths(paths, (last[0]+origin[0], last[1]+origin[1]))
        z = snap(-depth-origin[2], resolution["z"])
        for path in paths:
            x, y = path[0]
            # Feed links are allowed only inside the current conservative cut
            # domain at this depth. They are cutting moves, never false rapids.
            current_xy = (last[0]+origin[0], last[1]+origin[1])
            link = LineString([current_xy, (x, y)])
            if last[2] == z and legal_workspace.covers(link):
                emit(x-origin[0], y-origin[1], z, "cut")
            else:
                emit(last[0], last[1], clearance, "travel")
                emit(x-origin[0], y-origin[1], clearance, "travel")
                emit(x-origin[0], y-origin[1], z, "plunge")
            for x, y in path[1:]:
                emit(x-origin[0], y-origin[1], z, "cut")
    if last:
        emit(last[0], last[1], clearance, "travel")
    return moves


def timing(moves):
    cut = travel = 0.
    for a, b in zip(moves, moves[1:]):
        seconds = math.dist([a[k] for k in ("x", "y", "z")],
                            [b[k] for k in ("x", "y", "z")])*60/b["feed_mm_min"]
        if b["role"] == "travel":
            travel += seconds
        else:
            cut += seconds
    return {"cut_s": cut, "travel_s": travel, "total_s": cut+travel}
