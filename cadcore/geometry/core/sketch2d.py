"""Sketch loops OpenCASCADE makes for a sketch: text outlines and offsets of a profile.

Both come back as `Segment` loops in the sketch's own (u, v), so what follows
(extrude, pocket, a drawing) treats them like anything drawn by hand.
"""
from __future__ import annotations

import math

from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepTools import BRepTools_WireExplorer
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.TopAbs import TopAbs_Orientation, TopAbs_ShapeEnum
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from ...errors import CadError
from ...sketching.model import Plane, Segment
from .measure import edge_geometry
from .naming import faces_of

__all__ = ["text_loops", "offset_loops"]


def text_loops(name: str, text: str, height: float, at=(0.0, 0.0), angle_deg: float = 0.0,
               font: str = "DejaVu Sans", bold: bool = False, italic: bool = False) -> list:
    """The outlines of ``text`` as loops of segments in sketch (u, v).

    ``height`` is the font size: capitals come out about 0.7 of it. ``at`` is
    the pen's start, the baseline's left end; ``angle_deg`` turns the line
    about it. A font that is not installed falls back to one that is.
    """
    from OCP.Font import Font_FontAspect
    from OCP.NCollection import NCollection_String
    from OCP.StdPrs import StdPrs_BRepFont, StdPrs_BRepTextBuilder
    from OCP.TCollection import TCollection_AsciiString

    if not text:
        raise CadError("bad_parameter", f"text {name!r} is empty")
    if float(height) <= 0:
        raise CadError("bad_parameter", f"text {name!r} needs a positive height")
    aspect = {(False, False): Font_FontAspect.Font_FontAspect_Regular,
              (True, False): Font_FontAspect.Font_FontAspect_Bold,
              (False, True): Font_FontAspect.Font_FontAspect_Italic,
              (True, True): Font_FontAspect.Font_FontAspect_BoldItalic}[(bool(bold), bool(italic))]
    made = StdPrs_BRepFont.FindAndCreate_s(TCollection_AsciiString(str(font)), aspect, float(height))
    if made is None:
        raise CadError("missing_dependency", "no font could be found to draw text with",
                       {"font": font})
    shape = StdPrs_BRepTextBuilder().Perform(made, NCollection_String(str(text)))
    turn = math.radians(float(angle_deg))
    c, s = math.cos(turn), math.sin(turn)

    def to_uv(x: float, y: float) -> tuple:
        return (round(at[0] + x * c - y * s, 9), round(at[1] + x * s + y * c, 9))

    loops, count = [], 0
    for face in faces_of(shape):
        for wire in _wires(face):
            loop = _loop_of(wire, f"{name}_{count}", to_uv, float(height) * 0.004)
            if loop:
                loops.append(loop)
                count += 1
    if not loops:
        raise CadError("empty_sketch", f"text {name!r} drew nothing", {"text": text})
    return loops


