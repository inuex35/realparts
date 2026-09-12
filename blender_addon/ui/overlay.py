"""Draw the selected feature's dimensions in the viewport and let one be clicked and retyped.

Labels are anchored at the feature's own faces, found through the face names
(``boss1/east`` belongs to ``boss1``), so no separate position map is needed.
"""
from __future__ import annotations

import json
import math

import blf
import bpy
import gpu
import mathutils
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader

from ..link import sync
from ..link import names
from ..viewport.marks import LABELS, PLANES, plane_size  # noqa: F401

_handle = None


def _anchor(feature: str):
    """The feature's anchor in world space: the mean centre of the faces it made."""
    total, count = [0.0, 0.0, 0.0], 0
    # every part: the feature's faces may be on any one of them, and a feature
    # that spans two (a mate, a boolean) is anchored between them
    for body in sync.bodies():
        if not body.get("cad_face_table"):
            continue
        table = json.loads(body["cad_face_table"])
        attribute = body.data.attributes.get("cad_face")
        if attribute is None:
            continue
        wanted = {i for i, name in enumerate(table)
                  if names.feature_of(name) == feature}
        if not wanted:
            continue
        for centre in _face_centres(body, attribute, wanted):
            total = [total[i] + centre[i] for i in range(3)]
            count += 1
    if not count:
        return None
    return [c / count for c in total]


def _face_centres(body, attribute, wanted: set):
    """World-space centres of the polygons whose CAD face index is in `wanted`."""
    if body.mode == 'EDIT':
        # in edit mode the attribute reads empty; the BMesh has the faces
        import bmesh

        bm = bmesh.from_edit_mesh(body.data)
        layer = bm.faces.layers.int.get("cad_face")
        if layer is None:
            return
        for face in bm.faces:
            if face[layer] in wanted:
                yield body.matrix_world @ face.calc_center_median()
        return
    for polygon in body.data.polygons:
        if attribute.data[polygon.index].value in wanted:
            yield body.matrix_world @ polygon.center


def _sketch_for(props) -> str | None:
    """The sketch the selected feature is, or was made from.

    The kernel answers this for a face that was picked, and that answer is
    used; the name is only guessed from for a feature clicked in the history,
    where there is no face to ask about.
    """
    from ..viewport import marks

    if not (0 <= props.feature_index < len(props.features)):
        return None
    item = props.features[props.feature_index]
    if marks.SKETCH_BEHIND[0] == item.name and marks.SKETCH_BEHIND[1]:
        return marks.SKETCH_BEHIND[1]
    if item.kind == "sketch":
        return item.name
    for argument in props.feature_args:
        if argument.name == "sketch" and argument.text:
            return argument.text
    names = {f.name for f in props.features}
    return item.name + "_profile" if item.name + "_profile" in names else None


def sketch_labels(geometry: dict, constraints: list, values: dict) -> list:
    """Where each dimension of a sketch sits in 3D, and what it says.

    A distance between two points is drawn at their midpoint on the sketch
    plane. A dimension whose value is a parameter name shows the parameter
    and can be clicked; a plain number is shown as it is.
    """
    plane = geometry["plane"]
    o, n, x = plane["origin"], plane["normal"], plane["x_axis"]
    y = (n[1] * x[2] - n[2] * x[1], n[2] * x[0] - n[0] * x[2], n[0] * x[1] - n[1] * x[0])

    def to_3d(u, v):
        return [o[i] + u * x[i] + v * y[i] for i in range(3)]

    out = []
    points = geometry["points"]
    for constraint in constraints:
        if constraint.get("type") != "distance" or "value" not in constraint:
            continue
        pair = constraint.get("points") or []
        if len(pair) != 2 or pair[0] not in points or pair[1] not in points:
            continue
        a, b = points[pair[0]], points[pair[1]]
        value = constraint["value"]
        if isinstance(value, str) and value in values:
            name, text = value, "%s  %.3g" % (value, values[value])
        elif isinstance(value, (int, float)):
            name, text = None, "%.3g" % value
        else:
            continue
        out.append({"at": to_3d((a[0] + b[0]) / 2, (a[1] + b[1]) / 2),
                    "ends": (to_3d(*a), to_3d(*b)), "name": name, "text": text})
    return out


_cache: dict = {}        # (sketch, undo depth, faces, volume) -> (geometry, constraints)


def forget_sketch_cache() -> None:
    """After an edit that moved a sketch without changing the solid's size."""
    _cache.clear()


def _sketch_data(context, props, sketch: str):
    from ..link.client import ServerError
    from ..link.state import get_client

    key = (sketch, props.undo_depth, props.faces, round(props.volume, 3))
    if key in _cache:
        return _cache[key]
    try:
        data = (get_client(context).call("sketch_geometry", sketch=sketch),
                get_client(context).call("sketch_constraints", sketch=sketch)["constraints"])
    except ServerError:
        data = None
    if len(_cache) > 8:
        _cache.clear()
    _cache[key] = data
    return data


