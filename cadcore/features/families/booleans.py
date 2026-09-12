"""Booleans, and the features that are booleans with a built-in tool.

A pocket is an extrusion cut away, a hole is a drill tool cut away, a boss is
the same prism fused on. Having them as their own types keeps the history
readable and lets a reply say ``pocket1/floor`` rather than ``cut3/face2``.
"""
from __future__ import annotations

from ...model import fasteners
from ...geometry import kernel
from ... import names
from ...errors import CadError
from ...geometry.core.naming import Body
from ..declare.registry import feature
from ..declare.schema import (END_CONDITION, REPEAT, SEAT, Direction, Flag, Name, Number, Point, Ref,
                              Text)
from .extents import extent
from .patterns import repeat


def standard_hole(args: dict) -> dict:
    """Fill in a hole's diameter and seat from its standard, when one is named."""
    if not args.get("standard"):
        return args
    spec = fasteners.resolve(args["standard"], args.get("fit", "normal"),
                             args.get("seat", "none"))
    filled = dict(args)
    filled.setdefault("diameter", spec["diameter"])
    for key in ("counterbore", "countersink"):
        if key in spec:
            filled.setdefault(key, spec[key])
    return filled


def resolved(spec, ev):
    return {k: ev(v) for k, v in spec.items()} if spec else None


@feature("cut", "fuse", "common", category="boolean", stands_for="target",
         args={
    "target": Ref(), "tool": Ref(),
    "fuzzy": Number(required=False, note="a gap to treat as touching; 0 by default"),
    "glue": Flag(note="the shapes only meet along whole faces: skip the intersection search")})
def cut(graph, f, a, ev) -> Body:
    """Cut, fuse or intersect two bodies, carrying both sets of names across."""
    return getattr(kernel, f.type)(f.id, graph.body_of(a["target"]), graph.body_of(a["tool"]),
                                   float(ev(a["fuzzy"])) if a.get("fuzzy") else 0.0,
                                   bool(a.get("glue")))


@feature("split", category="boolean", stands_for="body", args={
    "body": Ref(),
    "face": Name(required=False, note="a flat face of the body to cut parallel to"),
    "offset": Number(required=False, default=0.0,
                     note="how far off that face, along its normal; negative goes into the part"),
    "plane": Ref(required=False, note="a work plane to cut at"),
    "tool": Ref(required=False, note="a surface body to cut along, instead of a plane"),
    "origin": Point(required=False), "normal": Direction(required=False),
    "keep": Text(choices=("above", "below", "both"), default="above",
                 note="above is the side the normal points to; both keeps two solids")})
def split(graph, f, a, ev) -> Body:
    """Cut a body in two at a plane or a surface and keep one side, or both."""
    body = graph.body_of(a["body"])
    keep = a.get("keep", "above")
    if a.get("tool"):
        return kernel.split(f.id, body, graph.body_of(a["tool"]), keep)
    if a.get("face"):
        frame = kernel.face_frame(body, a["face"])
        offset = float(ev(a.get("offset", 0.0)))
        origin = [frame["origin"][i] + frame["normal"][i] * offset for i in range(3)]
        normal = frame["normal"]
    elif a.get("plane"):
        frame = graph.plane_of(a["plane"])
        origin, normal = frame["origin"], frame["normal"]
    elif a.get("normal"):
        origin, normal = ev(a.get("origin", [0, 0, 0])), ev(a["normal"])
    else:
        raise CadError("bad_arguments", "a split needs a face, a plane, a tool, or a normal",
                       {"feature": f.id})
    return kernel.split(f.id, body, kernel.plane_face(body, origin, normal), keep,
                        origin, normal)


@feature("pocket", "boss", category="boolean", stands_for="body",
         args={
    "body": Ref(), "sketch": Ref(), "depth": Number(required=False),
    "distance": Number(required=False), "until": END_CONDITION,
    "symmetric": Flag(False), "pattern": REPEAT})
def pocket(graph, f, a, ev) -> Body:
    """Extrude a sketch on a face into the body as a pocket, or out of it as a boss."""
    # the sketch normal points out of its face: a pocket goes against it, a boss with it
    pocket = f.type == "pocket"
    target = graph.body_of(a["body"])
    distance, sketch = extent(graph, f, a, ev, graph.sketch_of(a["sketch"]),
                                    into=pocket, body=target)
    tool = kernel.extrude(f.id, sketch, distance)
    if pocket:
        # rename the prism's caps to the pocket's floor and mouth
        rename = {names.face(f.id, "top"): names.face(f.id, "floor"),
                  names.face(f.id, "bottom"): names.face(f.id, "mouth")}
        tool.names = [(rename.get(n, n), face) for n, face in tool.names]
    tool = repeat(graph, f, tool, a.get("pattern"), ev)
    op = kernel.cut if pocket else kernel.fuse
    return op(f.id, target, tool)


