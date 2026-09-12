"""Surfaces: the shapes that are not solids, and the ways back to solid."""
from __future__ import annotations

import math

from OCP.BRep import BRep_Builder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing, BRepAlgoAPI_Splitter
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing)
from OCP.BRepLib import BRepLib
from OCP.BRepOffsetAPI import (BRepOffsetAPI_MakeFilling, BRepOffsetAPI_MakeOffsetShape,
                               BRepOffsetAPI_MakeThickSolid, BRepOffsetAPI_ThruSections)
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.GeomAbs import GeomAbs_Shape
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shell
from ..core.occ import ListOfShape
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Vec

from .booleans import _inherit
from ..core.measure import kind_of, volume
from ... import names
from ..core import provenance
from ...errors import CadError
from ..core.naming import Body, face_info, faces_of, role_names
from .sweeps import (_name_generated, _oriented_solid, _outer_wire, _profile, _segment_edge,
                     _wire_of)

CONTINUITY = {"G0": GeomAbs_Shape.GeomAbs_C0,
              "G1": GeomAbs_Shape.GeomAbs_G1,
              "G2": GeomAbs_Shape.GeomAbs_G2}


# ----------------------------------------------------------------- creating --
def planar(feature_id: str, sketch) -> Body:
    """The sketch itself as a face -- the simplest surface there is."""
    face, _ = _profile(sketch)
    return Body(face, [(names.face(feature_id, "face"), face)], {}, [])


#: seconds a defeature may take in the second process before it is refused
DEFEATURE_PATIENCE = float(__import__("os").environ.get("CAD_DEFEATURE_TIMEOUT", "30"))


def skin(feature_id: str, sketches: list, ruled: bool = False) -> Body:
    """Loft between sections without capping the ends: a skin, not a solid.

    Same algorithm as the solid loft, asked for an open result. The side faces
    keep the first section's segment names, so ``skin/front`` means what it says
    even after the sections move.
    """
    if len(sketches) < 2:
        raise CadError("bad_parameter", "a skin needs at least two sections",
                       {"given": len(sketches)})
    algo = BRepOffsetAPI_ThruSections(False, ruled, 1e-6)
    outline = None
    for sketch in sketches:
        face, named = _profile(sketch)
        if outline is None:
            outline = (face, named)
        algo.AddWire(_outer(face))
        algo.CheckCompatibility(False)
    algo.Build()
    if not algo.IsDone():
        raise CadError("skin_failed", "the skin did not build",
                       {"hint": "sections need matching segment counts and orientation"})
    return _named_from_profile(feature_id, algo, algo.Shape(), *outline)


def extruded(feature_id: str, sketch, distance: float) -> Body:
    """Drag a sketch -- open or closed -- into a wall of surface."""
    d = float(distance)
    if abs(d) < 1e-9:
        raise CadError("bad_parameter", "extrude distance must be non-zero")
    n = sketch.plane.normal
    wire = _wire_of(sketch)
    algo = BRepPrimAPI_MakePrism(wire, gp_Vec(n[0] * d, n[1] * d, n[2] * d))
    algo.Build()
    if not algo.IsDone():
        raise CadError("surface_failed", "the swept wall did not build")
    return _named_from_profile(feature_id, algo, algo.Shape(), wire, _named_edges(sketch))


def revolved(feature_id: str, sketch, origin, direction, angle: float = 360.0) -> Body:
    """Spin a sketch about an axis, as surface rather than as solid."""
    axis = gp_Ax1(gp_Pnt(*[float(v) for v in origin]),
                  gp_Dir(*[float(v) for v in direction]))
    wire = _wire_of(sketch)
    algo = BRepPrimAPI_MakeRevol(wire, axis, math.radians(float(angle)))
    algo.Build()
    if not algo.IsDone():
        raise CadError("surface_failed", "the revolved surface did not build")
    return _named_from_profile(feature_id, algo, algo.Shape(), wire, _named_edges(sketch))


