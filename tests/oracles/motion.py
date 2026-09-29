"""Small-case analytic motion checks independent of the production planner.

These functions certify only their stated scope: exact constant-depth line
sweeps, bounded ramp sweeps at specified XY probes, and explicit command
invariants. A finite probe set is not a whole-surface certificate.
"""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Cutter:
    shape: str
    radius: float
    cutting_length: float
    corner_radius: float = 0.0
    included_angle_deg: float = 60.0
    tip_radius: float = 0.0

    def __post_init__(self):
        numbers = (self.radius, self.cutting_length, self.corner_radius,
                   self.included_angle_deg, self.tip_radius)
        if not all(math.isfinite(x) for x in numbers):
            raise ValueError("Cutter dimensions must be finite")
        if self.shape not in {"flat", "v", "bull"}:
            raise ValueError("Unsupported cutter shape")
        if self.radius <= 0 or self.cutting_length <= 0:
            raise ValueError("Radius and cutting length must be positive")
        if self.shape == "bull" and not 0 < self.corner_radius < self.radius:
            raise ValueError("Bull nose must have a positive corner and flat centre")
        if self.shape == "v" and not (
            0 < self.included_angle_deg < 180 and 0 <= self.tip_radius < self.radius
        ):
            raise ValueError("Invalid V angle or flat tip")

    @classmethod
    def from_snapshot(cls, tool):
        return cls(tool["shape"], tool["diameter"] / 2,
                   tool["cutting_length"], tool.get("corner_radius", 0),
                   tool.get("included_angle_deg", 60),
                   tool.get("tip_diameter", 0) / 2)

    def lower_surface(self, distance):
        """Height above the tip at radius distance; infinity outside cutter."""
        if distance < 0:
            raise ValueError("Radial distance cannot be negative")
        if distance > self.radius:
            return math.inf
        if self.shape == "flat":
            return 0.0
        if self.shape == "v":
            return max(0.0, distance - self.tip_radius) / math.tan(
                math.radians(self.included_angle_deg / 2))
        flat_radius = self.radius - self.corner_radius
        if distance <= flat_radius:
            return 0.0
        return self.corner_radius - math.sqrt(max(
            0.0, self.corner_radius ** 2 - (distance - flat_radius) ** 2))

    def radius_at_height(self, height):
        """Radius of the cutting profile, excluding an unmodelled shank."""
        if height < 0:
            return 0.0
        if self.shape == "flat":
            return self.radius
        if self.shape == "v":
            return min(self.radius, self.tip_radius + height * math.tan(
                math.radians(self.included_angle_deg / 2)))
        if height >= self.corner_radius:
            return self.radius
        return self.radius - self.corner_radius + math.sqrt(
            2 * self.corner_radius * height - height * height)


@dataclass(frozen=True)
class DepthInterval:
    lower: float
    upper: float
    certified: bool
    subdivisions: int = 0


def closest_fraction(point, start, end):
    vx, vy = end[0] - start[0], end[1] - start[1]
    length2 = vx * vx + vy * vy
    if length2 == 0:
        return 0.0
    return min(1.0, max(0.0, (
        (point[0] - start[0]) * vx + (point[1] - start[1]) * vy) / length2))


def interpolate(start, end, fraction):
    return tuple(a + fraction * (b - a) for a, b in zip(start, end))


