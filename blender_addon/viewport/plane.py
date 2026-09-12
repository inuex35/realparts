"""Work planes: off a face, between two faces, or hung on an edge and turned.

All three end in the pen: a work plane is somewhere to draw, and asking for
it again by name after making it is the step this removes.
"""
from __future__ import annotations

import math

import bpy
import mathutils
from bpy.props import FloatProperty

from . import drawing, modal, pick
from ..link import sync
from ..link import names
from ..link.client import ServerError
from ..link.state import _apply_build, get_client, push_undo, report_error
from .live import _send, _snapper, _face_to_act_on, _one_face, _screen, _Live


def _take_the_plane(context, plane: str) -> None:
    """The plane just made is the picked one, whoever asked for it."""
    pick.remember_plane(plane)
    context.scene.cadcore.status = (
        "drawing on %s -- click to place points, Enter to finish" % plane)


def _draw_on_it(context, plane: str) -> None:
    """Hand the pen the plane that was just made.

    A work plane is made to draw on. Finishing here and leaving the person to
    find the new plane and press Draw is two steps for one intent.
    """
    _take_the_plane(context, plane)
    bpy.ops.cadcore.draw_sketch('INVOKE_DEFAULT')


def _newest_plane(context) -> str | None:
    return next((f.name for f in reversed(context.scene.cadcore.features)
                 if f.kind == "plane"), None)