def swept(feature_id: str, profile, path, corner: str = "sharp") -> Body:
    """Sweep a profile along a path as surface -- an open profile is allowed.

    The solid sweep needs a closed profile to have something to fill; a surface
    sweep does not, which is the point: an open section dragged along a curve is
    how a fairing or a lip gets made.
    """
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
    from OCP.BRepBuilderAPI import BRepBuilderAPI_TransitionMode

    algo = BRepOffsetAPI_MakePipeShell(_wire_of(path))
    algo.SetTransitionMode(BRepBuilderAPI_TransitionMode.BRepBuilderAPI_RoundCorner
                           if corner == "round"
                           else BRepBuilderAPI_TransitionMode.BRepBuilderAPI_RightCorner)
    wire = _wire_of(profile)
    algo.Add(wire, False, False)
    algo.Build()
    if not algo.IsDone():
        raise CadError("surface_failed", "the swept surface did not build",
                       {"hint": "the path must be tangent-continuous enough to "
                                "carry the profile without folding"})
    return _named_from_profile(feature_id, algo, algo.Shape(), wire,
                               _named_edges(profile))


def _named_edges(sketch) -> list:
    return [(seg.name, _segment_edge(seg, sketch.plane)) for seg in sketch.loops[0]]


def from_points(feature_id: str, grid: list) -> Body:
    """A smooth surface through a grid of points: rows of equal length, world mm."""
    from OCP.GeomAPI import GeomAPI_PointsToBSplineSurface
    from OCP.collections import Array2_gp_Pnt

    rows = [list(row) for row in grid]
    if len(rows) < 2 or any(len(row) != len(rows[0]) for row in rows) or len(rows[0]) < 2:
        raise CadError("bad_arguments",
                       "a point surface needs at least two rows of at least two points, all rows the same length",
                       {"rows": [len(row) for row in rows]})
    array = Array2_gp_Pnt(1, len(rows), 1, len(rows[0]))
    for i, row in enumerate(rows, start=1):
        for j, point in enumerate(row, start=1):
            array.SetValue(i, j, gp_Pnt(*[float(c) for c in point]))
    algo = GeomAPI_PointsToBSplineSurface(array)
    if not algo.IsDone():
        raise CadError("surface_failed", "no surface passes through these points")
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    face = BRepBuilderAPI_MakeFace(algo.Surface(), 1e-6).Face()
    return Body(face, [(names.face(feature_id, "patch"), face)], {}, [])


STYLES = ("coons", "curved", "stretch")


def from_boundary(feature_id: str, sketches: list, style: str = "coons") -> Body:
    """A patch between two, three or four boundary curves, each an open sketch.

    The curves are taken in order round the patch; ``coons`` blends them
    linearly, ``curved`` follows their curvature (three or four curves),
    ``stretch`` stays flat.
    """
    from OCP.GeomAPI import GeomAPI_PointsToBSpline
    from OCP.GeomFill import GeomFill_BSplineCurves, GeomFill_FillingStyle
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from ..core.occ import Array1OfPnt
    from .sweeps import points_along

    if style not in STYLES:
        raise CadError("bad_parameter", f"style is one of {STYLES}", {"given": style})
    if not 2 <= len(sketches) <= 4:
        raise CadError("bad_arguments", "a boundary patch takes two, three or four curves",
                       {"given": len(sketches)})
    curves = []
    for sketch in sketches:
        points = points_along(sketch, 32)
        array = Array1OfPnt(1, len(points))
        for k, point in enumerate(points, start=1):
            array.SetValue(k, gp_Pnt(*point))
        curves.append(GeomAPI_PointsToBSpline(array).Curve())
    chosen = {"coons": GeomFill_FillingStyle.GeomFill_CoonsStyle,
              "curved": GeomFill_FillingStyle.GeomFill_CurvedStyle,
              "stretch": GeomFill_FillingStyle.GeomFill_StretchStyle}[style]
    try:
        surface = GeomFill_BSplineCurves(*curves, chosen).Surface()
    except Exception as exc:                                        # noqa: BLE001
        raise CadError("surface_failed", "the curves do not bound one patch",
                       {"hint": "each curve must start where the one before ends",
                        "reason": str(exc).strip() or type(exc).__name__}) from exc
    face = BRepBuilderAPI_MakeFace(surface, 1e-6).Face()
    return Body(face, [(names.face(feature_id, "patch"), face)], {}, [])


