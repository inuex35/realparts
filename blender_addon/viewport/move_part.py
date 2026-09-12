"""Drag a part of an assembly by one of its faces.

Bring it near a face that looks back at it and the part snaps against that
face and the drag becomes a mate: a placement that holds when the other part
changes, rather than three numbers that were right once.
"""
from __future__ import annotations


import bpy
import mathutils
from bpy.props import FloatVectorProperty

from . import modal, pick
from ..link import sync, throttle
from ..link.client import ServerError
from ..link.state import _apply_build, get_client, push_undo, report_error, set_error
from .live import _send, _face_to_act_on, _one_face, _Live


class CADCORE_OT_move_part(_Live, bpy.types.Operator):
    bl_idname = "cadcore.move_part"
    bl_label = "Move Part"
    bl_description = ("Grab a part of an assembly by one of its faces and drag it; "
                      "the translate that places it follows. A part placed by a mate "
                      "says so instead")
    bl_options = {'REGISTER', 'UNDO'}

    offset: FloatVectorProperty(name="Move by", size=3, default=(0.0, 0.0, 0.0), options={'SKIP_SAVE'})

    def _make(self, value):
        return ("edit_feature", {"feature_id": self.positioner,
                                 "args": {"offset": [round(v, 3) for v in value]}})

    def _target(self, context, face):
        try:
            said = get_client(context).call("part_of", face=face)
        except ServerError as exc:
            report_error(self, exc)
            return None
        if said["positioner"] is None:
            if said["mate"]:
                self.report({'ERROR'}, "%s is placed by the mate %r; edit the mate, not the part"
                            % (said["body"], said["mate"]))
            else:
                self.report({'ERROR'}, "%s has no translate to move; add one first" % said["body"])
            return None
        return said

    def execute(self, context):
        face = _one_face(context)
        if face is None:
            self.report({'ERROR'}, "select a face of the part to move")
            return {'CANCELLED'}
        said = self._target(context, face)
        if said is None:
            return {'CANCELLED'}
        self.positioner = said["positioner"]
        moved = [b + d for b, d in zip(said["offset_mm"], self.offset)]
        try:
            out = _send(get_client(context), self._make(moved))
            _apply_build(context, out)
        except ServerError as exc:
            return report_error(self, exc)
        push_undo("move part", self)
        return {'FINISHED'}

    def invoke(self, context, event):
        face = _face_to_act_on(context, event)
        if face is None:
            self.report({'ERROR'}, "select a face of the part to move")
            return {'CANCELLED'}
        said = self._target(context, face)
        if said is None:
            return {'CANCELLED'}
        self.positioner = said["positioner"]
        self.body_id = said["body"]
        try:
            self.base = list(said["offset_mm"])
            frame = get_client(context).call("face_frame", face=face)
        except ServerError as exc:
            return report_error(self, exc)
        self.anchor = mathutils.Vector(frame["origin"])
        self.start = mathutils.Vector((event.mouse_region_x, event.mouse_region_y))
        self.face = face
        self.part = face.split(":", 1)[0] if ":" in face else ""
        self.face_normal = mathutils.Vector(frame["normal"])
        self.snap = None
        self._catches = self._catchers(context)
        self.reach = 0.25 * (max((max(ob.dimensions) for ob in sync.bodies()),
                                 default=40.0) or 40.0)
        self._begin(context)
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Move %s: drag in the view plane | X/Y/Z to lock an axis | "
                                     "click or Enter to keep | Esc" % self.body_id)
        self.lock = None
        return {'RUNNING_MODAL'}

    def _modal(self, context, event):
        from bpy_extras import view3d_utils

        if event.type in modal.NAVIGATION:
            return {'PASS_THROUGH'}
        if event.type in {'X', 'Y', 'Z'} and event.value == 'PRESS':
            self.lock = None if self.lock == event.type else event.type
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE':
            region, rv3d = context.region, context.region_data
            here = (event.mouse_region_x, event.mouse_region_y)
            # the mouse in 3D at the depth of the grabbed face
            a = view3d_utils.region_2d_to_location_3d(region, rv3d, tuple(self.start), self.anchor)
            b = view3d_utils.region_2d_to_location_3d(region, rv3d, here, self.anchor)
            delta = b - a
            if self.lock:
                keep = "XYZ".index(self.lock)
                delta = mathutils.Vector([delta[i] if i == keep else 0.0 for i in range(3)])
            step = 0.1 if event.ctrl else 0.5
            delta = [round(d / step) * step for d in delta]
            if all(abs(d) < 1e-9 for d in delta):
                return {'RUNNING_MODAL'}
            moved = [bs + d for bs, d in zip(self.base, delta)]
            self._value = moved
            snap = self._snap_at(delta)
            if snap != self.snap:
                self.snap = snap
                pick.SNAP[0] = snap["face"] if snap else None
                self._show(context, moved)
            elif snap is None:
                self._preview(context, moved, self._make,
                              lambda v: self._make(v)[1]["args"])
            if snap:
                # the mate decides where it sits; the mouse only decides whether
                # it is still near enough to stay there
                modal.say(context, event, "Move %s: against %s | let go to mate it | Esc"
                          % (self.body_id, snap["face"]), "against %s" % snap["face"])
            else:
                modal.say(context, event,
                          "Move %s: %+.1f %+.1f %+.1f mm%s | click or Enter to keep | Esc"
                          % (self.body_id, *delta, "  [%s]" % self.lock if self.lock else ""),
                          "%+.1f  %+.1f  %+.1f mm" % tuple(delta))
            return {'RUNNING_MODAL'}
        if (event.type == 'LEFTMOUSE' and event.value == 'RELEASE') or \
                (event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS'):
            if self._value is None:
                return self._cancel(context)
            if self.snap:
                return self._finish(context, self._make_mate, "mate")
            return self._finish(context, self._make, "move part")
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._cancel(context)
        return {'RUNNING_MODAL'}

    # -- snapping to a face that looks back ---------------------------------
    def _catchers(self, context) -> list:
        """Every flat face of the other parts, asked for once at the start."""
        try:
            faces = get_client(context).call("describe_faces",
                                             query={"shape": "plane"})["faces"]
        except ServerError:
            return []
        return [dict(f, part=f["name"].split(":", 1)[0]) for f in faces
                if ":" in f["name"] and f["name"].split(":", 1)[0] != self.part]

    def _snap_at(self, delta):
        """The face this drag would settle against, or None.

        The two have to look at each other -- a face cannot sit against one
        pointing the same way -- and be near in both senses: close along the
        normal, and not off the side of it.
        """
        origin = self.anchor + mathutils.Vector(delta)
        best, closest = None, self.reach
        for face in self._catches:
            normal = mathutils.Vector(face["normal"])
            if self.face_normal.dot(normal) > -0.98:
                continue
            away = mathutils.Vector(face["centre"]) - origin
            along = abs(away.dot(normal))
            if along < closest and (away - normal * away.dot(normal)).length < self.reach:
                best, closest = face, along
        return {"face": best["name"], "to": best["part"]} if best else None

    def _make_mate(self, _value):
        return ("add_feature", {"type": "mate", "args": {
            "move": self.body_id, "to": self.snap["to"],
            "faces": [self.face, self.snap["face"]], "kind": "fastened",
            "offset": 0.0, "flip": True}})

    def _show(self, context, moved) -> None:
        """The snap changed: preview the mate, or the translate, for real."""
        client = get_client(context)
        try:
            client.call("reset_drag")
            out = _send(client, self._make_mate(None) if self.snap else self._make(moved))
            _apply_build(context, out)
        except ServerError as exc:
            self.snap, pick.SNAP[0] = None, None
            set_error(context, exc)
            return
        for area in context.screen.areas:
            area.tag_redraw()

    def _finish(self, context, make, label):
        pick.SNAP[0] = None
        return super()._finish(context, make, label)

    def _cancel(self, context):
        pick.SNAP[0] = None
        return super()._cancel(context)

    def _preview(self, context, value, make, edit):
        # the translate already exists, so every preview is an edit
        self._value = value

        def work():
            try:
                out = _send(get_client(context), make(value))
            except ServerError as exc:
                set_error(context, exc)
                return
            _apply_build(context, out)
            for area in context.screen.areas:
                area.tag_redraw()
        throttle.soon(self.bl_idname, work)


class CADCORE_OT_drag_the_part(bpy.types.Operator):
    """Watch one press on a part: a click is over, a press held and moved is
    a part move. Nothing is asked of the kernel until the mouse moves."""

    bl_idname = "cadcore.drag_the_part"
    bl_label = "Drag the Part"
    bl_options = {'REGISTER'}

    #: pixels of travel that tell a drag from a click made with a shaky hand
    SLACK = 8

    def invoke(self, context, event):
        self.start = (event.mouse_region_x, event.mouse_region_y)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            if ((event.mouse_region_x - self.start[0]) ** 2
                    + (event.mouse_region_y - self.start[1]) ** 2) <= self.SLACK ** 2:
                return {'RUNNING_MODAL'}
            try:
                bpy.ops.cadcore.move_part('INVOKE_DEFAULT')
            except RuntimeError as exc:      # bpy.ops turns the refusal into this
                self.report({'ERROR'}, str(exc).replace("Error: ", "", 1))
            return {'FINISHED'}
        if event.type in {'LEFTMOUSE', 'ESC', 'RIGHTMOUSE'}:
            return {'FINISHED'}              # a click: the face is picked, and that is all
        return {'PASS_THROUGH'}
