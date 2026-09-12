"""Where the parts of an assembly end up, and how much freedom is left."""
from __future__ import annotations

import math

import numpy as np

from ... import names as naming
from .assembly import MATES, check_mate_faces, mate_geometry, mate_transform
from ...errors import CadError
from ..core.naming import Body

TOLERANCE = 1e-7          # mm and radians: what counts as satisfied
RANK_TOLERANCE = 1e-8     # a singular value below this is not a constraint
STEP_DEG = 10.0           # a drive moves this far per solve, so a loop stays on its branch
STEP_MM = 5.0


# --- poses ----------------------------------------------------------------------
# A pose is (R, t): a part's own coordinates times R, plus t. The unknowns are a
# twist about the world origin, which is the convention the null space is then
# read in -- an axis of rotation comes out as a screw axis, and the point on it
# is recovered from the pair rather than assumed.

def _identity() -> tuple:
    return np.eye(3), np.zeros(3)


def _skew(w) -> np.ndarray:
    return np.array([[0.0, -w[2], w[1]], [w[2], 0.0, -w[0]], [-w[1], w[0], 0.0]])


def _rotation(w) -> np.ndarray:
    """Rodrigues: the rotation a rotation vector stands for."""
    angle = float(np.linalg.norm(w))
    if angle < 1e-14:
        return np.eye(3) + _skew(w)
    axis = np.asarray(w, dtype=float) / angle
    K = _skew(axis)
    return np.eye(3) + math.sin(angle) * K + (1 - math.cos(angle)) * (K @ K)


def _advance(pose: tuple, twist) -> tuple:
    """Apply a twist about the world origin to a pose."""
    R, t = pose
    dR = _rotation(twist[:3])
    return dR @ R, dR @ t + np.asarray(twist[3:], dtype=float)


def _place(pose: tuple, point) -> np.ndarray:
    R, t = pose
    return R @ np.asarray(point, dtype=float) + t


def _turn(pose: tuple, direction) -> np.ndarray:
    return pose[0] @ np.asarray(direction, dtype=float)


def _from_trsf(trsf) -> tuple:
    """A gp_Trsf as (R, t), so the ordered placement can seed the solve."""
    R = np.array([[trsf.Value(r, c) for c in range(1, 4)] for r in range(1, 4)])
    t = np.array([trsf.Value(r, 4) for r in range(1, 4)])
    return R, t


def as_trsf(pose: tuple):
    """(R, t) back as a gp_Trsf, to move the actual solid."""
    from OCP.gp import gp_Trsf

    R, t = pose
    out = gp_Trsf()
    out.SetValues(R[0][0], R[0][1], R[0][2], t[0],
                  R[1][0], R[1][1], R[1][2], t[1],
                  R[2][0], R[2][1], R[2][2], t[2])
    return out


def _turned_since(pose: tuple, reference: tuple, axis, memory: dict | None = None,
                  key=None) -> float:
    """How far a part has turned about a world axis since its reference pose (radians).

    With a ``memory`` the answer is the whole turn, not the angle: the value
    nearest the last one read under ``key``, so a drive can go round twice.
    """
    relative = pose[0] @ reference[0].T
    r0 = _perpendiculars(axis)[0]
    r = relative @ r0
    angle = math.atan2(float(np.asarray(axis) @ np.cross(r0, r)), float(r0 @ r))
    if memory is not None:
        last = memory.get(key)
        if last is not None:
            angle += 2 * math.pi * round((last - angle) / (2 * math.pi))
        memory[key] = angle
    return angle


# --- what a mate is written against ---------------------------------------------

def _perpendiculars(direction) -> tuple:
    """Two unit vectors spanning the plane at right angles to this one."""
    d = np.asarray(direction, dtype=float)
    d = d / np.linalg.norm(d)
    other = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(d, other)
    u /= np.linalg.norm(u)
    return u, np.cross(d, u)


def _geometry(part: str, body: Body, face: str) -> dict:
    """The face a mate names, in its own part's coordinates.

    Read once, before any solving: the pose moves it afterwards by arithmetic.
    A face lookup is an OCCT call and the solver takes hundreds of steps.
    """
    g = mate_geometry(body, face)
    out = {"part": part, "face": face, "shape": g["shape"],
           "centre": np.asarray(g["centre"], dtype=float), "radius": g.get("radius"),
           "topo": body.face(face)}              # the face itself, for a cam
    for key in ("origin", "direction", "x_axis", "mid"):
        if key in g:
            out[key] = np.asarray(g[key], dtype=float)
    return out


