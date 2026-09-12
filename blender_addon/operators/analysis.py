"""Operators that ask questions of the built part: studies, checks, drawings, export."""
from __future__ import annotations

import json
import os

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty

from ..link import sync
from ..link.client import ServerError
from ..link.state import _apply_build, get_client, push_undo, report_error


class CADCORE_OT_export_step(bpy.types.Operator):
    bl_idname = "cadcore.export_step"
    bl_label = "Export STEP"

    # SKIP_SAVE: Blender remembers operator properties between invocations,
    # and `invoke` skips the dialog when the path is already set.
    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_glob: StringProperty(default="*.step;*.stp", options={'HIDDEN'})

    def invoke(self, context, event):
        if self.filepath:
            return self.execute(context)
        self.filepath = os.path.splitext(context.scene.cadcore.doc_path or "part")[0] + ".step"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            out = get_client(context).call("export_step", path=bpy.path.abspath(self.filepath))
        except ServerError as exc:
            return report_error(self, exc)
        context.scene.cadcore.status = "exported %s (%d bytes)" % (out["path"], out["bytes"])
        return {'FINISHED'}


class CADCORE_OT_simulate(bpy.types.Operator):
    bl_idname = "cadcore.simulate"
    bl_label = "Run Studies"
    bl_description = "Solve the document's structural studies and colour the body by stress"

    show_field: BoolProperty(name="Show Stress", default=True)

    def execute(self, context):
        props = context.scene.cadcore
        try:
            out = get_client(context).call(
                "simulate", **({"deflection": props.deflection} if self.show_field else {}))
        except ServerError as exc:
            return report_error(self, exc)
        if not out["studies"]:
            context.scene.cadcore.status = "no studies in this document"
            self.report({'WARNING'}, context.scene.cadcore.status)
            return {'FINISHED'}
        parts = [_study_line(s) for s in out["studies"]]
        props.studies.clear()
        field = out.get("field")
        per_face = (field or {}).get("per_face") or {}
        worst = max(per_face, key=lambda n: per_face[n].get("max_MPa", 0.0)) if per_face else ""
        for study in out["studies"]:
            item = props.studies.add()
            item.name = study["id"]
            item.ok = bool(study["ok"])
            item.headline = _study_line(study).split(": ", 1)[-1]
            conv = study.get("convergence") or {}
            item.settled = bool(conv.get("converged", True))
            if field and field.get("study") == study["id"]:
                item.worst_face = worst
        if field and out.get("mesh"):
            sync.apply(out["mesh"])
            if sync.paint(field["vertex_values"], field["scale"], field["per_face"]):
                parts.append("%s coloured to %.3g" % (field["quantity"], field["scale"]))
        props.status = " | ".join(parts)
        self.report({'INFO'} if all(s["ok"] for s in out["studies"]) else {'WARNING'}, props.status)
        return {'FINISHED'}


def _study_line(study: dict) -> str:
    """A one-line summary of a study, in the units its kind answers in."""
    r = study["result"]
    ok = "OK" if study["ok"] else "FAIL"
    kind = study.get("type", "static_structural")
    if kind == "modal":
        return "%s: %.0f Hz first mode %s" % (study["id"], r["fundamental_Hz"], ok)
    if kind == "thermal":
        return "%s: %.0f C peak, %.0f MPa (p95) %s" % (
            study["id"], r["max_temperature_C"], r["p95_von_mises_MPa"], ok)
    if kind == "buckling":
        return "%s: buckles at x%.1f %s" % (study["id"], r["critical_factor"], ok)
    # two numbers, said plainly: the peak sits on a clamp or a corner and grows
    # with a finer mesh; the settled (p95) figure is the one to judge by
    return "%s: %.0f MPa peak, %.0f MPa settled, safety %.1f (%.1f settled), %.0f g %s" % (
        study["id"], r["max_von_mises_MPa"], r.get("p95_von_mises_MPa", r["max_von_mises_MPa"]),
        r["safety_factor"], r.get("safety_factor_p95", r["safety_factor"]), r["mass_g"], ok)


