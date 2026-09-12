"""Stable tool categories, selection actions, editable results and the assistant."""
from __future__ import annotations

import os

import bpy

from . import menus, selection
from .icons import (CONSTRAINT_FALLBACK, CONSTRAINT_ICONS,  # noqa: F401
                    constraint_icon, icon_for, icon_names)


def _round(value: float) -> str:
    """Format a volume with fewer decimals for large values."""
    return "%.0f" % value if abs(value) >= 100 else "%.2f" % value


class CADCORE_UL_features(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop):
        props = context.scene.cadcore
        row = layout.row(align=True)
        # Features after the rollback point are in the document but not built.
        if props.rolled_back_to:
            cut = props.features.find(props.rolled_back_to)
            row.active = cut < 0 or props.features.find(item.name) <= cut
        # A suppressed feature is greyed the same way.
        if item.suppressed:
            row.active = False
        row.label(text=item.name,
                  icon='CHECKBOX_DEHLT' if item.suppressed
                  else icon_for(item.kind, context))
        sub = row.row()
        sub.alignment = 'RIGHT'
        sub.label(text=("off -- " + (item.summary or item.kind)) if item.suppressed
                  else (item.summary or item.kind))


#: Approximate sidebar character width in pixels. Blender neither wraps labels
#: nor exposes text measurement to Python, so `wrap` estimates; a slightly low
#: value truncates a word rather than leaving an empty line.
PER_CHAR = 7.2


def wrap(layout, text: str, context, icon: str = 'NONE') -> None:
    """Draw ``text`` as as many labels as fit the region width.

    Blender labels do not wrap, and a truncated refusal loses its hint.
    """
    preferences = getattr(context, "preferences", None)
    scale = getattr(getattr(preferences, "system", None), "ui_scale", 1.0)
    room = getattr(context.region, "width", 280) / max(scale, 0.5) - 32
    columns = max(16, int(room / PER_CHAR))
    line, first = "", True
    for word in str(text).split():
        if line and len(line) + 1 + len(word) > columns:
            layout.label(text=line, icon=icon if first else 'BLANK1')
            line, first = word, False
        else:
            line = (line + " " + word).strip()
    if line:
        layout.label(text=line, icon=icon if first else 'BLANK1')


def tool(layout, idname: str, picked: tuple, text: str = "", icon: str = 'NONE',
         primary: bool = False, **args):
    """Draw one tool button, greyed when the selection does not satisfy it.

    The reason is not put on the button (it truncates in the sidebar);
    :func:`needs` says it once per box. Returns the operator properties when
    the button is enabled so the caller can set them, else None.
    """
    row = layout.row(align=True)
    row.enabled = selection.blocked(idname, picked) is None
    if primary:
        row.scale_y = 1.25
    op = row.operator(idname, text=text or "", icon=icon)
    if not row.enabled:
        return None
    for key, value in args.items():
        setattr(op, key, value)
    return op


def needs(layout, picked: tuple, *idnames: str) -> None:
    """Draw the distinct selection requirements of these tools once, in draw order."""
    seen: list = []
    for idname in idnames:
        why = selection.blocked(idname, picked)
        if why and why not in seen:
            seen.append(why)
    if not seen:
        return
    row = layout.row()
    row.enabled = False
    row.label(text=" · ".join(seen), icon='INFO')


class _Panel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CAD"


class _DocumentPanel(_Panel):
    """A panel that is only there once a document is open."""

    @classmethod
    def poll(cls, context):
        # A new document has no path yet, so `has_document` is checked as well
        # as `doc_path`.
        props = context.scene.cadcore
        return bool(props.has_document or props.doc_path)


