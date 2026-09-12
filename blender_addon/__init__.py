"""cadcore bridge: parametric CAD inside Blender.

The loop is viewport selection -> CAD face/edge name -> feature edit ->
rebuild -> mesh. The kernel runs out of process (``link/client.py``);
``link/`` is the connection, ``ui/`` the panels, ``viewport/`` the drags.
"""
from __future__ import annotations

import bpy

from .ui import animate, changes, gallery, icons, menus, overlay, panels, props, selection  # noqa: F401
from .link import bridge, client, state, sync, throttle
from .viewport import (boolean, box, drawing, fillet, gpu_draw, clipping, holding, hole, live,  # noqa: F401
                       marks, modal, move_part, pick, plane, press_pull, section, sideways, split,
                       toolbar)
from .operators import (actions, analysis, assembly, assistant, documents, draw_op, edits, holds, kernel,  # noqa: F401
                        modelling, picking)

bl_info = {
    "name": "RealParts",
    "author": "oss-cad",
    "version": (0, 2, 4),
    # Blender 5.1 bundles Python 3.13, the interpreter the kernel's wheels are
    # built for; the Install button has nothing to install into on 4.x.
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > CAD",
    "description": "Parametric BREP modelling with stable face names, driven from the viewport",
    "category": "Object",
}

def _operators() -> tuple:
    """Every operator the add-on defines, found in its modules; the order does not matter."""
    import inspect

    out = []
    for module in (actions, documents, modelling, picking, assistant, analysis, assembly, kernel, draw_op, holds,
                   overlay, press_pull, hole, fillet, box, plane, move_part, boolean, pick, section,
                   split, sideways):
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, bpy.types.Operator) and cls.__module__ == module.__name__ and cls not in out:
                out.append(cls)
    return tuple(out)


CLASSES = (
    props.CADCORE_Parameter, props.CADCORE_FeatureArg, props.CADCORE_FeatureItem,
    props.CADCORE_ReportItem, props.CADCORE_TranscriptLine, props.CADCORE_StudyItem, props.CADCORE_RequirementItem,
    props.CADCORE_ConstraintItem,
    props.CADCORE_FamilyItem,
    props.CADCORE_Props, props.CADCORE_Prefs,
    *_operators(),
    menus.CADCORE_MT_menu,
    menus.CADCORE_MT_more,
    menus.CADCORE_MT_create, menus.CADCORE_MT_modify, menus.CADCORE_MT_assemble,
    menus.CADCORE_MT_inspect, menus.CADCORE_MT_output,
    panels.CADCORE_UL_features, panels.CADCORE_PT_panel,
    panels.CADCORE_PT_tools, panels.CADCORE_PT_edit,
    panels.CADCORE_PT_assistant, panels.CADCORE_PT_export,
    panels.CADCORE_PT_parameters,
    panels.CADCORE_PT_features,
    panels.CADCORE_PT_sketch,
    # Blender requires parent panels to be registered before their sub-panels.
    panels.CADCORE_PT_analysis, panels.CADCORE_PT_requirements, panels.CADCORE_PT_studies,
    panels.CADCORE_PT_printing, panels.CADCORE_PT_checks,
)

_keymaps = []


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.cadcore = bpy.props.PointerProperty(type=props.CADCORE_Props)
    toolbar.register_tools()
    state.register_handler()
    gallery.load()
    bpy.types.VIEW3D_MT_editor_menus.append(menus.draw_header_menu)
    bpy.types.TEXT_HT_header.append(menus.draw_ask_header)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.prepend(menus.draw_body_context_menu)
    bpy.types.VIEW3D_MT_object_context_menu.prepend(menus.draw_object_context_menu)
    # Ctrl+Enter in the Text Editor sends the Ask text
    configs = bpy.context.window_manager.keyconfigs.addon
    if configs is not None:
        keymap = configs.keymaps.new(name="Text", space_type='TEXT_EDITOR')
        item = keymap.keymap_items.new("cadcore.ask", type='RET', value='PRESS', ctrl=True)
        _keymaps.append((keymap, item))
        keymap = configs.keymaps.new(name="3D View", space_type='VIEW_3D')
        item = keymap.keymap_items.new("cadcore.pick_under_cursor", type='LEFTMOUSE', value='PRESS')
        item.properties.sketches_only = True
        _keymaps.append((keymap, item))

    # Ctrl+Z is not bound here: modelling operators register a Blender undo
    # step and `state.on_undo` moves the kernel to wherever Blender lands,
    # which is also what makes F9 work.


def unregister():
    toolbar.unregister_tools()
    bridge.stop()            # close the socket before the add-on is gone
    bpy.types.VIEW3D_MT_editor_menus.remove(menus.draw_header_menu)
    bpy.types.TEXT_HT_header.remove(menus.draw_ask_header)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(menus.draw_body_context_menu)
    bpy.types.VIEW3D_MT_object_context_menu.remove(menus.draw_object_context_menu)
    overlay.disable()
    throttle.forget()        # drop timers that would run into a dead add-on
    state.unregister_handler()
    gallery.unload()
    state.stop_client()
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()
    del bpy.types.Scene.cadcore
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
