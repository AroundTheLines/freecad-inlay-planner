"""Independent scalar stock membership for constant-section prism probes.

No production geometry or polygon library is imported.  Boundary uncertainty
is retained explicitly: motion through absent material is not a zero-depth
cut in an imaginary enclosing slab.  These are local probe oracles, not a
whole-volume assembly certificate.
"""

from dataclasses import dataclass
import math

from .motion import DepthInterval, closest_fraction, operations_removal


def ring_edges(ring):
    if len(ring) < 3:
        raise ValueError("A stock ring needs at least three vertices")
    return zip(ring, ring[1:] + ring[:1])


def point_segment_distance(point, start, end):
    t = closest_fraction(point, start, end)
    return math.hypot(point[0]-start[0]-t*(end[0]-start[0]),
                      point[1]-start[1]-t*(end[1]-start[1]))


def ring_contains(point, ring):
    """Odd/even ray test; callers handle the explicit boundary band first."""
    x, y = point
    inside = False
    for a, b in ring_edges(ring):
        if (a[1] > y) != (b[1] > y):
            intercept = a[0] + (y-a[1])*(b[0]-a[0])/(b[1]-a[1])
            if intercept > x:
                inside = not inside
    return inside


@dataclass(frozen=True)
class StockMembership:
    state: str  # material | air | uncertain
    nominal_material: bool
    boundary_distance: float


class PolygonStock:
    def __init__(self, records, allowance=0., arithmetic_reserve=1e-9):
        if not records or not math.isfinite(allowance) or allowance < 0:
            raise ValueError("Nonempty stock and finite nonnegative allowance required")
        self.records = records
        self.allowance = allowance
        self.arithmetic_reserve = arithmetic_reserve
        self.rings = [ring for record in records
                      for ring in [record["outer"], *record.get("holes", [])]]
        for ring in self.rings:
            if len(ring) < 3 or not all(math.isfinite(v) for p in ring for v in p):
                raise ValueError("Stock rings must have finite vertices")

    def membership(self, point):
        distance = min(point_segment_distance(point, a, b)
                       for ring in self.rings for a, b in ring_edges(ring))
        nominal = any(ring_contains(point, record["outer"])
                      and not any(ring_contains(point, hole) for hole in record.get("holes", []))
                      for record in self.records)
        state = ("uncertain" if distance <= self.allowance+self.arithmetic_reserve
                 else "material" if nominal else "air")
        return StockMembership(state, nominal, distance)


@dataclass(frozen=True)
class MaterialProbe:
    state: str
    removed_depth: DepthInterval | None


def material_removal(point, stock, operations, cutters, origin=(0, 0, 0)):
    state = stock.membership(point).state
    if state != "material":
        return MaterialProbe(state, None)
    return MaterialProbe(state, operations_removal(point, operations, cutters, origin))


def local_assembly_gap(board, plug, seating_depth):
    """Return no numerical gap for absent or uncertain stock columns."""
    if "air" in (board.state, plug.state):
        return {"state": "no_common_material", "gap": None}
    if "uncertain" in (board.state, plug.state):
        return {"state": "uncertain", "gap": None}
    a, b = board.removed_depth, plug.removed_depth
    return {"state": "common_material", "gap": DepthInterval(
        a.lower+b.lower-seating_depth, a.upper+b.upper-seating_depth,
        a.certified and b.certified, a.subdivisions+b.subdivisions)}


def assert_capsule_inside_rectangle(start, end, radius, rectangle):
    """Entire constant-radius straight sweep stays in a known convex void."""
    xmin, ymin, xmax, ymax = rectangle
    if radius < 0 or not all(xmin+radius < x < xmax-radius
                             and ymin+radius < y < ymax-radius for x, y in (start, end)):
        raise AssertionError("The complete cutter capsule is not strictly inside the void")


def circle_chord_error(ring, centre, radius, tolerance=1e-8):
    """Exact radial sagitta of chords whose endpoints lie on a known circle."""
    endpoint_error = max(abs(math.dist(point, centre)-radius) for point in ring)
    if endpoint_error > tolerance:
        raise AssertionError("Captured vertices do not lie on the known native circle")
    return max(radius-point_segment_distance(centre, a, b) for a, b in ring_edges(ring))
