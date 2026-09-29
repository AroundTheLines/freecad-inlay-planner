"""Independent, deliberately narrow LinuxCNC G0/G1 replay for test evidence.

Unknown coordinate-changing codes raise an error. This is a verifier, not an
exporter, machine controller, or general LinuxCNC interpreter.
"""

from dataclasses import dataclass, field
from decimal import Decimal
import re


D = Decimal
WORD = re.compile(r"([A-Za-z])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))")


class UnsupportedProgram(ValueError):
    pass


@dataclass(frozen=True)
class Motion:
    line: int
    kind: str
    start: tuple
    end: tuple
    feed_mm_min: Decimal | None
    spindle_rpm: Decimal
    spindle_mode: str
    tool: int | None
    work_offset: str
    length_offset: int | None
    path_control: str | None
    blend_tolerance_mm: Decimal | None

    @property
    def complete(self):
        return all(value is not None for value in self.start + self.end)


@dataclass
class Program:
    motions: list = field(default_factory=list)
    tool_loads: list = field(default_factory=list)
    units_seen: list = field(default_factory=list)
    final_position: tuple = (None, None, None)

    @property
    def tool_changes(self):
        return sum(a != b for a, b in zip(self.tool_loads, self.tool_loads[1:]))


def _without_comments(line):
    output = []
    depth = 0
    for char in line:
        if char == ";" and depth == 0:
            break
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                raise UnsupportedProgram("Unmatched comment terminator")
            depth -= 1
        elif depth == 0:
            output.append(char)
    if depth:
        raise UnsupportedProgram("Unclosed comment")
    return "".join(output).strip()


def parse_program(text, initial_position=None):
    """Replay ordinary metric/inch, absolute/incremental linear CAM motion.

    Positions and feeds are exact Decimal mm and mm/min values. An unknown
    initial axis remains None, so an unverified initial diagonal cannot be
    silently treated as an approach from the origin.
    """
    result = Program()
    position = tuple(D(str(x)) for x in initial_position) if initial_position else (None, None, None)
    scale = D(1)
    absolute = True
    motion = None
    feed = None
    spindle = D(0)
    spindle_mode = "M5"
    selected_tool = active_tool = None
    work_offset = "G54"
    path_control = None
    blend = None
    length_offset = None
    ended = False

    for number, original in enumerate(text.splitlines(), 1):
        line = _without_comments(original)
        if not line or line == "%":
            continue
        if ended:
            raise UnsupportedProgram(f"Line {number}: executable content after program end")
        words = [(m.group(1).upper(), D(m.group(2))) for m in WORD.finditer(line)]
        if WORD.sub("", line).strip():
            raise UnsupportedProgram(f"Line {number}: unsupported syntax {original!r}")
        g_codes = [value for word, value in words if word == "G"]
        m_codes = [value for word, value in words if word == "M"]
        axes = {word: value for word, value in words if word in {"X", "Y", "Z"}}
        params = {word: value for word, value in words if word not in {"G", "M", "N"}}
        if len(axes) != sum(word in {"X", "Y", "Z"} for word, _ in words):
            raise UnsupportedProgram(f"Line {number}: repeated axis word")
        allowed_words = {"G", "M", "N", "X", "Y", "Z", "F", "S", "T", "H", "P", "Q"}
        if any(word not in allowed_words for word, _ in words):
            raise UnsupportedProgram(f"Line {number}: unsupported address")
        for value in g_codes:
            if value == 20:
                scale = D("25.4")
                result.units_seen.append("inch")
            elif value == 21:
                scale = D(1)
                result.units_seen.append("mm")
            elif value == 90:
                absolute = True
            elif value == 91:
                absolute = False
            elif value in (0, 1):
                motion = "rapid" if value == 0 else "feed"
            elif value in (D(61), D("61.1")):
                path_control, blend = f"G{value}", None
            elif value == 64:
                path_control = "G64"
                blend = params.get("P") * scale if "P" in params else None
            elif value in (D(54), D(55), D(56), D(57), D(58), D(59), D("59.1"), D("59.2"), D("59.3")):
                next_offset = f"G{value}"
                if next_offset != work_offset:
                    position = (None, None, None)
                work_offset = next_offset
            elif value == 43:
                register = params.get("H")
                if register is None or register < 0 or register != register.to_integral_value():
                    raise UnsupportedProgram(f"Line {number}: G43 needs a valid length register")
                length_offset = int(register)
            elif value == 49:
                length_offset = None
            elif value in (17, 40, 80, 94):
                pass  # Plane, no radius compensation, length compensation, or feed/min.
            else:
                raise UnsupportedProgram(f"Line {number}: unmodelled G{value}")
        if "F" in params:
            feed = params["F"] * scale
        if "S" in params:
            spindle = params["S"]
        if "T" in params:
            if params["T"] != params["T"].to_integral_value() or params["T"] < 0:
                raise UnsupportedProgram(f"Line {number}: invalid tool number")
            selected_tool = int(params["T"])
        for value in m_codes:
            if value == 6:
                if selected_tool is None:
                    raise UnsupportedProgram(f"Line {number}: M6 has no selected tool")
                active_tool = selected_tool
                result.tool_loads.append(active_tool)
                # LinuxCNC M6 stops the spindle. A prior M3 cannot authorize
                # cutting with a newly loaded tool without another start.
                spindle_mode = "M5"
            elif value in (3, 4, 5):
                spindle_mode = f"M{value}"
            elif value in (2, 30):
                ended = True
            elif value in (0, 1, 7, 8, 9):
                pass
            else:
                raise UnsupportedProgram(f"Line {number}: unmodelled M{value}")
        if axes:
            if motion is None:
                raise UnsupportedProgram(f"Line {number}: axes without known motion mode")
            end = list(position)
            for index, axis in enumerate(("X", "Y", "Z")):
                if axis in axes:
                    amount = axes[axis] * scale
                    end[index] = amount if absolute else (
                        position[index] + amount if position[index] is not None else None)
            result.motions.append(Motion(number, motion, position, tuple(end), feed, spindle,
                                         spindle_mode, active_tool, work_offset, length_offset,
                                         path_control, blend))
            position = tuple(end)
    result.final_position = position
    return result


