"""A sketch pressed onto a face: raised or cut, projected straight or wrapped round.

The raised or cut volume is the face thickened by the depth and limited to
the sketch: a prism of the sketch for a projection, the sketch redrawn on
the cylinder for a wrap.
"""
from __future__ import annotations

import math

from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakeWire)
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.gp import gp_Pnt, gp_Vec

from ...errors import CadError
from ..core.measure import bounding_span
from ..core.naming import Body, faces_of, role_names
from .booleans import cut as cut_op
from .booleans import fuse
from .sweeps import _oriented_solid, _profile

__all__ = ["emboss", "projected"]


def emboss(feature_id: str, body: Body, face_name: str, sketch, depth: float,
           cut: bool = False, wrap: bool = False) -> Body:
    """Raise the sketch off ``face_name`` by ``depth``, or cut it in that deep.

    ``wrap`` redraws the sketch on a cylindrical face, u round it and v along
    its axis, from the point nearest the sketch's origin; otherwise the sketch
    is projected along its own normal.
    """
    depth = float(depth)
    if depth <= 0:
        raise CadError("bad_parameter", "an emboss needs a positive depth", {"depth": depth})
    face = body.face(face_name)
    if face is None:
        raise CadError("unresolved_reference", f"no face named {face_name!r} on this body",
                       {"available": body.face_names()})
    if wrap:
        pieces = [_thick(region, depth, body, face_name, inward=cut)
                  for region in _wrapped(face, sketch)]
    else:
        slab = _thick(face, depth, body, face_name, inward=cut)
        prism = _prism(sketch, bounding_span(body), face)
        common = BRepAlgoAPI_Common(slab, prism)
        common.Build()
        if not common.IsDone() or not faces_of(common.Shape()):
            raise CadError("empty_result", "the sketch does not reach the face",
                           {"face": face_name, "hint": "draw the sketch over the face"})
        pieces = _near_solids(common.Shape(), sketch.plane, depth)
    shape = _compound(pieces) if len(pieces) > 1 else pieces[0]
    tool = Body(shape, role_names(feature_id, faces_of(shape)))
    return (cut_op if cut else fuse)(feature_id, body, tool)


def _compound(shapes: list):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    out = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(out)
    for shape in shapes:
        builder.Add(out, shape)
    return out


def _prism(sketch, span: float, face):
    """The sketch's profile swept through the part both ways; the piece nearest the sketch is kept."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    profile, _ = _profile(sketch)
    n = sketch.plane.normal
    # starting a little behind the plane keeps the prism's end off a face it would lie in
    back = gp_Trsf()
    back.SetTranslation(gp_Vec(*[-n[i] * span for i in range(3)]))
    moved = BRepBuilderAPI_Transform(profile, back, True).Shape()
    prism = BRepPrimAPI_MakePrism(moved, gp_Vec(*[n[i] * 2 * span for i in range(3)]))
    prism.Build()
    if not prism.IsDone():
        raise CadError("extrude_failed", "the sketch could not be swept through the part")
    return prism.Shape()


def _near_solids(shape, plane, depth: float) -> list:
    """Of the pieces the prism cut out of the slab, those on the sketch's side of the part.

    A prism through a can meets the slab twice; the letters on the far side
    are a slab's depth and more further from the sketch plane than the near ones.
    """
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from .split import _solids

    pieces = _solids(shape)
    if len(pieces) <= 1:
        return [_oriented_solid(shape)]
    n, o = plane.normal, plane.origin

    def away(solid) -> float:
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(solid, props)
        c = props.CentreOfMass()
        return abs(sum((q - o[i]) * n[i] for i, q in enumerate((c.X(), c.Y(), c.Z()))))

    nearest = min(away(p) for p in pieces)
    return [_oriented_solid(p) for p in pieces if away(p) <= nearest + 2 * depth]


def _thick(face, depth: float, body: Body, face_name: str, inward: bool):
    """The face thickened by ``depth``, outside the part or (``inward``) inside it.

    OCCT's sign for the offset follows the face's orientation, which a cut or
    a mirror may have turned: the side is read off the result instead, by
    whether a point on the moved face lies inside the part.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.TopAbs import TopAbs_State

    surface = BRepAdaptor_Surface(faces_of(face)[0])
    on = surface.Value((surface.FirstUParameter() + surface.LastUParameter()) / 2,
                       (surface.FirstVParameter() + surface.LastVParameter()) / 2)
    start = BRepBuilderAPI_MakeVertex(on).Vertex()
    for sign in (1.0, -1.0):
        algo = BRepOffsetAPI_MakeThickSolid()
        algo.MakeThickSolidBySimple(face, sign * depth)
        algo.Build()
        if not algo.IsDone():
            continue
        solid = _oriented_solid(algo.Shape())
        # the moved face is the one about a depth away from a point on the original
        nearest, moved = None, None
        for candidate in faces_of(solid):
            gap = BRepExtrema_DistShapeShape(start, candidate)
            gap.Perform()
            if not gap.IsDone() or not gap.NbSolution() or gap.Value() < depth * 0.5:
                continue
            if nearest is None or abs(gap.Value() - depth) < nearest:
                nearest, moved = abs(gap.Value() - depth), gap.PointOnShape2(1)
        if moved is None:
            continue
        classifier = BRepClass3d_SolidClassifier(body.shape)
        classifier.Perform(moved, depth * 0.05)
        if (classifier.State() == TopAbs_State.TopAbs_IN) == inward:
            return solid
    raise CadError("thicken_failed", f"the face {face_name!r} could not be thickened",
                   {"depth": depth})


