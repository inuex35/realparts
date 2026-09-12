"""Assemblies: parts placed against each other by their named faces.

Mates resolve in order, each moving one part against something already
placed; a closed loop is solved by :mod:`cadcore.geometry.assembly.placement`.
Interference is the shared volume in mm^3, re-measured by rays when the boolean is empty.
"""
from __future__ import annotations

import math

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_SurfaceType
from OCP.gp import gp_Ax1, gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

from OCP.TopoDS import TopoDS

from ... import names
from ...errors import CadError
from ..core.measure import face_frame
from ..core.naming import Body, face_info, faces_of, reroled
from ..core.occ import bounds

#: every mate kind, and what each holds: see ``ROWS`` for how many numbers
MATES = ("fastened", "planar", "concentric", "parallel", "perpendicular", "angle",
         "distance", "tangent", "hinge", "slider", "ball", "gear", "screw", "belt", "slot",
         "cam")

#: which face shapes a kind is written against: the moving face, the fixed face
NEEDS = {
    "fastened": "two flat faces, put together",
    "planar": "two flat faces, at a distance",
    "concentric": "two round faces (cylinder or cone) on one axis",
    "parallel": "two faces with a direction: flat, round or a cone",
    "perpendicular": "two faces with a direction, at right angles",
    "angle": "two faces with a direction, at the given angle",
    "distance": "flat, round or spherical faces, this far apart",
    "tangent": "a round or spherical face touching a flat, round or spherical one",
    "hinge": "two round faces on one axis, centred on each other: a pin in a hole",
    "slider": "two round faces on one axis that do not turn: a piston in a bore",
    "ball": "two spherical faces with one centre",
    "gear": "two round faces with parallel axes that turn each other",
    "screw": "two round faces on one axis: a turn is a step along it",
    "belt": "two round faces with parallel axes that turn the same way, as a belt or chain joins them",
    "slot": "a round face (the pin) held against a flat side of a slot: it slides along the side and turns",
    "cam": "a round face (the follower) kept touching any face (the cam) as it turns",
}


def prefixed(body: Body, prefix: str) -> Body:
    """The same body with every name scoped to its part: ``base:plate/+z``."""
    return body.rebuilt(
        names=[(names.scoped(prefix, n), f) for n, f in body.names],
        aliases={names.scoped(prefix, a): names.scoped(prefix, c)
                 for a, c in body.aliases.items()},
        dropped=[names.scoped(prefix, n) for n in body.dropped])


def transformed(body: Body, trsf: gp_Trsf, rename: bool = False) -> Body:
    """Move a whole part, keeping every name attached to its face.

    A placed part keeps its own names: ``base:plate/+z`` is the plate's top
    wherever the plate ends up, and the mates say so. ``rename`` is for a
    body that is only ever seen placed (a fastener), whose roles then say
    which way its faces face.
    """
    algo = BRepBuilderAPI_Transform(body.shape, trsf, True)
    algo.Build()
    out = body.rebuilt(shape=algo.Shape(), names=[])
    # the result's own faces: ModifiedShape hands back the algorithm's
    # orientation, and the shell's is what decides the outward normal
    live: dict = {}
    for face in faces_of(algo.Shape()):
        live.setdefault(hash(face), []).append(face)
    for name, face in body.names:
        try:
            moved = TopoDS.Face_s(algo.ModifiedShape(face))
        except Exception:                                        # noqa: BLE001
            out.dropped.append(name)
            continue
        out.names.append((name, next((f for f in live.get(hash(moved), ()) if f.IsSame(moved)), moved)))
    return reroled(out) if rename else out


def axis_of(body: Body, face_name: str) -> tuple:
    """A cylindrical face's axis: a point on it and a direction."""
    face = body.face(face_name)
    if face is None:
        raise CadError("unresolved_reference", f"no face named {face_name!r}",
                       {"available": body.face_names()})
    adaptor = BRepAdaptor_Surface(face)
    if adaptor.GetType() != GeomAbs_SurfaceType.GeomAbs_Cylinder:
        info = face_info(face)               # a spline that is a cylinder to within a micron
        if info.get("recognised") == "cylinder":
            return tuple(info["axis_origin"]), tuple(info["axis"]), info["radius"]
        raise CadError("not_a_cylinder", f"face {face_name!r} is not cylindrical",
                       {"hint": "concentric mates need round faces"})
    axis = adaptor.Cylinder().Axis()
    location, direction = axis.Location(), axis.Direction()
    return ((location.X(), location.Y(), location.Z()),
            (direction.X(), direction.Y(), direction.Z()),
            adaptor.Cylinder().Radius())


