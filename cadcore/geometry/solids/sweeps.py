"""Sweeps: turning a solved sketch into a solid, with the names intact.

Extrude, revolve, loft and sweep all do the same two things -- build a face from
the sketch's loops, then move it through space -- and all four share a naming
problem: OCCT reports ``Generated`` for some swept edges and not others. So they
share the profile builder, the history walk, and the geometric fallback that
follows a segment to the middle of its own sweep.
"""
from __future__ import annotations

import math

from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakeVertex, BRepBuilderAPI_MakeWire,
                                BRepBuilderAPI_TransitionMode)
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepLib import BRepLib
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell, BRepOffsetAPI_ThruSections
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.BRepTools import BRepTools
from OCP.GC import GC_MakeArcOfCircle
from OCP.GeomAPI import GeomAPI_PointsToBSpline
from ..core.occ import Array1OfPnt
from OCP.ShapeFix import ShapeFix_Face
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax1, gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

from ... import names
from OCP.BRepCheck import BRepCheck_Analyzer

from ...errors import CadError
from ..core.naming import Body, edges_of, faces_of, role_names


def _segment_edge(seg, plane):
    """One sketch segment as an OCCT edge, in 3D on the sketch plane."""
    if seg.kind == "circle":
        centre = gp_Pnt(*plane.to_3d(*seg.centre))
        axis = gp_Ax2(centre, gp_Dir(*plane.normal), gp_Dir(*plane.x_axis))
        return BRepBuilderAPI_MakeEdge(gp_Circ(axis, seg.radius)).Edge()
    if seg.kind == "ellipse":
        from OCP.gp import gp_Elips
        centre = gp_Pnt(*plane.to_3d(*seg.centre))
        c, s = math.cos(seg.angle), math.sin(seg.angle)
        y = plane.y_axis()
        major = gp_Dir(*[plane.x_axis[i] * c + y[i] * s for i in range(3)])
        axis = gp_Ax2(centre, gp_Dir(*plane.normal), major)
        return BRepBuilderAPI_MakeEdge(gp_Elips(axis, seg.radius, seg.minor)).Edge()
    p1 = gp_Pnt(*plane.to_3d(*seg.start))
    p2 = gp_Pnt(*plane.to_3d(*seg.end))
    if p1.Distance(p2) < 1e-9:
        raise CadError("degenerate_segment", f"segment {seg.name!r} has zero length")
    if seg.kind == "line":
        return BRepBuilderAPI_MakeEdge(p1, p2).Edge()
    if seg.kind == "spline":
        # interpolated through its own points, which are ordinary sketch points:
        # each one can be constrained and dimensioned like any other
        array = Array1OfPnt(1, len(seg.through))
        for i, uv in enumerate(seg.through, start=1):
            array.SetValue(i, gp_Pnt(*plane.to_3d(*uv)))
        curve = GeomAPI_PointsToBSpline(array).Curve()
        if curve is None:
            raise CadError("bad_profile", f"spline {seg.name!r} could not be fitted")
        return BRepBuilderAPI_MakeEdge(curve).Edge()
    # three points beat a centre and two angles: the arc then cannot come out
    # sweeping the long way round, whatever the solver did to the angles
    mid = gp_Pnt(*plane.to_3d(*seg.mid()))
    arc = GC_MakeArcOfCircle(p1, mid, p2)
    if not arc.IsDone():
        raise CadError("bad_profile", f"arc {seg.name!r} is degenerate")
    return BRepBuilderAPI_MakeEdge(arc.Value()).Edge()