def _world(g: dict, pose: tuple) -> dict:
    """The same geometry where the pose puts it."""
    out = {"shape": g["shape"], "radius": g["radius"], "centre": _place(pose, g["centre"])}
    for key in ("origin", "mid"):
        if key in g:
            out[key] = _place(pose, g[key])
    for key in ("direction", "x_axis"):
        if key in g:
            out[key] = _turn(pose, g[key])
    return out


def _held_at(w: dict) -> np.ndarray:
    """The point a placed shape is held by: on the axis, the sphere's centre,
    or the plane's centroid."""
    if w["shape"] == "plane":
        return w["origin"]
    return w["mid"] if "mid" in w else w["centre"]


def _owner(face: str, parts: dict) -> str:
    """Which part a scoped face name belongs to."""
    scope = naming.parse(face).scope
    if scope in parts:
        return scope
    # not scoped, or scoped to something that is not a part here: fall back to
    # whichever part actually has a face by that name
    for name, body in parts.items():
        if body.face(face) is not None:
            return name
    raise CadError("unresolved_reference",
                   f"no part in this assembly has a face named {face!r}",
                   {"parts": sorted(parts)})


# --- residuals ------------------------------------------------------------------
# Each returns numbers that are zero when the mate is satisfied, and there are
# exactly as many of them as the mate takes away: two for "these axes point the
# same way", two more for "and are the same line", one for a distance along it.
# Writing three where two would do (the whole difference of two unit vectors, for
# instance) makes every mate look redundant, which is a thing this has to report.

def _residuals(mate: dict, poses: dict) -> np.ndarray:
    if mate["kind"] == "drive":
        return _drive_residual(mate, poses)
    moving, fixed = mate["geometry"]
    kind = mate["kind"]
    m = _world(moving, poses[moving["part"]])
    f = _world(fixed, poses[fixed["part"]])
    offset = mate["offset"]
    direction_m = m.get("direction")
    direction_f = f.get("direction")
    if direction_f is not None and mate["flip"]:
        direction_f = -direction_f
    if direction_f is not None:
        u, v = _perpendiculars(direction_f)
        aligned = [float(direction_m @ u), float(direction_m @ v)] \
            if direction_m is not None else []

    if kind == "fastened":
        # a full coincidence of the two face frames: nothing left to move
        error = np.cross(direction_m, direction_f) + np.cross(m["x_axis"], f["x_axis"])
        return np.concatenate([m["origin"] - f["origin"], 0.5 * error])

    if kind == "planar":
        return np.asarray(aligned + [float((m["origin"] - f["origin"]) @ direction_f)
                                     + (offset or 0.0)])

    if kind in ("concentric", "hinge", "slider", "screw"):
        between = _held_at(m) - f["origin"]
        out = aligned + [float(between @ u), float(between @ v)]       # the same line
        along = float((_held_at(m) - _held_at(f)) @ direction_f)
        if kind == "concentric" and offset is not None:
            out.append(float((m["origin"] - f["origin"]) @ direction_f) - offset)
        elif kind == "hinge":
            out.append(along - (offset or 0.0))
        elif kind == "slider":
            out.append(_spin(mate, poses, moving["part"], direction_f)
                       - _spin(mate, poses, fixed["part"], direction_f))
        elif kind == "screw":
            # right hand: a turn the way the fingers curl moves along the thumb
            turned = _spin(mate, poses, moving["part"], direction_f) \
                - _spin(mate, poses, fixed["part"], direction_f)
            out.append((along - mate["along_at_rest"])
                       - mate["hand"] * mate["pitch"] * turned / (2 * math.pi))
        return np.asarray(out, dtype=float)

    if kind == "parallel":
        return np.asarray(aligned)
    if kind == "perpendicular":
        return np.asarray([float(direction_m @ direction_f)])
    if kind == "angle":
        return np.asarray([float(direction_m @ direction_f)
                           - math.cos(math.radians(mate["angle"]))])
    if kind == "ball":
        return m["centre"] - f["centre"]
    if kind in ("gear", "belt"):
        between = _held_at(m) - _held_at(f)
        across = between - float(between @ direction_f) * direction_f
        apart = offset if offset is not None else moving["radius"] + fixed["radius"]
        # gear rims move together where they touch, so the turns are in the
        # ratio of the radii and opposite in sense; a belt turns both the same way
        sense = 1.0 if kind == "gear" else -1.0
        coupling = _spin(mate, poses, moving["part"], direction_f) \
            + sense * mate["ratio"] * _spin(mate, poses, fixed["part"], direction_f)
        if kind == "belt" and offset is None:
            return np.asarray(aligned + [coupling])           # any centre distance
        return np.asarray(aligned + [float(np.linalg.norm(across)) - apart, coupling])
    if kind == "slot":
        # the pin's axis lies along the side, a radius (or the offset) off it:
        # outside the face with flip, inside it (a pin in a groove's wall) without
        outward = f["direction"]
        pin_axis = float(direction_m @ outward)
        gap = float((_held_at(m) - f["origin"]) @ outward)
        apart = offset if offset is not None else moving["radius"]
        side = 1.0 if mate["flip"] else -1.0
        return np.asarray([pin_axis, gap - side * apart])
    if kind == "cam":
        return np.asarray([_cam_gap(mate, m, f, poses) - (offset if offset is not None else 0.0)])

    # distance and tangent: how far apart, by what the two shapes are
    apart = offset if kind == "distance" else _touching(mate, moving, fixed)
    if m["shape"] == "plane" and f["shape"] == "plane":
        return np.asarray(aligned + [float((m["origin"] - f["origin"]) @ direction_f)
                                     + (apart or 0.0)])
    if "plane" in (m["shape"], f["shape"]):
        plane, other = (f, m) if f["shape"] == "plane" else (m, f)
        outward = _turn(poses[fixed["part"]] if plane is f else poses[moving["part"]],
                        (fixed if plane is f else moving)["direction"])
        side = 1.0 if mate["flip"] else -1.0      # outside the face, or inside it
        gap = float((_held_at(other) - plane["origin"]) @ outward) - side * apart
        if other.get("direction") is not None:
            return np.asarray([float(other["direction"] @ outward), gap])
        return np.asarray([gap])
    if direction_m is not None and direction_f is not None:
        between = _held_at(m) - _held_at(f)
        across = between - float(between @ direction_f) * direction_f
        return np.asarray(aligned + [float(np.linalg.norm(across)) - apart])
    if direction_f is not None or direction_m is not None:
        axis_of, ball = (f, m) if direction_f is not None else (m, f)
        d = axis_of["direction"]
        between = ball["centre"] - _held_at(axis_of)
        across = between - float(between @ d) * d
        return np.asarray([float(np.linalg.norm(across)) - apart])
    return np.asarray([float(np.linalg.norm(m["centre"] - f["centre"])) - apart])