def mate_geometry(body: Body, face_name: str) -> dict:
    """What a mate can hold a face by, in the part's own coordinates.

    ``shape`` is plane, cylinder, cone or sphere. A plane has an ``origin``
    (its centroid), an outward ``direction`` and an ``x_axis``; a cylinder or
    cone an ``origin`` on the axis, the axis as ``direction``, a ``radius`` and
    ``mid``, the point on the axis level with the face's centroid; a sphere a
    ``centre`` and a ``radius``. Every shape has ``centre``, the face centroid.
    """
    face = body.face(face_name)
    if face is None:
        raise CadError("unresolved_reference", f"no face named {face_name!r}",
                       {"available": body.face_names()})
    adaptor = BRepAdaptor_Surface(face)
    kind = adaptor.GetType()
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    centre = props.CentreOfMass()
    out = {"face": face_name, "centre": (centre.X(), centre.Y(), centre.Z())}
    if kind == GeomAbs_SurfaceType.GeomAbs_Plane:
        frame = face_frame(body, face_name)
        out.update(shape="plane", origin=tuple(frame["origin"]),
                   direction=tuple(frame["normal"]), x_axis=tuple(frame["x_axis"]))
        return out
    if kind in (GeomAbs_SurfaceType.GeomAbs_Cylinder, GeomAbs_SurfaceType.GeomAbs_Cone):
        surface = adaptor.Cylinder() if kind == GeomAbs_SurfaceType.GeomAbs_Cylinder \
            else adaptor.Cone()
        axis = surface.Axis()
        location, direction = axis.Location(), axis.Direction()
        origin = (location.X(), location.Y(), location.Z())
        d = (direction.X(), direction.Y(), direction.Z())
        along = sum((out["centre"][i] - origin[i]) * d[i] for i in range(3))
        out.update(shape="cylinder" if kind == GeomAbs_SurfaceType.GeomAbs_Cylinder
                   else "cone",
                   origin=origin, direction=d,
                   radius=surface.Radius() if kind == GeomAbs_SurfaceType.GeomAbs_Cylinder
                   else surface.RefRadius(),
                   mid=tuple(origin[i] + d[i] * along for i in range(3)))
        return out
    if kind == GeomAbs_SurfaceType.GeomAbs_Sphere:
        sphere = adaptor.Sphere()
        at = sphere.Location()
        out.update(shape="sphere", centre=(at.X(), at.Y(), at.Z()),
                   radius=sphere.Radius())
        return out
    info = face_info(face)
    if info.get("recognised") == "cylinder":
        origin, d = tuple(info["axis_origin"]), tuple(info["axis"])
        along = sum((out["centre"][i] - origin[i]) * d[i] for i in range(3))
        out.update(shape="cylinder", origin=origin, direction=d, radius=info["radius"],
                   mid=tuple(origin[i] + d[i] * along for i in range(3)))
        return out
    out["shape"] = str(kind).rsplit("_", 1)[-1].lower()
    return out


#: the face shapes each kind accepts: any pair drawn from the set, except that
#: a pair of planes has nothing to be tangent to
_AXIAL = frozenset({"cylinder", "cone"})
_DIRECTED = _AXIAL | {"plane"}
_ACCEPTS = {
    "fastened": frozenset({"plane"}), "planar": frozenset({"plane"}),
    "concentric": _AXIAL, "hinge": _AXIAL, "slider": _AXIAL, "gear": _AXIAL,
    "screw": _AXIAL, "parallel": _DIRECTED, "perpendicular": _DIRECTED,
    "angle": _DIRECTED, "distance": _DIRECTED | {"sphere"},
    "tangent": _DIRECTED | {"sphere"}, "ball": frozenset({"sphere"}),
    "belt": _AXIAL, "slot": _AXIAL | {"plane"}, "cam": frozenset({"plane", "cylinder", "cone", "sphere", "other"}),
}


