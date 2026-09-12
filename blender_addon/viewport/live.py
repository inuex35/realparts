"""What every drag tool shares: preview, keep or drop, typed numbers, snapping, the face under the cursor."""
from __future__ import annotations

import math

import bpy
import mathutils

from . import drawing, modal
from ..link import sync, throttle
from ..link import state
from ..link.client import ServerError
from ..link.state import _apply_build, get_client, push_undo, report_error, set_error


#: a drag is a run of trial edits between begin_drag and end_drag, which the
#: kernel records as one step or none


def _send(client, made):
    """Call the client with a `(operation, arguments)` pair from `make()`."""
    op, args = made
    return client.call(op, **args)


class Snapper:
    """Snap targets for a drag, built once per drag from `describe_faces`.

    Targets are parallel planes (a pulled face stops flush, a pocket goes
    through) and points on the face (its centre, the axes of cylinders
    through it).
    """

    #: snap distance in pixels
    PIXELS = 9.0

    def __init__(self, faces: list, frame: dict, own: str):
        self.frame = frame
        self.own = own
        n = mathutils.Vector(frame["normal"])
        o = mathutils.Vector(frame["origin"])
        # planes parallel to this one, by their distance along the normal
        self.planes = []
        for face in faces:
            if face.get("shape") != "plane" or face["name"] == own or not face.get("normal"):
                continue
            m = mathutils.Vector(face["normal"])
            if abs(abs(n.dot(m)) - 1.0) > 1e-3:
                continue
            d = (mathutils.Vector(face["centre"]) - o).dot(n)
            if abs(d) > 1e-6:
                self.planes.append((d, face["name"]))
        # points on this face: its centre and the axes of cylinders through it
        self.points = [((0.0, 0.0), "centre")]
        for face in faces:
            if face.get("shape") != "cylinder" or not face.get("axis"):
                continue
            a = mathutils.Vector(face["axis"])
            if abs(abs(n.dot(a)) - 1.0) > 1e-3:
                continue
            c = mathutils.Vector(face["centre"])
            uv = drawing.to_uv(tuple(c), frame)
            self.points.append((uv, face["name"]))

    def distance(self, raw: float, mm_per_pixel: float):
        """`raw` snapped onto the nearest parallel plane within reach, with its name."""
        best, label = raw, None
        for d, name in self.planes:
            if abs(d - raw) <= self.PIXELS * mm_per_pixel and (label is None or abs(d - raw) < abs(best - raw)):
                best, label = d, name
        return best, label

    def uv(self, uv, mm_per_pixel: float):
        """`uv` snapped onto a point, else onto a u or v line through one, with its name."""
        reach = self.PIXELS * mm_per_pixel
        for (pu, pv), name in self.points:
            if math.dist(uv, (pu, pv)) <= reach:
                return (pu, pv), name
        u, v = uv
        for (pu, pv), name in self.points:
            if abs(u - pu) <= reach:
                return (pu, v), name + " (u)"
            if abs(v - pv) <= reach:
                return (u, pv), name + " (v)"
        return uv, None


def _snapper(context, face: str, frame: dict) -> "Snapper":
    try:
        faces = get_client(context).call("describe_faces")["faces"]
    except ServerError:
        faces = []
    return Snapper(faces, frame, face)


def _one_face(context):
    faces = sync.selected_face_names()
    return faces[0] if len(faces) == 1 else None


def _screen(context, point) -> mathutils.Vector | None:
    from bpy_extras import view3d_utils

    return view3d_utils.location_3d_to_region_2d(context.region, context.region_data,
                                                 mathutils.Vector(point))


