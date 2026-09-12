"""Operations that change an existing solid: fillet, chamfer, shell, draft, hole.

All take references by name and return a body whose names still resolve. A
geometry OCCT cannot build is a ``*_failed`` CadError, not a crash.
"""
from __future__ import annotations

import math

from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepOffsetAPI import BRepOffsetAPI_DraftAngle, BRepOffsetAPI_MakeThickSolid
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakePrism
from OCP.GeomAbs import GeomAbs_SurfaceType
from ..core.occ import ListOfShape
from OCP.gp import gp_Ax2, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec

from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

from .booleans import _inherit, cut as _cut, fuse
from ... import names
from ...errors import CadError
from ..core.naming import Body, face_info, faces_of, role_names
from .primitives import cylinder
from . import sentry


def _sentry_says(kind: str, body, edges, used, size):
    """Try the operation in a second process first, since ``Build()`` can segfault (see cadcore/geometry/solids/sentry.py).

    Returns ``(message, detail)`` for the caller to raise, or ``None``; the
    error kind stays at each raise site so the registry can see it.
    """
    verdict = sentry.survives(kind, body.shape, edges, size)
    if verdict == "crashed":
        return (f"this {kind} crashes OCCT rather than refusing, so it was not "
                f"attempted here",
                {"size": size, "edges": used, "verdict": verdict,
                 "hint": "OCCT's own Simulate() reports these contours healthy, "
                         "so there is nothing to check: try a smaller radius, "
                         "or fewer edges at once"})
    if verdict == "timed_out":
        return (f"this {kind} did not finish in the time a second process was "
                f"given, so it was not attempted here",
                {"size": size, "edges": used, "verdict": verdict,
                 "hint": "raise CAD_SENTRY_TIMEOUT, or set CAD_SENTRY=0 to run "
                         "it unguarded"})
    return None


def _numbered_by_edge(out: Body, algo, table: dict, used: list, feature_id: str) -> Body:
    """Number this feature's faces by the edge each one rounds, in edge-name order.

    Position would do as a tie-break, but two rounds of one feature can swap
    places when a dimension moves, and then ``side#2`` would mean the other
    edge. An edge name is the two faces it lies between, which does not move.
    """
    from ..core.naming import faces_of

    live = faces_of(out.shape)
    rank = {}
    for k, edge_name in enumerate(sorted(used)):
        for made in algo.Generated(table[edge_name]):
            face = next((f for f in live if f.IsSame(made)), None)
            name = out.name_of(face) if face is not None else None
            if name is not None:
                rank.setdefault(name, k)
    by_role: dict = {}
    for name, _ in out.names:
        parts = names.parse(name)
        if (parts.feature == feature_id and not parts.scope
                and all(kind == names.DUPLICATE for kind, _ in parts.suffixes)):
            by_role.setdefault(parts.role, []).append(name)
    renamed = {}
    for role, group in by_role.items():
        ordered = sorted(group, key=lambda n: (rank.get(n, len(used)), group.index(n)))
        for k, name in enumerate(ordered, start=1):
            renamed[name] = names.face(feature_id, role, k)
    return out.rebuilt(names=[(renamed.get(n, n), f) for n, f in out.names])