#: the edge's grips by what they make; the face's arrow is amber, so neither is
GRIP_WORD = {"fillet": "Round", "chamfer": "Chamfer"}
GRIP_LEAD = 30.0        # pixels from the edge to the grips
GRIP_SPREAD = 22.0      # pixels between the two grips
GRIP_SIZE = 10.0        # pixels, drawn
GRIP_HIT = 16.0         # pixels, pressed
GRIP_COLOUR = {"fillet": (0.30, 0.90, 0.45, 0.9),
               "chamfer": (1.00, 0.55, 0.20, 0.9)}



def plane_corners(frame: dict, size: float) -> list:
    o, n, x = (mathutils.Vector(frame[k]) for k in ("origin", "normal", "x_axis"))
    y = n.cross(x)
    return [o + x * u * size + y * v * size for u, v in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


def _draw_planes(context, to_2d) -> None:
    """Each work plane as a faint square about its origin, its name beside it;
    the picked one brighter. Squares are what the pick tool can click."""
    from ..viewport import marks

    if not PLANES:
        return
    size = plane_size()
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    font = 0
    for name, frame in PLANES.items():
        corners = [to_2d(c) for c in plane_corners(frame, size)]
        if any(c is None for c in corners):
            continue
        pts = [(c.x, c.y) for c in corners]
        picked = marks.picked_plane() == name
        # the picked plane -- the one being drawn on -- gets a soft yellow
        # wash and a bright edge; an idle plane gets no wash and a faint
        # neutral edge, so a plane nobody is using does not shout yellow over
        # the whole viewport
        gpu.state.blend_set('ALPHA')
        shader.bind()
        if picked:
            shader.uniform_float("color", (0.98, 0.86, 0.35, 0.12))
            batch_for_shader(shader, 'TRI_FAN', {"pos": pts}).draw(shader)
            gpu.state.line_width_set(2.5)
            shader.uniform_float("color", (0.98, 0.86, 0.35, 0.95))
            batch_for_shader(shader, 'LINE_LOOP', {"pos": pts}).draw(shader)
            gpu.state.line_width_set(1.0)
        else:
            shader.uniform_float("color", (0.60, 0.62, 0.66, 0.30))
            batch_for_shader(shader, 'LINE_LOOP', {"pos": pts}).draw(shader)
        gpu.state.blend_set('NONE')
        blf.size(font, 12)
        blf.position(font, pts[2][0] + 4, pts[2][1] + 4, 0)
        if picked:
            blf.color(font, 0.98, 0.86, 0.35, 1.0)
        else:
            blf.color(font, 0.66, 0.68, 0.72, 0.7)
        blf.draw(font, name)


def _draw_handle(context, to_2d) -> None:
    """The push/pull arrow on the picked face: a line along the normal and a
    round tip to grab. Only while that face is still the marks."""
    from ..viewport import marks

    marks.HANDLE_TIP.clear()
    marks.FACE_TIPS.clear()
    marks.FACE_LABELS.clear()
    handle = marks.HANDLE
    body = sync.body()
    if not handle or body is None or sync.selected_face_names() != [handle["face"]]:
        return
    size = max((max(ob.dimensions) for ob in sync.bodies()), default=0.0) or 10.0
    o = mathutils.Vector(handle["origin"])
    n = mathutils.Vector(handle["normal"])
    start, tip = to_2d(o), to_2d(o + n * size * 0.2)
    if start is None or tip is None:
        return
    _leader((start.x, start.y), (tip.x, tip.y))
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    steps = 20
    ring = [(tip.x + 9 * math.cos(2 * math.pi * i / steps), tip.y + 9 * math.sin(2 * math.pi * i / steps))
            for i in range(steps)]
    batch = batch_for_shader(shader, 'TRI_FAN', {"pos": [(tip.x, tip.y)] + ring + [ring[0]]})
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_float("color", (0.98, 0.86, 0.35, 0.9))
    batch.draw(shader)
    gpu.state.blend_set('NONE')
    marks.HANDLE_TIP[:] = [tip.x, tip.y, 14.0]
    _word("arrow", MARK_WORD["arrow"], (tip.x + 13, tip.y - 6), (0.98, 0.86, 0.35, 1.0))
    _draw_face_offers(start, shader)


#: what each mark on a picked face is called, beside it, in its own colour
MARK_WORD = {"arrow": "Push / Pull", "draw": "Draw on it", "plane": "Offset plane"}


def _word(kind: str, text: str, at, colour, align_right: bool = False) -> None:
    """A mark's word beside it, and the box a press on the word lands in."""
    from ..viewport import marks

    font = 0
    blf.size(font, 12)
    width, height = blf.dimensions(font, text)
    x = at[0] - width if align_right else at[0]
    blf.position(font, x, at[1], 0)
    blf.color(font, *colour)
    blf.draw(font, text)
    marks.FACE_LABELS[kind] = (x - 3, at[1] - 3, width + 6, height + 6)


#: the pencil that draws on the picked face, and the plane that comes off it
DRAW_ON_IT = (0.35, 0.75, 1.0, 0.95)
PLANE_OFF_IT = (0.98, 0.86, 0.35, 0.95)


def _draw_face_offers(at, shader) -> None:
    """The two other things a picked flat face offers, beside its arrow.

    A pencil and a plane, drawn as what they are rather than named in a menu
    somebody has to know to open. Both sit below the face's middle, where the
    arrow is not.
    """
    from ..viewport import marks

    left = (at.x - 40, at.y - 28)
    right = (at.x + 40, at.y - 28)
    gpu.state.blend_set('ALPHA')
    shapes = {"draw": (left, _pencil(left, 9.0), DRAW_ON_IT),
              "plane": (right, _plane_mark(right, 9.0), PLANE_OFF_IT)}
    for kind in marks.FACE_HANDLES:
        spot, shape, colour = shapes[kind]
        batch = batch_for_shader(shader, 'TRI_FAN', {"pos": shape})
        shader.bind()
        shader.uniform_float("color", colour)
        batch.draw(shader)
        marks.FACE_TIPS[kind] = (spot[0], spot[1], 14.0)
        # the word sits outside the pair, away from the other mark
        outward = -1.0 if kind == "draw" else 1.0
        _word(kind, MARK_WORD[kind], (spot[0] + outward * 13, spot[1] - 6), colour,
              align_right=(outward < 0))
    gpu.state.blend_set('NONE')


def _pencil(at, size: float) -> list:
    """A pencil, tip down-left: a slanted body and a point."""
    x, y = at
    s = size
    body = [(x - s, y - s), (x - s * 0.45, y - s), (x + s, y + s * 0.55),
            (x + s * 0.55, y + s), (x - s, y - s * 0.45)]
    return [at] + body + [body[0]]


def _plane_mark(at, size: float) -> list:
    """A square seen at an angle: a work plane."""
    x, y = at
    s = size
    corners = [(x - s, y - s * 0.45), (x + s * 0.35, y - s),
               (x + s, y + s * 0.45), (x - s * 0.35, y + s)]
    return [at] + corners + [corners[0]]


def _draw_edge_handle(context, to_2d) -> None:
    """The grips on the picked edges: one per thing that can be done to them.

    A green disc rounds the edge and an orange cut corner takes the corner
    off, side by side at the end of one leader. Two shapes rather than one
    shape and a gesture: what a person can see, they can find. While a drag
    runs only the grip it has hold of is drawn.
    """
    from ..viewport import marks

    marks.EDGE_TIPS.clear()
    marks.EDGE_LABELS.clear()
    handle = marks.EDGE_HANDLE
    across = max((max(ob.dimensions) for ob in sync.bodies()), default=0.0)
    if not handle or across <= 0.0:
        return
    o = mathutils.Vector(handle["origin"])
    n = mathutils.Vector(handle["normal"])
    start, away = to_2d(o), to_2d(o + n * across * 0.1)
    if start is None or away is None:
        return
    # a fixed distance off the edge on screen, whatever the part's size or
    # the zoom: far enough to clear the edge, near enough to be its grips
    along = mathutils.Vector((away.x - start.x, away.y - start.y))
    along = along.normalized() if along.length > 1e-6 else mathutils.Vector((0.0, 1.0))
    tip = mathutils.Vector((start.x, start.y)) + along * GRIP_LEAD
    running = marks.DRAGGING[0]
    kinds = [running] if running in GRIP_COLOUR else list(marks.KINDS)
    _leader((start.x, start.y), (tip.x, tip.y),
            GRIP_COLOUR[kinds[0] if len(kinds) == 1 else "fillet"])
    across_2d = mathutils.Vector((-along.y, along.x))
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    font = 0
    blf.size(font, 12)
    for index, kind in enumerate(kinds):
        offset = across_2d * (GRIP_SPREAD * (2 * index - 1) if len(kinds) == 2 else 0.0)
        at = (tip.x + offset.x, tip.y + offset.y)
        shape = (_round_grip(at, GRIP_SIZE) if kind == "fillet"
                 else _cut_corner_grip(at, GRIP_SIZE))
        batch = batch_for_shader(shader, 'TRI_FAN', {"pos": shape})
        shader.bind()
        shader.uniform_float("color", GRIP_COLOUR[kind])
        batch.draw(shader)
        marks.EDGE_TIPS[kind] = (at[0], at[1], GRIP_HIT)
        if len(kinds) == 2:
            # the word is the grip's meaning, and pressing it is pressing the grip
            word = GRIP_WORD[kind]
            width, height = blf.dimensions(font, word)
            side = 1.0 if index == 1 else -1.0
            wx = at[0] + side * (GRIP_SIZE + 6) - (0.0 if side > 0 else width)
            wy = at[1] - height / 2
            blf.position(font, wx, wy, 0)
            blf.color(font, *GRIP_COLOUR[kind])
            blf.draw(font, word)
            marks.EDGE_LABELS[kind] = (wx - 3, wy - 3, width + 6, height + 6)
    gpu.state.blend_set('NONE')
    # no name: a length is what the part measures, not a number to type over
    if "length_mm" in handle:
        font = 0
        blf.size(font, 13)
        _label(font, start.x + 10, start.y - 18, "%.2f mm" % handle["length_mm"], "")


def _round_grip(at, radius: float, steps: int = 20) -> list:
    """A disc: what a rounded edge looks like end on."""
    ring = [(at[0] + radius * math.cos(2 * math.pi * i / steps),
             at[1] + radius * math.sin(2 * math.pi * i / steps)) for i in range(steps)]
    return [at] + ring + [ring[0]]


def _cut_corner_grip(at, radius: float) -> list:
    """A square with its top right corner taken off: a chamfer, end on."""
    x, y = at
    r = radius
    corners = [(x - r, y - r), (x + r, y - r), (x + r, y + r * 0.2),
               (x + r * 0.2, y + r), (x - r, y + r)]
    return [at] + corners + [corners[0]]


#: the ring a plane is turned on: where it started, and where it is now
RING_ZERO = (0.98, 0.86, 0.35, 0.5)
RING_NOW = (0.98, 0.86, 0.35, 1.0)


def _draw_ring(to_2d) -> None:
    """The ring a work plane is being turned on: the circle it swings round,
    a faint spoke at the face it started from and a bright one at the angle."""
    from ..viewport import marks

    ring = marks.RING[0]
    if not ring:
        return
    origin = mathutils.Vector(ring["origin"])
    axis = mathutils.Vector(ring["normal"])
    x = mathutils.Vector(ring["x_axis"])
    y = axis.cross(x)
    radius = ring["radius"]

    def at(degrees, scale=1.0):
        turn = math.radians(degrees)
        return origin + (x * math.cos(turn) + y * math.sin(turn)) * radius * scale

    circle = [to_2d(at(step * 360.0 / 48)) for step in range(49)]
    if any(p is None for p in circle):
        return
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    _strip(shader, [(p.x, p.y) for p in circle], RING_ZERO)
    for degrees, colour in ((0.0, RING_ZERO), (ring["angle"], RING_NOW)):
        spoke = [to_2d(origin), to_2d(at(degrees))]
        if all(p is not None for p in spoke):
            _strip(shader, [(p.x, p.y) for p in spoke], colour)
    gpu.state.blend_set('NONE')


def _draw_badge() -> None:
    """The value a drag is at, beside the cursor rather than in the header."""
    from ..viewport import marks

    badge = marks.BADGE[0]
    if not badge:
        return
    text, x, y = badge
    font = 0
    blf.size(font, 15)
    why = marks.BADGE_REFUSED[0]
    if why:
        # the shape on screen is the last one that built; the cursor has gone
        # past what it can take, and letting go keeps what is on the screen
        _label(font, x, y + 22, text, "", colour=(1.0, 0.45, 0.4, 1.0))
        blf.size(font, 12)
        _label(font, x, y, "will not build: %s -- let go to keep this" % why, "",
               colour=(1.0, 0.45, 0.4, 1.0))
        return
    _label(font, x, y, text, "")


def _label(font, x, y, text, name, colour=(0.98, 0.86, 0.35, 1.0)) -> float:
    """Draw one label box at (x, y); return its height."""
    width, height = blf.dimensions(font, text)
    _panel(x - 4, y - 4, width + 8, height + 8)
    blf.position(font, x, y, 0)
    blf.color(font, *colour)
    blf.draw(font, text)
    if name:
        LABELS.append((x - 4, y - 4, width + 8, height + 8, name))
    return height


def draw(context) -> None:
    from ..viewport import marks

    LABELS.clear()
    marks.forget_sketch()
    props = getattr(context.scene, "cadcore", None)
    if props is None or not props.show_dimensions:
        return
    to_2d_early = lambda p: view3d_utils.location_3d_to_region_2d(  # noqa: E731
        context.region, context.region_data, p)
    _draw_hover(context, props, to_2d_early)
    _draw_snap(context, props, to_2d_early)
    _draw_changes(context, props, to_2d_early)
    _draw_ring(to_2d_early)
    _draw_badge()
    if not (0 <= props.feature_index < len(props.features)):
        return
    feature = props.features[props.feature_index].name
    font = 0
    blf.size(font, 13)
    to_2d = lambda p: view3d_utils.location_3d_to_region_2d(  # noqa: E731
        context.region, context.region_data, p)

    # the feature's own numbers, beside the faces it made
    arguments = [a for a in props.feature_args if a.kind == "number"]
    anchor = _anchor(feature) if arguments else None
    position = to_2d(anchor) if anchor is not None else None
    if position is not None:
        x, y = position.x + 18, position.y + 6 + 9 * len(arguments)
        for argument in arguments:
            text = "%s  %.3g" % (argument.name, argument.value)
            y -= _label(font, x, y, text, argument.parameter or argument.name) + 10
        _leader((position.x, position.y), (x - 6, position.y + 6))

    _draw_planes(context, to_2d)
    _draw_handle(context, to_2d)
    _draw_edge_handle(context, to_2d)

    # the sketch's dimensions, on the lines they measure
    sketch = _sketch_for(props)
    if sketch is None:
        return
    data = _sketch_data(context, props, sketch)
    if data is None:
        return
    _draw_sketch_shape(context, data[0], data[1], to_2d)
    values = {p.name: p.value for p in props.parameters}
    for label in sketch_labels(data[0], data[1], values):
        at = to_2d(label["at"])
        if at is None:
            continue
        ends = [to_2d(e) for e in label["ends"]]
        if all(e is not None for e in ends):
            _leader((ends[0].x, ends[0].y), (ends[1].x, ends[1].y))
        _label(font, at.x + 6, at.y + 6, label["text"], label["name"])


#: what each constraint is drawn as, beside the thing it holds. A mark rather
#: than a letter: a letter reads as a key to press, which is what this is for
#: getting rid of.
MARKS = {"horizontal": "\u2014", "vertical": "|", "parallel": "//",
         "perpendicular": "\u2310", "equal_length": "=", "tangent": "~",
         "coincident": "\u2022", "fix": "\u00d7", "concentric": "\u25ce",
         "symmetric": "><", "radius": "R", "angle": "\u2220"}


def _middle(geometry: dict, constraint: dict):
    """Where a constraint's mark goes, in the sketch's own (u, v)."""
    points, segments = geometry["points"], geometry["segments"]
    named = [constraint[k] for k in ("line", "point") if k in constraint]
    named += list(constraint.get("lines") or constraint.get("points") or [])
    spots = []
    for name in named:
        if name in points:
            spots.append(points[name])
            continue
        segment = next((s for s in segments if s["name"] == name), None)
        if segment is None:
            continue
        if segment["kind"] == "line":
            spots.append([(segment["start"][i] + segment["end"][i]) / 2 for i in range(2)])
        elif segment.get("centre"):
            spots.append(list(segment["centre"]))
    if not spots:
        return None
    return [sum(s[i] for s in spots) / len(spots) for i in range(2)]


def _draw_sketch_shape(context, geometry: dict, constraints: list, to_2d) -> None:
    """The selected sketch, where it lies: its lines, its points, its holds.

    A point the constraints leave free is white and a held one amber. Where
    each landed is written back to `pick`, in region pixels, because these are
    what the pick tool clicks: the sketch is picked on the part, not in a mode
    of its own.
    """
    from ..viewport import marks

    plane = geometry["plane"]
    o, n, x = plane["origin"], plane["normal"], plane["x_axis"]
    y = (n[1] * x[2] - n[2] * x[1], n[2] * x[0] - n[0] * x[2], n[0] * x[1] - n[1] * x[0])

    def to_3d(uv):
        return mathutils.Vector([o[i] + uv[0] * x[i] + uv[1] * y[i] for i in range(3)])

    def flatten(uvs):
        """A run of (u, v) as region pixels, or None if any of it is off screen."""
        out = [to_2d(to_3d(p)) for p in uvs]
        return None if any(p is None for p in out) else [(p.x, p.y) for p in out]

    marks.SKETCH[0] = geometry["sketch"]
    picked = {name for _, name in marks.sketch_picks()}
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(2.0)
    for segment in geometry["segments"]:
        uvs = _segment_points(segment)
        if uvs is None:
            continue
        flat = flatten(uvs)
        if flat is None:
            continue
        marks.SKETCH_LINES.append((segment["name"], flat))
        batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": flat})
        shader.bind()
        shader.uniform_float("color", PICKED_ON_SKETCH if segment["name"] in picked
                             else (0.35, 0.75, 1.0, 0.9))
        batch.draw(shader)
    free = geometry.get("free", {})
    spots: dict = {}
    for name, uv in geometry["points"].items():
        at = to_2d(to_3d(uv))
        if at is not None:
            spots[name] = (at.x, at.y)
            marks.SKETCH_POINTS.append((at.x, at.y, name))
    for movable, colour, size in ((True, (1.0, 1.0, 1.0, 1.0), 7.0),
                                  (False, (1.0, 0.62, 0.15, 1.0), 5.0)):
        at = [p for name, p in spots.items()
              if name not in picked and bool(free.get(name, True)) is movable]
        if at:
            gpu.state.point_size_set(size)
            batch = batch_for_shader(shader, 'POINTS', {"pos": at})
            shader.bind()
            shader.uniform_float("color", colour)
            batch.draw(shader)
    at = [p for name, p in spots.items() if name in picked]
    if at:
        gpu.state.point_size_set(11.0)
        batch = batch_for_shader(shader, 'POINTS', {"pos": at})
        shader.bind()
        shader.uniform_float("color", PICKED_ON_SKETCH)
        batch.draw(shader)
    _draw_sketch_drag(geometry, spots, flatten, shader)
    gpu.state.blend_set('NONE')

    font = 0
    blf.size(font, 11)
    blf.color(font, 0.55, 0.85, 1.0, 1.0)
    for constraint in constraints:
        mark = MARKS.get(constraint.get("type"))
        if mark is None:                   # a distance is drawn as its number
            continue
        uv = _middle(geometry, constraint)
        at = to_2d(to_3d(uv)) if uv else None
        if at is None:
            continue
        blf.position(font, at.x + 5, at.y + 5, 0)
        blf.draw(font, mark)


#: what the cursor is over, before the click: one colour for all of it, so
#: "this is what you are about to get" reads the same wherever it appears
HOVERED = (0.35, 1.0, 0.65, 1.0)
_hover_shapes: dict = {}     # (what, name, body, undo depth) -> world-space geometry


def _draw_hover(context, props, to_2d) -> None:
    """What a click would land on, shown before it lands.

    A face glows faintly, an edge thickens, a corner and a sketch point grow,
    a number gets a box round it, a handle gets a ring. One colour for all of
    them: the question being answered is always the same one.
    """
    from ..viewport import marks

    hover = marks.HOVER
    what = hover.get("what")
    if not what:
        return
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    if what == "handle":
        x, y, radius = hover["at"]
        _ring(shader, (x, y), radius + 4)
    elif what == "label":
        left, bottom, width, height = hover["box"]
        _outline(shader, [(left, bottom), (left + width, bottom),
                          (left + width, bottom + height), (left, bottom + height)])
    elif what == "point" and marks.SKETCH_POINTS:
        for px, py, name in marks.SKETCH_POINTS:
            if name == hover["name"]:
                _ring(shader, (px, py), 9.0)
    elif what == "line" and marks.SKETCH_LINES:
        for name, line in marks.SKETCH_LINES:
            if name == hover["name"]:
                gpu.state.line_width_set(4.0)
                _strip(shader, line, HOVERED)
                gpu.state.line_width_set(2.0)
    elif what == "corner":
        at = to_2d(mathutils.Vector(hover["at3"]))
        if at is not None:
            _ring(shader, (at.x, at.y), 8.0)
    elif what in ("face", "edge"):
        shape = _hovered_shape(context, props, hover)
        flat = [[to_2d(p) for p in run] for run in shape]
        flat = [[(p.x, p.y) for p in run] for run in flat if all(p is not None for p in run)]
        if what == "edge":
            gpu.state.line_width_set(5.0)
            for run in flat:
                _strip(shader, run, HOVERED)
            gpu.state.line_width_set(2.0)
        else:
            for run in flat:
                batch = batch_for_shader(shader, 'TRI_FAN', {"pos": run})
                shader.bind()
                shader.uniform_float("color", HOVERED[:3] + (0.14,))
                batch.draw(shader)
    gpu.state.blend_set('NONE')


#: what the assistant just did: new faces, faces that went, faces that moved
ADDED = (0.30, 0.90, 0.45, 1.0)
GONE = (1.0, 0.35, 0.35, 1.0)
MOVED = (0.98, 0.86, 0.35, 1.0)


def _draw_changes(context, props, to_2d) -> None:
    """The assistant's last step, on the part: new faces green, faces that
    went as a red ghost of the shape they had, moved faces with an arrow,
    and the numbers that changed, old -> new. With an Undo to press."""
    from ..viewport import marks
    from . import changes

    marks.UNDO_BUTTON.clear()
    if not changes.showing():
        return
    shown = changes.SHOWING
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    for name in shown["added"]:
        body = _body_carrying(name)
        if body is None:
            continue
        for run in _hovered_shape(context, props, {"what": "face", "name": name,
                                                   "body": body.name}):
            _fill(shader, to_2d, run, ADDED[:3] + (0.30,), ADDED)
    for run in shown["gone"]:
        _fill(shader, to_2d, run, GONE[:3] + (0.18,), GONE[:3] + (0.6,))
    for start, end in shown["moved"]:
        a, b = to_2d(mathutils.Vector(start)), to_2d(mathutils.Vector(end))
        if a is not None and b is not None:
            _leader((a.x, a.y), (b.x, b.y), MOVED)
            _ring(shader, (b.x, b.y), 5.0)
    gpu.state.blend_set('NONE')
    font = 0
    blf.size(font, 13)
    x, y = 24, 24
    words = "the assistant: " + changes.summary()
    width, height = blf.dimensions(font, words)
    _label(font, x, y, words, "")
    bx = x + width + 18
    _label(font, bx, y, "Undo", "", colour=(1.0, 1.0, 1.0, 1.0))
    bw, bh = blf.dimensions(font, "Undo")
    marks.UNDO_BUTTON[:] = [bx - 4, y - 4, bw + 8, bh + 8]


def _fill(shader, to_2d, run, inside, edge) -> None:
    flat = [to_2d(p) for p in run]
    if any(p is None for p in flat):
        return
    points = [(p.x, p.y) for p in flat]
    batch = batch_for_shader(shader, 'TRI_FAN', {"pos": points})
    shader.bind()
    shader.uniform_float("color", inside)
    batch.draw(shader)
    _strip(shader, points + [points[0]], edge)


#: where a dragged part would settle: the face it is about to sit against
SNAPPED = (0.35, 0.75, 1.0, 1.0)


def _draw_snap(context, props, to_2d) -> None:
    """The face a part being dragged would settle against, lit while it would.

    The part itself is already there -- the preview is the real mate -- so
    this says which face is holding it, which is the thing that will still be
    true tomorrow when the other part changes size.
    """
    from ..viewport import marks

    face = marks.SNAP[0]
    if not face:
        return
    body = _body_carrying(face)
    if body is None:
        return
    shape = _hovered_shape(context, props,
                           {"what": "face", "name": face, "body": body.name})
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    for run in shape:
        flat = [to_2d(p) for p in run]
        if any(p is None for p in flat):
            continue
        points = [(p.x, p.y) for p in flat]
        batch = batch_for_shader(shader, 'TRI_FAN', {"pos": points})
        shader.bind()
        shader.uniform_float("color", SNAPPED[:3] + (0.25,))
        batch.draw(shader)
        _strip(shader, points + [points[0]], SNAPPED)
    gpu.state.blend_set('NONE')


def _body_carrying(face: str):
    """The object whose face table has this CAD face on it."""
    for body in sync.bodies():
        table = body.get("cad_face_table")
        if table and face in json.loads(table):
            return body
    return None


def _ring(shader, at, radius: float, steps: int = 24) -> None:
    points = [(at[0] + radius * math.cos(2 * math.pi * i / steps),
               at[1] + radius * math.sin(2 * math.pi * i / steps)) for i in range(steps)]
    _strip(shader, points + [points[0]], HOVERED)


def _outline(shader, corners) -> None:
    _strip(shader, list(corners) + [corners[0]], HOVERED)


def _strip(shader, points, colour) -> None:
    if len(points) < 2:
        return
    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": points})
    shader.bind()
    shader.uniform_float("color", colour)
    batch.draw(shader)


def _hovered_shape(context, props, hover) -> list:
    """The world-space runs that draw the hovered face or edge, worked out
    once and kept until the model or the target changes."""
    key = (hover["what"], hover["name"], hover.get("body"), props.undo_depth,
           round(props.volume, 3))
    if key in _hover_shapes:
        return _hover_shapes[key]
    body = bpy.data.objects.get(hover.get("body") or "")
    runs: list = []
    if body is not None and body.get("cad_face_table"):
        table = json.loads(body["cad_face_table"])
        if hover["what"] == "face" and hover["name"] in table:
            runs = _face_fans(body, {table.index(hover["name"])})
        elif hover["what"] == "edge":
            runs = _edge_runs(body, table, tuple(hover["pair"]))
    if len(_hover_shapes) > 8:
        _hover_shapes.clear()
    _hover_shapes[key] = runs
    return runs


def _face_fans(body, wanted: set) -> list:
    """One fan of world-space corners per polygon of the wanted CAD faces."""
    out = []
    if body.mode == 'EDIT':
        import bmesh

        bm = bmesh.from_edit_mesh(body.data)
        layer = bm.faces.layers.int.get("cad_face")
        if layer is None:
            return out
        for face in bm.faces:
            if face[layer] in wanted:
                out.append([body.matrix_world @ v.co for v in face.verts])
        return out
    attribute = body.data.attributes.get("cad_face")
    if attribute is None:
        return out
    for polygon in body.data.polygons:
        if attribute.data[polygon.index].value in wanted:
            out.append([body.matrix_world @ body.data.vertices[i].co
                        for i in polygon.vertices])
    return out


def _edge_runs(body, table: list, pair: tuple) -> list:
    """The mesh edges that make up the CAD edge between those two faces."""
    if body.mode != 'EDIT':
        return []
    import bmesh

    bm = bmesh.from_edit_mesh(body.data)
    layer = bm.faces.layers.int.get("cad_face")
    if layer is None:
        return []
    sides = sync.rim_map(bm, table, layer)
    out = []
    for edge in bm.edges:
        if not edge.is_boundary:
            continue
        if tuple(sorted(sides.get(sync.edge_key(edge), ()))) == tuple(sorted(pair)):
            out.append([body.matrix_world @ v.co for v in edge.verts])
    return out


#: what a click has hold of on the sketch, points and lines alike
PICKED_ON_SKETCH = (1.0, 0.35, 0.85, 1.0)
#: and where a dragged point is being taken
DRAGGED_ON_SKETCH = (0.2, 1.0, 0.5, 1.0)


def _segment_points(segment):
    """A segment as a run of (u, v) to draw, or None for one with no shape."""
    if segment["kind"] == "line":
        return [segment["start"], segment["end"]]
    if not segment.get("centre"):
        return None
    if segment["kind"] == "circle":
        return _circle_points(segment["centre"], segment["radius"])
    if segment["kind"] == "arc":
        return _arc_points(segment)
    return None


def _draw_sketch_drag(geometry, spots, flatten, shader) -> None:
    """While a point is being dragged: a line from where it is to the cursor."""
    from ..viewport import marks

    held = marks.SKETCH_DRAG[0]
    if held is None or held[0] not in geometry["points"]:
        return
    name, cursor = held
    line = flatten([geometry["points"][name], cursor])
    if line is None:
        return
    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": line})
    shader.bind()
    shader.uniform_float("color", DRAGGED_ON_SKETCH)
    batch.draw(shader)
    gpu.state.point_size_set(13.0)
    batch = batch_for_shader(shader, 'POINTS', {"pos": [line[1]]})
    shader.bind()
    shader.uniform_float("color", DRAGGED_ON_SKETCH)
    batch.draw(shader)


def _arc_points(segment, steps: int = 24) -> list:
    """An arc as (u, v), the short way round or the long one as `ccw` says."""
    centre, radius = segment["centre"], segment["radius"]
    start = math.atan2(segment["start"][1] - centre[1], segment["start"][0] - centre[0])
    end = math.atan2(segment["end"][1] - centre[1], segment["end"][0] - centre[0])
    if segment["ccw"] and end <= start:
        end += 2 * math.pi
    if not segment["ccw"] and end >= start:
        end -= 2 * math.pi
    return [(centre[0] + radius * math.cos(start + (end - start) * k / steps),
             centre[1] + radius * math.sin(start + (end - start) * k / steps))
            for k in range(steps + 1)]


def _circle_points(centre, radius, steps: int = 48) -> list:
    return [(centre[0] + radius * math.cos(2 * math.pi * i / steps),
             centre[1] + radius * math.sin(2 * math.pi * i / steps))
            for i in range(steps + 1)]


def _panel(x, y, width, height) -> None:
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRI_FAN', {"pos": [
        (x, y), (x + width, y), (x + width, y + height), (x, y + height)]})
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_float("color", (0.06, 0.06, 0.07, 0.72))
    batch.draw(shader)
    gpu.state.blend_set('NONE')