class CADCORE_OT_drawing(bpy.types.Operator):
    bl_idname = "cadcore.drawing"
    bl_label = "Drawing (SVG)"
    bl_description = "Project a dimensioned drawing sheet from the model"

    # SKIP_SAVE: Blender remembers operator properties between invocations,
    # and `invoke` skips the dialog when the path is already set.
    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_glob: StringProperty(default="*.svg", options={'HIDDEN'})

    def invoke(self, context, event):
        if self.filepath:
            return self.execute(context)
        self.filepath = os.path.splitext(context.scene.cadcore.doc_path or "part")[0] + ".svg"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            out = get_client(context).call("drawing", path=bpy.path.abspath(self.filepath))
        except ServerError as exc:
            return report_error(self, exc)
        context.scene.cadcore.status = "drawing: %s -> %s" % (
            ", ".join(out["views"]), os.path.basename(out["path"]))
        self.report({'INFO'}, context.scene.cadcore.status)
        return {'FINISHED'}


class CADCORE_OT_interference(bpy.types.Operator):
    bl_idname = "cadcore.interference"
    bl_label = "Check Interference"
    bl_description = "Report assembly parts that share space, and by how much"

    def execute(self, context):
        try:
            out = get_client(context).call("interference")
        except ServerError as exc:
            return report_error(self, exc)
        if out["clear"]:
            context.scene.cadcore.status = "%d parts, no interference" % len(out["parts"])
            self.report({'INFO'}, context.scene.cadcore.status)
            return {'FINISHED'}
        worst = max(out["interferences"], key=lambda c: c["volume_mm3"])
        context.scene.cadcore.status = "%d clash(es), worst %s/%s %.1f mm3" % (
            len(out["interferences"]), worst["parts"][0], worst["parts"][1],
            worst["volume_mm3"])
        self.report({'WARNING'}, context.scene.cadcore.status)
        return {'FINISHED'}


_KINDS = None


def measure_kinds(self, context):
    """Enum items for the measurement kinds, asked of the kernel once and cached.

    An enum items callback runs on every panel redraw, so the round trip is
    made only once.
    """
    global _KINDS
    if _KINDS is None:
        try:
            _KINDS = [(k, k.title(), "") for k in
                      get_client(context).call("measure_kinds")["kinds"]]
        except ServerError:
            _KINDS = [("linear", "Linear", "")]
    return _KINDS


class CADCORE_OT_measure(bpy.types.Operator):
    bl_idname = "cadcore.measure"
    bl_label = "Measure"
    bl_description = "Measure between the selected faces"
    bl_options = {'REGISTER'}

    wants = "any"
    picks = "faces"

    kind: bpy.props.EnumProperty(name="Kind", items=measure_kinds)

    def execute(self, context):
        props = context.scene.cadcore
        faces = sync.selected_face_names()
        if not faces:
            self.report({'ERROR'}, "empty_selection: pick one or two faces first")
            return {'CANCELLED'}
        try:
            out = get_client(context).call("measure", kind=self.kind, faces=faces[:2])
        except ServerError as exc:
            return report_error(self, exc)
        # each kind answers under its own key; report every numeric value
        said = ", ".join("%s %s" % (key.replace("_", " "),
                                    ("%.3f" % value).rstrip("0").rstrip("."))
                         for key, value in out.items()
                         if isinstance(value, (int, float)))
        props.status = "%s: %s" % (self.kind, said or json.dumps(out)[:120])
        props.status_is_error = False
        self.report({'INFO'}, props.status)
        return {'FINISHED'}


class CADCORE_OT_bill_of_materials(bpy.types.Operator):
    bl_idname = "cadcore.bill_of_materials"
    bl_label = "Bill of Materials"
    bl_description = "What this assembly is made of: each part, how many, how heavy"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.cadcore
        try:
            out = get_client(context).call("bill_of_materials")
        except ServerError as exc:
            return report_error(self, exc)
        parts = out.get("parts") or []
        if not parts:
            props.status = "no parts: a bill of materials needs an assembly"
            props.status_is_error = False
            self.report({'INFO'}, props.status)
            return {'FINISHED'}
        mass = out.get("total_mass_g")
        props.status = "%d part(s)%s -- %s" % (
            len(parts), ", %.1f g" % mass if mass else "",
            ", ".join("%s x%s" % (p.get("part", "?"), p.get("quantity", 1))
                      for p in parts[:4]))
        props.status_is_error = False
        self.report({'INFO'}, props.status)
        return {'FINISHED'}