def _draw_setup(layout, context) -> bool:
    """The one-time set-up, in the sidebar: the kernel, then how Ask will run.
    Returns True while the kernel is missing, so nothing else is drawn."""
    import shutil

    from ..link.assistant import resolve_backend
    from ..link.state import kernel_status, prefs as preferences

    prefs = preferences(context)
    ready, said = kernel_status(prefs)
    if ready:
        return False
    box = layout.box()
    box.label(text="Set up once", icon='PREFERENCES')
    wrap(box.column(align=True), "1. The CAD kernel, for Blender's own Python. "
         "About 160 MB, downloaded once.", context, icon='BLANK1')
    run = box.row()
    run.scale_y = 1.4
    run.operator_context = 'EXEC_DEFAULT'          # no dialog: the full kernel
    run.operator("cadcore.install_kernel", text="Install the CAD Kernel", icon='IMPORT').which = 'studies'
    small = box.row()
    small.operator_context = 'EXEC_DEFAULT'
    small.operator("cadcore.install_kernel", text="Modelling only (85 MB, studies later)",
                   icon='BLANK1', emboss=False).which = 'kernel'
    status = box.row()
    status.enabled = False
    current = context.scene.cadcore.status
    wrap(status.column(align=True), current if current.startswith(("installing", "kernel install"))
         else said, context, icon='INFO')

    box.separator()
    wrap(box.column(align=True), "2. Ask: the assistant that works the part from a sentence.",
         context, icon='BLANK1')
    backend = resolve_backend(prefs)
    if backend != "api":
        command = getattr(prefs, backend + "_command", backend) or backend
        note = box.row()
        note.label(text="%s found: uses your plan" % ("Claude Code" if backend == "claude" else "Codex"),
                   icon='CHECKMARK' if shutil.which(command) else 'ERROR')
        box.operator("cadcore.assistant_login", text="Sign In (opens a terminal)",
                     icon='CONSOLE').kind = backend
    else:
        wrap(box.column(align=True), "No claude or codex command found. Install Claude Code or "
             "Codex and sign in, or paste an Anthropic API key:", context, icon='INFO')
        box.prop(prefs, "anthropic_api_key", text="")
    return True


class CADCORE_PT_panel(_Panel, bpy.types.Panel):
    bl_label = "RealParts"
    bl_idname = "CADCORE_PT_panel"

    def draw(self, context):
        props = context.scene.cadcore
        layout = self.layout
        if not props.has_document and _draw_setup(layout, context):
            return

        row = layout.row(align=True)
        row.operator("cadcore.new_document", text="New", icon='FILE_NEW')
        row.operator("cadcore.open", text="Open", icon='FILE_FOLDER')
        # The Save button is marked when the document has unsaved edits.
        save = row.row(align=True)
        save.alert = not props.saved
        save.operator("cadcore.save", text="Save*" if not props.saved else "Save",
                      icon='FILE_TICK').ask = False
        save.operator("cadcore.save", text="", icon='COPY_ID').ask = True
        row = layout.row(align=True)
        row.operator("cadcore.undo", text="Undo", icon='LOOP_BACK').redo = False
        row.operator("cadcore.undo", text="Redo", icon='LOOP_FORWARDS').redo = True
        row.menu(menus.CADCORE_MT_more.bl_idname, text="Utilities")

        if props.has_document or props.doc_path:
            row = layout.row(align=True)
            row.menu("CADCORE_MT_create", text="Create")
            row.menu("CADCORE_MT_modify", text="Modify")
            row = layout.row(align=True)
            row.menu("CADCORE_MT_assemble", text="Assemble")
            row.menu("CADCORE_MT_inspect", text="Inspect")
            layout.menu("CADCORE_MT_output", text="Export", icon='EXPORT')
        else:
            wrap(layout, "Make editable parts. Start a new design, open a file, "
                 "or learn with an example.", context)
            layout.operator("cadcore.ask_dialog", text="Create with the Assistant", icon='LIGHT')

        # nothing open, or nothing made yet: the examples, as pictures
        if not props.has_document or not len(props.features):
            pick = layout.box()
            pick.label(text="Learn with an example", icon='IMAGE_DATA')
            pick.template_icon_view(props, "example", show_labels=True, scale=5.0, scale_popup=6.0)
            row = pick.row()
            row.scale_y = 1.2
            row.operator("cadcore.start_example", text="Open a Copy of %s" % props.example.replace("_", " "),
                         icon='DUPLICATE')
            wrap(pick, "Try changing a dimension, adding a hole, then exporting STEP.", context)

        # Start on a plane before a solid exists.
        if props.has_document and not props.has_body:
            start = layout.box()
            start.label(text="Create Sketch", icon='GREASEPENCIL')
            wrap(start, "Choose a plane, draw a closed profile, then extrude it.", context)
            row = start.row(align=True)
            for plane, label in (("xy", "XY (top)"), ("xz", "XZ (front)"), ("yz", "YZ (side)")):
                row.operator("cadcore.start_drawing", text=label).plane = plane
        if props.unsaved_work:
            box = layout.box()
            box.alert = True
            wrap(box, "unsaved work from an earlier session", context,
                 icon='RECOVER_LAST')
            recover = box.operator("cadcore.open", text="Recover it", icon='RECOVER_LAST')
            recover.filepath = props.doc_path
            recover.recover = True
        if props.doc_path:
            layout.label(text=os.path.basename(props.doc_path), icon='FILE_BLANK')
        # Only while the view is cut: the cut is the reason this shows up.
        if props.section_on:
            cut = layout.box()
            row = cut.row(align=True)
            row.label(text="Cut on %s" % (props.section_face or "a face"), icon='MOD_BOOLEAN')
            row.operator("cadcore.section_clear", text="", icon='X')
            row = cut.row(align=True)
            row.prop(props, "section_offset", text="")
            row.prop(props, "section_flip", text="", icon='ARROW_LEFTRIGHT')

        # The status is usually a refusal, so it sits by the buttons, not at
        # the bottom.
        box = layout.box()
        box.alert = props.status_is_error
        wrap(box, props.status or "no document", context,
             icon='ERROR' if props.status_is_error else 'INFO')


