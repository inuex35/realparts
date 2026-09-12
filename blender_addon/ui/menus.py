"""The menus: the CAD header menu, the right-click menus that follow the
pick, and the Send button in the Ask editor's header."""
from __future__ import annotations

import bpy

from ..link import sync
from . import selection


class CADCORE_MT_menu(bpy.types.Menu):
    """The same tool categories as the sidebar, reached from the header."""

    bl_label = "CAD"
    bl_idname = "CADCORE_MT_menu"

    def draw(self, context):
        layout = self.layout
        draw_categories(layout)
        layout.separator()
        layout.operator("cadcore.ask_dialog", text="Ask...", icon='LIGHT')
        layout.operator("cadcore.export_step", text="Export STEP", icon='EXPORT')
        layout.separator()
        layout.menu(CADCORE_MT_more.bl_idname, text="Utilities", icon='COLLAPSEMENU')


def draw_categories(layout):
    """Stable places to discover tools, also used by the sidebar."""
    for menu in (CADCORE_MT_create, CADCORE_MT_modify, CADCORE_MT_assemble,
                 CADCORE_MT_inspect, CADCORE_MT_output):
        layout.menu(menu.__name__, text=menu.bl_label)


def face_action(layout, action, text, icon):
    layout.operator("cadcore.begin_face_action", text=text, icon=icon).action = action


class CADCORE_MT_create(bpy.types.Menu):
    bl_label = "Create"

    def draw(self, context):
        layout = self.layout
        start = layout.column()
        start.enabled = not context.scene.cadcore.has_body
        start.operator_menu_enum("cadcore.start_drawing", "plane", text="Create Sketch", icon='GREASEPENCIL')
        layout.operator("cadcore.draw_sketch", text="Draw on a Face or Plane", icon='GREASEPENCIL')
        layout.operator("cadcore.add_profile", text="Rectangle, Circle or Slot", icon='MESH_PLANE')
        layout.operator("cadcore.add_plane", text="Work Plane", icon='ORIENTATION_VIEW')
        layout.operator("cadcore.offset_plane", text="Drag a Plane off a Face", icon='ORIENTATION_VIEW')
        start.operator_menu_enum("cadcore.start", "shape", text="Basic Shape", icon='ADD')


class CADCORE_MT_modify(bpy.types.Menu):
    bl_label = "Modify"

    def draw(self, context):
        layout = self.layout
        face_action(layout, 'hole', "Drill a Hole", 'MESH_CIRCLE')
        face_action(layout, 'pocket', "Cut a Pocket", 'MOD_BOOLEAN')
        face_action(layout, 'push', "Push / Pull a Face", 'ORIENTATION_NORMAL')
        layout.operator("cadcore.fillet_selected", text="Round Edges (Fillet)", icon='MOD_BEVEL').kind = 'fillet'
        layout.operator("cadcore.fillet_selected", text="Bevel Edges (Chamfer)", icon='MOD_BEVEL').kind = 'chamfer'
        layout.separator()
        layout.operator("cadcore.thread_face", text="Thread a Cylindrical Face", icon='MOD_SCREW')
        layout.operator("cadcore.shell", text="Hollow Out (Shell)", icon='MOD_SOLIDIFY')
        layout.operator("cadcore.mirror", text="Mirror Across a Face", icon='MOD_MIRROR')
        layout.operator("cadcore.pattern", text="Repeat Around a Face", icon='MOD_ARRAY')
        layout.operator("cadcore.draft", text="Taper Faces (Draft)", icon='MOD_WARP')
        layout.operator("cadcore.draft_from_here", text="Draft From a Parting Face", icon='MOD_WARP')
        layout.operator("cadcore.split_here", text="Split at a Face", icon='MOD_BOOLEAN')
        layout.operator("cadcore.emboss_text", text="Emboss or Wrap Text", icon='FONT_DATA')
        layout.operator("cadcore.coil_round", text="Coil Round a Face", icon='MOD_SCREW')
        layout.operator("cadcore.drag_box", text="Draw a Cut or Raised Block", icon='MESH_CUBE')
        layout.operator("cadcore.place_hole", text="Place Multiple Holes", icon='MESH_CIRCLE')
        layout.operator("cadcore.flange_edge", text="Bend a Sheet Edge", icon='MOD_SIMPLEDEFORM')
        layout.separator()
        layout.operator("cadcore.delete_faces", text="Delete Faces", icon='X')
        layout.operator("cadcore.thicken", text="Thicken a Surface", icon='MOD_SOLIDIFY')
        layout.operator("cadcore.cap", text="Close a Surface", icon='MESH_PLANE')


