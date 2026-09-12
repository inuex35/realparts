"""Writing features into a document: an edit as a value, without the session.

An edit is the features it adds, the parameters it needs, and what the
document's result becomes; the session validates, applies and rebuilds it.
Sizes become named parameters (``pocket1_depth``) so the panel can drive them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .document import Document, Feature
from ..errors import CadError


@dataclass
class Edit:
    """What an operation adds to a document, before anything is applied."""

    features: list = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    result: str = ""
    info: dict = field(default_factory=dict)


def fresh_id(doc: Document, prefix: str) -> str:
    taken = {f.id for f in doc.features}
    i = 1
    while f"{prefix}{i}" in taken:
        i += 1
    return f"{prefix}{i}"


def target_of(doc: Document) -> str:
    if not doc.features:
        raise CadError("empty_document", "there is nothing to add to yet")
    return doc.result or doc.features[-1].id


def _repeat_args(fid: str, count: int, spacing: float, direction) -> tuple:
    """A feature's own pattern, so one edit moves the whole row."""
    if int(count) <= 1:
        return None, {}
    name = f"{fid}_pitch"
    return ({"direction": list(direction or [1, 0, 0]), "spacing": name,
             "count": int(count)}, {name: float(spacing)})


def edge_feature(doc: Document, kind: str, edges: list, size: float,
                 feature_id: str | None = None) -> Edit:
    """A fillet or a chamfer on edges named by the caller."""
    if kind not in ("fillet", "chamfer"):
        raise CadError("bad_arguments", f"kind must be fillet or chamfer, not {kind!r}")
    fid = feature_id or fresh_id(doc, kind)
    target = target_of(doc)
    dimension = {"radius": size} if kind == "fillet" else {"distance": size}
    return Edit([Feature(fid, kind, {"body": target, **dimension, "edges": list(edges)})],
                {}, fid)


def flange(doc: Document, edge: str, length: float, angle: float = 90.0,
           radius: float | None = None, feature_id: str | None = None) -> Edit:
    """Bend a flap up from an edge of a sheet metal part."""
    fid = feature_id or fresh_id(doc, "flange")
    args = {"body": target_of(doc), "edge": edge, "length": length, "angle": angle}
    if radius is not None:
        args["radius"] = radius
    return Edit([Feature(fid, "flange", args)], {}, fid)


def pocket(doc: Document, face: str, width: float, height: float, depth: float,
           kind: str = "pocket", at=(0.0, 0.0), count: int = 1, spacing: float = 0.0,
           direction=None, feature_id: str | None = None,
           until: str | dict | None = None, symmetric: bool = False) -> Edit:
    """A rectangular pocket or boss, sketched on a named face."""
    if kind not in ("pocket", "boss"):
        raise CadError("bad_arguments", f"kind must be pocket or boss, not {kind!r}")
    if min(float(width), float(height)) <= 0 or (not until and float(depth) <= 0):
        # with an end condition the depth is not used, so it is not required
        raise CadError("bad_parameter", "width, height and depth must be positive")

    fid = feature_id or fresh_id(doc, kind)
    target = target_of(doc)
    w, h, d = f"{fid}_w", f"{fid}_h", f"{fid}_depth"
    u0, v0 = float(at[0]), float(at[1])
    corners = {"sw": (f"{u0} - {w}/2", f"{v0} - {h}/2"),
               "se": (f"{u0} + {w}/2", f"{v0} - {h}/2"),
               "ne": (f"{u0} + {w}/2", f"{v0} + {h}/2"),
               "nw": (f"{u0} - {w}/2", f"{v0} + {h}/2")}
    sketch = Feature(f"{fid}_profile", "sketch", {
        "on": {"body": target, "face": face},
        "points": {k: list(v) for k, v in corners.items()},
        "lines": {"south": ["sw", "se"], "east": ["se", "ne"],
                  "north": ["ne", "nw"], "west": ["nw", "sw"]},
        "constraints": [
            {"type": "fix", "point": "sw", "at": list(corners["sw"])},
            {"type": "horizontal", "line": "south"},
            {"type": "vertical", "line": "east"},
            {"type": "horizontal", "line": "north"},
            {"type": "vertical", "line": "west"},
            {"type": "distance", "points": ["sw", "se"], "value": w},
            {"type": "distance", "points": ["se", "ne"], "value": h}]})

    args = {"body": target, "sketch": sketch.id, "depth": d}
    if until:
        # an end condition replaces the depth: "through all" is a relationship,
        # and keeping a stale number beside it invites the two to disagree
        args.pop("depth")
        args["until"] = until
    if symmetric:
        args["symmetric"] = True
    repeat, repeat_parameters = _repeat_args(fid, count, spacing, direction)
    if repeat:
        args["pattern"] = repeat
    parameters = {w: float(width), h: float(height), **repeat_parameters}
    if "depth" in args:
        parameters[d] = float(depth)
    return Edit([sketch, Feature(fid, kind, args)], parameters, fid, {"on_face": face})