def offset_loops(name: str, loops: list, plane: Plane, distance: float,
                 join: str = "arc") -> list:
    """Every loop moved outwards by ``distance`` (inwards when negative), as new loops.

    Corners are rounded (``arc``) or extended to meet (``intersection``).
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
    from OCP.GeomAbs import GeomAbs_JoinType
    from ..solids.sweeps import _segment_edge

    if abs(float(distance)) < 1e-9:
        raise CadError("bad_parameter", "an outline offset needs a non-zero distance")
    joins = {"arc": GeomAbs_JoinType.GeomAbs_Arc,
             "intersection": GeomAbs_JoinType.GeomAbs_Intersection}
    if join not in joins:
        raise CadError("bad_parameter", "join is arc or intersection", {"given": join})
    x, y, o = plane.x_axis, plane.y_axis(), plane.origin

    def to_uv(px: float, py: float, pz: float) -> tuple:
        d = (px - o[0], py - o[1], pz - o[2])
        return (round(sum(d[i] * x[i] for i in range(3)), 9),
                round(sum(d[i] * y[i] for i in range(3)), 9))

    out = []
    for k, loop in enumerate(loops):
        wire = BRepBuilderAPI_MakeWire()
        for seg in loop:
            wire.Add(_segment_edge(seg, plane))
        if not wire.IsDone():
            raise CadError("bad_profile", "the loop to offset does not make a wire",
                           {"loop": [seg.name for seg in loop]})
        algo = BRepOffsetAPI_MakeOffset(wire.Wire(), joins[join])
        algo.Perform(float(distance))
        if not algo.IsDone():
            raise CadError("offset_failed", "the outline could not be offset",
                           {"distance": distance, "loop": [seg.name for seg in loop]})
        walk = TopExp_Explorer(algo.Shape(), TopAbs_ShapeEnum.TopAbs_WIRE)
        found = 0
        while walk.More():
            made = _loop_of(TopoDS.Wire_s(walk.Current()), f"{name}_{k}_{found}",
                            lambda px, py, pz=0.0: to_uv(px, py, pz), 0.02, in_3d=True)
            if made:
                out.append(made)
                found += 1
            walk.Next()
    if not out:
        raise CadError("offset_failed", "the offset left nothing", {"distance": distance})
    return out


def _wires(face) -> list:
    from OCP.BRepTools import BRepTools

    outer = BRepTools.OuterWire_s(face)
    wires = [outer]
    walk = TopExp_Explorer(face, TopAbs_ShapeEnum.TopAbs_WIRE)
    while walk.More():
        wire = TopoDS.Wire_s(walk.Current())
        if not wire.IsSame(outer):
            wires.append(wire)
        walk.Next()
    return wires


def _loop_of(wire, prefix: str, to_uv, deflection: float, in_3d: bool = False) -> list:
    """A wire's edges, in order, as segments: lines and arcs stay what they are."""
    loop = []
    walk = BRepTools_WireExplorer(wire)
    k = 0
    while walk.More():
        edge = walk.Current()
        backwards = edge.Orientation() == TopAbs_Orientation.TopAbs_REVERSED
        walk.Next()
        seg = _segment_of(edge, f"{prefix}_{k}", to_uv, deflection, backwards, in_3d)
        if seg is not None:
            loop.append(seg)
            k += 1
    return loop


def _segment_of(edge, name: str, to_uv, deflection: float, backwards: bool,
                in_3d: bool) -> Segment | None:
    curve = BRepAdaptor_Curve(edge)
    first, last = curve.FirstParameter(), curve.LastParameter()
    kind, data = edge_geometry(edge)
    if kind == "line":
        a, b = (curve.Value(first), curve.Value(last))
        if backwards:
            a, b = b, a
        return Segment(name, "line", _uv(a, to_uv, in_3d), _uv(b, to_uv, in_3d))
    if kind == "circle":
        a, m, b = (curve.Value(first), curve.Value((first + last) / 2), curve.Value(last))
        if backwards:
            a, b = b, a
        start, mid, end = (_uv(p, to_uv, in_3d) for p in (a, m, b))
        centre = _uv(_pnt(data["centre"]), to_uv, in_3d)
        radius = round(float(data["radius"]), 9)
        if math.dist(start, end) < 1e-9:
            return Segment(name, "circle", centre, centre, centre, radius)
        cross = (start[0] - centre[0]) * (mid[1] - centre[1]) - (start[1] - centre[1]) * (mid[0] - centre[0])
        return Segment(name, "arc", start, end, centre, radius, cross > 0)
    sampler = GCPnts_QuasiUniformDeflection(curve, float(deflection))
    if not sampler.IsDone() or sampler.NbPoints() < 2:
        return None
    points = [_uv(sampler.Value(i), to_uv, in_3d) for i in range(1, sampler.NbPoints() + 1)]
    if backwards:
        points.reverse()
    if len(points) == 2:
        return Segment(name, "line", points[0], points[1])
    return Segment(name, "spline", points[0], points[-1], through=points)


def _pnt(xyz):
    from OCP.gp import gp_Pnt
    return gp_Pnt(*xyz)


def _uv(p, to_uv, in_3d: bool) -> tuple:
    return to_uv(p.X(), p.Y(), p.Z()) if in_3d else to_uv(p.X(), p.Y())