def _profile(sketch) -> tuple:
    """The sketch as a face -- outline plus holes -- with its edges named.

    A loop inside another is a hole in it, so a plate with a bolt pattern is
    one sketch and one feature rather than five. Loops inside no other are
    each an outline of their own, and the result is then a compound of faces
    (the letters of a text).
    """
    wires, named = [], []
    for loop in sketch.loops:
        wire = BRepBuilderAPI_MakeWire()
        for seg in loop:
            edge = _segment_edge(seg, sketch.plane)
            named.append((seg.name, edge))
            wire.Add(edge)
        if not wire.IsDone():
            raise CadError("bad_profile", "could not build a wire from the sketch",
                           {"loop": [seg.name for seg in loop]})
        wires.append(wire.Wire())

    faces = []
    for outer, holes in _nested(sketch.loops):
        maker = BRepBuilderAPI_MakeFace(wires[outer])
        if not maker.IsDone():
            raise CadError("bad_profile", "the sketch outline is not planar or self-intersects")
        for hole in holes:
            maker.Add(TopoDS.Wire_s(wires[hole].Reversed()))
        fix = ShapeFix_Face(maker.Face())
        fix.Perform()
        fix.FixOrientation()
        face = fix.Face()
        # ShapeFix is asked to repair the face and never asked whether it managed.
        # A hole drawn across the outline it is meant to be inside comes back as a
        # face OpenCASCADE will not accept, and everything downstream builds an
        # invalid solid out of it and reports a size
        if not BRepCheck_Analyzer(face).IsValid():
            raise CadError(
                "bad_profile",
                "the sketch does not make a face: the outline may cross itself, or "
                "a hole may not be inside it",
                {"loops": [[seg.name for seg in loop] for loop in sketch.loops]})
        faces.append(face)
    if len(faces) == 1:
        return faces[0], named
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for face in faces:
        builder.Add(compound, face)
    return compound, named


def _nested(loops: list) -> list:
    """``(outline index, [hole indexes])`` per outline; loops are largest first.

    A loop is a hole of the smallest loop that contains it an odd number of
    loops deep; deeper still, it is an outline again (an island in a hole).
    """
    samples = [[p for seg in loop for p in seg.sample(24)] for loop in loops]
    inside = [[j for j in range(len(loops)) if j != i and _contains(samples[j], samples[i][0])]
              for i in range(len(loops))]
    out = []
    for i, within in enumerate(inside):
        if len(within) % 2 == 0:
            holes = [j for j, w in enumerate(inside)
                     if len(w) == len(within) + 1 and i in w]
            out.append((i, holes))
    return out


def _contains(polygon: list, point) -> bool:
    """Even-odd test of a point against a sampled loop, in sketch (u, v)."""
    x, y = point
    hit = False
    for k in range(len(polygon)):
        (x0, y0), (x1, y1) = polygon[k - 1], polygon[k]
        if (y0 > y) != (y1 > y):
            at = x0 + (y - y0) * (x1 - x0) / ((y1 - y0) or 1e-300)
            if at > x:
                hit = not hit
    return hit


def _face_at(point, faces):
    """The face a point lies on, or None -- used when history is silent."""
    vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*point)).Vertex()
    best, best_d = None, 1e-6
    for f in faces:
        dist = BRepExtrema_DistShapeShape(vertex, f)
        dist.Perform()
        if dist.IsDone() and dist.Value() < best_d:
            best, best_d = f, dist.Value()
    return best


def _name_swept(feature_id: str, segments, at_middle, body: Body, live) -> None:
    """Name what the sweep's history forgot, by where the segment ended up.

    OCCT reports ``Generated`` for some swept edges and not others -- a revolve
    names its cylinders but stays silent about the annulus a radial edge sweeps
    out. Rather than let those faces fall back to a positional index, each
    unnamed segment is followed to the middle of its own sweep: the face that
    point lands on is the face that segment made.
    """
    for seg, mid in segments:
        if any(n == names.face(feature_id, seg) for n, _ in body.names):
            continue
        f = _face_at(at_middle(mid), [x for x in live if body.name_of(x) is None])
        if f is not None:
            body.names.append((names.face(feature_id, seg), f))


def _name_generated(algo, feature_id: str, face, named_edges, body: Body, live) -> None:
    """Name each face the profile's edges generated, after its own segment.

    MakeWire/MakeFace copy the edges, so the sweep's history does not know the
    ones built above; each segment is re-found inside the face by its midpoint.
    """
    face_edges = edges_of(face)
    for name, edge in named_edges:
        mid = _edge_mid(edge)
        match = min(face_edges, key=lambda e: math.dist(_edge_mid(e), mid), default=None)
        if match is None or math.dist(_edge_mid(match), mid) > 1e-6:
            continue
        used = 0
        for gen in algo.Generated(match):
            try:
                f = TopoDS.Face_s(gen)
            except Exception:                                    # noqa: BLE001
                continue
            f = next((x for x in live if x.IsSame(f)), None)
            if f is None or body.name_of(f) is not None:
                continue
            # one segment can generate several faces (a sweep bends the profile
            # once per path segment); number them rather than repeat a name
            used += 1
            body.names.append((names.face(feature_id, name, used), f))