def _leader(start, end, colour=(0.98, 0.86, 0.35, 0.8)) -> None:
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'LINES', {"pos": [start, end]})
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_float("color", colour)
    batch.draw(shader)
    gpu.state.blend_set('NONE')


def enable() -> None:
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(
            draw, (bpy.context,), 'WINDOW', 'POST_PIXEL')


def disable() -> None:
    from ..viewport import marks

    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
    marks.forget_sketch()          # nothing is drawn, so nothing on it is clickable


class CADCORE_OT_show_dimensions(bpy.types.Operator):
    bl_idname = "cadcore.show_dimensions"
    bl_label = "Dimensions on the model"
    bl_description = "Draw the selected feature's dimensions beside the geometry"

    def execute(self, context):
        props = context.scene.cadcore
        props.show_dimensions = not props.show_dimensions
        enable() if props.show_dimensions else disable()
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return {'FINISHED'}


class CADCORE_OT_edit_dimension(bpy.types.Operator):
    bl_idname = "cadcore.edit_dimension"
    bl_label = "Edit Dimension"
    bl_description = "Type a new value for one dimension"

    name: bpy.props.StringProperty()
    value: bpy.props.FloatProperty(name="Value")

    def invoke(self, context, event):
        props = context.scene.cadcore
        for argument in props.feature_args:
            if (argument.parameter or argument.name) == self.name:
                self.value = argument.value
                break
        else:
            for parameter in props.parameters:
                if parameter.name == self.name:
                    self.value = parameter.value
        return context.window_manager.invoke_props_dialog(self, width=240)

    def draw(self, context):
        self.layout.prop(self, "value", text=self.name)

    def execute(self, context):
        props = context.scene.cadcore
        for argument in props.feature_args:
            if (argument.parameter or argument.name) == self.name:
                argument.value = self.value      # the update callback rebuilds
                return {'FINISHED'}
        for parameter in props.parameters:        # a sketch dimension is a parameter
            if parameter.name == self.name:
                parameter.value = self.value
                return {'FINISHED'}
        self.report({'ERROR'}, "no dimension called %r on this feature" % self.name)
        return {'CANCELLED'}


class CADCORE_OT_pick_dimension(bpy.types.Operator):
    bl_idname = "cadcore.pick_dimension"
    bl_label = "Click a Dimension"
    bl_description = "Click one of the numbers on the model to change it"

    def invoke(self, context, event):
        if not context.scene.cadcore.show_dimensions:
            bpy.ops.cadcore.show_dimensions()
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Click a dimension to change it | Esc to stop")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            context.area.header_text_set(None)
            return {'FINISHED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            x, y = event.mouse_region_x, event.mouse_region_y
            for left, bottom, width, height, name in LABELS:
                if left <= x <= left + width and bottom <= y <= bottom + height:
                    context.area.header_text_set(None)
                    return bpy.ops.cadcore.edit_dimension('INVOKE_DEFAULT', name=name)
        return {'PASS_THROUGH'}
