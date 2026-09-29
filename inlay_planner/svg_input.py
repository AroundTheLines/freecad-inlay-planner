"""Filled SVG geometry with explicit unsupported-content diagnostics.

No resource retrieval, font lookup, stroke expansion, or implicit repair occurs.
All accepted curves are flattened in physical space with a bounded chord error.
"""
from __future__ import annotations

import hashlib
import math
import re

from defusedxml import ElementTree
from shapely.geometry import LinearRing, LineString, Polygon
from shapely.ops import polygonize, unary_union
from shapely.validation import explain_validity
from svg.path import parse_path, Move, Line, Close, CubicBezier, QuadraticBezier, Arc

IDENTITY = (1., 0., 0., 1., 0., 0.)
NUMBER = r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?"
NUMBER_TOKEN = re.compile(NUMBER)
NUMBER_SEPARATOR = re.compile(r"(?:[ \t\r\n]+,?[ \t\r\n]*|,[ \t\r\n]*)")
CSS_GEOMETRY_PROPERTIES = {"transform", "transform-origin", "transform-box", "x", "y",
                           "width", "height", "cx", "cy", "r", "rx", "ry", "d"}
UNITS = {"": 25.4/96, "px": 25.4/96, "mm": 1., "cm": 10.,
         "in": 25.4, "pt": 25.4/72, "pc": 25.4/6, "q": .25}


def mul(a, b):
    return (a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1],
            a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3],
            a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5])


def point(p, m):
    x, y = (p.real, p.imag) if isinstance(p, complex) else p
    result = (m[0]*x+m[2]*y+m[4], m[1]*x+m[3]*y+m[5])
    if not all(math.isfinite(value) for value in result):
        raise ValueError("SVG geometry contains a nonfinite coordinate.")
    return result


def number_list(text, label):
    """Read the complete comma/whitespace-separated list, never repair it."""
    text = text.strip(" \t\r\n")
    values, cursor = [], 0
    while cursor < len(text):
        match = NUMBER_TOKEN.match(text, cursor)
        if match is None:
            raise ValueError(f"Invalid SVG {label}: expected numeric values.")
        value = float(match[0])
        if not math.isfinite(value):
            raise ValueError(f"SVG {label} must contain finite numbers.")
        values.append(value)
        cursor = match.end()
        if cursor == len(text):
            return values
        separator = NUMBER_SEPARATOR.match(text, cursor)
        if separator is None or separator.end() == len(text):
            raise ValueError(f"Invalid SVG {label}: expected a separated numeric value.")
        cursor = separator.end()
    raise ValueError(f"Invalid SVG {label}: expected numeric values.")


def transform(text):
    out = IDENTITY
    cursor = 0
    for match in re.finditer(r"([A-Za-z]+)\s*\(([^)]*)\)", text or ""):
        if (text[cursor:match.start()].strip(" ,\t\r\n")):
            raise ValueError("Invalid SVG transform.")
        name, raw = match.groups()
        values = number_list(raw, "transform arguments")
        if name == "matrix" and len(values) == 6:
            m = tuple(values)
        elif name == "translate" and len(values) in (1, 2):
            m = (1, 0, 0, 1, values[0], values[1] if len(values) == 2 else 0)
        elif name == "scale" and len(values) in (1, 2):
            m = (values[0], 0, 0, values[-1], 0, 0)
        elif name == "rotate" and len(values) in (1, 3):
            angle = math.radians(values[0])
            m = (math.cos(angle), math.sin(angle), -math.sin(angle), math.cos(angle), 0, 0)
            if len(values) == 3:
                x, y = values[1:]
                m = mul(mul((1, 0, 0, 1, x, y), m), (1, 0, 0, 1, -x, -y))
        elif name in ("skewX", "skewY") and len(values) == 1:
            v = math.tan(math.radians(values[0]))
            m = (1, 0, v, 1, 0, 0) if name == "skewX" else (1, v, 0, 1, 0, 0)
        else:
            raise ValueError("Unsupported or invalid SVG transform: " + name)
        if not all(math.isfinite(x) for x in m):
            raise ValueError("Nonfinite SVG transform.")
        out = mul(out, m)
        if not all(math.isfinite(value) for value in out):
            raise ValueError("Nonfinite SVG transform.")
        cursor = match.end()
    if (text or "")[cursor:].strip(" ,\t\r\n"):
        raise ValueError("Invalid SVG transform.")
    return out


def length(text, default=None):
    if text is None:
        return default
    match = re.fullmatch(r"\s*(" + NUMBER + r")\s*([A-Za-z]*)\s*", text)
    if not match or match[2].lower() not in UNITS:
        raise ValueError("SVG physical dimensions need supported units, not percentages.")
    value = float(match[1])*UNITS[match[2].lower()]
    if not math.isfinite(value):
        raise ValueError("SVG physical dimensions must be finite.")
    return value