def drawn_sketch(doc: Document, face: str | None, spec: dict, dimensions: dict,
                 operation: str = "pocket", depth: float = 5.0,
                 feature_id: str | None = None, until: str | dict | None = None,
                 symmetric: bool = False, plane: str | None = None) -> Edit:
    """A sketch drawn in the viewport, already dimensioned by :mod:`autodim`.
    On a face of the body, or on a work plane (`plane`)."""
    if operation not in ("pocket", "boss", "extrude"):
        raise CadError("bad_arguments",
                       f"operation must be pocket, boss or extrude, not {operation!r}")
    fid = feature_id or fresh_id(doc, operation)
    # an extrude on a work plane needs no body to cut; the others do
    target = target_of(doc) if not (plane and operation == "extrude") else None
    args = {k: v for k, v in spec.items()
            if k in ("points", "lines", "circles", "arcs", "ellipses", "slots",
                     "constraints")}
    if spec.get("allow_underconstrained"):
        args["allow_underconstrained"] = True
    # the sketch follows the face, or sits on the plane
    args["on"] = {"plane": plane} if plane else {"body": target, "face": face}
    sketch = Feature(f"{fid}_profile", "sketch", args)
    depth_name = f"{fid}_depth"
    extent = {"until": until} if until else {"depth": depth_name}
    if symmetric:
        extent["symmetric"] = True
    if operation == "extrude":
        args = {"sketch": sketch.id}
        args.update({"distance": depth_name} if not until else extent)
        body = Feature(fid, "extrude", args)
    else:
        body = Feature(fid, operation, {"body": target, "sketch": sketch.id, **extent})
    parameters = dict(dimensions)
    if not until:
        parameters[depth_name] = float(depth)
    return Edit([sketch, body], parameters, fid,
                {"on_face": face, "on_plane": plane, "dimensions": dict(dimensions)})


def hole(doc: Document, face: str, diameter: float, depth: float | None = None,
         counterbore: dict | None = None, countersink: dict | None = None,
         at=(0.0, 0.0), count: int = 1, spacing: float = 0.0, direction=None,
         standard: str | None = None, fit: str = "normal", seat: str = "none",
         note: str | None = None, feature_id: str | None = None) -> Edit:
    """A hole, with its seat and its repeat, as one feature."""
    fid = feature_id or fresh_id(doc, "hole")
    target = target_of(doc)
    dia, dep = f"{fid}_d", f"{fid}_depth"
    args = {"body": target, "face": face, "at": [float(at[0]), float(at[1])],
            "diameter": dia}
    parameters = {dia: float(diameter)}
    if standard:
        # keep the intent as well as the number: the document should still say
        # "M6 tapped" when someone reads it a year later
        args.update({"standard": standard, "fit": fit, "seat": seat, "note": note})
    if depth:
        parameters[dep] = float(depth)
        args["depth"] = dep
    if counterbore:
        parameters[f"{fid}_seat_d"] = float(counterbore["diameter"])
        parameters[f"{fid}_seat_depth"] = float(counterbore["depth"])
        args["counterbore"] = {"diameter": f"{fid}_seat_d", "depth": f"{fid}_seat_depth"}
    elif countersink:
        parameters[f"{fid}_seat_d"] = float(countersink["diameter"])
        args["countersink"] = {"diameter": f"{fid}_seat_d",
                               "angle": float(countersink.get("angle", 90))}
    repeat, repeat_parameters = _repeat_args(fid, count, spacing, direction)
    if repeat:
        args["pattern"] = repeat
    parameters.update(repeat_parameters)
    return Edit([Feature(fid, "hole", args)], parameters, fid,
                {"on_face": face, "note": note})