class CADCORE_OT_offset_plane(_Live, bpy.types.Operator):
    bl_idname = "cadcore.offset_plane"
    bl_label = "Plane Off a Face"
    bl_description = ("Drag a work plane out from the selected face; sketches and "
                      "profiles can then be drawn on it")
    bl_options = {'REGISTER', 'UNDO'}

    offset: FloatProperty(name="Offset", default=0.0, options={'SKIP_SAVE'})

    def _make(self, value):
        return ("add_plane", {"face": self.face, "offset": round(value, 3)})

    def execute(self, context):
        face = _one_face(context)
        if face is None:
            self.report({'ERROR'}, "select exactly one planar face")
            return {'CANCELLED'}
        self.face = face
        try:
            out = _send(get_client(context), self._make(self.offset))
            _apply_build(context, out)
        except ServerError as exc:
            return report_error(self, exc)
        _take_the_plane(context, out["feature"])
        push_undo("plane", self)
        return {'FINISHED'}

    def invoke(self, context, event):
        face = _face_to_act_on(context, event)
        if face is None:
            self.report({'ERROR'}, "select exactly one planar face")
            return {'CANCELLED'}
        self.face = face
        try:
            frame = get_client(context).call("face_frame", face=face)
        except ServerError as exc:
            return report_error(self, exc)
        o, n = mathutils.Vector(frame["origin"]), mathutils.Vector(frame["normal"])
        a, b = _screen(context, o), _screen(context, o + n * 10.0)
        if a is None or b is None or (b - a).length < 1e-3:
            self.report({'ERROR'}, "the face is edge-on; orbit a little and try again")
            return {'CANCELLED'}
        self.axis = (b - a)
        self.per_pixel = 10.0 / self.axis.length
        self.axis.normalize()
        self.start = mathutils.Vector((event.mouse_region_x, event.mouse_region_y))
        self.snapper = _snapper(context, face, frame)
        self._begin(context)
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Plane off %s: drag the offset, or type | Enter | Esc" % face)
        return {'RUNNING_MODAL'}

    def _modal(self, context, event):
        if event.type in modal.NAVIGATION:
            return {'PASS_THROUGH'}
        if self._typing(event):
            typed = self._typed_value()
            if typed is not None:
                self._preview(context, typed, self._make, lambda v: {"offset": round(v, 3)})
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE' and not self._typed:
            here = mathutils.Vector((event.mouse_region_x, event.mouse_region_y))
            offset = (here - self.start).dot(self.axis) * self.per_pixel
            offset = round(offset / (0.1 if event.ctrl else 1.0)) * (0.1 if event.ctrl else 1.0)
            level = None
            if not event.ctrl:
                offset, level = self.snapper.distance(offset, self.per_pixel)
            modal.say(context, event,
                      "Plane off %s: %+.1f mm%s | click or Enter to keep | Esc"
                      % (self.face, offset, "  -- level with %s" % level if level else ""),
                      "%+.1f mm" % offset)
            self._preview(context, offset, self._make, lambda v: {"offset": round(v, 3)})
            return {'RUNNING_MODAL'}
        if (event.type == 'LEFTMOUSE' and event.value == 'RELEASE') or \
                (event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS'):
            if self._value is None:
                return self._cancel(context)
            self.offset = self._value
            done = self._finish(context, self._make, "plane")
            plane = _newest_plane(context)
            if done == {'FINISHED'} and plane:
                _draw_on_it(context, plane)
            return done
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._cancel(context)
        return {'RUNNING_MODAL'}


class CADCORE_OT_plane_between(bpy.types.Operator):
    """A work plane halfway between two parallel faces, and the pen on it."""

    bl_idname = "cadcore.plane_between"
    bl_label = "Draw Between These Faces"
    bl_description = ("Put a work plane halfway between the two picked faces and "
                      "start drawing on it; its offset is a number you can retype")
    bl_options = {'REGISTER', 'UNDO'}

    offset: FloatProperty(name="Offset", default=0.0, options={'SKIP_SAVE'},
                          description="from halfway, along the first face's normal")

    def execute(self, context):
        faces = sync.selected_face_names()
        if len(faces) != 2:
            self.report({'ERROR'}, "select two parallel faces")
            return {'CANCELLED'}
        try:
            out = get_client(context).call("add_plane", between=faces,
                                           offset=round(self.offset, 3))
            _apply_build(context, out)
        except ServerError as exc:
            return report_error(self, exc)
        self.plane = out["feature"]
        _take_the_plane(context, self.plane)
        push_undo("plane", self)
        return {'FINISHED'}

    def invoke(self, context, event):
        if self.execute(context) == {'CANCELLED'}:
            return {'CANCELLED'}
        _draw_on_it(context, self.plane)
        return {'FINISHED'}


class CADCORE_OT_turn_plane(_Live, bpy.types.Operator):
    """Hang a work plane on a picked edge and turn it about that edge.

    The plane keeps the edge, so it stays touching the part however the face
    behind it moves. Letting go leaves the pen on it.
    """

    bl_idname = "cadcore.turn_plane"
    bl_label = "Turn a Plane About This Edge"
    bl_description = ("Hang a work plane on the picked edge and drag the ring for "
                      "the angle; the pen lands on it")
    bl_options = {'REGISTER', 'UNDO'}

    angle: FloatProperty(name="Angle", default=45.0, options={'SKIP_SAVE'},
                         description="degrees from the face the edge belongs to")

    def _make(self, value):
        return ("add_plane", {"face": self.face, "about": self.edge,
                              "angle": round(value, 2)})

    def _edge(self, context):
        try:
            edges = sync.selected_edge_names(get_client(context))
        except ServerError as exc:
            report_error(self, exc)
            return None
        if len(edges) != 1:
            self.report({'ERROR'}, "pick one straight edge to hang the plane on")
            return None
        return edges[0]

    def execute(self, context):
        edge = self._edge(context)
        if edge is None:
            return {'CANCELLED'}
        self.edge, self.face = edge, names.first_face(edge)
        try:
            out = _send(get_client(context), self._make(self.angle))
            _apply_build(context, out)
        except ServerError as exc:
            return report_error(self, exc)
        _take_the_plane(context, out["feature"])
        push_undo("plane", self)
        return {'FINISHED'}

    def invoke(self, context, event):
        edge = self._edge(context)
        if edge is None:
            return {'CANCELLED'}
        self.edge, self.face = edge, names.first_face(edge)
        handle = pick.EDGE_HANDLE
        if "along" not in handle:
            self.report({'ERROR'}, "a plane turns about a straight edge; this one curves")
            return {'CANCELLED'}
        try:
            frame = get_client(context).call("face_frame", face=self.face)
        except ServerError as exc:
            return report_error(self, exc)
        axis = mathutils.Vector(handle["along"]).normalized()
        # the angle is measured from the face itself, so zero leaves the plane
        # lying on it -- and the zero direction has to be square to the hinge
        normal = mathutils.Vector(frame["normal"])
        zero = (normal - axis * normal.dot(axis))
        if zero.length < 1e-6:
            self.report({'ERROR'}, "the edge runs along the face's own normal")
            return {'CANCELLED'}
        self.ring = {"origin": tuple(mathutils.Vector(handle["origin"])),
                     "normal": tuple(axis), "x_axis": tuple(zero.normalized())}
        self.radius = 0.35 * (max((max(ob.dimensions) for ob in sync.bodies()),
                                  default=40.0) or 40.0)
        pick.RING[0] = dict(self.ring, radius=self.radius, angle=0.0)
        self._begin(context)
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set(
            "Plane on %s: drag round the ring, or type | Enter | Esc" % self.edge)
        return {'RUNNING_MODAL'}

    def _angle_at(self, context, event):
        """Where the cursor is round the ring, in degrees from the face."""
        hit = modal.plane_hit(context, event, self.ring)
        if hit is None:
            return None
        u, v = drawing.to_uv(hit, self.ring)
        if u * u + v * v < 1e-9:
            return None
        return math.degrees(math.atan2(v, u))

    def _modal(self, context, event):
        if event.type in modal.NAVIGATION:
            return {'PASS_THROUGH'}
        if self._typing(event):
            typed = self._typed_value()
            if typed is not None:
                modal.say(context, event, "Plane on %s: %s degrees (typed) | Enter | Esc"
                          % (self.edge, self._typed), self._typed + " deg")
                self._preview(context, typed, self._make, lambda v: {"angle": round(v, 2)})
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE' and not self._typed:
            angle = self._angle_at(context, event)
            if angle is None:
                return {'RUNNING_MODAL'}
            angle = round(angle / (1.0 if event.ctrl else 5.0)) * (1.0 if event.ctrl else 5.0)
            pick.RING[0] = dict(self.ring, radius=self.radius, angle=angle)
            modal.say(context, event,
                      "Plane on %s: %+.0f degrees | click or Enter to keep | Esc"
                      % (self.edge, angle), "%+.0f deg" % angle)
            self._preview(context, angle, self._make, lambda v: {"angle": round(v, 2)})
            return {'RUNNING_MODAL'}
        if (event.type == 'LEFTMOUSE' and event.value == 'RELEASE') or \
                (event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS'):
            if self._value is None:
                return self._cancel(context)
            self.angle = self._value
            done = self._finish(context, self._make, "plane")
            plane = _newest_plane(context)
            if done == {'FINISHED'} and plane:
                _draw_on_it(context, plane)
            return done
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._cancel(context)
        return {'RUNNING_MODAL'}

    def _finish(self, context, make, label):
        pick.RING[0] = None
        return super()._finish(context, make, label)

    def _cancel(self, context):
        pick.RING[0] = None
        return super()._cancel(context)