def polygons_data(geometry):
    if geometry.is_empty:
        return []
    parts = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
    return [{"outer": [list(p) for p in poly.exterior.coords],
             "holes": [[list(p) for p in r.coords] for r in poly.interiors]}
            for poly in parts if poly.geom_type == "Polygon" and poly.area > 0]


def distance_line(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    if dx == dy == 0:
        return math.dist(p, a)
    t = max(0., min(1., ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy)))
    return math.dist(p, (a[0]+t*dx, a[1]+t*dy))


def bezier_flatten(control, tolerance, depth=0):
    if max(distance_line(p, control[0], control[-1]) for p in control[1:-1]) <= tolerance:
        return [control[-1]]
    if depth >= 24:
        raise ValueError("Curve cannot be resolved within the geometry budget.")
    levels = [control]
    while len(levels[-1]) > 1:
        prev = levels[-1]
        levels.append([((a[0]+b[0])/2, (a[1]+b[1])/2) for a, b in zip(prev, prev[1:])])
    left = [row[0] for row in levels]
    right = [row[-1] for row in reversed(levels)]
    return bezier_flatten(left, tolerance, depth+1) + bezier_flatten(right, tolerance, depth+1)


def arc_subdivisions(radius, sweep, tolerance, minimum=1):
    if not math.isfinite(radius) or not math.isfinite(sweep):
        raise ValueError("Curve geometry must be finite.")
    if radius <= 0:
        return minimum  # A singular transform is diagnosed as a degenerate fill.
    # Equivalent to 2*acos(1-tolerance/radius), without cancellation when
    # tolerance/radius is tiny. Never replace an unresolved angle by a floor.
    step = 4*math.asin(math.sqrt(min(1., tolerance/radius/2)))
    if step <= 0:
        raise ValueError("Curve cannot be resolved within the geometry budget.")
    count = max(minimum, math.ceil(abs(sweep)/step))
    if count > 250000:
        raise ValueError("Curve exceeds the bounded import working set.")
    return count


def segment_points(segment, m, tolerance):
    if isinstance(segment, (Line, Close)):
        return [point(segment.end, m)]
    if isinstance(segment, CubicBezier):
        controls = (segment.start, segment.control1, segment.control2, segment.end)
        return bezier_flatten([point(p, m) for p in controls], tolerance)
    if isinstance(segment, QuadraticBezier):
        return bezier_flatten([point(p, m) for p in (segment.start, segment.control, segment.end)], tolerance)
    if isinstance(segment, Arc):
        if segment.radius.real == 0 or segment.radius.imag == 0 or segment.start == segment.end:
            return [point(segment.end, m)]
        # Spectral norm is bounded above by Frobenius norm; eccentric ellipses
        # and affine transforms therefore retain a conservative sagitta bound.
        radius = (max(abs(segment.radius.real), abs(segment.radius.imag))
                  * segment.radius_scale * math.hypot(*m[:4]))
        sweep = abs(math.radians(segment.delta))
        n = arc_subdivisions(radius, sweep, tolerance)
        return [point(segment.point(i/n), m) for i in range(1, n+1)]
    raise ValueError("Unsupported SVG path segment.")


def winding(p, ring):
    x, y = p
    value = 0
    for a, b in zip(ring, ring[1:]):
        cross = (b[0]-a[0])*(y-a[1])-(x-a[0])*(b[1]-a[1])
        if a[1] <= y < b[1] and cross > 0:
            value += 1
        elif b[1] <= y < a[1] and cross < 0:
            value -= 1
    return value


def filled_rings(rings, rule):
    if rule not in ("evenodd", "nonzero"):
        raise ValueError("Unsupported SVG fill rule: " + rule)
    clean = []
    for ring in rings:
        if len(ring) < 4 or ring[0] != ring[-1]:
            raise ValueError("Open or degenerate contour; close the outline before importing.")
        if not LinearRing(ring).is_simple:
            raise ValueError("Self-intersecting contour; repair its outline before importing.")
        if abs(Polygon(ring).area) == 0:
            raise ValueError("Contour has no filled area.")
        clean.append(ring)
    network = unary_union([LineString(ring) for ring in clean])
    regions = []
    for poly in polygonize(network):
        p = poly.representative_point()
        numbers = [winding((p.x, p.y), ring) for ring in clean]
        include = sum(abs(n) for n in numbers) % 2 if rule == "evenodd" else sum(numbers) != 0
        if include:
            regions.append(poly)
    result = unary_union(regions)
    if not result.is_valid:
        raise ValueError(explain_validity(result))
    return result