class CADCORE_MT_assemble(bpy.types.Menu):
    bl_label = "Assemble"

    def draw(self, context):
        layout = self.layout
        layout.operator("cadcore.move_part", text="Move a Part", icon='ORIENTATION_GLOBAL')
        layout.operator("cadcore.mate_faces", text="Mate Two Picked Faces", icon='LINKED')
        layout.operator("cadcore.drive", text="Drive a Part (Turn or Slide)", icon='DRIVER_ROTATIONAL_DIFFERENCE')
        layout.operator("cadcore.explode", text="Take Apart (Exploded View)", icon='MOD_EXPLODE')
        layout.operator("cadcore.pick_boolean", text="Cut or Join Bodies", icon='MOD_BOOLEAN')
        layout.operator("cadcore.interference", text="Check for Collisions", icon='MOD_PHYSICS')


class CADCORE_MT_inspect(bpy.types.Menu):
    bl_label = "Inspect"

    def draw(self, context):
        layout = self.layout
        layout.operator("cadcore.measure", text="Measure", icon='DRIVER_DISTANCE')
        layout.operator("cadcore.section_view", text="Look Inside (Section)", icon='MOD_BOOLEAN')
        layout.operator("cadcore.section_clear", text="Show the Whole Part", icon='X')
        layout.operator("cadcore.check", text="Check the Design", icon='CHECKMARK')
        layout.operator("cadcore.printability", text="Check 3D Printing", icon='MOD_TRIANGULATE')
        layout.operator("cadcore.draft_check", text="Check Moulding (Draft)", icon='MOD_WARP')
        layout.operator("cadcore.mass", text="Mass", icon='RIGID_BODY')
        layout.operator("cadcore.simulate", text="Run Strength Studies", icon='PHYSICS')
        layout.operator("cadcore.broken_references", text="Check References", icon='LIBRARY_DATA_BROKEN')


class CADCORE_MT_output(bpy.types.Menu):
    bl_label = "Export"

    def draw(self, context):
        draw_output(self.layout, context)


def draw_output(layout, context):
    layout.operator("cadcore.export_step", text="CAD Solid (STEP)", icon='EXPORT')
    layout.operator("cadcore.export_mesh", text="3D Print (STL, 3MF)", icon='MESH_DATA')
    layout.operator("cadcore.drawing", text="Drawing (SVG, DXF)", icon='FILE_IMAGE')
    kinds = {f.kind for f in context.scene.cadcore.features}
    if kinds & {"sheet", "flange"}:
        layout.operator("cadcore.flat_pattern", text="Flat Pattern (DXF)", icon='MOD_UVPROJECT')
    if kinds & {"part", "mate", "assemble"}:
        layout.operator("cadcore.bill_of_materials", text="Bill of Materials", icon='PRESET')


class CADCORE_MT_more(bpy.types.Menu):
    """Maintenance and conversion tools."""

    bl_label = "Utilities"
    bl_idname = "CADCORE_MT_more"

    def draw(self, context):
        layout = self.layout
        layout.operator("cadcore.ask_editor", text="Long AI Conversation", icon='TEXT')
        layout.operator("cadcore.bake", text="Convert to Blender Mesh", icon='MESH_DATA')
        layout.separator()
        layout.operator("cadcore.rebuild", text="Rebuild", icon='FILE_REFRESH')
        layout.operator("cadcore.restart", text="Restart CAD Kernel", icon='FILE_REFRESH')


