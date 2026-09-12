"""The add-on's scene properties and their update callbacks.

Editing a value here edits the document, so each callback sends it to the
kernel and rebuilds. `state.is_refreshing` stops callbacks reacting while
the panel is being refreshed.
"""
from __future__ import annotations

import bpy

#: the add-on's own package -- one level up from this sub-package -- which is
#: what Blender keys its preferences by, whichever door it was installed through
ADDON = __package__.rpartition(".")[0]

ISSUES = "https://github.com/inuex35/realparts/issues"
#: the Discord button is not drawn until an invite is set here, so no version
#: ever ships a link that goes nowhere
DISCORD = ""
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, StringProperty)

from ..link import throttle
from ..link.client import ServerError
from ..link.state import (_apply_build, _refresh_constraints, _refresh_feature_args, get_client,
                          is_refreshing)


def _gallery_items(self, context):
    from . import gallery

    return gallery.items(self, context)


def _on_parameter_edit(self, context):
    if is_refreshing():
        return
    # Coalesced per parameter through `throttle`: a drag fires this on every tick.
    name, value = self.name, self.value

    def rebuild():
        try:
            info = get_client(context).call("set_parameter", name=name, value=value)
        except ServerError as exc:
            context.scene.cadcore.status = f"{exc.kind}: {exc.message}"
            return
        _apply_build(context, info)

    throttle.soon("parameter:" + name, rebuild)


class CADCORE_Parameter(bpy.types.PropertyGroup):
    name: StringProperty()
    value: FloatProperty(update=_on_parameter_edit)
    #: True when the document declares a range in `parameters_bounds`; those
    #: are the parameters meant to be driven.
    bounded: BoolProperty(default=False)
    low: FloatProperty(default=0.0)
    high: FloatProperty(default=0.0)


def _edit_argument(context, item, value):
    """Send one argument's new value to the kernel and apply the rebuild,
    coalesced per argument like a parameter edit."""
    props = context.scene.cadcore
    if not (0 <= props.feature_index < len(props.features)):
        return
    fid = props.features[props.feature_index].name
    name = item.name

    def rebuild():
        try:
            info = get_client(context).call("edit_feature", feature_id=fid,
                                            args={name: value})
        except ServerError as exc:
            props.status = f"{exc.kind}: {exc.message}"
            props.status_is_error = True
            return
        _apply_build(context, info)

    throttle.soon("argument:%s.%s" % (fid, name), rebuild)


def _on_feature_arg_edit(self, context):
    if is_refreshing():
        return
    props = context.scene.cadcore
    if not (0 <= props.feature_index < len(props.features)):
        return
    fid = props.features[props.feature_index].name
    try:
        if self.parameter:
            # The argument is an expression naming a parameter: edit the
            # parameter so the feature keeps the reference.
            info = get_client(context).call("set_parameter", name=self.parameter,
                                            value=self.value)
        else:
            info = get_client(context).call("edit_feature", feature_id=fid,
                                            args={self.name: self.value})
    except ServerError as exc:
        props.status = f"{exc.kind}: {exc.message}"
        props.status_is_error = True
        return
    props.status_is_error = False
    _apply_build(context, info)


def _on_section_changed(self, context):
    """The section is a view setting, not a document edit: nothing is sent."""
    from ..viewport import section

    section.refresh(context)


def _on_meta_edit(self, context):
    """The part's material or colour typed in: the document's own notes."""
    if is_refreshing():
        return
    from ..link.client import ServerError
    from ..link.state import get_client, set_error
    try:
        get_client(context).call("set_meta", material=self.material.strip(),
                                 colour=self.colour.strip())
    except ServerError as exc:
        set_error(context, exc)


def _on_feature_selected(self, context):
    if not is_refreshing():
        _refresh_feature_args(context)
        _refresh_constraints(context)


def _on_feature_flag_edit(self, context):
    if is_refreshing():
        return
    _edit_argument(context, self, self.flag)


def _on_feature_text_edit(self, context):
    if is_refreshing():
        return
    _edit_argument(context, self, self.text)


class CADCORE_FeatureArg(bpy.types.PropertyGroup):
    """One argument of the selected feature.

    `kind` comes from the feature type's declaration in the catalogue, not
    from the value's type.
    """

    name: StringProperty()
    parameter: StringProperty()          # set when the argument is an expression
    kind: StringProperty(default="number")
    choices: StringProperty()
    value: FloatProperty(update=_on_feature_arg_edit)
    flag: BoolProperty(update=_on_feature_flag_edit)
    text: StringProperty(update=_on_feature_text_edit)


