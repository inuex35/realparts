"""Operators on the document itself: new, open, save, start, undo, history, bake."""
from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty

from ..link import state, sync, throttle
from ..link.client import ServerError
from ..link.state import _apply_build, get_client, push_undo, report_error, stop_client


class CADCORE_OT_new_document(bpy.types.Operator):
    bl_idname = "cadcore.new_document"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "New Document"
    bl_description = "Start an empty document"

    unit: bpy.props.EnumProperty(
        name="Unit", default="m", options={'SKIP_SAVE'},
        items=[(u, u, "documents are written in %s and built in mm" % u)
               for u in ("mm", "cm", "m", "in", "ft")])
    name: StringProperty(name="Name", default="part", options={'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        props = context.scene.cadcore
        throttle.forget()
        from ..link import assistant
        assistant.forget()
        try:
            get_client(context).call("new_document", unit=self.unit, name=self.name)
        except ServerError as exc:
            return report_error(self, exc)
        props.doc_path = ""
        props.has_document = True
        props.was_picking = False            # a fresh document: frame it and hand the tool over again
        props.saved = False
        props.unsaved_work = ""
        # an empty document has no build result, so refresh the panel from the
        # document and clear what the last document left in the scene
        state.clear_scene_for_empty_document(context)
        props.status = "a new document in %s: start with a box or a sketch" % self.unit
        props.status_is_error = False
        return {'FINISHED'}


class CADCORE_OT_open(bpy.types.Operator):
    bl_idname = "cadcore.open"
    bl_label = "Open Document"
    bl_description = "Load a cadcore JSON document, or a STEP, IGES, BREP or mesh file, and build it"

    # SKIP_SAVE: Blender remembers operator properties between invocations,
    # and `invoke` skips the dialog when the path is already set.
    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_glob: StringProperty(default="*.json;*.step;*.stp;*.iges;*.igs;*.brep;*.stl;*.obj;*.3mf;*.gltf;*.glb",
                                options={'HIDDEN'})
    # SKIP_SAVE: recovery is a decision about one open, not a mode to stay in
    recover: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'},
                          description="Open the unsaved work beside the document "
                                      "instead of the document itself")

    def invoke(self, context, event):
        if self.filepath:
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        props = context.scene.cadcore
        path = bpy.path.abspath(self.filepath or props.doc_path)
        # a pending throttled edit belongs to the document being closed
        throttle.forget()
        from ..link import assistant
        assistant.forget()
        client = get_client(context)
        try:
            opened = client.call("open", path=path, recover=self.recover)
            info = client.call("build")
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        # from the reply: a STEP import opens under the name of the document
        # it will become
        props.doc_path = opened.get("path") or path
        props.has_document = True
        props.was_picking = False            # a fresh document: frame it and hand the tool over again
        # reported, not acted on: the user decides whether to recover
        props.unsaved_work = opened.get("unsaved_work") or ""
        props.saved = bool(opened.get("saved", True))
        if props.unsaved_work:
            props.status = "unsaved work from an earlier session is waiting"
        elif self.recover:
            props.status = "recovered the unsaved work -- save to keep it"
        state._refresh_families(context)       # the feature kinds the kernel can build
        return {'FINISHED'}


class CADCORE_OT_save(bpy.types.Operator):
    """Write the document back to its file."""
    bl_idname = "cadcore.save"
    bl_label = "Save Document"
    bl_description = "Write the document back to its file"

    # SKIP_SAVE: Blender remembers operator properties between invocations,
    # and `invoke` skips the dialog when the path is already set.
    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})
    ask: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'},
                      description="Choose where to write it")

    @classmethod
    def poll(cls, context):
        # nothing to save until a document is open; a new document has no
        # path and asks where
        props = context.scene.cadcore
        return bool(props.has_document or props.doc_path)

    def invoke(self, context, event):
        props = context.scene.cadcore
        if self.ask or not props.doc_path:
            self.filepath = self.filepath or props.doc_path or "part.json"
            context.window_manager.fileselect_add(self)
            return {'RUNNING_MODAL'}
        return self.execute(context)

    def execute(self, context):
        props = context.scene.cadcore
        path = bpy.path.abspath(self.filepath) if self.filepath else None
        try:
            out = get_client(context).call("save", **({"path": path} if path else {}))
        except ServerError as exc:
            return report_error(self, exc)
        props.doc_path = out["path"]
        props.has_document = True
        props.was_picking = False            # a fresh document: frame it and hand the tool over again
        props.saved = True
        props.unsaved_work = ""
        props.status = "saved %s" % os.path.basename(out["path"])
        return {'FINISHED'}