class CADCORE_PT_parameters(_Panel, bpy.types.Panel):
    """The document's parameters, bounded ones first.

    A separate panel rather than a box because the list has no upper bound
    and a panel can be collapsed.
    """

    bl_label = "Parameters"
    bl_parent_id = "CADCORE_PT_panel"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 50

    @classmethod
    def poll(cls, context):
        return bool(context.scene.cadcore.parameters)

    def draw_header(self, context):
        props = context.scene.cadcore
        if not props.in_envelope:
            row = self.layout.row()
            row.alert = True
            row.label(text="· outside the envelope", icon='ERROR')

    def draw(self, context):
        props = context.scene.cadcore
        layout = self.layout

        # Parameters with a declared range are the ones meant to be driven;
        # the rest are shown on demand.
        driven = [p for p in props.parameters if p.bounded]
        rest = [p for p in props.parameters if not p.bounded]

        def rows(into, group):
            column = into.column(align=True)
            column.use_property_split = True
            column.use_property_decorate = False
            for p in group:
                # Shown as "Wall height"; expressions still use `wall_height`.
                column.prop(p, "value", text=p.name.replace("_", " ").capitalize())

        if driven:
            rows(layout, driven)
            if rest:
                # Left-aligned: an unembossed boolean otherwise centres its
                # label away from the arrow.
                more = layout.row()
                more.alignment = 'LEFT'
                more.prop(props, "show_every_parameter",
                          text="%d more" % len(rest),
                          icon='TRIA_DOWN' if props.show_every_parameter
                          else 'TRIA_RIGHT', emboss=False)
                if props.show_every_parameter:
                    rows(layout.box(), rest)
        else:
            # No declared ranges: show every parameter.
            rows(layout, rest)

        made_of = layout.column(align=True)
        made_of.use_property_split = True
        made_of.prop(props, "material")
        made_of.prop(props, "colour")
        # The parameters are also ID properties on the body for keyframes and
        # drivers; rebuilding from them is opt-in because a rebuild is costly.
        layout.prop(props, "drive_on_frame")
        if props.drive_on_frame:
            note = layout.row()
            note.enabled = False
            note.label(text="keyframe them on cad_body (N panel)",
                       icon='DECORATE_KEYFRAME')


