"""Threads that are actually there."""
from __future__ import annotations

import math

from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakePolygon,
                                BRepBuilderAPI_MakeWire, BRepBuilderAPI_TransitionMode)
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
from OCP.Geom import Geom_CylindricalSurface
from OCP.Geom2d import Geom2d_Line
from OCP.gp import gp_Ax3, gp_Dir, gp_Dir2d, gp_Pnt, gp_Pnt2d, gp_Vec

from OCP.BRepCheck import BRepCheck_Analyzer

from ...errors import CadError
from ...model.fasteners import METRIC
from ..core.measure import solid_count, volume
from ..core.naming import Body, face_info, is_hole, is_round

# ISO 68-1: the sharp triangle is H = sqrt(3)/2 * pitch, truncated to H/8 at the
# crest and H/4 at the root, leaving 5H/8 of engaged flank.
SHARP = math.sqrt(3) / 2


def spec_of(standard: str | None, pitch: float | None) -> dict:
    """Pitch and thread height, from a standard name or from a given pitch."""
    if standard:
        size = METRIC.get(standard.upper())
        if size is None:
            raise CadError("unknown_fastener", f"no standard thread called {standard!r}",
                           {"available": sorted(METRIC)})
        pitch = size["pitch"]
    if not pitch or float(pitch) <= 0:
        raise CadError("bad_parameter", "a thread needs a pitch, or a standard to take one from")
    pitch = float(pitch)
    return {"pitch": pitch, "height": SHARP * pitch * 5 / 8,
            "standard": standard.upper() if standard else None}


def thread(feature_id: str, body: Body, face_name: str, standard: str | None = None,
           pitch: float | None = None, length: float | None = None,
           start: float = 0.0, hand: str = "right", clearance: float = 0.0) -> Body:
    """Cut a helical thread into a named cylindrical face.

    Outward (a hole) or inward (a shaft) is read off the face: OCCT's cylinder
    normal points away from the axis, so a hole's wall is the reversed one.
    ``clearance`` shrinks the cut all round, so a printed pair screws together.
    """
    face = body.face(face_name)
    if face is None:
        raise CadError("unresolved_reference", f"no face named {face_name!r} on this body",
                       {"available": body.face_names()[:40]})
    info = face_info(face)
    if not is_round(info):
        raise CadError("not_a_cylinder", f"{face_name!r} is not a cylinder, so it cannot be threaded",
                       {"face": face_name})

    spec = spec_of(standard, pitch)
    radius, axis = info["radius"], info["axis"]
    origin = _axis_origin(face)
    extent = _extent_along(face, origin, axis)
    reach = float(length) if length else (extent[1] - extent[0] - float(start))
    if reach <= spec["pitch"]:
        raise CadError("bad_parameter",
                       "the threaded length is shorter than one pitch",
                       {"length": reach, "pitch": spec["pitch"]})

    internal = is_hole(face)
    depth = max(spec["height"] - float(clearance), spec["pitch"] * 0.05)

    # Two ordinary booleans: turn the depth off the cylinder, then add the
    # ridge back. A helical tool on the cylinder it cuts is a tangential
    # boolean, which OCCT answers empty or with the whole part gone, without
    # an error. So nothing touches: the ridge stops short of the relief's ends
    # and its root reaches into material that is certainly there.
    low = max(extent[0], extent[0] + float(start))
    high = min(extent[1], low + reach)
    if high - low <= spec["pitch"]:
        raise CadError("bad_parameter", "the face is shorter than one pitch",
                       {"length": high - low, "pitch": spec["pitch"]})
    inset = spec["pitch"] * 0.05
    out = _cut_relief(feature_id, body, radius, depth, origin, axis, low, high, internal)
    out = _add_thread(feature_id, out, radius, origin, axis, spec["pitch"], depth,
                      low + inset, high - inset, internal, hand != "left",
                      float(clearance))
    # appended: a part may carry more than one thread
    out.notes.setdefault("threads", []).append({"face": face_name,
                           "designation": (f"{spec['standard']}x{spec['pitch']:g}"
                                           if spec["standard"] else
                                           f"{radius * 2:g}x{spec['pitch']:g}"),
                           "hand": "left" if hand == "left" else "right",
                           "internal": internal,
                           "length_mm": round(reach, 3),
                           "clearance_mm": float(clearance)})
    # and the singular key kept, meaning the same thing it always did -- the
    # thread most recently cut -- so nothing reading it has to change at once
    out.notes["thread"] = out.notes["threads"][-1]
    return out