def fillet(feature_id: str, body: Body, edge_names: list[str], radius) -> Body:
    """Round the named edges.

    ``radius`` is a number, or ``[start, end]`` for a fillet that varies along
    each edge.
    """
    if isinstance(radius, (list, tuple)):
        if len(radius) != 2:
            raise CadError("bad_parameter",
                           "a variable fillet needs exactly two radii",
                           {"given": list(radius)})
        start, end = (float(r) for r in radius)
    else:
        start = end = float(radius)
    if min(start, end) <= 0:
        raise CadError("bad_parameter", "fillet radius must be positive",
                       {"radius": radius})

    table = body.edge_table()
    algo = BRepFilletAPI_MakeFillet(body.shape)
    used = []
    for name in edge_names:
        edge = table.get(name)
        if edge is None:
            raise CadError("unresolved_reference", f"no edge named {name!r} on this body",
                           {"available": sorted(table)[:40], "asked_for": name})
        try:
            if start == end:
                algo.Add(start, edge)
            else:
                algo.Add(start, end, edge)
        except Exception as exc:                                 # noqa: BLE001
            # OCCT throws here, not at Build, for an edge with one face on it
            # (the rim of an open shell): "ChFi3d_Builder: only 2 faces"
            raise CadError("fillet_failed", f"the edge {name!r} cannot take a fillet",
                           {"edge": name, "error": str(exc),
                            "hint": "a fillet needs two faces meeting at the edge; "
                                    "the rim of an open shell has one"}) from exc
        used.append(name)
    if not used:
        raise CadError("empty_selection", "fillet got no edges")
    trouble = _sentry_says("fillet", body, [table[n] for n in used], used, radius)
    if trouble:
        raise CadError("fillet_crashed", *trouble)
    try:
        algo.Build()
    except Exception as exc:                                     # noqa: BLE001
        raise CadError("fillet_failed", "OCCT could not build the fillet",
                       {"radius": radius, "edges": used, "error": str(exc)}) from exc
    if not algo.IsDone():
        raise CadError("fillet_failed", "fillet did not converge", {"radius": radius})
    out = _inherit(algo, [body], algo.Shape(), feature_id)
    if not BRepCheck_Analyzer(out.shape).IsValid():
        raise CadError("invalid_shape", "fillet produced an invalid solid",
                       {"radius": radius})
    return _numbered_by_edge(out, algo, table, used, feature_id)


def chamfer(feature_id: str, body: Body, edge_names: list[str], distance: float) -> Body:
    """Chamfer the named edges; edges resolve as for a fillet.

    An edge name that no longer resolves is an ``unresolved_reference``; a
    distance the geometry cannot take is a ``chamfer_failed``.
    """
    d = float(distance)
    if d <= 0:
        raise CadError("bad_parameter", "chamfer distance must be positive", {"distance": d})
    table = body.edge_table()
    algo = BRepFilletAPI_MakeChamfer(body.shape)
    used = []
    for name in edge_names:
        edge = table.get(name)
        if edge is None:
            raise CadError("unresolved_reference", f"no edge named {name!r} on this body",
                           {"available": sorted(table)[:40], "asked_for": name})
        try:
            algo.Add(d, edge)
        except Exception as exc:                                 # noqa: BLE001
            raise CadError("chamfer_failed", f"the edge {name!r} cannot take a chamfer",
                           {"edge": name, "error": str(exc),
                            "hint": "a chamfer needs two faces meeting at the edge"}) from exc
        used.append(name)
    if not used:
        raise CadError("empty_selection", "chamfer got no edges")
    trouble = _sentry_says("chamfer", body, [table[n] for n in used], used, d)
    if trouble:
        raise CadError("chamfer_crashed", *trouble)
    try:
        algo.Build()
    except Exception as exc:                                     # noqa: BLE001
        raise CadError("chamfer_failed", "OCCT could not build the chamfer",
                       {"distance": d, "edges": used, "error": str(exc)}) from exc
    if not algo.IsDone():
        raise CadError("chamfer_failed", "the chamfer did not build",
                       {"distance": d, "edges": used,
                        "hint": "the distance is probably wider than a neighbouring face"})
    out = _inherit(algo, [body], algo.Shape(), feature_id)
    return _numbered_by_edge(out, algo, table, used, feature_id)


