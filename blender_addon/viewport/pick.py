"""The pick: a click on the part picks a face, an edge or a corner, and the
mode follows. A picked flat face carries an arrow to push or pull it."""
from __future__ import annotations

import bpy
import mathutils

from ..link import sync
from ..link import names
from ..link.client import ServerError
from ..link import state
from ..link.state import get_client


from .marks import (  # noqa: F401  -- the marks live there; these names stay usable here
    BADGE, BADGE_REFUSED, CYCLE, DRAGGING, EDGE_HANDLE, EDGE_LABELS, EDGE_TIPS, FACE_LABELS, HINTED, FACE_HANDLES, FACE_TIPS, HANDLE, HANDLE_TIP, HOVER, HOVER_AT, KINDS, LABELS, PICKED_ON, PICKED_PLANE, PICKED_SKETCH, PLANES, RING, SKETCH, SKETCH_BEHIND, SKETCH_DRAG, SKETCH_LINES, SKETCH_POINTS, SNAP, UNDO_BUTTON, _point_to_segment, forget_hover, forget_sketch, forget_sketch_picks, on_sketch_at, pick_on_sketch, picked_plane, plane_size, remember_plane, sketch_picks, step_out)


def _face_sketch(context, sketch: str) -> bool:
    """Focus the clicked sketch; consume the click if the view moved."""
    from .modal import align_to_plane

    frame = get_client(context).call("sketch_geometry", sketch=sketch)["plane"]
    view = context.region_data
    rotation = view.view_rotation.copy()
    location = view.view_location.copy()
    perspective = view.view_perspective
    align_to_plane(context, frame)
    return (rotation.rotation_difference(view.view_rotation).angle > 1e-5
            or (location - view.view_location).length > 1e-5
            or perspective != 'ORTHO')


def _face_handle(kind):
    """The operator a handle beside the picked face starts."""
    return {"draw": bpy.ops.cadcore.draw_sketch,
            "plane": bpy.ops.cadcore.offset_plane}[kind]


def remember_edge_handle(context, body) -> None:
    """Where the grip on the picked edges goes. Nothing picked clears it.

    The mesh is triangulated per CAD face and not welded across a CAD edge, so
    the two faces the grip leans between are found by name, not by asking the
    mesh edge what it touches -- it touches one.
    """
    import bmesh
    import json

    EDGE_HANDLE.clear()
    EDGE_TIPS.clear()
    EDGE_LABELS.clear()
    if body is None or body.mode != 'EDIT' or not body.get("cad_face_table"):
        return
    pairs = sync.picked(body)[1]
    if not pairs:
        return
    bm = bmesh.from_edit_mesh(body.data)
    chosen = [e for e in bm.edges if e.select]
    layer = bm.faces.layers.int.get("cad_face")
    if not chosen or layer is None:
        return
    # the longest of them: a pick takes a run of short mesh edges along the
    # rim, and the grip belongs where there is room to see it
    edge = max(chosen, key=lambda e: (e.verts[0].co - e.verts[1].co).length)
    middle = (edge.verts[0].co + edge.verts[1].co) / 2
    table = json.loads(body["cad_face_table"])
    away = mathutils.Vector((0.0, 0.0, 0.0))
    for name in pairs[0]:
        face = next((f for f in bm.faces if table[f[layer]] == name), None)
        if face is not None:
            away += face.normal
    if away.length < 1e-6:                  # the two faces fold back on each other
        return
    EDGE_HANDLE.update({
        "origin": tuple(body.matrix_world @ middle),
        "normal": tuple((body.matrix_world.to_3x3() @ away).normalized())})
    # the mesh is a polyline along a curved rim, and the sum of its segments is
    # shorter than the arc. A straight run is the mesh exactly, so that is the
    # only one whose length is shown
    runs = [body.matrix_world.to_3x3() @ (e.verts[1].co - e.verts[0].co) for e in chosen]
    runs = [d for d in runs if d.length > 1e-9]
    if runs and all(abs(abs(d.normalized().dot(runs[0].normalized())) - 1.0) < 1e-4
                    for d in runs):
        EDGE_HANDLE["length_mm"] = sum(d.length for d in runs)
        # straight, so it has one direction: a work plane can hang on it and turn
        EDGE_HANDLE["along"] = tuple(runs[0].normalized())