def check_mate_faces(kind: str, moving: dict, fixed: dict) -> None:
    """Refuse a mate written against faces it cannot hold."""
    shapes = (moving["shape"], fixed["shape"])
    fits = set(shapes) <= _ACCEPTS[kind]
    if kind == "tangent" and shapes == ("plane", "plane"):
        fits = False
    if kind == "slot" and (moving["shape"] == "plane" or fixed["shape"] != "plane"):
        fits = False
    if kind == "cam" and moving["shape"] not in ("cylinder", "sphere"):
        fits = False
    if not fits:
        raise CadError("bad_mate_faces",
                       f"a {kind} mate needs {NEEDS[kind]}; given "
                       f"{moving['face']!r} ({moving['shape']}) and "
                       f"{fixed['face']!r} ({fixed['shape']})",
                       {"kind": kind, "faces": [moving["face"], fixed["face"]],
                        "shapes": list(shapes), "needs": NEEDS[kind]})


def _ax3(origin, normal, reference) -> gp_Ax3:
    return gp_Ax3(gp_Pnt(*[float(c) for c in origin]),
                  gp_Dir(*[float(c) for c in normal]),
                  gp_Dir(*[float(c) for c in reference]))


def _perpendicular(direction) -> tuple:
    """Any unit vector at right angles to this one, chosen deterministically."""
    reference = (0.0, 0.0, 1.0) if abs(direction[2]) < 0.9 else (1.0, 0.0, 0.0)
    v = (reference[1] * direction[2] - reference[2] * direction[1],
         reference[2] * direction[0] - reference[0] * direction[2],
         reference[0] * direction[1] - reference[1] * direction[0])
    length = math.sqrt(sum(c * c for c in v))
    return tuple(c / length for c in v)


def _sub(a, b):
    return tuple(a[i] - b[i] for i in range(3))


def _add(a, b, k=1.0):
    return tuple(a[i] + k * b[i] for i in range(3))


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(v):
    return math.sqrt(_dot(v, v))


def _unit(v):
    length = _norm(v) or 1.0
    return tuple(c / length for c in v)


def _axis_point(g: dict) -> tuple:
    """The point a shape is held by: on the axis for a cylinder, the centre of
    a sphere, the centroid of a plane."""
    if g["shape"] == "plane":
        return g["origin"]
    return g.get("mid") or g["centre"]


