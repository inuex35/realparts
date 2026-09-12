"""Features that make a solid out of nothing, or out of a sketch."""
from __future__ import annotations

import math

from ...geometry import kernel
from ...errors import CadError
from ...geometry.core.naming import Body
from .extents import extent
from ..declare.registry import feature
from ..declare.schema import Angle, Direction
from ..declare.schema import (AXIS, END_CONDITION, Flag, Name, Number, Point, Ratio, Ref, Refs, Spec,
                              Text)



@feature("box", args={
    "size": Point(note="the three sides, or two for a square prism"),
    "at": Point(required=False, default=[0, 0, 0]),
    "centred": Flag(True)})
def box(graph, f, a, ev) -> Body:
    """A rectangular block, centred or from a corner."""
    return kernel.box(f.id, ev(a["size"]), ev(a.get("at", [0, 0, 0])),
                      a.get("centred", True))


@feature("cylinder", args={
    "radius": Number(), "height": Number(),
    "at": Point(required=False, default=[0, 0, 0]),
    "axis": Direction(required=False, default=[0, 0, 1]),
    "centred": Flag(True)})
def cylinder(graph, f, a, ev) -> Body:
    """A cylinder about an axis, centred or from its base."""
    return kernel.cylinder(f.id, ev(a["radius"]), ev(a["height"]),
                           ev(a.get("at", [0, 0, 0])), ev(a.get("axis", [0, 0, 1])),
                           a.get("centred", True))


@feature("extrude", args={
    "sketch": Ref(), "distance": Number(required=False),
    "depth": Number(required=False), "until": END_CONDITION,
    "symmetric": Flag(False)})
def extrude(graph, f, a, ev) -> Body:
    """Drag a sketch along its own normal into a solid."""
    sketch = graph.sketch_of(a["sketch"])
    distance, sketch = extent(graph, f, a, ev, sketch, into=False)
    return kernel.extrude(f.id, sketch, distance)


@feature("revolve", args={
    "sketch": Ref(), "angle": Angle(required=False, default=360), "axis": AXIS})
def revolve(graph, f, a, ev) -> Body:
    """Revolve a sketch about an axis into a solid."""
    spec = a.get("axis")
    if spec is None:
        return kernel.revolve(f.id, graph.sketch_of(a["sketch"]), ev(a.get("angle", 360)))
    frame = graph.axis_frame(spec)
    return kernel.revolve(f.id, graph.sketch_of(a["sketch"]), ev(a.get("angle", 360)),
                          ev(frame["origin"]), ev(frame["direction"]))


@feature("loft", args={"sketches": Refs(), "ruled": Flag(False)})
def loft(graph, f, a, ev) -> Body:
    """A solid through a series of sections, in the order given."""
    return kernel.loft(f.id, [graph.sketch_of(s) for s in a["sketches"]],
                       bool(a.get("ruled", False)))


HELIX = Spec({"radius": Number(), "pitch": Number(note="rise per turn"), "height": Number(),
              "at": Point(required=False, default=[0, 0, 0], note="the axis's foot"),
              "axis": Direction(required=False, default=[0, 0, 1]),
              "left": Flag(note="left-handed")},
             note="a helical path instead of a sketch; the profile sits at its start, "
                  "radius along the axis frame's x from `at`")


@feature("sweep", args={
    "profile": Ref(), "path": Ref(required=False), "helix": HELIX,
    "corner": Text(choices=("sharp", "round"), default="sharp")})
def sweep(graph, f, a, ev) -> Body:
    """Drag a profile along a path sketch, or along a helix (a spring, a coil)."""
    helix = a.get("helix")
    if helix:
        helix = {"radius": ev(helix["radius"]), "pitch": ev(helix["pitch"]),
                 "height": ev(helix["height"]), "origin": ev(helix.get("at", [0, 0, 0])),
                 "axis": ev(helix.get("axis", [0, 0, 1])), "left": bool(helix.get("left"))}
    elif not a.get("path"):
        raise CadError("bad_arguments", "a sweep needs a path sketch or a helix", {"feature": f.id})
    return kernel.sweep(f.id, graph.sketch_of(a["profile"]),
                        graph.sketch_of(a["path"]) if a.get("path") else None,
                        a.get("corner", "sharp"), helix)


@feature("sheet", category="solid", args={
    "sketch": Ref(), "thickness": Number(note="the stock it is cut from")})
def sheet(graph, f, a, ev) -> Body:
    """A flat sheet metal blank: a sketch, given the stock's thickness."""
    return kernel.sheet(f.id, graph.sketch_of(a["sketch"]), ev(a["thickness"]))


@feature("flange", category="modify", stands_for="body",
         args={
    "body": Ref(), "edge": Name(note="an edge of the sheet to bend at"),
    "length": Number(), "angle": Angle(required=False, default=90),
    "radius": Number(required=False, note="inside radius; the thickness by default"),
    "thickness": Number(required=False, note="measured off the sheet by default"),
    "k": Ratio(required=False,
               note="the neutral axis factor for this material and tooling; "
                    "0.44 by default")})
def flange(graph, f, a, ev) -> Body:
    """Bend a flap up from an edge of a sheet metal part."""
    return kernel.flange(f.id, graph.body_of(a["body"]), a["edge"],
                         ev(a["length"]), ev(a.get("angle", 90)),
                         ev(a["radius"]) if a.get("radius") else None,
                         ev(a["thickness"]) if a.get("thickness") else None,
                         ev(a["k"]) if a.get("k") is not None else None)


@feature("rib", stands_for="body",
         args={
    "body": Ref(), "sketch": Ref(), "thickness": Number(),
    "depth": Number(required=False), "until": END_CONDITION,
    "direction": Direction(required=False, default=[0, -1])})
def rib(graph, f, a, ev) -> Body:
    """A thin web dropped from a sketch onto the body it stiffens."""
    target = graph.body_of(a["body"])
    sketch = graph.sketch_of(a["sketch"])
    direction = ev(a.get("direction", [0, -1]))
    if a.get("until"):
        # the rib runs in its sketch's plane, so that is the way to measure to the stop
        x, y = sketch.plane.x_axis, sketch.plane.y_axis()
        size = math.hypot(float(direction[0]), float(direction[1])) or 1.0
        way = [(x[i] * float(direction[0]) + y[i] * float(direction[1])) / size for i in range(3)]
        distance, sketch = extent(graph, f, dict(a, until=a["until"]), ev, sketch,
                                        into=False, body=target, direction=way)
    else:
        if a.get("depth") is None:
            raise CadError("missing_argument",
                           f"{f.id}: a rib needs a depth or an until",
                           {"feature": f.id})
        distance = ev(a["depth"])
    return kernel.fuse(f.id, target,
                       kernel.rib(f.id, sketch, ev(a["thickness"]), abs(distance),
                                  direction))