def show_the_feature_behind(context, face: str) -> None:
    """Select in the history the feature that made this face.

    Picking a face is how a person points at a feature; asking them to find
    it again in a list is the step this removes. The numbers of that feature
    -- and the sketch it was made from -- are then drawn on the part.
    """
    props = getattr(context.scene, "cadcore", None)
    SKETCH_BEHIND[0] = SKETCH_BEHIND[1] = None
    if props is None or not face:
        return
    made_by = names.feature_of(face)
    for index, item in enumerate(props.features):
        if item.name == made_by:
            if props.feature_index != index:
                props.feature_index = index
            try:
                SKETCH_BEHIND[:] = [item.name, get_client(context).call(
                    "sketch_of", face=face)["sketch"]]
            except ServerError:
                pass                    # nothing sketched made it; there is no sketch
            return


def remember_handle(context, face: str | None) -> None:
    """After a face pick: where its push/pull arrow goes. None clears it."""
    HANDLE.clear()
    HANDLE_TIP.clear()
    FACE_TIPS.clear()
    FACE_LABELS.clear()
    if not face:
        return
    try:
        frame = get_client(context).call("face_frame", face=face)
    except ServerError:
        return                                  # not planar: no arrow
    HANDLE.update({"face": face, "origin": tuple(frame["origin"]), "normal": tuple(frame["normal"])})


def _what_is_picked_on_the_sketch() -> str:
    """The status line for a click on the sketch: what is picked, and what next."""
    picked = sketch_picks()
    if len(picked) == 1:
        what, name = picked[0]
        if what == "point":
            return "%s -- drag it to move it; right-click for what can hold it" % name
        return "%s -- right-click for what can hold it" % name
    return "%s -- right-click for what can hold them" % ", ".join(
        name for _, name in picked)


def _hand_off(operator, call) -> set:
    """Start one of the drag operators from the grip that was clicked.

    `bpy.ops` turns the callee's reported error into a `RuntimeError` here.
    Letting it out leaves a Python traceback; swallowing it leaves a grip that
    does nothing when clicked, which is worse -- the refusal has a reason and
    the reason is the only way out of it. So it is said again, as this
    operator's own report, and the grip stays where it is: it is what the
    person will click after reading why it did not go.
    """
    try:
        call()
    except RuntimeError as exc:
        operator.report({'ERROR'}, str(exc).replace("Error: ", "", 1))
        return {'CANCELLED'}
    return {'FINISHED'}


def _face_mark_at(x: int, y: int):
    """The mark on the picked face under (x, y): the pencil, the plane, the
    arrow's tip, or the word beside any of them."""
    for kind, (hx, hy, hr) in FACE_TIPS.items():
        if (x - hx) ** 2 + (y - hy) ** 2 <= hr * hr:
            return kind
    if HANDLE_TIP:
        hx, hy, hr = HANDLE_TIP
        if (x - hx) ** 2 + (y - hy) ** 2 <= hr * hr:
            return "arrow"
    for kind, (left, bottom, width, height) in FACE_LABELS.items():
        if left <= x <= left + width and bottom <= y <= bottom + height:
            return kind
    return None


def _grip_at(x: int, y: int):
    """The edge grip under (x, y): its disc, or the word beside it."""
    for kind, (ex, ey, er) in EDGE_TIPS.items():
        if (x - ex) ** 2 + (y - ey) ** 2 <= er * er:
            return kind
    for kind, (left, bottom, width, height) in EDGE_LABELS.items():
        if left <= x <= left + width and bottom <= y <= bottom + height:
            return kind
    return None


#: what a handle does, said in the header while the cursor is on it
HINTS = {"fillet": "Drag to round the edge -- type a number for an exact radius, Esc drops it",
         "chamfer": "Drag to cut the corner off -- type a number for the size, Esc drops it",
         "arrow": "Drag to push or pull the face -- type a number for the distance",
         "draw": "Click to draw a shape on this face",
         "plane": "Drag a work plane off this face, and draw on it"}


def _hint(context) -> None:
    """The header describes the handle under the cursor, and only that."""
    name = HOVER.get("name") if HOVER.get("what") == "handle" else None
    if name == HINTED[0] or context.area is None:
        return
    HINTED[0] = name
    context.area.header_text_set(HINTS.get(name) if name else None)