def audit_program(program, xy_resolution, z_resolution, tolerance_mm="0", require_exact_path=True):
    """Return specific modal/lattice errors, without claiming material coverage."""
    issues = []
    tolerance = D(str(tolerance_mm))
    steps = (D(str(xy_resolution)), D(str(xy_resolution)), D(str(z_resolution)))
    if any(step <= 0 for step in steps) or tolerance < 0:
        raise ValueError("Positive resolution and nonnegative tolerance required")
    if not program.units_seen:
        issues.append("Output does not explicitly establish metric/imperial units")
    for move in program.motions:
        if require_exact_path and move.path_control not in {"G61", "G61.1"}:
            issues.append(f"Line {move.line}: unqualified path control {move.path_control}")
        for axis, value, step in zip("XYZ", move.end, steps):
            if value is not None:
                nearest = (value/step).to_integral_value() * step
                if abs(value-nearest) > tolerance:
                    issues.append(f"Line {move.line}: {axis}={value} off {step} mm lattice")
        if move.kind == "feed" and move.start != move.end and (
            move.feed_mm_min is None or move.feed_mm_min <= 0
        ):
            issues.append(f"Line {move.line}: moving feed block has no positive feed")
    return issues


def compare_endpoints(expected, program, tolerance_mm=1e-9):
    """Compare a known emitted block sequence; added setup moves are explicit."""
    actual = [move.end for move in program.motions]
    if len(expected) != len(actual):
        raise AssertionError(f"Endpoint count differs: expected {len(expected)}, actual {len(actual)}")
    tolerance = D(str(tolerance_mm))
    for index, (wanted, found) in enumerate(zip(expected, actual)):
        for axis, a, b in zip("XYZ", wanted, found):
            if a is None or b is None:
                if a != b:
                    raise AssertionError(f"Endpoint {index} {axis}: unknown position mismatch")
            elif abs(D(str(a))-b) > tolerance:
                raise AssertionError(f"Endpoint {index} {axis}: {b} differs from {a}")
