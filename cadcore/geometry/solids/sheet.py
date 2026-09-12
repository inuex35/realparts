"""Sheet metal: a part that is one thickness everywhere, and folds flat.

It records what it did (the sheet, each bend) so the flat blank is read off
the record rather than unfolded from the solid.
"""
from __future__ import annotations

import math

from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakeWire)
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.GC import GC_MakeArcOfCircle
from OCP.gp import gp_Pnt, gp_Vec

from ... import names
from .booleans import fuse
from ..core import provenance
from ...errors import CadError
from ..core.naming import Body, face_info, faces_of
from .sweeps import _oriented_solid

DEFAULT_K = 0.44


def bend_allowance(angle_deg: float, radius: float, thickness: float,
                   k: float = DEFAULT_K) -> float:
    """How much material the bend itself eats, along the neutral axis."""
    return math.radians(abs(float(angle_deg))) * (float(radius) + k * float(thickness))


def sheet(feature_id: str, sketch, thickness: float) -> Body:
    """A flat blank: the sketch, given a thickness, and told it is sheet metal (so a flat pattern can be read off it)."""
    from .sweeps import extrude

    if float(thickness) <= 0:
        raise CadError("bad_parameter", "a sheet needs a positive thickness")
    body = extrude(feature_id, sketch, float(thickness))
    # the blank starts as the sketch, so the sketch's own outline is the start
    # of the flat pattern. Kept in the sheet's plane, in its own coordinates
    outline = [[round(p[0], 6), round(p[1], 6)] for p in _outline_of(sketch)]
    body.notes["sheet"] = {"thickness_mm": float(thickness), "feature": feature_id,
                           "outline": outline,
                           "plane": {"origin": list(sketch.plane.origin),
                                     "normal": list(sketch.plane.normal),
                                     "x_axis": list(sketch.plane.x_axis)}}
    return body


def _outline_of(sketch) -> list:
    """The sketch's outer loop as a closed run of 2D points; arcs sampled, as a cutter reads them."""
    loops = getattr(sketch, "loops", None) or []
    if not loops:
        return []

    def points_of(loop):
        out = []
        for segment in loop:
            out.extend(list(p) for p in segment.sample()[:-1])   # the end is the next start
        return out

    def area(points):
        return abs(sum(points[i][0] * points[(i + 1) % len(points)][1]
                       - points[(i + 1) % len(points)][0] * points[i][1]
                       for i in range(len(points)))) / 2

    return points_of(max(loops, key=lambda loop: area(points_of(loop))))


def flange(feature_id: str, body: Body, edge_name: str, length: float,
           angle: float = 90.0, radius: float | None = None,
           thickness: float | None = None, k: float | None = None) -> Body:
    """Bend a new flap up from a boundary edge of the sheet: a cross-section swept along the edge and fused on."""
    table = body.edge_table()
    edge = table.get(edge_name)
    if edge is None:
        raise CadError("unresolved_reference", f"no edge named {edge_name!r}",
                       {"available": sorted(table)[:40]})
    owners = [body.face(name) for name in body.edge_owners(edge_name)]
    owners = [f for f in owners if f is not None]
    plate = _flat_face(owners)
    if plate is None:
        raise CadError("not_a_sheet_edge",
                       f"{edge_name!r} does not run along a flat face of the sheet",
                       {"edge": edge_name})

    thickness = float(thickness) if thickness else _thickness(body, plate)
    radius = float(radius) if radius is not None else thickness
    if float(length) <= 0:
        raise CadError("bad_parameter", "a flange needs a positive length")

    frame = _edge_frame(edge, plate, body)
    # a positive angle bends towards the face's normal; the section is mirrored
    # in the sheet's mid-plane to fold the other way
    section = _section(frame, thickness, radius, abs(float(angle)), float(length))
    if float(angle) > 0:
        section = _mirrored(section, frame, thickness)
    prism = BRepPrimAPI_MakePrism(section, gp_Vec(*[frame["along"][i] * frame["span"]
                                                    for i in range(3)]))
    prism.Build()
    if not prism.IsDone():
        raise CadError("flange_failed", "the flange did not sweep along its edge")
    flap = _named_flap(feature_id, _oriented_solid(prism.Shape()), frame, radius)
    from .surfaces import unified

    # the flap's sides run flush with the sheet's: one face each, not two
    out = unified(fuse(feature_id, body, flap))
    # the bend is recorded now: the flat pattern is read off the record
    out.notes.setdefault("bends", []).append(
        {"feature": feature_id, "edge": edge_name, "angle_deg": float(angle),
         "radius_mm": radius, "thickness_mm": thickness, "length_mm": float(length),
         "allowance_mm": round(bend_allowance(angle, radius, thickness,
                                             DEFAULT_K if k is None else float(k)), 4),
         "k": DEFAULT_K if k is None else float(k),
         "from": _flat_owner(body, plate),
         "at": [list(frame["origin"]),
                [frame["origin"][i] + frame["along"][i] * frame["span"]
                 for i in range(3)]],
         "out": list(frame["out"]), "up": list(frame["up"])})
    return out


