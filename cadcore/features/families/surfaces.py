"""Features that make, edit, or close a surface; thicken, cap and sew return one to a solid."""
from __future__ import annotations

from ...geometry import kernel
from ...errors import CadError
from ...geometry.core.naming import Body
from ...geometry.core.query import select_edges
from ..declare.registry import feature, handler
from ..declare.schema import Angle, Count
from ..declare.schema import (AXIS, Flag, ListOf, Name, Names, Number, Point, Query, Ref,
                              Refs, Text)


@feature("surface", category="surface", produces="surface", args={
    "method": Text(choices=("planar", "skin", "loft", "extrude", "revolve", "sweep",
                            "points", "boundary"),
                   default="planar"),
    "sketch": Ref(required=False), "sketches": Refs(required=False),
    "profile": Ref(required=False), "path": Ref(required=False),
    "distance": Number(required=False), "axis": AXIS,
    "angle": Angle(required=False, default=360), "ruled": Flag(False),
    "corner": Text(choices=("sharp", "round"), default="sharp"),
    "grid": ListOf(ListOf(Point()), note="points: rows of points the surface passes through"),
    "style": Text(choices=("coons", "curved", "stretch"), default="coons",
                  note="boundary: how the patch is blended between its curves")})
def surface(graph, f, a, ev) -> Body:
    """Make a surface from sketches: planar, skinned, extruded, revolved, swept, through a grid of points, or between boundary curves."""
    method = a.get("method", "planar")
    if method == "planar":
        return kernel.planar(f.id, graph.sketch_of(a["sketch"]))
    if method == "points":
        return kernel.from_points(f.id, [[ev(p) for p in row] for row in (a.get("grid") or [])])
    if method == "boundary":
        return kernel.from_boundary(f.id, [graph.sketch_of(s) for s in a.get("sketches") or []],
                                    a.get("style", "coons"))
    if method in ("skin", "loft"):
        return kernel.skin(f.id, [graph.sketch_of(s) for s in a["sketches"]],
                           bool(a.get("ruled", False)))
    if method == "extrude":
        return kernel.extruded_surface(f.id, graph.sketch_of(a["sketch"]),
                                       ev(a["distance"]))
    if method == "revolve":
        frame = graph.axis_frame(a.get("axis") or {"direction": [0, 0, 1]})
        return kernel.revolved_surface(f.id, graph.sketch_of(a["sketch"]),
                                       ev(frame["origin"]), ev(frame["direction"]),
                                       ev(a.get("angle", 360)))
    if method == "sweep":
        return kernel.swept(f.id, graph.sketch_of(a["profile"]), graph.sketch_of(a["path"]),
                            a.get("corner", "sharp"))
    raise CadError("bad_parameter", f"no surface method called {method!r}",
                   {"known": list(handler("surface").args["method"].choices)})


@feature("fill", category="surface", produces="surface", args={
    "body": Ref(), "edges": Query(required=False),
    "loop": Count(required=False, default=0),
    "continuity": Text(choices=("G0", "G1", "G2"), default="G1"),
    "through": ListOf(Point(), note="points the patch must pass through")})
def fill(graph, f, a, ev) -> Body:
    """Patch a boundary with a surface that meets its neighbours smoothly."""
    body = graph.body_of(a["body"])
    edges = a.get("edges")
    if not edges:
        loops = kernel.boundary_loops(body)
        if not loops:
            raise CadError("nothing_to_fill", f"{a['body']!r} has no open boundary",
                           {"hint": "name the edges to patch, or use a shape "
                                    "that is actually open"})
        edges = loops[int(ev(a.get("loop", 0)))]
    elif not isinstance(edges, list):
        edges = select_edges(body, edges)
    return kernel.fill(f.id, body, edges, a.get("continuity", "G1"),
                       [ev(p) for p in a.get("through") or []])


@feature("sew", category="surface", args={
    "bodies": Refs(), "tolerance": Number(required=False, default=1e-6),
    "solid": Flag(True, note="make a solid when the result closes")})
def sew(graph, f, a, ev) -> Body:
    """Stitch faces and shells together, into a solid if they close."""
    return kernel.sew(f.id, [graph.body_of(b) for b in a["bodies"]],
                      ev(a.get("tolerance", 1e-6)), bool(a.get("solid", True)))


@feature("thicken", category="surface", stands_for="body",
         args={
    "body": Ref(), "thickness": Number()})
def thicken(graph, f, a, ev) -> Body:
    """Give a surface a wall thickness, which makes it a solid."""
    return kernel.thicken(f.id, graph.body_of(a["body"]), ev(a["thickness"]))


@feature("cap", category="surface", stands_for="body",
         args={
    "body": Ref(), "continuity": Text(choices=("G0", "G1", "G2"), default="G0")})
def cap(graph, f, a, ev) -> Body:
    """Patch every opening of a surface body and sew it into a solid."""
    return kernel.cap(f.id, graph.body_of(a["body"]), a.get("continuity", "G0"))


@feature("offset_surface", category="surface", produces="surface",
         stands_for="body", args={"body": Ref(), "distance": Number()})
def offset_surface(graph, f, a, ev) -> Body:
    """Move a surface along its own normal."""
    return kernel.offset_surface(f.id, graph.body_of(a["body"]), ev(a["distance"]))


@feature("trim", category="surface", produces="surface", stands_for="body",
         args={
    "body": Ref(), "tool": Ref(),
    "keep": Text(choices=("inside", "outside", "both"), default="outside")})
def trim(graph, f, a, ev) -> Body:
    """Cut a surface against another shape and keep one side."""
    return kernel.trim(f.id, graph.body_of(a["body"]), graph.body_of(a["tool"]),
                       a.get("keep", "outside"))


@feature("extend", category="surface", produces="surface", stands_for="body",
         args={
    "body": Ref(), "face": Name(), "distance": Number()})
def extend(graph, f, a, ev) -> Body:
    """Lengthen a face past its boundary."""
    return kernel.extend(f.id, graph.body_of(a["body"]), a["face"], ev(a["distance"]))


@feature("move_face", category="modify", stands_for="body",
         args={
    "body": Ref(), "face": Name(note="the planar face to push or pull"),
    "distance": Number(note="along the face's own normal; negative pushes in")})
def move_face(graph, f, a, ev) -> Body:
    """Push or pull one face of a solid along its own normal."""
    return kernel.move_face(f.id, graph.body_of(a["body"]), a["face"],
                            ev(a["distance"]))


@feature("delete_face", category="surface", stands_for="body",
         args={
    "body": Ref(), "faces": Names(required=False), "face": Name(required=False),
    "heal": Flag(True, note="let the neighbours close the gap, or leave it open")})
def delete_face(graph, f, a, ev) -> Body:
    """Remove faces from a body, healing the gap or leaving it open."""
    faces = a.get("faces") or ([a["face"]] if "face" in a else [])
    return kernel.delete_face(f.id, graph.body_of(a["body"]), faces,
                              bool(a.get("heal", True)))