# ------------------------------------------------------------------ shell --
def shell(feature_id: str, body: Body, open_faces: list[str], thickness) -> Body:
    """Hollow a solid, opening it at the named faces.

    ``thickness`` is a number (mm), or ``{"default": 2, "faces": {"base/-z": 4}}``
    for walls of different thickness.
    """
    if not open_faces:
        raise CadError("empty_selection", "a shell needs a face to open at",
                       {"hint": "pick the face the inside is reached through"})
    if isinstance(thickness, dict):
        return _shell_per_face(feature_id, body, open_faces, thickness)
    t = float(thickness)
    if t <= 0:
        raise CadError("bad_parameter", "shell thickness must be positive", {"thickness": t})
    removed = ListOfShape()
    chosen = []
    for name in open_faces:
        face = body.face(name)
        if face is None:
            raise CadError("unresolved_reference", f"no face named {name!r} on this body",
                           {"available": body.face_names()})
        removed.Append(face)
        chosen.append(face)
    # the offsetter can segfault rather than refuse: a second process tries it first
    trouble = _sentry_says("shell", body, chosen, list(open_faces), t)
    if trouble is not None:
        raise CadError("shell_failed", trouble[0], trouble[1])
    algo = BRepOffsetAPI_MakeThickSolid()
    try:
        algo.MakeThickSolidByJoin(body.shape, removed, -t, 1e-4)
    except Exception as exc:                                     # noqa: BLE001
        raise CadError("shell_failed", "OCCT could not hollow this solid",
                       {"thickness": t, "open": list(open_faces), "error": str(exc),
                        "hint": "the wall is probably thicker than a local feature"}) from exc
    if not algo.IsDone():
        raise CadError("shell_failed", "the shell did not build",
                       {"thickness": t, "open": list(open_faces)})
    out = algo.Shape()
    # OCCT can report IsDone and return the input solid unchanged, e.g. when an
    # open face's edges were filleted and the offsetter finds nothing to join.
    # The volume comparison catches that.
    before, after = GProp_GProps(), GProp_GProps()
    BRepGProp.VolumeProperties_s(body.shape, before)
    BRepGProp.VolumeProperties_s(out, after)
    if before.Mass() > 0 and abs(after.Mass() - before.Mass()) < before.Mass() * 1e-9:
        raise CadError("shell_failed",
                       "the shell reported success and removed nothing",
                       {"thickness": t, "open": list(open_faces),
                        "volume_mm3": round(before.Mass(), 6),
                        "hint": "an open face whose edges have been filleted or "
                                "chamfered is the usual cause; shell first and "
                                "round the result afterwards"})
    return _inherit(algo, [body], out, feature_id)


def _shell_per_face(feature_id: str, body: Body, open_faces: list, thickness: dict) -> Body:
    """A shell whose walls are not all the same thickness.

    OCCT's thick-solid builder takes one offset, so the inner solid is built
    with per-face offsets and subtracted, and the openings are cut by sweeping
    each open face inward through its own wall.
    """
    from OCP.BRepOffset import BRepOffset_MakeOffset, BRepOffset_Mode
    from OCP.GeomAbs import GeomAbs_JoinType

    default = float(thickness.get("default", 0))
    per_face = {name: float(value) for name, value in (thickness.get("faces") or {}).items()}
    if default <= 0 or any(v <= 0 for v in per_face.values()):
        raise CadError("bad_parameter", "every shell thickness must be positive",
                       {"default": default, "faces": per_face})
    for name in list(per_face) + list(open_faces):
        if body.face(name) is None:
            raise CadError("unresolved_reference", f"no face named {name!r} on this body",
                           {"available": body.face_names()})

    algo = BRepOffset_MakeOffset()
    algo.Initialize(body.shape, -default, 1e-4, BRepOffset_Mode.BRepOffset_Skin,
                    False, False, GeomAbs_JoinType.GeomAbs_Arc, False)
    for name, value in per_face.items():
        algo.SetOffsetOnFace(body.face(name), -value)
    try:
        algo.MakeOffsetShape()
    except Exception as exc:                                     # noqa: BLE001
        raise CadError("shell_failed", "OCCT could not hollow this solid",
                       {"thickness": thickness, "error": str(exc)}) from exc
    if not algo.IsDone():
        raise CadError("shell_failed", "the inner surface did not build",
                       {"thickness": thickness})

    inner = algo.Shape()

    # the opening is made by growing the void, not by sweeping the outer face
    # inward (that would take the side walls too): the inner solid's matching
    # face is swept back out through the surface, then one cut does the rest
    void = inner
    for name in open_faces:
        face = body.face(name)
        info = face_info(face)
        normal = info.get("normal")
        if normal is None:
            raise CadError("non_planar_face", f"cannot open {name!r}: it is not planar")
        wall = per_face.get(name, default)
        matching = _facing(inner, normal, info["centre"])
        if matching is None:
            raise CadError("shell_failed", f"nothing inside {name!r} to open through",
                           {"face": name})
        prism = BRepPrimAPI_MakePrism(
            matching, gp_Vec(*[normal[i] * wall * 3 for i in range(3)]))
        prism.Build()
        void = BRepAlgoAPI_Fuse(void, prism.Shape()).Shape()

    return _cut(feature_id, body, Body(void, []))


