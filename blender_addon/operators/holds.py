"""The sketch on the part: drag one of its points, and hold what is picked.

The sketch is drawn where it lies, its points and lines are picked with the
same tool as a face, and these two operators are everything that can then be
done to them. There is no sketch mode to be in or to leave. Which holds fit
which picks is in :mod:`viewport.holding`, which needs no Blender.
"""
from __future__ import annotations

import bpy
from bpy.props import StringProperty

from ..link.client import ServerError
from ..link.state import _apply_build, get_client, push_undo, report_error
from ..viewport import drawing, modal, pick
from ..viewport.holding import HOLDS, build


def _geometry(operator, context, sketch: str):
    try:
        return get_client(context).call("sketch_geometry", sketch=sketch)
    except ServerError as exc:
        report_error(operator, exc)
        return None


def _redraw(context) -> None:
    from ..ui import overlay

    overlay.forget_sketch_cache()      # the solid may be the same size, the sketch is not
    for area in context.screen.areas:
        area.tag_redraw()


class CADCORE_OT_hold_sketch(bpy.types.Operator):
    """Hold what is picked on the sketch: level, parallel, together, and the rest."""

    bl_idname = "cadcore.hold_sketch"
    bl_label = "Hold It"
    bl_description = "Add this hold to what is picked on the sketch drawn on the part"
    bl_options = {'REGISTER', 'UNDO'}

    kind: StringProperty(name="Hold", default="", options={'SKIP_SAVE'})

    def execute(self, context):
        sketch, picks = pick.SKETCH[0], pick.sketch_picks()
        if sketch is None or not picks:
            self.report({'ERROR'}, "pick a point or a line of the sketch first")
            return {'CANCELLED'}
        geometry = _geometry(self, context, sketch)
        if geometry is None:
            return {'CANCELLED'}
        constraint = build(self.kind, picks, geometry)
        if constraint is None:
            self.report({'ERROR'}, "%s needs %s" %
                        (self.kind, HOLDS.get(self.kind, ("", "a different pick"))[1]))
            return {'CANCELLED'}
        try:
            out = get_client(context).call("add_constraint", sketch=sketch,
                                           constraint=constraint)
        except ServerError as exc:
            return report_error(self, exc)
        _apply_build(context, out)
        push_undo("hold", self)
        from ..link import state

        state._refresh_constraints(context)
        pick.forget_sketch_picks()
        _redraw(context)
        dof = context.scene.cadcore.sketch_dof
        context.scene.cadcore.status = "%s -- %s" % (
            HOLDS.get(self.kind, (self.kind, ""))[0],
            "the sketch is held" if dof == 0 else "%d left free" % dof)
        return {'FINISHED'}


class CADCORE_OT_drag_sketch_point(bpy.types.Operator):
    """Drag a point of the sketch drawn on the part; the holds decide what
    moves, and a held point says what holds it."""

    bl_idname = "cadcore.drag_sketch_point"
    bl_label = "Drag a Sketch Point"
    bl_description = ("Move a point of the sketch; what the holds leave free moves, "
                      "and a held point says what holds it")
    bl_options = {'REGISTER', 'UNDO'}

    sketch: StringProperty(default="", options={'SKIP_SAVE'})
    point: StringProperty(default="", options={'SKIP_SAVE'})

    def invoke(self, context, event):
        geometry = _geometry(self, context, self.sketch)
        if geometry is None:
            return {'CANCELLED'}
        if self.point not in geometry["points"]:
            self.report({'ERROR'}, "the sketch has no point %s" % self.point)
            return {'CANCELLED'}
        self.frame = geometry["plane"]
        self.at = None
        self.moved = False
        pick.SKETCH_DRAG[0] = (self.point, tuple(geometry["points"][self.point]))
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set(
            "%s: drag it | let go to keep | Esc" % self.point)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in modal.NAVIGATION:
            return {'PASS_THROUGH'}
        if event.type == 'MOUSEMOVE':
            hit = modal.plane_hit(context, event, self.frame)
            if hit is not None:
                self.at = drawing.to_uv(hit, self.frame)
                self.moved = True
                pick.SKETCH_DRAG[0] = (self.point, tuple(self.at))
                context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            # a press that did not move is a pick, not a drag: it left the
            # point picked on the way in, and that is all it was for
            if self.moved and self.at is not None:
                return self._apply(context)
            return self._done(context, "")
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._done(context, "")
        return {'RUNNING_MODAL'}

    def _apply(self, context):
        try:
            out = get_client(context).call("drag_point", sketch=self.sketch,
                                           point=self.point, to=list(self.at))
        except ServerError as exc:
            self._done(context, "")
            return report_error(self, exc)
        if out["held"]:
            holds = ", ".join(sorted({c["type"] for c in out["held_by"]})) or "the sketch"
            numbers = ", ".join(out["parameters"][:3]) or "a dimension"
            return self._done(context, "%s is held by %s -- change %s to move it"
                              % (self.point, holds, numbers))
        _apply_build(context, out)
        push_undo("drag point", self)
        return self._done(context, "moved " + ", ".join(sorted(out["moved"])))

    def _done(self, context, message: str):
        pick.SKETCH_DRAG[0] = None
        context.area.header_text_set(None)
        if message:
            context.scene.cadcore.status = message
            self.report({'INFO'}, message)
        _redraw(context)
        return {'FINISHED'}