def parse_svg(text: str, tolerance: float = .0003125) -> dict:
    if not 0 < tolerance < math.inf:
        raise ValueError("SVG tolerance must be positive and finite.")
    if len(text.encode()) > 16*1024*1024:
        raise ValueError("SVG exceeds the 16 MiB bounded importer budget; split the artwork.")
    root = ElementTree.fromstring(text, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    if root.tag.split("}")[-1] != "svg":
        raise ValueError("The selected file is not an SVG document.")
    vb = number_list(root.get("viewBox"), "viewBox") if "viewBox" in root.attrib else []
    if vb and (len(vb) != 4 or min(vb[2:]) <= 0):
        raise ValueError("Invalid SVG viewBox.")
    width = length(root.get("width"), vb[2]*UNITS[""] if vb else None)
    height = length(root.get("height"), vb[3]*UNITS[""] if vb else None)
    if width is None or height is None or min(width, height) <= 0:
        raise ValueError("SVG needs a positive width/height or a viewBox.")
    if not vb:
        vb = [0., 0., width/UNITS[""], height/UNITS[""]]
    sx, sy = width/vb[2], height/vb[3]
    ex, ey = 0., 0.
    aspect = root.get("preserveAspectRatio", "xMidYMid meet").split()
    if aspect and aspect[0] != "none":
        scale = max(sx, sy) if "slice" in aspect else min(sx, sy)
        ex = (width-vb[2]*scale)*(0. if "xMin" in aspect[0] else 1. if "xMax" in aspect[0] else .5)
        ey = (height-vb[3]*scale)*(0. if "YMin" in aspect[0] else 1. if "YMax" in aspect[0] else .5)
        sx = sy = scale
    base = (sx, 0., 0., -sy, ex-vb[0]*sx, height-ey+vb[1]*sy)
    if not all(math.isfinite(value) for value in base):
        raise ValueError("SVG viewport transform must be finite.")
    indexed = {el.get("id"): el for el in root.iter() if el.get("id")}
    regions, diagnostics = [], []
    css = any(el.tag.split("}")[-1] == "style" for el in root.iter())
    ignored = {"title", "desc", "metadata", "namedview", "defs", "style"}
    serial = [0]

    def number(el, name, default=0.):
        raw = el.get(name)
        if raw is None:
            return default
        if re.fullmatch(NUMBER, raw.strip()):
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError(f"SVG {name} must be finite.")
            return value
        # Physical lengths inside the viewBox are expressed in CSS user units.
        return length(raw)/UNITS[""]

    def unsupported(rid, el, message):
        regions.append({"id": rid, "name": el.get("id", rid), "polygons": [], "supported": False})
        diagnostics.append({"code": "SVG_UNSUPPORTED", "severity": "error", "region_ids": [rid],
                            "message": message, "polygons": []})

    def walk(el, parent_m, inherited, ancestry=(), use_prefix=""):
        tag = el.tag.split("}")[-1]
        if tag in ignored:
            return
        serial[0] += 1
        rid = use_prefix + (el.get("id") or f"{tag}-{serial[0]}")
        if serial[0] > 20000:
            raise ValueError("SVG exceeds the bounded element working set.")
        style = dict(inherited)
        css_geometry = set()
        for key in ("fill", "fill-rule", "display", "visibility", "opacity", "fill-opacity",
                    "clip-path", "mask", "filter"):
            if key in el.attrib:
                style[key] = el.attrib[key]
        for declaration in el.get("style", "").split(";"):
            if ":" in declaration:
                key, value = declaration.split(":", 1)
                key = key.strip()
                style[key] = value.strip()
                if key in CSS_GEOMETRY_PROPERTIES:
                    css_geometry.add(key)
        if style.get("display") == "none":
            return
        # Visibility is inherited, but a descendant can explicitly become
        # visible. Containers and <use> must therefore still be traversed.
        if tag not in ("g", "svg", "use") and style.get("visibility") in ("hidden", "collapse"):
            return
        try:
            if css_geometry:
                raise ValueError("CSS geometry properties are unsupported ("
                                 + ", ".join(sorted(css_geometry))
                                 + "); convert them to SVG attributes or flattened outlines.")
            m = mul(parent_m, transform(el.get("transform", "")))
            # These effects composite the group itself; child declarations
            # cannot undo them. Fill opacity remains inheritable below.
            if any(style.get(k, "none") not in ("none", "") for k in ("clip-path", "mask", "filter")):
                raise ValueError("Clipping, masks, and filters require flattened filled outlines.")
            if float(style.get("opacity", "1")) != 1:
                raise ValueError("Transparent artwork is unsupported; use opaque filled outlines.")
            if tag in ("g", "svg"):
                if tag == "svg" and el is not root:
                    raise ValueError("Nested SVG viewports are unsupported; flatten the document first.")
                for child in el:
                    walk(child, m, style, ancestry, use_prefix)
                return
            if tag == "use":
                href = el.get("href", el.get("{http://www.w3.org/1999/xlink}href", ""))
                if not href.startswith("#") or href[1:] not in indexed:
                    raise ValueError("Only existing local vector references are supported; external resources are never loaded.")
                if href in ancestry or len(ancestry) >= 32:
                    raise ValueError("Cyclic or excessively nested SVG reference.")
                shift = (1, 0, 0, 1, number(el, "x"), number(el, "y"))
                walk(indexed[href[1:]], mul(m, shift), style, ancestry+(href,), rid+"/")
                return
            if css:
                raise ValueError("Stylesheets are unsupported; convert CSS to inline fill properties.")
            fill = style.get("fill", "black")
            if fill == "none" or fill == "transparent":
                raise ValueError("Stroke-only artwork is unsupported; convert strokes to filled outlines.")
            if fill.startswith("url("):
                raise ValueError("Paint-server effects are unsupported; use a solid filled outline.")
            if float(style.get("fill-opacity", "1")) != 1:
                raise ValueError("Transparent artwork is unsupported; use opaque filled outlines.")
            rings = []
            if tag == "path":
                current = []
                for segment in parse_path(el.get("d", "")):
                    if isinstance(segment, Move):
                        if current:
                            rings.append(current)
                        current = [point(segment.end, m)]
                    else:
                        if not current:
                            current = [point(segment.start, m)]
                        current.extend(segment_points(segment, m, tolerance))
                if current:
                    rings.append(current)
            elif tag in ("polygon", "polyline"):
                coords = number_list(el.get("points", ""), "polygon coordinates")
                if len(coords) % 2:
                    raise ValueError("Invalid polygon coordinates.")
                ring = [point(p, m) for p in zip(coords[::2], coords[1::2])]
                if tag == "polygon" and ring and ring[-1] != ring[0]:
                    ring.append(ring[0])
                rings = [ring]
            elif tag == "rect":
                x, y = number(el, "x"), number(el, "y")
                w, h = number(el, "width"), number(el, "height")
                if min(w, h) <= 0:
                    raise ValueError("Rectangle dimensions must be positive.")
                rx = min(w/2, number(el, "rx", number(el, "ry")))
                ry = min(h/2, number(el, "ry", rx))
                if min(rx, ry) < 0:
                    raise ValueError("Rectangle corner radii must be nonnegative.")
                if rx and ry:
                    d = (f"M{x+rx},{y} H{x+w-rx} A{rx},{ry} 0 0 1 {x+w},{y+ry} "
                         f"V{y+h-ry} A{rx},{ry} 0 0 1 {x+w-rx},{y+h} "
                         f"H{x+rx} A{rx},{ry} 0 0 1 {x},{y+h-ry} "
                         f"V{y+ry} A{rx},{ry} 0 0 1 {x+rx},{y} Z")
                    ring = []
                    for seg in parse_path(d):
                        ring.extend([point(seg.end, m)] if isinstance(seg, Move) else segment_points(seg, m, tolerance))
                    rings = [ring]
                else:
                    rings = [[point(p, m) for p in [(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)]]]
            elif tag in ("circle", "ellipse"):
                cx, cy = number(el, "cx"), number(el, "cy")
                rx, ry = ((number(el, "r"),)*2 if tag == "circle" else (number(el, "rx"), number(el, "ry")))
                if min(rx, ry) <= 0:
                    raise ValueError("Ellipse radii must be positive.")
                radius = max(rx, ry)*math.hypot(*m[:4])
                n = arc_subdivisions(radius, 2*math.pi, tolerance, minimum=16)
                ring = [point((cx+rx*math.cos(2*math.pi*i/n), cy+ry*math.sin(2*math.pi*i/n)),m) for i in range(n)]
                rings = [ring+[ring[0]]]
            else:
                raise ValueError(f"Unsupported SVG content: {tag}. Text must be outlined; raster images cannot be cut.")
            if not rings:
                raise ValueError("Element has no closed filled contours.")
            geometry = filled_rings(rings, style.get("fill-rule", "nonzero"))
            if geometry.is_empty:
                raise ValueError("Element has no filled area under its fill rule.")
            regions.append({"id": rid, "name": el.get("id", rid),
                            "polygons": polygons_data(geometry), "supported": True,
                            "fill": fill})
        except (ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
            unsupported(rid, el, str(exc))

    walk(root, base, {})
    all_geometry = unary_union([Polygon(p["outer"], p["holes"]) for region in regions
                                for p in region["polygons"]])
    return {"schema_version": 1, "source_hash": hashlib.sha256(text.encode()).hexdigest(),
            "regions": regions, "diagnostics": diagnostics, "size": [width, height],
            "bounds": list(all_geometry.bounds) if not all_geometry.is_empty else [0,0,width,height],
            "flattening_tolerance": tolerance,
            "size_assumed": not(root.get("width") and root.get("height"))}