class CADCORE_OT_flat_pattern(bpy.types.Operator):
    bl_idname = "cadcore.flat_pattern"
    bl_label = "Flat Pattern"
    bl_description = ("The blank this sheet metal part is cut from, written as a "
                      "DXF the cutter can read")
    bl_options = {'REGISTER'}

    # SKIP_SAVE: Blender remembers operator properties between invocations,
    # and `invoke` skips the dialog when the path is already set.
    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_glob: StringProperty(default="*.dxf", options={'HIDDEN'})

    def invoke(self, context, event):
        base = context.scene.cadcore.doc_path or "flat.dxf"
        self.filepath = os.path.splitext(bpy.path.abspath(base))[0] + "_flat.dxf"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        props = context.scene.cadcore
        client = get_client(context)
        try:
            flat = client.call("flat_pattern")
            client.call("flat_dxf", path=bpy.path.abspath(self.filepath))
        except ServerError as exc:
            return report_error(self, exc)
        props.status = "flat pattern written: %d bend(s) -> %s" % (
            len(flat.get("bends") or []), os.path.basename(self.filepath))
        props.status_is_error = False
        self.report({'INFO'}, props.status)
        return {'FINISHED'}


class CADCORE_OT_broken_references(bpy.types.Operator):
    bl_idname = "cadcore.broken_references"
    bl_label = "Check References"
    bl_description = "Every name in the document that no longer points at anything"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.cadcore
        try:
            out = get_client(context).call("broken_references")
        except ServerError as exc:
            return report_error(self, exc)
        broken = list(out.get("broken") or []) + list(out.get("dropped") or [])
        if not broken:
            props.broken_name = ""
            props.status = "every reference still resolves"
            props.status_is_error = False
            self.report({'INFO'}, props.status)
            return {'FINISHED'}
        props.broken_name = str(broken[0])
        props.status = "%d reference(s) point at nothing: %s" % (
            len(broken), ", ".join(str(b) for b in broken[:4]))
        props.status_is_error = True
        self.report({'WARNING'}, props.status)
        return {'FINISHED'}


class CADCORE_OT_check(bpy.types.Operator):
    bl_idname = "cadcore.check"
    bl_label = "Check Everything"
    bl_description = ("Build it, and ask whether it is still inside its own "
                      "envelope, whether every reference resolves, whether the "
                      "parts collide and whether a printer could make it")
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.cadcore
        try:
            out = get_client(context).call("check")
        except ServerError as exc:
            return report_error(self, exc)
        props.report.clear()
        for line in out["checks"]:
            item = props.report.add()
            item.name = line["check"]
            item.ok = line["ok"]
            item.said = line["said"]
            item.advice = line.get("severity") != "must"
        failed, warned = out["failed"], out.get("warned") or []
        props.status_is_error = not out["ok"]
        props.status = ("everything passes" if out["ok"] and not warned else
                        "%d fault(s)%s" % (len(failed),
                                           ", %d to watch" % len(warned) if warned else "")
                        if failed else "%d to watch" % len(warned))
        self.report({'WARNING'} if failed else {'INFO'}, props.status)
        return {'FINISHED'}