@feature("hole", category="boolean", stands_for="body",
         args={
    "body": Ref(), "face": Name(), "at": Point(required=False, default=[0, 0]),
    "diameter": Number(required=False), "depth": Number(required=False),
    "standard": Text(note="M3 ... M12, instead of a diameter"),
    "fit": Text(choices=("tapped", "close", "normal", "loose")),
    "seat": Text(choices=("none", "counterbore", "countersink")),
    "counterbore": SEAT, "countersink": SEAT, "pattern": REPEAT,
    "threaded": Flag(False, note="cut the helix as well as noting the size"),
    "pitch": Number(required=False), "thread_length": Number(required=False),
    "hand": Text(choices=("right", "left"), default="right"),
    "clearance": Number(required=False, default=0.0)})
def hole(graph, f, a, ev) -> Body:
    """Drill a hole into a face, by diameter or by standard, optionally seated, patterned or threaded."""
    body = graph.body_of(a["body"])
    frame = kernel.face_frame(body, a["face"])
    a = standard_hole(a)                 # "M6 tapped" fills in the numbers
    if a.get("diameter") is None:
        raise CadError("missing_argument",
                       f"{f.id}: a hole needs a diameter or a standard",
                       {"feature": f.id})
    tool = kernel.hole_tool(f.id, frame, ev(a["diameter"]),
                            ev(a["depth"]) if a.get("depth") else None,
                            ev(a.get("at", [0, 0])),
                            resolved(a.get("counterbore"), ev),
                            resolved(a.get("countersink"), ev),
                            through_depth=kernel.bounding_span(body) * 1.5)
    try:
        out = kernel.cut(f.id, body, repeat(graph, f, tool, a.get("pattern"), ev))
    except CadError as nothing_removed:
        # the kernel refuses a cut that removes nothing and describes a tool
        # that missed; a hole usually misses because the material is already
        # gone there, so say that instead
        if nothing_removed.kind != "no_intersection":
            raise
        raise CadError(
            "no_intersection",
            f"{f.id}: this hole removes nothing",
            {"face": a["face"], "diameter": ev(a["diameter"]),
             "hint": "the material may already be gone there -- a hole drilled "
                     "inside a larger one, or on the flat of a counterbore that "
                     "already goes deeper"}) from nothing_removed
    if a.get("threaded"):
        # thread every bore the feature made, not only the first: a pattern
        # names its copies `bore~1`, `bore~2`. Cutting a thread consumes the
        # bore it was cut into, so the list is re-read each pass rather than
        # taken up front. The loop is bounded so a thread that stopped
        # consuming bore names fails instead of hanging.
        for _ in range(int(ev(a.get("pattern", {}).get("count", 1))) + 2):
            todo = [n for n in out.face_names() if names.is_role(n, f.id, "bore")]
            if not todo:
                break
            out = kernel.thread(f.id, out, todo[0], standard=a.get("standard"),
                                pitch=ev(a["pitch"]) if a.get("pitch") else None,
                                length=ev(a["thread_length"])
                                if a.get("thread_length") else None,
                                hand=a.get("hand", "right"),
                                clearance=ev(a.get("clearance", 0.0)))
        else:
            raise CadError("thread_failed",
                           "the bores would not stop coming back after threading",
                           {"feature": f.id, "left": todo[:4],
                            "hint": "a thread should consume the bore it cuts"})
    return out


@feature("thread", category="thread", stands_for="body",
         args={
    "body": Ref(), "face": Name(note="the cylindrical face to thread"),
    "standard": Text(), "pitch": Number(required=False),
    "length": Number(required=False), "start": Number(required=False, default=0.0),
    "hand": Text(choices=("right", "left"), default="right"),
    "clearance": Number(required=False, default=0.0,
                        note="shrink the thread all round, so a printed pair fits")})
def thread(graph, f, a, ev) -> Body:
    """Cut a real helical thread into a named cylindrical face."""
    return kernel.thread(f.id, graph.body_of(a["body"]), a["face"],
                         standard=a.get("standard"),
                         pitch=ev(a["pitch"]) if a.get("pitch") else None,
                         length=ev(a["length"]) if a.get("length") else None,
                         start=ev(a.get("start", 0.0)),
                         hand=a.get("hand", "right"),
                         clearance=ev(a.get("clearance", 0.0)))


@feature("emboss", category="boolean", stands_for="body", args={
    "body": Ref(), "face": Name(note="the face the sketch is pressed onto"),
    "sketch": Ref(), "depth": Number(),
    "cut": Flag(note="cut the sketch in instead of raising it"),
    "wrap": Flag(note="wrap the sketch round a cylindrical face instead of projecting it")})
def emboss(graph, f, a, ev) -> Body:
    """Press a sketch onto a face: raised or cut, projected along its normal or wrapped round a cylinder."""
    return kernel.emboss(f.id, graph.body_of(a["body"]), a["face"], graph.sketch_of(a["sketch"]),
                         ev(a["depth"]), bool(a.get("cut")), bool(a.get("wrap")))
