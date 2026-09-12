"""2D sketches: geometry plus constraints, solved with PlaneGCS."""
from __future__ import annotations

import math

from .. import names
from ..errors import CadError
from . import constraints, derived
from .loops import _loops as loops_of
from .model import Plane, Segment, SketchResult

__all__ = ["solve", "extent", "Plane", "Segment", "SketchResult"]



def solve(spec: dict, evaluate) -> SketchResult:
    """Solve the sketch, and only trust a clean bill of health.

    PlaneGCS reports degrees of freedom from the rank of the Jacobian at the
    solution, and that rank lies in a common case: a tangency that happens to be
    satisfied exactly (a rounded corner meeting a vertical side) comes back as
    "redundant", leaving phantom degrees of freedom on a sketch that is in fact
    fully determined. Refusing there would make arcs unusable; trusting the
    counter blindly would let genuinely floppy sketches through.

    So when degrees of freedom are left over, the sketch is solved a second time
    from a jittered start. If it lands in the same place, it was determined and
    the count was an artefact. If it lands somewhere else, it really can move,
    and the refusal names the points that moved.
    """
    result = _solve_once(spec, evaluate)
    if result.dof <= 0 or spec.get("allow_underconstrained"):
        return result

    scale = extent(result.points)
    # the jitter is ~2% of the sketch, and a free point moves by that much; the
    # tolerance only has to clear the solver's own convergence noise
    tolerance = max(1e-6, scale * 1e-5)
    try:
        shifted = _solve_once(spec, evaluate, jitter=scale * 0.02)
    except CadError:
        # the jittered start failing to converge says nothing about the sketch
        # -- the real solve above succeeded. The check is inconclusive, not a
        # verdict, so the successful solve stands
        result.diagnosis["jitter_check"] = "inconclusive"
        return result
    moved = {name: (result.points[name], shifted.points[name])
             for name in result.points
             if math.dist(result.points[name], shifted.points[name]) > tolerance}
    if moved:
        raise CadError("sketch_underconstrained",
                       f"{result.dof} degree(s) of freedom left in the sketch",
                       {"diagnosis": result.diagnosis,
                        "moved": {k: [list(a), list(b)] for k, (a, b) in
                                  list(moved.items())[:6]},
                        "hint": "add dimensions, or set allow_underconstrained"})
    result.diagnosis["determinate_despite_dof"] = True
    result.diagnosis["jitter_tolerance"] = tolerance
    return result


def extent(points: dict) -> float:
    if not points:
        return 1.0
    span = max(max(p[i] for p in points.values()) - min(p[i] for p in points.values())
               for i in range(2))
    return span or 1.0