class CADCORE_OT_optimize(bpy.types.Operator):
    bl_idname = "cadcore.optimize"
    bl_label = "Search for a Lighter One"
    bl_description = ("Move the parameters the document declared a range for, "
                      "rebuild each time, and keep the lightest that still holds "
                      "its asserts -- and, by mass, still passes its studies")
    bl_options = {'REGISTER'}

    trials: IntProperty(name="Trials", default=25, min=2, max=500)
    objective: bpy.props.EnumProperty(
        name="Least", default='volume',
        items=[('volume', "Volume", "Take material out; needs only the kernel"),
               ('mass', "Mass", "Weigh it, and require the studies to pass")])
    seed: IntProperty(name="Seed", default=0, min=0, max=9999)

    @classmethod
    def poll(cls, context):
        props = context.scene.cadcore
        return bool(props.has_document or props.doc_path)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        props = context.scene.cadcore
        try:
            out = get_client(context).call("optimize", trials=self.trials,
                                           objective=self.objective,
                                           seed=self.seed, apply=True)
        except ServerError as exc:
            return report_error(self, exc)
        try:
            _apply_build(context, get_client(context).call("build"))
        except ServerError as exc:
            return report_error(self, exc)
        was, best = out.get("was") or {}, out.get("best") or {}
        key = "mass_g" if self.objective == 'mass' else "volume_mm3"
        props.status = "%d of %d trials stood up; %s %s -> %s" % (
            out["feasible"], out["trials"], key.split("_")[0],
            was.get(key, "?"), best.get(key, "?"))
        props.status_is_error = False
        push_undo("optimize", self)
        self.report({'INFO'}, props.status)
        return {'FINISHED'}


def _requirement_quantities(self, context):
    """Enum items for the requirement quantities, asked of the kernel so the menu matches what it accepts."""
    try:
        kinds = get_client(context).call("requirement_kinds")["quantities"]
    except Exception:                                            # noqa: BLE001
        kinds = {"mass_g": {"about": "mass"}, "bbox_max": {"about": "longest extent"},
                 "printable": {"about": "prints"}, "solid": {"about": "one solid"}}
    return [(name, name, str(info.get("about", "")))
            for name, info in sorted(kinds.items())]


class CADCORE_OT_add_requirement(bpy.types.Operator):
    bl_idname = "cadcore.add_requirement"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Require"
    bl_description = ("Say what the part must be -- under a mass, inside a box, "
                      "printable, one solid -- and see on every rebuild whether it is")

    quantity: bpy.props.EnumProperty(name="Quantity", items=_requirement_quantities)
    compare: bpy.props.EnumProperty(name="Is", default='<=', items=(
        ('<=', "<=", ""), ('<', "<", ""), ('>=', ">=", ""), ('>', ">", ""),
        ('==', "==", ""), ('!=', "!=", "")))
    value: StringProperty(name="Value", default="100",
                          description="a number, or an expression over the parameters")
    req_id: StringProperty(name="Name", default="", options={'SKIP_SAVE'})
    min_wall: FloatProperty(name="Min wall (printable)", default=1.5, min=0.05)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        props = context.scene.cadcore
        value = self.value.strip()
        try:
            value = float(value) if value.replace(".", "", 1).replace("-", "", 1).isdigit() else value
        except ValueError:
            pass
        args = {"quantity": self.quantity, "compare": self.compare, "value": value}
        if self.req_id.strip():
            args["name"] = self.req_id.strip()
        if self.quantity == "printable":
            args["min_wall"] = self.min_wall
        try:
            info = get_client(context).call("add_requirement", **args)
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        rows = info.get("requirements") or []
        met = sum(1 for r in rows if r.get("ok"))
        props.status = "%d of %d requirements met" % (met, len(rows))
        props.status_is_error = met < len(rows)
        push_undo("require", self)
        return {'FINISHED'}


class CADCORE_OT_remove_requirement(bpy.types.Operator):
    bl_idname = "cadcore.remove_requirement"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Drop Requirement"
    bl_description = "Take this requirement out of the document"

    req_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        props = context.scene.cadcore
        try:
            info = get_client(context).call("remove_requirement", name=self.req_id)
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        props.status = "dropped requirement %r" % self.req_id
        props.status_is_error = False
        push_undo("drop requirement", self)
        return {'FINISHED'}