def _look(context, x: int, y: int) -> None:
    """Work out what a click here would land on, and put it in `HOVER`.

    The same order the click itself uses, so what is shown is what happens.
    Nothing is selected and no kernel call is made: this runs on every mouse
    move, and a hover that costs a round trip is a hover that stutters.
    """
    HOVER.clear()
    body = sync.body()
    if body is None:
        return
    kind = _grip_at(x, y)
    if kind is not None:
        HOVER.update({"what": "handle", "name": kind, "at": EDGE_TIPS[kind]})
        return
    kind = _face_mark_at(x, y)
    if kind is not None:
        at = FACE_TIPS.get(kind) or tuple(HANDLE_TIP)
        HOVER.update({"what": "handle", "name": kind, "at": at})
        return
    if HANDLE and HANDLE_TIP:
        hx, hy, hr = HANDLE_TIP
        if (x - hx) ** 2 + (y - hy) ** 2 <= hr * hr:
            HOVER.update({"what": "handle", "name": "arrow", "at": (hx, hy, hr)})
            return
    for left, bottom, width, height, name in LABELS:
        if left <= x <= left + width and bottom <= y <= bottom + height:
            HOVER.update({"what": "label", "name": name,
                          "box": (left, bottom, width, height)})
            return
    on_the_sketch = on_sketch_at(x, y)
    if on_the_sketch is not None:
        HOVER.update({"what": on_the_sketch[0], "name": on_the_sketch[1]})
        return
    face, location, ob = _face_at(context, (x, y))
    if face is None:
        return
    HOVER.update({"what": "face", "name": face, "body": ob.name})
    if ob.mode != 'EDIT':
        return          # corners and edges are read off the edit mesh, and there is none
    step = CYCLE[0]
    corner = None if step else _corner_near(context, ob, location, (x, y), 10)
    if corner is not None:
        HOVER.update({"what": "corner", "name": "corner", "at3": corner[0]})
        return
    edge = None if step > 1 else _rim_edge_near(context, ob, face, (x, y), 10)
    if edge is not None:
        HOVER.update({"what": "edge", "name": names.edge(*edge), "pair": edge,
                      "body": ob.name})


class CADCORE_OT_hover(bpy.types.Operator):
    """Show what a click would land on, before it is clicked.

    Runs on every mouse move the pick tool sees, so it does no more than a
    ray cast and some arithmetic on numbers the overlay already worked out.
    """

    bl_idname = "cadcore.hover"
    bl_label = "Show What Is Under the Cursor"
    bl_options = set()          # neither REGISTER nor UNDO: it is not an edit

    def invoke(self, context, event):
        x, y = event.mouse_region_x, event.mouse_region_y
        if (x - HOVER_AT[0]) ** 2 + (y - HOVER_AT[1]) ** 2 <= 9:
            return {'PASS_THROUGH'}
        HOVER_AT[0], HOVER_AT[1] = x, y
        CYCLE[0] = 0                # the cursor moved: back to the smallest thing
        before = (HOVER.get("what"), HOVER.get("name"))
        _look(context, x, y)
        _hint(context)
        if (HOVER.get("what"), HOVER.get("name")) != before:
            for area in context.screen.areas:
                area.tag_redraw()
        return {'PASS_THROUGH'}


class CADCORE_OT_cycle_pick(bpy.types.Operator):
    """Step the pick out to the next thing under the cursor.

    A corner beats an edge beats a face, which is right until the small thing
    in front is not the one that was wanted. With nothing to step out to this
    passes the key on, so Tab is still Blender's own where it always was.
    """

    bl_idname = "cadcore.cycle_pick"
    bl_label = "Pick the Next Thing Under the Cursor"
    bl_options = set()

    def invoke(self, context, event):
        if not step_out():
            return {'PASS_THROUGH'}      # nothing smaller than the face is in the way
        _look(context, HOVER_AT[0], HOVER_AT[1])
        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


