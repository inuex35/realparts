"""Assembling in the viewport: mates from two picks, the parts taken apart, a part driven."""
from __future__ import annotations

import bpy
import mathutils
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from ..link import sync
from ..link import names
from ..link.client import ServerError
from ..link.state import _apply_build, get_client, push_undo, report_error

#: the mate kinds, in the kernel's order (tests hold this list to MATES)
MATE_KINDS = (
    ('fastened', "Fastened", "Two flat faces put together: nothing left free"),
    ('planar', "Planar", "Two flat faces at a distance: slides and turns in the plane"),
    ('concentric', "Concentric", "Two round faces on one axis: turns and slides along it"),
    ('parallel', "Parallel", "The two directions point the same way"),
    ('perpendicular', "Perpendicular", "The two directions at right angles"),
    ('angle', "Angle", "The two directions at the given angle"),
    ('distance', "Distance", "This far apart"),
    ('tangent', "Tangent", "A round face touching a flat or round one"),
    ('hinge', "Hinge", "A pin in a hole, centred: only turns"),
    ('slider', "Slider", "A piston in a bore: only slides"),
    ('ball', "Ball", "Two spheres on one centre: only turns"),
    ('gear', "Gear", "Two round faces that turn each other, in the ratio of their radii"),
    ('screw', "Screw", "A turn is a step along the axis, by the pitch"),
    ('belt', "Belt", "Two round faces turned the same way by a belt, in the ratio of their radii"),
    ('slot', "Slot", "A pin held along a flat side of a slot: slides along it and turns"),
    ('cam', "Cam", "A round follower kept touching any face"),
)


def _parts_of(faces: list) -> tuple:
    """The part each picked face belongs to: its scope, or the feature that made it."""
    return tuple(sync.part_of(f) or names.feature_of(f) for f in faces)


def _part_objects(scope: str) -> list:
    """The body and wire objects drawn for a part."""
    return [ob for ob in bpy.data.objects
            if ob.name in (sync.PART + scope, sync.EDGE_PART + scope)]


def _reset_parts() -> None:
    for ob in sync.bodies():
        ob.matrix_world = mathutils.Matrix.Identity(4)
    for ob in bpy.data.objects:
        if ob.name.startswith(sync.EDGE_PART):
            ob.matrix_world = mathutils.Matrix.Identity(4)


def _pose_matrix(pose) -> mathutils.Matrix:
    rotation, offset = pose
    m = mathutils.Matrix.Identity(4)
    for r in range(3):
        for c in range(3):
            m[r][c] = rotation[r][c]
        m[r][3] = offset[r]
    return m


class CADCORE_OT_mate_faces(bpy.types.Operator):
    """Two picked faces of two parts: hold one against the other.

    Flat faces meet flush, round faces share an axis, or pick another kind.
    The part of the first picked face is the one that moves. Solved with the
    assembly's other mates, so several can hold one part.
    """

    bl_idname = "cadcore.mate_faces"
    bl_label = "Mate These Faces"
    bl_description = ("Hold the part of the first picked face against the part of the second: "
                      "flat faces flush, round faces on one axis, or another kind")
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name="Kind", items=MATE_KINDS, default='fastened')
    offset: FloatProperty(name="Offset", default=0.0, description="mm, along the axis or normal")
    angle: FloatProperty(name="Angle", default=0.0, description="degrees")
    flip: BoolProperty(name="Flip", default=True, description="turn the moving part round")
    pitch: FloatProperty(name="Pitch", default=1.0, min=0.01, description="mm per turn, for a screw")
    ordered: BoolProperty(name="One transform, in order", default=False,
                          description="a `mate` feature applied in order, instead of a mate "
                                      "solved together with the others")

    @classmethod
    def poll(cls, context):
        if sync.body() is None:
            return False
        faces = sync.selected_face_names()
        return len(faces) == 2 and len(set(_parts_of(faces))) == 2

    def invoke(self, context, event):
        first, second = sync.selected_face_names()
        try:
            shapes = {f["name"]: f.get("shape")
                      for f in get_client(context).call("describe_faces")["faces"]}
        except ServerError as exc:
            return report_error(self, exc)
        round_ = {"cylinder", "cone"}
        if shapes.get(first) in round_ and shapes.get(second) in round_:
            self.kind = 'concentric'
        elif shapes.get(first) == "plane" and shapes.get(second) == "plane":
            self.kind = 'fastened'
        else:
            self.kind = 'tangent'
        return self.execute(context)

    def execute(self, context):
        first, second = sync.selected_face_names()
        moving, fixed = _parts_of((first, second))
        client = get_client(context)
        try:
            if self.ordered:
                info = client.call("add_feature", type="mate", args={
                    "move": moving, "to": fixed, "faces": [first, second], "kind": self.kind,
                    "offset": self.offset, "flip": self.flip, "angle": self.angle})
            else:
                extra = {}
                if self.kind in ('planar', 'distance', 'hinge') or self.offset:
                    extra["offset"] = self.offset
                if self.kind == 'screw':
                    extra["pitch"] = self.pitch
                info = client.call("add_mate", faces=[first, second], kind=self.kind,
                                   flip=self.flip, angle=self.angle, **extra)
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        props = context.scene.cadcore
        free = (info.get("freedom") or {}).get("freedom", {}).get(moving)
        left = "" if not free else (", held" if not free["dof"] else ", %d free" % free["dof"])
        props.status = "%s: %s onto %s%s" % (self.kind, moving, fixed, left)
        push_undo("mate", self)
        self.report({'INFO'}, props.status)
        return {'FINISHED'}


