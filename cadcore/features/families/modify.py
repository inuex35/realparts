"""Features that change a body that already exists."""
from __future__ import annotations

from ...geometry import kernel
from ...errors import CadError
from ...geometry.core.naming import Body
from ...geometry.core.query import select_edges
from ..declare.registry import feature
from ..declare.schema import Angle, Direction
from ..declare.schema import (  MapOf, Name, Names, Number, OneOf, Point, Query,
                              Ref, Spec)


@feature("fillet", "chamfer", category="modify", stands_for="body",
         args={
    "body": Ref(), "edges": Query(),
    "radius": OneOf(Number(), Point(required=False), required=False,
                    note="one radius, or [start, end] for a variable fillet"),
    "distance": Number(required=False, note="a chamfer says distance")})
def fillet(graph, f, a, ev) -> Body:
    """Round or chamfer the named edges."""
    body = graph.body_of(a["body"])
    names = a["edges"] if isinstance(a["edges"], list) else select_edges(body, a["edges"])
    size = a.get("radius") if f.type == "fillet" \
        else a.get("distance", a.get("radius"))
    if size is None:
        raise CadError("missing_argument",
                       f"{f.id}: a {f.type} needs a "
                       + ("radius" if f.type == "fillet" else "distance"),
                       {"feature": f.id})
    if f.type == "fillet":
        return kernel.fillet(f.id, body, names, ev(size))
    return kernel.chamfer(f.id, body, names, ev(size))


@feature("shell", category="modify", stands_for="body",
         args={
    "body": Ref(),
    "thickness": OneOf(Number(),
                       Spec({"default": Number(),
                             "faces": MapOf(Number(),
                                            note="a heavier wall, by face name")}),
                       required=True,
                       note="one wall, or a default with heavier faces by name"),
    "open": Names(required=False),
    "open_faces": Names(required=False, note="the same as open; the older spelling")})
def shell(graph, f, a, ev) -> Body:
    """Hollow the body, opening it at the named faces."""
    thickness = a["thickness"]
    if isinstance(thickness, dict):
        thickness = {"default": ev(thickness.get("default", 0)),
                     "faces": {k: ev(v) for k, v in (thickness.get("faces") or {}).items()}}
    else:
        thickness = ev(thickness)
    return kernel.shell(f.id, graph.body_of(a["body"]),
                        a.get("open") or a.get("open_faces") or [], thickness)


@feature("draft", category="modify", stands_for="body",
         args={
    "body": Ref(), "faces": Names(), "angle": Angle(),
    "neutral": Name(required=False), "direction": Direction(required=False),
    "parting": Ref(required=False, note="a work plane; each side of it is drafted away from it"),
    "parting_face": Name(required=False, note="a flat face of the body the parting plane lies in"),
    "parting_offset": Number(required=False, default=0.0,
                             note="how far off that face the parting plane is, along its normal")})
def draft(graph, f, a, ev) -> Body:
    """Taper the named faces about a neutral face, or away from a parting plane."""
    body = graph.body_of(a["body"])
    if a.get("parting_face"):
        frame = kernel.face_frame(body, a["parting_face"])
        offset = float(ev(a.get("parting_offset", 0.0)))
        return kernel.draft_parted(f.id, body, a["faces"], ev(a["angle"]),
                                   [frame["origin"][i] + frame["normal"][i] * offset for i in range(3)],
                                   frame["normal"])
    if a.get("parting"):
        frame = graph.plane_of(a["parting"])
        return kernel.draft_parted(f.id, body, a["faces"], ev(a["angle"]),
                                   frame["origin"], frame["normal"])
    return kernel.draft(f.id, graph.body_of(a["body"]), a["faces"], ev(a["angle"]),
                        ev(a.get("direction", [0, 0, 1])), a.get("neutral"))