def _cut_relief(feature_id: str, body: Body, radius: float, depth: float,
                origin, axis, low: float, high: float, internal: bool) -> Body:
    """Turn the thread's depth off the cylinder before the ridge is added.

    A plain ring is the boolean OCCT is good at. The result is measured: a
    ring of known size removes a known volume, and a cut that removed
    something else (OCCT can hand back the whole part, or a negative solid,
    without an error) is refused.
    """
    from .primitives import cylinder
    from .booleans import cut as _boolean_cut

    inner, outer = ((radius - depth, radius) if not internal
                    else (radius, radius + depth))
    at = [origin[i] + axis[i] * low for i in range(3)]
    length = high - low
    core_at = [at[i] - axis[i] * length * 0.01 for i in range(3)]
    want = math.pi * (outer ** 2 - inner ** 2) * length
    before = volume(body)
    out = None          # every attempt may raise, and the refusal reads it
    for fuzzy in (0.0, 1e-6, 1e-5, 1e-4):
        ring = cylinder(feature_id + "_relief", outer, length, at, axis, centred=False)
        core = cylinder(feature_id + "_core", inner, length * 1.02, core_at, axis,
                        centred=False)
        try:
            out = _boolean_cut(feature_id, body,
                               _boolean_cut(feature_id + "_r", ring, core), fuzzy)
        except CadError:
            continue
        removed = before - volume(out)
        if 0.25 * want <= removed <= 2.0 * want:
            return out
    raise CadError("thread_failed",
                   "turning the relief off the cylinder took the wrong amount "
                   "of material away",
                   {"expected_mm3": round(want, 1),
                    "removed_mm3": None if out is None
                    else round(before - volume(out), 1),
                    "hint": "the surrounding solid may already be too broken up "
                            "here for a clean boolean"})


# how long a stretch of thread to sweep at once, longest first: a long sweep
# can bulge and a short one can refuse to fuse, so the length is retried
_SECTION_MM = (40.0, 20.0, 10.0)


def _add_thread(feature_id: str, body: Body, radius: float, origin, axis,
                pitch: float, depth: float, begin: float, finish: float,
                internal: bool, right_hand: bool, narrow: float = 0.0) -> Body:
    """Add the thread to the turned-down cylinder, in sections, and check that each arrived.

    The ridge overlaps the core, its crest stops a hair short of the surface
    the cylinder had, and the fuse is measured: a thread of this size adds a
    known volume.
    """
    # each section starts at the phase the helix has reached, so the joins are joins
    span = finish - begin
    trouble = None
    for longest in _SECTION_MM:
        parts = max(1, math.ceil(span / longest))
        step = span / parts
        out = body
        try:
            for index in range(parts):
                out = _add_section(feature_id, out, radius, origin, axis, pitch,
                                   depth, begin, index * step, (index + 1) * step,
                                   internal, right_hand, narrow)
        except CadError as trouble_here:
            trouble = trouble_here                # shorter sections, from the top
            continue
        return out
    raise trouble


def _add_section(feature_id: str, body: Body, radius: float, origin, axis,
                 pitch: float, depth: float, base: float, begin: float, end: float,
                 internal: bool, right_hand: bool, narrow: float = 0.0) -> Body:
    """Fuse one section of thread, and insist that it arrived.

    A helical fuse can return only the tool and call it success, so the
    volume is the check; a section that did not arrive is retried with the
    tolerance widened, then from a start shifted by a fraction of a pitch.
    """
    from .booleans import fuse as _boolean_fuse

    want = _ridge_volume(radius, pitch, depth, end - begin)
    before = volume(body)
    lumps, strayed = 0, None
    for fuzzy, shift in ((0.0, 0.0), (1e-5, 0.0), (0.0, pitch * 0.13),
                         (1e-4, pitch * 0.07)):
        ridge = _helical_ridge(feature_id, radius, origin, axis, pitch, base,
                               begin - shift, end, depth, internal, right_hand,
                               narrow)
        # a sweep that bulged still adds about the right volume: the crest's radius is measured too
        strayed = _off_the_cylinder(ridge, radius, origin, axis, depth, internal)
        if strayed is not None:
            continue
        try:
            candidate = _boolean_fuse(feature_id, body, ridge, fuzzy)
        except CadError:
            continue                              # that attempt found nothing to join
        added = volume(candidate) - before
        lumps = solid_count(candidate.shape)
        # and a valid one: a helical fuse can leave a self-intersecting wire
        # with the right volume, at lengths no rule predicts
        if (0.4 * want <= added <= 2.5 * want and lumps == 1
                and BRepCheck_Analyzer(candidate.shape).IsValid()):
            return candidate
    if strayed is not None:
        raise CadError("thread_failed",
                       "the swept thread does not stay on its own cylinder",
                       {"crest_radius_mm": radius, "reached_mm": round(strayed, 3),
                        "at_mm": round(begin, 2),
                        "hint": "the sweep deformed; a shorter section will not"})
    raise CadError("thread_failed",
                   "a section of the thread would not fuse onto the part",
                   {"expected_mm3": round(want, 1), "at_mm": round(begin, 2),
                    "lumps": lumps,
                    "hint": ("the ridge sat on the part without joining it"
                             if lumps > 1 else
                             "the pitch may be too coarse for the diameter")})


