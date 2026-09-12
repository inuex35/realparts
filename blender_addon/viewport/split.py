"""Split the body at a plane dragged off the picked face; what is under it stays."""
from __future__ import annotations

import bpy
import mathutils
from bpy.props import FloatProperty

from . import modal
from ..link.client import ServerError
from ..link.state import _apply_build, get_client, push_undo, report_error
from .live import _face_to_act_on, _one_face, _screen, _Live


class CADCORE_OT_split_here(_Live, bpy.types.Operator):
    bl_idname = "cadcore.split_here"
    bl_label = "Split Here"
    bl_description = ("Cut the body at a plane parallel to the picked face, dragged into the "
                      "part; what is under the cut stays, and the cut follows the face")
    bl_options = {'REGISTER', 'UNDO'}

    offset: FloatProperty(name="Offset", default=0.0, options={'SKIP_SAVE'},
                          description="given, the cut is this far off the face with no dragging")

    def _make(self, value):
        return ("add_split", {"face": self.face, "offset": round(value, 3), "keep": "below"})

    def execute(self, context):
        face = _one_face(context)
        if face is None:
            self.report({'ERROR'}, "select exactly one planar face to split from")
            return {'CANCELLED'}
        self.face = face
        try:
            _apply_build(context, get_client(context).call("add_split", face=face,
                                                            offset=self.offset, keep="below"))
        except ServerError as exc:
            return report_error(self, exc)
        context.scene.cadcore.status = "split %+.2f mm off %s" % (self.offset, face)
        push_undo("split", self)
        return {'FINISHED'}

    def invoke(self, context, event):
        face = _face_to_act_on(context, event)
        if face is None:
            self.report({'ERROR'}, "select exactly one planar face to split from")
            return {'CANCELLED'}
        self.face = face
        try:
            frame = get_client(context).call("face_frame", face=face)
        except ServerError as exc:
            return report_error(self, exc)
        o = mathutils.Vector(frame["origin"])
        n = mathutils.Vector(frame["normal"])
        a, b = _screen(context, o), _screen(context, o + n * 10.0)
        if a is None or b is None or (b - a).length < 1e-3:
            self.report({'ERROR'}, "the face is edge-on; orbit a little and try again")
            return {'CANCELLED'}
        self.axis = (b - a)
        self.per_pixel = 10.0 / self.axis.length      # mm per pixel along the normal
        self.axis.normalize()
        self.start = mathutils.Vector((event.mouse_region_x, event.mouse_region_y))
        self._begin(context)
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Split off %s: drag the cut into the part | click or Enter "
                                     "to keep | Esc to cancel" % face)
        return {'RUNNING_MODAL'}

    def _modal(self, context, event):
        if event.type in modal.NAVIGATION:
            return {'PASS_THROUGH'}
        edit = lambda v: ("set_parameter", {"name": self.feature + "_offset",      # noqa: E731
                                            "value": round(v, 3)})
        if self._typing(event):
            typed = self._typed_value()
            if typed is not None:
                modal.say(context, event, "Split off %s: %s mm (typed) | Enter to keep | Esc"
                          % (self.face, self._typed), self._typed + " mm")
                self._preview(context, typed, self._make, edit)
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE' and not self._typed:
            here = mathutils.Vector((event.mouse_region_x, event.mouse_region_y))
            offset = (here - self.start).dot(self.axis) * self.per_pixel
            step = 0.1 if event.ctrl else 1.0
            offset = round(offset / step) * step
            if abs(offset) < 1e-6:
                return {'RUNNING_MODAL'}
            modal.say(context, event, "Split off %s: %+.2f mm | click or Enter to keep | Esc"
                      % (self.face, offset), "%+.2f mm" % offset)
            self._preview(context, offset, self._make, edit)
            return {'RUNNING_MODAL'}
        if (event.type == 'LEFTMOUSE' and event.value == 'RELEASE') or \
                (event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS'):
            if self._value is None:
                return self._cancel(context)
            self.offset = self._value
            return self._finish(context, self._make, "split")
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._cancel(context)
        return {'RUNNING_MODAL'}
