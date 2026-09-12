"""Drags read along the screen's x: the draft away from a face, and the pitch of a coil round one."""
from __future__ import annotations

import bpy
import mathutils
from bpy.props import FloatProperty

from . import modal
from ..link import sync
from ..link.client import ServerError
from ..link.state import _apply_build, get_client, push_undo, report_error
from .live import _face_to_act_on, _one_face, _Live


class _Sideways(_Live):
    """A drag whose value grows to the right; a subclass says what it makes."""

    label = ""
    hint = ""
    low = 0.0                 # the least the value may be
    step = 0.5

    def _per_pixel(self, context) -> float:
        body = sync.body()
        centre = body.matrix_world @ (0.125 * sum((mathutils.Vector(c) for c in body.bound_box),
                                                  mathutils.Vector()))
        return modal.pixel_size(context, centre)

    def _value_at(self, pixels: float) -> float:
        return max(self.low, pixels * self.per_pixel)

    def _text(self, value: float) -> str:
        return "%.1f" % value

    def invoke(self, context, event):
        face = _face_to_act_on(context, event)
        if face is None:
            self.report({'ERROR'}, "select exactly one face")
            return {'CANCELLED'}
        self.face = face
        self.per_pixel = self._per_pixel(context)
        self.start = event.mouse_region_x
        self._begin(context)
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("%s %s: %s | click or Enter to keep | Esc" % (self.label, face, self.hint))
        return {'RUNNING_MODAL'}

    def _modal(self, context, event):
        if event.type in modal.NAVIGATION:
            return {'PASS_THROUGH'}
        if self._typing(event):
            typed = self._typed_value()
            if typed is not None:
                modal.say(context, event, "%s %s: %s (typed) | Enter to keep | Esc"
                          % (self.label, self.face, self._typed), self._typed)
                self._preview(context, typed, self._make, self._edit)
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE' and not self._typed:
            value = self._value_at(event.mouse_region_x - self.start)
            step = self.step / 5.0 if event.ctrl else self.step
            value = max(self.low, round(value / step) * step)
            if value <= 0:
                return {'RUNNING_MODAL'}
            modal.say(context, event, "%s %s: %s | click or Enter to keep | Esc"
                      % (self.label, self.face, self._text(value)), self._text(value))
            self._preview(context, value, self._make, self._edit)
            return {'RUNNING_MODAL'}
        if (event.type == 'LEFTMOUSE' and event.value == 'RELEASE') or \
                (event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS'):
            if self._value is None:
                return self._cancel(context)
            self._keep(self._value)
            return self._finish(context, self._make, self.label.lower())
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._cancel(context)
        return {'RUNNING_MODAL'}


class CADCORE_OT_draft_from_here(_Sideways, bpy.types.Operator):
    bl_idname = "cadcore.draft_from_here"
    bl_label = "Draft From Here"
    bl_description = ("Lean every wall away from the picked face's plane, as a mould would need; "
                      "drag right for more degrees, and letting go is one undoable step")
    bl_options = {'REGISTER', 'UNDO'}

    angle: FloatProperty(name="Angle", default=0.0, min=0.0, max=45.0, options={'SKIP_SAVE'},
                         description="given, the walls are drafted this far with no dragging")

    label = "Draft from"
    hint = "the walls lean away from its plane; drag right for more"

    def _per_pixel(self, context) -> float:
        return 1.0 / 12.0                                   # degrees per pixel

    def _text(self, value: float) -> str:
        return "%.1f°" % value

    def _make(self, value):
        return ("add_draft", {"parting_face": self.face, "angle": round(value, 2)})

    def _edit(self, value):
        return {"angle": round(value, 2)}

    def _keep(self, value) -> None:
        self.angle = value

    def execute(self, context):
        face = _one_face(context)
        if face is None:
            self.report({'ERROR'}, "select exactly one planar face to draft from")
            return {'CANCELLED'}
        self.face = face
        try:
            _apply_build(context, get_client(context).call("add_draft", parting_face=face,
                                                            angle=self.angle))
        except ServerError as exc:
            return report_error(self, exc)
        context.scene.cadcore.status = "drafted %.1f° from %s" % (self.angle, face)
        push_undo("draft", self)
        return {'FINISHED'}


class CADCORE_OT_coil_round(_Sideways, bpy.types.Operator):
    bl_idname = "cadcore.coil_round"
    bl_label = "Coil Round It"
    bl_description = ("Wind a wire round the picked round face and join it on; drag right "
                      "for a longer pitch, and the pitch stays a parameter")
    bl_options = {'REGISTER', 'UNDO'}

    pitch: FloatProperty(name="Pitch", default=0.0, min=0.0, options={'SKIP_SAVE'},
                         description="given, the coil is wound at this pitch with no dragging")

    label = "Coil round"
    hint = "drag right for a longer pitch"
    low = 0.5

    def _text(self, value: float) -> str:
        return "pitch %.1f mm" % value

    def _make(self, value):
        return ("add_coil", {"face": self.face, "pitch": round(value, 2)})

    def _edit(self, value):
        return ("set_parameter", {"name": self.feature + "_pitch", "value": round(value, 2)})

    def _keep(self, value) -> None:
        self.pitch = value

    def execute(self, context):
        face = _one_face(context)
        if face is None:
            self.report({'ERROR'}, "select exactly one round face to wind round")
            return {'CANCELLED'}
        self.face = face
        try:
            _apply_build(context, get_client(context).call("add_coil", face=face,
                                                            pitch=self.pitch or None))
        except ServerError as exc:
            return report_error(self, exc)
        context.scene.cadcore.status = "coil round %s" % face
        push_undo("coil", self)
        return {'FINISHED'}
