"""Parts, mates, and the assembly they make.

An assembly is not a boolean: the parts stay separate solids, keep their own
names under a scope (``base:plate/+z``), and are placed by naming faces.
"""
from __future__ import annotations

from ...geometry import kernel
from ...geometry.assembly import placement
from ...geometry.assembly.assembly import MATES, NEEDS, mate_transform, transformed
from ... import names
from ...errors import CadError
from ...geometry.core.naming import Body
from ...model import fasteners
from ..declare.registry import feature
from ..declare.schema import ANGLE, Angle, Direction, ListOf, Point, Ratio, Spec
from ..declare.schema import Flag, Path, Anything, Names, Number, Ref, Refs, Text

KINDS_NOTE = "; ".join(f"{kind}: {what}" for kind, what in NEEDS.items())


@feature("part", category="assembly", produces="assembly", args={
    "document": Path(required=True, note="another document, relative to this one"),
    "parameters": Anything(note="parameters to drive the part with"),
    "result": Text(note="which feature of it to take"),
    "material": Text(note="what it is made of, for the bill of materials"),
    "colour": Text(note="#rrggbb, written to STEP")})
def part(graph, f, a, ev) -> Body:
    """Bring another document in as one named part of this assembly."""
    out = graph.build_part(f)
    return dressed(out, f.id, a.get("colour"), a.get("material"))


def dressed(body: Body, scope: str, colour, material) -> Body:
    """The part's colour and material in its notes, keyed by scope, over what it brought."""
    for kind, value in (("colours", colour), ("materials", material)):
        inside = {names.scoped(scope, k): v for k, v in (body.notes.get(kind) or {}).items()}
        if value:
            inside[scope] = str(value)
        if inside:
            body.notes[kind] = inside
    return body


@feature("fastener", category="assembly", produces="solid", args={
    "standard": Text(required=True, note="M3 ... M12"),
    "kind": Text(choices=fasteners.KINDS, default="socket_head"),
    "length": Number(required=False, note="a screw's shank under the head; four "
                                          "diameters when left out"),
    "at": Point(required=False, default=[0, 0, 0], note="where the head sits"),
    "axis": Direction(required=False, default=[0, 0, 1],
                      note="the way the head faces; the shank goes the other way"),
    "material": Text(default="S45C", note="for the bill of materials"),
    "colour": Text(note="#rrggbb, written to STEP")})
def fastener(graph, f, a, ev) -> Body:
    """A standard screw, bolt, nut or washer, ready to mate into a hole of the same size."""
    spec = fasteners.part(a["standard"], a.get("kind", "socket_head"),
                          ev(a["length"]) if a.get("length") is not None else None)
    out = kernel.fastener(f.id, spec, ev(a.get("at", [0, 0, 0])), ev(a.get("axis", [0, 0, 1])))
    out.notes["fastener"] = {"name": spec["name"], "standard": spec["standard"],
                             "kind": spec["kind"], "pitch": spec["pitch"],
                             "material": a.get("material") or "S45C"}
    return dressed(out, f.id, a.get("colour"), a.get("material") or "S45C")


#: what one mate takes, shared by the ordered `mate` and the solved `assemble`
MATE_ARGS = {
    "kind": Text(choices=MATES, default="fastened", note=KINDS_NOTE),
    "faces": Names(),
    "offset": Number(required=False, note="a distance held along the axis or normal; "
                                          "left out, a concentric part is free to slide"),
    "angle": Angle(required=False, default=0.0,
                   note="degrees: the angle an `angle` mate holds, or a spin about the axis"),
    "flip": Flag(default=True, note="turn the moved part round: faces meet, a pin goes in"),
    "pitch": Number(required=False, note="mm per turn, for a screw"),
    "hand": Text(choices=("right", "left"), default="right", note="which way a screw advances"),
    "ratio": Ratio(required=False, note="gear or belt: turns of the first part per turn of "
                                        "the second; the radii decide when left out"),
    "min": Number(required=False, note="hinge: degrees, slider: mm; how far back it may go",
                  measures_by=("kind", {"hinge": ANGLE})),
    "max": Number(required=False, note="hinge: degrees, slider: mm; how far on it may go",
                  measures_by=("kind", {"hinge": ANGLE})),
}


