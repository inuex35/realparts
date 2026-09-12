"""Click a face where the holes go; the wheel sets the size."""
from __future__ import annotations


import bpy
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty

from . import drawing, gpu_draw, modal
from ..link.client import ServerError
from ..link.state import _apply_build, get_client, push_undo, report_error, set_error
from .live import _snapper, _face_to_act_on, _one_face


#: clearance hole diameters the wheel steps through, in mm (ISO 273 medium)
HOLE_STEPS = ((3.4, "M3"), (4.5, "M4"), (5.5, "M5"), (6.6, "M6"), (9.0, "M8"),
              (11.0, "M10"), (13.5, "M12"))


class CADCORE_OT_place_hole(modal.ViewportTool, bpy.types.Operator):
    bl_idname = "cadcore.place_hole"
    bl_label = "Hole Here"
    bl_description = ("Click on the selected face to put a through hole there; the "
                      "wheel steps the clearance size (M3..M12). Esc when done")
    bl_options = {'REGISTER', 'UNDO'}

    at: FloatVectorProperty(name="At", size=2, default=(0.0, 0.0), options={'SKIP_SAVE'},
                            description="given, the hole goes here in the face's frame")
    diameter: FloatProperty(name="Diameter", default=6.6, min=0.1)
    place: BoolProperty(default=False, options={'SKIP_SAVE', 'HIDDEN'})

    def execute(self, context):
        face = _one_face(context)
        if face is None:
            self.report({'ERROR'}, "select exactly one planar face to drill")
            return {'CANCELLED'}
        try:
            out = get_client(context).call("add_hole", face=face, diameter=self.diameter,
                                           at=[float(self.at[0]), float(self.at[1])])
            _apply_build(context, out)
        except ServerError as exc:
            return report_error(self, exc)
        context.scene.cadcore.status = "%s: hole %.1f at (%.1f, %.1f)" % (
            face, self.diameter, self.at[0], self.at[1])
        push_undo("hole", self)
        return {'FINISHED'}

    def invoke(self, context, event):
        face = _face_to_act_on(context, event)
        if face is None:
            self.report({'ERROR'}, "select exactly one planar face to drill")
            return {'CANCELLED'}
        self.face = face
        try:
            self.frame = get_client(context).call("face_frame", face=face)
        except ServerError as exc:
            return report_error(self, exc)
        self.index = next((i for i, (d, _) in enumerate(HOLE_STEPS)
                           if abs(d - self.diameter) < 1e-6), 3)
        self.cursor = None
        self.snapped = None
        self.placed = 0
        self.snapper = _snapper(context, face, self.frame)
        self._watch(context)
        context.window_manager.modal_handler_add(self)
        self._say(context)
        return {'RUNNING_MODAL'}

    def _say(self, context):
        d, name = HOLE_STEPS[self.index]
        context.area.header_text_set(
            "Hole on %s: click to drill %s clearance (%.1f mm) | wheel: size | "
            "%d placed | Esc to finish" % (self.face, name, d, self.placed))

    def _draw(self, context):
        if self.cursor is None:
            return
        d, _ = HOLE_STEPS[self.index]
        ring = [drawing.to_3d(p, self.frame) for p in drawing.circle_points(self.cursor, d / 2)]
        gpu_draw.line_strip(ring + ring[:1], (1.0, 0.75, 0.2, 1.0))
        if self.snapped:
            gpu_draw.points([drawing.to_3d(self.cursor, self.frame)], (0.3, 1.0, 0.5, 1.0), 9.0)

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

    def modal(self, context, event):
        if event.type in {'MIDDLEMOUSE'}:
            return {'PASS_THROUGH'}
        context.area.tag_redraw()
        if event.type == 'MOUSEMOVE':
            self._track(context, event)
            if self.snapped:
                d, name = HOLE_STEPS[self.index]
                context.area.header_text_set("Hole on %s: %s at (%.1f, %.1f) -- on %s | click | Esc"
                                             % (self.face, name, self.cursor[0], self.cursor[1], self.snapped))
            else:
                self._say(context)
            return {'RUNNING_MODAL'}
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} and event.value == 'PRESS':
            self.index = (self.index + (1 if event.type == 'WHEELUPMOUSE' else -1)) % len(HOLE_STEPS)
            self._say(context)
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self._track(context, event)
            if self.cursor is None:
                return {'RUNNING_MODAL'}
            d, name = HOLE_STEPS[self.index]
            try:
                out = get_client(context).call("add_hole", face=self.face, standard=name,
                                               fit="normal", at=[round(self.cursor[0], 2),
                                                                 round(self.cursor[1], 2)])
                _apply_build(context, out)
            except ServerError as exc:
                set_error(context, exc)
                return {'RUNNING_MODAL'}
            self.placed += 1
            push_undo("hole %s" % name, self)
            self.diameter = d
            self._say(context)
            return {'RUNNING_MODAL'}
        if event.type in {'ESC', 'RIGHTMOUSE', 'RET'} and event.value == 'PRESS':
            self._teardown(context)
            context.scene.cadcore.status = "%d hole(s) on %s" % (self.placed, self.face)
            return {'FINISHED'} if self.placed else {'CANCELLED'}
        return {'RUNNING_MODAL'}