def segment_removal(point, start, end, cutter, tolerance=1e-7, max_nodes=20000):
    """Bound removed depth at one XY point for a line in positive-depth XYZ.

    The upper bound uses the lowest radial distance and greatest tip depth on
    each parameter interval. It remains conservative at the bull-nose's
    vertical tangent and at the cutter's outer radius. Only arithmetic
    roundoff remains outside the mathematical interval construction.
    """
    if tolerance <= 0 or max_nodes < 1:
        raise ValueError("Positive tolerance and node limit required")

    def removal_at(t):
        x, y, depth = interpolate(start, end, t)
        return max(0.0, depth - cutter.lower_surface(math.hypot(x-point[0], y-point[1])))

    if start[2] == end[2]:
        t = closest_fraction(point, start, end)
        value = removal_at(t)
        return DepthInterval(value, value, True)

    def bound(left, right):
        a, b = interpolate(start, end, left), interpolate(start, end, right)
        near = interpolate(a, b, closest_fraction(point, a, b))
        distance = math.hypot(near[0]-point[0], near[1]-point[1])
        upper = max(0.0, max(a[2], b[2]) - cutter.lower_surface(distance))
        near_t = left + (right-left) * closest_fraction(point, a, b)
        lower = max(removal_at(left), removal_at(right), removal_at((left+right)/2),
                    removal_at(near_t))
        return lower, upper

    lower, upper = bound(0.0, 1.0)
    pending = [(upper, 0.0, 1.0)]
    nodes = 0
    while pending:
        index = max(range(len(pending)), key=lambda i: pending[i][0])
        highest, left, right = pending.pop(index)
        if highest <= lower + tolerance:
            return DepthInterval(lower, max(lower, highest), True, nodes)
        if nodes >= max_nodes:
            return DepthInterval(lower, max(highest, lower), False, nodes)
        middle = (left + right) / 2
        for a, b in ((left, middle), (middle, right)):
            local_lower, local_upper = bound(a, b)
            lower = max(lower, local_lower)
            pending.append((local_upper, a, b))
        nodes += 1
    return DepthInterval(lower, lower, True, nodes)


def stock_points(moves, origin=(0, 0, 0)):
    """Convert contract WCS positions into stock XY and positive depth."""
    return [(float(m["x"]) + origin[0], float(m["y"]) + origin[1],
             -float(m["z"]) - origin[2]) for m in moves]


def path_removal(point, moves, cutter, origin=(0, 0, 0), tolerance=1e-7):
    """Actual cutting-envelope removal, including unsafe links if present."""
    points = stock_points(moves, origin)
    intervals = [segment_removal(point, a, b, cutter, tolerance)
                 for a, b in zip(points, points[1:])]
    return DepthInterval(max((x.lower for x in intervals), default=0),
                         max((x.upper for x in intervals), default=0),
                         all(x.certified for x in intervals),
                         sum(x.subdivisions for x in intervals))


def operations_removal(point, operations, cutters, origin=(0, 0, 0), tolerance=1e-7):
    """Union actual removal across different physical cutters on one face.

    Operations stay separate: no imaginary segment joins the last position
    of one use with the first position of the next tool use.
    """
    intervals = [path_removal(point, operation["moves"], cutters[operation["tool_id"]],
                              origin, tolerance) for operation in operations]
    return DepthInterval(max((value.lower for value in intervals), default=0),
                         max((value.upper for value in intervals), default=0),
                         all(value.certified for value in intervals),
                         sum(value.subdivisions for value in intervals))


def assert_probe_stepdown(points, operations, cutters, origin=(0, 0, 0), tolerance=1e-6):
    """Check newly removed axial depth against prior cuts at fixed probes.

    This checks actual ordered cutter envelopes and effective per-use limits;
    it does not replace a complete engagement proof between the probe points.
    """
    previous = [DepthInterval(0, 0, True) for _ in points]
    for operation in operations:
        cutter = cutters[operation["tool_id"]]
        maximum = operation["settings"]["max_stepdown"]
        if not math.isfinite(maximum) or maximum <= 0:
            raise ValueError("Positive finite effective stepdown required")
        positions = stock_points(operation["moves"], origin)
        for segment, (a, b) in enumerate(zip(positions, positions[1:]), 1):
            for index, point in enumerate(points):
                contact = segment_removal(point, a, b, cutter, tolerance/8)
                before = previous[index]
                if not contact.certified or contact.upper-before.lower > maximum+tolerance:
                    raise AssertionError(
                        f"{operation.get('id', operation['tool_id'])} segment {segment}: "
                        f"new material at {point} may exceed stepdown {maximum}")
                previous[index] = DepthInterval(max(before.lower, contact.lower),
                                                 max(before.upper, contact.upper), True)


def assert_depth_probes(moves, cutter, probes, origin=(0, 0, 0), tolerance=1e-6):
    """Assert (x, y, expected depth) fixtures; does not certify between probes."""
    for x, y, expected in probes:
        interval = path_removal((x, y), moves, cutter, origin, tolerance/8)
        if not interval.certified or interval.lower < expected-tolerance or interval.upper > expected+tolerance:
            raise AssertionError(f"Removal at {(x, y)}: {interval}; expected {expected}")