def mate_transform(kind: str, moving: Body, fixed: Body, faces: list,
                   offset: float = 0.0, angle: float = 0.0, flip: bool = True,
                   pitch: float | None = None) -> gp_Trsf:
    # ``flip`` turns the moving part round: faces meet instead of pointing the
    # same way, and a part goes into a hole rather than out of it.
    """The transform that brings the moving part's face onto the fixed one.

    One transform, so the kinds that leave freedom take the nearest placement:
    ``parallel``, ``perpendicular`` and ``angle`` only turn the part, and a
    ``distance``, ``tangent`` or ``gear`` is set off along the fixed face's own
    frame. ``angle`` is degrees, a spin about the fixed axis for the axial
    kinds and the angle between the two directions for ``angle``.
    """
    if kind not in MATES:
        raise CadError("unknown_mate", f"mate kind {kind!r} is not implemented",
                       {"available": list(MATES)})
    if len(faces) != 2:
        raise CadError("bad_arguments", "a mate needs exactly two faces",
                       {"given": faces})
    if kind == "concentric":
        # the message names the flat face, as it always has: a mate between a
        # flange and a bore is the mistake a person makes
        axis_of(moving, faces[0])
        axis_of(fixed, faces[1])
    m = mate_geometry(moving, faces[0])
    f = mate_geometry(fixed, faces[1])
    if kind == "fastened":
        face_frame(moving, faces[0])            # a round face refuses as non_planar_face
        face_frame(fixed, faces[1])
    check_mate_faces(kind, m, f)

    direction_f = f.get("direction")
    if direction_f is not None and flip:
        direction_f = tuple(-c for c in direction_f)
    direction_m = m.get("direction")

    if kind in ("fastened", "planar", "distance", "tangent") and \
            m["shape"] == "plane" and f["shape"] == "plane":
        gap = 0.0 if kind == "fastened" else offset
        source = _ax3(m["origin"], direction_m, m["x_axis"])
        target = _ax3(_add(f["origin"], direction_f, -gap), direction_f, f["x_axis"])
    elif kind in ("concentric", "hinge", "slider", "screw"):
        # a hole's axis direction is whatever OCCT gave it, so which way the
        # part points is the document's ``flip`` choice, not guessed here
        at = _add(f["origin"], direction_f, offset)
        if kind in ("hinge", "screw"):          # centred on the hole, then set off
            level = _dot(_sub(f["mid"], f["origin"]), direction_f)
            at = _add(f["origin"], direction_f, level + offset)
            source = _ax3(m["mid"], direction_m, _perpendicular(direction_m))
        else:
            source = _ax3(m["origin"], direction_m, _perpendicular(direction_m))
        target = _ax3(at, direction_f, _perpendicular(direction_f))
    elif kind == "ball":
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(*_sub(f["centre"], m["centre"])))
        return trsf
    elif kind in ("parallel", "perpendicular", "angle"):
        # turn about the moving face's own point; nothing here says where to go
        wanted = _turned_direction(kind, direction_m, direction_f, angle)
        source = _ax3(_axis_point(m), direction_m, _perpendicular(direction_m))
        target = _ax3(_axis_point(m), wanted, _perpendicular(wanted))
        trsf = gp_Trsf()
        trsf.SetDisplacement(source, target)
        return trsf
    elif kind == "gear" or (m["shape"] != "plane" and f["shape"] != "plane"):
        # side by side: axes parallel, this far apart, set off along the fixed
        # face's x direction; a sphere has no axis and sits on the fixed point
        apart = offset if kind == "distance" else _apart(kind, m, f, flip)
        across = _perpendicular(direction_f) if direction_f else (1.0, 0.0, 0.0)
        at = _add(_axis_point(f), across, apart)
        if direction_m is None or direction_f is None:
            trsf = gp_Trsf()
            trsf.SetTranslation(gp_Vec(*_sub(at, _axis_point(m))))
            return trsf
        source = _ax3(m.get("mid") or m["origin"], direction_m, _perpendicular(direction_m))
        target = _ax3(at, direction_f, _perpendicular(direction_f))
    elif f["shape"] == "plane":
        # a round or spherical face against a flat one: the axis lies along
        # the plane, the distance is measured along the plane's outward normal
        apart = offset if kind == "distance" else m["radius"]
        outward = f["direction"] if flip else tuple(-c for c in f["direction"])
        at = _add(f["origin"], outward, apart)
        if direction_m is None:
            trsf = gp_Trsf()
            trsf.SetTranslation(gp_Vec(*_sub(at, m["centre"])))
            return trsf
        source = _ax3(m["mid"], direction_m, _perpendicular(direction_m))
        target = _ax3(at, f["x_axis"], outward)
    else:
        # a flat face against a round one: the plane lies along the axis
        apart = offset if kind == "distance" else f["radius"]
        outward = _perpendicular(f["direction"])
        if not flip:
            outward = tuple(-c for c in outward)
        at = _add(_axis_point(f), outward, apart)
        source = _ax3(m["origin"], direction_m, m["x_axis"])
        target = _ax3(at, tuple(-c for c in outward), f["direction"])

    trsf = gp_Trsf()
    trsf.SetDisplacement(source, target)
    if abs(angle) > 1e-12 and kind not in ("angle",):
        spin = gp_Trsf()
        spin.SetRotation(gp_Ax1(target.Location(), target.Direction()),
                         math.radians(angle))
        trsf = spin.Multiplied(trsf)
    return trsf


def _apart(kind: str, m: dict, f: dict, flip: bool) -> float:
    """How far apart two round things sit when they touch: outside each
    other with ``flip``, one inside the other without."""
    if kind == "gear":
        return m["radius"] + f["radius"]
    return m["radius"] + f["radius"] if flip else abs(f["radius"] - m["radius"])


def _turned_direction(kind: str, direction_m, direction_f, angle: float) -> tuple:
    """Where the moving direction ends up: along, across, or at an angle to
    the fixed one, turned in the plane the two directions span."""
    if kind == "parallel":
        return direction_f
    wanted = 90.0 if kind == "perpendicular" else angle
    across = _cross(direction_f, direction_m)
    if _norm(across) < 1e-9:                    # already in line: any across will do
        across = _cross(direction_f, _perpendicular(direction_f))
    across = _unit(across)
    sideways = _unit(_cross(across, direction_f))
    return _unit(_add(tuple(c * math.cos(math.radians(wanted)) for c in direction_f),
                      sideways, math.sin(math.radians(wanted))))