class CADCORE_OT_rebuild(bpy.types.Operator):
    bl_idname = "cadcore.rebuild"
    bl_label = "Rebuild"
    bl_description = "Re-evaluate the feature graph and refresh the mesh"

    @classmethod
    def poll(cls, context):
        # there is nothing to rebuild until a document is open
        props = context.scene.cadcore
        return bool(props.has_document or props.doc_path)

    def execute(self, context):
        try:
            _apply_build(context, get_client(context).call("build"))
        except ServerError as exc:
            return report_error(self, exc)
        return {'FINISHED'}


class CADCORE_OT_start_drawing(bpy.types.Operator):
    """A ground plane and the pen, in one press.

    The other four starters give a shape that was decided for you. This one
    gives somewhere to draw and gets out of the way.
    """

    bl_idname = "cadcore.start_drawing"
    bl_label = "Draw"
    bl_description = "Put a work plane on the ground and start drawing a profile on it"
    bl_options = {'REGISTER', 'UNDO'}

    plane: bpy.props.EnumProperty(name="Plane", items=[
        ('xy', "XY (top)", ""), ('xz', "XZ (front)", ""), ('yz', "YZ (side)", "")],
        default='xy')

    def execute(self, context):
        from ..viewport import pick

        props = context.scene.cadcore
        if props.has_body:
            self.report({'ERROR'}, "this document has a solid already; press on a face to draw on it")
            return {'CANCELLED'}
        try:
            info = get_client(context).call("add_plane", on=self.plane)
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        # the plane is what `draw_sketch` asks for, and picking it here is what
        # keeps this one press rather than two
        pick.remember_plane(info["feature"])
        props.status = "drawing on %s -- click to place points, Enter to finish" % info["feature"]
        push_undo("work plane", self)
        return {'FINISHED'}

    def invoke(self, context, event):
        if self.execute(context) == {'CANCELLED'}:
            return {'CANCELLED'}
        bpy.ops.cadcore.draw_sketch('INVOKE_DEFAULT')
        return {'FINISHED'}


