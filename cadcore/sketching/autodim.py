"""Turn a roughly drawn sketch into a dimensioned, fully constrained one.

The drawing is read as intent: near-axis segments become horizontal and
vertical holds, lengths become named dimensions. Dimensions are added
cheapest first, only while the solver says freedom is left.
"""
from __future__ import annotations

import math

from ..errors import CadError

AXIS_TOLERANCE_DEG = 4.0        # how far off axis a segment may be and still snap


def name_segment(start: tuple, end: tuple, used: dict) -> str:
    """A readable name from the direction the segment runs on the face."""
    du, dv = end[0] - start[0], end[1] - start[1]
    if abs(du) >= abs(dv):
        role = "east" if du > 0 else "west"
    else:
        role = "north" if dv > 0 else "south"
    used[role] = used.get(role, 0) + 1
    return role if used[role] == 1 else f"{role}{used[role]}"


def _angle_off_axis(start: tuple, end: tuple) -> tuple:
    du, dv = end[0] - start[0], end[1] - start[1]
    length = math.hypot(du, dv)
    if length < 1e-9:
        raise CadError("degenerate_segment", "a drawn segment has zero length")
    horizontal = math.degrees(math.asin(min(1.0, abs(dv) / length)))
    vertical = math.degrees(math.asin(min(1.0, abs(du) / length)))
    return horizontal, vertical, length


def draft_spec(points: list, lines: list, circles: list, plane: dict,
               arcs=(), ellipses=(), slots=()) -> tuple:
    """The sketch as drawn: points, named segments, and the obvious constraints.

    ``arcs``, ``ellipses`` and ``slots`` are curved primitives the pen places
    whole -- each references its points by their drawn index. They are pinned
    where drawn and left undimensioned, the way ``add_profile`` makes the same
    shapes, so the sketch stays ``allow_underconstrained``."""
    spec = {"plane": plane, "points": {}, "lines": {}, "circles": {},
            "constraints": [], "allow_underconstrained": True}
    for i, (u, v) in enumerate(points):
        spec["points"][f"p{i}"] = [round(float(u), 6), round(float(v), 6)]

    used: dict = {}
    order: list = []
    for a, b in lines:
        name = name_segment(points[a], points[b], used)
        spec["lines"][name] = [f"p{a}", f"p{b}"]
        horizontal, vertical, length = _angle_off_axis(points[a], points[b])
        if horizontal <= AXIS_TOLERANCE_DEG:
            spec["constraints"].append({"type": "horizontal", "line": name})
        elif vertical <= AXIS_TOLERANCE_DEG:
            spec["constraints"].append({"type": "vertical", "line": name})
        order.append((name, f"p{a}", f"p{b}", length))

    for k, circle in enumerate(circles):
        name = "hole" if k == 0 else f"hole{k + 1}"
        centre = f"p{circle['centre']}"
        spec["circles"][name] = {"centre": centre,
                                 "radius": round(float(circle["radius"]), 6)}
        # a circle's centre is a position, not a shape: it is pinned where it
        # was drawn, and the radius is what becomes a dimension
        if centre != "p0":
            spec["constraints"].append({"type": "fix", "point": centre,
                                        "at": spec["points"][centre]})
    pin = set()
    for k, arc in enumerate(arcs or ()):
        spec.setdefault("arcs", {})["arc%d" % (k + 1)] = {
            "centre": "p%d" % arc["centre"], "from": "p%d" % arc["from"],
            "to": "p%d" % arc["to"], "ccw": bool(arc.get("ccw", True))}
        pin |= {arc["centre"], arc["from"], arc["to"]}
    for k, e in enumerate(ellipses or ()):
        spec.setdefault("ellipses", {})["oval%d" % (k + 1)] = {
            "centre": "p%d" % e["centre"], "major": round(float(e["major"]), 6),
            "minor": round(float(e["minor"]), 6)}
        pin |= {e["centre"]}
    for k, sl in enumerate(slots or ()):
        spec.setdefault("slots", {})["slot%d" % (k + 1)] = {
            "from": "p%d" % sl["from"], "to": "p%d" % sl["to"],
            "width": round(float(sl["width"]), 6)}
        pin |= {sl["from"], sl["to"]}
    for idx in sorted(pin):
        if idx == 0:
            continue
        spec["constraints"].append({"type": "fix", "point": "p%d" % idx,
                                    "at": spec["points"]["p%d" % idx]})
    if points:
        spec["constraints"].insert(0, {"type": "fix", "point": "p0",
                                       "at": spec["points"]["p0"]})
    return spec, order


def dimension(spec: dict, order: list, prefix: str, solve, evaluate,
              precision: float = 0.5) -> tuple:
    """Add named dimensions until the sketch cannot move, and no further.

    Each added dimension is checked by re-solving: the loop stops the moment the
    degrees of freedom reach zero, which is what keeps a rectangle at two
    dimensions instead of four that fight each other.
    """
    parameters: dict = {}
    spec = dict(spec)
    spec["constraints"] = list(spec["constraints"])
    spec["circles"] = {k: dict(v) for k, v in (spec.get("circles") or {}).items()}

    def dof_of(candidate: dict) -> int:
        try:
            return solve(candidate, lambda v: evaluate(v, parameters)).dof
        except CadError:
            return -1                      # unsolvable: treat as "do not add"

    dof = dof_of(spec)
    for name, a, b, length in sorted(order, key=lambda t: -t[3]):
        if dof <= 0:
            break
        parameter = f"{prefix}_{name}"
        rounded = max(precision, round(length / precision) * precision)
        trial = dict(spec, constraints=spec["constraints"] +
                     [{"type": "distance", "points": [a, b], "value": parameter}])
        parameters[parameter] = rounded
        after = dof_of(trial)
        if after < 0 or after >= dof:      # redundant or conflicting: not this one
            parameters.pop(parameter)
            continue
        spec, dof = trial, after

    for name, circle in (spec.get("circles") or {}).items():
        parameter = f"{prefix}_{name}_r"
        parameters[parameter] = float(circle["radius"])
        circle["radius"] = parameter

    spec.pop("allow_underconstrained", None)
    return spec, parameters, dof
