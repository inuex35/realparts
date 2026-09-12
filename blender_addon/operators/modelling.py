"""Operators that add or change a feature from what is picked."""
from __future__ import annotations

import json

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty

from ..link import state, sync
from ..link.client import ServerError
from ..link.state import _apply_build, get_client, push_undo, report_error
from .edits import EditFromSelection, edit_dialog, extra_settings


AXES = {'X': [1, 0, 0], 'Y': [0, 1, 0], 'Z': [0, 0, 1]}


class CADCORE_OT_fillet_selected(bpy.types.Operator):
    bl_idname = "cadcore.fillet_selected"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Round Selection"
    bl_description = "Add a fillet or chamfer on the edges named by the current selection"

    # declared as in `EditFromSelection`, so the panel can say what this needs.
    # Edges, or the faces that meet at them.
    wants = "any"
    picks = "edges"

    radius: FloatProperty(name="Size", default=3.0, min=0.01, max=1000.0)
    kind: StringProperty(default="fillet")

    def execute(self, context):
        client = get_client(context)
        try:
            names = sync.selected_edge_names(client)
        except ServerError as exc:
            return report_error(self, exc)
        if not names:
            self.report({'ERROR'}, "empty_selection: select CAD faces or edges first")
            return {'CANCELLED'}
        try:
            info = client.call("add_fillet", edges=names, radius=self.radius, kind=self.kind)
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        context.scene.cadcore.status = "%s on %d edge(s): %s" % (
            info["feature"], len(names), ", ".join(names[:3]))
        push_undo(self.kind, self)
        self.report({'INFO'}, context.scene.cadcore.status)
        return {'FINISHED'}


def _profile_shapes(self, context):
    """Enum items for the profile shapes, asked of the kernel so the menu cannot go stale."""
    try:
        shapes = get_client(context).call("profile_shapes")["shapes"]
    except Exception:                                            # noqa: BLE001
        shapes = ["rect", "circle", "slot", "polygon"]
    words = {"rect": "Rectangle", "circle": "Circle", "slot": "Slot",
             "polygon": "Polygon"}
    return [(s, words.get(s, s.title()), "a %s, fully constrained" % s)
            for s in shapes]


class CADCORE_OT_add_profile(EditFromSelection, bpy.types.Operator):
    bl_idname = "cadcore.add_profile"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Profile on Face"
    bl_description = ("Draw a rectangle, circle, slot or polygon on the selected "
                      "face -- fully constrained, so it stays editable by its "
                      "dimensions")

    operation = "add_profile"
    wants = 1
    # no `settings`: the sidebar has no profile fields to seed these from

    profile_shape: bpy.props.EnumProperty(name="Shape", items=_profile_shapes)
    profile_x: FloatProperty(name="At X", default=0.0)
    profile_y: FloatProperty(name="At Y", default=0.0)
    profile_w: FloatProperty(name="Width", default=20.0, min=0.01)
    profile_h: FloatProperty(name="Height", default=10.0, min=0.01)
    profile_r: FloatProperty(name="Radius", default=5.0, min=0.01)
    profile_sides: IntProperty(name="Sides", default=6, min=3, max=64)
    profile_length: FloatProperty(name="Length", default=20.0, min=0.01)
    profile_rotation: FloatProperty(name="Rotation", default=0.0)

    def execute(self, context):
        from ..viewport import pick

        faces = sync.selected_face_names()
        plane = pick.picked_plane() if not faces else None
        if not plane:
            return EditFromSelection.execute(self, context)
        props = context.scene.cadcore
        args = self.arguments(props, [None])
        args.pop("face")
        args["plane"] = plane
        try:
            info = get_client(context).call("add_profile", **args)
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        props.status = "%s on %s" % (info.get("profile", self.profile_shape), plane)
        push_undo("profile", self)
        return {'FINISHED'}

    def arguments(self, props, picked):
        return {"shape": self.profile_shape, "face": picked[0],
                "at": [self.profile_x, self.profile_y],
                "width": self.profile_w, "height": self.profile_h,
                "radius": self.profile_r, "sides": self.profile_sides,
                "length": self.profile_length, "rotation": self.profile_rotation}

    def summarise(self, info, picked, props):
        return "%s on %s" % (info.get("profile", self.profile_shape), picked[0])