def _wrapped(face, sketch) -> list:
    """The sketch drawn on a cylindrical face, one face per loop: u round the axis, v along it."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLib import BRepLib
    from OCP.Geom import Geom_CylindricalSurface
    from OCP.Geom2d import Geom2d_Line, Geom2d_TrimmedCurve
    from OCP.Geom2dAPI import Geom2dAPI_PointsToBSpline
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.gp import gp_Dir2d, gp_Pnt2d
    from OCP.collections import Array1_gp_Pnt2d
    from OCP.GeomAbs import GeomAbs_SurfaceType

    adaptor = BRepAdaptor_Surface(face)
    if adaptor.GetType() != GeomAbs_SurfaceType.GeomAbs_Cylinder:
        raise CadError("not_a_cylinder", "a wrap goes round a cylindrical face",
                       {"hint": "use a projection on any other face"})
    surface = Geom_CylindricalSurface(adaptor.Cylinder())
    radius = adaptor.Cylinder().Radius()
    origin = GeomAPI_ProjectPointOnSurf(gp_Pnt(*sketch.plane.origin), surface)
    if origin.NbPoints() < 1:
        raise CadError("cannot_project", "the sketch origin has no nearest point on the face")
    theta0, z0 = origin.LowerDistanceParameters()

    def at(u: float, v: float) -> gp_Pnt2d:
        return gp_Pnt2d(theta0 + u / radius, z0 + v)

    faces = []
    for loop in sketch.loops:
        wire = BRepBuilderAPI_MakeWire()
        for seg in loop:
            if seg.kind == "line":
                a, b = at(*seg.start), at(*seg.end)
                length = a.Distance(b)
                if length < 1e-9:
                    continue
                curve = Geom2d_TrimmedCurve(Geom2d_Line(a, gp_Dir2d(b.X() - a.X(), b.Y() - a.Y())),
                                            0.0, length)
            else:
                points = seg.sample(64)
                array = Array1_gp_Pnt2d(1, len(points))
                for i, (u, v) in enumerate(points, start=1):
                    array.SetValue(i, at(u, v))
                curve = Geom2dAPI_PointsToBSpline(array).Curve()
            edge = BRepBuilderAPI_MakeEdge(curve, surface).Edge()
            BRepLib.BuildCurves3d_s(edge)
            wire.Add(edge)
        if not wire.IsDone():
            raise CadError("bad_profile", "the wrapped loop does not close",
                           {"loop": [seg.name for seg in loop]})
        maker = BRepBuilderAPI_MakeFace(surface, wire.Wire())
        if not maker.IsDone():
            raise CadError("bad_profile", "the wrapped loop does not make a face")
        faces.append(maker.Face())
    return faces


def projected(body: Body, sketch, direction=None, deflection: float = 0.05) -> dict:
    """The sketch's curves projected onto the body along a direction (its normal by default).

    Read only: polylines in world mm, one per projected edge, for a viewport
    or for a check of where a marking would land.
    """
    from OCP.BRepProj import BRepProj_Projection
    from OCP.gp import gp_Dir
    from ..core.naming import edges_of
    from .split import _length, _sampled
    from .sweeps import _segment_edge

    direction = direction or sketch.plane.normal
    curves = []
    for loop in sketch.loops:
        wire = BRepBuilderAPI_MakeWire()
        for seg in loop:
            wire.Add(_segment_edge(seg, sketch.plane))
        if not wire.IsDone():
            raise CadError("bad_profile", "the sketch loop does not make a wire")
        algo = BRepProj_Projection(wire.Wire(), body.shape, gp_Dir(*[float(c) for c in direction]))
        if not algo.IsDone():
            continue
        for edge in edges_of(algo.Shape()):
            points = _sampled(edge, deflection)
            if len(points) >= 2:
                curves.append({"points": points, "length_mm": round(_length(edge), 6)})
    if not curves:
        raise CadError("cannot_project", "the sketch projects onto nothing",
                       {"direction": [float(c) for c in direction]})
    return {"direction": [round(float(c), 6) for c in direction], "curves": curves,
            "length_mm": round(sum(c["length_mm"] for c in curves), 6)}