def _flat_owner(body: Body, plate) -> str | None:
    """Which feature made the face this flange bends from.

    The base plate for the first flange; the previous flap for one bent at the
    end of it. That chain is what the flat pattern walks.
    """
    from ... import names
    found = body.name_of(plate)
    return names.feature_of(found) if found else None


def _named_flap(feature_id: str, shape, frame: dict, radius: float) -> Body:
    """Name a flange's faces for their part in the bend (``end``, ``face``): what the next flange and a fastener refer to."""
    along = frame["along"]
    named, cylinders, flats = [], [], []
    for face in faces_of(shape):
        info = face_info(face)
        if "radius" in info:
            cylinders.append((info["radius"], face))
        elif "normal" in info:
            flats.append((abs(sum(info["normal"][i] * along[i] for i in range(3))),
                          info["area"], face))
    for k, (_, face) in enumerate(sorted(cylinders, key=lambda pair: pair[0])):
        named.append((names.face(feature_id, "bend" if k == 0 else "bend_out"), face))

    # the two ends of the sweep run along the edge; of the rest, the biggest two
    # are the flap's own faces and the smallest is the tip it ends at
    ends = [f for _, _, f in sorted(flats, key=lambda t: (-t[0], -t[1]))[:2]]
    rest = [(area, f) for _, area, f in flats if not any(f.IsSame(e) for e in ends)]
    rest.sort(key=lambda pair: -pair[0])
    for k, (_, face) in enumerate(rest[:2]):
        named.append((names.face(feature_id, "face" if k == 0 else "back"), face))
    # of what is left, the tip is the one further from the edge it was bent at:
    # the other is the root, and the fuse welds that one into the sheet
    tail = sorted(((_distance(face_info(f)["centre"], frame["origin"]), f)
                   for _, f in rest[2:]), key=lambda pair: -pair[0])
    for k, (_, face) in enumerate(tail):
        named.append((names.face(feature_id, "end" if k == 0 else "root"), face))
    for k, face in enumerate(ends):
        named.append((names.face(feature_id, "side", k + 1), face))
    return Body(shape, named, {}, [])


def _distance(a, b) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def _flat_face(faces) -> object | None:
    """Which of an edge's two flat faces is the sheet: the big one, not the strip of its thickness."""
    flat = [(face_info(f)["area"], f) for f in faces if "normal" in face_info(f)]
    return max(flat, key=lambda pair: pair[0])[1] if flat else None


def _thickness(body: Body, plate) -> float:
    """The sheet's thickness, measured: a ray from a point on the face into the material meets the other side."""
    from ..core.measure import first_hit

    # the ray starts a step inside the material, so the step is added back on
    step = 1e-4
    info = face_info(plate)
    inward = [-c for c in info["normal"]]
    # from a point on the face: a U's centre of mass is in its slot
    from_here = point_on(plate)
    start = [from_here[i] + inward[i] * step for i in range(3)]
    reach = first_hit(body.shape, start, inward, minimum=1e-6)
    if reach is None or reach <= 1e-6:
        raise CadError("not_a_sheet", "this body has no measurable thickness there",
                       {"hint": "a flange needs a sheet: two parallel faces"})
    return reach + step