def extrude(feature_id: str, sketch, distance: float) -> Body:
    """Prism a solved sketch, naming each side face after its sketch segment.

    The names come from the sketch, not from OCCT: ``profile/right`` is the face
    generated by the segment the designer called ``right``, and it keeps that
    name however the dimensions move. The prism's own history (``Generated``,
    ``FirstShape``, ``LastShape``) supplies the mapping, so this is provenance,
    not geometric guesswork.
    """
    d = float(distance)
    if abs(d) < 1e-9:
        raise CadError("bad_parameter", "extrude distance must be non-zero")
    face, named_edges = _profile(sketch)

    n = sketch.plane.normal
    prism = BRepPrimAPI_MakePrism(face, gp_Vec(n[0] * d, n[1] * d, n[2] * d))
    prism.Build()
    if not prism.IsDone():
        raise CadError("extrude_failed", "the prism did not build")
    shape = _oriented_solid(prism.Shape())

    body = Body(shape, [], {}, [])
    live = faces_of(shape)
    _name_generated(prism, feature_id, face, named_edges, body, live)
    _name_swept(feature_id, [(seg.name, sketch.plane.to_3d(*seg.mid()))
                             for loop in sketch.loops for seg in loop],
                lambda p: (p[0] + n[0] * d / 2, p[1] + n[1] * d / 2, p[2] + n[2] * d / 2),
                body, live)
    for role, sub in (("bottom", prism.FirstShape()), ("top", prism.LastShape())):
        try:
            f = TopoDS.Face_s(sub)
        except Exception:                                        # noqa: BLE001
            continue
        f = next((x for x in live if x.IsSame(f)), None)
        if f is not None and body.name_of(f) is None:
            # a sketch segment may be called `top` too, and the cap is what
            # `top` means on a prism: the swept side takes the `#2`
            wanted = names.face(feature_id, role)
            body.names = [(names.unused(n, {wanted}) if n == wanted else n, x)
                          for n, x in body.names]
            body.names.append((wanted, f))
    unnamed = [f for f in live if body.name_of(f) is None]
    body.names.extend(role_names(feature_id, unnamed))
    return body


def revolve(feature_id: str, sketch, angle_deg: float, origin=None, direction=None) -> Body:
    """Revolve a solved sketch about an axis in its own plane.

    The axis defaults to the sketch plane's x axis through its origin, which is
    the lathe convention: draw the half-section, spin it. Segment names carry
    over exactly as they do for an extrusion.
    """
    angle = math.radians(float(angle_deg))
    if abs(angle) < 1e-9:
        raise CadError("bad_parameter", "revolve angle must be non-zero")
    if abs(angle) > 2 * math.pi + 1e-9:
        raise CadError("bad_parameter", "revolve angle must be at most 360 degrees")
    face, named_edges = _profile(sketch)

    base = gp_Pnt(*[float(v) for v in (origin if origin is not None else sketch.plane.origin)])
    axis_dir = gp_Dir(*[float(v) for v in
                        (direction if direction is not None else sketch.plane.x_axis)])
    algo = BRepPrimAPI_MakeRevol(face, gp_Ax1(base, axis_dir), angle)
    algo.Build()
    if not algo.IsDone():
        raise CadError("revolve_failed", "the revolve did not build",
                       {"hint": "the profile may cross the axis"})
    shape = _oriented_solid(algo.Shape())

    body = Body(shape, [], {}, [])
    live = faces_of(shape)
    _name_generated(algo, feature_id, face, named_edges, body, live)
    half = gp_Trsf()
    half.SetRotation(gp_Ax1(base, axis_dir), angle / 2)
    _name_swept(feature_id, [(seg.name, sketch.plane.to_3d(*seg.mid()))
                             for loop in sketch.loops for seg in loop],
                lambda p: gp_Pnt(*p).Transformed(half).Coord(), body, live)
    for role, sub in (("start", algo.FirstShape()), ("end", algo.LastShape())):
        try:
            f = TopoDS.Face_s(sub)
        except Exception:                                        # noqa: BLE001
            continue
        f = next((x for x in live if x.IsSame(f)), None)
        if f is not None and body.name_of(f) is None:
            body.names.append((names.face(feature_id, role), f))
    body.names.extend(role_names(feature_id, [f for f in live if body.name_of(f) is None]))
    return body