def _facing(shape, normal, towards) -> object | None:
    """The face of the inner solid that looks the same way as an opening."""
    best, distance = None, None
    for candidate in faces_of(shape):
        info = face_info(candidate)
        theirs = info.get("normal")
        if theirs is None:
            continue
        if sum(theirs[i] * normal[i] for i in range(3)) < 0.999:
            continue
        gap = sum((info["centre"][i] - towards[i]) ** 2 for i in range(3))
        if distance is None or gap < distance:
            best, distance = candidate, gap
    return best


def draft_parted(feature_id: str, body: Body, faces: list[str], angle_deg: float,
                 origin, normal) -> Body:
    """Taper the named faces away from a parting plane: each half pulled its own way.

    The body is split at the plane, each half is drafted about it with the
    pull along the normal (up for the upper half, down for the lower), and
    the halves are fused back. A face the plane crosses is drafted in two
    pieces, ``name@0`` and ``name@1``.
    """
    from .booleans import fuse
    from .split import _only, _solids, plane_face, split

    pieces = split(feature_id, body, plane_face(body, origin, normal), "both", origin, normal)
    halves = []
    for solid in _solids(pieces.shape):
        half = _only(feature_id, pieces, [solid])
        from .split import _centroid
        c = _centroid(solid)
        up = sum((c[i] - origin[i]) * normal[i] for i in range(3)) > 0
        pull = [float(v) * (1.0 if up else -1.0) for v in normal]
        mine = [n for n in half.face_names()
                if any(n == f or n.startswith(f + names.PIECE) for f in faces)]
        if mine:
            # OCCT's positive angle opens the face along the pull; a mould wants it closed
            half = draft(feature_id, half, mine, -float(angle_deg), pull,
                         neutral_plane=(origin, normal))
        halves.append(half)
    if len(halves) < 2:
        raise CadError("split_failed", "the parting plane does not pass through the body")
    out = halves[0]
    for other in halves[1:]:
        out = fuse(feature_id, out, other, glue=True)
    return out


def draft(feature_id: str, body: Body, faces: list[str], angle_deg: float,
          direction=(0.0, 0.0, 1.0), neutral: str | None = None,
          neutral_plane: tuple | None = None) -> Body:
    """Taper the named faces for moulding, about a neutral plane (a face's, or given)."""
    angle = math.radians(float(angle_deg))
    algo = BRepOffsetAPI_DraftAngle(body.shape)
    pull = gp_Dir(*[float(v) for v in direction])
    if neutral_plane is not None:
        plane = gp_Pln(gp_Pnt(*[float(c) for c in neutral_plane[0]]),
                       gp_Dir(*[float(c) for c in neutral_plane[1]]))
    elif neutral is not None:
        ref = body.face(neutral)
        if ref is None:
            raise CadError("unresolved_reference", f"no face named {neutral!r} on this body",
                           {"available": body.face_names()})
        info = face_info(ref)
        if "normal" not in info:
            raise CadError("non_planar_face", f"the neutral face {neutral!r} is not flat",
                           {"face": neutral, "hint": "a draft is measured from a plane"})
        plane = gp_Pln(gp_Pnt(*info["centre"]), gp_Dir(*info["normal"]))
    else:
        plane = gp_Pln(gp_Pnt(0, 0, 0), pull)
    for name in faces:
        face = body.face(name)
        if face is None:
            raise CadError("unresolved_reference", f"no face named {name!r} on this body",
                           {"available": body.face_names()})
        # OCCT throws out of Add for some faces rather than answering AddDone,
        # and a Standard_OutOfRange reaching the caller is not a refusal
        try:
            algo.Add(face, pull, angle, plane)
            done = algo.AddDone()
        except Exception as exc:                                   # noqa: BLE001
            raise CadError("draft_failed", f"OCCT could not draft {name!r}",
                           {"face": name, "angle_deg": angle_deg,
                            "reason": str(exc).strip() or type(exc).__name__}) from exc
        if not done:
            raise CadError("draft_failed", f"OCCT refused to draft {name!r}",
                           {"angle_deg": angle_deg,
                            "hint": "the face may be perpendicular to the pull direction"})
    try:
        algo.Build()
        built = algo.IsDone()
    except Exception as exc:                                       # noqa: BLE001
        raise CadError("draft_failed", "the draft did not build",
                       {"faces": list(faces), "angle_deg": angle_deg,
                        "reason": str(exc).strip() or type(exc).__name__}) from exc
    if not built:
        raise CadError("draft_failed", "the draft did not build",
                       {"faces": list(faces), "angle_deg": angle_deg})
    out = _inherit(algo, [body], algo.Shape(), feature_id)

    # DraftAngle reports Modified for the faces it did not tilt and nothing for
    # the ones it did, so drafted faces would lose their names. They are
    # re-attached by nearest centroid, which is safe because a tilt about a
    # neutral plane barely moves a centroid.
    live = faces_of(out.shape)
    for name in faces:
        if out.face(name) is not None:
            continue
        before = face_info(body.face(name))["centre"]
        # candidates are faces with no inherited name: either unnamed, or named
        # by this feature's own fallback, which carries no provenance to lose
        free = [f for f in live
                if out.name_of(f) is None
                or names.parse(out.name_of(f)).role.startswith("face")]
        best = min(free, key=lambda f: math.dist(face_info(f)["centre"], before), default=None)
        if best is not None:
            out.names = [(n, f) for n, f in out.names if not f.IsSame(best)]
            out.names.append((name, best))
            if name in out.dropped:
                out.dropped.remove(name)
    return out