class CADCORE_OT_start(bpy.types.Operator):
    """Make the first solid of a new document.

    A profile is a ground plane, sketch and extrude sent as one `apply`, so
    they are one undo step and a refusal leaves nothing behind.
    """

    bl_idname = "cadcore.start"
    bl_label = "Start With"
    bl_description = "Make the first solid of this document: a box, a cylinder, " \
                     "or a profile extruded off a ground plane"
    bl_options = {'REGISTER', 'UNDO'}

    shape: bpy.props.EnumProperty(name="Shape", items=[
        ('box', "Box", "a rectangular block, centred on the origin"),
        ('cylinder', "Cylinder", "a cylinder about z, standing on the ground plane"),
        ('rect', "Rectangle, extruded", "a rectangle sketched on a ground plane and extruded"),
        ('circle', "Circle, extruded", "a circle sketched on a ground plane and extruded"),
    ], default='box')
    plane: bpy.props.EnumProperty(name="Plane", items=[
        ('xy', "XY (top)", ""), ('xz', "XZ (front)", ""), ('yz', "YZ (side)", "")],
        default='xy')
    tilt: FloatProperty(name="Tilt", default=0.0, min=-90.0, max=90.0,
                        description="degrees to tip the plane about its own x axis")
    turn: FloatProperty(name="Turn", default=0.0, min=-180.0, max=180.0,
                        description="degrees to turn the plane about the vertical")
    size_x: FloatProperty(name="X", default=40.0, min=0.01)
    size_y: FloatProperty(name="Y", default=20.0, min=0.01)
    size_z: FloatProperty(name="Z / Height", default=10.0, min=0.01)
    radius: FloatProperty(name="Radius", default=10.0, min=0.01)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "shape")
        if self.shape in ('rect', 'circle'):
            layout.prop(self, "plane")
            row = layout.row(align=True)
            row.prop(self, "tilt")
            row.prop(self, "turn")
        if self.shape in ('box', 'rect'):
            row = layout.row(align=True)
            row.prop(self, "size_x")
            row.prop(self, "size_y")
        else:
            layout.prop(self, "radius")
        layout.prop(self, "size_z")

    def steps(self, props) -> list:
        """The operations, as `apply` takes them."""
        x, y, z, r = self.size_x, self.size_y, self.size_z, self.radius
        if self.shape == 'box':
            return [{"op": "add_feature", "type": "box",
                     "args": {"size": [x, y, z], "at": [0, 0, z / 2]}}]
        if self.shape == 'cylinder':
            return [{"op": "add_feature", "type": "cylinder",
                     "args": {"radius": r, "height": z, "at": [0, 0, z / 2]}}]
        taken = {f.name for f in props.features}

        def fresh(prefix):
            n = 1
            while f"{prefix}{n}" in taken:
                n += 1
            return f"{prefix}{n}"

        # a work plane already in the document is drawn on rather than doubled:
        # the selected one if a plane is selected, else the first there is
        planes = [f.name for f in props.features if f.kind == "plane"]
        picked = props.features[props.feature_index].name \
            if 0 <= props.feature_index < len(props.features) else None
        plane = picked if picked in planes else (planes[0] if planes else None)
        steps = []
        if plane is None:
            plane = fresh("plane")
            steps.append({"op": "add_plane", "on": self.plane, "tilt": self.tilt, "turn": self.turn,
                          "feature_id": plane})
        sketch = fresh("rect" if self.shape == 'rect' else "circle")
        steps.append({"op": "add_profile", "shape": "rect", "plane": plane, "width": x,
                      "height": y, "feature_id": sketch} if self.shape == 'rect' else
                     {"op": "add_profile", "shape": "circle", "plane": plane, "radius": r,
                      "feature_id": sketch})
        steps.append({"op": "add_feature", "type": "extrude",
                      "args": {"sketch": sketch, "distance": z}})
        return steps

    def execute(self, context):
        props = context.scene.cadcore
        if props.has_body:
            self.report({'ERROR'}, "this document has a solid already; pick a face and use the tools")
            return {'CANCELLED'}
        try:
            info = get_client(context).call("apply", ops=self.steps(props))
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        props.status = "started with a %s" % dict(
            box="box", cylinder="cylinder", rect="rectangle", circle="circle")[self.shape]
        push_undo("start", self)
        self.report({'INFO'}, props.status)
        return {'FINISHED'}


class CADCORE_OT_start_example(bpy.types.Operator):
    """Open the chosen example as a new, unsaved document."""

    bl_idname = "cadcore.start_example"
    bl_label = "Start From This Example"
    bl_description = ("Open the example shown as a copy: change it, then Save asks where. "
                      "The example itself is not touched")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from ..ui import gallery

        props = context.scene.cadcore
        stem = props.example
        path = gallery.path_of(stem)
        if not os.path.exists(path):
            self.report({'ERROR'}, "no example called %r" % stem)
            return {'CANCELLED'}
        throttle.forget()
        client = get_client(context)
        try:
            client.call("open", path=path, as_copy=True)
            info = client.call("build")
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        props.doc_path = ""
        props.has_document = True
        props.was_picking = False            # a fresh document: frame it and hand the tool over again
        props.saved = False
        props.unsaved_work = ""
        props.status = "started from %s -- Save will ask where to put it" % stem
        props.status_is_error = False
        return {'FINISHED'}


