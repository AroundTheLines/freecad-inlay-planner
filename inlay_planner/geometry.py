"""Planar geometry and analytic cutter/target profiles (millimetres).

No FreeCAD objects enter this module.  Polygon buffers are approximations with
an explicit chord-error budget; display meshes are never machining evidence.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from shapely import affinity
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box
from shapely.ops import unary_union


def polygons(value):
    if value.is_empty:
        return []
    if value.geom_type == "Polygon":
        return [value]
    return [p for item in getattr(value, "geoms", ()) for p in polygons(item)]


def from_records(records):
    items = []
    for record in records:
        p = Polygon(record["outer"], record.get("holes", []))
        if not p.is_valid:
            raise ValueError("Artwork contains an invalid or self-intersecting polygon")
        if not p.is_empty and p.area > 0:
            items.append(p)
    return unary_union(items) if items else GeometryCollection()


def records(value):
    return [{"outer": [[float(x), float(y)] for x, y in p.exterior.coords],
             "holes": [[[float(x), float(y)] for x, y in r.coords]
                       for r in p.interiors]} for p in polygons(value)]


def stock_outline(face):
    """Nominal constant-section material; an enclosing box is not the stock."""
    extent = box(0., 0., *face["stock"][:2])
    if "stock_outline" not in face:
        return extent
    shape = from_records(face["stock_outline"])
    if shape.is_empty:
        raise ValueError("The selected stock outline has no material")
    scale = max(face["stock"][:2])
    coordinate_error = max(1e-10, math.ulp(scale)*32)
    if not extent.buffer(coordinate_error).covers(shape):
        raise ValueError("The selected stock outline extends beyond its physical enclosing dimensions")
    return shape.intersection(extent)


def stock_enclosure(face, error, outer=True):
    """Material enclosure for an explicitly bounded native outline capture.

    The capture contract guarantees erosion(P, delta) is material and that
    dilation(P, delta) contains all material. A circumscribed circular offset
    plus the numerical reserve preserves those directions around holes too.
    """
    shape = stock_outline(face)
    delta = face.get("stock_outline_tolerance", 0.)
    if delta < 0:
        raise ValueError("Stock outline tolerance cannot be negative")
    if delta == 0:
        return shape
    n = quadrant_segments(delta, error/4)
    radius = (delta+error)/math.cos(math.pi/(4*n))
    enclosed = shape.buffer(radius if outer else -radius, quad_segs=n)
    return enclosed.intersection(box(0., 0., *face["stock"][:2]))


def transform(value, matrix, inverse=False):
    a, b, c, d, e, f = matrix
    if inverse:
        det = a*d-b*c
        return affinity.affine_transform(value, [d/det, -c/det, -b/det,
                                                a/det, (c*f-d*e)/det,
                                                (b*e-a*f)/det])
    return affinity.affine_transform(value, [a, c, b, d, e, f])


def quadrant_segments(radius, error):
    if radius <= error:
        return 8
    angle = math.acos(max(-1., 1-error/radius))
    if angle == 0:
        raise ValueError("The requested geometry precision is below floating-point representability")
    required = max(8, math.ceil(math.pi/(4*angle)))
    if required > 4096:
        raise ValueError("This radius/resolution requires more curve subdivisions than the bounded geometry budget; no resolution was relaxed")
    return required


def offset(value, distance, error):
    if not distance:
        return value
    return value.buffer(distance, quad_segs=quadrant_segments(abs(distance), error))


def disk_buffer(value, radius, error, outer=False):
    """Inner/outer enclosures of a circular sweep, plus GEOS error reserve."""
    if radius <= 0:
        return value.buffer(error if outer else 0)
    n = quadrant_segments(radius, error/4)
    r = (radius+error)/math.cos(math.pi/(4*n)) if outer else max(0., radius-error)
    return value.buffer(r, quad_segs=n)


def compact_workspace(value, tolerance):
    """Compact each ring with small local containment proofs.

    A single global predicate over hundreds of near-coincident holes creates
    a large GEOS intersection graph. An inset outer ring and enlarged holes
    instead establish the same subset relation independently, ring by ring.
    """
    result = []
    for polygon in polygons(value):
        exterior = Polygon(polygon.exterior)
        outer = exterior.simplify(tolerance, preserve_topology=True)
        if not exterior.covers(outer):
            outer = exterior.buffer(-2*tolerance).simplify(tolerance, preserve_topology=True)
        if outer.is_empty or outer.geom_type != 'Polygon' or not exterior.covers(outer):
            result.append(polygon)
            continue
        holes = []
        for ring in polygon.interiors:
            original = Polygon(ring)
            hole = original.simplify(tolerance, preserve_topology=True)
            if not hole.covers(original):
                hole = original.buffer(2*tolerance).simplify(tolerance, preserve_topology=True)
            if hole.geom_type != 'Polygon' or not hole.covers(original):
                hole = original
            holes.append(hole.exterior.coords)
        compact = Polygon(outer.exterior.coords, holes)
        result.append(compact if compact.is_valid else polygon)
    return result[0] if len(result) == 1 else MultiPolygon(result)


def cutter_radius(tool, axial_height):
    if axial_height < 0:
        return 0.
    R = tool["diameter"]/2
    if tool["shape"] == "flat":
        return R
    if tool["shape"] == "v":
        t = math.tan(math.radians(tool["included_angle_deg"])/2)
        return min(R, tool.get("tip_diameter", 0.)/2 + axial_height*t)
    r = tool["corner_radius"]
    if axial_height >= r:
        return R
    return R-r+math.sqrt(max(0., 2*r*axial_height-axial_height**2))


def cutter_profile(tool, radial_distance):
    R = tool["diameter"]/2
    if radial_distance > R:
        return math.inf
    if tool["shape"] == "flat":
        return 0.
    if tool["shape"] == "v":
        return max(0., radial_distance-tool.get("tip_diameter", 0.)/2) / math.tan(
            math.radians(tool["included_angle_deg"])/2)
    r = tool["corner_radius"]
    delta = max(0., radial_distance-(R-r))
    return r-math.sqrt(max(0., r*r-delta*delta))


@dataclass
class Target:
    design: object
    face_name: str
    face: dict
    style: str
    depth: float
    height: float
    seating: float
    side: float
    finishing_tool: dict
    error: float

    def __post_init__(self):
        self.art = transform(self.design, self.face["design_to_face"])
        self.stock = stock_enclosure(self.face, self.error, outer=True)
        self.guaranteed_stock = stock_enclosure(self.face, self.error, outer=False)
        self.bounds = box(*self.face["bounds"])
        self.limit = self.depth if self.face_name == "board" else self.height
        self._slice_cache = {}

    def boundary_offset(self, d):
        amount = 0.
        if self.style == "v":
            theta = math.radians(self.finishing_tool["included_angle_deg"])/2
            amount = -d*math.tan(theta) if self.face_name == "board" else (
                (d-self.seating)*math.tan(theta)-self.side/math.cos(theta))
        elif self.style == "bull":
            r = self.finishing_tool["corner_radius"]
            if self.face_name == "board":
                a = max(0., d-(self.depth-r))
                amount = -(r-math.sqrt(max(0., r*r-a*a))) if a else 0.
            else:
                crown = r-math.sqrt(max(0., 2*r*d-d*d)) if d < r else 0.
                a = max(0., d-(self.height-r))
                root = r-math.sqrt(max(0., r*r-a*a)) if a else 0.
                amount = -self.side-crown+root
        elif self.face_name == "plug":
            amount = -self.side
        return amount

    def removable(self, d):
        if d < 0:
            return self.stock
        if d > self.limit+1e-9:
            return GeometryCollection()
        # Every in-range flat target section is identical. Reusing that
        # immutable polygon avoids retaining many copies of dense artwork.
        key = 0. if self.style == "flat" else round(d, 10)
        if key in self._slice_cache:
            return self._slice_cache[key]
        amount = self.boundary_offset(d)
        shape = offset(self.art, amount, self.error/4)
        value = shape if self.face_name == "board" else self.stock.difference(shape)
        value = value.intersection(self.bounds).intersection(self.stock)
        if len(self._slice_cache) >= 64:
            self._slice_cache.pop(next(iter(self._slice_cache)))
        self._slice_cache[key] = value
        return value

    def centres(self, tool, h, xy_margin, axial_step):
        """Conservative full-profile cutter domain, including air off stock.

        Over an axial interval the deeper removable slice is smaller, while
        the cutter radius at the upper endpoint is larger. Their combination
        therefore encloses the forbidden contact for every intermediate slice.
        """
        R = tool["diameter"]/2
        world = box(-R-1, -R-1, self.face["stock"][0]+R+1,
                    self.face["stock"][1]+R+1)
        if tool["shape"] == "flat":
            heights = [0., h]
        else:
            n = max(1, math.ceil(h/axial_step))
            heights = [h*i/n for i in range(n+1)]
        extrema = []
        for i in range(len(heights)-1):
            a0, a1 = heights[i:i+2]
            amount = self.boundary_offset(max(0., h-a0))
            r = cutter_radius(tool, a1) + xy_margin + self.error
            extrema.append(amount-r if self.face_name == "board" else amount+r)
        # All target slices are offsets of the same polygon. Successive
        # erosions compose; erosion followed by dilation is contained in the
        # corresponding direct offset (the opening inclusion). Thus the
        # scalar extremum gives a conservative domain without hundreds of
        # intermediate GEOS buffers per layer, including polygons with holes.
        if self.face_name == "board":
            domain = offset(self.art, min(extrema), self.error/4)
        else:
            forbidden = offset(self.art, max(extrema), self.error/4)
            domain = world.difference(forbidden)
        engaged_radius = cutter_radius(tool, h)+xy_margin+self.error
        forbidden_stock = self.stock.difference(self.bounds)
        if not forbidden_stock.is_empty:
            domain = domain.difference(disk_buffer(forbidden_stock, engaged_radius, self.error, outer=True))
        domain = domain.intersection(offset(self.stock, R, self.error))
        # Repeated rounded offsets can create many sub-resolution vertices.
        # Compact only the legal centre workspace, never the desired artwork.
        # The explicit containment predicate retains its conservative meaning;
        # a failed or collapsed candidate simply leaves the original domain.
        return compact_workspace(domain, self.error/4)


def preview_mesh(surface_depth, stock, max_axis=32, outline=None):
    """Bounded, explicitly display-only grid mesh."""
    w, h = stock[:2]
    nx = max(2, min(max_axis, math.ceil(w/max(w, h)*max_axis)))
    ny = max(2, min(max_axis, math.ceil(h/max(w, h)*max_axis)))
    vertices = [[w*i/nx, h*j/ny, float(surface_depth(w*i/nx, h*j/ny))]
                for j in range(ny+1) for i in range(nx+1)]
    faces = []
    for j in range(ny):
        for i in range(nx):
            k = j*(nx+1)+i
            for triangle in ([k, k+1, k+nx+2], [k, k+nx+2, k+nx+1]):
                if outline is None or outline.covers(Polygon([vertices[n][:2] for n in triangle])):
                    faces.append(triangle)
    return {"vertices": vertices, "faces": faces, "display_only": True}