class CADCORE_OT_pocket_face(EditFromSelection, bpy.types.Operator):
    invoke = edit_dialog
    primary_settings = ("pocket_w", "pocket_h", "pocket_depth")
    advanced: BoolProperty(name="More options", default=False, options={'SKIP_SAVE'})

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        for name in self.primary_settings:
            layout.prop(self, name)
        extra_settings(self, layout)
        layout.label(text="Cut at the centre of the picked face")
    bl_idname = "cadcore.pocket_face"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Pocket on Face"
    bl_description = "Sketch a rectangle on the selected face and cut or add a prism"

    operation = "add_pocket"
    wants = 1
    settings = ("pocket_kind", "pocket_w", "pocket_h", "pocket_depth",
                "pocket_until", "pocket_symmetric", "pocket_count",
                "pocket_pitch", "pocket_axis")

    pocket_kind: StringProperty(name="Kind", default="pocket")
    pocket_w: FloatProperty(name="Width", default=20.0, min=0.01)
    pocket_h: FloatProperty(name="Height", default=12.0, min=0.01)
    pocket_depth: FloatProperty(name="Depth", default=4.0, min=0.01)
    pocket_until: StringProperty(name="Extent", default="depth")
    pocket_symmetric: BoolProperty(name="Symmetric", default=False)
    pocket_count: IntProperty(name="Count", default=1, min=1, max=200)
    pocket_pitch: FloatProperty(name="Pitch", default=20.0, min=0.01)
    pocket_axis: StringProperty(name="Axis", default="X")

    def arguments(self, props, picked):
        return {"face": picked[0], "kind": self.pocket_kind,
                "depth": self.pocket_depth, "width": self.pocket_w,
                "height": self.pocket_h, "count": self.pocket_count,
                "spacing": self.pocket_pitch, "direction": AXES[self.pocket_axis],
                "until": None if self.pocket_until == 'depth' else self.pocket_until,
                "symmetric": self.pocket_symmetric}

    def summarise(self, info, picked, props):
        return "%s on %s" % (info["feature"], info["on_face"])


class CADCORE_OT_remove_constraint(bpy.types.Operator):
    bl_idname = "cadcore.remove_constraint"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Remove Constraint"
    bl_description = ("Take this constraint off the sketch. What it was holding "
                      "becomes free to move")

    index: bpy.props.IntProperty()

    def execute(self, context):
        props = context.scene.cadcore
        try:
            info = get_client(context).call("remove_constraint", sketch=props.sketch_name,
                                            index=self.index)
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        state._refresh_constraints(context)
        props.status = "removed the %s constraint" % info["removed"].get("type", "")
        push_undo("take a hold off", self)          # a step, even when no click ran this
        return {'FINISHED'}


class CADCORE_OT_add_constraint(bpy.types.Operator):
    bl_idname = "cadcore.add_constraint"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Add Constraint"
    bl_description = "Add one hold to the sketch shown in the panel"

    kind: bpy.props.StringProperty()
    payload: bpy.props.StringProperty()          # the constraint, as JSON

    def execute(self, context):
        props = context.scene.cadcore
        try:
            constraint = json.loads(self.payload) if self.payload else {"type": self.kind}
            info = get_client(context).call("add_constraint", sketch=props.sketch_name,
                                            constraint=constraint)
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        state._refresh_constraints(context)
        kind = constraint["type"]
        props.status = "added a%s %s constraint" % (
            "n" if kind[0] in "aeiou" else "", kind)
        push_undo("hold", self)          # a step, even when no click ran this
        return {'FINISHED'}