def loft(feature_id: str, sketches: list, ruled: bool = False) -> Body:
    """Loft through a series of sketches, keeping the first one's segment names.

    Corresponding segments have to be named consistently across the sections --
    ``front`` lofts to ``front`` -- which is also what makes the result
    predictable rather than a surprise about how OCCT paired the wires.
    """
    if len(sketches) < 2:
        raise CadError("bad_parameter", "a loft needs at least two sections",
                       {"given": len(sketches)})
    algo = BRepOffsetAPI_ThruSections(True, ruled, 1e-6)
    outline = None
    for sketch in sketches:
        face, named = _profile(sketch)
        if outline is None:
            outline = (face, named)
        algo.AddWire(_outer_wire(face, "a loft section's"))
    algo.Build()
    if not algo.IsDone():
        raise CadError("loft_failed", "the loft did not build",
                       {"hint": "sections must have matching segment counts and orientation"})
    shape = _oriented_solid(algo.Shape())
    body = Body(shape, [], {}, [])
    live = faces_of(shape)
    _name_generated(algo, feature_id, outline[0], outline[1], body, live)
    for role, sub in (("start", algo.FirstShape()), ("end", algo.LastShape())):
        try:
            f = next((x for x in live if x.IsSame(TopoDS.Face_s(sub))), None)
        except Exception:                                        # noqa: BLE001
            continue
        if f is not None and body.name_of(f) is None:
            body.names.append((names.face(feature_id, role), f))
    body.names.extend(role_names(feature_id, [f for f in live if body.name_of(f) is None]))
    return body


def _wire_of(sketch):
    """A sketch as a single wire -- closed loop or open chain."""
    wire = BRepBuilderAPI_MakeWire()
    for seg in sketch.loops[0]:
        wire.Add(_segment_edge(seg, sketch.plane))
    if not wire.IsDone():
        raise CadError("bad_profile", "could not build a wire from the sketch")
    return wire.Wire()


def helix_wire(radius: float, pitch: float, height: float, origin=(0, 0, 0),
               axis=(0, 0, 1), left: bool = False):
    """A helix as a wire: ``radius`` about ``axis`` from ``origin``, climbing ``pitch`` per turn.

    It starts at ``origin`` + ``radius`` along the axis frame's x direction.
    """
    from OCP.Geom import Geom_CylindricalSurface
    from OCP.Geom2d import Geom2d_Line
    from OCP.gp import gp_Ax3, gp_Dir2d, gp_Pnt2d

    if min(float(radius), float(pitch), float(height)) <= 0:
        raise CadError("bad_parameter", "a helix needs a positive radius, pitch and height",
                       {"radius": radius, "pitch": pitch, "height": height})
    surface = Geom_CylindricalSurface(gp_Ax3(gp_Pnt(*[float(c) for c in origin]),
                                             gp_Dir(*[float(c) for c in axis])), float(radius))
    rise = float(pitch) / (2 * math.pi)
    line = Geom2d_Line(gp_Pnt2d(0.0, 0.0), gp_Dir2d(-1.0 if left else 1.0, rise))
    scale = math.hypot(1.0, rise)          # the 2d line is parameterised by its own length
    turns = float(height) / float(pitch)
    edge = BRepBuilderAPI_MakeEdge(line, surface, 0.0, turns * 2 * math.pi * scale).Edge()
    BRepLib.BuildCurves3d_s(edge)
    return BRepBuilderAPI_MakeWire(edge).Wire()