def segment_engaged_bounds(start, end, cutter):
    """Conservative XY envelope where a line's cutting profile reaches stock."""
    if max(start[2], end[2]) <= 0:
        return None
    a, b = start, end
    if a[2] < 0:
        a = interpolate(start, end, -start[2]/(end[2]-start[2]))
    if b[2] < 0:
        b = interpolate(start, end, -start[2]/(end[2]-start[2]))
    radius = cutter.radius_at_height(max(a[2], b[2]))
    return (min(a[0], b[0])-radius, min(a[1], b[1])-radius,
            max(a[0], b[0])+radius, max(a[1], b[1])+radius)


def assert_sweep_bounds(moves, cutter, bounds, origin=(0, 0, 0), tolerance=1e-9, stock_bounds=None):
    """Certify rectangular bounds, optionally clipping the sweep to real stock.

    Off-stock air is not material removed outside the user's selected bounds.
    If stock_bounds is omitted, surrounding stock is conservatively assumed.
    """
    points = stock_points(moves, origin)
    for i, (a, b) in enumerate(zip(points, points[1:]), 1):
        envelope = segment_engaged_bounds(a, b, cutter)
        if envelope and stock_bounds is not None:
            envelope = (max(envelope[0], stock_bounds[0]), max(envelope[1], stock_bounds[1]),
                        min(envelope[2], stock_bounds[2]), min(envelope[3], stock_bounds[3]))
            if envelope[0] > envelope[2] or envelope[1] > envelope[3]:
                continue
        if envelope and (envelope[0] < bounds[0]-tolerance or envelope[1] < bounds[1]-tolerance
                         or envelope[2] > bounds[2]+tolerance or envelope[3] > bounds[3]+tolerance):
            raise AssertionError(f"Segment {i} envelope {envelope} exceeds {bounds}")


def assert_air_travel(moves, origin=(0, 0, 0), tolerance=1e-9):
    """Strict fixture oracle for plans declaring all lateral travel above stock.

    This intentionally does not certify links through previously cleared
    material; those require a separate prior-stock certificate.
    """
    points = stock_points(moves, origin)
    for i, (a, b, move) in enumerate(zip(points, points[1:], moves[1:]), 1):
        lateral = math.hypot(a[0]-b[0], a[1]-b[1]) > tolerance
        if lateral and (move["kind"] == "rapid" or move["role"] == "travel"):
            if max(a[2], b[2]) > tolerance:
                raise AssertionError(f"Noncutting lateral segment {i} enters stock")


def assert_fixture_reach(moves, cutter, stock_thickness, origin=(0, 0, 0), tolerance=1e-9):
    """Conservative reach/backing check for flat-stock, non-through fixtures.

    The fixture does not assume that prior cuts grant unmodelled extra shank
    reach. All linear-segment depths lie between their endpoint depths.
    """
    if stock_thickness <= 0:
        raise ValueError("Positive stock thickness required")
    for index, (_, _, depth) in enumerate(stock_points(moves, origin)):
        if depth > cutter.cutting_length + tolerance:
            raise AssertionError(f"Move {index} exceeds declared cutting reach")
        if depth >= stock_thickness - tolerance:
            raise AssertionError(f"Move {index} cuts through required backing")


def assert_lattice(moves, xy, z, tolerance=1e-8):
    for axis, step in (("x", xy), ("y", xy), ("z", z)):
        if step <= 0 or not math.isfinite(step):
            raise ValueError("Resolution must be finite and positive")
        for i, move in enumerate(moves):
            value = float(move[axis])
            if not math.isfinite(value) or abs(value-round(value/step)*step) > tolerance:
                raise AssertionError(f"Move {i} {axis}={value} is off {step} lattice")


def motion_seconds(moves):
    """Independent constant-feed timing; excludes unknown initial positioning."""
    cut = travel = 0.0
    for start, end in zip(moves, moves[1:]):
        distance = math.sqrt(sum((float(end[k])-float(start[k]))**2 for k in ("x", "y", "z")))
        feed = float(end["feed_mm_min"])
        if distance and (not math.isfinite(feed) or feed <= 0):
            raise AssertionError("Moving segment has no positive effective feed")
        duration = distance/feed*60 if distance else 0.0
        if end["role"] == "travel" or end["kind"] == "rapid":
            travel += duration
        else:
            cut += duration
    return {"cut_s": cut, "travel_s": travel, "total_s": cut+travel}


