"""Drag the radius of a fillet on the picked edges."""
from __future__ import annotations


import bpy
import mathutils
from bpy.props import FloatProperty, StringProperty

from . import modal
from ..link import sync
from ..link.client import ServerError
from ..link.state import _apply_build, get_client, push_undo, report_error
from .live import _Live


def _colour_the_grip(kind) -> None:
    """Say what the drag is making, so the grip can show it; None between drags."""
    from . import pick

    pick.DRAGGING[0] = kind


class CADCORE_OT_drag_fillet(_Live, bpy.types.Operator):
    bl_idname = "cadcore.drag_fillet"
    bl_label = "Drag a Fillet"
    bl_description = ("Round the selected edges, dragging the radius until it looks "
                      "right; letting go is one undoable step")
    bl_options = {'REGISTER', 'UNDO'}

    radius: FloatProperty(name="Radius", default=0.0, min=0.0, options={'SKIP_SAVE'},
                          description="given, the fillet is made at this radius with no dragging")

    #: what the grip on the edge is showing; a chamfer's size is a `distance`
    kind: StringProperty(name="Kind", default="fillet", options={'SKIP_SAVE'})

    def _make(self, value):
        return ("add_fillet", {"edges": self.edges, "radius": round(value, 3),
                               "kind": self.kind})

    def _edit(self, value) -> dict:
        return {"radius" if self.kind == "fillet" else "distance": round(value, 3)}

    def _edges(self, context):
        try:
            edges = sync.selected_edge_names(get_client(context))
        except ServerError as exc:
            report_error(self, exc)
            return None
        if not edges:
            self.report({'ERROR'}, "select edges, or the face(s) whose edges to round")
            return None
        return edges

    def execute(self, context):
        edges = self._edges(context)
        if edges is None:
            return {'CANCELLED'}
        self.edges = edges
        try:
            out = get_client(context).call("add_fillet", edges=edges, radius=self.radius,
                                           kind=self.kind)
            _apply_build(context, out)
        except ServerError as exc:
            return report_error(self, exc)
        push_undo("fillet", self)
        return {'FINISHED'}

    def invoke(self, context, event):
        edges = self._edges(context)
        if edges is None:
            return {'CANCELLED'}
        self.edges = edges
        body = sync.body()
        centre = body.matrix_world @ (0.125 * sum((mathutils.Vector(c) for c in body.bound_box),
                                                  mathutils.Vector()))
        self.per_pixel = modal.pixel_size(context, centre)      # mm per pixel of drag
        self.start = event.mouse_region_x
        _colour_the_grip(self.kind)
        self._begin(context)
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("%s %d edge(s): drag right for more | "
                                     "click or Enter to keep | Esc"
                                     % (self.kind.title(), len(edges)))
        return {'RUNNING_MODAL'}

    def _modal(self, context, event):
        if event.type in modal.NAVIGATION:
            return {'PASS_THROUGH'}
        if self._typing(event):
            typed = self._typed_value()
            if typed is not None and typed > 0:
                modal.say(context, event,
                          "%s: %s mm (typed) | Enter to keep | Esc"
                          % (self.kind.title(), self._typed), self._typed + " mm")
                self._preview(context, typed, self._make, self._edit)
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE' and not self._typed:
            radius = max(0.0, (event.mouse_region_x - self.start) * self.per_pixel)
            radius = round(radius / (0.1 if event.ctrl else 0.5)) * (0.1 if event.ctrl else 0.5)
            if radius <= 0.0:
                return {'RUNNING_MODAL'}
            modal.say(context, event,
                      "%s %d edge(s): %.1f mm | click or Enter to keep | Esc"
                      % (self.kind.title(), len(self.edges), radius), "%.1f mm" % radius)
            self._preview(context, radius, self._make, self._edit)
            return {'RUNNING_MODAL'}
        if (event.type == 'LEFTMOUSE' and event.value == 'RELEASE') or \
                (event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS'):
            if self._value is None:
                return self._cancel(context)
            self.radius = self._value
            return self._finish(context, self._make, self.kind)
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._cancel(context)
        return {'RUNNING_MODAL'}

    def _finish(self, context, make, label):
        _colour_the_grip(None)
        return super()._finish(context, make, label)

    def _cancel(self, context):
        _colour_the_grip(None)
        return super()._cancel(context)
