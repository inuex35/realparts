"""Drag a rectangle on a face, then its depth: a pocket in, a boss out."""
from __future__ import annotations


import bpy
import mathutils
from bpy.props import FloatProperty, FloatVectorProperty

from . import drawing, gpu_draw, modal
from ..link import throttle
from ..link.client import ServerError
from ..link.state import _apply_build, get_client, push_undo, report_error
from .live import _send, _snapper, _face_to_act_on, _one_face, _screen, _Live


class CADCORE_OT_drag_box(_Live, modal.ViewportTool, bpy.types.Operator):
    bl_idname = "cadcore.drag_box"
    bl_label = "Box Cut / Boss"
    bl_description = ("On the selected face: drag a rectangle, release, then drag the "
                      "depth -- into the face for a pocket, out of it for a boss")
    bl_options = {'REGISTER', 'UNDO'}

    at: FloatVectorProperty(name="Centre", size=2, default=(0.0, 0.0), options={'SKIP_SAVE'})
    width: FloatProperty(name="Width", default=0.0, min=0.0, options={'SKIP_SAVE'})
    height: FloatProperty(name="Height", default=0.0, min=0.0, options={'SKIP_SAVE'})
    depth: FloatProperty(name="Depth", default=0.0, options={'SKIP_SAVE'},
                         description="negative cuts a pocket, positive raises a boss")

    def _make(self, value):
        return ("add_pocket", {"face": self.face, "kind": "pocket" if value < 0 else "boss",
                               "depth": round(abs(value), 3), "width": round(self.width, 3),
                               "height": round(self.height, 3),
                               "at": [round(self.at[0], 3), round(self.at[1], 3)]})

    def execute(self, context):
        face = _one_face(context)
        if face is None or self.width <= 0 or self.height <= 0 or self.depth == 0:
            self.report({'ERROR'}, "select one face; a box needs a width, a height and a depth")
            return {'CANCELLED'}
        self.face = face
        try:
            out = _send(get_client(context), self._make(self.depth))
            _apply_build(context, out)
        except ServerError as exc:
            return report_error(self, exc)
        push_undo("box " + ("cut" if self.depth < 0 else "boss"), self)
        return {'FINISHED'}

    def invoke(self, context, event):
        face = _face_to_act_on(context, event)
        if face is None:
            self.report({'ERROR'}, "select exactly one planar face to draw the box on")
            return {'CANCELLED'}
        self.face = face
        try:
            self.frame = get_client(context).call("face_frame", face=face)
        except ServerError as exc:
            return report_error(self, exc)
        self.corner = None                 # first corner, in the face's frame
        self.cursor = None
        self.snapped = None
        self.phase = 'RECT'                # then 'DEPTH'
        self.snapper = _snapper(context, face, self.frame)
        o = mathutils.Vector(self.frame["origin"])
        n = mathutils.Vector(self.frame["normal"])
        a, b = _screen(context, o), _screen(context, o + n * 10.0)
        self.axis = (b - a) if a is not None and b is not None else None
        if self.axis is not None and self.axis.length > 1e-3:
            self.per_pixel = 10.0 / self.axis.length
            self.axis.normalize()
        else:
            self.axis = None
        self._watch(context)
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Box on %s: drag a rectangle | Esc" % face)
        return {'RUNNING_MODAL'}

    def _track(self, context, event):
        hit = modal.plane_hit(context, event, self.frame)
        if hit is None:
            self.cursor, self.snapped = None, None
            return
        uv = drawing.to_uv(hit, self.frame)
        if event.ctrl:
            self.cursor, self.snapped = uv, None
        else:
            self.cursor, self.snapped = self.snapper.uv(uv, modal.pixel_size(context, hit))

    def _rect(self):
        if self.corner is None or self.cursor is None:
            return None
        (u0, v0), (u1, v1) = self.corner, self.cursor
        return (min(u0, u1), min(v0, v1), max(u0, u1), max(v0, v1))

    def _draw(self, context):
        rect = self._rect()
        if rect is None:
            return
        u0, v0, u1, v1 = rect
        ring = [drawing.to_3d(p, self.frame) for p in ((u0, v0), (u1, v0), (u1, v1), (u0, v1), (u0, v0))]
        gpu_draw.line_strip(ring, (1.0, 0.75, 0.2, 1.0))

    def _modal(self, context, event):
        if event.type in modal.NAVIGATION:
            return {'PASS_THROUGH'}
        context.area.tag_redraw()
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._teardown(context)
            if self.phase == 'DEPTH':
                return self._cancel(context)
            return {'CANCELLED'}
        if self.phase == 'RECT':
            if event.type == 'MOUSEMOVE':
                self._track(context, event)
                if self.corner is not None:
                    rect = self._rect()
                    if rect:
                        context.area.header_text_set("Box on %s: %.1f x %.1f mm | release to set, then drag the depth"
                                                     % (self.face, rect[2] - rect[0], rect[3] - rect[1]))
                return {'RUNNING_MODAL'}
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS' and self.corner is None:
                self._track(context, event)
                self.corner = self.cursor
                return {'RUNNING_MODAL'}
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self.corner is not None:
                rect = self._rect()
                if rect is None or rect[2] - rect[0] < 0.5 or rect[3] - rect[1] < 0.5:
                    self.corner = None
                    return {'RUNNING_MODAL'}
                u0, v0, u1, v1 = rect
                self.at = ((u0 + u1) / 2, (v0 + v1) / 2)
                self.width, self.height = u1 - u0, v1 - v0
                if self.axis is None:
                    self._teardown(context)
                    self.report({'ERROR'}, "the face is edge-on; orbit a little and try again")
                    return {'CANCELLED'}
                self.phase = 'DEPTH'
                self.start = mathutils.Vector((event.mouse_region_x, event.mouse_region_y))
                self._begin(context)
                context.area.header_text_set("Box %.1f x %.1f: drag in for a pocket, out for a boss, or type | Enter | Esc"
                                             % (self.width, self.height))
                return {'RUNNING_MODAL'}
            return {'RUNNING_MODAL'}
        # DEPTH
        if self._typing(event):
            typed = self._typed_value()
            if typed:
                self._preview(context, typed, self._make, lambda v: {"depth": round(abs(v), 3)})
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE' and not self._typed:
            here = mathutils.Vector((event.mouse_region_x, event.mouse_region_y))
            depth = (here - self.start).dot(self.axis) * self.per_pixel
            depth = round(depth / (0.1 if event.ctrl else 0.5)) * (0.1 if event.ctrl else 0.5)
            through = None
            if not event.ctrl:
                depth, through = self.snapper.distance(depth, self.per_pixel)
            if abs(depth) < 1e-6:
                return {'RUNNING_MODAL'}
            # the sign decides pocket or boss; a sign change needs a new feature
            if self._value is not None and (self._value < 0) != (depth < 0):
                throttle.forget()
                get_client(context).call("reset_drag")
                self.feature = None
            context.area.header_text_set("Box %.1f x %.1f: %s %.1f mm%s | click or Enter to keep | Esc"
                                         % (self.width, self.height, "pocket" if depth < 0 else "boss", abs(depth),
                                            "  -- through to %s" % through if through and depth < 0 else
                                            ("  -- flush with %s" % through if through else "")))
            self._preview(context, depth, self._make, lambda v: {"depth": round(abs(v), 3)})
            return {'RUNNING_MODAL'}
        if (event.type == 'LEFTMOUSE' and event.value == 'PRESS') or \
                (event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS'):
            self._teardown(context)
            if self._value is None:
                return self._cancel(context)
            self.depth = self._value
            return self._finish(context, self._make, "box " + ("cut" if self.depth < 0 else "boss"))
        return {'RUNNING_MODAL'}