def shell(doc: Document, open_faces: list, thickness: float,
          feature_id: str | None = None) -> Edit:
    fid = feature_id or fresh_id(doc, "shell")
    target = target_of(doc)
    name = f"{fid}_wall"
    return Edit([Feature(fid, "shell", {"body": target, "open": list(open_faces),
                                        "thickness": name})],
                {name: float(thickness)}, fid)


def thread(doc: Document, face: str, standard: str | None = None,
           pitch: float | None = None, length: float | None = None,
           clearance: float = 0.0, feature_id: str | None = None) -> Edit:
    """A real thread on a named cylindrical face."""
    fid = feature_id or fresh_id(doc, "thread")
    args = {"body": target_of(doc), "face": face}
    parameters = {}
    if standard:
        args["standard"] = standard
    else:
        name = f"{fid}_pitch"
        args["pitch"] = name
        parameters[name] = float(pitch or 1.0)
    if length:
        name = f"{fid}_length"
        args["length"] = name
        parameters[name] = float(length)
    if clearance:
        name = f"{fid}_clearance"
        args["clearance"] = name
        parameters[name] = float(clearance)
    return Edit([Feature(fid, "thread", args)], parameters, fid)


def move_face(doc: Document, face: str, distance: float,
              feature_id: str | None = None) -> Edit:
    """Push or pull one face -- direct editing, on a model with no history."""
    fid = feature_id or fresh_id(doc, "move")
    name = f"{fid}_distance"
    return Edit([Feature(fid, "move_face", {"body": target_of(doc), "face": face,
                                            "distance": name})],
                {name: float(distance)}, fid)


def delete_faces(doc: Document, faces: list, heal: bool = True,
                 feature_id: str | None = None) -> Edit:
    """Take faces off: healed (defeatured) or left open for surface work."""
    fid = feature_id or fresh_id(doc, "erase" if heal else "open")
    return Edit([Feature(fid, "delete_face", {"body": target_of(doc),
                                              "faces": list(faces),
                                              "heal": bool(heal)})], {}, fid)


def thicken(doc: Document, thickness: float, feature_id: str | None = None) -> Edit:
    """Give the current surface a wall, which turns it back into a solid."""
    fid = feature_id or fresh_id(doc, "thicken")
    name = f"{fid}_thickness"
    return Edit([Feature(fid, "thicken", {"body": target_of(doc), "thickness": name})],
                {name: float(thickness)}, fid)


def cap(doc: Document, continuity: str = "G0", feature_id: str | None = None) -> Edit:
    """Patch every opening and sew, which is the short way back to a solid."""
    fid = feature_id or fresh_id(doc, "cap")
    return Edit([Feature(fid, "cap", {"body": target_of(doc),
                                      "continuity": continuity})], {}, fid)


def draft(doc: Document, faces: list, angle: float, neutral: str | None = None,
          direction=None, feature_id: str | None = None) -> Edit:
    fid = feature_id or fresh_id(doc, "draft")
    target = target_of(doc)
    name = f"{fid}_angle"
    return Edit([Feature(fid, "draft", {"body": target, "faces": list(faces),
                                        "angle": name,
                                        "direction": list(direction or [0, 0, 1]),
                                        "neutral": neutral})],
                {name: float(angle)}, fid)


def mirror(doc: Document, origin, normal, merge: bool = True,
           feature_id: str | None = None) -> Edit:
    """Reflect the body about a plane given as a point and its normal."""
    fid = feature_id or fresh_id(doc, "mirror")
    return Edit([Feature(fid, "mirror", {"body": target_of(doc), "merge": bool(merge),
                                         "plane": {"origin": list(origin),
                                                   "normal": list(normal)}})],
                {}, fid)


def ring(doc: Document, count: int, origin, direction, step: float | None = None,
         merge: bool = True, feature_id: str | None = None) -> Edit:
    """Repeat the body around an axis given as a point and a direction.

    `step` is the turn between copies; without one they spread evenly over a
    full turn, which is what "six bolts" means.
    """
    fid = feature_id or fresh_id(doc, "ring")
    args = {"body": target_of(doc), "count": int(count), "merge": bool(merge),
            "axis": {"origin": list(origin), "direction": list(direction)}}
    parameters = {}
    if step:
        name = f"{fid}_step"
        args["angle"] = name
        parameters[name] = float(step)
    return Edit([Feature(fid, "pattern", args)], parameters, fid)