def _sketch_items(layout) -> bool:
    """The menu for what is picked on the sketch drawn on the part.

    Only the holds that fit these picks: two points can be put together or
    held a distance apart, and asking a point to be parallel is not a thing
    anybody should have to read past.
    """
    from ..viewport import holding, pick
    from .icons import constraint_icon

    picks = pick.sketch_picks()
    if not picks:
        return False
    layout.label(text=holding.what_is_picked(picks), icon='GREASEPENCIL')
    fits = holding.fitting(picks)
    for kind in fits:
        layout.operator("cadcore.hold_sketch", text=holding.HOLDS[kind][0],
                        icon=constraint_icon(kind)).kind = kind
    if not fits:
        layout.label(text=holding.what_it_needs(picks), icon='INFO')
    layout.operator("cadcore.ask_dialog", text="Ask About This...", icon='LIGHT')
    layout.separator()
    return True


def _plane_items(layout) -> bool:
    """The menu for a picked work plane; True if one is picked."""
    from ..viewport import pick

    plane = pick.picked_plane()
    if not plane:
        return False
    layout.label(text="This plane: %s" % plane, icon='ORIENTATION_VIEW')
    # the pen is a modal tool: a menu runs operators without invoking them
    # unless told, and an uninvoked pen is "invalid operator call" in the log
    layout.operator_context = 'INVOKE_REGION_WIN'
    layout.operator("cadcore.draw_sketch", text="Draw on It", icon='GREASEPENCIL')
    layout.operator("cadcore.add_profile", text="Profile on It", icon='MESH_PLANE')
    layout.operator("cadcore.section_view", text="Cut the View Here", icon='MOD_BOOLEAN')
    layout.operator("cadcore.ask_dialog", text="Ask About This...", icon='LIGHT')
    layout.separator()
    return True


def draw_body_context_menu(self, context):
    """The top of Blender's right-click menu, when the body is what is picked.

    Faces get face things, edges get edge things, and nothing else is shown:
    the selection is the context.
    """
    ob = context.active_object
    if not sync.is_body(ob) or getattr(context.scene, "cadcore", None) is None:
        return
    draw_selection_actions(self.layout, context)