class CADCORE_OT_hole_face(EditFromSelection, bpy.types.Operator):
    invoke = edit_dialog
    primary_settings = ("hole_d", "hole_depth")
    advanced: BoolProperty(name="More options", default=False, options={'SKIP_SAVE'})

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        if self.hole_standard == 'none':
            layout.prop(self, "hole_d")
        layout.prop(self, "hole_depth", text="Depth (0 = through)")
        extra_settings(self, layout)
        layout.label(text="Drill at the centre of the picked face")
    bl_idname = "cadcore.hole_face"
    bl_label = "Hole on Face"
    bl_description = "Drill a hole in the selected face, with an optional seat for the head"
    bl_options = {'REGISTER', 'UNDO'}

    operation = "add_hole"
    wants = 1
    settings = ("hole_standard", "hole_fit", "hole_d", "hole_depth", "hole_seat",
                "hole_seat_d", "hole_seat_depth", "pocket_count", "pocket_pitch",
                "pocket_axis")

    hole_standard: StringProperty(name="Standard", default="none")
    hole_fit: StringProperty(name="Fit", default="normal")
    hole_d: FloatProperty(name="Diameter", default=6.0, min=0.01)
    hole_depth: FloatProperty(name="Depth", default=0.0, min=0.0)
    hole_seat: StringProperty(name="Seat", default="none")
    hole_seat_d: FloatProperty(name="Seat Diameter", default=11.0, min=0.01)
    hole_seat_depth: FloatProperty(name="Seat Depth", default=4.0, min=0.01)
    pocket_count: IntProperty(name="Count", default=1, min=1, max=200)
    pocket_pitch: FloatProperty(name="Pitch", default=20.0, min=0.01)
    pocket_axis: StringProperty(name="Axis", default="X")

    def arguments(self, props, picked):
        args = {"face": picked[0], "depth": self.hole_depth or None}
        if self.hole_standard != 'none':
            args.update({"standard": self.hole_standard, "fit": self.hole_fit,
                         "seat": self.hole_seat})
        else:
            args["diameter"] = self.hole_d
            if self.hole_seat == 'counterbore':
                args["counterbore"] = {"diameter": self.hole_seat_d,
                                       "depth": self.hole_seat_depth}
            elif self.hole_seat == 'countersink':
                args["countersink"] = {"diameter": self.hole_seat_d, "angle": 90}
        if self.pocket_count > 1:                 # the repeat controls are shared
            args.update({"count": self.pocket_count, "spacing": self.pocket_pitch,
                         "direction": AXES[self.pocket_axis]})
        return args

    def summarise(self, info, picked, props):
        return "%s in %s%s" % (info["feature"], info["on_face"],
                               " -- " + info["note"] if info.get("note") else "")


class CADCORE_OT_shell(EditFromSelection, bpy.types.Operator):
    bl_idname = "cadcore.shell"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Shell"
    bl_description = "Hollow the body, opening it at the selected faces"

    operation = "add_shell"
    wants = "any"

    settings = ("shell_thickness",)

    shell_thickness: FloatProperty(name="Wall", default=2.0, min=0.01)

    def arguments(self, props, picked):
        return {"open": picked, "thickness": self.shell_thickness}

    def summarise(self, info, picked, props):
        return "%s, open at %s" % (info["feature"], ", ".join(picked) or "nothing")


class CADCORE_OT_mirror(EditFromSelection, bpy.types.Operator):
    bl_idname = "cadcore.mirror"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Mirror Across This Face"
    bl_description = ("Reflect the body about the selected flat face. Pick the face "
                      "the part is symmetric about, not the part -- for a symmetry "
                      "plane that is not a face, put a work plane between two faces "
                      "and pick that")

    operation = "add_mirror"
    wants = 1

    merge: BoolProperty(name="Join", default=True)

    def arguments(self, props, picked):
        return {"face": picked[0], "merge": self.merge}

    def summarise(self, info, picked, props):
        return "%s across %s" % (info["feature"], picked[0])


class CADCORE_OT_flip_pocket(bpy.types.Operator):
    """The same profile, the other way: a pocket becomes a boss and back."""

    bl_idname = "cadcore.flip_pocket"
    bl_label = "The Other Way"
    bl_description = ("Cut this profile into the face, or stand it off -- the same "
                      "profile and the same depth either way")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.cadcore
        if not (0 <= props.feature_index < len(props.features)):
            self.report({'ERROR'}, "pick the pocket or boss in the history first")
            return {'CANCELLED'}
        feature = props.features[props.feature_index].name
        try:
            info = get_client(context).call("flip_pocket", feature_id=feature)
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        props.status = "%s is a %s now" % (feature, info["kind"])
        push_undo("the other way", self)
        self.report({'INFO'}, props.status)
        return {'FINISHED'}


class CADCORE_OT_flange_edge(EditFromSelection, bpy.types.Operator):
    bl_idname = "cadcore.flange_edge"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Bend a Flange Up"
    bl_description = ("Bend a flap up from the picked edge of a sheet metal part. "
                      "The edge is named, so the flange stays on it when the blank "
                      "changes size")

    operation = "add_flange"
    wants = 1
    picks = "edges"

    length: FloatProperty(name="Length", default=12.0, min=0.1, max=1000.0)
    angle: FloatProperty(name="Angle", default=90.0, min=-180.0, max=180.0)

    def arguments(self, props, picked):
        return {"edge": picked[0], "length": self.length, "angle": self.angle}

    def summarise(self, info, picked, props):
        return "%s off %s, %.0f mm at %.0f deg" % (info["feature"], picked[0],
                                                   self.length, self.angle)