class CADCORE_OT_explode(bpy.types.Operator):
    """Show the assembly apart: each part moved out along the axis its mates hold it by.

    Only the picture moves; the document does not change. Zero puts it back.
    """

    bl_idname = "cadcore.explode"
    bl_label = "Take Apart (Exploded View)"
    bl_description = "Move each part out along its mate to see the assembly apart; 0 puts it back"
    bl_options = {'REGISTER', 'UNDO'}

    factor: FloatProperty(name="How far", default=1.0, min=0.0, soft_max=4.0,
                          description="times each part's own size; 0 puts the parts back")

    @classmethod
    def poll(cls, context):
        return len(sync.bodies()) > 1

    def execute(self, context):
        if self.factor <= 0.0:
            _reset_parts()
            context.scene.cadcore.status = "parts back in place"
            return {'FINISHED'}
        try:
            out = get_client(context).call("explode", factor=self.factor)
        except ServerError as exc:
            return report_error(self, exc)
        _reset_parts()
        for fid, offset in out["offsets"].items():
            scope = out["scopes"].get(fid, fid)
            for ob in _part_objects(scope):
                ob.matrix_world = mathutils.Matrix.Translation(mathutils.Vector(offset))
        context.scene.cadcore.status = "%d parts apart, x%.1f" % (len(out["offsets"]), self.factor)
        return {'FINISHED'}


class CADCORE_OT_drive(bpy.types.Operator):
    """Turn or slide a part along what its mates leave free; the other parts follow.

    Pick a face of the part, then drag: left and right turns it (or slides
    it, with S). Enter writes the position into the document's assemble
    feature, so the mechanism is built there from then on; Esc puts it back.
    """

    bl_idname = "cadcore.drive"
    bl_label = "Drive a Part"
    bl_description = ("Drag to turn or slide the picked part along the freedom its mates leave; "
                      "the rest follow. Enter keeps the position, Esc drops it")
    bl_options = {'REGISTER', 'UNDO'}

    part: StringProperty(name="Part")
    turn: FloatProperty(name="Turn", default=0.0, description="degrees")
    slide: FloatProperty(name="Slide", default=0.0, description="mm")
    sliding: BoolProperty(name="Slide instead of turn", default=False)
    frames: IntProperty(default=1, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return len(sync.bodies()) > 1 and len(sync.selected_face_names()) >= 1

    def _show(self, context, amount: float) -> bool:
        """Ask the kernel where the parts are at this amount, and move the objects."""
        try:
            call = {"slide": amount} if self.sliding else {"turn": amount}
            out = get_client(context).call("drive", part=self.part, frames=1, **call)
        except ServerError as exc:
            report_error(self, exc)
            return False
        frame = out["frames"][-1]
        for fid, pose in frame.items():
            scope = out["scopes"].get(fid, fid)
            relative = _pose_matrix(pose) @ _pose_matrix(out["rest"][fid]).inverted()
            for ob in _part_objects(scope):
                ob.matrix_world = relative
        return True

    def execute(self, context):
        amount = self.slide if self.sliding else self.turn
        if not self.part:
            faces = sync.selected_face_names()
            if not faces:
                self.report({'ERROR'}, "pick a face of the part to drive")
                return {'CANCELLED'}
            self.part = _parts_of(faces[:1])[0]
        if not self._show(context, amount):
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        faces = sync.selected_face_names()
        if not faces:
            self.report({'ERROR'}, "pick a face of the part to drive")
            return {'CANCELLED'}
        self.part = _parts_of(faces[:1])[0]
        self.start_x = event.mouse_region_x
        self.amount = 0.0
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Drive %s: drag to turn | S to slide instead | "
                                     "Enter keeps the position | Esc" % self.part)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        from ..viewport import modal

        if event.type in modal.NAVIGATION:
            return {'PASS_THROUGH'}
        if event.type == 'S' and event.value == 'PRESS':
            self.sliding = not self.sliding
            self.start_x = event.mouse_region_x
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE':
            step = 1.0 if event.ctrl else 5.0
            pixels = event.mouse_region_x - self.start_x
            amount = round(pixels / (2.0 if self.sliding else 1.0) / step) * step
            if amount != self.amount:
                self.amount = amount
                if self._show(context, amount):
                    unit = "mm" if self.sliding else "deg"
                    modal.say(context, event, "Drive %s: %+g %s | S to %s | Enter keeps | Esc"
                              % (self.part, amount, unit, "turn" if self.sliding else "slide"),
                              "%+g %s" % (amount, unit))
            return {'RUNNING_MODAL'}
        if event.type in {'RET', 'NUMPAD_ENTER', 'LEFTMOUSE'} and event.value == 'PRESS':
            context.area.header_text_set(None)
            return self._keep(context)
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            context.area.header_text_set(None)
            _reset_parts()
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def _keep(self, context):
        """Write the position into the assemble feature's drive list."""
        _reset_parts()
        if not self.amount:
            return {'CANCELLED'}
        client = get_client(context)
        try:
            doc = client.call("document_json")
            assemble = next((f for f in doc.get("features", []) if f.get("type") == "assemble"), None)
            if assemble is None:
                self.report({'ERROR'}, "no assemble feature to keep the position in")
                return {'CANCELLED'}
            drives = [d for d in (assemble.get("drive") or []) if d.get("part") != self.part]
            drives.append({"part": self.part,
                           ("slide" if self.sliding else "turn"): self.amount})
            info = client.call("edit_feature", feature_id=assemble["id"], args={"drive": drives})
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        push_undo("drive", self)
        context.scene.cadcore.status = "%s driven %+g %s" % (
            self.part, self.amount, "mm" if self.sliding else "deg")
        self.report({'INFO'}, context.scene.cadcore.status)
        return {'FINISHED'}