def _cam_gap(mate: dict, m: dict, f: dict, poses: dict) -> float:
    """How far the follower's surface is from the cam face: zero when they touch."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeVertex, BRepBuilderAPI_Transform
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    moving, fixed = mate["geometry"]
    cam = BRepBuilderAPI_Transform(fixed["topo"], as_trsf(poses[fixed["part"]]), True).Shape()
    held = _held_at(m)
    if m.get("direction") is not None:
        reach = float(moving.get("length") or 1e3)
        a = held - m["direction"] * reach
        b = held + m["direction"] * reach
        probe = BRepBuilderAPI_MakeEdge(gp_Pnt(*a), gp_Pnt(*b)).Edge()
    else:
        probe = BRepBuilderAPI_MakeVertex(gp_Pnt(*held)).Vertex()
    gap = BRepExtrema_DistShapeShape(probe, cam)
    gap.Perform()
    if not gap.IsDone() or gap.NbSolution() < 1:
        return 0.0
    return float(gap.Value()) - float(moving["radius"] or 0.0)


def _touching(mate: dict, moving: dict, fixed: dict) -> float:
    """The gap between the held points when two shapes touch: the radii added
    with ``flip`` (outside each other), subtracted without (one inside the other)."""
    radii = [g["radius"] for g in (moving, fixed) if g["radius"] is not None]
    if len(radii) == 1:
        return radii[0]
    return radii[0] + radii[1] if mate["flip"] else abs(radii[1] - radii[0])


def _spin(mate: dict, poses: dict, part: str, axis) -> float:
    """How far a part has turned about this axis since the reference poses."""
    return _turned_since(poses[part], mate["reference"][part], axis,
                         mate["memory"], (mate["index"], part))


def _drive_residual(drive: dict, poses: dict) -> np.ndarray:
    part = drive["part"]
    if "turn" in drive:
        turned = _spin(drive, poses, part, drive["axis"])
        return np.asarray([turned - math.radians(drive["turn"])])
    moved = _place(poses[part], drive["point"]) - _place(drive["reference"][part],
                                                          drive["point"])
    return np.asarray([float(moved @ drive["axis"]) - drive["slide"]])


def _stacked(mates: list, poses: dict) -> np.ndarray:
    return np.concatenate([_residuals(m, poses) for m in mates]) if mates \
        else np.zeros(0)


def _jacobian(mates: list, poses: dict, free: list) -> np.ndarray:
    """Numerically, by central differences.

    The residuals are arithmetic on numbers read once, so a column costs
    nothing measurable and an analytic derivative would only be another place
    for the two to disagree.
    """
    step = 1e-7
    columns = []
    for part in free:
        for axis in range(6):
            twist = np.zeros(6)
            twist[axis] = step
            ahead = dict(poses, **{part: _advance(poses[part], twist)})
            behind = dict(poses, **{part: _advance(poses[part], -twist)})
            columns.append((_stacked(mates, ahead) - _stacked(mates, behind))
                           / (2 * step))
    return np.column_stack(columns) if columns else np.zeros((len(_stacked(mates, poses)), 0))


# --- solving --------------------------------------------------------------------

def prepare(parts: dict, mates: list) -> list:
    """Each mate with its faces read once, ready to solve."""
    prepared = []
    for index, spec in enumerate(mates):
        faces = spec.get("faces") or []
        if len(faces) != 2:
            raise CadError("bad_arguments", "a mate needs exactly two faces",
                           {"mate": index, "given": faces})
        kind = spec.get("kind", "fastened")
        if kind not in MATES:
            raise CadError("unknown_mate", f"mate kind {kind!r} is not implemented",
                           {"mate": index, "available": list(MATES)})
        owners = [_owner(face, parts) for face in faces]
        geometry = [_geometry(owners[i], parts[owners[i]], faces[i]) for i in range(2)]
        check_mate_faces(kind, geometry[0], geometry[1])
        pitch = spec.get("pitch")
        if kind == "screw" and not pitch:
            raise CadError("missing_argument", "a screw mate needs a pitch",
                           {"mate": index, "faces": list(faces)})
        radii = [g["radius"] for g in geometry]
        prepared.append({
            "index": index, "kind": kind, "faces": list(faces),
            "flip": bool(spec.get("flip", True)),
            "offset": None if spec.get("offset") is None else float(spec["offset"]),
            "angle": float(spec.get("angle") or 0.0),
            "pitch": float(pitch or 0.0),
            "hand": -1.0 if spec.get("hand") == "left" else 1.0,
            "ratio": float(spec["ratio"]) if spec.get("ratio")
            else (radii[1] / radii[0] if kind in ("gear", "belt") and radii[0] else 1.0),
            "limits": _limits(spec, index, kind),
            "geometry": geometry, "parts": owners})
    return prepared


def _limits(spec: dict, index: int, kind: str) -> tuple | None:
    """``(min, max)`` a hinge may turn (degrees) or a slider slide (mm), or None."""
    low, high = spec.get("min"), spec.get("max")
    if low is None and high is None:
        return None
    if kind not in ("hinge", "slider"):
        raise CadError("bad_arguments", "min and max limit a hinge or a slider",
                       {"mate": index, "kind": kind})
    low = -math.inf if low is None else float(low)
    high = math.inf if high is None else float(high)
    if low > high:
        raise CadError("bad_arguments", "a limit's min must not be above its max",
                       {"mate": index, "min": low, "max": high})
    return (low, high)


def travelled(mate: dict, poses: dict) -> float:
    """How far a hinge has turned (degrees) or a slider slid (mm) since its rest pose."""
    moving, fixed = mate["geometry"]
    f = _world(fixed, poses[fixed["part"]])
    d = f["direction"]
    if mate["flip"]:
        d = -d
    if mate["kind"] == "hinge":
        return math.degrees(_spin(mate, poses, moving["part"], d)
                            - _spin(mate, poses, fixed["part"], d))
    along = float((_held_at(_world(moving, poses[moving["part"]])) - _held_at(f)) @ d)
    return along - mate.get("along_at_rest", along)


def solve(parts: dict, mates: list, ground: str | None = None,
          iterations: int = 60, drives: list | None = None,
          seed: dict | None = None, reference: dict | None = None,
          memory: dict | None = None) -> dict:
    """Place every part so that every mate holds, and say what is left free.

    ``parts`` are the unplaced bodies by name, ``mates`` the constraints. The
    first one listed is the ground unless another is named: something has to
    be, because an assembly with no ground floats -- every part free together
    is six degrees of freedom nobody asked about.

    ``drives`` hold one part a set way along a freedom it has (see
    :func:`drive`); ``seed`` starts the solve from these poses instead of the
    ordered placement, and ``reference`` is where turns and slides are
    measured from -- the seed, unless given. ``memory`` carries the last
    turn read for each coupled part from one solve to the next, so a chain
    of solves can go past a half turn.
    """
    if not parts:
        raise CadError("not_an_assembly", "an assembly needs parts to place",
                       {"hint": "list them in the assemble feature's bodies"})
    ground = ground or next(iter(parts))       # the first listed, as written
    if ground not in parts:
        raise CadError("unknown_feature", f"no part named {ground!r} to ground on",
                       {"parts": sorted(parts)})

    prepared = prepare(parts, mates)
    poses = {name: _identity() for name in parts}
    free = [name for name in sorted(parts) if name != ground]
    chain = []
    if seed:
        poses.update({name: (np.array(pose[0], dtype=float), np.array(pose[1], dtype=float))
                      for name, pose in seed.items()})
    else:
        chain = _seed(parts, prepared, poses, ground)
    # turns and slides are measured from here: the seed, unless the caller
    # (a drive) says where the rest pose was
    reference = reference or {name: pose for name, pose in poses.items()}
    memory = {} if memory is None else memory
    for mate in prepared:
        mate["reference"] = reference
        mate["memory"] = memory
        if mate["kind"] in ("screw", "slider"):   # the seed says where the travel starts
            moving, fixed = mate["geometry"]
            d = _world(fixed, reference[fixed["part"]])["direction"]
            if mate["flip"]:
                d = -d
            mate["along_at_rest"] = float(
                (_held_at(_world(moving, reference[moving["part"]]))
                 - _held_at(_world(fixed, reference[fixed["part"]]))) @ d)
    _settle(prepared, poses, chain)
    for index, spec in enumerate(drives or []):
        if spec["part"] not in parts:
            raise CadError("unknown_feature", f"no part named {spec['part']!r} to drive",
                           {"parts": sorted(parts)})
        if spec["part"] == ground:
            raise CadError("no_freedom", f"{spec['part']!r} is the ground; it cannot move",
                           {"part": spec["part"], "hint": "drive another part, or "
                                                          "ground a different one"})
        prepared.append(dict(spec, kind="drive", index=f"drive:{index}",
                             faces=[], reference=reference, memory=memory,
                             axis=np.asarray(spec["axis"], dtype=float),
                             point=np.asarray(spec.get("point", (0.0, 0.0, 0.0)),
                                              dtype=float)))
    for mate in prepared:                # how many numbers each one contributes
        mate["rows"] = len(_residuals(mate, poses))

    # -- Gauss-Newton, damped: the seed is close, so this is a polish
    for _ in range(iterations):
        residual = _stacked(prepared, poses)
        if not residual.size or float(np.linalg.norm(residual)) < TOLERANCE:
            break
        J = _jacobian(prepared, poses, free)
        step, *_ = np.linalg.lstsq(J, -residual, rcond=None)
        length = float(np.linalg.norm(step))
        if length > 50.0:                      # keep a bad seed from throwing a part
            step *= 50.0 / length
        for i, part in enumerate(free):
            poses[part] = _advance(poses[part], step[6 * i:6 * i + 6])
        if length < 1e-12:
            break

    return _report(prepared, poses, free, ground,
                   _jacobian(prepared, poses, free))


def _seed(parts: dict, mates: list, poses: dict, ground: str) -> list:
    """Start from the ordered placement: each part put by one mate, in turn.

    The same computation the ``mate`` feature has always done, used as a first
    guess rather than as the answer. It costs one transform per part and it
    lands the parts the right way round, which matters: two faces pointing at
    each other and two faces back to back both satisfy "parallel", and only a
    starting point tells the solver which was meant.

    Returns what placed each part: ``(moving, fixed, axis, point)`` with the
    axis the mate left it free to spin about, or None.
    """
    placed = {ground}
    progress = True
    chain = []
    # a mate that fixes which way up a part is seeds it before one that only
    # says where its axis lies: a washer put on its seat and then slid to the
    # bore lands the right way up, one put in the bore first may not
    first = {"fastened": 0, "planar": 1, "distance": 1, "tangent": 1}
    while progress:
        progress = False
        for mate in sorted(mates, key=lambda m: first.get(m["kind"], 2)):
            # whichever end is not placed yet is the one this mate can move,
            # so a mate written the other way round still seeds
            order = [0, 1] if mate["parts"][1] in placed else [1, 0]
            moving, fixed = (mate["parts"][i] for i in order)
            if moving in placed or fixed not in placed:
                continue
            try:
                trsf = mate_transform(
                    mate["kind"], parts[moving], moved(parts[fixed], poses[fixed]),
                    [mate["faces"][i] for i in order],
                    mate["offset"] or 0.0, mate["angle"], mate["flip"])
            except CadError:
                continue                 # a mate that cannot seed still constrains
            poses[moving] = _from_trsf(trsf)
            placed.add(moving)
            progress = True
            held = mate["geometry"][order[1]]
            spins = held.get("direction") is not None and mate["kind"] != "fastened"
            chain.append((moving, fixed, held if spins else None))
    return chain


def _settle(mates: list, poses: dict, chain: list) -> None:
    """Turn each seeded part about the axis its mate left free, with the parts
    seeded from it, until the other mates are as near as they can be to holding.

    The ordered seed knows nothing about a loop: the last mate of a four-bar
    is left wide open, and a polish over every part's six unknowns walks into
    a twisted compromise. Over the spins alone the chain's own mates stay
    exact, so what is left is closing the loop, which a Gauss-Newton on a few
    angles does; a straight chain is a saddle for it, so it is restarted from
    a few angles apart when the first try does not close.
    """
    spins = [(moving, fixed, held) for moving, fixed, held in chain if held is not None]
    if not mates or not spins:
        return
    groups = {moving: _descendants(moving, chain) for moving, _, _ in spins}

    def turned(angles) -> dict:
        out = dict(poses)
        for (moving, fixed, held), angle in zip(spins, angles):
            if abs(angle) < 1e-15:
                continue
            at = _world(held, out[fixed])          # the axis where the parent now is
            out = _spun(out, groups[moving], at["direction"], _held_at(at), angle)
        return out

    def error(angles) -> np.ndarray:
        return _stacked(mates, turned(angles))

    def polish(angles) -> tuple:
        angles = np.asarray(angles, dtype=float)
        for _ in range(40):
            r = error(angles)
            if float(np.linalg.norm(r)) < TOLERANCE:
                break
            columns = []
            for i in range(len(angles)):
                step = np.zeros(len(angles))
                step[i] = 1e-6
                columns.append((error(angles + step) - error(angles - step)) / 2e-6)
            move, *_ = np.linalg.lstsq(np.column_stack(columns), -r, rcond=None)
            length = float(np.linalg.norm(move))
            if length > 0.5:
                move *= 0.5 / length
            angles = angles + move
            if length < 1e-12:
                break
        return float(np.linalg.norm(error(angles))), angles

    best, angles = polish(np.zeros(len(spins)))
    if best > SATISFIED:
        import itertools

        thirds = (0.0, 2 * math.pi / 3, -2 * math.pi / 3)
        for start in itertools.product(thirds, repeat=min(3, len(spins))):
            seed = np.zeros(len(spins))
            seed[:len(start)] = start
            found, candidate = polish(seed)
            if found < best - 1e-9:
                best, angles = found, candidate
            if best < TOLERANCE:
                break
    poses.update(turned(angles))


def _descendants(part: str, chain: list) -> list:
    """The part and every part seeded from it, which move with it."""
    out, todo = [], [part]
    while todo:
        here = todo.pop()
        out.append(here)
        todo += [m for m, f, _ in chain if f == here and m not in out]
    return out


def _spun(poses: dict, group: list, axis, point, angle: float) -> dict:
    """The poses with these parts turned together about a world axis."""
    w = np.asarray(axis, dtype=float) * angle
    dR = _rotation(w)
    p = np.asarray(point, dtype=float)
    twist = np.concatenate([w, p - dR @ p])
    return {name: (_advance(pose, twist) if name in group else pose)
            for name, pose in poses.items()}


def moved(body: Body, pose: tuple) -> Body:
    """A part where the solve put it, names and all."""
    from .assembly import transformed

    if np.allclose(pose[0], np.eye(3)) and np.allclose(pose[1], 0.0):
        return body
    return transformed(body, as_trsf(pose))


# --- driving a freedom ----------------------------------------------------------

def drive(parts: dict, mates: list, drives: list, ground: str | None = None,
          rest: dict | None = None, frames: int = 1) -> dict:
    """Move parts along the freedom the mates leave them, the rest following.

    Each drive names a part and either ``turn`` (degrees) or ``slide`` (mm),
    and may say ``about`` or ``along`` (a direction) to pick which of the
    part's freedoms when it has more than one. The amount is reached in steps
    from the rest pose, each solve seeded by the last, so a closed chain stays
    on the branch it started on. ``frames`` poses along the way come back.
    """
    rest = rest or solve(parts, mates, ground)
    if rest["conflicts"]:
        raise CadError("over_constrained", "these mates cannot all be true at once",
                       {"conflicts": rest["conflicts"]})
    resolved = [_pick_freedom(spec, rest) for spec in drives]
    biggest = max(abs(float(s.get("turn") or 0.0)) / STEP_DEG
                  if "turn" in s else abs(float(s.get("slide") or 0.0)) / STEP_MM
                  for s in resolved)
    frames = max(1, int(frames))
    keep_every = max(1, int(math.ceil(biggest / frames)))
    steps = keep_every * frames                # so exactly ``frames`` poses come back
    poses = rest["poses"]
    out_frames = []
    report = rest
    memory: dict = {}
    for k in range(1, steps + 1):
        share = k / steps
        partial = [dict(s, **({"turn": s["turn"] * share} if "turn" in s
                              else {"slide": s["slide"] * share}))
                   for s in resolved]
        report = solve(parts, mates, rest["ground"], drives=partial, seed=poses,
                       reference=rest["poses"], memory=memory)
        poses = report["poses"]
        for mate in report["_mates"]:
            if mate.get("limits"):
                low, high = mate["limits"]
                went = travelled(mate, poses)
                if went < low - 1e-6 or went > high + 1e-6:
                    unit = "deg" if mate["kind"] == "hinge" else "mm"
                    raise CadError("no_freedom",
                                   f"the {mate['kind']} between {mate['faces'][0]!r} and "
                                   f"{mate['faces'][1]!r} stops at {high if went > high else low:g} {unit}",
                                   {"mate": mate["index"], "reached": round(went, 4),
                                    "min": low, "max": high, "unit": unit})
        if report["conflicts"]:
            stuck = [c for c in report["conflicts"] if str(c["mate"]).startswith("drive")]
            raise CadError("no_freedom",
                           "the mates do not let this move go that far",
                           {"reached": [dict((key, s[key]) for key in ("part", "turn", "slide")
                                             if key in s) for s in partial],
                            "conflicts": report["conflicts"],
                            "hint": "a loop of parts has reached its limit, or "
                                    "the mates hold the part" if stuck else
                                    "the drive is fine but another mate broke"})
        if k % keep_every == 0:
            out_frames.append({name: [np.round(pose[0], 9).tolist(),
                                      np.round(pose[1], 6).tolist()]
                               for name, pose in poses.items()})
    return dict(report, frames=out_frames, drives=[
        dict((key, s[key]) for key in ("part", "turn", "slide") if key in s)
        | {"axis": [round(float(c), 6) for c in s["axis"]]} for s in resolved])


def _pick_freedom(spec: dict, rest: dict) -> dict:
    """The world axis a drive moves along, from what the rest pose leaves free."""
    part = spec.get("part")
    if part == rest["ground"]:
        raise CadError("no_freedom", f"{part!r} is the ground; it cannot move",
                       {"part": part, "hint": "drive another part, or ground a different one"})
    free = rest["freedom"].get(part)
    if free is None:
        raise CadError("unknown_feature", f"no part named {part!r} to drive",
                       {"parts": sorted(rest["freedom"]) + [rest["ground"]]})
    turning = "turn" in spec and spec["turn"] is not None
    if not turning and spec.get("slide") is None:
        raise CadError("bad_arguments", "a drive says how far: turn (degrees) or slide (mm)",
                       {"drive": spec})
    choices = free["turns_about"] if turning else free["slides_along"]
    if not choices:
        raise CadError("no_freedom",
                       f"{part!r} cannot {'turn' if turning else 'slide'}: the mates hold it",
                       {"part": part, "freedom": free,
                        "hint": "leave an offset out of a mate, or drive another part"})
    wanted = spec.get("about") if turning else spec.get("along")
    if wanted is not None:
        w = np.asarray(wanted, dtype=float)
        w = w / (np.linalg.norm(w) or 1.0)
        chosen = max(choices, key=lambda c: abs(float(
            np.asarray(c["direction"] if turning else c) @ w)))
        sign = 1.0 if float(np.asarray(chosen["direction"] if turning else chosen) @ w) >= 0 \
            else -1.0
    else:
        chosen, sign = choices[0], 1.0
    if turning:
        return {"part": part, "turn": float(spec["turn"]) * sign,
                "axis": chosen["direction"], "point": chosen["through"]}
    return {"part": part, "slide": float(spec["slide"]) * sign, "axis": chosen,
            "point": [0.0, 0.0, 0.0]}


# --- the report -----------------------------------------------------------------

SATISFIED = 1e-4          # mm: nearer than any manufacturing cares about


def _report(mates: list, poses: dict, free: list, ground: str,
            J: np.ndarray) -> dict:
    rows = J.shape[0] if J.size else 0
    singular = np.linalg.svd(J, compute_uv=False) if J.size else np.zeros(0)
    scale = float(singular[0]) if singular.size else 1.0
    rank = int(np.sum(singular > max(RANK_TOLERANCE, scale * 1e-9)))
    dof = 6 * len(free) - rank

    conflicts = []
    for mate in mates:
        error = float(np.linalg.norm(_residuals(mate, poses)))
        if error > SATISFIED:
            # the solve stops at TOLERANCE, three orders finer; a mate is only
            # called a conflict when it misses by something a shop would see
            conflicts.append({"mate": mate["index"], "kind": mate["kind"],
                              "faces": mate["faces"], "error": round(error, 6)})

    return {"poses": poses, "ground": ground, "_mates": mates,
            "dof": dof, "constrained": rank, "equations": rows,
            "freedom": _freedom(free, J, dof),
            "redundant": _redundant(mates, J, rank) if rows > rank else [],
            "conflicts": conflicts,
            "solved": not conflicts}


def _freedom(free: list, J: np.ndarray, dof: int) -> dict:
    """What each part can still do, from the null space of the Jacobian.

    A vector in the null space is a twist no mate objects to. The basis SVD
    hands back is arbitrary, though, and reporting it raw made a part nobody
    had mated come back as six meaningless screws. So the block is split into
    the two things a person asks about: which ways it can turn -- the column
    space of the rotation half, which is basis-independent -- and which ways it
    can move without turning, which is the rest.

    A rotation is reported as a line rather than a direction: the point on its
    screw axis nearest the origin comes out of the pair, so "turns about the
    bore" says *which* bore.
    """
    out = {part: {"dof": 0, "turns_about": [], "slides_along": []} for part in free}
    if dof <= 0 or not J.size:
        return out
    null = np.linalg.svd(J)[2][J.shape[1] - dof:].T
    for i, part in enumerate(free):
        block = null[6 * i:6 * i + 6]
        if not block.size or not np.any(np.abs(block) > 1e-9):
            continue
        spin, slide = block[:3], block[3:]
        out[part]["dof"] = int(np.linalg.matrix_rank(block, tol=1e-9))

        axes, spread, _ = np.linalg.svd(spin)
        turns = int(np.sum(spread > max(1e-9, float(spread[0]) * 1e-6))) \
            if spread.size else 0
        for j in range(turns):
            axis = axes[:, j]
            # the combination of null vectors that turns exactly about `axis`,
            # so the translation that comes with it is this rotation's own
            twist = block @ (np.linalg.pinv(spin) @ axis)
            through = np.cross(twist[:3], twist[3:]) / float(twist[:3] @ twist[:3])
            out[part]["turns_about"].append(
                {"direction": _unit(axis),
                 "through": [round(float(c), 6) + 0.0 for c in through]})

        # what is left after the turns: the combinations that do not rotate
        held = _null_space(spin)
        out[part]["slides_along"] = _independent(
            [slide @ held[:, k] for k in range(held.shape[1])])
    return out


def _null_space(matrix: np.ndarray) -> np.ndarray:
    """The combinations this matrix sends to nothing."""
    _, spread, vt = np.linalg.svd(matrix)
    limit = max(1e-9, float(spread[0]) * 1e-6) if spread.size else 1e-9
    keep = int(np.sum(spread > limit))
    return vt[keep:].T


def _unit(vector) -> list:
    v = np.asarray(vector, dtype=float)
    v = v / np.linalg.norm(v)
    if float(v[np.argmax(np.abs(v))]) < 0:      # a direction, not an arrow
        v = -v
    return [round(float(c), 6) + 0.0 for c in v]     # + 0.0: never a "-0"


def _independent(directions: list) -> list:
    """Drop the ones the others already span: a plane is two, not three."""
    kept: list = []
    for d in directions:
        v = np.asarray(d, dtype=float)
        for k in kept:
            v = v - float(v @ np.asarray(k)) * np.asarray(k)
        if float(np.linalg.norm(v)) > 1e-6:
            kept.append(_unit(v))
    return kept


def _redundant(mates: list, J: np.ndarray, rank: int) -> list:
    """Which mates say something another one has already said.

    From the left null space: a combination of rows that adds to nothing is a
    constraint the rest of them already imply. It is reported and not refused
    -- a redundant mate is usually a designer being explicit, and it only
    becomes an error when it disagrees, which is what `conflicts` is for.
    """
    u, singular, _ = np.linalg.svd(J)
    left = u[:, rank:]
    if not left.size:
        return []
    spans, row = {}, 0
    for mate in mates:
        spans[mate["index"]] = (row, row + mate["rows"], mate)
        row += mate["rows"]
    weight = np.linalg.norm(left, axis=1)
    out = []
    for index, (start, stop, mate) in spans.items():
        share = float(np.max(weight[start:stop])) if stop > start else 0.0
        if share > 1e-6:
            out.append({"mate": index, "kind": mate["kind"],
                        "faces": mate["faces"], "share": round(share, 4)})
    return out