def rib(feature_id: str, sketch, thickness: float, distance: float,
        direction=(0.0, -1.0)) -> Body:
    """A wall grown from an open sketch line, ``thickness`` wide and ``distance`` deep.

    The chain is swept in its own plane to a surface, which is then thickened
    symmetrically about the line.
    """
    from .sweeps import _oriented_solid, _wire_of

    t, d = float(thickness), float(distance)
    if t <= 0 or abs(d) < 1e-9:
        raise CadError("bad_parameter", "a rib needs a positive thickness and a depth",
                       {"thickness": t, "depth": d})
    plane = sketch.plane
    y_axis = plane.y_axis()
    run = [plane.x_axis[i] * float(direction[0]) + y_axis[i] * float(direction[1])
           for i in range(3)]
    length = math.sqrt(sum(c * c for c in run))
    if length < 1e-9:
        raise CadError("bad_parameter", "the rib has no direction to grow in")
    run = [c / length * d for c in run]

    prism = BRepPrimAPI_MakePrism(_wire_of(sketch), gp_Vec(*run))
    prism.Build()
    if not prism.IsDone():
        raise CadError("rib_failed", "the rib's surface did not build")

    # thicken to one side, then step back half a thickness: a rib straddles the
    # line it was drawn on
    algo = BRepOffsetAPI_MakeThickSolid()
    try:
        algo.MakeThickSolidBySimple(prism.Shape(), t)
        algo.Build()
    except Exception as exc:                                     # noqa: BLE001
        raise CadError("rib_failed", "OCCT could not thicken the rib",
                       {"thickness": t, "error": str(exc)}) from exc
    if not algo.IsDone():
        raise CadError("rib_failed", "the rib did not thicken", {"thickness": t})

    # step back by however far the thickening actually went, measured from the
    # centres of mass: `MakeThickSolidBySimple` offsets along the surface's own
    # normal, whose sign depends on which way the chain was drawn
    shift = _dot(_centre_of_mass(algo.Shape(), solid=True), plane.normal) - \
        _dot(_centre_of_mass(prism.Shape(), solid=False), plane.normal)
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(*[-c * shift for c in plane.normal]))
    centred = BRepBuilderAPI_Transform(algo.Shape(), trsf, True)
    centred.Build()
    shape = _oriented_solid(centred.Shape())
    return Body(shape, role_names(feature_id, faces_of(shape)))