def row(doc: Document, count: int, spacing: float, direction,
        merge: bool = True, feature_id: str | None = None) -> Edit:
    """Repeat the body along a direction."""
    fid = feature_id or fresh_id(doc, "row")
    name = f"{fid}_pitch"
    return Edit([Feature(fid, "pattern", {"body": target_of(doc), "count": int(count),
                                          "spacing": name, "merge": bool(merge),
                                          "direction": list(direction)})],
                {name: float(spacing)}, fid)



def split(doc: Document, face: str | None, offset: float, keep: str,
          normal=None, origin=None, feature_id: str | None = None) -> Edit:
    """Cut the body at a plane off a face (or a given plane) and keep one side."""
    fid = feature_id or fresh_id(doc, "split")
    target = target_of(doc)
    if face:
        name = f"{fid}_offset"
        return Edit([Feature(fid, "split", {"body": target, "face": face, "offset": name,
                                            "keep": keep})], {name: float(offset)}, fid)
    if normal is not None:
        args = {"body": target, "origin": [float(c) for c in (origin or [0, 0, 0])],
                "normal": [float(c) for c in normal], "keep": keep}
        return Edit([Feature(fid, "split", args)], {}, fid)
    raise CadError("bad_arguments", "a split needs a face or a normal")


def draft_from(doc: Document, faces: list, angle: float, parting_face: str,
               offset: float = 0.0, feature_id: str | None = None) -> Edit:
    """Taper faces away from a parting plane lying in (or off) a face of the body."""
    fid = feature_id or fresh_id(doc, "draft")
    name = f"{fid}_angle"
    return Edit([Feature(fid, "draft", {"body": target_of(doc), "faces": list(faces),
                                        "angle": name, "parting_face": parting_face,
                                        "parting_offset": float(offset)})],
                {name: float(angle)}, fid)


def emboss(doc: Document, face: str, text: str, height: float, depth: float,
           cut: bool = False, wrap: bool = False, at=None,
           feature_id: str | None = None, plane: dict | None = None) -> Edit:
    """Words drawn on a face (or on ``plane``, for a round face) and pressed into it."""
    if not text:
        raise CadError("bad_arguments", "an emboss needs the text")
    fid = feature_id or fresh_id(doc, "emboss")
    target = target_of(doc)
    sketch_id = fid + "_text"
    # the pen starts at the baseline's left end: centre the words on the face
    at = list(at) if at is not None else [-0.3 * float(height) * len(text), -0.35 * float(height)]
    where = {"plane": plane} if plane else {"on": {"body": target, "face": face}}
    sketch = Feature(sketch_id, "sketch", dict(where, texts={
        "words": {"text": str(text), "at": [float(at[0]), float(at[1])], "height": float(height)}}))
    depth_name = f"{fid}_depth"
    made = Feature(fid, "emboss", {"body": target, "face": face, "sketch": sketch_id,
                                   "depth": depth_name, "cut": bool(cut), "wrap": bool(wrap)})
    return Edit([sketch, made], {depth_name: abs(float(depth))}, fid)


def coil(doc: Document, face: str, wire: float, pitch: float, turns: float,
         start, tangent, x_axis, axis_foot, axis, radius: float,
         feature_id: str | None = None) -> Edit:
    """A wire wound round a cylindrical face: a round profile swept along a helix."""
    fid = feature_id or fresh_id(doc, "coil")
    sketch_id = fid + "_wire"
    sketch = Feature(sketch_id, "sketch", {
        "plane": {"origin": [float(c) for c in start], "normal": [float(c) for c in tangent],
                  "x_axis": [float(c) for c in x_axis]},
        "points": {"o": [0.0, 0.0]},
        "circles": {"wire": {"centre": "o", "radius": float(wire) / 2}},
        "constraints": [{"type": "fix", "point": "o", "at": [0.0, 0.0]}]})
    pitch_name = f"{fid}_pitch"
    wound = Feature(fid + "_wound", "sweep", {
        "profile": sketch_id,
        "helix": {"radius": float(radius), "pitch": pitch_name,
                  "height": float(pitch) * float(turns),
                  "at": [float(c) for c in axis_foot], "axis": [float(c) for c in axis]}})
    joined = Feature(fid, "fuse", {"target": target_of(doc), "tool": fid + "_wound"})
    return Edit([sketch, wound, joined], {pitch_name: float(pitch)}, fid)
