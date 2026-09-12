"""Which holds fit what is picked on a sketch, and what each one is.

Free of Blender so it can be tested headlessly. A hold is only offered when
it fits: a point cannot be parallel, and a menu of ten buttons that would
each refuse is worse than four that will not.
"""
from __future__ import annotations

import math


#: each hold, as the button that adds it: what it does, and what it needs
HOLDS = {
    "horizontal": ("Keep It Level", "one line"),
    "vertical": ("Keep It Upright", "one line"),
    "parallel": ("Keep Them Parallel", "two lines"),
    "perpendicular": ("Keep Them Square", "two lines"),
    "equal_length": ("Keep Them the Same Length", "two lines"),
    "tangent": ("Keep Them Touching", "two lines"),
    "angle": ("Hold This Angle", "two lines"),
    "fix": ("Pin It Where It Is", "one point"),
    "coincident": ("Put Them Together", "two points"),
    "distance": ("Hold This Distance", "two points"),
}

#: which holds fit which picks, and the order they are offered in
FITS = {
    (1, 0): ["horizontal", "vertical"],
    (2, 0): ["parallel", "perpendicular", "equal_length", "tangent", "angle"],
    (0, 1): ["fix"],
    (0, 2): ["coincident", "distance"],
}


def _sorted(picks) -> tuple:
    lines = [name for what, name in picks if what == "line"]
    points = [name for what, name in picks if what == "point"]
    return lines, points


def fitting(picks) -> list:
    """The holds that fit these picks -- nothing else is worth offering."""
    lines, points = _sorted(picks)
    return FITS.get((len(lines), len(points)), [])


def what_is_picked(picks) -> str:
    """The line at the top of the menu, naming what the holds would hold."""
    lines, points = _sorted(picks)
    parts = []
    if points:
        parts.append("this point" if len(points) == 1 else "these %d points" % len(points))
    if lines:
        parts.append("this line" if len(lines) == 1 else "these %d lines" % len(lines))
    return (" and ".join(parts) or "nothing on the sketch").capitalize()


def what_it_needs(picks) -> str:
    """Why nothing is offered: what to pick instead."""
    lines, points = _sorted(picks)
    if lines and points:
        return "pick points, or lines, not both"
    return "shift-click to pick one or two points, or one or two lines"


def _segment(geometry: dict, name: str):
    return next((s for s in geometry["segments"] if s["name"] == name), None)


def _heading(geometry: dict, name: str) -> float:
    """Which way a line points, in radians on the sketch plane."""
    segment = _segment(geometry, name)
    (x0, y0), (x1, y1) = segment["start"], segment["end"]
    return math.atan2(y1 - y0, x1 - x0)


def build(kind: str, picks, geometry: dict):
    """The constraint to add, from what is picked. None if it does not fit.

    A dimension is built at the size the sketch already is, so adding one
    holds the shape rather than moving it; the number is retyped afterwards.
    """
    lines, points = _sorted(picks)
    if kind in ("horizontal", "vertical") and len(lines) == 1:
        return {"type": kind, "line": lines[0]}
    if kind in ("parallel", "perpendicular", "equal_length") and len(lines) == 2:
        return {"type": kind, "lines": lines}
    if kind == "tangent" and len(lines) == 2:
        return {"type": kind, "of": lines}
    if kind == "angle" and len(lines) == 2:
        # measured from the first line to the second, as the solver reads it
        turn = math.degrees(_heading(geometry, lines[1]) - _heading(geometry, lines[0]))
        return {"type": kind, "lines": lines,
                "value": round((turn + 180.0) % 360.0 - 180.0, 3)}
    if kind == "fix" and len(points) == 1:
        return {"type": kind, "point": points[0],
                "at": list(geometry["points"][points[0]])}
    if kind == "coincident" and len(points) == 2:
        return {"type": kind, "points": points}
    if kind == "distance" and len(points) == 2:
        a, b = (geometry["points"][name] for name in points)
        return {"type": kind, "points": points, "value": round(math.dist(a, b), 4)}
    return None