def point_on(face) -> tuple:
    """A point on the face (its centre of mass may be in a slot or a hole): the middle first, then samples."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from ..core.occ import FClass2d
    from OCP.TopAbs import TopAbs_OUT
    from OCP.gp import gp_Pnt2d

    surface = BRepAdaptor_Surface(face)
    inside = FClass2d(face, 1e-7)
    u0, u1 = surface.FirstUParameter(), surface.LastUParameter()
    v0, v1 = surface.FirstVParameter(), surface.LastVParameter()
    steps = [0.5, 0.25, 0.75, 0.125, 0.375, 0.625, 0.875]
    for fu in steps:
        for fv in steps:
            u, v = u0 + (u1 - u0) * fu, v0 + (v1 - v0) * fv
            if inside.Perform(gp_Pnt2d(u, v)) != TopAbs_OUT:
                point = surface.Value(u, v)
                return (point.X(), point.Y(), point.Z())
    raise CadError("not_a_sheet", "no point could be found on that face",
                   {"hint": "the face may be degenerate"})


def _edge_frame(edge, plate, body: Body) -> dict:
    """Where the bend happens: along the edge, out of the sheet, up its normal."""
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp

    first = BRep_Tool.Pnt_s(TopExp.FirstVertex_s(edge))
    last = BRep_Tool.Pnt_s(TopExp.LastVertex_s(edge))
    along = [last.X() - first.X(), last.Y() - first.Y(), last.Z() - first.Z()]
    span = math.sqrt(sum(c * c for c in along))
    if span < 1e-9:
        raise CadError("not_a_sheet_edge", "that edge has no length")
    along = [c / span for c in along]

    up = list(face_info(plate)["normal"])
    # out of the sheet, in its own plane: a hair off the edge's middle each
    # way, and the face says which point it contains (a centre of mass is not
    # inside a concave face)
    out = [up[1] * along[2] - up[2] * along[1],
           up[2] * along[0] - up[0] * along[2],
           up[0] * along[1] - up[1] * along[0]]
    middle = [(first.X() + last.X()) / 2, (first.Y() + last.Y()) / 2,
              (first.Z() + last.Z()) / 2]
    if _inside(plate, [middle[i] + out[i] * 1e-3 for i in range(3)]):
        out = [-c for c in out]
    return {"origin": (first.X(), first.Y(), first.Z()), "along": along,
            "out": out, "up": up, "span": span}


def _inside(face, point) -> bool:
    """Whether the face contains this point, not merely its plane."""
    from OCP.BRep import BRep_Tool
    from ..core.occ import FClass2d
    from OCP.ShapeAnalysis import ShapeAnalysis_Surface
    from OCP.TopAbs import TopAbs_OUT
    from OCP.gp import gp_Pnt

    where = ShapeAnalysis_Surface(BRep_Tool.Surface_s(face))
    uv = where.ValueOfUV(gp_Pnt(*[float(c) for c in point]), 1e-6)
    return FClass2d(face, 1e-7).Perform(uv) != TopAbs_OUT


def _section(frame: dict, thickness: float, radius: float, angle: float,
             length: float):
    """The flange's cross-section: the bend's arc, then the flat.

    Drawn in the plane across the edge, from the sheet's own top and bottom
    surfaces, so the flap meets the sheet exactly rather than nearly.
    """
    origin, out, up = frame["origin"], frame["out"], frame["up"]
    turn = math.radians(angle)

    def at(u: float, v: float) -> gp_Pnt:
        return gp_Pnt(*[origin[i] + out[i] * u + up[i] * v for i in range(3)])

    # the bend turns about a centre one inside radius below the sheet's bottom
    # surface, at the edge: the inner arc is tangent to the bottom and the
    # outer to the top, so the flap stays one thickness (v runs -t to 0)
    centre = (0.0, -(thickness + radius))       # (out, up) from the edge
    inner = radius
    outer = radius + thickness

    def arc(r: float, start_angle: float, end_angle: float, steps: int = 1):
        mid = (start_angle + end_angle) / 2
        return (at(centre[0] + r * math.cos(start_angle),
                   centre[1] + r * math.sin(start_angle)),
                at(centre[0] + r * math.cos(mid), centre[1] + r * math.sin(mid)),
                at(centre[0] + r * math.cos(end_angle),
                   centre[1] + r * math.sin(end_angle)))

    # from straight up (+pi/2) round to the flange's direction
    start, end = math.pi / 2, math.pi / 2 - turn
    inner_start, inner_mid, inner_end = arc(inner, start, end)
    outer_start, outer_mid, outer_end = arc(outer, start, end)

    # the flat, carried on from where the bend ended
    direction = (math.cos(end - math.pi / 2), math.sin(end - math.pi / 2))
    def carried(point: gp_Pnt) -> gp_Pnt:
        return gp_Pnt(*[point.Coord(i + 1)
                        + (out[i] * direction[0] + up[i] * direction[1]) * length
                        for i in range(3)])

    wire = BRepBuilderAPI_MakeWire()
    wire.Add(BRepBuilderAPI_MakeEdge(
        GC_MakeArcOfCircle(inner_start, inner_mid, inner_end).Value()).Edge())
    wire.Add(BRepBuilderAPI_MakeEdge(inner_end, carried(inner_end)).Edge())
    wire.Add(BRepBuilderAPI_MakeEdge(carried(inner_end), carried(outer_end)).Edge())
    wire.Add(BRepBuilderAPI_MakeEdge(carried(outer_end), outer_end).Edge())
    wire.Add(BRepBuilderAPI_MakeEdge(
        GC_MakeArcOfCircle(outer_end, outer_mid, outer_start).Value()).Edge())
    wire.Add(BRepBuilderAPI_MakeEdge(outer_start, inner_start).Edge())
    if not wire.IsDone():
        raise CadError("flange_failed", "the flange's section did not close",
                       {"hint": "an angle of 180 degrees or more folds the flap "
                                "back through the sheet"})
    face = BRepBuilderAPI_MakeFace(wire.Wire())
    if not face.IsDone():
        raise CadError("flange_failed", "the flange's section is not a face")
    return face.Face()


def _mirrored(face, frame: dict, thickness: float):
    """The same section, folded the other way: reflected in the sheet's mid-plane."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax2, gp_Dir, gp_Trsf
    from OCP.TopoDS import TopoDS

    origin, up = frame["origin"], frame["up"]
    middle = gp_Pnt(*[origin[i] - up[i] * thickness / 2 for i in range(3)])
    mirror = gp_Trsf()
    mirror.SetMirror(gp_Ax2(middle, gp_Dir(*up)))
    algo = BRepBuilderAPI_Transform(face, mirror, True)
    algo.Build()
    return TopoDS.Face_s(algo.Shape())