def solids_of(body: Body) -> list:
    """Each solid of the body as a body of its own, with the names it carries."""
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer

    out, explorer = [], TopExp_Explorer(body.shape, TopAbs_ShapeEnum.TopAbs_SOLID)
    while explorer.More():
        solid = explorer.Current()
        named = [(n, f) for n, f in ((body.name_of(f), f) for f in faces_of_shape(solid)) if n]
        out.append(Body(solid, named))
        explorer.Next()
    return out


def faces_of_shape(shape) -> list:
    from ..core.naming import faces_of

    return faces_of(shape)


def bonded(body: Body) -> Body:
    """The parts of an assembly as one solid, glued wherever they touch.

    What a study of an assembly means when nothing says otherwise: no slip,
    no gap, no contact that opens. A part that touches nothing cannot be
    glued and is refused, because a study of it would float.
    """
    from ..solids.booleans import fuse

    parts = solids_of(body)
    if len(parts) < 2:
        return body
    out = parts[0]
    for part in parts[1:]:
        out = fuse("bonded", out, part)
    if len(solids_of(out)) > 1:
        scopes = sorted({names.parse(n).scope or names.feature_of(n)
                         for piece in solids_of(out) for n in piece.face_names()})
        raise CadError("parts_not_touching",
                       "the parts of this assembly do not all touch, so they cannot "
                       "be studied as one bonded solid",
                       {"parts": scopes,
                        "hint": "mate every part against another, or study one part"})
    return out


def interference(parts: dict, tolerance: float = 1e-6) -> list:
    """Every pair of parts that share volume, with how much (mm^3).

    Bounding boxes prefilter the pairs before any boolean is run.
    """
    boxes = {}
    for name, body in parts.items():
        box = Bnd_Box()
        BRepBndLib.Add_s(body.shape, box)
        boxes[name] = box

    found = []
    names = sorted(parts)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if boxes[a].IsOut(boxes[b]):
                continue
            algo = BRepAlgoAPI_Common(parts[a].shape, parts[b].shape)
            if not algo.IsDone():
                continue
            props = GProp_GProps()
            BRepGProp.VolumeProperties_s(algo.Shape(), props)
            volume = props.Mass()
            if volume > tolerance:
                found.append({"parts": [a, b], "volume_mm3": round(volume, 6),
                              "measured": "boolean"})
                continue
            # an empty boolean is trusted only when the parts are separated;
            # touching solids are the case the boolean gets wrong, so they are
            # re-measured by rays
            gap = BRepExtrema_DistShapeShape(parts[a].shape, parts[b].shape)
            gap.Perform()
            if not gap.IsDone() or gap.Value() > 1e-7:
                continue
            shared = _shared_by_rays(parts[a], parts[b], boxes[a], boxes[b])
            if shared > tolerance:
                found.append({"parts": [a, b], "volume_mm3": round(shared, 3),
                              "measured": "rays",
                              "note": "the boolean intersection came back empty, "
                                      "the parts are not apart, and points along "
                                      "the rays are inside both -- so they do "
                                      "share volume. How much is not known: the "
                                      "figure is integrated along rays, and on a "
                                      "nut and screw it came back 1.73 whatever "
                                      "was changed underneath it. Read it as "
                                      "'some', and find the place rather than "
                                      "trusting the size"})
    return found


#: distance (mm) below which two surfaces count as the same surface: contact,
#: not overlap.
TOUCHING = 1e-3