class CADCORE_PT_assistant(_DocumentPanel, bpy.types.Panel):
    """Send the document and selection with a request; show its results."""

    bl_label = "Create / Edit with AI"
    bl_parent_id = "CADCORE_PT_panel"
    bl_order = 30

    def draw_header(self, context):
        from ..link import bridge

        if bridge.listening():
            row = self.layout.row()
            row.label(text="· %d in" % bridge.clients() if bridge.clients()
                      else "· open", icon='LINKED')

    def draw(self, context):
        from ..link import bridge, state

        layout = self.layout
        props = context.scene.cadcore
        # the Ask box: Claude works the part from here
        prefs = state.prefs(context)
        box = layout.box()
        picked = selection.counts(context)
        target = selection.summary(picked) if any(picked) else "the whole document"
        wrap(box, "Send with: " + target, context, icon='RESTRICT_SELECT_OFF')
        if any(picked):
            wrap(box, ", ".join(selection.names(context)) or target, context)
        from ..link.assistant import resolve_backend

        backend = resolve_backend(prefs)
        if backend == "api" and not getattr(prefs, "anthropic_api_key", ""):
            box.label(text="Set an Anthropic API key in the add-on preferences", icon='INFO')
        elif backend != "api":
            import shutil as _shutil
            command = getattr(prefs, backend + "_command", backend) or backend
            found = _shutil.which(command)
            note = box.row()
            note.alert = not found
            note.label(text="%s: %s" % ("Claude Code" if backend == "claude" else "Codex",
                                       "your plan" if found else "%s not found" % command),
                       icon='LINKED' if found else 'ERROR')
            if found:
                note.operator("cadcore.assistant_login", text="", icon='CONSOLE').kind = backend
        # Blender has no text box that wraps, so the field is tall and the
        # whole question is shown under it, wrapped, once it no longer fits
        field = box.row(align=True)
        field.scale_y = 1.8
        field.prop(props, "ask", text="", placeholder="a 6 mm plate, 80 x 60, four M6 holes")
        field.operator("cadcore.ask_dialog", text="", icon='TEXT')
        long = bpy.data.texts.get("Ask")
        if not props.ask.strip() and long is not None and long.as_string().strip():
            note = box.row()
            note.enabled = False
            note.label(text="Ask will send the text in the \"Ask\" window", icon='TEXT')
        if len(props.ask) > 36:
            whole = box.column(align=True)
            whole.enabled = False
            wrap(whole, props.ask, context, icon='BLANK1')
        row = box.row(align=True)
        row.scale_y = 1.2
        if props.assistant_busy:
            row.operator("cadcore.ask_stop", text="Stop", icon='CANCEL')
        else:
            row.operator("cadcore.ask", text="Ask", icon='PLAY')
        row.operator("cadcore.ask_clear", text="", icon='TRASH')
        box.prop(prefs, "assistant_stages", text="Step by step")
        if props.assistant_busy:
            box.label(text="working...", icon='TIME')
        for line in props.transcript:
            icon = {"said": 'OUTLINER_OB_SPEAKER', "did": 'CHECKMARK', "refused": 'ERROR',
                    "note": 'INFO', "error": 'CANCEL'}.get(line.kind, 'BLANK1')
            wrap(box.column(align=True), line.text, context, icon=icon)
        if props.transcript and not props.assistant_busy:
            wrap(box, "Review the model and Edit Feature above. Each edit can be undone.", context)
            box.operator("cadcore.undo", text="Undo Last Edit", icon='LOOP_BACK').redo = False

        # Ask opens the door itself; these are for an assistant run from a
        # terminal, which needs the config once
        layout.prop(props, "show_assistant_connection", text="External assistant connection")
        if not props.show_assistant_connection:
            return
        layout.separator()
        note = layout.column(align=True)
        note.enabled = False
        note.label(text="From a terminal instead: copy the config", icon='CONSOLE')
        row = layout.row(align=True)
        row.operator("cadcore.assistant_config", text="Claude Code", icon='COPYDOWN').client = 'claude'
        row.operator("cadcore.assistant_config", text="Codex", icon='COPYDOWN').client = 'codex'
        if bridge.listening():
            note = layout.row()
            note.enabled = False
            note.label(text="listening on %s:%d, loopback only" % (bridge.HOST, bridge.PORT), icon='LINKED')