class CADCORE_OT_pattern(EditFromSelection, bpy.types.Operator):
    bl_idname = "cadcore.pattern"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Pattern Around This Face"
    bl_description = ("Repeat the body around the axis of the selected round face -- "
                      "a bolt circle. Pick the bore or the boss the copies turn about")

    operation = "add_pattern"
    wants = 1

    count: IntProperty(name="Count", default=6, min=2, max=200)
    step: FloatProperty(name="Step", default=0.0, min=0.0, max=360.0,
                        description="Degrees between copies; 0 spreads them over a full turn")
    merge: BoolProperty(name="Join", default=True)

    def arguments(self, props, picked):
        return {"count": self.count, "face": picked[0], "step": self.step or None,
                "merge": self.merge}

    def summarise(self, info, picked, props):
        return "%s: %d around %s" % (info["feature"], self.count, picked[0])


class CADCORE_OT_thread_face(EditFromSelection, bpy.types.Operator):
    bl_idname = "cadcore.thread_face"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Thread on Face"
    bl_description = ("Cut a real helical thread on the selected cylindrical face. "
                      "A hole threads outwards, a shaft inwards -- the face says which")

    operation = "add_thread"
    wants = 1

    settings = ("thread_standard", "thread_pitch", "thread_length",
                "thread_clearance")

    thread_standard: StringProperty(name="Thread", default="M6")
    thread_pitch: FloatProperty(name="Pitch", default=1.0, min=0.1, max=20.0)
    thread_length: FloatProperty(name="Length", default=0.0, min=0.0)
    thread_clearance: FloatProperty(name="Clearance", default=0.15,
                                    min=0.0, max=1.0)

    def arguments(self, props, picked):
        standard = None if self.thread_standard == 'none' else self.thread_standard
        return {"face": picked[0], "standard": standard,
                "pitch": None if standard else self.thread_pitch,
                "length": self.thread_length or None,
                "clearance": self.thread_clearance}

    def summarise(self, info, picked, props):
        note = (info.get("notes") or {}).get("thread", {})
        return "%s on %s%s" % (info["feature"], picked[0],
                               " (%s)" % note["designation"] if note else "")


class CADCORE_OT_emboss_text(EditFromSelection, bpy.types.Operator):
    bl_idname = "cadcore.emboss_text"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Emboss Text"
    bl_description = ("Words raised off the picked face or cut into it, from a font; on a "
                      "round face they are wrapped round it")

    operation = "add_emboss"
    wants = 1

    text: StringProperty(name="Words", default="TEXT")
    depth: FloatProperty(name="Depth", default=1.0, min=0.01, max=100.0)
    height: FloatProperty(name="Font size", default=0.0, min=0.0,
                          description="0 lets the face's size decide")
    cut: BoolProperty(name="Cut in", default=False)

    def invoke(self, context, event):
        self.seed(context)
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        for name in ("text", "depth", "height", "cut"):
            layout.prop(self, name)

    def arguments(self, props, picked):
        return {"face": picked[0], "text": self.text, "depth": self.depth,
                "height": self.height or None, "cut": self.cut}

    def summarise(self, info, picked, props):
        return "%s %s on %s" % ("cut" if self.cut else "raised", self.text, picked[0])


class CADCORE_OT_delete_faces(EditFromSelection, bpy.types.Operator):
    bl_idname = "cadcore.delete_faces"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Delete Faces"
    bl_description = ("Remove the selected faces. Healed, the neighbours grow back "
                      "together (a fillet or a boss comes off); unhealed, the body "
                      "opens for surface work")

    heal: BoolProperty(default=True)

    operation = "delete_faces"
    wants = "any"

    def arguments(self, props, picked):
        return {"faces": picked, "heal": self.heal}

    def summarise(self, info, picked, props):
        return "%s %s: %s" % ("healed over" if self.heal else "opened at",
                              info["feature"], ", ".join(picked))