def draw_selection_actions(layout, context, compact=False):
    """Offer the same selection actions in the sidebar and right-click menu."""
    if _sketch_items(layout) or _plane_items(layout):
        return
    faces, edges = selection.counts(context)
    if faces:
        if not compact:
            layout.label(text="This face" if faces == 1 else "These %d faces" % faces, icon='FACESEL')
        if faces == 2 and bpy.ops.cadcore.mate_faces.poll():
            layout.operator("cadcore.mate_faces", text="Mate: Put the First Against the Second",
                            icon='LINKED')
        if faces >= 1 and bpy.ops.cadcore.drive.poll():
            layout.operator("cadcore.drive", text="Drive This Part", icon='DRIVER_ROTATIONAL_DIFFERENCE')
        if faces == 2 and selection.parallel(context, selection.names(context)[:2]):
            layout.operator("cadcore.plane_between", text="Draw Between These Faces",
                            icon='MOD_MIRROR')
        if faces == 2:
            layout.operator("cadcore.measure", text="Distance Between", icon='DRIVER_DISTANCE').kind = 'distance'
        if faces == 1:
            shape = selection.face_shape(context, selection.names(context)[:1])
            # five of these want somewhere flat to work from. `face_frame`
            # refuses every curved face -- cone, torus and the free surfaces a
            # lofted part is made of, not only a cylinder -- so the test is
            # what the face *is*, not what it is not. None is "the kernel did
            # not say", and those stay.
            flat = shape in (None, "plane")
            if flat:
                if compact:
                    face_action(layout, 'push', "Push / Pull", 'ORIENTATION_NORMAL')
                else:
                    layout.operator("cadcore.press_pull", text="Push / Pull", icon='ORIENTATION_NORMAL')
                face_action(layout, 'hole', "Drill a Hole", 'MESH_CIRCLE')
                face_action(layout, 'pocket', "Cut a Pocket", 'MOD_BOOLEAN')
            if shape == "cylinder":
                layout.operator("cadcore.thread_face", text="Thread It", icon='MOD_SCREW')
                layout.operator("cadcore.pattern", text="Pattern Around It", icon='MOD_ARRAY')
                layout.operator("cadcore.emboss_text", text="Wrap Text Round It", icon='FONT_DATA')
                layout.operator("cadcore.coil_round", text="Coil Round It", icon='MOD_SCREW')
            elif shape == "plane" and not compact:
                layout.operator("cadcore.mirror", text="Mirror Across It", icon='MOD_MIRROR')
            if flat:
                layout.operator_context = 'INVOKE_REGION_WIN'
                layout.operator("cadcore.draw_sketch", text="Draw on It",
                                icon='GREASEPENCIL')
                layout.operator("cadcore.split_here", text="Split Here", icon='MOD_BOOLEAN')
                layout.operator("cadcore.emboss_text", text="Emboss Text...", icon='FONT_DATA')
                layout.operator("cadcore.draft_from_here", text="Draft From Here", icon='MOD_WARP')
                if not compact:
                    layout.operator("cadcore.add_plane", text="Offset Plane", icon='ORIENTATION_VIEW')
                    layout.operator("cadcore.section_view", text="Cut the View Here", icon='MOD_BOOLEAN')
        layout.operator("cadcore.fillet_selected", text="Round Its Edges", icon='MOD_BEVEL').kind = "fillet"
        if faces >= 2:
            layout.operator("cadcore.draft", text="Draft", icon='MOD_WARP')
    elif edges:
        if not compact:
            layout.label(text="This edge" if edges == 1 else "These %d edges" % edges, icon='EDGESEL')
        layout.operator("cadcore.fillet_selected", text="Fillet", icon='MOD_BEVEL').kind = "fillet"
        layout.operator("cadcore.fillet_selected", text="Chamfer", icon='MOD_BEVEL').kind = "chamfer"
        if edges == 1:
            layout.operator("cadcore.turn_plane", text="Turn a Plane About It",
                            icon='DRIVER_ROTATIONAL_DIFFERENCE')
        # a bend belongs to a blank: offered when the document has one
        if edges == 1 and {f.kind for f in context.scene.cadcore.features} & {"sheet", "flange"}:
            layout.operator("cadcore.flange_edge", text="Bend a Flange Up",
                            icon='MOD_SIMPLEDEFORM')
    else:
        corner = sync.picked_corner(sync.body()) if sync.body() else None
        if corner is not None:
            layout.label(text="This corner (%.1f, %.1f, %.1f)" % corner[0], icon='VERTEXSEL')
        else:
            layout.label(text="Pick a face, an edge or a corner first", icon='INFO')
    if not compact:
        layout.operator("cadcore.ask_dialog", text="Ask About This...", icon='LIGHT')
    layout.separator()


def draw_object_context_menu(self, context):
    """In object mode: the way in, without knowing about Tab or select modes."""
    ob = context.active_object
    if getattr(context.scene, "cadcore", None) is None:
        return
    layout = self.layout
    if _sketch_items(layout) or _plane_items(layout):
        return
    if not sync.is_body(ob):
        return
    layout.label(text="CAD part", icon='MESH_CUBE')
    layout.operator("cadcore.pick", text="Pick Faces", icon='FACESEL').what = 'FACE'
    layout.operator("cadcore.pick", text="Pick Edges", icon='EDGESEL').what = 'EDGE'
    layout.operator("cadcore.ask_dialog", text="Ask About This Part...", icon='LIGHT')
    layout.operator("cadcore.bridge", text="Let an Assistant Drive This", icon='LINKED')
    layout.separator()


def draw_ask_header(self, context):
    """`Send` in the Text Editor's header, when the text shown is the Ask text."""
    space = context.space_data
    text = getattr(space, "text", None)
    props = getattr(context.scene, "cadcore", None)
    if text is None or text.name != "Ask" or props is None:
        return
    row = self.layout.row(align=True)
    row.scale_x = 1.2
    if props.assistant_busy:
        row.operator("cadcore.ask_stop", text="Stop", icon='CANCEL')
    else:
        row.operator("cadcore.ask", text="Send to the assistant  (Ctrl+Enter)", icon='PLAY')


def draw_header_menu(self, context):
    """Add the CAD menu to the 3D View header once a document is open."""
    props = getattr(context.scene, "cadcore", None)
    if props is not None and (props.has_document or props.doc_path):
        self.layout.menu(CADCORE_MT_menu.bl_idname)