def _named_from_profile(feature_id: str, algo, shape, source, named_edges) -> Body:
    """Name each generated face after the sketch segment that swept it.

    The same walk the solid sweeps use: the algorithm's history is asked first,
    and the profile's edges are re-found inside the shape the algorithm was
    actually given, because MakeWire copies them.
    """
    body = Body(shape, [], {}, [])
    live = faces_of(shape)
    _name_generated(algo, feature_id, source, named_edges, body, live)
    claimed = [f for _, f in body.names]
    fresh = [f for f in live if not any(f.IsSame(c) for c in claimed)]
    body.names.extend(role_names(feature_id, fresh))
    return body


def _outer(face):
    return _outer_wire(face)


# -------------------------------------------------------------------- patch --
def fill(feature_id: str, body: Body, edge_names: list, continuity: str = "G1",
         through: list | None = None) -> Body:
    """Patch a boundary with a surface that meets its neighbours smoothly.

    This is the operation a solid modeller cannot fake. The boundary edges are
    given to OCCT's filling algorithm together with the faces they belong to,
    so the patch is built to be tangent (``G1``) or curvature-continuous
    (``G2``) with what it joins -- which is the difference between a patch and
    a visible crease.

    The result is the patch alone, as a face; sew it to the body to close it.
    """
    if continuity not in CONTINUITY:
        raise CadError("bad_parameter", f"continuity must be one of {sorted(CONTINUITY)}",
                       {"given": continuity})
    table = body.edge_table()
    algo = BRepOffsetAPI_MakeFilling()
    order = CONTINUITY[continuity]
    for name in edge_names:
        edge = table.get(name)
        if edge is None:
            raise CadError("unresolved_reference", f"no edge named {name!r} on this body",
                           {"asked_for": name, "available": sorted(table)[:40]})
        support = _face_beside(body, name)
        if support is not None and order != GeomAbs_Shape.GeomAbs_C0:
            algo.Add(edge, support, order)
        else:
            algo.Add(edge, GeomAbs_Shape.GeomAbs_C0)
    for point in through or []:
        algo.Add(gp_Pnt(*[float(v) for v in point]))
    algo.Build()
    if not algo.IsDone():
        raise CadError("fill_failed", "the patch did not build",
                       {"hint": "the boundary must be closed; try G0 if the "
                                "neighbouring faces cannot be met smoothly"})
    face = TopoDS.Face_s(algo.Shape())
    patch = Body(face, [(names.face(feature_id, "patch"), face)], {}, [])
    # how well it actually met its neighbours, in the units of the promise:
    # a patch that says G1 and lands 2 degrees off should be able to be caught
    patch.notes["fill"] = {"continuity": continuity, "gap_mm": round(algo.G0Error(), 6)}
    if order != GeomAbs_Shape.GeomAbs_C0:
        patch.notes["fill"]["tangent_error_deg"] = round(math.degrees(algo.G1Error()), 4)
    return patch


def _face_beside(body: Body, edge_name: str):
    """The face an edge belongs to, for a tangency constraint.

    Asked of the edge table rather than read out of the name: a face may have a
    ``#`` in its own name, so the pair cannot be recovered from the text.
    """
    for owner in body.edge_owners(edge_name):
        face = body.face(owner)
        if face is not None:
            return face
    return None


# ------------------------------------------------------------------ closing --
def sew(feature_id: str, bodies: list, tolerance: float = 1e-6,
        make_solid: bool = True) -> Body:
    """Stitch faces and shells into one shape, and close it if it closes.

    Sewing is how surface work gets back to a solid: patch the openings, sew
    the lot, and if the result has no free boundary left it becomes a solid
    rather than a shell. Saying which it became is the caller's cue.
    """
    if not bodies:
        raise CadError("bad_parameter", "nothing to sew")
    algo = BRepBuilderAPI_Sewing(float(tolerance))
    for body in bodies:
        algo.Add(body.shape)
    algo.Perform()
    shape = algo.SewedShape()

    if make_solid and kind_of(shape) == "shell" and _is_closed(shape):
        maker = BRepBuilderAPI_MakeSolid(_as_shell(shape))
        maker.Build()
        if maker.IsDone():
            shape = maker.Solid()

    out = Body(shape, [], {}, [], provenance.merged(bodies))
    live = faces_of(shape)
    claimed = []
    for body in bodies:
        for name, face in body.names:
            moved = algo.ModifiedSubShape(face) if algo.IsModifiedSubShape(face) else face
            match = next((f for f in live if f.IsSame(moved)), None)
            if match is None or out.name_of(match) is not None:
                continue
            out.names.append((name, match))
            claimed.append(match)
        out.aliases.update(body.aliases)

    fresh = [f for f in live if not any(f.IsSame(c) for c in claimed)]
    out.names.extend(role_names(feature_id, fresh))
    return out


