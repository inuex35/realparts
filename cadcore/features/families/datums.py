"""Datums and sketches: the things features are placed against.

None of these build a solid. A work plane is a named frame, a datum axis a
named direction, and a sketch a solved profile; each is a feature so that
other features can refer to it by name.
"""
from __future__ import annotations

from ...geometry import kernel
from ...geometry.assembly.assembly import axis_of
from ...sketching import solve as solve_sketch
from ...sketching.loops import _area as loop_area
from ...sketching.model import Plane, SketchResult
from ...errors import CadError
from ..declare.registry import feature
from ..declare.schema import Direction
from ..declare.schema import (Angle, Anything, Flag, ListOf, MapOf, Name, Number, Path, Point,
                              Ratio, Ref, Spec, Text)


@feature("plane", category="datum", produces="datum", args={
    "from": Spec({"body": Ref(), "face": Name()}),
    "between": ListOf(Spec({"body": Ref(), "face": Name()}),
                      note="two {body, face} references to sit halfway between"),
    "about": Spec({"body": Ref(), "edge": Name()},
                  note="a straight edge of the same body to turn about"),
    "angle": Angle(required=False, default=0.0),
    "offset": Number(required=False, default=0.0),
    "origin": Point(required=False), "normal": Direction(required=False),
    "x_axis": Direction(required=False)})
def plane(graph, f, a, ev):
    """A named work plane: off a face, turned about one of its edges, midway
    between two faces, or given as a frame."""
    graph.planes[f.id] = graph.frame_of(f, a, ev)
    return None


@feature("axis", category="datum", produces="datum", args={
    "body": Ref(required=False), "face": Name(required=False),
    "through": ListOf(Point(), note="two points the axis passes through"),
    "origin": Point(required=False), "direction": Direction(required=False)})
def axis(graph, f, a, ev):
    """A named axis to revolve about or pattern around, from a round face, two points, or a direction.

    Taken from the round face when one is given, so the axis follows the bore
    when it moves.
    """
    if "face" in a:
        body = graph.body_of(a["body"]) if a.get("body") else None
        if body is None:
            raise CadError("bad_arguments", "an axis from a face needs a body",
                           {"feature": f.id})
        origin, direction, _ = axis_of(body, a["face"])
    elif "through" in a:
        points = [ev(p) for p in a["through"]]
        if len(points) != 2:
            raise CadError("bad_arguments", "an axis through points needs two",
                           {"given": len(points)})
        origin = points[0]
        direction = [points[1][i] - points[0][i] for i in range(3)]
    else:
        origin = ev(a.get("origin", [0, 0, 0]))
        direction = ev(a.get("direction", [0, 0, 1]))
    length = sum(c * c for c in direction) ** 0.5
    if length < 1e-9:
        raise CadError("bad_parameter", "the axis has no direction", {"feature": f.id})
    graph.axes[f.id] = {"origin": list(origin),
                        "direction": [c / length for c in direction]}
    return None



def _sketch_lengths(args: dict) -> list:
    """Paths to the lengths inside a sketch's own geometry.

    The sketch language is declared `Anything()`, so the declaration walk
    cannot see into it; this hook gives the unit converter the length paths.
    Directions have no unit, a line is two point names, and a constraint's
    value is a length or an angle as `cadcore.sketching.constraints` says.
    """
    from ...sketching.constraints import measures

    out: list = []
    plane = args.get("plane")
    if isinstance(plane, dict) and isinstance(plane.get("origin"), list):
        out += [["plane", "origin", i] for i in range(len(plane["origin"]))]
    for name, point in (args.get("points") or {}).items():
        if isinstance(point, list):
            out += [["points", name, i] for i in range(len(point))]
    for group, key in (("arcs", "radius"), ("circles", "radius"),
                       ("offsets", "distance"), ("slots", "width"),
                       ("ellipses", "major"), ("ellipses", "minor"), ("texts", "height")):
        for name, item in (args.get(group) or {}).items():
            if isinstance(item, dict) and key in item:
                out.append([group, name, key])
    for name, item in (args.get("texts") or {}).items():
        if isinstance(item, dict) and isinstance(item.get("at"), list):
            out += [["texts", name, "at", i] for i in range(len(item["at"]))]
    if isinstance(args.get("outline_offset"), dict) and "distance" in args["outline_offset"]:
        out.append(["outline_offset", "distance"])
    for index, constraint in enumerate(args.get("constraints") or []):
        if not isinstance(constraint, dict):
            continue
        what = measures(str(constraint.get("type", "")))
        if what == "length" and "value" in constraint:
            out.append(["constraints", index, "value"])
        elif what == "place" and isinstance(constraint.get("at"), list):
            out += [["constraints", index, "at", i]
                    for i in range(len(constraint["at"]))]
    return out