class CADCORE_PT_features(_DocumentPanel, bpy.types.Panel):
    bl_label = "History"
    bl_parent_id = "CADCORE_PT_panel"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 60

    def draw(self, context):
        props = context.scene.cadcore
        layout = self.layout
        row = layout.row()
        row.template_list("CADCORE_UL_features", "", props, "features",
                          props, "feature_index", rows=5)
        col = row.column(align=True)
        col.operator("cadcore.move_feature", text="", icon='TRIA_UP').direction = -1
        col.operator("cadcore.move_feature", text="", icon='TRIA_DOWN').direction = 1
        col.separator()
        # Suppress sits beside remove as the non-destructive alternative.
        picked = (props.features[props.feature_index]
                  if 0 <= props.feature_index < len(props.features) else None)
        col.operator("cadcore.suppress_feature", text="",
                     icon='CHECKBOX_HLT' if picked and picked.suppressed
                     else 'CHECKBOX_DEHLT')
        col.operator("cadcore.remove_feature", text="", icon='TRASH')

        row = layout.row(align=True)
        row.operator("cadcore.rollback", text="Roll Back Here",
                     icon='REW').clear = False
        sub = row.row(align=True)
        sub.enabled = bool(props.rolled_back_to)
        sub.operator("cadcore.rollback", text="", icon='FF').clear = True
        if props.rolled_back_to:
            note = layout.row()
            note.alert = True
            note.label(text="showing up to %s" % props.rolled_back_to, icon='TIME')
        keep = layout.row(align=True)
        keep.operator("cadcore.checkpoint", text="Keep This", icon='PINNED')
        keep.operator("cadcore.restore", text="Go Back To", icon='LOOP_BACK')

        # a drawn profile is cut in; standing it off is the same profile the
        # other way, and this is the only place the two differ
        if picked is not None and picked.kind in ("pocket", "boss"):
            layout.operator("cadcore.flip_pocket",
                            text="Stand It Off" if picked.kind == "pocket" else "Cut It In",
                            icon='ARROW_LEFTRIGHT')

class CADCORE_PT_edit(_DocumentPanel, bpy.types.Panel):
    bl_label = "Edit Feature"
    bl_parent_id = "CADCORE_PT_panel"
    bl_order = 20

    @classmethod
    def poll(cls, context):
        return bool(context.scene.cadcore.feature_args)

    def draw(self, context):
        props = context.scene.cadcore
        layout = self.layout
        if props.feature_args:
            picked = (props.features[props.feature_index]
                      if 0 <= props.feature_index < len(props.features) else None)
            box = layout.box()
            row = box.row(align=True)
            row.label(text=picked.name if picked else "Selected feature",
                      icon=icon_for(picked.kind, context) if picked else 'DOT')
            row.operator("cadcore.show_dimensions", text="",
                         icon='DRIVER_DISTANCE', depress=props.show_dimensions)
            row.operator("cadcore.pick_dimension", text="", icon='EYEDROPPER')
            fields = box.column(align=True)
            fields.use_property_split = True
            fields.use_property_decorate = False
            # Drawn by the declared kind, not by the value's type.
            primary = [arg for arg in props.feature_args if arg.kind not in {'flag', 'text'}][:4]
            if not primary:
                primary = list(props.feature_args)[:4]
            visible = list(props.feature_args) if props.show_feature_details else primary
            for arg in visible:
                shown = arg.name.replace("_", " ").capitalize()
                if arg.kind == "flag":
                    fields.prop(arg, "flag", text=shown)
                elif arg.kind == "text":
                    fields.prop(arg, "text", text=shown)
                    if arg.choices:
                        note = fields.row()
                        note.enabled = False
                        wrap(note.column(align=True), arg.choices, context,
                             icon='BLANK1')
                else:
                    fields.prop(arg, "value", text=shown)
            if len(props.feature_args) > len(primary):
                box.prop(props, "show_feature_details", text="All %d settings" % len(props.feature_args))
            box.label(text="Edits apply immediately", icon='INFO')


class CADCORE_PT_sketch(_Panel, bpy.types.Panel):
    """The selected sketch's constraints."""

    bl_label = "Sketch"
    bl_parent_id = "CADCORE_PT_panel"
    bl_order = 25

    @classmethod
    def poll(cls, context):
        return bool(context.scene.cadcore.sketch_name)

    def draw_header(self, context):
        """Show the sketch's degrees of freedom in the header."""
        props = context.scene.cadcore
        if props.sketch_dof < 0:
            return
        row = self.layout.row()
        row.alert = props.sketch_dof > 0
        row.label(text="· held" if props.sketch_dof == 0
                       else "· %d free" % props.sketch_dof)

    def draw(self, context):
        props = context.scene.cadcore
        layout = self.layout
        layout.label(text=props.sketch_name, icon='GREASEPENCIL')

        box = layout.box()
        for constraint in props.constraints:
            row = box.row(align=True)
            row.label(text=constraint.name, icon=constraint_icon(constraint.name))
            if constraint.has_value:
                row.prop(constraint, "value_text", text="")
            row.operator("cadcore.remove_constraint", text="", icon='X',
                         emboss=False).index = constraint.index
            # The references go on their own line; they do not fit beside the
            # name.
            if constraint.detail and not constraint.has_value:
                note = box.row()
                note.enabled = False
                note.label(text=constraint.detail, icon='BLANK1')
        if not props.constraints:
            box.label(text="no constraints yet", icon='INFO')
        hint = layout.column(align=True)
        hint.enabled = False
        wrap(hint, "the sketch is drawn on the part: click a point to pick it, "
                   "drag it to move it, right-click for what can hold it",
             context, icon='GREASEPENCIL')