def thicken(feature_id: str, body: Body, thickness: float,
            tolerance: float = 1e-6) -> Body:
    """Give a surface a wall thickness: shell in, solid out.

    A negative thickness offsets the other way, which is the whole reason the
    sign is a parameter rather than an absolute -- "2 mm inboard of this skin"
    and "2 mm outboard" are different parts.
    """
    if abs(float(thickness)) < 1e-9:
        raise CadError("bad_parameter", "thickness must be non-zero")
    algo = BRepOffsetAPI_MakeThickSolid()
    algo.MakeThickSolidBySimple(body.shape, float(thickness))
    algo.Build()
    if not algo.IsDone():
        raise CadError("thicken_failed", "the surface could not be thickened",
                       {"hint": "a thickness larger than the surface's own "
                                "curvature radius folds it inside out"})
    out = _inherit(algo, [body], _oriented_solid(algo.Shape()), feature_id)
    _name_twins(out, body, feature_id, float(thickness))
    return out


def _name_twins(out: Body, source: Body, feature_id: str, distance: float,
                twin: bool = True) -> None:
    """Name faces that moved, after the faces they came from.

    Two operations need this. Thickening makes a *twin* of every face one
    thickness away, and the twin is named for the same role under the new
    feature -- the other side of ``skin/south`` is ``thick/south``. Offsetting
    makes no twin: the face simply moved, and it keeps its own name, because
    "the top sheet" is still the top sheet three millimetres up.

    A twin carries the *role of the face it came from*, not a fresh reading of
    which way it points: ``thick/+z`` is the other side of ``X/+z``, which for a
    negative thickness is underneath it.

    ``skin/south`` thickened gives two faces, and the second one needs a name a
    designer can guess. It is the same role under the new feature -- the other
    side of ``skin/south`` is ``thick/south`` -- and what is left over (the
    walls closing the open boundary) becomes ``thick/wall``.

    OCCT's history says nothing about which new face is whose twin, so they are
    paired geometrically: a twin lies about one thickness from its original and
    has about the same area. Pairs are taken best-first rather than in name
    order, so one ambiguous face cannot mis-seat the rest, and anything that
    stays unpaired keeps a role name instead of borrowing one.
    """
    from ..core.naming import face_info
    unclaimed = [(n, f) for n, f in out.names if names.feature_of(n) == feature_id]
    span = abs(distance)
    pairs = []
    for name, face in source.names:
        a = face_info(face)
        for candidate in unclaimed:
            b = face_info(candidate[1])
            gap = math.dist(a["centre"], b["centre"])
            if not 0.4 * span <= gap <= 1.8 * span:
                continue
            if not 0.4 <= (b["area"] / a["area"] if a["area"] else 0) <= 2.5:
                continue
            pairs.append((gap, name, candidate))
    taken, used = {n for n, _ in out.names}, set()
    for _, name, candidate in sorted(pairs, key=lambda t: t[0]):
        role = names.parse(name).role or name
        carried = names.face(feature_id, role) if twin else name
        if id(candidate) in used:
            continue
        if carried == candidate[0]:               # role naming already got there
            used.add(id(candidate))
            unclaimed.remove(candidate)
            continue
        if carried in taken:
            continue
        out.rename(out.names.index(candidate), carried)
        taken.add(carried)
        used.add(id(candidate))
        unclaimed.remove(candidate)
    for k, (name, face) in enumerate(sorted(unclaimed,
                                            key=lambda t: face_info(t[1])["centre"])):
        wall = names.face(feature_id, "wall", k + 1)
        out.rename(out.names.index((name, face)), wall)


def cap(feature_id: str, body: Body, continuity: str = "G0") -> Body:
    """Close an open shell by filling every boundary loop, then sewing.

    The convenience move: patches across the openings and one sewn solid, for
    the common case where the openings are simple and the caller just wants the
    thing closed.
    """
    loops = boundary_loops(body)
    if not loops:
        raise CadError("already_closed", "this shape has no open boundary",
                       {"kind": kind_of(body.shape)})
    patches = [fill(f"{feature_id}_p{k}", body, loop, continuity)
               for k, loop in enumerate(loops)]
    return sew(feature_id, [body] + patches, 1e-6, True)


