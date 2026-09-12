"""The two primitives everything else is built from.

They exist as features rather than as sketch-and-extrude because a box is a box:
naming its faces by their axis (``base/+z``) is more useful than naming them
after a rectangle's segments.
"""
from __future__ import annotations

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from ...errors import CadError
from ..core.naming import Body, faces_of, role_names


def box(feature_id: str, size, at=(0.0, 0.0, 0.0), centred=True) -> Body:
    w, d, h = (float(v) for v in size)
    if min(w, d, h) <= 0:
        raise CadError("bad_parameter", "box dimensions must be positive",
                       {"size": [w, d, h]})
    x, y, z = (float(v) for v in at)
    corner = gp_Pnt(x - w / 2, y - d / 2, z - h / 2) if centred else gp_Pnt(x, y, z)
    shape = BRepPrimAPI_MakeBox(corner, w, d, h).Shape()
    return Body(shape, role_names(feature_id, faces_of(shape)))


def cylinder(feature_id: str, radius, height, at=(0.0, 0.0, 0.0), axis=(0, 0, 1),
             centred=True) -> Body:
    r, h = float(radius), float(height)
    if r <= 0 or h <= 0:
        raise CadError("bad_parameter", "cylinder radius and height must be positive",
                       {"radius": r, "height": h})
    ax = gp_Dir(*[float(v) for v in axis])
    base = gp_Pnt(*[float(v) for v in at])
    if centred:                       # shift back along the axis by h/2
        base = gp_Pnt(base.X() - ax.X() * h / 2, base.Y() - ax.Y() * h / 2,
                      base.Z() - ax.Z() * h / 2)
    shape = BRepPrimAPI_MakeCylinder(gp_Ax2(base, ax), r, h).Shape()
    return Body(shape, role_names(feature_id, faces_of(shape)))


