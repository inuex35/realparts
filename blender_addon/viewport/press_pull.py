"""Grab a face and drag it along its normal."""
from __future__ import annotations


import bpy
import mathutils
from bpy.props import FloatProperty

from . import modal
from ..link.client import ServerError
from ..link.state import _apply_build, get_client, push_undo, report_error
from .live import _snapper, _face_to_act_on, _one_face, _screen, _Live


class CADCORE_OT_press_pull(_Live, bpy.types.Operator):
    bl_idname = "cadcore.press_pull"
    bl_label = "Press / Pull"
    bl_description = ("Grab the selected face and drag it along its normal; the "
                      "model follows, and letting go is one undoable step")
    bl_options = {'REGISTER', 'UNDO'}

    distance: FloatProperty(name="Distance", default=0.0, options={'SKIP_SAVE'},
                            description="given, the face moves this far with no dragging")
    step: FloatProperty(name="Snap", default=0.5, min=0.0)

    def _make(self, value):
        return ("move_face", {"face": self.face, "distance": round(value, 3)})

    def execute(self, context):
        face = _one_face(context)
        if face is None:
            self.report({'ERROR'}, "select exactly one planar face to push or pull")
            return {'CANCELLED'}
        self.face = face
        try:
            out = get_client(context).call("move_face", face=face, distance=self.distance)
            _apply_build(context, out)
        except ServerError as exc:
            return report_error(self, exc)
        context.scene.cadcore.status = "%s moved %+.2f mm" % (face, self.distance)
        push_undo("press/pull", self)
        return {'FINISHED'}

    def invoke(self, context, event):
        face = _face_to_act_on(context, event)
        if face is None:
            self.report({'ERROR'}, "select exactly one planar face to push or pull")
            return {'CANCELLED'}
        self.face = face
        try:
            frame = get_client(context).call("face_frame", face=face)
        except ServerError as exc:
            return report_error(self, exc)
        self.frame = frame
        # the drag is measured along the face normal projected to the screen;
        # 10 mm along the normal gives the pixels-per-millimetre scale
        o = mathutils.Vector(frame["origin"])
        n = mathutils.Vector(frame["normal"])
        a, b = _screen(context, o), _screen(context, o + n * 10.0)
        if a is None or b is None or (b - a).length < 1e-3:
            self.report({'ERROR'}, "the face is edge-on; orbit a little and try again")
            return {'CANCELLED'}
        self.axis = (b - a)
        self.per_pixel = 10.0 / self.axis.length      # mm per pixel along the axis
        self.axis.normalize()
        self.start = mathutils.Vector((event.mouse_region_x, event.mouse_region_y))
        self.snapper = _snapper(context, face, frame)
        self._begin(context)
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Press/Pull %s: drag along the normal | Ctrl fine | "
                                     "click or Enter to keep | Esc to cancel" % face)
        return {'RUNNING_MODAL'}

    def _modal(self, context, event):
        if event.type in modal.NAVIGATION:
            return {'PASS_THROUGH'}
        if self._typing(event):
            typed = self._typed_value()
            if typed is not None:
                modal.say(context, event,
                          "Press/Pull %s: %s mm (typed) | Enter to keep | Esc"
                          % (self.face, self._typed), self._typed + " mm")
                self._preview(context, typed, self._make, lambda v: {"distance": round(v, 3)})
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE' and not self._typed:
            here = mathutils.Vector((event.mouse_region_x, event.mouse_region_y))
            distance = (here - self.start).dot(self.axis) * self.per_pixel
            step = (self.step / 5.0 if event.ctrl else self.step) or 0.0
            if step:
                distance = round(distance / step) * step
            # snapping flush with another face overrides the step unless Ctrl is held
            snapped = None
            if not event.ctrl:
                distance, snapped = self.snapper.distance(distance, self.per_pixel)
            if abs(distance) < 1e-6:
                return {'RUNNING_MODAL'}
            modal.say(context, event,
                      "Press/Pull %s: %+.2f mm%s | click or Enter to keep | Esc"
                      % (self.face, distance,
                         "  -- flush with %s" % snapped if snapped else ""),
                      "%+.2f mm" % distance)
            self._preview(context, distance, self._make,
                          lambda v: {"distance": round(v, 3)})
            return {'RUNNING_MODAL'}
        if (event.type == 'LEFTMOUSE' and event.value == 'RELEASE') or \
                (event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS'):
            if self._value is None:
                return self._cancel(context)
            self.distance = self._value
            return self._finish(context, self._make, "press/pull")
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._cancel(context)
        return {'RUNNING_MODAL'}
