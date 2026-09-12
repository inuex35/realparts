"""Standard screws, nuts and washers as solids with faces named for what they are."""
from __future__ import annotations

import math

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.gp import gp_Ax2, gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

from ... import names
from ..core.naming import Body, face_info, faces_of

#: what the faces of each kind are called, from the top down: the head or
#: nut sits on z = 0 and a screw's shank hangs below it
ROLES = ("top", "flat", "head", "under", "shank", "tip", "bore", "outer", "+z", "-z")


def fastener(feature_id: str, spec: dict, at=(0.0, 0.0, 0.0), axis=(0.0, 0.0, 1.0)) -> Body:
    """A standard part from :func:`cadcore.model.fasteners.part`, its head on the
    plane through ``at`` facing ``axis`` and the shank going the other way.

    Faces: ``top`` and ``under`` the head, ``head`` (round) or ``flat`` (six of
    them, ``flat``, ``flat#2`` ...), ``shank`` and ``tip``; a nut has ``+z``,
    ``-z``, its flats and a ``bore``; a washer ``+z``, ``-z``, ``outer`` and ``bore``.
    """
    kind = spec["kind"]
    if kind == "socket_head":
        shape = _fuse(_cylinder(spec["head_width"] / 2, spec["head_height"]),
                      _cylinder(spec["nominal"] / 2, spec["length"], down=True))
    elif kind == "hex_bolt":
        shape = _fuse(_hexagon(spec["head_width"], spec["head_height"]),
                      _cylinder(spec["nominal"] / 2, spec["length"], down=True))
    elif kind == "nut":
        shape = _cut(_hexagon(spec["across_flats"], spec["height"]),
                     _cylinder(spec["nominal"] / 2, spec["height"] * 3, down=True,
                               from_z=spec["height"]))
    else:
        shape = _cut(_cylinder(spec["outer"] / 2, spec["thickness"]),
                     _cylinder(spec["inner"] / 2, spec["thickness"] * 3, down=True,
                               from_z=spec["thickness"]))
    body = Body(shape, _named(feature_id, shape, spec))
    return _placed(body, at, axis)


def _cylinder(radius: float, height: float, down: bool = False, from_z: float = 0.0):
    start = gp_Pnt(0.0, 0.0, from_z)
    direction = gp_Dir(0.0, 0.0, -1.0 if down else 1.0)
    return BRepPrimAPI_MakeCylinder(gp_Ax2(start, direction), radius, height).Shape()


def _hexagon(across_flats: float, height: float):
    """A hexagonal prism standing on z = 0, a flat facing +x."""
    radius = across_flats / math.sqrt(3.0)          # across corners, halved
    polygon = BRepBuilderAPI_MakePolygon()
    for k in range(6):
        angle = math.radians(30.0 + 60.0 * k)
        polygon.Add(gp_Pnt(radius * math.cos(angle), radius * math.sin(angle), 0.0))
    polygon.Close()
    face = BRepBuilderAPI_MakeFace(polygon.Wire(), True).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, height)).Shape()


def _fuse(a, b):
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

    fused = BRepAlgoAPI_Fuse(a, b).Shape()
    unify = ShapeUpgrade_UnifySameDomain(fused, True, True, False)
    unify.Build()                   # one head face, not a ring of them at the join
    return unify.Shape()


def _cut(a, b):
    return BRepAlgoAPI_Cut(a, b).Shape()


def _named(feature_id: str, shape, spec: dict) -> list:
    """Name each face by what it is: planes by height, cylinders by radius."""
    kind = spec["kind"]
    nominal = spec["nominal"]
    out, flats = [], 0
    for face in faces_of(shape):
        info = face_info(face)
        z = info["centre"][2]
        if info.get("shape") == "plane":
            n = info["normal"]
            if abs(n[2]) < 0.5:
                flats += 1
                role = names.face(feature_id, "flat", flats)
            elif kind in ("socket_head", "hex_bolt"):
                if n[2] > 0:
                    role = names.face(feature_id, "top")
                elif z > -1e-6:
                    role = names.face(feature_id, "under")
                else:
                    role = names.face(feature_id, "tip")
            else:
                role = names.face(feature_id, "+z" if n[2] > 0 else "-z")
        elif info.get("shape") == "cylinder":
            radius = info["radius"]
            if kind in ("socket_head", "hex_bolt"):
                role = names.face(feature_id, "shank" if abs(radius - nominal / 2) < 1e-6
                                  else "head")
            elif kind == "nut":
                role = names.face(feature_id, "bore")
            else:
                role = names.face(feature_id, "bore" if abs(radius - spec["inner"] / 2) < 1e-6
                                  else "outer")
        else:
            role = names.face(feature_id, "face", len(out) + 1)
        out.append((role, face))
    return out


def _placed(body: Body, at, axis) -> Body:
    from ..assembly.assembly import transformed

    ax = [float(c) for c in axis]
    if list(at) == [0.0, 0.0, 0.0] and ax == [0.0, 0.0, 1.0]:
        return body
    trsf = gp_Trsf()
    trsf.SetDisplacement(gp_Ax3(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0)),
                         gp_Ax3(gp_Pnt(*[float(c) for c in at]), gp_Dir(*ax)))
    return transformed(body, trsf, rename=True)