def _on_constraint_value_edit(self, context):
    """Send a retyped dimension to the sketch and rebuild."""
    if is_refreshing():
        return
    props = context.scene.cadcore
    try:
        value = float(self.value_text)
    except ValueError:
        value = self.value_text                 # a parameter name, or an expression
    try:
        info = get_client(context).call("set_constraint_value", sketch=props.sketch_name,
                                        index=self.index, value=value)
    except ServerError as exc:
        props.status = f"{exc.kind}: {exc.message}"
        props.status_is_error = True
        return
    _apply_build(context, info)
    _refresh_constraints(context)


class CADCORE_ConstraintItem(bpy.types.PropertyGroup):
    name: StringProperty()                      # the constraint type
    detail: StringProperty()                    # what it refers to
    index: IntProperty()
    has_value: BoolProperty(default=False)
    value_text: StringProperty(name="", update=_on_constraint_value_edit)


class CADCORE_FamilyItem(bpy.types.PropertyGroup):
    """One feature type as the kernel describes it: its family and its summary."""

    name: StringProperty()                      # the type
    category: StringProperty()
    summary: StringProperty()
    args: StringProperty()                      # its argument declaration, as JSON


class CADCORE_FeatureItem(bpy.types.PropertyGroup):
    name: StringProperty()
    kind: StringProperty()
    summary: StringProperty()
    suppressed: BoolProperty(default=False)


class CADCORE_TranscriptLine(bpy.types.PropertyGroup):
    """One line of what the assistant said or did."""

    kind: StringProperty()          # said | did | refused | note | error
    text: StringProperty()


class CADCORE_ReportItem(bpy.types.PropertyGroup):
    """One line of the check report."""

    name: StringProperty()
    ok: BoolProperty(default=True)
    said: StringProperty()
    #: True for a manufacturing note (an overhang needs support) rather than
    #: a defect; drawn as info, not as an error.
    advice: BoolProperty(default=False)


class CADCORE_RequirementItem(bpy.types.PropertyGroup):
    """One requirement row, as the kernel last measured it."""
    name: StringProperty()
    text: StringProperty()          # "mass_g <= 150 g"
    got: StringProperty()           # "186.6"
    ok: BoolProperty(default=False)
    error: StringProperty()


class CADCORE_StudyItem(bpy.types.PropertyGroup):
    name: StringProperty()
    ok: BoolProperty(default=True)
    headline: StringProperty()
    worst_face: StringProperty()        # where the stress is highest, by CAD name
    settled: BoolProperty(default=True)  # the answer stopped moving under refinement