@feature("mate", category="assembly", produces="assembly", args=dict(
    MATE_ARGS, move=Ref(), to=Ref()))
def mate(graph, f, a, ev) -> Body:
    """Place one part against another by naming the faces that meet (a single transform)."""
    moving, fixed = graph.body_of(a["move"]), graph.body_of(a["to"])
    trsf = mate_transform(a.get("kind", "fastened"), moving, fixed, a["faces"],
                          ev(a.get("offset") or 0.0), ev(a.get("angle") or 0.0),
                          bool(a.get("flip", True)))
    return transformed(moving, trsf)


@feature("assemble", category="assembly", produces="assembly", args={
    "bodies": Refs(),
    "ground": Ref(required=False, note="the part that does not move"),
    "mates": ListOf(Spec(dict(MATE_ARGS, note=Text(note="why this mate is here"))),
                    note="constraints solved together, not one at a time"),
    "drive": ListOf(Spec({
        "part": Text(required=True, note="one of the bodies"),
        "turn": Angle(required=False, note="degrees, about a freedom the mates leave"),
        "slide": Number(required=False, note="mm, along a freedom the mates leave"),
        "about": Direction(required=False, note="which axis, when the part has more than one"),
        "along": Direction(required=False, note="which direction, when it has more than one")}),
        note="hold a part this far along its freedom; the rest follow")})
def assemble(graph, f, a, ev) -> Body:
    """Put parts together as separate solids under their own scopes, solving any mates at once.

    With ``mates`` the parts are placed here and all mates are solved together,
    so several can hold one part. The ``mate`` feature applies one transform
    in order and is enough when one is all that is needed. A mate's ``offset``
    is a constraint only when written; a concentric mate without one leaves
    the part free to slide along the bore. ``drive`` turns or slides a part
    along what the mates leave free, and a parameter in it is the mechanism's
    position.
    """
    bodies = {b: graph.body_of(b) for b in a["bodies"]}
    if not a.get("mates"):
        return kernel.compound(f.id, list(bodies.values()))

    mates = [dict(m, offset=None if m.get("offset") is None else ev(m["offset"]),
                  angle=ev(m.get("angle") or 0.0), pitch=ev(m.get("pitch") or 0.0),
                  ratio=ev(m["ratio"]) if m.get("ratio") is not None else None,
                  min=ev(m["min"]) if m.get("min") is not None else None,
                  max=ev(m["max"]) if m.get("max") is not None else None)
             for m in a["mates"]]
    solved = placement.solve(bodies, mates, ground=a.get("ground"))
    if solved["conflicts"]:
        raise CadError("over_constrained",
                       "these mates cannot all be true at once",
                       {"conflicts": solved["conflicts"],
                        "hint": "one of them is fighting the others; the error "
                                "is how far each is from being satisfied"})
    if a.get("drive"):
        drives = [dict(d, **{key: ev(d[key]) for key in ("turn", "slide") if d.get(key) is not None})
                  for d in a["drive"]]
        solved = placement.drive(bodies, mates, drives, ground=a.get("ground"), rest=solved)
    placed = {name: placement.moved(body, solved["poses"][name])
              for name, body in bodies.items()}
    out = kernel.compound(f.id, list(placed.values()))
    # stored on the body, not the evaluator: a cached rebuild never runs this
    # function, and what the mates left free belongs to the assembly it built
    out.notes["assembly"] = dict(
        {k: v for k, v in solved.items() if k not in ("poses", "frames") and not k.startswith("_")},
        poses={name: [pose[0].tolist(), pose[1].tolist()]
               for name, pose in solved["poses"].items()})
    return out
