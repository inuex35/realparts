"""Copies: moved, mirrored, repeated.

``repeat`` lives here rather than on the evaluator because a pattern feature
and a feature with its own ``pattern`` spec are the same operation.
"""
from __future__ import annotations

from ...geometry import kernel
from ...geometry.core.naming import Body
from ..declare.registry import feature
from ..declare.schema import Angle, Count, Direction
from ..declare.schema import AXIS, Flag, Number, OneOf, Point, Ref, Spec
from .extents import along_path


def repeat(graph, f, tool: Body, spec, ev, merge: bool = True) -> Body:
    """Repeat a body per a pattern spec, for a pattern feature or a feature's own ``pattern``."""
    if not spec:
        return tool
    # a self-pattern declares `merge` inside its spec, and that value wins
    merge = bool(spec.get("merge", merge))
    if spec.get("path"):
        return kernel.path_pattern(f.id, tool,
                                   along_path(graph.sketch_of(spec["path"]),
                                              int(ev(spec["count"]))),
                                   bool(merge))
    if "axis" in spec:
        frame = graph.axis_frame(spec["axis"])
        return kernel.circular_pattern(
            f.id, tool, ev(frame["origin"]), ev(frame["direction"]),
            ev(spec["count"]),
            ev(spec["angle"]) if "angle" in spec else None, bool(merge))
    return kernel.pattern(f.id, tool, ev(spec["direction"]), ev(spec["spacing"]),
                          ev(spec["count"]), bool(merge))


@feature("translate", category="pattern", stands_for="body",
         args={
    "body": Ref(), "offset": Point()})
def translate(graph, f, a, ev) -> Body:
    """Move a body by an offset vector."""
    return kernel.translate(f.id, graph.body_of(a["body"]), ev(a["offset"]))


@feature("mirror", category="pattern", stands_for="body", args={
    "body": Ref(),
    "plane": OneOf(Direction(required=False),
                   Spec({"origin": Point(required=False),
                         "normal": Direction(required=False)}),
                   note="the mirror plane: its normal, or {origin, normal}"),
    "merge": Flag(True)})
def mirror(graph, f, a, ev) -> Body:
    """Mirror a body about a plane, keeping the names on the copy."""
    plane = a.get("plane")
    if isinstance(plane, (list, tuple)):        # a bare normal, through the origin
        origin, normal = [0, 0, 0], plane
    else:
        plane = plane or {}
        origin = plane.get("origin", [0, 0, 0])
        normal = plane.get("normal", [1, 0, 0])
    return kernel.mirror(f.id, graph.body_of(a["body"]),
                         ev(origin), ev(normal), bool(a.get("merge", True)))


@feature("pattern", category="pattern", stands_for="body",
         args={
    "body": Ref(), "count": Count(), "spacing": Number(required=False),
    "direction": Direction(required=False), "axis": AXIS,
    "angle": Angle(required=False), "path": Ref(required=False),
    "merge": Flag(True)})
def pattern(graph, f, a, ev) -> Body:
    """Repeat a body: along a direction, around an axis, or along a path."""
    return repeat(graph, f, graph.body_of(a["body"]), a, ev, merge=a.get("merge", True))