def boundary_loops(body: Body) -> list:
    """The named boundary edges, grouped into the loops they form."""
    table = body.edge_table()
    # the strict test, not a substring: a feature the user called `opening1`
    # puts "|open" inside ordinary shared-edge names, and capping those would
    # patch across the middle of the solid
    free = {name: edge for name, edge in table.items() if names.is_boundary(name)}
    loops, used = [], set()
    for name in sorted(free):
        if name in used:
            continue
        loop, frontier = [], [name]
        while frontier:
            current = frontier.pop()
            if current in used:
                continue
            used.add(current)
            loop.append(current)
            for other in free:
                if other not in used and _touches(free[current], free[other]):
                    frontier.append(other)
        loops.append(loop)
    return loops


def _touches(a, b, tolerance: float = 1e-6) -> bool:
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp
    va, wa, vb, wb = (TopExp.FirstVertex_s(a), TopExp.LastVertex_s(a),
                      TopExp.FirstVertex_s(b), TopExp.LastVertex_s(b))
    points = [BRep_Tool.Pnt_s(v) for v in (va, wa)]
    others = [BRep_Tool.Pnt_s(v) for v in (vb, wb)]
    return any(p.Distance(q) <= tolerance for p in points for q in others)


def _is_closed(shape) -> bool:
    from OCP.BRepCheck import BRepCheck_Shell, BRepCheck_Status
    shell = _as_shell(shape)
    if shell is None:
        return False
    return BRepCheck_Shell(shell).Closed() == BRepCheck_Status.BRepCheck_NoError


def _as_shell(shape):
    if shape.ShapeType() == TopAbs_ShapeEnum.TopAbs_SHELL:
        return TopoDS.Shell_s(shape)
    it = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SHELL)
    return TopoDS.Shell_s(it.Current()) if it.More() else None


# ------------------------------------------------------------------ editing --
def offset_surface(feature_id: str, body: Body, distance: float,
                   tolerance: float = 1e-6) -> Body:
    """Move a surface along its own normal, keeping the names on the result."""
    if abs(float(distance)) < 1e-9:
        raise CadError("bad_parameter", "offset distance must be non-zero")
    algo = BRepOffsetAPI_MakeOffsetShape()
    algo.PerformBySimple(body.shape, float(distance))
    algo.Build()
    if not algo.IsDone():
        raise CadError("offset_failed", "the surface could not be offset",
                       {"hint": "an offset larger than the smallest radius "
                                "of curvature self-intersects"})
    out = _inherit(algo, [body], algo.Shape(), feature_id)
    _name_twins(out, body, feature_id, float(distance), twin=False)
    return out


def trim(feature_id: str, body: Body, tool: Body, keep: str = "outside") -> Body:
    """Cut a surface against another shape and keep one side.

    Trimming is the surface answer to a boolean: the tool does not remove
    material, it decides which part of the sheet survives. Which part that is
    is a choice, so it is said out loud rather than inferred.
    """
    if keep not in ("inside", "outside", "both"):
        raise CadError("bad_parameter", "keep must be inside, outside or both",
                       {"given": keep})
    splitter = BRepAlgoAPI_Splitter()
    args, tools = ListOfShape(), ListOfShape()
    args.Append(body.shape)
    tools.Append(tool.shape)
    splitter.SetArguments(args)
    splitter.SetTools(tools)
    splitter.Build()
    if not splitter.IsDone():
        raise CadError("trim_failed", "the trim did not build")
    pieces = _inherit(splitter, [body], splitter.Shape(), feature_id)
    if keep == "both":
        return pieces
    inside = _inside_faces(pieces, tool)
    wanted = [(n, f) for n, f in pieces.names
              if (any(f.IsSame(g) for g in inside) == (keep == "inside"))]
    if not wanted:
        raise CadError("trim_empty", f"nothing was left {keep} the tool",
                       {"hint": "check which side the tool encloses"})
    return _shell_of(feature_id, wanted, pieces)