def _solve_once(spec: dict, evaluate, jitter: float = 0.0) -> SketchResult:
    from planegcs import Sketch as GcsSketch, SolveStatus

    plane = Plane(tuple(evaluate(spec.get("plane", {}).get("origin", [0, 0, 0]))),
                  tuple(evaluate(spec.get("plane", {}).get("normal", [0, 0, 1]))),
                  tuple(evaluate(spec.get("plane", {}).get("x_axis", [1, 0, 0]))))

    gcs = GcsSketch()
    for key in ("points", "lines", "arcs", "circles", "ellipses", "splines", "slots",
                "offsets", "trims"):
        table = spec.get(key)
        if table is not None and not isinstance(table, dict):
            raise CadError("bad_arguments", f"a sketch's {key} is an object of names",
                           {"key": key, "got": type(table).__name__})
        for name in table or {}:
            names.check_id(name, "sketch %s name" % key[:-1])
    if not isinstance(spec.get("constraints", []), list):
        raise CadError("bad_arguments", "a sketch's constraints are a list",
                       {"got": type(spec.get("constraints")).__name__})
    pid: dict[str, object] = {}
    start: dict[str, tuple] = {}
    for k, (name, xy) in enumerate((spec.get("points") or {}).items()):
        u, v = evaluate(xy)
        if jitter:                       # deterministic, so a refusal reproduces
            u += jitter * (((k * 7919) % 13) - 6) / 6.0
            v += jitter * (((k * 104729) % 11) - 5) / 5.0
        start[name] = (float(u), float(v))
        pid[name] = gcs.add_point(float(u), float(v))
    if not pid:
        raise CadError("empty_sketch", "a sketch needs at least one point")

    def require_points(kind: str, name: str, *points) -> None:
        for p in points:
            if p not in pid:
                raise CadError("unknown_point", f"{kind} {name!r} refers to point {p!r}",
                               {"known": sorted(pid)})

    lid: dict[str, object] = {}
    ends: dict[str, tuple] = {}
    # every line's endpoints, including the ones a trim takes out of the
    # profile: a trimmed line is still there to be cut *by*
    defined: dict[str, tuple] = {}
    pending: list = []

    def add_line(name, p1, p2, strict=True):
        if strict and not all(p in pid for p in (p1, p2)):
            return False
        require_points("line", name, p1, p2)
        lid[name] = gcs.add_line(pid[p1], pid[p2])
        ends[name] = defined[name] = (p1, p2)
        return True

    # An offset makes its own endpoints, and other lines close the profile with
    # them, so the two cannot be read in one pass: lines whose points do not
    # exist yet wait for the offsets and are tried again.
    for name, (p1, p2) in spec.get("lines", {}).items():
        if not add_line(name, p1, p2):
            pending.append((name, p1, p2))

    aid: dict[str, object] = {}
    arcs: dict[str, dict] = {}
    for name, spec_arc in (spec.get("arcs") or {}).items():
        centre, a, b = spec_arc.get("centre"), spec_arc.get("from"), spec_arc.get("to")
        require_points("arc", name, centre, a, b)
        c0, s0, e0 = start[centre], start[a], start[b]
        r0 = float(evaluate(spec_arc["radius"])) if "radius" in spec_arc else \
            math.dist(s0, c0) or 1.0
        t0 = math.atan2(s0[1] - c0[1], s0[0] - c0[0])
        t1 = math.atan2(e0[1] - c0[1], e0[0] - c0[0])
        ccw = bool(spec_arc.get("ccw", True))
        # PlaneGCS reads the sweep direction from the angles alone
        if ccw and t1 <= t0:
            t1 += 2 * math.pi
        if not ccw and t1 >= t0:
            t1 -= 2 * math.pi
        rp = gcs.add_param(r0)
        arc = gcs.add_arc(pid[centre], pid[a], pid[b], rp,
                          gcs.add_param(t0), gcs.add_param(t1))
        aid[name] = arc
        arcs[name] = {"centre": centre, "ccw": ccw, "radius_param": rp}
        ends[name] = (a, b)
        if "radius" in spec_arc:
            gcs.arc_radius(arc, gcs.add_param(r0, fixed=True))

    cid: dict[str, object] = {}
    circles: dict[str, dict] = {}
    for name, spec_circle in (spec.get("circles") or {}).items():
        centre = spec_circle.get("centre")
        require_points("circle", name, centre)
        r0 = float(evaluate(spec_circle["radius"])) if "radius" in spec_circle else 1.0
        rp = gcs.add_param(r0)
        cid[name] = gcs.add_circle(pid[centre], rp)
        circles[name] = {"centre": centre, "radius_param": rp}
        if "radius" in spec_circle:
            gcs.circle_radius(cid[name], gcs.add_param(r0, fixed=True))

    eid: dict[str, object] = {}
    ellipses: dict[str, dict] = {}
    for name, spec_ellipse in (spec.get("ellipses") or {}).items():
        centre = spec_ellipse.get("centre")
        require_points("ellipse", name, centre)
        major = float(evaluate(spec_ellipse["major"]))
        minor = float(evaluate(spec_ellipse["minor"]))
        if not 0 < minor <= major:
            raise CadError("bad_parameter",
                           f"ellipse {name!r} needs 0 < minor <= major",
                           {"major": major, "minor": minor})
        turn = math.radians(float(evaluate(spec_ellipse.get("angle", 0.0))))
        reach = math.sqrt(major * major - minor * minor)       # centre to focus
        cx, cy = start[centre]
        focus = f"{name}_f"
        start[focus] = (cx + reach * math.cos(turn), cy + reach * math.sin(turn))
        pid[focus] = gcs.add_point(*start[focus])
        # the focus is held where the sizes put it: the ellipse follows its centre only
        gcs.p2p_distance(pid[centre], pid[focus], gcs.add_param(reach, fixed=True))
        if reach > 1e-9:
            gcs.p2p_angle(pid[centre], pid[focus], gcs.add_param(turn, fixed=True))
        eid[name] = gcs.add_ellipse(pid[centre], pid[focus], minor)
        ellipses[name] = {"centre": centre, "focus": focus, "minor": minor}

    splines: dict[str, list] = {}
    for name, spec_spline in (spec.get("splines") or {}).items():
        through = spec_spline.get("through") or []
        if len(through) < 3:
            raise CadError("bad_arguments",
                           f"spline {name!r} needs at least three points",
                           {"given": through})
        require_points("spline", name, *through)
        splines[name] = list(through)
        ends[name] = defined[name] = (through[0], through[-1])

    for name, spec_offset in (spec.get("offsets") or {}).items():
        derived._add_offset(gcs, name, spec_offset, pid, lid, ends, defined, start, evaluate)
    for name, p1, p2 in pending:
        add_line(name, p1, p2, strict=False)

    for name, spec_trim in (spec.get("trims") or {}).items():
        derived._add_trim(gcs, name, spec_trim, pid, lid, ends, defined, start)

    for name, spec_slot in (spec.get("slots") or {}).items():
        derived._add_slot(gcs, name, spec_slot, pid, lid, aid, ends, arcs, start, evaluate)

    if not (ends or circles or ellipses):
        raise CadError("empty_sketch", "a sketch needs at least one segment")

    tags = []
    for k, con in enumerate(spec.get("constraints", [])):
        try:
            tag = constraints.apply(gcs, con, pid, lid, aid, cid, evaluate, k)
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            # the constraint's own shape is wrong (no value, one curve of two, not an object)
            raise CadError("bad_constraint",
                           "constraint %d (%s) is missing what it reads: %s"
                           % (k, (con or {}).get("type") if isinstance(con, dict) else con, exc),
                           {"index": k, "constraint": con}) from exc
        if tag is not None:
            tags.append((k, con.get("type"), tag))

    status = gcs.solve()
    diag = gcs.diagnose()
    residuals = {}
    for k, kind, tag in tags:
        try:
            residuals[f"{k}:{kind}"] = abs(float(gcs.constraint_error(tag)))
        except Exception:                                       # noqa: BLE001
            continue
    worst = max(residuals.values(), default=0.0)
    info = {"dof": diag.dof,
            "conflicting": list(diag.conflicting),
            "redundant": list(diag.redundant),
            "partially_redundant": list(diag.partially_redundant),
            "worst_residual": round(worst, 12)}
    # The solver distinguishes "Success" from "Converged", and the difference is
    # not always meaningful -- what matters is whether the constraints actually
    # hold, so the residuals decide rather than the status word.
    if str(status) == str(SolveStatus.Failed) or worst > 1e-6:
        raise CadError("sketch_unsolved", f"the constraint solver returned {status}",
                       {"diagnosis": info,
                        "worst": max(residuals, key=residuals.get) if residuals else None,
                        "hint": "conflicting constraints, or a bad starting position"})
    if diag.conflicting:
        raise CadError("sketch_conflicting", "conflicting constraints", {"diagnosis": info})
    points = {name: tuple(round(c, 9) for c in gcs.get_point(p)) for name, p in pid.items()}
    segments: dict[str, Segment] = {}
    for name, (a, b) in ends.items():
        if name in splines:
            segments[name] = Segment(name, "spline", points[a], points[b],
                                     through=[points[p] for p in splines[name]])
        elif name in aid:
            solved = gcs.get_arc(aid[name])
            segments[name] = Segment(name, "arc", points[a], points[b],
                                     tuple(round(c, 9) for c in solved.center),
                                     round(solved.radius, 9), arcs[name]["ccw"])
        else:
            segments[name] = Segment(name, "line", points[a], points[b])
    for name, circle in circles.items():
        solved = gcs.get_circle(cid[name])
        centre = tuple(round(c, 9) for c in solved.center)
        segments[name] = Segment(name, "circle", centre, centre, centre,
                                 round(solved.radius, 9))
    for name, ellipse in ellipses.items():
        solved = gcs.get_ellipse(eid[name])
        centre = tuple(round(c, 9) for c in solved.center)
        dx, dy = solved.focus1[0] - centre[0], solved.focus1[1] - centre[1]
        minor = float(solved.radmin)
        segments[name] = Segment(name, "ellipse", centre, centre, centre,
                                 round(math.hypot(math.hypot(dx, dy), minor), 9),
                                 minor=round(minor, 9), angle=round(math.atan2(dy, dx), 12))

    open_path = bool(spec.get("open"))
    return SketchResult(plane, points, loops_of(ends, segments, open_path), diag.dof, info,
                        not open_path)