def _off_the_cylinder(ridge: Body, radius: float, origin, axis, depth: float,
                      internal: bool):
    """How far the ridge strayed past the crest's surface, or None: nothing legitimate lies beyond it."""
    from ..io.tessellate import tessellate

    allow = max(depth * 0.05, 1e-3)
    reach = None
    for point in tessellate(ridge, deflection=max(depth / 4, 0.05))["vertices"]:
        along = sum((point[i] - origin[i]) * axis[i] for i in range(3))
        off = [point[i] - origin[i] - along * axis[i] for i in range(3)]
        r = math.sqrt(sum(v * v for v in off))
        if not internal and r > radius + allow:
            reach = r if reach is None else max(reach, r)
        elif internal and r < radius - allow:
            reach = r if reach is None else min(reach, r)
    return reach


def _ridge_volume(radius: float, pitch: float, depth: float, length: float) -> float:
    """How much a thread adds back: a trapezoid dragged along a helix."""
    section = (3 * pitch / 8 + pitch / 16) * depth
    helix = (length / pitch) * math.hypot(2 * math.pi * radius, pitch)
    return section * helix


def _axis_origin(face) -> tuple:
    """A point on the face's axis, through `face_info`: a cylinder held as a spline has no adaptor cylinder."""
    return tuple(face_info(face)["axis_origin"])