def fields(layout):
    """A property column with Blender's own split layout.

    A free function, not a panel method: `verify_addon.py` draws the panels
    against a stand-in `self` that has only a layout.
    """
    column = layout.column(align=True)
    column.use_property_split = True
    column.use_property_decorate = False
    return column


class CADCORE_PT_tools(_DocumentPanel, bpy.types.Panel):
    bl_label = "Selection Actions"
    bl_parent_id = "CADCORE_PT_panel"
    bl_order = 10

    def draw(self, context):
        layout = self.layout
        picked = selection.counts(context)

        # The selection readout; it decides which tools can run.
        box = layout.box()
        from ..viewport import marks
        plane = marks.picked_plane()
        if plane is not None or marks.sketch_picks():
            box.label(text=plane or marks.SKETCH[0], icon='OUTLINER_DATA_GP_LAYER')
            menus.draw_selection_actions(box, context, compact=True)
            return
        row = box.row(align=True)
        row.label(text=selection.summary(picked),
                  icon='RESTRICT_SELECT_OFF' if any(picked) else 'RESTRICT_SELECT_ON')
        row.operator("cadcore.show_selection", text="", icon='HIDE_OFF')
        if not any(picked):
            wrap(box, "Pick a face to drill or cut. Pick an edge to round or bevel.", context)
            menus.face_action(box, 'hole', "Drill a Hole...", 'MESH_CIRCLE')
            menus.face_action(box, 'pocket', "Cut a Pocket...", 'MOD_BOOLEAN')
        else:
            menus.draw_selection_actions(box, context, compact=True)


class CADCORE_PT_analysis(_DocumentPanel, bpy.types.Panel):
    bl_label = "Design Checks"
    bl_parent_id = "CADCORE_PT_panel"
    # Closed by default; studies and exports are opened on purpose.
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 70

    def draw(self, context):
        """Empty; the content is in the sub-panels."""


class CADCORE_PT_requirements(_Panel, bpy.types.Panel):
    """The document's requirements and whether the current build meets them.

    The rows come with every build reply, so they are never stale. Study
    results are under Studies; they are only as current as the last solve.
    """

    bl_label = "Requirements"
    bl_parent_id = "CADCORE_PT_analysis"

    def draw_header(self, context):
        props = context.scene.cadcore
        if not props.requirements:
            return
        failed = [r for r in props.requirements if not r.ok]
        row = self.layout.row()
        row.alert = bool(failed)
        row.label(text="· %d not met" % len(failed) if failed
                       else "· all %d met" % len(props.requirements))

    def draw(self, context):
        props = context.scene.cadcore
        layout = self.layout
        add = layout.row()
        add.scale_y = 1.25
        add.operator("cadcore.add_requirement", text="Require...", icon='ADD')
        if not props.requirements:
            note = layout.column(align=True)
            note.enabled = False
            wrap(note, "nothing required yet. Mass, size, printability, one "
                       "solid -- checked on every rebuild, next to the part",
                 context, icon='INFO')
            return
        for req in props.requirements:
            row = layout.row(align=True)
            row.alert = not req.ok
            row.label(text="%s: %s" % (req.name, req.text),
                      icon='CHECKMARK' if req.ok else 'CANCEL')
            row.operator("cadcore.remove_requirement", text="",
                         icon='X').req_id = req.name
            said = layout.row()
            said.enabled = False
            said.alert = not req.ok
            said.label(text="    now %s" % (req.error or req.got), icon='BLANK1')