def sweep(feature_id: str, profile, path=None, corner: str = "sharp", helix: dict | None = None) -> Body:
    """Sweep a profile along a path sketch, or a helix, naming faces after the profile.

    The path may be open -- that is what a sweep is usually for -- so it is
    taken as a wire rather than a face. A helix is given as radius, pitch,
    height, origin, axis and hand; the profile sits at its start.
    """
    profile_face, named = _profile(profile)
    if helix is not None:
        spine = helix_wire(helix["radius"], helix["pitch"], helix["height"],
                           helix.get("origin", (0, 0, 0)), helix.get("axis", (0, 0, 1)),
                           bool(helix.get("left")))
    elif path is not None:
        spine = _wire_of(path)
    else:
        raise CadError("bad_arguments", "a sweep needs a path or a helix")
    algo = BRepOffsetAPI_MakePipeShell(spine)
    if helix is not None:
        algo.SetMode(gp_Dir(*[float(c) for c in helix.get("axis", (0, 0, 1))]))
    # The default transition collapses a sharp corner -- an L-shaped path came
    # out at less than half its volume -- so corners are mitred unless asked
    # for round.
    algo.SetTransitionMode(BRepBuilderAPI_TransitionMode.BRepBuilderAPI_RoundCorner
                           if corner == "round"
                           else BRepBuilderAPI_TransitionMode.BRepBuilderAPI_RightCorner)
    algo.Add(_outer_wire(profile_face, "a swept"), False, False)
    algo.Build()
    if not algo.IsDone() or not algo.MakeSolid():
        raise CadError("sweep_failed", "the sweep did not build",
                       {"hint": "the path may turn tighter than the profile is wide"})
    shape = _oriented_solid(algo.Shape())
    body = Body(shape, [], {}, [])
    live = faces_of(shape)
    _name_generated(algo, feature_id, profile_face, named, body, live)
    body.names.extend(role_names(feature_id, [f for f in live if body.name_of(f) is None]))
    return body


def _outer_wire(face, what: str = "this"):
    """The face's boundary -- and a refusal if that is not all of it.

    `AddWire` takes one wire per section, so a profile with a hole in it was
    lofted and swept as though the hole were not there: a 20x20 square with an
    8x8 hole extrudes to 3360 mm3 and lofted to 4000, silently. Whether that
    can be built is a separate question; answering it with the wrong solid is
    not.
    """
    from OCP.TopAbs import TopAbs_WIRE, TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer

    walk = TopExp_Explorer(face, TopAbs_WIRE)
    wires = 0
    while walk.More():
        wires += 1
        walk.Next()
    if face.ShapeType() != TopAbs_ShapeEnum.TopAbs_FACE:
        raise CadError("bad_profile", f"{what} profile has several outlines, and this "
                                      "operation takes one closed outline")
    if wires > 1:
        raise CadError(
            "bad_profile",
            f"{what} profile has {wires - 1} hole(s) in it, and this operation "
            f"takes one closed outline",
            {"wires": wires,
             "hint": "build the outside and the inside separately and cut one "
                     "from the other -- an extrude takes holes directly"})
    return BRepTools.OuterWire_s(TopoDS.Face_s(face))


def _oriented_solid(shape):
    """A wire is drawn in whatever direction the designer wrote it, and a sweep
    inherits that: every face comes out FORWARD with inward normals for a
    clockwise profile. Face orientation is not cosmetic -- it decides the +z/-z
    roles in naming and which way "into the face" is for a pocket."""
    try:
        solid = TopoDS.Solid_s(shape)
        BRepLib.OrientClosedSolid_s(solid)
        return solid
    except Exception:                                            # noqa: BLE001
        return shape


def _edge_mid(edge):
    ad = BRepAdaptor_Curve(edge)
    p = ad.Value(0.5 * (ad.FirstParameter() + ad.LastParameter()))
    return (p.X(), p.Y(), p.Z())


def points_along(sketch, count: int) -> list:
    """`count` points at equal arc length along the sketch's wire, in 3D."""
    from OCP.BRepAdaptor import BRepAdaptor_CompCurve
    from OCP.GCPnts import GCPnts_QuasiUniformAbscissa

    curve = BRepAdaptor_CompCurve(_wire_of(sketch))
    sampler = GCPnts_QuasiUniformAbscissa(curve, count)
    if not sampler.IsDone():
        raise CadError("bad_path", "could not walk along that path",
                       {"hint": "the path sketch must be one connected chain"})
    points = []
    for i in range(1, sampler.NbPoints() + 1):
        p = curve.Value(sampler.Parameter(i))
        points.append((p.X(), p.Y(), p.Z()))
    return points