def flat_pattern(body: Body, blank: dict | None = None) -> dict:
    """The blank this part is cut from, and the bends to make in it.

    Read off the bends the part records, not by unfolding the solid: a bend's
    developed length is a property of the material. The base sheet keeps its
    loop and each flange the line it was bent on; laying them out walks the chain.
    """
    bends = list(body.notes.get("bends", []))
    if not bends:
        raise CadError("not_sheet_metal", "this part records no bends",
                       {"hint": "build it from a sheet and flanges"})
    # a part joined to a copy of itself (mirrored with `merge`) has the bends
    # of both halves and the record of one: a blank laid out from that is wrong
    if provenance.lost(body, "bends"):
        raise CadError("not_sheet_metal",
                       "this part was joined to a copy of itself, so its bends "
                       "are no longer the whole of it",
                       {"why": [entry.get("why") for entry
                                in body.notes.get(provenance.LOST, [])
                                if entry.get("note") == "bends"],
                        "hint": "lay out the part before mirroring or "
                                "patterning it, and cut one blank per instance"})
    flat = {"bends": bends,
            "bend_allowance_mm": round(sum(b["allowance_mm"] for b in bends), 4),
            "added_mm": round(sum(b["allowance_mm"] + b["length_mm"]
                                  for b in bends), 3),
            "thickness_mm": bends[0]["thickness_mm"]}
    developed = _developed(body, bends)
    if developed:
        flat.update(developed)
    if blank:
        flat["blank_mm"] = blank
    return flat


