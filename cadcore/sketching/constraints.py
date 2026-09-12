"""The constraint types, one function each, in a registry.

Adding one is a function: ``@constraint("horizontal")`` on
``def horizontal(gcs, con, refs, value)``. ``refs`` finds the names the
constraint uses in what the solver has built, and refuses a missing one by name.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from ..errors import CadError

_KINDS: dict[str, Callable] = {}


def constraint(*names: str):
    """Register a handler for one or more constraint types."""
    def register(fn):
        for name in names:
            if name in _KINDS:
                raise RuntimeError(f"constraint {name!r} is registered twice")
            _KINDS[name] = fn
        return fn
    return register


def kinds() -> list:
    return sorted(_KINDS)


#: what a constraint's numbers count, so that a document written in inches can
#: be converted. `distance` carries a length and `angle` carries degrees, and a
#: converter that cannot tell them apart either leaves the sketch 25.4 times
#: too small or turns 30 degrees into 762 of them. Declared here, next to the
#: handlers, because this is the only place that knows what a constraint reads.
#:
#: A kind that is not here is a kind nothing scales, which is the safe answer
#: for the ones that carry no number at all -- `horizontal`, `tangent`. The
#: test beside this asserts every registered kind has been thought about.
MEASURES: dict[str, str] = {
    "horizontal": "none", "vertical": "none", "parallel": "none",
    "perpendicular": "none", "coincident": "none", "point_on_line": "none",
    "point_on_curve": "none", "equal_length": "none", "equal_radius": "none",
    "tangent": "none", "midpoint": "none", "symmetric": "none",
    "on_perpendicular_bisector": "none",
    # `at` is a place in the sketch, so it is two lengths and not a `value`
    "fix": "place",
    "distance": "length", "distance_to_line": "length",
    "radius": "length", "diameter": "length",
    "angle": "angle",
}


def measures(kind: str) -> str:
    """`none`, `place`, `length` or `angle` -- what this constraint's numbers are."""
    return MEASURES.get(kind, "none")


@dataclass
class References:
    """What a constraint's names point at, resolved or refused by name."""

    points: dict
    lines: dict
    arcs: dict
    circles: dict
    con: dict
    index: int

    def line(self, key: str = "line"):
        name = self.con[key]
        if name not in self.lines:
            raise CadError("unknown_line",
                           f"constraint {self.index} refers to line {name!r}",
                           {"known": sorted(self.lines)})
        return self.lines[name]

    def named_line(self, name: str):
        """A line by name, refused by name -- the same as :meth:`line`."""
        if name not in self.lines:
            raise CadError("unknown_line",
                           f"constraint {self.index} refers to line {name!r}",
                           {"known": sorted(self.lines)})
        return self.lines[name]

    def point(self, name: str):
        if name not in self.points:
            raise CadError("unknown_point",
                           f"constraint {self.index} refers to point {name!r}",
                           {"known": sorted(self.points)})
        return self.points[name]

    def curve(self, key: str = "arc"):
        """A curve by name, as ``(kind, reference)`` -- arcs and circles differ."""
        name = self.con.get(key) or self.con.get("circle") or self.con.get("arc")
        if name in self.arcs:
            return "arc", self.arcs[name]
        if name in self.circles:
            return "circle", self.circles[name]
        raise CadError("unknown_curve",
                       f"constraint {self.index} refers to curve {name!r}",
                       {"known": sorted(list(self.arcs) + list(self.circles))})

    def named_curve(self, name: str):
        if name in self.arcs:
            return "arc", self.arcs[name]
        if name in self.circles:
            return "circle", self.circles[name]
        raise CadError("unknown_curve",
                       f"constraint {self.index} refers to curve {name!r}",
                       {"known": sorted(list(self.arcs) + list(self.circles))})


def apply(gcs, con: dict, points: dict, lines: dict, arcs: dict, circles: dict,
          evaluate, index: int):
    """Add one constraint to the solver, or refuse it by name."""
    kind = con.get("type")
    handler = _KINDS.get(kind)
    if handler is None:
        raise CadError("unknown_constraint",
                       f"constraint type {kind!r} is not implemented",
                       {"index": index, "supported": kinds()})
    return handler(gcs, con, References(points, lines, arcs, circles, con, index),
                   evaluate)


# --- direction -----------------------------------------------------------------

@constraint("horizontal")
def horizontal(gcs, con, refs, value):
    """This line runs along the sketch's x axis."""
    return gcs.horizontal(refs.line())


@constraint("vertical")
def vertical(gcs, con, refs, value):
    """This line runs along the sketch's y axis."""
    return gcs.vertical(refs.line())


@constraint("parallel")
def parallel(gcs, con, refs, value):
    """Two lines stay parallel, whatever else moves."""
    return gcs.parallel(refs.named_line(con["lines"][0]),
                        refs.named_line(con["lines"][1]))


@constraint("perpendicular")
def perpendicular(gcs, con, refs, value):
    """Two lines stay square to each other."""
    return gcs.perpendicular(refs.named_line(con["lines"][0]),
                             refs.named_line(con["lines"][1]))


# --- coincidence ---------------------------------------------------------------

@constraint("coincident")
def coincident(gcs, con, refs, value):
    """Two points are the same point."""
    return gcs.coincident(refs.point(con["points"][0]), refs.point(con["points"][1]))


@constraint("point_on_line")
def point_on_line(gcs, con, refs, value):
    """A point rides on a line, free to slide along it."""
    return gcs.point_on_line(refs.point(con["point"]), refs.line())