def _sketch_angles(args: dict) -> list:
    """Paths to a sketch's angles, which a unit conversion must leave untouched."""
    from ...sketching.constraints import measures

    return [["constraints", index, "value"]
            for index, constraint in enumerate(args.get("constraints") or [])
            if isinstance(constraint, dict)
            and measures(str(constraint.get("type", ""))) == "angle"
            and "value" in constraint]


@feature("sketch", category="datum", produces="datum",
         lengths=_sketch_lengths, angles=_sketch_angles, args={
    "on": Spec({"body": Ref(required=False), "face": Name(required=False),
                "plane": Ref(required=False)}),
    "plane": Anything(note="an origin, a normal and an x axis"),
    "points": Anything(), "lines": Anything(), "arcs": Anything(),
    "circles": Anything(), "splines": Anything(), "slots": Anything(),
    "ellipses": Anything(note="{centre, major, minor, angle}: semi-axes, degrees from u"),
    "offsets": Anything(), "trims": Anything(),
    "texts": MapOf(Spec({"text": Text(required=True), "at": Point(required=False, default=[0, 0]),
                         "height": Number(note="the font size; capitals about 0.7 of it"),
                         "angle": Angle(required=False, default=0.0),
                         "font": Text(note="an installed font's name"),
                         "bold": Flag(), "italic": Flag()}),
                   note="words drawn as outlines, added to the profile"),
    "outline_offset": Spec({"distance": Number(note="outwards; negative goes in"),
                            "join": Text(choices=("arc", "intersection"), default="arc"),
                            "keep": Flag(note="keep the drawn loops beside the offset ones")},
                           note="every loop moved by a distance, corners rounded or met"),
    "file": Spec({"path": Path(note="a .dxf or .svg, relative to the document"),
                  "scale": Ratio(required=False, default=1.0)},
                 note="geometry read from a drawing file, pinned where the file put it"),
    "projections": MapOf(Spec({"body": Ref(required=False), "edge": Name()}),
                         note="edges of existing bodies to pin into the sketch"),
    "constraints": Anything(), "dimensions": Anything(),
    "allow_underconstrained": Flag(
        note="solve it anyway, and take what comes out"),
    "open": Flag(note="a profile that is not meant to close")})
def sketch(graph, f, a, ev):
    """A solved profile, on a face, on a work plane, or in absolute coordinates."""
    spec = a
    if a.get("file"):
        spec = with_file(graph, f, spec)
    on = a.get("on")
    if on and on.get("plane"):
        spec = dict(spec, plane=graph.plane_of(on["plane"]))
    elif on:
        # resolve the face frame against the body as it is now
        frame = kernel.face_frame(graph.body_of(on["body"]), on["face"])
        spec = dict(spec, plane={k: frame[k] for k in ("origin", "normal", "x_axis")})
    if a.get("projections"):
        spec = project_into(graph, f, spec, on)
    texts = a.get("texts") or {}
    if texts and not any(spec.get(k) for k in ("points", "lines", "circles", "ellipses")):
        # nothing but words: the solver has nothing to solve, the text is the profile
        solved = SketchResult(plane_of(spec))
    else:
        solved = solve_sketch(spec, ev)
    for name, item in texts.items():
        solved.loops += kernel.text_loops(name, str(item["text"]), ev(item["height"]),
                                          ev(item.get("at", [0, 0])), ev(item.get("angle", 0.0)),
                                          item.get("font") or "DejaVu Sans",
                                          bool(item.get("bold")), bool(item.get("italic")))
    around = a.get("outline_offset")
    if around:
        moved = kernel.offset_loops(f.id, solved.loops, solved.plane, ev(around["distance"]),
                                    around.get("join", "arc"))
        solved.loops = (solved.loops if around.get("keep") else []) + moved
    if texts or around:
        solved.loops.sort(key=loop_area, reverse=True)
    graph.sketches[f.id] = solved
    graph.sketch_specs[f.id] = spec


