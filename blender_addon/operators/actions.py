"""Start common face edits before or after selecting their target."""
import bpy

from ..link import sync
from ..ui import selection


FACE_ACTIONS = {
    'hole': ("Drill a Hole", "hole_face"),
    'pocket': ("Cut a Pocket", "pocket_face"),
    'push': ("Push / Pull", "move_face"),
}


class CADCORE_OT_begin_face_action(bpy.types.Operator):
    bl_idname = "cadcore.begin_face_action"
    bl_label = "Choose a Face to Edit"
    bl_description = "Use the picked planar face, or click one in the model; Esc cancels"

    action: bpy.props.EnumProperty(items=[(key, value[0], "") for key, value in FACE_ACTIONS.items()])

    @classmethod
    def poll(cls, context):
        props = getattr(context.scene, "cadcore", None)
        return bool(props and props.has_body and not props.assistant_busy
                    and context.area and context.area.type == 'VIEW_3D')

    def execute(self, context):
        names = sync.selected_face_names()
        if len(names) != 1 or selection.face_shape(context, names) != 'plane':
            self.report({'ERROR'}, "Pick one planar face")
            return {'CANCELLED'}
        result = getattr(bpy.ops.cadcore, FACE_ACTIONS[self.action][1])('INVOKE_DEFAULT')
        return {'CANCELLED'} if result == {'CANCELLED'} else {'FINISHED'}

    def invoke(self, context, event):
        context.scene.cadcore.status_is_error = False
        names = sync.selected_face_names()
        if len(names) == 1 and selection.face_shape(context, names) == 'plane':
            return self.execute(context)
        self.area = context.area
        self.region = next((r for r in self.area.regions if r.type == 'WINDOW'), None)
        if self.region is None:
            return {'CANCELLED'}
        self.hint = FACE_ACTIONS[self.action][0] + ": click a planar face | Esc to cancel"
        self.area.header_text_set(self.hint)
        context.scene.cadcore.status = self.hint
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self.cancel(context)
            return {'CANCELLED'}
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            x, y = event.mouse_x - self.region.x, event.mouse_y - self.region.y
            if not (0 <= x < self.region.width and 0 <= y < self.region.height):
                return {'RUNNING_MODAL'}
            from ..viewport.pick import _face_at
            with context.temp_override(area=self.area, region=self.region):
                face, _, _ = _face_at(context, (x, y))
                if not face or selection.face_shape(context, [face]) != 'plane':
                    context.scene.cadcore.status = "Choose a flat CAD face; Esc cancels"
                    self.area.tag_redraw()
                    return {'RUNNING_MODAL'}
                sync.select_faces([face])
                self.area.header_text_set(None)
                context.scene.cadcore.status = FACE_ACTIONS[self.action][0] + ": adjust the settings"
                return self.execute(context)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self.area.header_text_set(None)
        context.scene.cadcore.status = "Cancelled; the model is unchanged"
        self.area.tag_redraw()