class _Live(modal.Typed):
    """The preview/confirm/cancel contract shared by the drag operators."""

    feature: str | None = None
    #: what `_begin` took out of Blender's hands, for `_cancel` to give back:
    #: one (object name, faces, edge pairs) per body that was being edited
    _picked: list | None = None
    _select_mode: tuple | None = None

    def _begin(self, context) -> None:
        # A pick leaves Blender in edit mode, and every preview frame writes the
        # mesh, which cannot be done there. Leaving once here beats the mesh
        # write leaving and returning on each of them.
        editing = [ob for ob in sync.bodies() if ob.mode == 'EDIT']
        self._picked = None
        self._select_mode = None
        if editing:
            # taken down before the mode goes, because `picked` reads the edit
            # mesh and there is no edit mesh a line later. Per object: an
            # assembly edits every part at once, and an edge belongs to one
            self._picked = [(ob.name,) + sync.picked(ob) for ob in editing]
            self._select_mode = tuple(context.tool_settings.mesh_select_mode)
            context.view_layer.objects.active = editing[0]
            bpy.ops.object.mode_set(mode='OBJECT')
            state.carry_the_tool(context, 'OBJECT')
        get_client(context).call("begin_drag")
        self.feature = None
        self._value = None
        #: the last value that built and the last refused: past what the shape
        #: can take, letting go keeps what is on the screen
        self._good = None
        self._refused = None
        self._typed = ""

    def _preview(self, context, value, make, edit) -> None:
        """Preview `value`: create the feature on the first call, edit it after."""
        self._value = value

        def work():
            client = get_client(context)
            try:
                if self.feature is None:
                    out = _send(client, make(value))
                    self.feature = out.get("feature")
                else:
                    change = edit(value)
                    if isinstance(change, tuple):        # an operation of its own
                        out = _send(client, change)
                    else:
                        out = client.call("edit_feature", feature_id=self.feature, args=change)
            except ServerError as exc:
                # the shape on screen is still the last one that built: say
                # that in the badge rather than replacing a part with a message
                self._refused = value
                set_error(context, exc)
                modal.refused(context, exc.message)
                return
            self._good, self._refused = value, None
            modal.built(context)
            _apply_build(context, out)
            for area in context.screen.areas:
                area.tag_redraw()
        throttle.soon(self.bl_idname, work)

    def modal(self, context, event):
        """Blender's entry: the operator's own `_modal`, and a drag that raises is cancelled."""
        if getattr(self, "_dead", False):
            return {'CANCELLED'}
        try:
            return self._modal(context, event)
        except Exception:
            self._dead = True
            self._cancel(context)
            raise

    def cancel(self, context):
        """Blender took the operator away (the window changed): the drag is dropped."""
        if not getattr(self, "_dead", False):
            self._dead = True
            self._cancel(context)

    def _the_value_that_built(self):
        """What to keep on the way out: what is on the screen, which is the
        last value that built, not the one the cursor was at when it stopped."""
        if self._refused is not None and self._good is not None:
            return self._good
        return self._value

    def _finish(self, context, make, label: str):
        """Restore the checkpoint and apply the final value once, so undo is one step."""
        throttle.forget()
        client = get_client(context)
        value = self._the_value_that_built()
        try:
            client.call("reset_drag")
            _send(client, make(value))
            # end_drag's reply: it carries the new state's number
            _apply_build(context, client.call("end_drag", keep=True))
        except ServerError as exc:
            # the final value would not build: nothing of the drag is kept,
            # and the screen shows what the kernel went back to
            try:
                _apply_build(context, client.call("end_drag", keep=False))
            except ServerError:
                pass
            self._give_back_the_pick(context)
            modal.hush(context)
            return report_error(self, exc)
        if self._refused is not None:
            context.scene.cadcore.status = (
                "%s: kept %s -- the shape would not take any more" % (label, _mm(value)))
        self._value = value
        push_undo(label, self)
        modal.hush(context)
        return {'FINISHED'}

    def _give_back_the_pick(self, context) -> None:
        """Put back the edit mode and the selection `_begin` took.

        A drag that is kept does not need this: the feature is made and the
        pick has been answered. A drag that is dropped does. Pressing on a grip
        and letting go without moving is a whole drag -- begun and cancelled --
        and until this was here it left the body in object mode with an empty
        selection, while the grip stayed on screen. The next press on that grip
        was told to select some edges first.
        """
        if not self._picked:
            return
        back = [(bpy.data.objects.get(name), faces, pairs)
                for name, faces, pairs in self._picked]
        back = [row for row in back if row[0] is not None]
        if not back:
            return                              # the rebuild drew other parts
        for ob, _, _ in back:
            ob.select_set(True)
        context.view_layer.objects.active = back[0][0]
        if back[0][0].mode != 'EDIT':
            try:
                bpy.ops.object.mode_set(mode='EDIT')
            except RuntimeError:
                return
            state.carry_the_tool(context, 'EDIT_MESH')
        if self._select_mode is not None:
            context.tool_settings.mesh_select_mode = self._select_mode
        for ob, faces, pairs in back:
            written = False
            if faces:
                sync.select_faces(faces, ob)
                written = True
            for a, b in pairs:
                # the first edge on an object clears what is there unless faces
                # were put back first, in which case every one of them adds
                sync.select_edge_between(ob, a, b, add=written)
                written = True

    def _cancel(self, context):
        throttle.forget()
        try:
            _apply_build(context, get_client(context).call("end_drag", keep=False))
        except ServerError:
            pass
        self._give_back_the_pick(context)
        modal.hush(context)
        context.scene.cadcore.status = "cancelled"
        return {'CANCELLED'}


def _face_under_cursor(context, event):
    """The CAD face name and hit location under the mouse, by ray cast at the body mesh."""
    if sync.body() is None:
        return None, None
    origin, direction = modal.mouse_ray(context, event)
    depsgraph = context.evaluated_depsgraph_get()
    hit, location, _, index, ob, _ = context.scene.ray_cast(depsgraph, origin, direction)
    if not hit or not sync.is_body(ob) or index < 0:
        return None, None
    ob = ob.original                 # the ray answers with the evaluated copy
    return sync.face_name(ob, index), location


def _face_to_act_on(context, event):
    """The face a drag works on: the picked one, or the one under the cursor
    when nothing is picked. Reaching for a tool is also a way of picking."""
    face = _one_face(context)
    if face is not None:
        return face
    face, _ = _face_under_cursor(context, event)
    if face is not None:
        sync.select_faces([face])
    return face


def _mm(value) -> str:
    """A drag value as a person reads it: one number, or three."""
    if isinstance(value, (list, tuple)):
        return "  ".join("%+.1f" % v for v in value) + " mm"
    return "%.2f mm" % value