def _inside_faces(body: Body, tool: Body) -> list:
    """Which faces of a split shape lie inside the tool solid."""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_State
    from ..core.naming import face_info
    inside = []
    for _, face in body.names:
        classifier = BRepClass3d_SolidClassifier(tool.shape)
        centre = face_info(face)["centre"]
        classifier.Perform(gp_Pnt(*centre), 1e-6)
        if classifier.State() in (TopAbs_State.TopAbs_IN, TopAbs_State.TopAbs_ON):
            inside.append(face)
    return inside


def _shell_of(feature_id: str, kept: list, source: Body) -> Body:
    """Rebuild a shell from the faces that survived, names and all."""
    builder = BRep_Builder()
    shell = TopoDS_Shell()
    builder.MakeShell(shell)
    for _, face in kept:
        builder.Add(shell, face)
    out = Body(shell, list(kept), dict(source.aliases), list(source.dropped),
               dict(source.notes))
    out.dropped.extend(n for n, _ in source.names if not any(n == k for k, _ in kept))
    return out


def delete_face(feature_id: str, body: Body, face_names: list,
                heal: bool = True) -> Body:
    """Remove faces. Heal the wound, or leave it open.

    Two different operations wear the same name in most CAD, and they are worth
    separating. ``heal`` is *defeaturing*: take the fillet or the boss away and
    let the neighbours grow back together, which is how an imported solid with
    no history gets simplified. Without healing it is a hole cut in the shell --
    which is what surface work wants, because the next step is a new patch.
    """
    missing = [n for n in face_names if body.face(n) is None]
    if missing:
        raise CadError("unresolved_reference", "no such face on this body",
                       {"asked_for": missing, "available": body.face_names()[:40]})
    if not heal:
        gone = {body.canonical(n) for n in face_names}
        kept = [(n, f) for n, f in body.names if n not in gone]
        if not kept:
            raise CadError("bad_parameter", "that would delete every face")
        return _shell_of(feature_id, kept, body)

    # OCCT's defeaturing can run forever on some faces (a thread's flank on a
    # revolved neck took more than ten minutes and had not returned). A second
    # process tries it first, on a short leash, so the kernel never hangs
    from . import sentry

    verdict = sentry.survives("defeature", body.shape, [body.face(n) for n in face_names],
                              None, timeout=DEFEATURE_PATIENCE)
    if verdict in ("timed_out", "crashed"):
        raise CadError("defeature_failed",
                       "removing those faces %s, so it was not attempted here"
                       % ("did not finish in %d seconds" % DEFEATURE_PATIENCE
                          if verdict == "timed_out" else "crashes OCCT"),
                       {"faces": list(face_names), "verdict": verdict,
                        "hint": "remove the feature that made the face instead, or "
                                "delete without healing (heal=false)"})
    algo = BRepAlgoAPI_Defeaturing()
    algo.SetShape(body.shape)
    faces = ListOfShape()
    for name in face_names:
        faces.Append(body.face(name))
    algo.AddFacesToRemove(faces)
    algo.Build()
    if not algo.IsDone() or algo.Shape().IsNull():
        raise CadError("defeature_failed", "those faces could not be removed",
                       {"hint": "the neighbours have to be able to close the "
                                "gap; a face that carries the shape cannot go",
                        "faces": list(face_names)})
    out = _inherit(algo, [body], algo.Shape(), feature_id)
    out.dropped.extend(n for n in dict.fromkeys(face_names)
                       if out.face(n) is None and n not in out.dropped)
    return out


def extend(feature_id: str, body: Body, face_name: str, distance: float) -> Body:
    """Lengthen a face past its boundary -- the surface answer to "make it reach"."""
    face = body.face(face_name)
    if face is None:
        raise CadError("unresolved_reference", f"no face named {face_name!r}",
                       {"available": body.face_names()[:40]})
    if float(distance) <= 0:
        raise CadError("bad_parameter", "extension distance must be positive")
    grown = TopoDS_Face()
    BRepLib.ExtendFace_s(face, float(distance), True, True, True, True, grown)
    if grown.IsNull():
        raise CadError("extend_failed", f"{face_name!r} could not be extended",
                       {"hint": "only a face whose surface can be evaluated "
                                "past its edges can be extended"})
    grown_name = names.face(feature_id, "face")
    # the result is the one grown face: every other face of the body is gone,
    # and a reference to one of them has to be told so rather than left to
    # miss quietly
    kept = body.canonical(face_name)
    out = Body(grown, [(grown_name, grown)], {},
               [name for name, _ in body.names if name != kept])
    out.aliases[face_name] = grown_name                # the old name still means it
    return out


