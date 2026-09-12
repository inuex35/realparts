"""Operators about the pick: what is selected, selecting by name."""
from __future__ import annotations

import json

import bpy
from bpy.props import StringProperty

from ..link import sync
from ..link.client import ServerError
from ..link import state
from ..link.state import _apply_build, get_client, push_undo, report_error


class CADCORE_OT_pick(bpy.types.Operator):
    """Go to picking faces or edges on the body, whatever mode Blender is in."""

    bl_idname = "cadcore.pick"
    bl_label = "Pick"
    bl_description = "Pick faces or edges on the part (edit mode, the right select mode)"

    what: bpy.props.EnumProperty(items=[('FACE', "Faces", ""), ('EDGE', "Edges", "")], default='FACE')

    def execute(self, context):
        body = sync.body()
        if body is None:
            self.report({'ERROR'}, "there is no part to pick on yet")
            return {'CANCELLED'}
        from ..viewport import section

        section.clear(context)      # a clipped view cannot survive edit mode
        for other in context.view_layer.objects:
            other.select_set(other is body)
        context.view_layer.objects.active = body
        if body.mode != 'EDIT':
            bpy.ops.object.mode_set(mode='EDIT')
            # the tool is per mode: without this the click that follows is
            # Blender's own select, and the arrow on the face cannot be grabbed
            state.hand_the_pick_tool(context)
        bpy.ops.mesh.select_mode(type=self.what)
        context.scene.cadcore.status = "click a %s; right-click for what can be done to it" % (
            "face" if self.what == 'FACE' else "edge")
        return {'FINISHED'}


class CADCORE_OT_select_faces(bpy.types.Operator):
    """Select faces on the body by CAD name -- the panel's way of pointing at one."""

    bl_idname = "cadcore.select_faces"
    bl_label = "Select Face"
    bl_description = "Select this face on the body"

    names: StringProperty()             # comma separated

    def execute(self, context):
        wanted = [n.strip() for n in self.names.split(",") if n.strip()]
        found = sync.select_faces(wanted)
        if not found:
            self.report({'WARNING'}, "no such face on the body: %s" % ", ".join(wanted))
            return {'CANCELLED'}
        return {'FINISHED'}


class CADCORE_OT_show_selection(bpy.types.Operator):
    bl_idname = "cadcore.show_selection"
    bl_label = "What Is Selected"
    bl_description = "Report the CAD names behind the current selection"

    def execute(self, context):
        client = get_client(context)
        try:
            edges = sync.selected_edge_names(client)
        except ServerError as exc:
            return report_error(self, exc)
        faces = sync.selected_face_names()
        text = "faces: %s | edges: %s" % (", ".join(faces) or "-",
                                          ", ".join(edges[:4]) or "-")
        stress = {}
        for ob in sync.bodies():          # the field is written on the part it was solved on
            stress.update(json.loads(ob["cad_face_stress"]) if "cad_face_stress" in ob else {})
        hot = ["%s %.1f MPa" % (f, stress[f]["max_MPa"]) for f in faces if f in stress]
        if hot:
            text += " | peak " + ", ".join(hot)
        context.scene.cadcore.status = text
        self.report({'INFO'}, text)
        return {'FINISHED'}