class CADCORE_OT_pick_under_cursor(bpy.types.Operator):
    """Pick the face under the cursor -- or the edge, when the cursor is on
    one -- in whatever mode Blender is in. The mode follows the pick."""

    bl_idname = "cadcore.pick_under_cursor"
    bl_label = "Pick Face or Edge"
    bl_description = "Click a face to pick it; click an edge to pick the edge; Shift adds"
    bl_options = {'REGISTER'}       # not UNDO: a pick must not cost a Ctrl+Z

    add: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})
    sketches_only: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})
    x: bpy.props.IntProperty(default=-1, options={'SKIP_SAVE'})   # region coords, for scripts
    y: bpy.props.IntProperty(default=-1, options={'SKIP_SAVE'})
    near: bpy.props.IntProperty(default=10, description="pixels: closer than this to an edge picks the edge")

    def invoke(self, context, event):
        if self.sketches_only:
            tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
            if tool is not None and not tool.idname.startswith('builtin.select'):
                return {'PASS_THROUGH'}
            coord = (event.mouse_region_x, event.mouse_region_y)
            if on_sketch_at(*coord) is None and _plane_at(context, coord)[0] is None:
                return {'PASS_THROUGH'}
        self.x, self.y = event.mouse_region_x, event.mouse_region_y
        return self.execute(context)

    def execute(self, context):
        if self.x < 0 or context.region_data is None:
            return {'PASS_THROUGH'}
        # the assistant's last change is on the screen: Undo takes it back,
        # and any other click puts the answer away
        from ..ui import changes

        if UNDO_BUTTON and changes.showing():
            left, bottom, width, height = UNDO_BUTTON
            if left <= self.x <= left + width and bottom <= self.y <= bottom + height:
                changes.clear()
                return _hand_off(self, lambda: bpy.ops.cadcore.undo(redo=False))
        changes.clear()
        # the grips on the picked edge: one per thing that can be done to it
        if EDGE_HANDLE and EDGE_TIPS:
            kind = _grip_at(self.x, self.y)
            if kind is not None:
                return _hand_off(self, lambda: bpy.ops.cadcore.drag_fillet(
                    'INVOKE_DEFAULT', kind=kind))
        # what else the picked face offers: the pencil draws on it, the plane
        # pulls one off it. Both are beside the face, not on the toolbar.
        if HANDLE and (FACE_TIPS or FACE_LABELS):
            kind = _face_mark_at(self.x, self.y)
            if kind in FACE_HANDLES:
                start = _face_handle(kind)
                return _hand_off(self, lambda: start('INVOKE_DEFAULT'))
            if kind == "arrow":
                return _hand_off(
                    self, lambda: bpy.ops.cadcore.press_pull('INVOKE_DEFAULT'))
        # the arrow on the picked face: grab it and the drag is a push/pull
        if HANDLE and HANDLE_TIP:
            tx, ty, r = HANDLE_TIP
            if (self.x - tx) ** 2 + (self.y - ty) ** 2 <= r * r:
                # FINISHED, not what press/pull returned: RUNNING_MODAL from an
                # operator with no modal method leaves a handler behind forever
                return _hand_off(
                    self, lambda: bpy.ops.cadcore.press_pull('INVOKE_DEFAULT'))
        # a number drawn on the model, under the cursor: type over it
        for left, bottom, width, height, name in LABELS:
            if left <= self.x <= left + width and bottom <= self.y <= bottom + height:
                return _hand_off(
                    self,
                    lambda: bpy.ops.cadcore.edit_dimension('INVOKE_DEFAULT', name=name))
        # the sketch drawn on the part. A point is grabbed and dragged; a line
        # is picked, for a hold. Both come before the face they lie on.
        hit = on_sketch_at(self.x, self.y)
        if hit is not None:
            try:
                moved = _face_sketch(context, SKETCH[0])
            except ServerError as exc:
                context.scene.cadcore.status = f"{exc.kind}: {exc.message}"
                return {'CANCELLED'}
            pick_on_sketch(hit, self.add)
            context.scene.cadcore.status = _what_is_picked_on_the_sketch()
            for area in context.screen.areas:
                area.tag_redraw()
            if hit[0] == "point" and not self.add and not moved:
                return _hand_off(self, lambda: bpy.ops.cadcore.drag_sketch_point(
                    'INVOKE_DEFAULT', sketch=SKETCH[0], point=hit[1]))
            return {'FINISHED'}
        forget_sketch_picks()
        face, location, body = _face_at(context, (self.x, self.y))
        plane, plane_hit = _plane_at(context, (self.x, self.y))
        if plane is not None and (face is None or _nearer(context, (self.x, self.y), plane_hit, location)):
            sync.select_faces([])
            remember_handle(context, None)
            PICKED_PLANE[0] = plane
            from .modal import align_to_plane
            align_to_plane(context, PLANES[plane])
            context.scene.cadcore.status = "work plane %s -- right-click to draw on it" % plane
            for area in context.screen.areas:
                area.tag_redraw()
            return {'FINISHED'}
        PICKED_PLANE[0] = None
        if self.sketches_only:
            return {'PASS_THROUGH'}
        if face is None:
            if not self.add:
                sync.select_faces([])
                remember_handle(context, None)
            return {'PASS_THROUGH'}
        # the pick decides the mode, not the person. Every part of an
        # assembly goes into edit mode together: a selection that spans two of
        # them is one selection, and only the objects that are selected here
        # are the ones the mode change takes with it
        from . import section

        section.clear(context)      # a clipped view cannot survive edit mode
        drawn = sync.bodies()
        for other in context.view_layer.objects:
            other.select_set(other in drawn)
        context.view_layer.objects.active = body
        if body.mode != 'EDIT':
            bpy.ops.object.mode_set(mode='EDIT')
            # the tool comes too: edit mode holds its own, and without this the
            # click that picked the face leaves Tweak in hand
            state.carry_the_tool(context, 'EDIT_MESH')
            # a step of its own, as Tab makes one: without it the first edit
            # made in edit mode has no earlier mesh to undo back to, and one
            # Ctrl+Z after it changes nothing
            try:
                bpy.ops.ed.undo_push(message="CAD: pick")
            except RuntimeError:
                pass
        step = CYCLE[0]
        corner = (None if step else
                  _corner_near(context, body, location, (self.x, self.y), self.near))
        edge = (None if corner or step > 1 else
                _rim_edge_near(context, body, face, (self.x, self.y), self.near))
        if corner is not None:
            bpy.ops.mesh.select_mode(type='VERT')
            sync.select_corner(body, corner[0], add=self.add)
            context.scene.cadcore.status = "corner of %s at (%.1f, %.1f, %.1f)" % (
                ", ".join(corner[1]), *corner[0])
            remember_handle(context, None)
            remember_edge_handle(context, None)
        elif edge is not None:
            bpy.ops.mesh.select_mode(type='EDGE')
            sync.select_edge_between(body, *edge, add=self.add)
            context.scene.cadcore.status = (
                "edge %s -- drag the green grip to round it, the orange one "
                "to cut the corner off" % names.edge(*edge))
            remember_handle(context, None)
            remember_edge_handle(context, body)
        else:
            bpy.ops.mesh.select_mode(type='FACE')
            sync.select_faces([face] if not self.add else sync.selected_face_names() + [face])
            context.scene.cadcore.status = "%s -- drag the arrow to push or pull; right-click for more" % face
            remember_handle(context, face if not self.add else None)
            remember_edge_handle(context, None)
            show_the_feature_behind(context, face)
        if not context.scene.cadcore.show_dimensions:
            bpy.ops.cadcore.show_dimensions()        # the overlay draws the arrow
        # in an assembly, the same press held and moved takes the part with it.
        # One part has nowhere to be moved to, so there it stays a plain click.
        if corner is None and edge is None and not self.add and len(drawn) > 1:
            bpy.ops.cadcore.drag_the_part('INVOKE_DEFAULT')
        return {'FINISHED'}