class CADCORE_PT_studies(_Panel, bpy.types.Panel):
    bl_label = "Studies"
    bl_parent_id = "CADCORE_PT_analysis"

    @classmethod
    def poll(cls, context):
        # due once there is something to check against: a requirement, or a study
        props = context.scene.cadcore
        return bool(len(props.requirements) or len(props.studies))

    def draw_header(self, context):
        props = context.scene.cadcore
        if not props.studies:
            return
        failed = [s for s in props.studies if not s.ok]
        row = self.layout.row()
        row.alert = bool(failed)
        row.label(text="· %d failed" % len(failed) if failed
                       else "· %d passed" % len(props.studies))

    def draw(self, context):
        props = context.scene.cadcore
        layout = self.layout
        fields(layout).prop(props, "deflection")
        run = layout.row()
        run.scale_y = 1.25
        run.operator("cadcore.simulate", text="Run Studies", icon='PHYSICS')
        # The optimizer searches for the lightest design that still passes
        # the studies.
        layout.operator("cadcore.optimize", text="Search for a Lighter One",
                        icon='SORTSIZE')
        for study in props.studies:
            row = layout.row(align=True)
            row.alert = not study.ok
            row.label(text=study.name,
                      icon='CHECKMARK' if study.ok else 'CANCEL')
            # Headline on its own line; it does not fit beside the name.
            said = layout.row()
            said.enabled = study.ok
            said.alert = not study.ok
            wrap(said.column(align=True), study.headline, context, icon='BLANK1')
            if study.worst_face:
                where = layout.row(align=True)
                where.label(text="highest stress on %s" % study.worst_face, icon='BLANK1')
                where.operator("cadcore.select_faces", text="", icon='RESTRICT_SELECT_OFF').names = study.worst_face
            if not study.settled:
                warn = layout.row()
                warn.alert = True
                wrap(warn.column(align=True), "the answer was still moving with a finer mesh; "
                     "do not trust this number yet", context, icon='ERROR')
        if len(props.studies):
            note = layout.column(align=True)
            note.enabled = False
            wrap(note, "What this is: a first strength check. Linear material, a fixed face held "
                       "whole, one part at a time. Judge by the settled figure; the peak sits on a "
                       "clamp or a corner and grows with a finer mesh. Not a certification.",
                 context, icon='INFO')


class CADCORE_PT_printing(_Panel, bpy.types.Panel):
    bl_label = "3D Printing"
    bl_parent_id = "CADCORE_PT_analysis"

    def draw(self, context):
        props = context.scene.cadcore
        layout = self.layout
        layout.row(align=True).prop(props, "print_up", expand=True)
        column = fields(layout)
        column.prop(props, "print_overhang")
        column.prop(props, "print_wall")
        column.prop(props, "print_hole")
        layout.operator("cadcore.printability", text="Check Printability",
                        icon='MOD_TRIANGULATE')


class CADCORE_PT_export(_DocumentPanel, bpy.types.Panel):
    bl_label = "Export"
    bl_parent_id = "CADCORE_PT_panel"
    bl_order = 40
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        menus.draw_output(layout, context)


class CADCORE_PT_checks(_Panel, bpy.types.Panel):
    """Run the checks and show the report."""

    bl_label = "Checks"
    bl_parent_id = "CADCORE_PT_analysis"

    def draw(self, context):
        props = context.scene.cadcore
        layout = self.layout
        picked = selection.counts(context)
        run = layout.row()
        run.scale_y = 1.25
        run.operator("cadcore.check", text="Check Everything", icon='CHECKMARK')
        for line in props.report:
            row = layout.row(align=True)
            row.alert = not line.ok and not line.advice
            row.active = line.ok or not line.advice
            row.label(text=line.name,
                      icon='CHECKMARK' if line.ok
                      else ('INFO' if line.advice else 'CANCEL'))
            said = layout.row()
            said.enabled = False
            wrap(said.column(align=True), line.said, context, icon='BLANK1')
        layout.separator()
        layout.operator("cadcore.interference", text="Interference Only",
                        icon='MOD_PHYSICS')
        layout.operator("cadcore.broken_references", text="References Only",
                        icon='LIBRARY_DATA_BROKEN')
        # Reattach is offered only when a check found a broken reference.
        if props.broken_name:
            box = layout.box()
            wrap(box, "%s points at nothing" % props.broken_name, context,
                 icon='LIBRARY_DATA_BROKEN')
            tool(box, "cadcore.reattach", picked,
                 text="Reattach to the Picked Face", icon='FILE_REFRESH')
            needs(box, picked, "cadcore.reattach")