def _extent_along(face, origin, axis) -> tuple:
    """How far the face runs along its own axis -- where the thread can live."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    lows, exp = [], TopExp_Explorer(face, TopAbs_VERTEX)
    while exp.More():
        p = BRep_Tool.Pnt_s(TopoDS.Vertex_s(exp.Current()))
        lows.append(sum((p.Coord(i + 1) - origin[i]) * axis[i] for i in range(3)))
        exp.Next()
    if not lows:
        raise CadError("bad_face", "that cylinder has no bounded extent")
    return (min(lows), max(lows))


def _helical_ridge(feature_id: str, radius: float, origin, axis, pitch: float,
                   base: float, begin: float, end: float, depth: float,
                   internal: bool, right_hand: bool, narrow: float = 0.0) -> Body:
    """One stretch of thread, swept along the helix that starts at ``base``.

    ``begin`` and ``end`` are mm along the axis from ``base``; the profile is
    placed at the angle the helix has reached there, so sections line up.
    """
    frame = gp_Ax3(gp_Pnt(*[origin[i] + axis[i] * base for i in range(3)]),
                   gp_Dir(*axis))
    surface = Geom_CylindricalSurface(frame, radius)
    turn = 1.0 if right_hand else -1.0
    rise = pitch / (2 * math.pi)
    line = Geom2d_Line(gp_Pnt2d(0.0, 0.0), gp_Dir2d(turn, rise))
    # the line is parameterised by length in (angle, height) space: the
    # parameter reaching a height is longer by the slope's own length
    scale = math.hypot(1.0, rise)
    edge = BRepBuilderAPI_MakeEdge(line, surface,
                                   begin / pitch * 2 * math.pi * scale,
                                   end / pitch * 2 * math.pi * scale).Edge()
    from OCP.BRepLib import BRepLib
    BRepLib.BuildCurves3d_s(edge)
    spine = BRepBuilderAPI_MakeWire(edge).Wire()

    algo = BRepOffsetAPI_MakePipeShell(spine)
    algo.SetMode(gp_Dir(*axis))              # the profile keeps its orientation
    algo.SetTransitionMode(BRepBuilderAPI_TransitionMode.BRepBuilderAPI_RightCorner)
    algo.Add(_tooth(radius, axis, frame, pitch, depth, internal,
                    turn * begin / pitch * 2 * math.pi, begin, narrow), False, False)
    algo.Build()
    if not algo.IsDone():
        raise CadError("thread_failed", "the thread could not be swept",
                       {"hint": "a pitch this coarse for the diameter makes the "
                                "thread overlap itself"})
    if not algo.MakeSolid():
        raise CadError("thread_failed", "the swept thread did not close into a solid")
    from ..core.naming import faces_of, role_names
    shape = _facing_out(algo.Shape(), origin, axis, base + (begin + end) / 2)
    return Body(shape, role_names(feature_id + "_thread", faces_of(shape)), {}, [])


def _facing_out(shape, origin, axis, at: float):
    """Make sure the swept solid encloses the thread, not everything else.

    ``MakeSolid`` may call either side inside, and a fuse with an inverted
    solid removes material without an error. Flipping the orientation flag
    does not reach the boolean, so the solid is rebuilt from its shell by
    the sign of its volume, then checked against a point on the axis.
    """
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.ShapeFix import ShapeFix_Solid
    from OCP.TopAbs import TopAbs_ShapeEnum, TopAbs_State
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SHELL)
    if explorer.More():
        shape = ShapeFix_Solid().SolidFromShell(TopoDS.Shell_s(explorer.Current()))

    on_axis = gp_Pnt(*[origin[i] + axis[i] * at for i in range(3)])
    classifier = BRepClass3d_SolidClassifier(shape)
    classifier.Perform(on_axis, 1e-7)
    if classifier.State() == TopAbs_State.TopAbs_IN:
        raise CadError("thread_failed",
                       "the swept thread came out inside out",
                       {"hint": "the profile or the helix direction disagree "
                                "about which side the material is on"})
    return shape


def _tooth(radius: float, axis, frame, pitch: float, depth: float, internal: bool,
           angle: float, along: float, narrow: float = 0.0):
    """The thread's cross-section: the ISO trapezoid, root to crest.

    The material the thread is, not the groove: 3/4 of a pitch wide at the
    root, 1/8 at the crest, 60 degrees between the flanks. ``angle`` and
    ``along`` (radians, mm) place it on the helix.
    """
    over = depth * 0.3                          # how far to reach into the core
    # a crest on the same surface as a collar's face is a coincident-surface
    # fuse, which adds nothing: it stops 0.02 mm short of the nominal radius
    nick = 0.02
    # ISO's crest flat is p/8; a flat narrower than the sweep's fitting
    # tolerance fails the boolean, so it has a floor. `clearance` pulls the
    # flanks back as well as the crest: a thread needs room on its sides
    crest_half = max(pitch / 16 - narrow / 2, 0.15)
    root_half = max(3 * pitch / 8 - narrow / 2, crest_half + 0.05)

    sign = 1.0 if not internal else -1.0        # outwards on a shaft, inwards in a hole
    x = gp_Vec(frame.XDirection())
    y = gp_Vec(frame.YDirection())
    radial = x.Multiplied(math.cos(angle)).Added(y.Multiplied(math.sin(angle)))
    along_axis = gp_Vec(gp_Dir(*axis))
    start = frame.Location().Translated(along_axis.Multiplied(along))

    def at(offset: float, half: float, side: float):
        r = radius + sign * offset
        return gp_Pnt(start.X() + radial.X() * r + along_axis.X() * half * side,
                      start.Y() + radial.Y() * r + along_axis.Y() * half * side,
                      start.Z() + radial.Z() * r + along_axis.Z() * half * side)

    polygon = BRepBuilderAPI_MakePolygon()
    for point in (at(-depth - over, root_half, -1.0), at(-depth - over, root_half, 1.0),
                  at(-depth, root_half, 1.0), at(-nick, crest_half, 1.0),
                  at(-nick, crest_half, -1.0), at(-depth, root_half, -1.0)):
        polygon.Add(point)
    polygon.Close()
    if not polygon.IsDone():
        raise CadError("thread_failed", "the thread profile did not build")
    return polygon.Wire()