class CADCORE_OT_bake(bpy.types.Operator):
    """Turn the CAD body into a plain Blender mesh.

    After this, Knife, Loop Cut and every other Blender tool work on it and
    nothing is undone. The CAD document stays open; its next rebuild makes a
    new cad_body next to the baked one. There is no way back from the mesh
    to the CAD.
    """

    bl_idname = "cadcore.bake"
    bl_label = "Bake to Blender Mesh"
    bl_description = ("Make the body a plain Blender mesh. Blender's tools then keep "
                      "their changes; the CAD no longer redraws it")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = getattr(context.scene, "cadcore", None)
        return props is not None and props.has_body and sync.body() is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        props = context.scene.cadcore
        # every part of an assembly comes loose, not just the first
        name = ", ".join(sync.bake(ob) for ob in sync.bodies())
        props.has_body = False
        props.faces = props.edges = 0
        props.status = "%s is a Blender mesh now; the CAD will draw a new cad_body on its next rebuild" % name
        props.status_is_error = False
        self.report({'INFO'}, props.status)
        return {'FINISHED'}


class CADCORE_OT_checkpoint(bpy.types.Operator):
    bl_idname = "cadcore.checkpoint"
    bl_options = {'REGISTER'}
    bl_label = "Keep This"
    bl_description = "Remember the document under a name, to come back to"

    name: StringProperty(name="Name", default="idea", options={'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        props = context.scene.cadcore
        try:
            out = get_client(context).call("checkpoint", name=self.name)
        except ServerError as exc:
            return report_error(self, exc)
        props.status = "kept as %r (%d kept)" % (out["checkpoint"],
                                                 len(out["checkpoints"]))
        props.status_is_error = False
        return {'FINISHED'}


class CADCORE_OT_restore(bpy.types.Operator):
    bl_idname = "cadcore.restore"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Go Back To"
    bl_description = "Return to a kept document, as one undoable step"

    name: StringProperty(name="Name", default="", options={'SKIP_SAVE'})

    def invoke(self, context, event):
        try:
            self.kept = get_client(context).call("checkpoints")["checkpoints"]
        except ServerError:
            self.kept = []
        if not self.name and self.kept:
            self.name = self.kept[-1]
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        props = context.scene.cadcore
        try:
            info = get_client(context).call("restore", name=self.name)
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        props.status = "back to %r" % info.get("restored", self.name)
        props.status_is_error = False
        push_undo("restore", self)
        return {'FINISHED'}


class CADCORE_OT_remove_feature(bpy.types.Operator):
    bl_idname = "cadcore.remove_feature"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Remove Feature"
    bl_description = "Delete the selected feature and rebuild"

    def execute(self, context):
        props = context.scene.cadcore
        if not (0 <= props.feature_index < len(props.features)):
            self.report({'ERROR'}, "no feature selected")
            return {'CANCELLED'}
        fid = props.features[props.feature_index].name
        try:
            info = get_client(context).call("remove_feature", feature_id=fid)
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        props.status = "removed " + ", ".join(info["removed"])
        push_undo("remove feature", self)          # a step, even when no click ran this
        return {'FINISHED'}


class CADCORE_OT_suppress_feature(bpy.types.Operator):
    bl_idname = "cadcore.suppress_feature"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Suppress Feature"
    bl_description = ("Switch the selected feature off without deleting it. "
                      "A fillet a parameter change has broken can be switched "
                      "off, the rest of the model rebuilt, and the fillet "
                      "switched back on when the parameter moves again")

    def execute(self, context):
        props = context.scene.cadcore
        if not (0 <= props.feature_index < len(props.features)):
            self.report({'ERROR'}, "no feature selected")
            return {'CANCELLED'}
        item = props.features[props.feature_index]
        try:
            info = get_client(context).call("suppress_feature", feature_id=item.name,
                                            suppressed=not item.suppressed)
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        props.status = info.get("status", "")
        push_undo("suppress feature", self)          # a step, even when no click ran this
        return {'FINISHED'}


class CADCORE_OT_undo(bpy.types.Operator):
    bl_idname = "cadcore.undo"
    bl_label = "Undo CAD Edit"
    bl_description = "Undo the last change to the CAD document"

    redo: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        # Ctrl+Z is Blender's own undo, which the kernel follows; this button
        # only needs a document to act on, whatever object is active
        props = getattr(context.scene, "cadcore", None)
        return props is not None and bool(props.has_document or props.doc_path)

    def execute(self, context):
        try:
            info = (get_client(context).call("redo") if self.redo
                    else get_client(context).call("undo"))
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        context.scene.cadcore.status = "%s (%d step%s left)" % (
            "redone" if self.redo else "undone", info.get("undo_depth", 0),
            "" if info.get("undo_depth") == 1 else "s")
        # a step of its own on Blender's stack, so a Ctrl+Z after this button
        # is a step back from here and not a jump to before it
        push_undo("redo" if self.redo else "undo", self)
        return {'FINISHED'}


class CADCORE_OT_move_feature(bpy.types.Operator):
    bl_idname = "cadcore.move_feature"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Move Feature"
    bl_description = "Move the selected feature earlier or later in the history"

    direction: IntProperty(default=-1)

    def execute(self, context):
        props = context.scene.cadcore
        if not (0 <= props.feature_index < len(props.features)):
            self.report({'ERROR'}, "no feature selected")
            return {'CANCELLED'}
        names = [f.name for f in props.features]
        i = props.feature_index
        target = i + self.direction
        if not 0 <= target < len(names):
            return {'CANCELLED'}
        # moving up follows the feature above the target slot; None means the
        # front of the history (names[-1] would wrap to the last feature)
        after = (names[target - 1] if target > 0 else None) \
            if self.direction < 0 else names[target]
        if after == names[i]:
            return {'CANCELLED'}
        try:
            info = get_client(context).call("move_feature", feature_id=names[i],
                                            after=after)
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        props.status = "%s now follows %s" % (info["moved"], info["after"])
        push_undo("move feature", self)          # a step, even when no click ran this
        return {'FINISHED'}


class CADCORE_OT_rollback(bpy.types.Operator):
    bl_idname = "cadcore.rollback"
    bl_label = "Roll Back Here"
    bl_description = "Show the model as it was after the selected feature"

    clear: BoolProperty(default=False)

    def execute(self, context):
        props = context.scene.cadcore
        fid = None
        if not self.clear:
            if not (0 <= props.feature_index < len(props.features)):
                self.report({'ERROR'}, "no feature selected")
                return {'CANCELLED'}
            fid = props.features[props.feature_index].name
        try:
            _apply_build(context, get_client(context).call("rollback", feature_id=fid))
        except ServerError as exc:
            return report_error(self, exc)
        props.status = "rolled back to %s" % fid if fid else "showing the whole history"
        return {'FINISHED'}


class CADCORE_OT_restart(bpy.types.Operator):
    bl_idname = "cadcore.restart"
    bl_label = "Restart Kernel"
    bl_description = "Stop the kernel process and reload the document"

    def execute(self, context):
        throttle.forget()        # pending work was for the kernel being stopped
        stop_client()
        props = context.scene.cadcore
        if props.doc_path:
            # recover: the sidecar holds the session's unsaved edits
            return bpy.ops.cadcore.open(filepath=props.doc_path, recover=True)
        props.status = "kernel stopped"
        return {'FINISHED'}


class CADCORE_OT_reattach(bpy.types.Operator):
    bl_idname = "cadcore.reattach"
    bl_label = "Reattach"
    bl_description = ("Point a broken reference at the face picked now, and "
                      "rebuild")
    bl_options = {'REGISTER', 'UNDO'}

    wants = 1
    picks = "faces"

    def execute(self, context):
        props = context.scene.cadcore
        if not props.broken_name:
            self.report({'ERROR'}, "nothing to reattach: run Check References first")
            return {'CANCELLED'}
        faces = sync.selected_face_names()
        if not faces:
            self.report({'ERROR'}, "empty_selection: pick the face it should mean")
            return {'CANCELLED'}
        try:
            info = get_client(context).call("reattach", old=props.broken_name,
                                            new=faces[0])
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        props.status = "%s now means %s" % (props.broken_name, faces[0])
        props.broken_name = ""
        push_undo("reattach", self)
        self.report({'INFO'}, props.status)
        return {'FINISHED'}
