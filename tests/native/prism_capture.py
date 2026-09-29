"""Native OCC fixtures for exact planar prisms and explicit rejected sections."""
from types import SimpleNamespace

import FreeCAD as App
import Part

from native_support import check


def _face(points):
    points = [App.Vector(x, y, 0) for x, y in points]
    return Part.Face(Part.makePolygon(points + [points[0]]))


def _top(shape):
    return next("Face" + str(index + 1) for index, face in enumerate(shape.Faces)
                if isinstance(face.Surface, Part.Plane) and face.normalAt(0, 0).z > .99)


def run(doc, native):
    l_face = _face([[0, 0], [40, 0], [40, 20], [25, 20], [25, 35], [0, 35]])
    l_face = l_face.cut(_face([[3, 3], [6, 3], [6, 6], [3, 6]])).Faces[0]
    ring = Part.Face(Part.Wire(Part.makeCircle(20)))
    ring = ring.cut(Part.Face(Part.Wire(Part.makeCircle(5)))).Faces[0]
    ellipse = Part.Ellipse()
    ellipse.MajorRadius, ellipse.MinorRadius = 20, 12
    ellipse_face = Part.Face(Part.Wire(ellipse.toShape()))
    records = {}
    metrics = {}
    for name, face, depth, tolerance in (("ConcaveHoledStock", l_face, 8, .001),
                                         ("CircularHoledStock", ring, 4, .005),
                                         ("EllipticalStock", ellipse_face, 6, .005)):
        shape = face.extrude(App.Vector(0, 0, depth))
        face_name = _top(shape)
        obj = doc.addObject("Part::Feature", name)
        obj.Shape = shape
        obj.Placement = App.Placement(App.Vector(7, 8, 9), App.Rotation(App.Vector(1, 2, 3), 31))
        doc.recompute()
        record = native.capture_face(SimpleNamespace(Object=obj, SubElementNames=[face_name]), tolerance=tolerance)
        check(record["stock_capture_evidence"]["prism_missing_volume_mm3"] < 1e-9, "Prism proof left missing material")
        check(record["stock_capture_evidence"]["prism_extra_volume_mm3"] < 1e-9, "Prism proof left extra material")
        check(len(record["stock_outline"]) == 1, "Captured stock component count changed")
        expected_holes = 0 if name == "EllipticalStock" else 1
        check(len(record["stock_outline"][0]["holes"]) == expected_holes, "Captured holes were lost")
        check(record["stock_outline_tolerance"] == (0 if name == "ConcaveHoledStock" else tolerance), "Wrong outline enclosure allowance")
        records[name] = record
        metrics[name] = record["stock_capture_evidence"]
    for name, shape in (("TaperedStock", Part.makeCone(20, 15, 8)),
                        ("HiddenReliefStock", Part.makeBox(40, 40, 8).cut(Part.makeBox(5, 5, 3, App.Vector(10, 10, 0))))):
        obj = doc.addObject("Part::Feature", name)
        obj.Shape = shape
        doc.recompute()
        try:
            native.capture_face(SimpleNamespace(Object=obj, SubElementNames=[_top(shape)]))
        except ValueError as error:
            check("constant-section" in str(error), "Nonprismatic stock returned the wrong diagnostic")
        else:
            raise AssertionError("Nonprismatic stock was silently approximated: " + name)
    return records, metrics
