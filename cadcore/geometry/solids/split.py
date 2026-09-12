"""Splitting a body at a plane or a surface, and reading its section there.

A split keeps one side or both; a section is the curves and the area where a
plane passes through, read off the body without changing it.
"""
from __future__ import annotations

import math

from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Section, BRepAlgoAPI_Splitter
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp_Explorer
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

from ...errors import CadError
from ..core.measure import bounding_span, edge_geometry
from ..core.naming import Body, edges_of, faces_of
from ..core.occ import ListOfShape
from .booleans import _inherit

__all__ = ["split", "section"]

SIDES = ("above", "below", "both")


def plane_face(body: Body, origin, normal):
    """A flat face through ``origin`` with ``normal``, wide enough to cross the body."""
    span = bounding_span(body) * 1.5 or 1.0
    plane = gp_Pln(gp_Pnt(*[float(c) for c in origin]), gp_Dir(*[float(c) for c in normal]))
    return BRepBuilderAPI_MakeFace(plane, -span, span, -span, span).Face()


def split(feature_id: str, body: Body, tool, keep: str = "above",
          origin=None, normal=None) -> Body:
    """Cut the body in two at ``tool`` (a plane face or a surface body) and keep a side.

    ``above`` is the side the plane's normal points to; for a surface tool,
    the side its faces' normals point to. ``both`` keeps the two pieces as
    separate solids in one body.
    """
    if keep not in SIDES:
        raise CadError("bad_parameter", "keep must be above, below or both", {"given": keep})
    tool_shape = tool.shape if isinstance(tool, Body) else tool
    splitter = BRepAlgoAPI_Splitter()
    args, tools = ListOfShape(), ListOfShape()
    args.Append(body.shape)
    tools.Append(tool_shape)
    splitter.SetArguments(args)
    splitter.SetTools(tools)
    splitter.Build()
    if not splitter.IsDone():
        raise CadError("split_failed", "the split did not build")
    pieces = _inherit(splitter, [body], splitter.Shape(), feature_id)
    solids = _solids(pieces.shape)
    if len(solids) < 2:
        raise CadError("split_failed", "the tool does not pass through the body",
                       {"hint": "move the plane so it crosses the part"})
    if keep == "both":
        return pieces
    sides = [_above(solid, tool_shape, origin, normal) for solid in solids]
    kept = [s for s, up in zip(solids, sides) if up == (keep == "above")]
    if not kept:
        raise CadError("split_failed", f"nothing is {keep} the tool", {"sides": sides})
    return _only(feature_id, pieces, kept)


def _solids(shape) -> list:
    out, walk = [], TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SOLID)
    while walk.More():
        out.append(walk.Current())
        walk.Next()
    return out


def _centroid(shape) -> tuple:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    c = props.CentreOfMass()
    return (c.X(), c.Y(), c.Z())


def _above(solid, tool, origin, normal) -> bool:
    """Whether the solid's centre of mass is on the normal side of the tool."""
    c = _centroid(solid)
    if origin is not None and normal is not None:
        return sum((c[i] - origin[i]) * normal[i] for i in range(3)) > 0
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.gp import gp_Vec

    near = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(gp_Pnt(*c)).Vertex(), tool)
    if not near.IsDone() or near.NbSolution() < 1:
        return True
    on = near.PointOnShape2(1)
    face = min(faces_of(tool), key=lambda f: BRepExtrema_DistShapeShape(
        BRepBuilderAPI_MakeVertex(on).Vertex(), f).Value())
    from OCP.BRep import BRep_Tool
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    surface = BRep_Tool.Surface_s(face)
    project = GeomAPI_ProjectPointOnSurf(on, surface)
    u, v = project.LowerDistanceParameters()
    at, n = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face).Normal(u, v, at, n)
    return gp_Vec(on, gp_Pnt(*c)).Dot(n) > 0


def _only(feature_id: str, pieces: Body, solids: list) -> Body:
    """The body reduced to these solids, names of the rest dropped."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    if len(solids) == 1:
        shape = solids[0]
    else:
        shape = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(shape)
        for solid in solids:
            builder.Add(shape, solid)
    live = faces_of(shape)
    kept = [(n, f) for n, f in pieces.names if any(f.IsSame(g) for g in live)]
    gone = [n for n, _ in pieces.names if not any(n == k for k, _ in kept)]
    return Body(shape, kept, dict(pieces.aliases), list(pieces.dropped) + gone,
                dict(pieces.notes))


def section(body: Body, origin, normal, deflection: float = 0.05) -> dict:
    """The curves where a plane passes through the body, and the area it cuts.

    Curves come back as polylines in world mm, each with its kind (line,
    circle, or the OCCT curve name); ``area_mm2`` is the cut face's area,
    zero for a shell.
    """
    face = plane_face(body, origin, normal)
    cutter = BRepAlgoAPI_Section(body.shape, face)
    cutter.Build()
    if not cutter.IsDone():
        raise CadError("empty_section", "the section did not build")
    curves = []
    for edge in edges_of(cutter.Shape()):
        kind, _ = edge_geometry(edge)
        points = _sampled(edge, deflection)
        if len(points) >= 2:
            curves.append({"kind": kind, "points": points,
                           "length_mm": round(_length(edge), 6)})
    if not curves:
        raise CadError("empty_section", "the plane misses the body",
                       {"origin": list(origin), "normal": list(normal)})
    area = 0.0
    common = BRepAlgoAPI_Common(body.shape, face)
    if common.IsDone():
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(common.Shape(), props)
        area = props.Mass()
    return {"origin": [round(float(c), 6) for c in origin],
            "normal": [round(float(c), 6) for c in normal],
            "curves": curves, "area_mm2": round(area, 6),
            "length_mm": round(sum(c["length_mm"] for c in curves), 6)}


def _sampled(edge, deflection: float) -> list:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection

    sampler = GCPnts_QuasiUniformDeflection(BRepAdaptor_Curve(edge), float(deflection))
    if not sampler.IsDone():
        return []
    return [[round(sampler.Value(i).X(), 6), round(sampler.Value(i).Y(), 6),
             round(sampler.Value(i).Z(), 6)] for i in range(1, sampler.NbPoints() + 1)]


def _length(edge) -> float:
    props = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, props)
    return props.Mass()
