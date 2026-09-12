"""Click one part, then another: cut, fuse or intersect."""
from __future__ import annotations


import bpy
from bpy.props import StringProperty

from . import modal
from ..link.client import ServerError
from ..link.state import _apply_build, get_client, push_undo, report_error, set_error
from .live import _face_under_cursor


class CADCORE_OT_pick_boolean(bpy.types.Operator):
    bl_idname = "cadcore.pick_boolean"
    bl_label = "Cut / Fuse by Picking"
    bl_description = ("Click a face of the part to keep, then a face of the part to "
                      "cut it with (Shift-click to fuse instead); the assembly is "
                      "re-pointed at the result")
    bl_options = {'REGISTER', 'UNDO'}

    target: StringProperty(name="Keep", default="", options={'SKIP_SAVE'})
    tool: StringProperty(name="With", default="", options={'SKIP_SAVE'})
    kind: bpy.props.EnumProperty(name="Do", default='cut', items=(
        ('cut', "Cut", "take the tool out of the target"),
        ('fuse', "Fuse", "join them into one"),
        ('common', "Common", "keep only where they overlap")))

    def _apply(self, context, target, tool, kind):
        client = get_client(context)
        doc = client.call("document_json")["document"]
        assemble = next((f for f in doc["features"] if f["type"] == "assemble"), None)
        if assemble is None:
            self.report({'ERROR'}, "picking two parts needs an assembly")
            return {'CANCELLED'}
        new_id = "%s_%s" % (target, kind)
        bodies = list(assemble.get("bodies", []))
        if target not in bodies or tool not in bodies:
            self.report({'ERROR'}, "%s and %s must both be assembled bodies" % (target, tool))
            return {'CANCELLED'}
        ops = [{"op": "add_feature", "type": kind, "args": {"target": target, "tool": tool},
                "feature_id": new_id, "after": assemble["id"] if False else None},
               {"op": "edit_feature", "feature_id": assemble["id"],
                "args": {"bodies": [new_id if b == target else b for b in bodies if b != tool]}}]
        # the boolean must precede the assemble that lists it: insert it after
        # the later of the two bodies
        order = [f["id"] for f in doc["features"]]
        ops[0]["after"] = max(target, tool, key=order.index)
        try:
            out = client.call("apply", ops=ops)
            _apply_build(context, out)
        except ServerError as exc:
            return report_error(self, exc)
        context.scene.cadcore.status = "%s: %s %s %s" % (new_id, target, kind, tool)
        context.scene.cadcore.status_is_error = False
        push_undo(kind, self)
        return {'FINISHED'}

    def execute(self, context):
        if not (self.target and self.tool):
            self.report({'ERROR'}, "name the part to keep and the part to use")
            return {'CANCELLED'}
        return self._apply(context, self.target, self.tool, self.kind)

    def invoke(self, context, event):
        self.first = None
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Cut/Fuse: click a face of the part to KEEP | Esc")
        return {'RUNNING_MODAL'}

    def _body_of(self, context, event):
        face, _ = _face_under_cursor(context, event)
        if face is None:
            return None
        try:
            return get_client(context).call("part_of", face=face)["body"]
        except ServerError as exc:
            set_error(context, exc)
            return None

    def modal(self, context, event):
        if event.type in modal.NAVIGATION or event.type == 'MOUSEMOVE':
            return {'PASS_THROUGH'}
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            context.area.header_text_set(None)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            body = self._body_of(context, event)
            if body is None:
                return {'RUNNING_MODAL'}
            if self.first is None:
                self.first = body
                context.area.header_text_set("Cut/Fuse: keeping %s -- click a face of the part to cut WITH "
                                             "(Shift: fuse, Alt: common) | Esc" % body)
                return {'RUNNING_MODAL'}
            if body == self.first:
                return {'RUNNING_MODAL'}
            kind = 'fuse' if event.shift else ('common' if event.alt else 'cut')
            context.area.header_text_set(None)
            return self._apply(context, self.first, body, kind)
        return {'RUNNING_MODAL'}