def _shared_by_rays(one: Body, two: Body, box_one: Bnd_Box, box_two: Bnd_Box,
                    across: int = 24) -> float:
    """Volume two solids share, integrated along a grid of rays through their shared box.

    The kernel is only asked where a line crosses a face; the overlap is where
    the two solids' in/out intervals coincide.
    """
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt, gp_Vec

    first, second = bounds(box_one), bounds(box_two)
    low = [max(first[i], second[i]) for i in range(3)]
    high = [min(first[i + 3], second[i + 3]) for i in range(3)]
    if any(high[i] <= low[i] for i in range(3)):
        return 0.0

    span = [high[i] - low[i] for i in range(3)]
    along = span.index(min(span))               # shortest way through: fewest crossings
    sideways = [i for i in range(3) if i != along]
    direction = [0.0, 0.0, 0.0]
    direction[along] = 1.0

    def crossings(shape, origin):
        """Solid intervals along this ray, from which way each crossed face points.

        Crossings are not paired off in order: where two surfaces touch they
        stop alternating. Each face's normal says whether the ray enters or
        leaves, and a depth count makes a graze contribute nothing.
        ``BRepGProp_Face.Normal`` already accounts for the face orientation, so
        it is not reversed again for a REVERSED face.
        """
        inter = BRepIntCurveSurface_Inter()
        inter.Init(shape, gp_Lin(gp_Pnt(*origin), gp_Dir(*direction)), 1e-7)
        hits = []
        along = gp_Vec(*direction)
        while inter.More():
            spot, normal = gp_Pnt(), gp_Vec()
            BRepGProp_Face(inter.Face()).Normal(inter.U(), inter.V(), spot, normal)
            hits.append((inter.W(), 1 if normal.Dot(along) < 0 else -1))
            inter.Next()
        hits.sort()
        spans, depth, opened = [], 0, None
        for where, step in hits:
            was, depth = depth, depth + step
            if was <= 0 < depth:
                opened = where
            elif was > 0 >= depth and opened is not None:
                if where - opened > TOUCHING:
                    spans.append((opened, where))
                opened = None
        return spans

    def sweep(corner, width):
        """Fire a grid over one rectangle; return the volume and where it hit."""
        cell = (width[0] / across) * (width[1] / across)
        total, hits = 0.0, []
        found = inside_points
        for i in range(across):
            for j in range(across):
                origin = [0.0, 0.0, 0.0]
                origin[along] = low[along] - 1.0
                at = (corner[0] + width[0] * (i + 0.5177) / across,
                      corner[1] + width[1] * (j + 0.5177) / across)
                origin[sideways[0]], origin[sideways[1]] = at
                here = crossings(one.shape, origin)
                if not here:
                    continue
                there = crossings(two.shape, origin)
                length = 0.0
                for a_lo, a_hi in here:
                    for b_lo, b_hi in there:
                        share = min(a_hi, b_hi) - max(a_lo, b_lo)
                        # touching surfaces give a share of about zero and are
                        # not overlap. The middle of each surviving span is kept
                        # so the caller can confirm it with a point classifier.
                        if share > TOUCHING:
                            length += share
                            middle = (max(a_lo, b_lo) + min(a_hi, b_hi)) / 2
                            found.append([origin[k] + direction[k] * middle
                                          for k in range(3)])
                if length > 0:
                    total += length * cell
                    hits.append(at)
        return total, hits

    inside_points = []
    corner = (low[sideways[0]], low[sideways[1]])
    width = (span[sideways[0]], span[sideways[1]])
    first, hits = sweep(corner, width)
    if not hits:
        return 0.0
    # the overlap is usually a sliver that a grid over the whole shared box
    # mostly misses, so the grid is fired again over just the part that hit
    pad = (width[0] / across, width[1] / across)
    tight = tuple(min(h[k] for h in hits) - pad[k] for k in range(2))
    reach = tuple(max(h[k] for h in hits) + pad[k] - tight[k] for k in range(2))
    if reach[0] * reach[1] > width[0] * width[1] * 0.9:
        answer = first                          # already spread over the whole box
    else:
        inside_points = []
        answer = sweep(tight, reach)[0]
    return answer if _really_inside(one, two, inside_points) else 0.0


def _really_inside(one: Body, two: Body, points: list, wanted: int = 3) -> bool:
    """Whether at least ``wanted`` of these points are strictly inside both solids.

    Ray intervals misread surfaces in contact as overlap; a point classifier
    does not, so the ray result is only believed when this confirms it.
    """
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_State

    if not points:
        return False
    here = BRepClass3d_SolidClassifier(one.shape)
    there = BRepClass3d_SolidClassifier(two.shape)
    seen = 0
    for point in points[:: max(1, len(points) // 60)]:
        spot = gp_Pnt(*point)
        here.Perform(spot, 1e-7)
        if here.State() != TopAbs_State.TopAbs_IN:
            continue
        there.Perform(spot, 1e-7)
        if there.State() == TopAbs_State.TopAbs_IN:
            seen += 1
            if seen >= wanted:
                return True
    return False
