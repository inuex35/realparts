"""The tools on Blender's toolbar, and their registration.

Only the ones that place many things in a row, where holding the tool is
the point: the pen and the hole. Everything else is reached from the pick.
"""
from __future__ import annotations


import bpy

from .pick import CADCORE_TOOL_pick, CADCORE_TOOL_pick_edit


# ---------------------------------------------------------------------------
# toolbar tools: with one active, a left click runs the operator
class _Tool(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'


class CADCORE_TOOL_hole(_Tool):
    bl_idname = "cadcore.tool_hole"
    bl_label = "CAD Hole"
    bl_description = "Press on a face and click where the holes go; wheel for size"
    bl_icon = "ops.mesh.inset"
    bl_keymap = (("cadcore.place_hole", {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),)


class CADCORE_TOOL_draw(_Tool):
    bl_idname = "cadcore.tool_draw"
    bl_label = "CAD Draw"
    bl_description = "Press on a face and draw a profile on it"
    bl_icon = "ops.gpencil.draw"
    bl_keymap = (("cadcore.draw_sketch", {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),)


OBJECT_TOOLS = (CADCORE_TOOL_pick, CADCORE_TOOL_draw, CADCORE_TOOL_hole)


def _edit_twin(tool):
    """The same tool, in the edit-mode toolbar.

    Blender keeps one set of tools per mode, and picking a face puts the body
    in edit mode. A twin there is what keeps every CAD tool one click away.
    The operator is the same one: each drag leaves edit mode as it starts.
    """
    return type(tool.__name__ + "_edit", (tool,),
                {"bl_idname": tool.bl_idname + "_edit", "bl_context_mode": 'EDIT_MESH'})


EDIT_TOOLS = (CADCORE_TOOL_pick_edit,) + tuple(_edit_twin(t) for t in OBJECT_TOOLS[1:])
TOOLS = OBJECT_TOOLS + EDIT_TOOLS


def register_tools() -> None:
    for group in (OBJECT_TOOLS, EDIT_TOOLS):        # each mode has its own toolbar
        previous = None
        for tool in group:
            try:
                bpy.utils.register_tool(tool, after={previous} if previous else None,
                                        separator=previous is None, group=False)
            except Exception as exc:                                   # noqa: BLE001
                print("cadcore: tool %s not registered -- %s" % (tool.bl_idname, exc))
            previous = tool.bl_idname


def unregister_tools() -> None:
    for tool in reversed(TOOLS):
        try:
            bpy.utils.unregister_tool(tool)
        except Exception:                                              # noqa: BLE001
            pass