def _plane_at(context, coord):
    """The work plane whose square the ray under `coord` crosses, and where."""
    import mathutils
    from bpy_extras import view3d_utils

    if not PLANES:
        return None, None
    region, rv3d = context.region, context.region_data
    origin = mathutils.Vector(view3d_utils.region_2d_to_origin_3d(region, rv3d, coord))
    direction = mathutils.Vector(view3d_utils.region_2d_to_vector_3d(region, rv3d, coord))
    size = plane_size()
    best, best_t = None, None
    for name, frame in PLANES.items():
        o, n, x = (mathutils.Vector(frame[k]) for k in ("origin", "normal", "x_axis"))
        denominator = direction.dot(n)
        if abs(denominator) < 1e-9:
            continue
        t = (o - origin).dot(n) / denominator
        if t <= 0:
            continue
        hit = origin + direction * t
        local = hit - o
        if abs(local.dot(x)) <= size and abs(local.dot(n.cross(x))) <= size:
            if best_t is None or t < best_t:
                best, best_t = (name, hit), t
    return best if best else (None, None)


def _nearer(context, coord, a, b) -> bool:
    """Is point a nearer the eye than point b, along the ray under coord?"""
    import mathutils
    from bpy_extras import view3d_utils

    origin = mathutils.Vector(view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coord))
    return (mathutils.Vector(a) - origin).length < (mathutils.Vector(b) - origin).length - 1e-6