def move_face(feature_id: str, body: Body, face_name: str, distance: float) -> Body:
    """Push or pull one face of a solid along its own normal.

    The other half of direct editing. Deleting a face and letting the
    neighbours close the gap takes a feature *away*; this changes one a
    modelling history no longer exists for -- an imported STEP whose boss is
    two millimetres too tall.

    The face is swept the distance asked for and the prism is fused on or cut
    away, so the neighbours it slides along keep their names: only the face
    that moved is new, and it takes the old one's name because it is still the
    same face of the part.
    """
    from .booleans import cut as _cut
    from .booleans import fuse as _fuse

    face = body.face(face_name)
    if face is None:
        raise CadError("unresolved_reference", f"no face named {face_name!r} on this body",
                       {"available": body.face_names()[:40]})
    info = face_info(face)
    normal = info.get("normal")
    if normal is None:
        raise CadError("not_planar", f"{face_name!r} is not planar, so it has no one "
                                     "direction to move in", {"face": face_name})
    if abs(float(distance)) < 1e-9:
        raise CadError("bad_parameter", "the distance must be non-zero")

    step = float(distance)
    prism = BRepPrimAPI_MakePrism(face, gp_Vec(*[normal[i] * step for i in range(3)]))
    prism.Build()
    if not prism.IsDone():
        raise CadError("move_failed", f"{face_name!r} could not be swept")
    swept = Body(_oriented_solid(prism.Shape()),
                 role_names(feature_id + "_step", faces_of(prism.Shape())), {}, [])

    before = volume(body)
    out = (_fuse(feature_id, body, swept) if step > 0
           else _cut(feature_id, body, swept))
    moved = volume(out) - before
    if (step > 0) != (moved > 0) or abs(moved) < abs(step) * info["area"] * 0.5:
        raise CadError("move_failed",
                       "moving the face did not change the part by what it swept",
                       {"expected_mm3": round(info["area"] * step, 1),
                        "changed_mm3": round(moved, 1),
                        "hint": "a face cannot be pushed further than its neighbours reach"})
    # the face that moved is still that face of the part
    for name, candidate in list(out.names):
        if name.startswith(feature_id + "_step" + names.ROLE) and \
                _parallel(face_info(candidate).get("normal"), normal):
            out.rename(out.names.index((name, candidate)), face_name)
            break
    canonical = body.canonical(face_name)
    out.dropped = [n for n in out.dropped
                   if n not in (face_name, canonical) and not n.startswith(feature_id + "_step" + names.ROLE)]
    return unified(out)


def unified(body: Body) -> Body:
    """Merge faces that a boolean left as separate pieces of one surface.

    Pushing a face out leaves the prism's sides beside the walls they slid
    along: the same plane, two faces, and a seam that nothing asked for. OCCT
    can weld those, and the names follow the same rule a weld always follows
    here -- the survivor keeps one name and the other becomes an alias, so a
    reference written against either still resolves.
    """
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

    unifier = ShapeUpgrade_UnifySameDomain(body.shape, True, True, False)
    unifier.Build()
    shape = unifier.Shape()
    history = unifier.History()

    out = Body(shape, [], dict(body.aliases), list(body.dropped),
               dict(body.notes))
    out.notes.update(body.notes)
    live = faces_of(shape)
    for name, face in body.names:
        modified = history.Modified(face)
        survivors = [f for f in (list(modified) if modified.Extent() else [face])]
        found = None
        for survivor in survivors:
            found = next((f for f in live if f.IsSame(survivor)), None)
            if found is not None:
                break
        if found is None:
            out.dropped.append(name)
            continue
        existing = out.name_of(found)
        if existing is None:
            out.names.append((name, found))
        elif existing != name:
            out.aliases[name] = existing
    return out


def _parallel(a, b, tolerance: float = 1e-6) -> bool:
    return a is not None and abs(sum(a[i] * b[i] for i in range(3)) - 1.0) < tolerance