@constraint("point_on_curve")
def point_on_curve(gcs, con, refs, value):
    """A point rides on an arc or a circle."""
    what, ref = refs.curve()
    return (gcs.point_on_arc if what == "arc" else gcs.point_on_circle)(
        refs.point(con["point"]), ref)


@constraint("fix")
def fix(gcs, con, refs, value):
    """A point is pinned where it is -- the anchor a sketch is measured from."""
    u, v = value(con.get("at", [0, 0]))
    return gcs.fix_point(refs.point(con["point"]), float(u), float(v))


# --- dimensions ----------------------------------------------------------------

@constraint("distance")
def distance(gcs, con, refs, value):
    """A dimension between two points."""
    size = float(value(con["value"]))
    return gcs.p2p_distance(refs.point(con["points"][0]), refs.point(con["points"][1]),
                            gcs.add_param(size, fixed=True))


@constraint("distance_to_line")
def distance_to_line(gcs, con, refs, value):
    """A dimension from a point to a line."""
    size = float(value(con["value"]))
    return gcs.p2l_distance(refs.point(con["point"]), refs.line(),
                            gcs.add_param(size, fixed=True))


@constraint("angle")
def angle(gcs, con, refs, value):
    """The angle between two lines, or the direction of a line, in degrees.

    Degrees, because that is what every other angle in a document is written
    in -- a revolve's 360, a flange's 90 -- and a sketch that measured its one
    angle in radians would be a trap laid for the person editing it.

    The number is signed and it is read *from the first line to the second*,
    where a line's direction is the order its two endpoints were written in.
    So 30 between `a` and `b` is not the shape 30 between `b` and `a` is, and
    redrawing a segment end-for-end turns an angle into its supplement. That
    is PlaneGCS's rule rather than a choice made here, but a solve that lands
    on the supplement looks like a solver bug unless the rule is written down.
    """
    size = math.radians(float(value(con["value"])))
    if con.get("points"):
        first, second = con["points"]
        return gcs.p2p_angle(refs.point(first), refs.point(second),
                             gcs.add_param(size, fixed=True))
    lines = con.get("lines") or []
    if len(lines) != 2:
        raise CadError("bad_constraint",
                       f"constraint {refs.index} needs two lines, or the two "
                       f"points of the line whose direction is being set",
                       {"got": {k: v for k, v in con.items() if k != "type"}})
    return gcs.l2l_angle(refs.named_line(lines[0]), refs.named_line(lines[1]),
                         gcs.add_param(size, fixed=True))


@constraint("radius", "diameter")
def radius(gcs, con, refs, value):
    """A radius, or the same thing said as a diameter."""
    size = float(value(con["value"]))
    if con.get("type") == "diameter":
        size /= 2.0
    what, ref = refs.curve()
    param = gcs.add_param(size, fixed=True)
    return (gcs.arc_radius if what == "arc" else gcs.circle_radius)(ref, param)


# --- equality and symmetry -----------------------------------------------------

@constraint("equal_length")
def equal_length(gcs, con, refs, value):
    """Two lines are the same length, without saying what that length is."""
    return gcs.equal_length(refs.named_line(con["lines"][0]),
                            refs.named_line(con["lines"][1]))


@constraint("equal_radius")
def equal_radius(gcs, con, refs, value):
    """Two curves share a radius -- the constraint a row of holes is made of."""
    resolved = [refs.named_curve(name) for name in con["curves"]]
    kinds_seen = [kind for kind, _ in resolved]
    handles = [ref for _, ref in resolved]
    if kinds_seen == ["arc", "arc"]:
        return gcs.equal_radius_aa(*handles)
    if kinds_seen == ["circle", "circle"]:
        return gcs.equal_radius_cc(*handles)
    circle, arc = ((handles[0], handles[1]) if kinds_seen[0] == "circle"
                   else (handles[1], handles[0]))
    return gcs.equal_radius_ca(circle, arc)


@constraint("tangent")
def tangent(gcs, con, refs, value):
    """A line meets a curve without a corner."""
    if "of" in con and "line" not in con:
        # the viewport spells it as a pair of picks: sort out which is the
        # line and which is the curve, and say so when the pair has no curve
        given = list(con.get("of") or [])
        line = next((n for n in given if n in refs.lines), None)
        curve = next((n for n in given
                      if n in refs.arcs or n in refs.circles), None)
        if line is None or curve is None:
            raise CadError("bad_arguments",
                           f"constraint {refs.index}: tangent needs a line "
                           "and an arc or a circle",
                           {"given": given})
        what, ref = refs.named_curve(curve)
        return (gcs.tangent_line_arc if what == "arc"
                else gcs.tangent_line_circle)(refs.named_line(line), ref)
    what, ref = refs.curve()
    return (gcs.tangent_line_arc if what == "arc" else gcs.tangent_line_circle)(
        refs.line(), ref)


@constraint("midpoint")
def midpoint(gcs, con, refs, value):
    """The middle of line ``of`` lies on this line -- what a symmetric profile is made of."""
    return gcs.midpoint_on_line(refs.line("of"), refs.line())


@constraint("symmetric")
def symmetric(gcs, con, refs, value):
    """Two points are mirror images, about a line or about a point."""
    points = [refs.point(p) for p in con["points"]]
    if "line" in con:
        return gcs.symmetric_line(points[0], points[1], refs.line())
    return gcs.symmetric_point(points[0], points[1], refs.point(con["about"]))


@constraint("on_perpendicular_bisector")
def on_perpendicular_bisector(gcs, con, refs, value):
    """A point sits on the perpendicular bisector of a line."""
    return gcs.point_on_perp_bisector(refs.point(con["point"]), refs.line())