def _face_at(context, coord):
    """The CAD face, the hit point, and the body it is on, by ray cast.

    The body comes back because an assembly is several objects and the pick
    goes on with the one the ray found, not with whichever is first.
    """
    from bpy_extras import view3d_utils

    if sync.body() is None:
        return None, None, None
    region, rv3d = context.region, context.region_data
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    depsgraph = context.evaluated_depsgraph_get()
    hit, location, _, index, ob, _ = context.scene.ray_cast(depsgraph, origin, direction)
    if not hit or not sync.is_body(ob) or index < 0:
        return None, None, None
    ob = ob.original                 # the ray answers with the evaluated copy
    return sync.face_name(ob, index), location, ob


def _corner_near(context, body, hit, coord, near):
    """A corner within `near` pixels of the cursor: a point where three or
    more CAD faces meet, close in 3D to where the ray landed (so not a corner
    hidden behind the part). Returns (position, face names) or None."""
    import bmesh
    import json
    from bpy_extras import view3d_utils

    bm = bmesh.from_edit_mesh(body.data)
    layer = bm.faces.layers.int.get("cad_face")
    if layer is None or hit is None:
        return None
    table = json.loads(body["cad_face_table"])
    region, rv3d = context.region, context.region_data
    meets: dict = {}                                  # rounded position -> faces there
    for f in bm.faces:
        for v in f.verts:
            meets.setdefault(sync.pos_key(v.co), set()).add(table[f[layer]])
    size = max(body.dimensions) or 1.0
    best, best_d = None, near + 1
    cx, cy = coord
    seen = set()
    for v in bm.verts:
        key = sync.pos_key(v.co)
        if key in seen:
            continue
        seen.add(key)
        faces = meets.get(key, set())
        if len(faces) < 3:
            continue
        world = body.matrix_world @ v.co
        if (world - hit).length > 0.05 * size:        # behind the part, or far along it
            continue
        p = view3d_utils.location_3d_to_region_2d(region, rv3d, world)
        if p is None:
            continue
        d = ((p.x - cx) ** 2 + (p.y - cy) ** 2) ** 0.5
        if d < best_d:
            best, best_d = (key, sorted(faces)), d
    return best


def _rim_edge_near(context, body, face, coord, near):
    """The CAD edge (as its two faces) whose rim runs within `near` pixels of
    the cursor, or None if the cursor is on the face's inside."""
    import bmesh
    from bpy_extras import view3d_utils

    bm = bmesh.from_edit_mesh(body.data)
    layer = bm.faces.layers.int.get("cad_face")
    if layer is None:
        return None
    import json
    table = json.loads(body["cad_face_table"])
    mine = table.index(face) if face in table else -1
    region, rv3d = context.region, context.region_data
    sides = sync.rim_map(bm, table, layer)
    best, best_d = None, near + 1
    cx, cy = coord
    for e in bm.edges:
        if not e.is_boundary or not any(f[layer] == mine for f in e.link_faces):
            continue
        a = view3d_utils.location_3d_to_region_2d(region, rv3d, body.matrix_world @ e.verts[0].co)
        b = view3d_utils.location_3d_to_region_2d(region, rv3d, body.matrix_world @ e.verts[1].co)
        if a is None or b is None:
            continue
        d = _point_to_segment((cx, cy), (a.x, a.y), (b.x, b.y))
        if d < best_d:
            pair = sorted(sides.get(sync.edge_key(e), ()))
            if len(pair) == 2:
                best, best_d = tuple(pair), d
    return best


class _Tool(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'


class CADCORE_TOOL_pick(_Tool):
    bl_idname = "cadcore.tool_pick"
    bl_label = "CAD Pick"
    bl_description = "Click a face or an edge of the part; then right-click for what can be done to it"
    bl_icon = "ops.generic.select_box"
    bl_keymap = (("cadcore.pick_under_cursor", {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
                 ("cadcore.pick_under_cursor", {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True},
                  {"properties": [("add", True)]}),
                 # what the click would land on, shown before it lands
                 ("cadcore.hover", {"type": 'MOUSEMOVE', "value": 'ANY'}, None),
                 ("cadcore.cycle_pick", {"type": 'TAB', "value": 'PRESS'}, None))


class CADCORE_TOOL_pick_edit(CADCORE_TOOL_pick):
    bl_idname = "cadcore.tool_pick_edit"
    bl_context_mode = 'EDIT_MESH'