def plane_of(spec: dict) -> Plane:
    plane = spec.get("plane") or {}
    return Plane(tuple(plane.get("origin", [0, 0, 0])), tuple(plane.get("normal", [0, 0, 1])),
                 tuple(plane.get("x_axis", [1, 0, 0])))


def with_file(graph, f, spec: dict) -> dict:
    """The file's geometry merged into the sketch, its names prefixed by the feature's."""
    from ...sketching.files import read_file

    path = graph.doc.resolve(spec["file"]["path"])
    read = read_file(path, float(spec["file"].get("scale", 1.0) or 1.0), f.id + "_")
    out = dict(spec)
    for key in ("points", "lines", "arcs", "circles", "splines"):
        merged = dict(out.get(key) or {})
        merged.update(read[key])
        out[key] = merged
    out["constraints"] = list(out.get("constraints") or []) + read["constraints"]
    out.pop("file", None)
    return out


def project_into(graph, f, spec: dict, on) -> dict:
    """Bring existing edges into the sketch as fixed references, not copies.

    Projected geometry is pinned where the model puts it and re-projected when
    the body changes. A missing edge is refused rather than replaced.
    """
    plane = spec.get("plane") or {}
    origin, normal, x_axis = (plane.get("origin", [0, 0, 0]),
                              plane.get("normal", [0, 0, 1]),
                              plane.get("x_axis", [1, 0, 0]))
    y_axis = [normal[1] * x_axis[2] - normal[2] * x_axis[1],
              normal[2] * x_axis[0] - normal[0] * x_axis[2],
              normal[0] * x_axis[1] - normal[1] * x_axis[0]]

    def to_uv(point):
        delta = [point[i] - origin[i] for i in range(3)]
        return [round(sum(delta[i] * x_axis[i] for i in range(3)), 9),
                round(sum(delta[i] * y_axis[i] for i in range(3)), 9)]

    out = dict(spec)
    points = dict(out.get("points") or {})
    lines = dict(out.get("lines") or {})
    circles = dict(out.get("circles") or {})
    splines = dict(out.get("splines") or {})
    constraints = list(out.get("constraints") or [])

    for name, ref in f.args["projections"].items():
        source = ref.get("body") or (on or {}).get("body")
        if not source:
            raise CadError("bad_arguments",
                           f"projection {name!r} does not say which body",
                           {"feature": f.id})
        body = graph.body_of(source)
        table = body.edge_table()
        edge = table.get(ref["edge"])
        if edge is None:
            raise CadError("unresolved_reference",
                           f"no edge named {ref['edge']!r} to project",
                           {"available": sorted(table)[:40]})
        kind, data = kernel.edge_geometry(edge)
        if kind == "line":
            for label, point in (("a", data["start"]), ("b", data["end"])):
                key = f"{name}_{label}"
                points[key] = to_uv(point)
                constraints.append({"type": "fix", "point": key, "at": points[key]})
            lines[name] = [f"{name}_a", f"{name}_b"]
        elif kind == "circle":
            if abs(abs(sum(data["axis"][i] * normal[i] for i in range(3))) - 1) > 1e-6:
                raise CadError("cannot_project",
                               f"{ref['edge']!r} is not parallel to the sketch plane",
                               {"hint": "an angled circle projects to an ellipse"})
            key = f"{name}_c"
            points[key] = to_uv(data["centre"])
            constraints.append({"type": "fix", "point": key, "at": points[key]})
            circles[name] = {"centre": key, "radius": data["radius"]}
        else:
            # any other curve: pinned as a spline through its points, flat on the plane
            through = []
            for k, point in enumerate(kernel.edge_points(edge, 24)):
                key = f"{name}_{k}"
                points[key] = to_uv(point)
                constraints.append({"type": "fix", "point": key, "at": points[key]})
                through.append(key)
            splines[name] = {"through": through}

    out.update({"points": points, "lines": lines, "circles": circles, "splines": splines,
                "constraints": constraints})
    out.pop("projections", None)
    return out