def _developed(body: Body, bends: list) -> dict:
    """The blank's outline and its bend lines, in the sheet's own plane."""
    sheet_note = body.notes.get("sheet") or {}
    outline = sheet_note.get("outline")
    plane = sheet_note.get("plane")
    if not outline or not plane:
        return {}                    # an older part, or one not built from a sheet

    origin, normal, x_axis = (plane["origin"], plane["normal"], plane["x_axis"])
    y_axis = [normal[1] * x_axis[2] - normal[2] * x_axis[1],
              normal[2] * x_axis[0] - normal[0] * x_axis[2],
              normal[0] * x_axis[1] - normal[1] * x_axis[0]]

    def flatten(point) -> tuple:
        """A point of the folded part, in the sheet's plane."""
        rel = [point[i] - origin[i] for i in range(3)]
        return (sum(rel[i] * x_axis[i] for i in range(3)),
                sum(rel[i] * y_axis[i] for i in range(3)))

    # a flange off the base projects its record into the sheet's plane; one
    # off another flap inherits its parent's direction, and its bend line sits
    # at the far edge of the parent's developed material
    laid = {sheet_note.get("feature"): {"reach": 0.0, "away": None, "line": None}}
    faces, lines = [list(map(list, outline))], []
    for bend in bends:
        parent = laid.get(bend.get("from"))
        if parent is None or not bend.get("at"):
            return {}                # a chain this cannot walk: say nothing
        start, end = (flatten(p) for p in bend["at"])
        if parent["away"] is None:                       # bent off the base
            out = flatten([bend["at"][0][i] + bend["out"][i] for i in range(3)])
            away = (out[0] - start[0], out[1] - start[1])
            span = math.hypot(*away)
            if span < 1e-9:
                return {}
            away = (away[0] / span, away[1] / span)
            near = [start, end]
        else:                                            # bent off another flap
            if not _parallel(bend["at"], parent["at"]):
                return {}            # not bent at the end of it: not laid out
            away = parent["away"]
            step = parent["reach"]
            near = [(p[0] + away[0] * step, p[1] + away[1] * step)
                    for p in parent["line"]]

        added = bend["allowance_mm"] + bend["length_mm"]
        far = [(p[0] + away[0] * added, p[1] + away[1] * added) for p in near]
        faces.append([list(near[0]), list(near[1]), list(far[1]), list(far[0])])
        lines.append({"feature": bend["feature"],
                      "angle_deg": bend["angle_deg"],
                      "from": [round(c, 6) for c in near[0]],
                      "to": [round(c, 6) for c in near[1]]})
        laid[bend["feature"]] = {"reach": added, "away": away, "line": near,
                                 "at": bend["at"]}

    area = sum(_area(face) for face in faces)
    return {"blank": [[[round(c, 6) for c in p] for p in face] for face in faces],
            "bend_lines": lines,
            "blank_area_mm2": round(area, 4),
            "extent_mm": _extent([p for face in faces for p in face])}


def _parallel(one: list, other: list, tolerance: float = 1e-6) -> bool:
    """Do these two bend lines run the same way? A flange bent along a flap's side gets no outline rather than a wrong one."""
    def direction(line):
        d = [line[1][i] - line[0][i] for i in range(3)]
        span = math.sqrt(sum(c * c for c in d))
        return [c / span for c in d] if span > 1e-9 else None

    a, b = direction(one), direction(other)
    if a is None or b is None:
        return False
    return abs(abs(sum(a[i] * b[i] for i in range(3))) - 1.0) < tolerance


def _area(points: list) -> float:
    return abs(sum(points[i][0] * points[(i + 1) % len(points)][1]
                   - points[(i + 1) % len(points)][0] * points[i][1]
                   for i in range(len(points)))) / 2


def _extent(points: list) -> list:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [round(max(xs) - min(xs), 4), round(max(ys) - min(ys), 4)]


def flat_dxf(flat: dict) -> str:
    """The blank as DXF: layer `BLANK` is cut and `BEND` is folded, in the R2000 flavour every cutter reads."""
    if not flat.get("blank"):
        raise CadError("no_flat_pattern",
                       "this part has no developed outline to write",
                       {"hint": "it is laid out from what a sheet and its "
                                "flanges recorded; a part built another way "
                                "has nothing to unfold"})

    out = ["999", "RealParts flat pattern",
           "0", "SECTION", "2", "HEADER",
           "9", "$ACADVER", "1", "AC1015",
           "9", "$INSUNITS", "70", "4",              # millimetres
           "0", "ENDSEC",
           "0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "70", "2"]
    for layer, colour, kind in (("BLANK", "7", "CONTINUOUS"),
                                ("BEND", "1", "DASHED")):
        out += ["0", "LAYER", "2", layer, "70", "0", "62", colour, "6", kind]
    out += ["0", "ENDTAB", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]

    for face in flat["blank"]:
        out += ["0", "LWPOLYLINE", "8", "BLANK", "100", "AcDbEntity",
                "100", "AcDbPolyline", "90", str(len(face)), "70", "1"]  # closed
        for x, y in face:
            out += ["10", f"{x:.4f}", "20", f"{y:.4f}"]
    for line in flat.get("bend_lines", []):
        out += ["0", "LINE", "8", "BEND", "100", "AcDbEntity", "100", "AcDbLine",
                "10", f"{line['from'][0]:.4f}", "20", f"{line['from'][1]:.4f}",
                "11", f"{line['to'][0]:.4f}", "21", f"{line['to'][1]:.4f}"]
    out += ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(out) + "\n"