def _centre_of_mass(shape, solid: bool) -> tuple:
    props = GProp_GProps()
    if solid:
        BRepGProp.VolumeProperties_s(shape, props)
    else:
        BRepGProp.SurfaceProperties_s(shape, props)
    point = props.CentreOfMass()
    return (point.X(), point.Y(), point.Z())


def _dot(a, b) -> float:
    return sum(a[i] * b[i] for i in range(3))


def _drilled(tool: Body, feature_id: str, at, axis, side: str, far: str, near: str) -> None:
    """Name a drilled cylinder's faces from its own axis rather than the world's.

    ``role_names`` names planes by world axis, which would give the same hole
    different names depending on the direction it was drilled. Here the round
    face is ``side`` and the two flat ones are ``near``/``far`` by position
    along the axis.
    """
    caps = []
    for index, (_, face) in enumerate(tool.names):
        info = face_info(face)
        if info.get("radius") is not None:
            tool.rename(index, names.face(feature_id, side))
        elif "normal" in info:
            caps.append((sum((info["centre"][i] - at[i]) * axis[i] for i in range(3)),
                         index, face))
    caps.sort()                                 # nearest the mouth first
    for role, (_, index, face) in zip((near, far), caps):
        tool.rename(index, names.face(feature_id, role))


def hole_tool(feature_id: str, frame: dict, diameter: float, depth: float | None,
              at=(0.0, 0.0), counterbore: dict | None = None,
              countersink: dict | None = None, through_depth: float = 0.0) -> Body:
    """The solid a hole removes: bore, plus a counterbore or a countersink.

    The axis follows the face frame. ``depth`` None means through, using
    ``through_depth`` or 1000 mm.
    """
    d = float(diameter)
    if d <= 0:
        raise CadError("bad_parameter", "hole diameter must be positive", {"diameter": d})
    normal = [float(c) for c in frame["normal"]]
    x_axis = [float(c) for c in frame["x_axis"]]
    y_axis = [normal[1] * x_axis[2] - normal[2] * x_axis[1],
              normal[2] * x_axis[0] - normal[0] * x_axis[2],
              normal[0] * x_axis[1] - normal[1] * x_axis[0]]
    origin = [frame["origin"][i] + x_axis[i] * float(at[0]) + y_axis[i] * float(at[1])
              for i in range(3)]
    inward = [-c for c in normal]
    cut_depth = float(depth) if depth else float(through_depth or 1000.0)

    # start slightly proud of the face so the cut is clean rather than tangent
    lift = 0.01 * max(1.0, d)
    mouth = [origin[i] + normal[i] * lift for i in range(3)]
    tool = cylinder(feature_id, d / 2, cut_depth + lift, mouth, inward, centred=False)
    _drilled(tool, feature_id, mouth, inward, side="bore", far="floor", near="mouth")

    if counterbore:
        cb = cylinder(f"{feature_id}_cb", float(counterbore["diameter"]) / 2,
                      float(counterbore["depth"]) + lift, mouth, inward, centred=False)
        # the counterbore is drilled as its own cylinder and arrives with that
        # cylinder's names: rename them before fusing
        _drilled(cb, feature_id, mouth, inward,
                 side="counterbore", far="seat", near="cb_mouth")
        tool = fuse(feature_id, tool, cb)
    elif countersink:
        top_r = float(countersink["diameter"]) / 2
        angle = math.radians(float(countersink.get("angle", 90)) / 2)
        cone_h = (top_r - d / 2) / math.tan(angle) if angle > 1e-6 else 0.0
        axis = gp_Ax2(gp_Pnt(*mouth), gp_Dir(*inward))
        # the cone starts `lift` proud of the face, so its base radius grows by
        # the same slope to keep the angle and the diameter at the surface
        base_r = top_r + lift * math.tan(angle)
        cone = BRepPrimAPI_MakeCone(axis, base_r, d / 2, cone_h + lift).Shape()
        seat = Body(cone, [])
        for k, face in enumerate(faces_of(cone)):
            conical = face_info(face)["type"] == GeomAbs_SurfaceType.GeomAbs_Cone
            seat.names.append((names.face(feature_id, "countersink" if conical
                                          else f"cs_cap{k}"), face))
        tool = fuse(feature_id, tool, seat)
    return tool