def face_counts(operations):
    result = {}
    for face in ("board", "plug"):
        tools = [op["tool_id"] for op in operations if op["face"] == face]
        result[face] = {"operations": len(tools), "tool_changes": sum(
            a != b for a, b in zip(tools, tools[1:]))}
    return result


def check_result_contract(spec, result):
    """Non-geometric final-motion audit; return failures, never readiness by itself."""
    issues = []
    if result.get("status") != "ready":
        return [f"Result is {result.get('status')}, not ready"]
    if result.get("revision") != spec["revision"] or result.get("input_hash") != spec["input_hash"]:
        issues.append("Result does not match its input revision/hash")
    tools = {t["id"]: t for t in spec["tools"]}
    operations = result["operations"]
    counts = face_counts(operations)
    if counts != result.get("per_face_counts"):
        issues.append("Reported per-face counts differ from motion sequence")
    for face in ("board", "plug"):
        limits = spec["faces"][face]["limits"]
        for name in ("operations", "tool_changes"):
            if limits.get(name) is not None and counts[face][name] > limits[name]:
                issues.append(f"{face} exceeds {name} limit")
    for operation in operations:
        name = operation["id"]
        if operation["tool_id"] not in tools:
            issues.append(f"{name}: unknown physical tool")
            continue
        try:
            assert_lattice(operation["moves"], spec["resolution"]["xy"], spec["resolution"]["z"])
            measured = motion_seconds(operation["moves"])
            timing = operation["time"]
            if not math.isclose(measured["total_s"], timing["optimized_s"], abs_tol=1e-6, rel_tol=1e-8):
                issues.append(f"{name}: reported time differs from independent feed model")
            if timing["optimized_s"] > timing["baseline_s"] + 1e-7:
                issues.append(f"{name}: optimization is slower than its baseline")
        except (AssertionError, ValueError, KeyError) as error:
            issues.append(f"{name}: {error}")
    return issues


def apply_affine(point, transform):
    a, b, c, d, e, f = transform
    return (a*point[0]+c*point[1]+e, b*point[0]+d*point[1]+f)


def inverse_affine(point, transform):
    a, b, c, d, e, f = transform
    determinant = a*d-b*c
    if abs(determinant) < 1e-15:
        raise ValueError("Singular setup transform")
    x, y = point[0]-e, point[1]-f
    return ((d*x-c*y)/determinant, (-b*x+a*y)/determinant)


def assert_registration(canonical_points, board_points, plug_points, board_transform,
                        plug_transform, tolerance=1e-9):
    if not len(canonical_points) == len(board_points) == len(plug_points):
        raise AssertionError("Registration marker count differs")
    for reference, board, plug in zip(canonical_points, board_points, plug_points):
        for local, transform in ((board, board_transform), (plug, plug_transform)):
            actual = inverse_affine(local, transform)
            if math.dist(reference, actual) > tolerance:
                raise AssertionError(f"Assembly registration differs: {actual} != {reference}")


def paired_probe_gap(canonical_point, board_moves, plug_moves, board_cutter, plug_cutter,
                     board_face, plug_face, seating_depth, tolerance=1e-7):
    """Bound vertical wood-to-wood separation at a canonical XY probe.

    Valid for the plan's two flat, flipped, 2.5D heightfield stocks at the
    nominated pose. Negative values are rigid interference. This does not
    prove fit at unsampled points or account for absent/through-cut stock.
    """
    board_xy = apply_affine(canonical_point, board_face["design_to_face"])
    plug_xy = apply_affine(canonical_point, plug_face["design_to_face"])
    board = path_removal(board_xy, board_moves, board_cutter, board_face["origin"], tolerance)
    plug = path_removal(plug_xy, plug_moves, plug_cutter, plug_face["origin"], tolerance)
    return DepthInterval(board.lower+plug.lower-seating_depth,
                         board.upper+plug.upper-seating_depth,
                         board.certified and plug.certified,
                         board.subdivisions+plug.subdivisions)