class CADCORE_OT_export_mesh(bpy.types.Operator):
    bl_idname = "cadcore.export_mesh"
    bl_label = "Export Mesh"
    bl_description = "Write STL or 3MF for a slicer, from the same tessellation"

    # SKIP_SAVE: Blender remembers operator properties between invocations,
    # and `invoke` skips the dialog when the path is already set.
    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_glob: StringProperty(default="*.stl;*.3mf", options={'HIDDEN'})

    def invoke(self, context, event):
        if self.filepath:
            return self.execute(context)
        self.filepath = os.path.splitext(context.scene.cadcore.doc_path or "part")[0] + ".3mf"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        props = context.scene.cadcore
        try:
            out = get_client(context).call("export_mesh",
                                           path=bpy.path.abspath(self.filepath),
                                           deflection=min(props.deflection, 0.1))
        except ServerError as exc:
            return report_error(self, exc)
        props.status = "%d triangles -> %s" % (out["triangles"],
                                               os.path.basename(out["path"]))
        self.report({'INFO'}, props.status)
        return {'FINISHED'}


class CADCORE_OT_draft_check(bpy.types.Operator):
    bl_idname = "cadcore.draft_check"
    bl_label = "Draft Check"
    bl_description = ("Will it come out of a mould: pulled along the picked flat face's normal, "
                      "or up; walls that need draft and undercuts are painted on the part")

    min_angle: FloatProperty(name="Least draft", default=1.0, min=0.0, max=45.0,
                             description="degrees a wall needs")

    def execute(self, context):
        props = context.scene.cadcore
        client = get_client(context)
        direction = [0.0, 0.0, 1.0]
        faces = sync.selected_face_names()
        if len(faces) == 1:
            try:
                direction = client.call("face_frame", face=faces[0])["normal"]
            except ServerError:
                pass                                   # a round face: pulled straight up
        try:
            out = client.call("draft_check", direction=direction, min_angle=self.min_angle,
                              deflection=max(props.deflection, 0.3))
        except ServerError as exc:
            return report_error(self, exc)
        severity = {name: 0.55 for name in out["needs_draft"]}
        for entry in out["undercuts"]:
            severity[entry["face"]] = 1.0
        sync.paint_faces(severity)
        props.status = "%d wall(s) need draft, %d undercut(s), pulled along %s" % (
            len(out["needs_draft"]), len(out["undercuts"]),
            ", ".join("%.1f" % c for c in direction))
        props.status_is_error = False
        self.report({'INFO'} if out["ok"] else {'WARNING'}, props.status)
        return {'FINISHED'}


class CADCORE_OT_mass(bpy.types.Operator):
    bl_idname = "cadcore.mass"
    bl_label = "Mass"
    bl_description = "Grams by the document's material, and where the centre of mass is"

    def execute(self, context):
        props = context.scene.cadcore
        try:
            out = get_client(context).call("mass_properties")
        except ServerError as exc:
            return report_error(self, exc)
        props.status = "%.1f g of %s, centre of mass at %s" % (
            out["mass_g"], out["material"], ", ".join("%.1f" % c for c in out["centre_of_mass"]))
        props.status_is_error = False
        self.report({'INFO'}, props.status)
        return {'FINISHED'}


class CADCORE_OT_printability(bpy.types.Operator):
    bl_idname = "cadcore.printability"
    bl_label = "Check Printability"
    bl_description = ("Overhangs, thin walls and small holes, reported on the faces "
                      "that have them")

    def execute(self, context):
        props = context.scene.cadcore
        axis = {'X': (1, 0, 0), 'Y': (0, 1, 0), 'Z': (0, 0, 1)}[props.print_up]
        try:
            out = get_client(context).call(
                "printability", up=list(axis), overhang_deg=props.print_overhang,
                min_wall=props.print_wall, min_hole=props.print_hole,
                deflection=max(props.deflection, 0.3))
        except ServerError as exc:
            return report_error(self, exc)

        severity = {}
        for entry in out["overhangs"]:
            severity[entry["face"]] = max(severity.get(entry["face"], 0), 0.55)
        for entry in out["thin_walls"] + out["small_holes"]:
            severity[entry["face"]] = 1.0
        sync.paint_faces(severity)
        props.status = "%.0f%% unsupported, %d thin wall(s), %d small hole(s)" % (
            out["unsupported_fraction"] * 100, len(out["thin_walls"]),
            len(out["small_holes"]))
        self.report({'INFO'} if out["ok"] else {'WARNING'}, props.status)
        return {'FINISHED'}