class CADCORE_Props(bpy.types.PropertyGroup):
    doc_path: StringProperty(name="Document", subtype='FILE_PATH')
    saved: BoolProperty(default=True)           # does the file hold what is on screen
    unsaved_work: StringProperty()              # a sidecar waiting to be recovered
    parameters: CollectionProperty(type=CADCORE_Parameter)
    parameter_index: IntProperty()
    features: CollectionProperty(type=CADCORE_FeatureItem)
    feature_index: IntProperty(update=_on_feature_selected)
    feature_args: CollectionProperty(type=CADCORE_FeatureArg)
    rolled_back_to: StringProperty()
    #: The kernel's undo depth, for the panel.
    undo_depth: IntProperty(default=0)
    #: Which state of the document is on screen: the kernel's revision, which
    #: never repeats. Stored on the scene so Blender's undo restores it and
    #: `state.on_undo` asks the kernel for that state -- the depth stopped
    #: telling states apart once the kernel's stack was full.
    revision: IntProperty(default=0)
    report: CollectionProperty(type=CADCORE_ReportItem)
    show_dimensions: BoolProperty(name="Dimensions on the model", default=False)
    #: the reference the last check found broken, and the subject of a repair
    broken_name: StringProperty(name="Reference")
    #: the section view: a cut in the 3D view only, so it changes no document
    section_on: BoolProperty(default=False, update=_on_section_changed)
    section_face: StringProperty(name="Cut on")
    section_origin: FloatVectorProperty(size=3, subtype='XYZ')     # mm, world
    section_normal: FloatVectorProperty(size=3, subtype='XYZ')     # world; the
    # side it points at is the side that goes away
    section_offset: FloatProperty(name="Cut at", default=0.0, min=-1000.0, max=1000.0,
                                  update=_on_section_changed,
                                  description="Slide the cut along the face's normal")
    section_flip: BoolProperty(name="Other side", default=False,
                               update=_on_section_changed,
                               description="Keep the side that is hidden and hide the "
                                           "side that is kept")
    show_assistant_connection: BoolProperty(default=False)
    show_feature_details: BoolProperty(default=False)
    #: the document's own notes: what the part is made of, and its colour for STEP
    material: StringProperty(name="Material", update=_on_meta_edit,
                             description="the bill of materials, the mass and STEP use it")
    colour: StringProperty(name="Colour", update=_on_meta_edit, description="#rrggbb, written to STEP")
    show_every_parameter: BoolProperty(
        name="Every parameter", default=False,
        description="Show the parameters the document did not declare a range "
                    "for, which are usually how the part is built rather than "
                    "how it is driven")
    families: CollectionProperty(type=CADCORE_FamilyItem)
    constraints: CollectionProperty(type=CADCORE_ConstraintItem)
    constraint_index: IntProperty()
    sketch_name: StringProperty()               # whose constraints are listed
    sketch_dof: IntProperty(default=-1)
    studies: CollectionProperty(type=CADCORE_StudyItem)
    study_index: IntProperty()
    requirements: CollectionProperty(type=CADCORE_RequirementItem)
    requirement_index: IntProperty()
    #: Whether the kernel holds a document; a new document has no `doc_path` yet.
    has_document: BoolProperty(default=False)
    ask: StringProperty(name="", description="What you want made or changed, in your own words")
    example: EnumProperty(name="Example", items=_gallery_items)
    transcript: CollectionProperty(type=CADCORE_TranscriptLine)
    assistant_busy: BoolProperty(default=False)
    has_body: BoolProperty(default=False)          # a solid is built; faces can be picked
    was_picking: BoolProperty(default=False)       # the pick tool was handed over once
    status_is_error: BoolProperty(default=False)
    status: StringProperty(default="no document")
    faces: IntProperty()
    edges: IntProperty()
    volume: FloatProperty()
    in_envelope: BoolProperty(default=True)
    deflection: FloatProperty(name="Deflection", default=0.2, min=0.005, max=5.0,
                              description="Tessellation tolerance in mm")
    # Off by default: a rebuild per frame is 20-80 ms for a small part and
    # seconds for a gear.
    drive_on_frame: BoolProperty(
        name="Rebuild on frame change", default=False,
        description="Let keyframes and drivers on the body's parameters rebuild "
                    "the model. Motion is free; shape is not -- expect 20-80 ms "
                    "a frame for a small part and seconds for a gear")
    pocket_kind: EnumProperty(name="Kind", items=[('pocket', "Pocket", "Cut into the face"),
                                                 ('boss', "Boss", "Stand off the face")],
                              default='pocket')
    pocket_w: FloatProperty(name="Width", default=20.0, min=0.01)
    pocket_h: FloatProperty(name="Height", default=12.0, min=0.01)
    pocket_depth: FloatProperty(name="Depth", default=4.0, min=0.01)
    pocket_until: EnumProperty(
        name="Depth", default='depth',
        items=[('depth', "Depth", "As deep as the number says"),
               ('through_all', "Through all", "All the way through the body"),
               ('next', "To next", "Until it meets the next face")])
    pocket_symmetric: BoolProperty(name="Symmetric", default=False,
                                   description="Straddle the sketch plane")
    pocket_count: IntProperty(name="Count", default=1, min=1, max=200,
                              description="Repeat the pocket along an axis")
    pocket_pitch: FloatProperty(name="Pitch", default=20.0, min=0.01)
    pocket_axis: EnumProperty(name="Axis", items=[('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", "")],
                              default='X')
    hole_d: FloatProperty(name="Ø", default=6.0, min=0.01)
    hole_depth: FloatProperty(name="Depth", default=0.0, min=0.0,
                              description="0 goes all the way through")
    hole_standard: EnumProperty(
        name="Standard", default='none',
        items=[('none', "Ø", "Give the diameter directly")] +
              [(k, k, "%s screw" % k) for k in ("M3", "M4", "M5", "M6", "M8", "M10", "M12")])
    hole_fit: EnumProperty(
        name="Fit", default='normal',
        items=[('tapped', "Tapped", "Tapping drill size"),
               ('close', "Close", "ISO 273 close clearance"),
               ('normal', "Normal", "ISO 273 medium clearance"),
               ('loose', "Loose", "ISO 273 free clearance")])
    hole_seat: EnumProperty(name="Seat", default='none',
                            items=[('none', "Plain", "No seat for the head"),
                                   ('counterbore', "C'bore", "Flat seat"),
                                   ('countersink', "C'sink", "Conical seat")])
    hole_seat_d: FloatProperty(name="Seat Ø", default=11.0, min=0.01)
    hole_seat_depth: FloatProperty(name="Seat depth", default=4.0, min=0.01)
    shell_thickness: FloatProperty(name="Wall", default=2.0, min=0.01)
    thread_standard: EnumProperty(
        name="Thread", default='M6',
        items=[('none', "Pitch", "Give the pitch directly")] +
              [(k, k, "%s thread" % k) for k in ("M3", "M4", "M5", "M6", "M8", "M10", "M12")])
    thread_pitch: FloatProperty(name="Pitch", default=1.0, min=0.1, max=20.0)
    thread_length: FloatProperty(name="Length", default=0.0, min=0.0,
                                 description="0 threads the whole face")
    thread_clearance: FloatProperty(name="Clearance", default=0.15, min=0.0, max=1.0,
                                    description="Shrink the cut all round, so a "
                                                "printed pair actually screws together")
    move_distance: FloatProperty(name="Move", default=2.0, min=-500.0, max=500.0,
                                 description="Along the face's own normal; "
                                             "negative pushes it in")
    surface_thickness: FloatProperty(name="Wall", default=1.6, min=0.01)
    fill_continuity: EnumProperty(
        name="Meets", default='G0',
        items=[('G0', "G0", "Just span the opening"),
               ('G1', "G1", "Tangent to the faces it joins"),
               ('G2', "G2", "Matching curvature as well")])
    print_up: EnumProperty(name="Up", default='Z',
                           items=[('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", "")],
                           description="Which way the part stands on the bed")
    print_overhang: FloatProperty(name="Overhang", default=45.0, min=1.0, max=89.0,
                                  description="Degrees from the bed a surface can lean")
    print_wall: FloatProperty(name="Min wall", default=1.2, min=0.05)
    print_hole: FloatProperty(name="Min hole", default=2.0, min=0.05)
    draft_angle: FloatProperty(name="Angle", default=3.0, min=-45.0, max=45.0)


class CADCORE_Prefs(bpy.types.AddonPreferences):
    bl_idname = ADDON

    repo_path: StringProperty(name="Repository", subtype='DIR_PATH', default="")
    python_path: StringProperty(name="Kernel Python", subtype='FILE_PATH', default="")
    assistant_backend: EnumProperty(name="Ask uses", default='auto', items=[
        ('auto', "Whatever is installed", "Claude Code if the `claude` command is found, else Codex, "
                                          "else the API key below"),
        ('api', "Anthropic API key", "pay per use, with your own key from console.anthropic.com"),
        ('claude', "Claude Code", "your Claude subscription, through the `claude` command you signed in to"),
        ('codex', "Codex", "your ChatGPT subscription, through the `codex` command you signed in to")])
    anthropic_api_key: StringProperty(name="Anthropic API key", subtype='PASSWORD', default="",
                                      description="Used only to talk to api.anthropic.com from the Ask box")
    assistant_model: StringProperty(name="Model", default="claude-sonnet-5")
    assistant_stages: BoolProperty(
        name="Step by step", default=True,
        description="Build in steps you can watch and stop. More tokens: each step returns a build. "
                    "Off: one call, fewest tokens, nothing to see until done")
    claude_command: StringProperty(name="claude command", default="claude",
                                   description="The Claude Code command, or a full path to it")
    codex_command: StringProperty(name="codex command", default="codex",
                                  description="The Codex command, or a full path to it")

    def draw(self, context):
        from ..link.state import is_bundled

        col = self.layout.column()
        if is_bundled() and not self.repo_path:
            col.label(text="kernel code: bundled with the add-on", icon='PACKAGE')
        col.prop(self, "repo_path")
        col.prop(self, "python_path")
        col.separator()
        col.label(text="Ask: an assistant works the CAD from the sidebar", icon='LIGHT')
        col.prop(self, "assistant_backend")
        if self.assistant_backend == 'auto':
            from ..link.assistant import resolve_backend

            found = resolve_backend(self)
            col.label(text={"claude": "found the claude command: Ask uses your Claude plan",
                            "codex": "found the codex command: Ask uses your ChatGPT plan",
                            "api": "no claude or codex command found: Ask uses the API key below"}[found],
                      icon='CHECKMARK' if found != 'api' else 'INFO')
            if found == 'api':
                col.prop(self, "anthropic_api_key")
                col.prop(self, "assistant_model")
        elif self.assistant_backend == 'api':
            col.prop(self, "anthropic_api_key")
            col.prop(self, "assistant_model")
        elif self.assistant_backend == 'claude':
            col.prop(self, "claude_command")
            col.label(text="Runs `claude -p` with this Blender as its MCP server; usage counts "
                           "against your Claude plan", icon='INFO')
        else:
            col.prop(self, "codex_command")
            col.label(text="Runs `codex exec` with this Blender as its MCP server; usage counts "
                           "against your ChatGPT plan", icon='INFO')
        col.label(text="Blank: Blender's own Python, with the kernel installed "
                       "by the button below (or a .venv beside the repository)",
                  icon='INFO')

        from ..link.state import kernel_status
        ready, said = kernel_status(self)
        col.label(text=said, icon='CHECKMARK' if ready else 'ERROR')
        if not ready:
            col.operator("cadcore.install_kernel", icon='IMPORT')

        col.separator()
        row = col.row(align=True)
        if DISCORD:
            row.operator("wm.url_open", text="Join the Discord",
                         icon='COMMUNITY').url = DISCORD
        row.operator("wm.url_open", text="Report a Bug", icon='URL').url = ISSUES


# ---------------------------------------------------------------------------
# shared plumbing