class CADCORE_OT_move_face(EditFromSelection, bpy.types.Operator):
    invoke = edit_dialog

    def draw(self, context):
        self.layout.prop(self, "move_distance", text="Distance")
        self.layout.label(text="Positive = outwards; negative = inwards")
    bl_idname = "cadcore.move_face"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Push / Pull Face"
    bl_description = ("Move the selected planar face along its own normal. The "
                      "face keeps its name, and the walls it slides along keep theirs")

    operation = "move_face"
    wants = 1

    settings = ("move_distance",)

    move_distance: FloatProperty(name="Move", default=2.0, min=-500.0, max=500.0)

    def arguments(self, props, picked):
        return {"face": picked[0], "distance": self.move_distance}

    def summarise(self, info, picked, props):
        return "%s: %s moved %.2f mm" % (info["feature"], picked[0], self.move_distance)


class CADCORE_OT_thicken(EditFromSelection, bpy.types.Operator):
    bl_idname = "cadcore.thicken"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Thicken"
    bl_description = "Give the open surface a wall, which makes it a solid again"

    operation = "add_thicken"

    settings = ("surface_thickness",)

    surface_thickness: FloatProperty(name="Wall", default=1.6, min=0.01)

    def arguments(self, props, picked):
        return {"thickness": self.surface_thickness}

    def summarise(self, info, picked, props):
        return "%s: %.2f mm wall" % (info["feature"], self.surface_thickness)


class CADCORE_OT_cap(EditFromSelection, bpy.types.Operator):
    bl_idname = "cadcore.cap"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Close Surface"
    bl_description = ("Patch every opening and sew the result. G1 meets the "
                      "neighbouring faces tangentially; G0 just spans the hole")

    operation = "add_cap"

    settings = ("fill_continuity",)

    fill_continuity: StringProperty(name="Meets", default="G0")

    def arguments(self, props, picked):
        return {"continuity": self.fill_continuity}

    def summarise(self, info, picked, props):
        note = (info.get("notes") or {}).get("fill", {})
        return "%s closed%s" % (info["feature"],
                                " (gap %.4f mm)" % note["gap_mm"] if note else "")


class CADCORE_OT_draft(bpy.types.Operator):
    bl_idname = "cadcore.draft"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Draft"
    bl_description = "Taper the selected faces about the last selected one"

    wants = "2+"              # the faces to taper, and the neutral one last
    picks = "faces"

    angle: FloatProperty(name="Angle", default=3.0, min=-45.0, max=45.0)

    def execute(self, context):
        props = context.scene.cadcore
        if not self.properties.is_property_set("angle"):
            self.angle = props.draft_angle
        faces = sync.selected_face_names()
        if len(faces) < 2:
            self.report({'ERROR'},
                        "select the faces to taper, then the neutral face last")
            return {'CANCELLED'}
        # "last selected" is the active polygon; selection flags carry no order.
        # It lives on the object the last pick landed on, which is the active
        # one -- an assembly is drawn as several and only that one has it
        neutral = None
        chosen = context.view_layer.objects.active
        body = chosen if sync.is_body(chosen) else sync.body()
        active = body.data.polygons.active if body else -1
        if body and 0 <= active < len(body.data.polygons) \
                and body.data.polygons[active].select:
            neutral = sync.face_name(body, active)
        if neutral is None or neutral not in faces:
            neutral = faces[-1]
        tapered = [f for f in faces if f != neutral]
        try:
            info = get_client(context).call("add_draft", faces=tapered,
                                            neutral=neutral, angle=self.angle)
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        props.status = "%s on %s" % (info["feature"], ", ".join(tapered))
        push_undo("draft", self)
        return {'FINISHED'}


class CADCORE_OT_add_plane(bpy.types.Operator):
    bl_idname = "cadcore.add_plane"
    bl_label = "Work Plane"
    bl_description = ("A work plane: offset from the picked face, or halfway "
                      "between two")
    bl_options = {'REGISTER', 'UNDO'}

    wants = "any"
    picks = "faces"

    offset: FloatProperty(name="Offset", default=10.0, min=-1000.0, max=1000.0)

    def execute(self, context):
        props = context.scene.cadcore
        faces = sync.selected_face_names()
        if not faces:
            self.report({'ERROR'}, "empty_selection: pick one or two faces first")
            return {'CANCELLED'}
        # one face and an offset, or the plane between two faces
        args = ({"between": faces[:2]} if len(faces) >= 2
                else {"face": faces[0], "offset": self.offset})
        try:
            info = get_client(context).call("add_plane", **args)
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        props.status = "work plane %s" % info.get("feature", "")
        push_undo("work plane", self)
        self.report({'INFO'}, props.status)
        return {'FINISHED'}
