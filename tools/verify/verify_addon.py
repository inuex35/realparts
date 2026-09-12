"""Drive the add-on headlessly: select -> fillet -> rebuild -> still valid.

    blender -b -noaudio -P tools/verify/verify_addon.py

Everything the viewport would do is done here by setting ``select`` flags on the
tessellated mesh, so the round trip is exercised without a window.
"""
import json
import math
import pathlib
import re
import os
import sys

import bpy

import shutil

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
# a working copy, not the shipped example: this pass *edits* the document, and
# an edit now writes its unsaved state beside the file it belongs to. Driving
# the add-on should not leave anything in the repository
os.makedirs(os.path.join(REPO, "build"), exist_ok=True)
DOC = os.path.join(REPO, "build", "verify_doc.json")
shutil.copy(os.path.join(REPO, "examples", "sketch_plate.json"), DOC)
for stale in (DOC.replace(".json", ".autosave.json"),):
    if os.path.exists(stale):
        os.remove(stale)
fails = []


def pytest_approx(value, tolerance=1e-6):
    class _Near:
        def __eq__(self, other):
            return abs(other - value) <= tolerance * max(1.0, abs(value))
    return _Near()


def check(label, ok, extra=""):
    print("VERIFY %-40s %s %s" % (label, "ok" if ok else "FAIL", extra))
    if not ok:
        fails.append(label)


def _died(kind, value, tb):
    """`blender -b -P` exits 0 even when the script raises, so say it loudly."""
    import traceback

    traceback.print_exception(kind, value, tb)
    print("VERIFY RESULT died: %s: %s" % (kind.__name__, value))


sys.excepthook = _died

bpy.ops.preferences.addon_enable(module="cadcore_bridge")
import cadcore_bridge as addon                                     # noqa: E402

props = bpy.context.scene.cadcore


def cad():
    """The body, its name table and its attribute, re-read after every rebuild."""
    ob = addon.sync.body()
    return ob, json.loads(ob["cad_face_table"]), ob.data.attributes["cad_face"]


def select_faces(*names):
    """Pick by name, through the add-on: an assembly is one object per part and
    the named face is only on one of them."""
    addon.sync.select_faces(list(names))

r = bpy.ops.cadcore.open(filepath=DOC)
check("open document", r == {'FINISHED'}, props.status)
check("a document that is one part is one object, in the scene",
      [o.name for o in addon.sync.bodies()] == ["cad_body"]
      and bpy.data.objects.get("cad_edges") is not None
      and not [c for c in bpy.data.collections if c.name.startswith("cad:")],
      str([o.name for o in bpy.data.objects if o.name.startswith("cad")]))
body, table, attr = cad()
check("mesh created", len(body.data.polygons) > 0, "%d polygons" % len(body.data.polygons))
check("face names on polygons", all(addon.sync.face_name(body, p.index) in table
                                    for p in body.data.polygons), str(table))
check("feature list", [f.name for f in props.features] == ["profile", "plate", "rounded"],
      str([f.name for f in props.features]))
check("parameters listed", {p.name for p in props.parameters} ==
      {"width", "depth", "notch", "thickness", "corner_r"})

# a viewport pick: two faces whose shared edge is the one to round
select_faces("plate/left", "plate/top")
names = addon.sync.selected_edge_names(addon.state.get_client(bpy.context))
check("selection -> edge names", names == ["plate/left|plate/top"], str(names))

before = props.faces
r = bpy.ops.cadcore.fillet_selected(radius=2.5)
check("fillet operator", r == {'FINISHED'}, props.status)
check("new faces appeared", props.faces > before, "%d -> %d" % (before, props.faces))
body, table, attr = cad()
check("fillet face is named", any(n.startswith("fillet1/") for n in table), str(table))
check("mesh re-synced", len(body.data.polygons) > 0, "%d polygons" % len(body.data.polygons))

# the reference must survive a parameter change
volume = props.volume
props.parameters["width"].value = 118.0
check("parameter edit rebuilds", abs(props.volume - volume) > 1.0,
      "%.0f -> %.0f mm3" % (volume, props.volume))
body, table, attr = cad()
check("fillet survived the edit", any(n.startswith("fillet1/") for n in table), props.status)
check("still in envelope", props.in_envelope, props.status)

# a face pick that makes new geometry: sketch on the face, cut a pocket
select_faces("plate/top")
props.pocket_kind = 'pocket'
props.pocket_w, props.pocket_h, props.pocket_depth = 30.0, 20.0, 3.0
volume = props.volume
r = bpy.ops.cadcore.pocket_face()
check("pocket on face", r == {'FINISHED'} and abs(volume - props.volume - 1800.0) < 1.0,
      "%.0f -> %.0f mm3" % (volume, props.volume))
body, table, attr = cad()
check("pocket faces are named", "pocket1/floor" in table,
      str([n for n in table if n.startswith("pocket1")]))
check("pocket size is a parameter",
      {"pocket1_w", "pocket1_h", "pocket1_depth"} <= {p.name for p in props.parameters})
pocketed = props.volume
props.parameters["pocket1_depth"].value = 6.0
check("pocket depth drives the model", abs(pocketed - props.volume - 1800.0) < 1.0,
      "%.0f -> %.0f mm3" % (pocketed, props.volume))
props.parameters["width"].value = 118.0
body, table, attr = cad()
check("pocket follows its face", "pocket1/floor" in table, props.status)

# chamfer is the same gesture with a different verb
select_faces("plate/left", "plate/bottom")
volume = props.volume
r = bpy.ops.cadcore.fillet_selected(radius=1.5, kind="chamfer")
check("chamfer operator", r == {'FINISHED'} and props.volume < volume, props.status)
body, table, attr = cad()
check("chamfer face is named", any(n.startswith("chamfer1/") for n in table),
      str([n for n in table if n.startswith("chamfer1")]))

# a repeated pocket: one feature, three holes. On a fresh copy of the document,
# because the earlier pocket already hollowed out the middle of this face.
bpy.ops.cadcore.open(filepath=DOC)
select_faces("plate/top")
props.pocket_w, props.pocket_h, props.pocket_depth = 6.0, 6.0, 2.0
props.pocket_count, props.pocket_pitch, props.pocket_axis = 3, 12.0, 'X'
volume = props.volume
r = bpy.ops.cadcore.pocket_face()
check("repeated pocket", r == {'FINISHED'} and abs(volume - props.volume - 3 * 72) < 1.0,
      "%.0f -> %.0f mm3" % (volume, props.volume))
body, table, attr = cad()
check("every instance is named", {"pocket1/floor", "pocket1/floor~2"} <= set(table),
      str([n for n in table if "/floor" in n]))
props.pocket_count = 1

# picking an edge on the body is the other gesture: the edge between two faces
select_faces()
import bmesh
body_ob = addon.sync.body()
bpy.context.view_layer.objects.active = body_ob
bpy.ops.object.mode_set(mode='EDIT')
bm = bmesh.from_edit_mesh(body_ob.data)
layer = bm.faces.layers.int.get("cad_face")
for f in bm.faces:
    f.select = False
# faces do not share vertices, so an edge of the part is any edge on a face's rim
between = next(e for e in bm.edges if e.is_boundary)
for e in bm.edges:
    e.select = e is between
bmesh.update_edit_mesh(body_ob.data)
names = addon.sync.selected_edge_names(addon.state.get_client(bpy.context))
check("edge pick on the body -> the edge between its two faces", len(names) == 1 and "|" in names[0], str(names))
bpy.ops.object.mode_set(mode='OBJECT')
edges_ob = bpy.data.objects.get("cad_edges")
check("the wire object is drawn, not picked", edges_ob is not None and edges_ob.hide_select)

step = os.path.join(REPO, "build", "verify.step")
os.makedirs(os.path.dirname(step), exist_ok=True)
r = bpy.ops.cadcore.export_step(filepath=step)
check("export step", r == {'FINISHED'} and os.path.getsize(step) > 1000, props.status)

# deleting a sketch an extrude still consumes must be refused, by name
props.feature_index = [f.name for f in props.features].index("profile")
try:
    bpy.ops.cadcore.remove_feature()
    refused = False
except RuntimeError as exc:
    refused = "feature_in_use" in str(exc)
check("refuses to orphan a sketch", refused, props.status)

props.feature_index = len(props.features) - 1
last = props.features[props.feature_index].name
count = len(props.features)
r = bpy.ops.cadcore.remove_feature()
check("remove feature takes its sketch with it",
      r == {'FINISHED'} and not any(f.name.startswith(last) for f in props.features),
      "removed %s -> %s" % (last, [f.name for f in props.features]))

# holes, shells and drafts from the same face pick
bpy.ops.cadcore.open(filepath=DOC)
select_faces("plate/top")
props.hole_d, props.hole_depth = 6.0, 0.0
props.hole_seat, props.hole_seat_d, props.hole_seat_depth = 'counterbore', 11.0, 3.0
volume = props.volume
check("counterbored hole", bpy.ops.cadcore.hole_face() == {'FINISHED'} and props.volume < volume,
      props.status)
body, table, attr = cad()
check("hole faces are named", {"hole1/bore", "hole1/counterbore", "hole1/seat"} <= set(table),
      str([n for n in table if n.startswith("hole1")]))

props.shell_thickness = 2.0
select_faces("plate/top")
volume = props.volume
check("shell from a face pick",
      bpy.ops.cadcore.shell() == {'FINISHED'} and props.volume < volume / 2, props.status)

# history: the panel can move, roll back and undo
names = [f.name for f in props.features]
check("history listed", "shell1" in names and "hole1" in names, str(names))
props.feature_index = names.index("shell1")
check("roll back", bpy.ops.cadcore.rollback(clear=False) == {'FINISHED'}
      and props.rolled_back_to == "shell1", props.status)
bpy.ops.cadcore.rollback(clear=True)
check("roll forward again", props.rolled_back_to == "", props.status)

props.feature_index = [f.name for f in props.features].index("hole1")
args = {a.name: a.kind for a in props.feature_args}
check("feature arguments are editable", "at" not in args and args, str(args))
volume = props.volume
# by name, not by position: the fields follow the feature's declaration.
# 4, not 9: at 9 the 2 mm shell around an 11 mm counterbore leaves a wall the
# offset cannot make, and the kernel refuses the solid
next(a for a in props.feature_args if a.name == "diameter").value = 4.0
check("editing an argument rebuilds", props.volume > volume,
      "%.0f -> %.0f mm3 (bore 6 -> 4)" % (volume, props.volume))
check("and the panel does not claim it is an error", not props.status_is_error,
      props.status)

# and the other way: an edit the kernel will not stand behind
volume = props.volume
next(a for a in props.feature_args if a.name == "diameter").value = 9.0
check("an argument edit that breaks the solid is refused",
      abs(props.volume - volume) < 1e-9 and props.status_is_error,
      "%.0f mm3, %r" % (props.volume, props.status))
# and a refusal leaves no undo step behind, so one undo goes back past the
# edit that *was* accepted
volume = props.volume
bpy.context.view_layer.objects.active = addon.sync.body()
check("undo through the panel", bpy.ops.cadcore.undo(redo=False) == {'FINISHED'}
      and props.volume != volume, props.status)
check("redo", bpy.ops.cadcore.undo(redo=True) == {'FINISHED'}
      and abs(props.volume - volume) < 1e-6, props.status)

# imported geometry opens like a document and can be modelled on
step = os.path.join(REPO, "build", "verify.step")
r = bpy.ops.cadcore.open(filepath=step)
check("open a STEP file", r == {'FINISHED'} and props.faces > 0, props.status)
body, table, attr = cad()
check("imported faces are named", all("/" in n for n in table), str(table[:6]))
# any face this part will take a fillet on: about half of an imported solid's
# faces will not take a 1 mm one, which is geometry, not the add-on
filleted, on = False, None
for name in table:
    select_faces(name)
    volume = props.volume
    try:
        if bpy.ops.cadcore.fillet_selected(radius=1.0, kind="fillet") == {'FINISHED'} \
                and props.volume != volume:
            filleted, on = True, name
            break
    except RuntimeError:
        continue                       # this face cannot take it; try the next
check("model on imported geometry", filleted, "%s: %s" % (on, props.status))

# a document with studies: the field has to land on the CAD faces
r = bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples", "bracket.json"))
check("open the bracket", r == {'FINISHED'}, props.status)
r = bpy.ops.cadcore.simulate(show_field=True)
check("run studies", r in ({'FINISHED'}, {'CANCELLED'}) and "MPa" in props.status
      and "Hz" in props.status and "C" in props.status, props.status)
worst = next((s.worst_face for s in props.studies if s.worst_face), "")
check("the panel names the face with the highest stress", worst != "" and "/" in worst, worst)
r = bpy.ops.cadcore.select_faces(names=worst)
check("and can select it", r == {'FINISHED'} and addon.sync.selected_face_names(addon.sync.body()) == [worst],
      str(addon.sync.selected_face_names(addon.sync.body())))
check("the headline says peak and settled", "settled" in props.status and "peak" in props.status, props.status)
body, table, attr = cad()
stress = body.data.attributes.get("stress")
values = [d.value for d in stress.data] if stress else []
check("stress on every vertex", len(values) == len(body.data.vertices) and max(values) > 1.0,
      "%d values, peak %.1f MPa" % (len(values), max(values) if values else 0))
check("coloured for the viewport",
      body.data.color_attributes.get("stress_color") is not None
      and body.data.materials and body.data.materials[0].name == "cad_stress")
per_face = json.loads(body["cad_face_stress"])
hottest = max(per_face, key=lambda n: per_face[n]["max_MPa"])
check("stress is reported per CAD face", hottest in table,
      "%s at %.1f MPa" % (hottest, per_face[hottest]["max_MPa"]))
select_faces(hottest)
r = bpy.ops.cadcore.show_selection()
check("selection reports its stress", "MPa" in props.status, props.status)
props.parameters["thickness"].value = 9.0
body, table, attr = cad()
check("a rebuild drops the stale field", not body.data.materials
      and body.data.attributes.get("stress") is None, props.status)

# a drawing, from the panel
svg = os.path.join(REPO, "build", "verify.svg")
r = bpy.ops.cadcore.drawing(filepath=svg)
check("drawing from the panel", r == {'FINISHED'} and os.path.getsize(svg) > 5000,
      props.status)
check("the sheet carries measured dimensions", ">80.0</text>" in open(svg, encoding="utf-8").read(),
      "80 mm across the plate")

# an assembly, and the interference check
r = bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples", "assembly.json"))
check("open an assembly", r == {'FINISHED'}, props.status)
body, table, attr = cad()
check("parts keep scoped names", any(n.startswith("bush:") for n in table)
      and any(n.startswith("base:") for n in table), str(table[:4]))
r = bpy.ops.cadcore.interference()
check("interference check", r == {'FINISHED'} and "no interference" in props.status,
      props.status)

# end conditions: "through all" is a relationship, not a number
bpy.ops.cadcore.open(filepath=DOC)
select_faces("plate/top")
props.pocket_w, props.pocket_h = 12.0, 12.0
props.pocket_until, props.pocket_symmetric = 'through_all', False
volume = props.volume
check("pocket through all", bpy.ops.cadcore.pocket_face() == {'FINISHED'}
      and abs(volume - props.volume - 12 * 12 * 8) < 1.0,
      "%.0f -> %.0f mm3 (a 12x12 hole through 8 mm)" % (volume, props.volume))
body, table, attr = cad()
check("through all leaves no floor", not any(n.endswith("/floor") for n in table),
      str([n for n in table if n.startswith("pocket1")]))
props.pocket_until = 'depth'

# the sketch behind a face, which is what the editor picks up
bpy.ops.cadcore.open(filepath=DOC)
select_faces("plate/front")
client = addon.state.get_client(bpy.context)
check("a face names the sketch behind it",
      client.call("sketch_of", face="plate/front")["sketch"] == "profile",
      str(client.call("sketch_of", face="plate/front")))
geometry = client.call("sketch_geometry", sketch="profile")
check("the sketch comes back drawable",
      len(geometry["points"]) == 5 and {s["name"] for s in geometry["segments"]} ==
      {"front", "right", "chamfer", "back", "left"},
      "%d points, %d segments" % (len(geometry["points"]), len(geometry["segments"])))
check("a fully constrained sketch says so", geometry["editable"] is False,
      "dof %s" % geometry["dof"])
check("and every point of it draws as held",
      set(geometry["free"].values()) == {False},
      str(sorted(n for n, ok in geometry["free"].items() if ok)))
before_drag = client.call("build")["volume_mm3"]
held = client.call("drag_point", sketch="profile", point="b", to=[95.0, 4.0])
# the parameters offered are the ones in the dimensions holding *this* point:
# b is fixed by "depth - notch", and width holds a different point
check("dragging a held point explains itself",
      held["held"] and held["parameters"] == ["depth", "notch"],
      "held by %s -> %s" % (sorted({c["type"] for c in held["held_by"]}),
                            held["parameters"]))
# measured against what the model was before the drag, not against itself
after_drag = client.call("build")["volume_mm3"]
check("and changes nothing", abs(after_drag - before_drag) < 1e-9,
      "%.3f -> %.3f mm3" % (before_drag, after_drag))
# the pen's landing points are read in edit mode too: a pick leaves the body
# there, the attribute reads empty there, and the pen came out with an
# IndexError instead of its anchors
bpy.ops.cadcore.open(filepath=DOC)
select_faces("plate/top")
_frame = client.call("face_frame", face="plate/top")
_body = addon.sync.body()
bpy.context.view_layer.objects.active = _body
bpy.ops.object.mode_set(mode='EDIT')
_corners = addon.draw_op._corners_of("plate/top")
check("the pen finds the face's corners with the body in edit mode",
      len(_corners) >= 4, "%d corners" % len(_corners))
bpy.ops.object.mode_set(mode='OBJECT')
check("and in object mode", len(addon.draw_op._corners_of("plate/top")) >= 4,
      str(len(addon.draw_op._corners_of("plate/top"))))
check("and none for a face that is on no part", addon.draw_op._corners_of("nowhere/+z") == [], "")

# --- the sketch is picked on the part, and there is no mode to be in ---------
# the overlay writes where every point and line landed in region pixels and
# the pick tool clicks those; a spot must not outlive what it belonged to
check("the sketch modal is gone", not hasattr(bpy.types, "CADCORE_OT_edit_sketch")
      and hasattr(bpy.types, "CADCORE_OT_drag_sketch_point")
      and hasattr(bpy.types, "CADCORE_OT_hold_sketch"),
      "a mode to enter and leave is still registered")
addon.pick.forget_sketch()
addon.pick.forget_sketch_picks()
check("nothing on a sketch is clickable while none is drawn",
      addon.pick.on_sketch_at(100, 100) is None and addon.pick.sketch_picks() == [],
      str(addon.pick.SKETCH[0]))
addon.pick.SKETCH[0] = "profile"
addon.pick.SKETCH_POINTS[:] = [(100.0, 100.0, "a"), (200.0, 100.0, "b")]
addon.pick.SKETCH_LINES[:] = [("front", [(100.0, 100.0), (200.0, 100.0)]),
                              ("left", [(100.0, 100.0), (100.0, 200.0)])]
check("a click lands on the point, not on the lines that meet at it",
      addon.pick.on_sketch_at(102, 101) == ("point", "a"),
      str(addon.pick.on_sketch_at(102, 101)))
check("and away from the points it lands on the line",
      addon.pick.on_sketch_at(150, 103) == ("line", "front"),
      str(addon.pick.on_sketch_at(150, 103)))
check("and away from the sketch it lands on nothing",
      addon.pick.on_sketch_at(400, 400) is None,
      str(addon.pick.on_sketch_at(400, 400)))
addon.pick.pick_on_sketch(("point", "a"), False)
addon.pick.pick_on_sketch(("point", "b"), True)
check("shift adds a second pick",
      addon.pick.sketch_picks() == [("point", "a"), ("point", "b")],
      str(addon.pick.sketch_picks()))
addon.pick.pick_on_sketch(("point", "b"), True)
check("and shift-clicking one again takes it off",
      addon.pick.sketch_picks() == [("point", "a")], str(addon.pick.sketch_picks()))
addon.pick.SKETCH[0] = "pocket1_profile"
check("picks made on one sketch do not answer for another",
      addon.pick.sketch_picks() == [], str(addon.pick.sketch_picks()))
addon.pick.SKETCH[0] = "profile"
check("and they are still there when it is back on screen",
      addon.pick.sketch_picks() == [("point", "a")], str(addon.pick.sketch_picks()))

# only the holds that fit the picks, because a point cannot be parallel
_h = addon.holding
check("a point and a line each get their own holds",
      _h.fitting([("point", "a")]) == ["fix"]
      and _h.fitting([("point", "a"), ("point", "b")]) == ["coincident", "distance"]
      and _h.fitting([("line", "front")]) == ["horizontal", "vertical"]
      and len(_h.fitting([("line", "front"), ("line", "left")])) == 5,
      str(_h.fitting([("line", "front"), ("line", "left")])))
check("and a mixed pick gets none, with what to pick instead",
      _h.fitting([("point", "a"), ("line", "front")]) == []
      and "not both" in _h.what_it_needs([("point", "a"), ("line", "front")]),
      _h.what_it_needs([("point", "a"), ("line", "front")]))
check("three of anything gets none either",
      _h.fitting([("point", "a"), ("point", "b"), ("point", "c")]) == [], "")
_pair = [("point", "a"), ("point", "b")]
_built = _h.build("distance", _pair, geometry)
check("a dimension is built at the size the sketch already is",
      abs(_built["value"] - math.dist(geometry["points"]["a"],
                                      geometry["points"]["b"])) < 1e-3,
      "%.4f" % _built["value"])
_lines = [("line", "front"), ("line", "left")]
check("and an angle at the angle it already is",
      abs(abs(_h.build("angle", _lines, geometry)["value"]) - 90.0) < 1e-6,
      str(_h.build("angle", _lines, geometry)["value"]))
check("a hold that does not fit its picks is refused, not guessed",
      _h.build("distance", _lines, geometry) is None
      and _h.build("parallel", _pair, geometry) is None, "")

# end to end: take a hold off the sketch, then put it back from the menu
_before = client.call("sketch_constraints", sketch="profile")["constraints"]
_index = next(i for i, c in enumerate(_before) if c["type"] == "horizontal")
_line = _before[_index]["line"]
client.call("remove_constraint", sketch="profile", index=_index)
check("a sketch with a hold taken off has a point free",
      any(client.call("sketch_geometry", sketch="profile")["free"].values()),
      str(client.call("sketch_geometry", sketch="profile")["dof"]))
addon.pick.SKETCH[0] = "profile"
addon.pick.PICKED_SKETCH[:] = [("line", _line)]
addon.pick.PICKED_ON[0] = "profile"
r = bpy.ops.cadcore.hold_sketch(kind="horizontal")
_after = client.call("sketch_constraints", sketch="profile")["constraints"]
check("right-clicking the line puts the hold back",
      r == {'FINISHED'} and any(c["type"] == "horizontal" and c.get("line") == _line
                                for c in _after),
      "%s -> %d constraints" % (r, len(_after)))
check("and the picks are dropped once the hold is on",
      addon.pick.sketch_picks() == [], str(addon.pick.sketch_picks()))
try:
    _refused = bpy.ops.cadcore.hold_sketch(kind="horizontal")
except RuntimeError as _exc:
    _refused = str(_exc)
check("a hold with nothing picked is refused, and says what to pick",
      "pick a point or a line" in str(_refused), str(_refused))


class MenuSpy:
    """Records what a menu drew: the operators on it and their labels."""

    def __init__(self):
        self.items = []
        self.labels = []

    def operator(self, idname, text="", icon="", **kwargs):
        self.items.append((idname, text))
        return type("Set", (), {"__setattr__": lambda *a: None})()

    def operator_menu_enum(self, idname, prop, text="", icon=""):
        return self.operator(idname, text)

    def label(self, text="", icon=""):
        self.labels.append(text)

    def separator(self, **kwargs):
        pass

    def menu(self, *args, **kwargs):
        pass

    def row(self, *args, **kwargs):
        return self

    def column(self, *args, **kwargs):
        return self

    def box(self, *args, **kwargs):
        return self

    def prop(self, *args, **kwargs):
        pass


def menu_for(picks):
    """What the right-click menu draws with these sketch picks."""
    addon.pick.SKETCH[0] = "profile"
    addon.pick.PICKED_SKETCH[:] = list(picks)
    addon.pick.PICKED_ON[0] = "profile" if picks else None
    bpy.context.view_layer.objects.active = addon.sync.body()
    spy = MenuSpy()
    addon.menus.draw_body_context_menu(type("S", (), {"layout": spy})(), bpy.context)
    return spy


select_faces("plate/front")
_menu = menu_for([("point", "a"), ("point", "b")])
_holds = [text for idname, text in _menu.items if idname == "cadcore.hold_sketch"]
check("the menu on two points offers those two holds and no others",
      _holds == ["Put Them Together", "Hold This Distance"], str(_holds))
check("and nothing about the face they are drawn on",
      not any(i == "cadcore.press_pull" for i, _ in _menu.items),
      str([i for i, _ in _menu.items]))
_menu = menu_for([("line", "front")])
check("the menu on one line offers level and upright",
      [t for i, t in _menu.items if i == "cadcore.hold_sketch"] ==
      ["Keep It Level", "Keep It Upright"],
      str([t for i, t in _menu.items if i == "cadcore.hold_sketch"]))
_menu = menu_for([])
check("and with nothing picked on it the face's own menu is back",
      any(i == "cadcore.press_pull" for i, _ in _menu.items),
      str([i for i, _ in _menu.items]))
addon.pick.forget_sketch()
addon.pick.forget_sketch_picks()
_src = (pathlib.Path(addon.__file__).parent / "viewport" / "pick.py").read_text(encoding="utf-8")
check("a press on a point of the sketch drags it",
      "drag_sketch_point" in _src and "on_sketch_at" in _src,
      "the pick tool does not reach the sketch")
_src = (pathlib.Path(addon.__file__).parent / "ui" / "overlay.py").read_text(encoding="utf-8")
check("and the overlay clears the spots before it draws",
      "marks.forget_sketch()" in _src.split("def draw(context)")[1][:300],
      "a spot can outlive the sketch it belonged to")
try:
    client.call("sketch_of", face="rounded/side")
    refused = ""
except addon.client.ServerError as exc:
    refused = exc.kind
check("a face with nothing sketched behind it is refused",
      refused == "no_sketch_behind_it", refused or "not refused")

# dimensions drawn on the model, and one of them typed over
props.feature_index = [f.name for f in props.features].index("rounded")
if props.show_dimensions:                    # the pick tool turns them on by itself
    bpy.ops.cadcore.show_dimensions()
check("dimensions toggle on", bpy.ops.cadcore.show_dimensions() == {'FINISHED'}
      and props.show_dimensions, "overlay armed")
volume = props.volume
check("typing over a dimension rebuilds",
      bpy.ops.cadcore.edit_dimension(name="corner_r", value=4.0) == {'FINISHED'}
      and props.volume != volume, "%.0f -> %.0f mm3" % (volume, props.volume))
bpy.ops.cadcore.show_dimensions()
check("dimensions toggle off", not props.show_dimensions, "overlay disarmed")

# a mesh a slicer can open, and the checks that decide whether it will print
stl = os.path.join(REPO, "build", "verify.stl")
r = bpy.ops.cadcore.export_mesh(filepath=stl)
check("export STL", r == {'FINISHED'} and os.path.getsize(stl) > 1000, props.status)
three = os.path.join(REPO, "build", "verify.3mf")
r = bpy.ops.cadcore.export_mesh(filepath=three)
import zipfile
check("export 3MF", r == {'FINISHED'} and
      "3D/3dmodel.model" in zipfile.ZipFile(three).namelist(), props.status)

props.print_wall, props.print_hole = 3.0, 5.0
r = bpy.ops.cadcore.printability()
check("printability check", r in ({'FINISHED'}, {'CANCELLED'}) and "%" in props.status,
      props.status)
body, table, attr = cad()
check("findings are painted on the faces",
      body.data.color_attributes.get("stress_color") is not None
      and body.data.attributes.get("stress") is not None, "coloured by severity")

# restarting the kernel: the one path with no coverage until it was found broken
r = bpy.ops.cadcore.restart()
check("restart the kernel", r == {'FINISHED'} and props.faces > 0, props.status)
check("the model is back after a restart", len(cad()[0].data.polygons) > 0,
      "%d polygons" % len(cad()[0].data.polygons))

# properties really are registered -- annotations are strings under
# `from __future__ import annotations`, and Blender has to resolve them
rna = bpy.ops.cadcore.fillet_selected.get_rna_type()
check("operator properties are registered",
      {"radius", "kind"} <= {p.identifier for p in rna.properties},
      str([p.identifier for p in rna.properties if p.identifier != "rna_type"]))
check("panel properties are registered",
      {"pocket_w", "hole_standard", "deflection"} <=
      {p.identifier for p in addon.props.CADCORE_Props.bl_rna.properties})

# the kernel bootstrap only needs to know where things are, not to run
host = addon.client.find_host_python()
check("a host python for the kernel was found", host is not None, str(host))
check("and it is Blender's own, so a buyer installs nothing first",
      host == sys.executable, "%s vs %s" % (host, sys.executable))
python, site = addon.client.kernel_for(REPO)
client = addon.state.get_client(bpy.context)
check("the kernel path is where the client looks",
      (python, site) == (client.python, client.site), "%s %s" % (python, site))
check("a kernel for Blender's Python is versioned by it",
      addon.client.kernel_prefix(REPO).endswith("cp%d%d" % sys.version_info[:2])
      and addon.client.kernel_site(REPO).startswith(addon.client.kernel_prefix(REPO)),
      addon.client.kernel_site(REPO))


# --- threads and surfaces, from the viewport ------------------------------------
bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples", "bracket.json"))
volume = props.volume
select_faces("h1/side")
props.thread_standard = 'M4'
props.thread_clearance = 0.15
r = bpy.ops.cadcore.thread_face()
# h1 is a hole, so its thread cuts *outwards* into the wall: the part loses
# material, which is the opposite of what threading a shaft would do
check("a thread can be cut from a face pick", r == {'FINISHED'} and props.volume < volume,
      "%.1f -> %.1f mm3 | %s" % (volume, props.volume, props.status))
check("and the thread names itself", "M4" in props.status, props.status)

#: what the one-face menu offers, in the order it draws them
FLAT_ONLY = ("Push / Pull", "Hole", "Pocket", "Draw on It", "Offset Plane")


def _menu_for(face):
    """What the context menu would offer for one picked face."""
    from cadcore_bridge.ui import selection as _sel

    select_faces(face)
    shape = _sel.face_shape(bpy.context, [face])
    flat = () if shape == "cylinder" else FLAT_ONLY
    if shape == "cylinder":
        return flat + ("Thread It", "Pattern Around It")
    if shape == "plane":
        return flat + ("Mirror Across It",)
    return flat


# --- mirror and pattern, each from one face pick --------------------------------
bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples", "bracket.json"))
volume = props.volume
select_faces("plate/-y")
r = bpy.ops.cadcore.mirror()
check("a mirror from one flat face pick",
      r == {'FINISHED'} and props.volume > volume * 1.5,
      "%.1f -> %.1f mm3 | %s" % (volume, props.volume, props.status))
bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples", "bracket.json"))
volume = props.volume
select_faces("h1/side")
r = bpy.ops.cadcore.pattern(count=4)
check("a ring of copies from one round face pick",
      r == {'FINISHED'} and props.volume > volume and "4 around" in props.status,
      "%.1f -> %.1f mm3 | %s" % (volume, props.volume, props.status))
bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples", "bracket.json"))
round_face, flat_face = _menu_for("h1/side"), _menu_for("plate/-y")
check("the menu offers each only where it can work",
      round_face == ("Thread It", "Pattern Around It")
      and flat_face == FLAT_ONLY + ("Mirror Across It",),
      "%s | %s" % (round_face, flat_face))
# every one of those five asks the kernel for a frame and is refused on a
# curved face, so a menu that still drew them would be offering five refusals
for _item, _op, _args in (("Hole", "add_hole", {"face": "h1/side", "diameter": 2}),
                          ("Pocket", "add_pocket", {"face": "h1/side", "width": 3,
                                                    "height": 3, "depth": 1}),
                          ("Push / Pull", "move_face", {"face": "h1/side", "distance": 1}),
                          ("Offset Plane", "add_plane", {"face": "h1/side", "offset": 2})):
    try:
        addon.state.get_client(bpy.context).call(_op, **_args)
        _refused = ""
    except addon.client.ServerError as _exc:
        _refused = _exc.kind
    check("and %s really would be refused there" % _item,
          _refused in ("non_planar_face", "not_planar") and _item not in round_face,
          _refused or "it was allowed")

bpy.ops.cadcore.open(filepath=DOC)
volume = props.volume
select_faces("rounded/side")
r = bpy.ops.cadcore.delete_faces(heal=True)
check("a face can be erased and healed over",
      r == {'FINISHED'} and props.volume > volume,
      "%.1f -> %.1f mm3" % (volume, props.volume))

bpy.ops.cadcore.open(filepath=DOC)
solid = props.volume
select_faces("plate/top")
r = bpy.ops.cadcore.delete_faces(heal=False)
check("or left open, which makes it a surface", r == {'FINISHED'}, props.status)
props.fill_continuity = 'G0'
r = bpy.ops.cadcore.cap()
check("and patched shut again",
      r == {'FINISHED'} and abs(props.volume - solid) < 1.0,
      "%.1f -> %.1f mm3" % (solid, props.volume))




# --- the panel's fields come from the feature's own declaration -----------------
client = addon.state.get_client(bpy.context)   # a restart above may have replaced it
bpy.ops.cadcore.open(filepath=DOC)
props.feature_index = next(i for i, f in enumerate(props.features) if f.kind == "extrude")
addon.state._refresh_feature_args(bpy.context)
kinds = {a.name: a.kind for a in props.feature_args}
check("an argument is drawn by what the feature says it is",
      kinds.get("distance") == "number" and kinds.get("symmetric") == "flag",
      str(sorted(kinds.items())))
check("and a flag left at its default is still offered",
      "symmetric" not in (client.call("describe_document")["features"][1].get("args", {})),
      "symmetric is not in the document, but the panel has it")

flag = next(a for a in props.feature_args if a.name == "symmetric")
flag.flag = True                                   # the property's update runs the edit
extrude = next(f for f in client.call("describe_document")["features"]
               if f["id"] == "plate")
# a symmetric extrusion of the same length has the same volume -- it straddles
# the sketch instead of starting at it -- so the document is what says it landed
check("editing a flag from the panel reaches the document",
      extrude["args"].get("symmetric") is True, str(extrude["args"]))

r = None
try:
    client.call("edit_feature", feature_id="plate", args={"symetric": True})
except addon.client.ServerError as exc:
    r = exc.kind
check("and an argument the feature does not take is refused",
      r == "unknown_argument", str(r))


# --- the viewport asks the kernel what it can build -----------------------------
addon.state._refresh_families(bpy.context)
families = {f.name: f.category for f in props.families}
check("the kernel's feature types reach the panel", len(families) > 30,
      "%d types" % len(families))
check("and they carry the family the icons come from",
      families.get("thread") == "thread" and families.get("skin", families.get("surface")) == "surface",
      str(sorted(set(families.values()))))
check("an icon is chosen for a type nobody listed",
      addon.panels.icon_for("offset_surface", bpy.context) == 'OUTLINER_OB_SURFACE',
      addon.panels.icon_for("offset_surface", bpy.context))


# --- constraints, listed and edited from the panel ------------------------------
bpy.ops.cadcore.open(filepath=DOC)
props.feature_index = next(i for i, f in enumerate(props.features) if f.kind == "sketch")
addon.state._refresh_constraints(bpy.context)
check("a sketch lists its constraints", len(props.constraints) > 0,
      "%d on %s" % (len(props.constraints), props.sketch_name))
check("a dimension shows its value",
      any(c.has_value and c.value_text for c in props.constraints),
      str([(c.name, c.value_text) for c in props.constraints][:3]))
check("and the panel says how free the sketch is", props.sketch_dof == 0,
      "dof %d" % props.sketch_dof)

listed = len(props.constraints)
volume = props.volume
dimension = next(c for c in props.constraints if c.has_value)
r = bpy.ops.cadcore.remove_constraint(index=dimension.index)
check("a constraint can be taken off from the panel",
      r == {'FINISHED'} and len(props.constraints) == listed - 1,
      "%d -> %d" % (listed, len(props.constraints)))
check("and the sketch says it is loose now", props.sketch_dof > 0,
      "dof %d" % props.sketch_dof)

r = bpy.ops.cadcore.add_constraint(payload=json.dumps(
    {"type": "distance", "points": ["o", "a"], "value": 90}))
check("and put back", r == {'FINISHED'} and len(props.constraints) == listed,
      props.status)
check("which rebuilds the model", abs(props.volume - volume) < 1e-6,
      "%.1f -> %.1f mm3" % (volume, props.volume))

# an angle, swapped in for the perpendicularity it is equivalent to. `left`
# runs from d down to o and `front` along +x, so the angle from the first to
# the second is -90; getting the sense backwards solves to a mirrored plate
# rather than failing, which is why this is checked against a known volume.
upright = next(c for c in props.constraints
               if c.name == "vertical" and "left" in c.detail)
bpy.ops.cadcore.remove_constraint(index=upright.index)
check("taking off a vertical leaves the sketch loose", props.sketch_dof == 1,
      "dof %d" % props.sketch_dof)
r = bpy.ops.cadcore.add_constraint(payload=json.dumps(
    {"type": "angle", "lines": ["front", "left"], "value": -90}))
check("an angle of -90 constrains it again", r == {'FINISHED'} and props.sketch_dof == 0,
      "dof %d, %s" % (props.sketch_dof, props.status))
check("and rebuilds the same solid the perpendicular did",
      abs(props.volume - volume) < 1e-6,
      "%.3f -> %.3f mm3" % (volume, props.volume))


# --- what the constraint rows are drawn with ------------------------------------
# `-b` has no region to draw into, so the icon lookup the drawing does is
# checked over every type the panel can be handed
unknown = sorted(name for name in addon.panels.CONSTRAINT_ICONS.values()
                 if name not in addon.panels.icon_names())
check("every constraint icon is one Blender has", unknown == [], str(unknown))
check("and the fallback is too",
      addon.panels.CONSTRAINT_FALLBACK in addon.panels.icon_names(),
      addon.panels.CONSTRAINT_FALLBACK)
drawn = {c.name: addon.panels.constraint_icon(c.name) for c in props.constraints}
check("the constraints on this sketch all resolve to an icon",
      drawn and all(icon in addon.panels.icon_names() for icon in drawn.values()),
      str(sorted(drawn.items())[:4]))
check("a constraint type nobody listed still draws",
      addon.panels.constraint_icon("no_such_constraint")
      == addon.panels.CONSTRAINT_FALLBACK,
      addon.panels.constraint_icon("no_such_constraint"))

# --- every panel actually draws ------------------------------------------------
# a draw() runs only when someone is looking, so every body is run here
# against a layout that records instead of drawing
class Recorder:
    """Answers every layout call with itself, and remembers what was asked."""

    calls = 0

    def __getattr__(self, name):
        def call(*args, **kwargs):
            Recorder.calls += 1
            return self
        return call

    def __setattr__(self, name, value):
        pass


class Stand:
    """Stands in for the panel: a layout, and whatever bl_ attributes it reads."""

    def __init__(self, cls):
        self.layout = Recorder()
        self.bl_label = getattr(cls, "bl_label", "")
        self.bl_idname = getattr(cls, "bl_idname", "")


# menus too: a menu is not a Panel
panels = [c for c in addon.panels.__dict__.values()
          if isinstance(c, type)
          and issubclass(c, (bpy.types.Panel, bpy.types.Menu))
          and c not in (bpy.types.Panel, bpy.types.Menu)]
drawn, broke = 0, []
for cls in panels:
    try:
        if hasattr(cls, "poll") and not cls.poll(bpy.context):
            pass                      # still draw it: poll only hides it today
    except Exception:                                            # noqa: BLE001
        pass
    try:
        cls.draw(Stand(cls), bpy.context)
        drawn += 1
    except Exception as exc:                                     # noqa: BLE001
        broke.append("%s: %s: %s" % (cls.__name__, type(exc).__name__, exc))
check("every panel's draw() runs", not broke and drawn == len(panels),
      "%d of %d drawn, %d layout calls%s" % (drawn, len(panels), Recorder.calls,
                                             "" if not broke else " -- " + "; ".join(broke)))


# --- parameters Blender can animate --------------------------------------------
# A parameter had a field in the sidebar but no home Blender understands: a
# driver cannot point at a collection on the scene, and a keyframe on one is not
# something anybody finds again. They are mirrored onto the body as ID
# properties, which is where a number gets a slider, a keyframe and a driver.
bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples/mechanism/link.json"))
body = addon.sync.body()
owned = addon.animate.owned(body)
check("the document's parameters are on the body", owned == list(props.parameters.keys()),
      str(owned))
declared = addon.animate.values(body).get("centres")
check("and carry their value", declared == 80.0, str(declared))

ui = body.id_properties_ui("centres").as_dict()
check("a slider takes its range from the declared envelope",
      (ui.get("min"), ui.get("max")) == (20.0, 300.0),
      "%s to %s" % (ui.get("min"), ui.get("max")))
loose = body.id_properties_ui("ease").as_dict()
check("and a parameter with no envelope gets a soft one instead",
      loose.get("min") < 0.8 < loose.get("max"),
      "%.2f to %.2f" % (loose.get("min"), loose.get("max")))

# the toggle is the design, not caution: rebuilding costs 20-80 ms a frame
props.drive_on_frame = False
body["centres"] = 140.0
held = props.volume
bpy.context.scene.frame_set(bpy.context.scene.frame_current + 1)
check("with the toggle off a frame change rebuilds nothing",
      abs(props.volume - held) < 1e-9, "%.1f mm3" % props.volume)

props.drive_on_frame = True
bpy.context.scene.frame_set(bpy.context.scene.frame_current + 1)
check("with it on the model follows the parameter",
      props.volume > held + 1000.0, "%.1f -> %.1f mm3" % (held, props.volume))
props.drive_on_frame = False


# --- switching a feature off ---------------------------------------------------
# Suppressed, the feature stays and hands its input through, so the rest of
# the chain still builds.
bpy.ops.cadcore.open(filepath=DOC)
built = props.volume
props.feature_index = next(i for i, f in enumerate(props.features)
                           if f.kind in ("fillet", "chamfer", "hole", "pocket"))
picked = props.features[props.feature_index]
r = bpy.ops.cadcore.suppress_feature()
check("a feature can be switched off from the list",
      r == {'FINISHED'} and props.features[props.feature_index].suppressed,
      "%s -- %s" % (picked.name, props.status))
check("and the model rebuilds without it", abs(props.volume - built) > 1e-9,
      "%.1f -> %.1f mm3" % (built, props.volume))
check("while the feature is still in the history",
      any(f.name == picked.name for f in props.features), picked.name)

r = bpy.ops.cadcore.suppress_feature()
check("and switching it back on restores the solid",
      r == {'FINISHED'} and abs(props.volume - built) < 1e-6,
      "%.1f mm3" % props.volume)


# --- the work has to outlive the kernel that made it --------------------------
# Every edit above lived in the kernel's memory alone, and the add-on had no
# operator that could write any of it back. A restart -- or the crash the
# separate process exists to survive -- reopened the file and threw the session
# away, which is the opposite of what a crash guard is for.
AUTOSAVE = DOC.replace(".json", ".autosave.json")
if os.path.exists(AUTOSAVE):
    os.remove(AUTOSAVE)          # everything above edited without ever saving
bpy.ops.cadcore.open(filepath=DOC)
check("a freshly opened document is saved", props.saved and not props.unsaved_work,
      props.status)
before = props.volume
props.parameters["width"].value = 121.0
check("an edit says the file no longer holds it", not props.saved, props.status)
check("and it is on disk beside the document", os.path.exists(AUTOSAVE), AUTOSAVE)

edited = props.volume
bpy.ops.cadcore.restart()                     # the kernel dies and comes back
check("a restart keeps the unsaved work", abs(props.volume - edited) < 1e-6,
      "%.1f -> %.1f mm3" % (edited, props.volume))
check("and still says it is unsaved", not props.saved, props.status)

r = bpy.ops.cadcore.save()
check("save writes the document", r == {'FINISHED'} and props.saved, props.status)
check("and drops the sidecar it made obsolete", not os.path.exists(AUTOSAVE), AUTOSAVE)
check("the file now holds the edit",
      abs(json.load(open(DOC, encoding="utf-8"))["parameters"]["width"] - 121.0) < 1e-9,
      str(json.load(open(DOC, encoding="utf-8"))["parameters"]["width"]))
bpy.ops.cadcore.open(filepath=DOC)
check("reopening it is the edited part", abs(props.volume - edited) < 1e-6,
      "%.1f mm3" % props.volume)

# --- the panel answers to what is picked --------------------------------------
# The button and the refusal come from one declaration; this is the check
# that they cannot drift apart.
bpy.ops.cadcore.open(filepath=DOC)
sel = addon.selection

select_faces()                                    # nothing picked
picked = sel.counts(bpy.context)
check("with nothing picked the panel says so", picked == (0, 0), str(picked))
check("and a pocket says what it wants",
      sel.blocked("cadcore.pocket_face", picked) == "select 1 face",
      str(sel.blocked("cadcore.pocket_face", picked)))
check("and a draft wants more than one",
      sel.blocked("cadcore.draft", picked) == "select 2 or more faces",
      str(sel.blocked("cadcore.draft", picked)))

select_faces("plate/top")
picked = sel.counts(bpy.context)
check("one face picked is counted", picked[0] > 0, str(picked))
check("which is enough for a pocket",
      sel.blocked("cadcore.pocket_face", (1, 0)) is None)
check("and not for a draft",
      sel.blocked("cadcore.draft", (1, 0)) == "select 2 or more faces")
check("a draft takes two or more", sel.blocked("cadcore.draft", (3, 0)) is None)
check("a fillet takes faces when no edge is picked",
      sel.blocked("cadcore.fillet_selected", (2, 0)) is None)
check("and a tool that needs nothing is never blocked",
      sel.blocked("cadcore.thicken", (0, 0)) is None)
check("the picked faces are named", sel.names(bpy.context) == ["plate/top"],
      str(sel.names(bpy.context)))
check("and the readout counts CAD faces, not triangles", sel.summary(picked) == "1 face",
      sel.summary(picked))
check("with nothing picked it says so", sel.summary((0, 0)) == "nothing picked")
check("and a fillet with nothing picked asks for an edge, not a edge",
      sel.blocked("cadcore.fillet_selected", (0, 0)) == "select an edge",
      str(sel.blocked("cadcore.fillet_selected", (0, 0))))

# every panel draws *with* a selection too: the enable/disable path is new,
# and it only runs when something is picked
select_faces("plate/top", "plate/left")
again, broke_again = 0, []
for cls in panels:
    try:
        cls.draw(Stand(cls), bpy.context)
        again += 1
    except Exception as exc:                                     # noqa: BLE001
        broke_again.append("%s: %s" % (cls.__name__, exc))
check("every panel draws with something picked",
      not broke_again and again == len(panels), "; ".join(broke_again) or "%d" % again)

# --- a drag is one rebuild, not forty -----------------------------------------
throttle = addon.throttle
throttle.forget()
built = [0]
for tick in range(40):
    throttle.soon("drag", lambda: built.__setitem__(0, built[0] + 1))
check("a burst of ticks does not run forty times", built[0] == 1,
      "%d ran, %d held" % (built[0], throttle.pending()))
throttle.flush()
check("and the last value is never dropped", built[0] == 2, str(built[0]))
check("nothing is left held", throttle.pending() == 0)

# the selection has to survive the rebuild the held edit causes, or a fillet
# right after a drag sees nothing picked -- which is how this was found
bpy.ops.cadcore.open(filepath=DOC)
select_faces("plate/top", "plate/left")
was = sel.counts(bpy.context)
props.parameters["width"].value = 117.0
addon.state.get_client(bpy.context)               # flushes, which rebuilds
check("a rebuild keeps what was picked", sel.counts(bpy.context)[0] == was[0],
      "%s -> %s" % (was, sel.counts(bpy.context)))
check("and keeps the names", set(sel.names(bpy.context)) == {"plate/top", "plate/left"},
      str(sel.names(bpy.context)))

# and the document really does get the last drag value, not the first
bpy.ops.cadcore.open(filepath=DOC)
before = props.volume
for value in (100.0, 110.0, 118.0):
    props.parameters["width"].value = value
addon.state.get_client(bpy.context)               # any operation flushes first
doc = addon.state.get_client(bpy.context).call("describe_document")
check("the document holds the last value", abs(doc["parameters"]["width"] - 118.0) < 1e-9,
      str(doc["parameters"]["width"]))

# Blender's undo, and the kernel following it.
#
# What cannot be checked here is the keypress: an operator called from Python
# does not push an undo step -- `ed.undo.poll()` is False in a scripted Blender
# even straight after adding a cube -- so Ctrl+Z and F9 themselves need a
# human. What *is* new code is the handler, and it can be asked directly: put
# the depth back to where an undo would have restored it, and see whether the
# kernel and the mesh follow.
bpy.ops.cadcore.open(filepath=DOC)
start_depth, start_faces, start_revision = props.undo_depth, props.faces, props.revision
select_faces("plate/top", "plate/left")
bpy.ops.cadcore.fillet_selected(radius=2.0, kind="fillet")
edited_revision = props.revision
check("an edit moves the kernel's depth", props.undo_depth == start_depth + 1,
      "%d -> %d" % (start_depth, props.undo_depth))
check("and gives the state a new number", edited_revision != start_revision,
      "%d -> %d" % (start_revision, edited_revision))
check("and the part changes with it", props.faces != start_faces,
      "%d -> %d faces" % (start_faces, props.faces))

# what Blender's undo does to the scene: the properties come back holding the
# values they had in the step being restored -- the revision is the one that
# says which state that was
props.undo_depth, props.revision = start_depth, start_revision
# `on_undo` only schedules; the work is `_reconcile`, and there is no timer
# loop running under `-b`. The real Ctrl+Z path is checked in `verify_undo.py`,
# which needs a window
addon.state._reconcile()
check("the handler walks the kernel back to it", props.revision == start_revision,
      str(props.revision))
check("and the part is the one from before", props.faces == start_faces,
      "%d, wanted %d" % (props.faces, start_faces))
doc = addon.state.get_client(bpy.context).call("describe_document")
check("and the kernel agrees, not just the panel",
      doc["revision"] == start_revision and doc["undo_depth"] == start_depth,
      "%s / %s" % (doc["revision"], doc["undo_depth"]))

props.undo_depth, props.revision = start_depth + 1, edited_revision
addon.state._reconcile()
check("and forward again for a redo", props.faces != start_faces,
      "%d faces" % props.faces)

# How a message breaks at the width the sidebar happens to be.
#
# This is the only thing the sidebar's width decides, and it cannot be
# photographed: `Region.width` is read-only and `screen.region_scale` has no
# delta to give it, so the panel can only ever be shot at whatever width the
# window opened with. It is a function, though, and a function can be asked.
class Lines:
    """Collects the labels `wrap` draws, in order."""

    def __init__(self):
        self.said = []

    def label(self, text="", icon='NONE'):
        self.said.append(text)

    def column(self, align=False):
        return self

    def row(self, align=False):
        return self


class Width:
    def __init__(self, pixels):
        self.region = type("R", (), {"width": pixels})()


LONG = ("fillet_failed: OCCT could not build the fillet -- There are no "
        "suitable edges for chamfer or fillet, radius 500.0")

for pixels in (180, 280, 420, 900):
    lines = Lines()
    addon.panels.wrap(lines, LONG, Width(pixels))
    room = max(16, int((pixels - 24) / addon.panels.PER_CHAR))
    check("a refusal wraps at %d px" % pixels,
          lines.said and all(len(line) <= room for line in lines.said),
          "widest %d of %d columns" % (max(len(x) for x in lines.said), room))
    check("and loses no word at %d px" % pixels,
          " ".join(lines.said).split() == LONG.split(),
          " ".join(lines.said))

# a word longer than the whole column still has to appear somewhere
lines = Lines()
addon.panels.wrap(lines, "unresolved_reference:aVeryLongFaceNameThatCannotBeBroken",
                  Width(120))
check("a word wider than the panel is not dropped", len(lines.said) == 1,
      str(lines.said))

# Every operation the kernel has, reachable from the sidebar, or listed
# below with a reason.
NOT_IN_THE_UI = {
    # the Fillet/Chamfer toggle calls add_fillet with kind="chamfer", which is
    # what add_chamfer does; two buttons for one thing is one button too many
    "add_chamfer",
    # the plural is what set_parameter calls; a panel edits one field at a time
    "set_parameters",
    # a picture of the part, for a caller who has no viewport. Blender is a
    # viewport; drawing the model into a PNG and showing it in a panel would
    # be a photograph of the thing already on screen
    "render",
    # the assistant's surface. Each of these exists because a conversation
    # cannot do what a mouse does -- read a whole document, ask about every
    # face at once, send ten steps as one edit, or ask for smaller answers --
    # and each would be a button that does nothing a person wants pressed
    "add_feature",     # the panel has a button per feature type, which is better
    "apply",           # a batch is what a caller with latency needs
    "document_json", "load_json",     # the file, for something that has no file
    "describe_faces", "find_faces", "face_query_keys",   # the viewport is this
    "reply_style",     # how big an answer is; a panel reads what it reads
    "checkpoints",     # the list behind the Go Back To dialog
    "profile_shapes",  # the list behind the Profile dropdown
    "requirement_kinds",   # the list behind the Require dropdown
    "requirements",    # the status the Requirements panel *is*; every build carries it
}

# what the kernel has is asked of the kernel: `handle` answers an operation it
# does not know with the list of the ones it does, so the catalogue here is the
# running kernel's own rather than a copy that can age
try:
    addon.state.get_client(bpy.context).call("no_such_operation")
    kernel_ops = set()
except addon.client.ServerError as exc:
    kernel_ops = set(exc.detail.get("available", []))

reached = set()
for module in pathlib.Path(addon.__file__).parent.rglob("*.py"):
    text = module.read_text(encoding="utf-8")
    reached |= set(re.findall(r'call\(\s*"([a-z_]+)"', text))
    reached |= set(re.findall(r'operation\s*=\s*"([a-z_]+)"', text))
unreachable = sorted(kernel_ops - reached - NOT_IN_THE_UI)
check("every kernel operation is reachable from the panel", not unreachable,
      ("no button reaches: " + ", ".join(unreachable)) if unreachable
      else "%d of %d, %d deliberately not"
           % (len(kernel_ops & reached), len(kernel_ops), len(NOT_IN_THE_UI)))
check("and the exceptions are still exceptions",
      NOT_IN_THE_UI <= kernel_ops,
      str(sorted(NOT_IN_THE_UI - kernel_ops)))

# --- the path a person actually takes ------------------------------------------
# the checks above pick faces in object mode; a person picks in edit mode
# and presses a tool, and mesh data cannot be written in edit mode
bpy.ops.cadcore.open(filepath=DOC)
body, table, attr = cad()
bpy.context.view_layer.objects.active = body
body.select_set(True)
select_faces("plate/top")
props.pocket_kind = 'pocket'
props.pocket_w, props.pocket_h, props.pocket_depth = 20.0, 10.0, 2.0
volume = props.volume
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_mode(type='FACE')
try:
    outcome, trouble = bpy.ops.cadcore.pocket_face(), None
except Exception as exc:                                         # noqa: BLE001
    outcome, trouble = None, "%s: %s" % (type(exc).__name__, exc)
check("a tool run from edit mode does not raise", trouble is None, trouble or "ok")
check("and the model changed with it",
      outcome == {'FINISHED'} and abs(volume - props.volume - 400.0) < 1.0,
      "%.0f -> %.0f mm3" % (volume, props.volume))
check("and the object is still in edit mode",
      addon.sync.body().mode == 'EDIT',
      addon.sync.body().mode)
if addon.sync.body().mode == 'EDIT':
    bpy.ops.object.mode_set(mode='OBJECT')

# --- the three the panel offers and nothing here had ever pressed --------------
#
# The case for this add-on over the other Blender CAD add-ons is six things
# none of them do: a study, a drawing, a flat pattern, a printability check, a
# bill of materials, and a search for a lighter design. Three of the six had a
# button and no check. A capability nobody has pressed is a capability nobody
# knows about, which is the same as not having it.
bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples", "sheet_bracket.json"))
blank = os.path.join(REPO, "build", "verify_blank.dxf")
r = bpy.ops.cadcore.flat_pattern(filepath=blank)
check("a flat pattern from the panel",
      r == {'FINISHED'} and os.path.exists(blank) and os.path.getsize(blank) > 0,
      "%s -> %s" % (props.status, os.path.getsize(blank) if os.path.exists(blank) else "nothing"))
check("and it says what the blank costs",
      "bend" in props.status or "blank" in props.status, props.status)

bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples", "assembly.json"))
r = bpy.ops.cadcore.bill_of_materials()
check("a bill of materials from the panel", r == {'FINISHED'}, props.status)
check("and it weighs the parts", "g" in props.status, props.status)

bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples", "bracket.json"))
was = props.volume
r = bpy.ops.cadcore.optimize(trials=6, objective='volume', seed=3)
check("a search for a lighter one from the panel", r == {'FINISHED'}, props.status)
check("and it found one", props.volume <= was,
      "%.0f -> %.0f mm3" % (was, props.volume))

# --- direct manipulation, through the path a hand takes without a mouse ----------
bpy.ops.cadcore.open(filepath=DOC)
volume = props.volume
select_faces("plate/top")
r = bpy.ops.cadcore.press_pull(distance=3.0)
check("press/pull the top face 3 mm", r == {'FINISHED'} and props.volume > volume,
      "%.0f -> %.0f mm3 | %s" % (volume, props.volume, props.status))
# the same drag from where a pick leaves Blender: edit mode. The face has to be
# read from the BMesh there, and the tool has to leave the mode itself
volume = props.volume
bpy.ops.cadcore.pick(what='FACE')
addon.sync.select_faces(["plate/top"])
_body = addon.sync.body()
check("a face stays picked in edit mode",
      _body.mode == 'EDIT' and addon.sync.selected_face_names(_body) == ["plate/top"],
      "%s %s" % (_body.mode, addon.sync.selected_face_names(_body)))
r = bpy.ops.cadcore.press_pull(distance=2.0)
check("and press/pull works from edit mode", r == {'FINISHED'} and props.volume > volume,
      "%.0f -> %.0f mm3 | %s" % (volume, props.volume, props.status))
if addon.sync.body().mode == 'EDIT':
    bpy.ops.object.mode_set(mode='OBJECT')
# a picked edge gets one grip per thing that can be done to it, the way a
# face gets an arrow. Two shapes, not one shape and a gesture: 0.2.3 hid the
# chamfer behind a double click, where nobody looking at the screen found it
bpy.ops.cadcore.open(filepath=DOC)
bpy.ops.cadcore.pick(what='EDGE')
_body = addon.sync.body()
addon.sync.select_edge_between(_body, "plate/left", "plate/top")
addon.pick.remember_edge_handle(bpy.context, _body)
check("a picked edge gets a grip", bool(addon.pick.EDGE_HANDLE),
      str(sorted(addon.pick.EDGE_HANDLE))[:60])
check("one grip per thing that can be done to it",
      tuple(addon.pick.KINDS) == ("fillet", "chamfer"), str(addon.pick.KINDS))
check("the edge's grips are not the face's arrow's colour",
      all(c[:3] != (0.98, 0.86, 0.35) for c in addon.overlay.GRIP_COLOUR.values()),
      "two amber discs of one size look like one disc")
check("and a round and a corner cut are two colours, not one",
      addon.overlay.GRIP_COLOUR["fillet"] != addon.overlay.GRIP_COLOUR["chamfer"]
      and set(addon.overlay.GRIP_COLOUR) == set(addon.pick.KINDS),
      str(addon.overlay.GRIP_COLOUR))
check("and two shapes, so the colours are not the only difference",
      addon.overlay._round_grip((0.0, 0.0), 8.0) !=
      addon.overlay._cut_corner_grip((0.0, 0.0), 8.0),
      "a colour-blind reader has one round grip and one round grip")
check("each grip carries its word, and a press on the word is a press on the grip",
      set(addon.overlay.GRIP_WORD) == set(addon.pick.KINDS)
      and "_grip_at" in (pathlib.Path(addon.__file__).parent / "viewport"
                         / "pick.py").read_text(encoding="utf-8"),
      str(addon.overlay.GRIP_WORD))
addon.pick.EDGE_TIPS.clear()
addon.pick.EDGE_LABELS.clear()
addon.pick.EDGE_TIPS["fillet"] = (100.0, 100.0, 16.0)
addon.pick.EDGE_LABELS["chamfer"] = (200.0, 90.0, 60.0, 20.0)
check("the disc and the word both answer to a press",
      addon.pick._grip_at(110, 105) == "fillet" and addon.pick._grip_at(230, 100) == "chamfer"
      and addon.pick._grip_at(400, 400) is None, "")
addon.pick.FACE_TIPS.clear()
addon.pick.FACE_LABELS.clear()
addon.pick.HANDLE_TIP[:] = [300.0, 300.0, 14.0]
addon.pick.FACE_TIPS["draw"] = (100.0, 100.0, 14.0)
addon.pick.FACE_LABELS["plane"] = (200.0, 90.0, 60.0, 20.0)
addon.pick.FACE_LABELS["arrow"] = (320.0, 290.0, 70.0, 20.0)
check("the face's marks and their words answer to a press the same way",
      addon.pick._face_mark_at(105, 104) == "draw" and addon.pick._face_mark_at(230, 100) == "plane"
      and addon.pick._face_mark_at(303, 298) == "arrow" and addon.pick._face_mark_at(350, 300) == "arrow"
      and addon.pick._face_mark_at(400, 400) is None, "")
check("and every mark on a face has a word", set(addon.overlay.MARK_WORD) == {"arrow", "draw", "plane"},
      str(addon.overlay.MARK_WORD))
addon.pick.HANDLE_TIP.clear()
addon.pick.FACE_TIPS.clear()
addon.pick.FACE_LABELS.clear()
check("and the header says what the grip under the cursor does",
      all(k in addon.pick.HINTS for k in ("fillet", "chamfer", "arrow", "draw", "plane")),
      str(sorted(addon.pick.HINTS)))
addon.pick.EDGE_TIPS.clear()
addon.pick.EDGE_LABELS.clear()
check("nothing counts a double click any more",
      not hasattr(addon.pick, "_is_second_press")
      and not hasattr(addon.pick, "_LAST_PRESS"),
      "the double-click timer is still there")
volume = props.volume
r = bpy.ops.cadcore.drag_fillet(radius=1.2, kind="chamfer")
check("and a straight edge shows how long it is",
      "length_mm" in addon.pick.EDGE_HANDLE
      and addon.pick.EDGE_HANDLE["length_mm"] > 1.0,
      str(addon.pick.EDGE_HANDLE.get("length_mm")))
# a tip is a click target only while the handle it belongs to is there
addon.pick.EDGE_TIPS["fillet"] = (100.0, 100.0, 14.0)
addon.pick.EDGE_HANDLE.clear()
addon.pick.HANDLE_TIP[:] = [100.0, 100.0, 14.0]
addon.pick.HANDLE.clear()
_src = (pathlib.Path(addon.__file__).parent / "viewport" / "pick.py").read_text(encoding="utf-8")
check("a leftover tip is not a target without its handle",
      "if EDGE_HANDLE and EDGE_TIPS:" in _src and "if HANDLE and HANDLE_TIP:" in _src,
      "the hit tests read the tip alone")
check("and the pick tool binds nothing but the press and the move",
      "DOUBLE_CLICK" not in _src.split("bl_keymap")[1][:500],
      "a DOUBLE_CLICK item changes how the press it shares a button with arrives")
addon.pick.remember_edge_handle(bpy.context, _body)
check("the status names both grips by colour",
      "green grip" in _src and "orange one" in _src,
      "the status does not say which grip does which")
check("and dragging one can cut a chamfer, not only a round",
      r == {'FINISHED'} and props.features[-1].kind == "chamfer" and props.volume < volume,
      "%s | %.0f -> %.0f mm3" % (props.features[-1].kind, volume, props.volume))
# while a drag runs, only the grip it has hold of is drawn: two grips and one
# of them moving is two things happening, and only one of them is
addon.fillet._colour_the_grip("chamfer")
check("a drag in flight leaves only its own grip",
      addon.pick.DRAGGING[0] == "chamfer", str(addon.pick.DRAGGING))
addon.fillet._colour_the_grip(None)
check("and both are back when it is over", addon.pick.DRAGGING == [None],
      str(addon.pick.DRAGGING))
if addon.sync.body().mode == 'EDIT':
    bpy.ops.object.mode_set(mode='OBJECT')

bpy.ops.cadcore.open(filepath=DOC)
volume = props.volume
select_faces("plate/top")
r = bpy.ops.cadcore.place_hole(at=(15.0, 10.0), diameter=6.6)
check("a hole where the click landed", r == {'FINISHED'} and props.volume < volume,
      "%.0f -> %.0f mm3 | %s" % (volume, props.volume, props.status))
volume = props.volume
select_faces("plate/top")
r = bpy.ops.cadcore.drag_fillet(radius=1.5)
check("a fillet at the dragged radius", r == {'FINISHED'} and props.volume != volume,
      "%.0f -> %.0f mm3 | %s" % (volume, props.volume, props.status))
volume = props.volume
select_faces("plate/top")
r = bpy.ops.cadcore.drag_box(at=(-20.0, -10.0), width=12.0, height=8.0, depth=-2.0)
check("a box cut where the rectangle was dragged", r == {'FINISHED'} and props.volume < volume,
      "%.0f -> %.0f mm3 | %s" % (volume, props.volume, props.status))
volume = props.volume
select_faces("plate/top")
r = bpy.ops.cadcore.drag_box(at=(20.0, -10.0), width=10.0, height=10.0, depth=4.0)
check("and a boss where the drag went out", r == {'FINISHED'} and props.volume > volume,
      "%.0f -> %.0f mm3 | %s" % (volume, props.volume, props.status))
select_faces("plate/top")
before = len(props.features)
r = bpy.ops.cadcore.offset_plane(offset=12.0)
check("a work plane dragged off a face", r == {'FINISHED'} and len(props.features) == before + 1,
      "%d -> %d features | %s" % (before, len(props.features), props.status))

# --- a work plane is somewhere to draw, so making one ends in the pen -------
_src = (pathlib.Path(addon.__file__).parent / "viewport" / "plane.py").read_text(encoding="utf-8")
check("a plane dragged off a face hands the pen the plane it made",
      "_draw_on_it(context, plane)" in _src and "draw_sketch" in _src,
      "the plane is made and then has to be found again")
# and the same for the one between two faces, which is where people want it
# far more often than at a named offset from one of them
bpy.ops.cadcore.open(filepath=DOC)
select_faces("plate/top", "plate/bottom")
before = len(props.features)
r = bpy.ops.cadcore.plane_between()
check("a plane halfway between two parallel faces", r == {'FINISHED'}
      and len(props.features) == before + 1 and props.features[-1].kind == "plane",
      "%d -> %d features" % (before, len(props.features)))
_frame = client.call("plane_frame", plane=props.features[-1].name)
_top = client.call("face_frame", face="plate/top")["origin"]
_bottom = client.call("face_frame", face="plate/bottom")["origin"]
check("and it sits halfway, not on either of them",
      all(abs(_frame["origin"][i] - (_top[i] + _bottom[i]) / 2) < 1e-6 for i in range(3)),
      str(_frame["origin"]))
check("and the pen has it, so nobody looks for it again",
      addon.pick.picked_plane() == props.features[-1].name,
      str(addon.pick.picked_plane()))
try:
    client.call("add_plane", between=["plate/top", "plate/left"], offset=0.0)
    _refused = ""
except addon.client.ServerError as _exc:
    _refused = _exc.message
check("two faces that meet at an angle have no halfway, and it says so",
      "parallel" in _refused and "90" in _refused, _refused or "not refused")
check("so the menu only offers it for two parallel faces",
      addon.selection.parallel(bpy.context, ["plate/top", "plate/bottom"])
      and not addon.selection.parallel(bpy.context, ["plate/top", "plate/left"]),
      "")

# and one hung on an edge, turned about it: the plane keeps the edge, so it
# stays touching the part however the face behind it moves
bpy.ops.cadcore.open(filepath=DOC)
_edge = client.call("select_edges", query={"of_face": "plate/top"})["edges"][0]
_turned = client.call("add_plane", face="plate/top", about=_edge, angle=30.0)
_flat = client.call("face_frame", face="plate/top")["normal"]
check("a work plane turns about a picked edge",
      abs(sum(a * b for a, b in zip(_turned["frame"]["normal"], _flat))
          - math.cos(math.radians(30.0))) < 1e-6,
      str([round(c, 3) for c in _turned["frame"]["normal"]]))
check("and hangs on the edge rather than in the middle of the face",
      _turned["frame"]["origin"] != client.call("face_frame", face="plate/top")["origin"],
      str([round(c, 3) for c in _turned["frame"]["origin"]]))
check("its angle is a number on the part, so it can be retyped",
      any(p.name.endswith("_angle") for p in props.parameters)
      or "angle" in str(client.call("describe_document")["features"][-1]),
      str(client.call("describe_document")["features"][-1].get("args")))
try:
    client.call("add_plane", about=_edge, angle=10.0)
    _refused = ""
except addon.client.ServerError as _exc:
    _refused = _exc.message
check("turning about an edge without saying which face is refused",
      "say which face" in _refused, _refused or "not refused")
check("the ring is drawn from the drag, and goes with it",
      addon.pick.RING == [None]
      and "_draw_ring" in (pathlib.Path(addon.__file__).parent / "ui"
                           / "overlay.py").read_text(encoding="utf-8"),
      str(addon.pick.RING))
_src = (pathlib.Path(addon.__file__).parent / "viewport" / "plane.py").read_text(encoding="utf-8")
check("and turning one also ends with the pen on it",
      _src.count("_draw_on_it(context, plane)") >= 2, "one of the three does not draw")

# a sketch drawn on a face is held by the face's name, not by a datum plane
# nobody asked for: the plane is only made when the drawing is somewhere the
# part has no face
bpy.ops.cadcore.open(filepath=DOC)
_planes = len([f for f in props.features if f.kind == "plane"])
select_faces("plate/top")
r = bpy.ops.cadcore.add_profile(profile_w=8.0, profile_h=8.0)
check("drawing on a face makes no work plane",
      r == {'FINISHED'}
      and len([f for f in props.features if f.kind == "plane"]) == _planes,
      "%d work planes before, %d after"
      % (_planes, len([f for f in props.features if f.kind == "plane"])))

# the picked face carries the pencil and the plane beside its arrow, and both
# stop being click targets the moment the arrow does
bpy.ops.cadcore.open(filepath=DOC)
select_faces("plate/top")
addon.pick.remember_handle(bpy.context, "plate/top")
check("a picked flat face gets an arrow to pull and a pencil to draw on it",
      bool(addon.pick.HANDLE) and addon.pick.FACE_HANDLES == ("draw", "plane"),
      str(addon.pick.FACE_HANDLES))
addon.pick.FACE_TIPS["draw"] = (100.0, 100.0, 12.0)
addon.pick.remember_handle(bpy.context, None)
check("and nothing beside a face that is no longer picked",
      not addon.pick.FACE_TIPS and not addon.pick.HANDLE, str(addon.pick.FACE_TIPS))
_src = (pathlib.Path(addon.__file__).parent / "ui" / "overlay.py").read_text(encoding="utf-8")
check("the overlay clears them before it draws them",
      "marks.FACE_TIPS.clear()" in _src.split("def _draw_handle")[1][:300],
      "a handle can outlive the face it belonged to")
check("and each is drawn as the thing it does",
      addon.overlay._pencil((0.0, 0.0), 9.0) != addon.overlay._plane_mark((0.0, 0.0), 9.0),
      "the pencil and the plane are one shape")
# an assembly of two bodies: grab one and move it, then cut one with the other
TWO = os.path.join(REPO, "build", "verify_two.json")
json.dump({"parameters": {"dx": 10}, "features": [
    {"id": "a", "type": "box", "size": [30, 30, 10]},
    {"id": "b", "type": "cylinder", "radius": 6, "height": 20},
    {"id": "tb", "type": "translate", "body": "b", "offset": ["dx", 0, 0]},
    {"id": "asm", "type": "assemble", "bodies": ["a", "tb"]}], "result": "asm"},
    open(TWO, "w", encoding="utf-8"))
bpy.ops.cadcore.open(filepath=TWO)
select_faces("b/side")
r = bpy.ops.cadcore.move_part(offset=(-4.0, 3.0, 0.0))
client = addon.state.get_client(bpy.context)
said = client.call("part_of", face="b/side")
check("a part is moved by its translate", r == {'FINISHED'} and said["offset"] == [6.0, 3.0, 0.0],
      "%s | %s" % (said, props.status))
# --- the bridge admits a connection by token, and nothing else ---------------
# Loopback is every process on the machine, and in WSL or a container more
# than that; the kernel behind the bridge is unfenced. So a connection says
# the token this Blender wrote, or it is over -- and a request's arguments
# cannot be the client's own parameters, nor reach outside the folder.
import socket as _socket                                             # noqa: E402
import threading as _threading                                       # noqa: E402

addon.bridge._token = "verify-token"


def _ask_the_bridge(lines):
    ours, theirs = _socket.socketpair()
    worker = _threading.Thread(target=addon.bridge._talk, args=(theirs,), daemon=True)
    worker.start()
    out = []
    stream = ours.makefile("rw", encoding="utf-8")
    for line in lines:
        try:
            stream.write(line + "\n")
            stream.flush()
            answer = stream.readline()
        except OSError:
            break                        # the bridge closed on us, which is the point
        if not answer:
            break
        out.append(json.loads(answer))
    for closing in (stream.close, ours.close):
        try:
            closing()
        except OSError:
            pass                         # closing a closed pipe flushes into it
    worker.join(timeout=2)
    return out


_said = _ask_the_bridge(['{"op": "describe_document"}'])
check("a connection without the token is refused and closed",
      len(_said) == 1 and _said[0]["ok"] is False and _said[0]["kind"] == "not_admitted",
      str(_said))
_said = _ask_the_bridge(['{"token": "wrong"}', '{"op": "describe_document"}'])
check("and a wrong token too", len(_said) == 1 and _said[0]["kind"] == "not_admitted",
      str(_said))
_said = _ask_the_bridge(['{"token": "verify-token"}'])
check("the right token is admitted", _said and _said[0].get("admitted") is True, str(_said))
addon.bridge._token = None
_src = (pathlib.Path(addon.__file__).parent / "link" / "bridge.py").read_text(encoding="utf-8")
check("and the token is compared in constant time and written 0600",
      "hmac.compare_digest" in _src and "0o600" in _src, "")
check("a request cannot smuggle the client's own parameters",
      "timeout" in addon.bridge.RESERVED
      and "k not in RESERVED" in _src, str(sorted(addon.bridge.RESERVED)))
_out = addon.bridge._outside_the_root(bpy.context, {"path": "/etc/passwd.json"})
check("and a path outside the session's folder is named and refused",
      _out == "/etc/passwd.json", str(_out))
_out = addon.bridge._outside_the_root(bpy.context, {"args": {"path": "../../up.step"}})
check("even nested in a feature's arguments", _out == "../../up.step", str(_out))
_out = addon.bridge._outside_the_root(bpy.context, {"path": "beside.step", "name": "x"})
check("while a file beside the document is fine", _out is None, str(_out))
_reply = addon.bridge._perform({"op": "describe_document", "timeout": 0})
check("and a stripped key does not reach the kernel",
      _reply.get("ok") is True, str(_reply)[:100])
check("an Ask that switched the bridge on gives it back",
      "for_ask=True" in (pathlib.Path(addon.__file__).parent / "link"
                         / "assistant.py").read_text(encoding="utf-8")
      and hasattr(addon.bridge, "release"), "")

# --- Blender's undo follows the kernel by more than luck ----------------------
# after an undo the kernel says what the restored scene says, and every edit
# pushes a step however it was run
bpy.ops.cadcore.open(filepath=DOC)
_client = addon.state.get_client(bpy.context)
_was = _client.call("describe_document")["parameters"]["thickness"]
with addon.state.refresh_guard():
    props.parameters["thickness"].value = _was + 2.0     # the scene says one thing
check("a scene that disagrees with the kernel after an undo wins",
      addon.state._match_the_scene(props) is not None
      and abs(_client.call("describe_document")["parameters"]["thickness"] - (_was + 2.0)) < 1e-9,
      str(_client.call("describe_document")["parameters"]["thickness"]))
check("and one that agrees is left alone", addon.state._match_the_scene(props) is None, "")
_ops = {}
for _path in ("operators/documents.py", "operators/modelling.py", "link/assistant.py"):
    _ops[_path] = (pathlib.Path(addon.__file__).parent / _path).read_text(encoding="utf-8")
for _op in ("remove_feature", "suppress_feature", "move_feature"):
    _body = _ops["operators/documents.py"].split('bl_idname = "cadcore.%s"' % _op)[1].split("\nclass ")[0]
    check("%s pushes a step when no click ran it" % _op, "push_undo(" in _body, "")
for _op in ("add_constraint", "remove_constraint"):
    _body = _ops["operators/modelling.py"].split('bl_idname = "cadcore.%s"' % _op)[1].split("\nclass ")[0]
    check("%s pushes a step when no click ran it" % _op, "push_undo(" in _body, "")
check("and the API assistant's edits are steps, like the bridge's",
      "state.push_undo(" in _ops["link/assistant.py"].split("def pump")[1][:1500], "")
# and the scene remembers *which state* is on screen, not how deep the stack
# is: past 64 steps the depth is 64 before and after an edit
bpy.ops.cadcore.open(filepath=DOC)
_r0 = props.revision
_client = addon.state.get_client(bpy.context)
_client.call("set_parameter", name="thickness", value=9.0)
bpy.ops.cadcore.rebuild()
check("a build stamps the scene with the kernel's revision",
      props.revision != _r0 and props.revision == _client.call("describe_document")["revision"],
      "%s -> %s" % (_r0, props.revision))
with addon.state.refresh_guard():
    props.revision = _r0                        # what Blender's undo would restore
_want, _info = addon.state._walk_kernel(props)
check("and Blender's undo takes the kernel to that revision, not to a depth",
      _want == _r0 and _info is not None and _info.get("moved") is True
      and _client.call("describe_document")["revision"] == _r0
      and _client.call("describe_document")["parameters"]["thickness"] == 8.0,
      str(_client.call("describe_document")["parameters"]["thickness"]))
_want, _info = addon.state._walk_kernel(props)
check("and asks for nothing when it is there already", _info is None, str(_info))
# a finished drag draws end_drag's reply: that is where the number is handed
# out, and a mesh drawn from the edit's reply remembered the one from before
_client.call("begin_drag")
_preview = _client.call("set_parameter", name="thickness", value=9.5)
_kept = _client.call("end_drag", keep=True)
addon.state._apply_build(bpy.context, _kept)
check("the mesh of a finished drag remembers the number the drag ended with",
      _kept["revision"] != _preview["revision"]
      and addon.sync.remembered_revision(addon.sync.body()) == _kept["revision"]
      == _client.call("describe_document")["revision"],
      "%s / %s / %s" % (_preview["revision"], _kept["revision"],
                        addon.sync.remembered_revision(addon.sync.body())))
_src = (pathlib.Path(addon.__file__).parent / "viewport" / "live.py").read_text(encoding="utf-8")
check("and the drag contract draws that reply, not the edit's",
      '_apply_build(context, client.call("end_drag", keep=True))' in _src,
      "the finish draws the preview's reply")
bpy.ops.cadcore.open(filepath=TWO)                  # the two-part assembly, for what follows

# --- an assistant's select reaches every part, and a replace clears the others
_pick = addon.bridge._select(bpy.context, {"faces": ["b/+z"]})
check("an assistant can select a face on the second part",
      _pick["ok"] and _pick["result"]["selected"] == ["b/+z"], str(_pick))
_pick = addon.bridge._select(bpy.context, {"faces": ["a/+z"]})
check("and selecting on the first part clears the second",
      _pick["ok"] and _pick["result"]["selected"] == ["a/+z"], str(_pick))
_pick = addon.bridge._select(bpy.context, {"faces": ["b/+z"], "add": True})
check("and add keeps both, across the two parts",
      _pick["ok"] and sorted(_pick["result"]["selected"]) == ["a/+z", "b/+z"], str(_pick))
_pick = addon.bridge._select(bpy.context, {"faces": ["nowhere/+z"]})
check("and a face that is on no part is refused by name",
      not _pick["ok"] and _pick["kind"] == "unknown_face", str(_pick)[:80])

# --- dragging a part onto another makes a mate, not three numbers -----------
# A translate is right once. A mate is right after the other part changes, so
# a drag that ends against a face makes one; the preview *is* the mate, so
# what is on screen when the button comes up is what is kept.
import mathutils                                                    # noqa: E402


class _Snapper:
    """The part of the drag that decides, without the drag."""

    _snap_at = addon.move_part.CADCORE_OT_move_part._snap_at
    anchor = mathutils.Vector((0.0, 0.0, 0.0))
    face_normal = mathutils.Vector((1.0, 0.0, 0.0))
    reach = 10.0
    _catches = [
        {"name": "b:left", "part": "b", "normal": [-1.0, 0.0, 0.0], "centre": [4.0, 0.0, 0.0]},
        {"name": "b:right", "part": "b", "normal": [1.0, 0.0, 0.0], "centre": [3.0, 0.0, 0.0]},
        {"name": "b:far", "part": "b", "normal": [-1.0, 0.0, 0.0], "centre": [80.0, 0.0, 0.0]},
    ]


_s = _Snapper()
check("a part near a face that looks back at it settles there",
      _s._snap_at([0.0, 0.0, 0.0]) == {"face": "b:left", "to": "b"},
      str(_s._snap_at([0.0, 0.0, 0.0])))
check("and not on one facing the same way, which it cannot sit against",
      all(f["face"] != "b:right" for f in [_s._snap_at([0.0, 0.0, 0.0])]),
      "b:right faces the same way as the face being dragged")
check("and not on one that is simply far away",
      _s._snap_at([60.0, 0.0, 0.0]) is None, str(_s._snap_at([60.0, 0.0, 0.0])))
check("and not on one off to the side of the drag",
      _s._snap_at([0.0, 40.0, 0.0]) is None, str(_s._snap_at([0.0, 40.0, 0.0])))
_mk = addon.move_part.CADCORE_OT_move_part._make_mate
_s.body_id, _s.face, _s.snap = "tb", "b:side", {"face": "a:top", "to": "a"}
_op, _args = _mk(_s, None)
check("letting go there makes a mate of the two faces",
      _op == "add_feature" and _args["type"] == "mate"
      and _args["args"]["faces"] == ["b:side", "a:top"]
      and _args["args"]["move"] == "tb" and _args["args"]["to"] == "a",
      str(_args))
_flat = [f["name"] for f in client.call("describe_faces",
                                        query={"shape": "plane"})["faces"]]
_on_b = next(n for n in _flat if n.startswith("b"))
_on_a = next(n for n in _flat if n.startswith("a"))
r = client.call("add_feature", type=_args["type"], args=dict(
    _args["args"], move="tb", to="a", faces=[_on_b, _on_a]))
check("and the kernel takes it, so the drag's own call is the real one",
      r.get("feature", "").startswith("mate"),
      "%s onto %s -> %s" % (_on_b, _on_a, r.get("feature")))
client.call("undo")
check("the face it would settle against is lit while it would be",
      addon.pick.SNAP == [None]
      and "_draw_snap" in (pathlib.Path(addon.__file__).parent / "ui"
                           / "overlay.py").read_text(encoding="utf-8"),
      str(addon.pick.SNAP))

volume = props.volume
r = bpy.ops.cadcore.pick_boolean(target="a", tool="tb", kind='cut')
check("and cut with the other, the assembly re-pointed",
      r == {'FINISHED'} and props.volume < volume
      and [f.name for f in props.features][-2:] == ["a_cut", "asm"],
      "%.0f -> %.0f mm3 | %s | %s" % (volume, props.volume, [f.name for f in props.features], props.status))
bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples", "assembly.json"))
# one object per part, so a person can switch a part off in the outliner. The
# scope on a face name is the part, and each object holds that part's polygons
# and no others -- while carrying the whole table, so a name read off a polygon
# is the same name whichever object the ray hit
_drawn = addon.sync.bodies()
_table = json.loads(_drawn[0]["cad_face_table"])
_scopes = {n.split(":")[0] for n in _table if ":" in n}
check("an assembly is drawn as one object per part",
      {ob.name for ob in _drawn} == {"cad_body:" + s for s in _scopes} and len(_scopes) > 1,
      str(sorted(ob.name for ob in _drawn)))
check("and each one holds its own part and nothing else",
      all({_table[ob.data.attributes["cad_face"].data[p.index].value].split(":")[0]
           for p in ob.data.polygons} == {ob.name.split(":", 1)[1]} for ob in _drawn),
      str([len(ob.data.polygons) for ob in _drawn]))
check("and switching one off leaves the others drawn",
      not any(ob.hide_viewport for ob in _drawn),
      str([ob.hide_viewport for ob in _drawn]))
_drawn[0].hide_viewport = True
check("one part hidden is one part hidden",
      [ob.hide_viewport for ob in addon.sync.bodies()] == [True] + [False] * (len(_drawn) - 1),
      str([ob.hide_viewport for ob in addon.sync.bodies()]))
_drawn[0].hide_viewport = False
# scopes nest: an assembly whose parts are themselves assemblies is drawn one
# object per innermost part, so a person can switch off a part of a part
_NESTED = os.path.join(REPO, "build", "verify_nested.json")
with open(_NESTED, "w", encoding="utf-8") as _f:
    json.dump({"meta": {"name": "an assembly of an assembly"},
               "features": [
                   {"id": "asm", "type": "part",
                    "document": os.path.join(REPO, "examples", "assembly.json")},
                   {"id": "solo", "type": "part",
                    "document": os.path.join(REPO, "examples", "bracket.json")},
                   {"id": "all", "type": "assemble", "bodies": ["asm", "solo"]}],
               "result": "all"}, _f)
bpy.ops.cadcore.open(filepath=_NESTED)
_deep = sorted(ob.name for ob in addon.sync.bodies())
check("a part of a part is an object of its own",
      _deep == ["cad_body:asm:base", "cad_body:asm:bush", "cad_body:solo"], str(_deep))
_deep_groups = sorted(c.name for c in bpy.data.collections if c.name.startswith("cad:"))
check("and the collections nest with them",
      _deep_groups == ["cad:asm:base", "cad:asm:bush", "cad:solo"], str(_deep_groups))
bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples", "assembly.json"))
_drawn = addon.sync.bodies()
# the wires go with the mesh they belong to, in a collection per part: the eye
# beside the collection is what switches a part off, wires and all
_wires = sorted(o.name for o in bpy.data.objects if o.name.startswith("cad_edges"))
check("the wires are split the same way",
      _wires == sorted("cad_edges:" + s for s in _scopes), str(_wires))
check("and every wire is on the part it belongs to",
      all({n.split(":")[0] for n in json.loads(bpy.data.objects[w]["cad_edge_table"])}
          == {w.split(":", 1)[1]} for w in _wires), str(_wires))
_groups = {c.name: sorted(o.name for o in c.objects) for c in bpy.data.collections
           if c.name.startswith("cad:")}
check("a part's mesh and its wires share one collection",
      _groups == {"cad:" + s: sorted(["cad_body:" + s, "cad_edges:" + s]) for s in _scopes},
      str(_groups))
select_faces("bush:barrel/side")
# an operator that reports an error is, from a script, a RuntimeError with
# the report's words -- which is what a person reads in the header
try:
    r = bpy.ops.cadcore.move_part(offset=(1.0, 0.0, 0.0))
    said = str(r)
except RuntimeError as exc:
    said = str(exc)
check("a part placed by a mate refuses to be dragged, and names the mate",
      "mate 'fitted'" in said, said[:120])

# the snap targets a drag on the top face would offer: the underside, a
# thickness away; the holes that are already there, exactly on their axes
bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples", "bracket.json"))
client = addon.state.get_client(bpy.context)
frame = client.call("face_frame", face="plate/+z")
snapper = addon.live.Snapper(client.call("describe_faces")["faces"], frame, "plate/+z")
thickness = next(p.value for p in props.parameters if p.name == "thickness")
# the underside is split by the bore, so it is plate/-z@0 and plate/-z@1
under = sorted({round(d, 3) for d, name in snapper.planes if name.startswith("plate/-z")})
check("press/pull on the top would snap flush with the underside",
      len(under) == 1 and abs(abs(under[0]) - thickness) < 1e-3,
      "%s vs thickness %.1f" % (under, thickness))
holes = [name for _, name in snapper.points if name.startswith("h")]
check("and a hole would snap onto the four that are there", len(holes) >= 4, holes)
snapped, label = snapper.uv((snapper.points[1][0][0] + 0.4, snapper.points[1][0][1] - 0.3), 0.1)
check("a cursor near a hole lands on it", snapped == snapper.points[1][0] and label == snapper.points[1][1],
      "%s -> %s (%s)" % ((snapper.points[1][0][0] + 0.4, snapper.points[1][0][1] - 0.3), snapped, label))
d, label = snapper.distance(-thickness + 0.3, 0.1)
check("a depth near the underside becomes through",
      label is not None and label.startswith("plate/-z") and abs(d + thickness) < 1e-6,
      "%.2f (%s)" % (d, label))

# Choosing a tool before doing a thing is a mode by another name. What is
# left on the toolbar is the pick and the two that place many things in a
# row; everything else is reached by grabbing what is drawn on the part.
check("the toolbar is the pick and the two that repeat",
      [t.bl_idname for t in addon.toolbar.OBJECT_TOOLS] ==
      ["cadcore.tool_pick", "cadcore.tool_draw", "cadcore.tool_hole"]
      and bpy.types.WorkSpaceTool in addon.toolbar.CADCORE_TOOL_pick.__mro__,
      [t.bl_idname for t in addon.toolbar.TOOLS])
# and every tool that came off it is still reachable, or it is gone, not hidden
_gone = ("cadcore.press_pull", "cadcore.drag_fillet", "cadcore.offset_plane",
         "cadcore.move_part", "cadcore.pick_boolean", "cadcore.drag_box")
_menus = "".join((pathlib.Path(addon.__file__).parent / "ui" / f).read_text(encoding="utf-8")
                 for f in ("menus.py", "panels.py"))
check("and what came off it is still on a menu",
      all(op in _menus for op in _gone),
      str([op for op in _gone if op not in _menus]))
# --- what a click would land on, before it lands ----------------------------
# The hover runs on every mouse move, so it must answer from numbers that are
# already worked out. These are the branches that do: a ray cast needs a
# region and there is none here.
bpy.ops.cadcore.open(filepath=DOC)
select_faces("plate/top")
addon.pick.forget_hover()
addon.pick.remember_handle(bpy.context, "plate/top")
addon.pick.FACE_TIPS["draw"] = (200.0, 200.0, 12.0)
addon.pick.HANDLE_TIP[:] = [400.0, 400.0, 14.0]
addon.overlay.LABELS.append((600.0, 600.0, 40.0, 16.0, "depth"))
addon.pick.SKETCH[0] = "profile"
addon.pick.SKETCH_POINTS[:] = [(800.0, 800.0, "a")]
addon.pick._look(bpy.context, 202, 201)
check("the cursor over a handle says so before the click",
      addon.pick.HOVER.get("what") == "handle"
      and addon.pick.HOVER.get("name") == "draw", str(addon.pick.HOVER))
addon.pick._look(bpy.context, 401, 400)
check("and over the arrow too",
      addon.pick.HOVER.get("name") == "arrow", str(addon.pick.HOVER))
addon.pick._look(bpy.context, 610, 605)
check("a number under the cursor is a number to type over",
      addon.pick.HOVER.get("what") == "label"
      and addon.pick.HOVER.get("name") == "depth", str(addon.pick.HOVER))
addon.pick._look(bpy.context, 801, 800)
check("and a sketch point is the point",
      addon.pick.HOVER.get("what") == "point", str(addon.pick.HOVER))
check("Tab stays Blender's own when nothing is in front of the face",
      addon.pick.step_out() is False and addon.pick.CYCLE == [0],
      str(addon.pick.HOVER.get("what")))
addon.pick.HOVER.update({"what": "edge", "name": "a|b"})
addon.pick.HOVER_AT[0], addon.pick.HOVER_AT[1] = 900, 900
check("and steps the pick out when something is",
      addon.pick.step_out() is True and addon.pick.CYCLE == [1],
      str(addon.pick.CYCLE))
addon.pick.forget_hover()
check("moving the cursor starts again from the smallest thing",
      addon.pick.CYCLE == [0] and not addon.pick.HOVER, str(addon.pick.CYCLE))
addon.overlay.LABELS.clear()
addon.pick.forget_sketch()
_src = (pathlib.Path(addon.__file__).parent / "viewport" / "pick.py").read_text(encoding="utf-8")
_keymap = _src.split("bl_keymap")[1][:600]
check("the pick tool watches the mouse and takes Tab",
      "MOUSEMOVE" in _keymap and "TAB" in _keymap, _keymap[:120])

# --- the number is beside the thing, not only in the header -----------------
class _Event:
    mouse_region_x, mouse_region_y = 300, 250


addon.modal.say(bpy.context, _Event(), "Press/Pull: +2.00 mm", "+2.00 mm")
check("a drag says its value beside the cursor as well as in the header",
      addon.pick.BADGE[0] is not None and addon.pick.BADGE[0][0] == "+2.00 mm"
      and addon.pick.BADGE[0][1] > _Event.mouse_region_x, str(addon.pick.BADGE))
addon.modal.hush(bpy.context)
check("and takes it away when the drag is over",
      addon.pick.BADGE == [None], str(addon.pick.BADGE))


class _Area:
    """A stand-in for context.area: headless there is none, and hush's
    branch that clears the real header never ran here -- it once called
    itself and every drag in a real viewport ended in a RecursionError."""

    def __init__(self):
        self.header, self.redrawn = "unset", 0

    def header_text_set(self, text):
        self.header = text

    def tag_redraw(self):
        self.redrawn += 1


_area = _Area()
addon.modal.hush(type("Ctx", (), {"area": _area})())
check("and clears the real header when there is one",
      _area.header is None and _area.redrawn == 1, "%r / %d" % (_area.header, _area.redrawn))
_drags = ("press_pull", "fillet", "plane", "move_part")
_says = {name: "modal.say" in (pathlib.Path(addon.__file__).parent / "viewport"
                               / ("%s.py" % name)).read_text(encoding="utf-8")
         for name in _drags}
check("and every drag that has a number to show, shows it there",
      all(_says.values()), str([n for n, ok in _says.items() if not ok]))
check("the drag contract clears it however the drag ends",
      (pathlib.Path(addon.__file__).parent / "viewport" / "live.py").read_text(
          encoding="utf-8").count("modal.hush(context)") >= 2,
      "a badge can outlive the drag that put it there")

# --- a drag past what the shape can take is not an error message ------------
# The preview leaves the last shape that built on the screen. Letting go there
# keeps that one, so what you see is what you get, and the badge says in red
# why the cursor has stopped meaning anything.
class _Drag(addon.live._Live):
    pass


_d = _Drag()
_d._value, _d._good, _d._refused = 12.0, 8.3, 12.0
check("a drag past what the shape can take keeps the last that built",
      _d._the_value_that_built() == 8.3, str(_d._the_value_that_built()))
_d._refused = None
check("and keeps the cursor's own value while it builds",
      _d._the_value_that_built() == 12.0, str(_d._the_value_that_built()))
_d._good, _d._refused = None, 12.0
check("and does not invent one when nothing has built yet",
      _d._the_value_that_built() == 12.0, str(_d._the_value_that_built()))
addon.modal.refused(bpy.context, "fillet_failed")
check("the badge says why, where the number is",
      addon.pick.BADGE_REFUSED[0] == "fillet_failed", str(addon.pick.BADGE_REFUSED))
addon.modal.built(bpy.context)
check("and stops saying it the moment it builds again",
      addon.pick.BADGE_REFUSED == [None], str(addon.pick.BADGE_REFUSED))

# --- what the assistant did, drawn on the part ------------------------------
# A transcript line names the operation; it does not say what happened to the
# shape. The picture before and the picture after say that.
bpy.ops.cadcore.open(filepath=DOC)
if addon.sync.body().mode == 'EDIT':
    bpy.ops.object.mode_set(mode='OBJECT')
_before = addon.changes.picture(bpy.context)
check("a picture of the part has every face with its corners",
      set(_before["faces"]) == set(json.loads(addon.sync.body()["cad_face_table"]))
      and all(runs and all(len(run) >= 3 for run in runs)
              for runs in _before["faces"].values()),
      "%d faces" % len(_before["faces"]))
select_faces("plate/top")
bpy.ops.cadcore.hole_face()
if addon.sync.body().mode == 'EDIT':
    bpy.ops.object.mode_set(mode='OBJECT')
addon.changes.settle(bpy.context, _before)
check("a hole shows as the new faces it made, and nothing as moved",
      addon.changes.showing() and addon.changes.SHOWING["added"]
      and all(n.startswith("hole") for n in addon.changes.SHOWING["added"])
      and not addon.changes.SHOWING["moved"],
      "%s | %d moved" % (addon.changes.SHOWING.get("added"),
                         len(addon.changes.SHOWING.get("moved", ()))))
check("and says so in words", "new face" in addon.changes.summary(),
      addon.changes.summary())
_before = addon.changes.picture(bpy.context)
client.call("undo")
bpy.ops.cadcore.rebuild()
if addon.sync.body().mode == 'EDIT':
    bpy.ops.object.mode_set(mode='OBJECT')
addon.changes.settle(bpy.context, _before)
check("taking it away again shows the faces that went, as the shape they had",
      addon.changes.SHOWING.get("gone") and all(len(run) >= 3 for run in
                                                 addon.changes.SHOWING["gone"]),
      "%d runs" % len(addon.changes.SHOWING.get("gone", ())))
_before = addon.changes.picture(bpy.context)
client.call("set_parameter", name="thickness", value=10.0)
bpy.ops.cadcore.rebuild()
if addon.sync.body().mode == 'EDIT':
    bpy.ops.object.mode_set(mode='OBJECT')
addon.changes.settle(bpy.context, _before)
check("a changed number is shown old -> new, and the faces it moved, moved",
      ("thickness", 8.0, 10.0) in addon.changes.SHOWING.get("numbers", [])
      and addon.changes.SHOWING.get("moved"),
      "%s | %d moved" % (addon.changes.SHOWING.get("numbers"),
                         len(addon.changes.SHOWING.get("moved", ()))))
check("and it says so", "thickness 8 -> 10" in addon.changes.summary(),
      addon.changes.summary())
addon.changes.clear()
check("a click puts the answer away", not addon.changes.showing(), "")
_before = addon.changes.picture(bpy.context)
addon.changes.settle(bpy.context, _before)
check("and a step that changed nothing shows nothing", not addon.changes.showing(), "")
_src = (pathlib.Path(addon.__file__).parent / "link" / "bridge.py").read_text(encoding="utf-8")
_src2 = (pathlib.Path(addon.__file__).parent / "link" / "assistant.py").read_text(encoding="utf-8")
check("both ways an assistant reaches the part take the picture",
      "changes.settle" in _src and "changes.settle" in _src2,
      "one of the two assistant paths shows nothing")

# --- a face is the way back to the feature that made it ---------------------
# Clicking a hole's bore, a fillet's face or a boss's top selects the feature
# that made it and puts its numbers on the part, so the History list is where
# you go to see the order, not to find the thing you are already looking at.
bpy.ops.cadcore.open(filepath=DOC)
for _face, _feature in (("plate/top", "plate"), ("rounded/side", "rounded")):
    select_faces(_face)
    addon.pick.show_the_feature_behind(bpy.context, _face)
    _shown = (props.features[props.feature_index].name
              if 0 <= props.feature_index < len(props.features) else None)
    check("clicking %s selects %s, the feature that made it" % (_face, _feature),
          _shown == _feature, str(_shown))
    check("and its numbers are on the part, not in a list to go and find",
          bool([a.name for a in props.feature_args if a.kind == "number"]),
          str([a.name for a in props.feature_args]))
# a bore is the same story: the hole is found by clicking the hole
select_faces("plate/top")
r = bpy.ops.cadcore.hole_face()
_bore = next((n for n in addon.sync.selected_face_names() or [] if "/" in n), None)
_bore = next((n for n in json.loads(addon.sync.body()["cad_face_table"])
              if n.startswith("hole1/")), None)
addon.pick.show_the_feature_behind(bpy.context, _bore or "")
check("and clicking a bore finds the hole that drilled it",
      r == {'FINISHED'} and _bore is not None
      and props.features[props.feature_index].name == "hole1",
      "%s -> %s" % (_bore, props.features[props.feature_index].name))

check("a press held and moved on a part is a part move",
      hasattr(bpy.types, "CADCORE_OT_drag_the_part")
      and "drag_the_part" in (pathlib.Path(addon.__file__).parent / "viewport"
                              / "pick.py").read_text(encoding="utf-8"),
      "the pick does not hand a held press to the part")
# a pick puts the body in edit mode, and Blender keeps one toolbar per mode.
# Without a twin there, picking a face takes every drag tool off the toolbar
# and only the arrow on the face is left.
_edit_ids = {t.bl_idname: t for t in addon.toolbar.EDIT_TOOLS}
check("and every one of them is on the edit-mode toolbar too",
      all(t.bl_idname + "_edit" in _edit_ids for t in addon.toolbar.OBJECT_TOOLS)
      and all(t.bl_context_mode == 'EDIT_MESH' for t in _edit_ids.values()),
      sorted(_edit_ids))
check("and the edit twins run the same operators",
      all(_edit_ids[t.bl_idname + "_edit"].bl_keymap == t.bl_keymap
          for t in addon.toolbar.OBJECT_TOOLS),
      [t.bl_idname for t in addon.toolbar.OBJECT_TOOLS])

# a bend belongs to a blank: the flange is on the edge, by name, so it stays
# there when the blank changes size
bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples", "sheet_bracket.json"))
_before = props.volume
_edges = addon.state.get_client(bpy.context).call(
    "select_edges", query={"of_face": "plate/top"})["edges"]
_flat = next((e for e in _edges if "bore" not in e and "bend" not in e), None)
check("a sheet edge to bend", _flat is not None, str(_edges[:4]))
r = bpy.ops.cadcore.flange_edge(length=12.0) if _flat is None else \
    addon.state.get_client(bpy.context).call("add_flange", edge=_flat, length=12.0)
check("a flange bends up off it", isinstance(r, dict) and r.get("feature", "").startswith("flange"),
      str(r.get("feature") if isinstance(r, dict) else r))
check("and it added material", isinstance(r, dict) and r["volume_mm3"] > _before,
      "%.0f -> %.0f mm3" % (_before, r["volume_mm3"] if isinstance(r, dict) else 0))

# starting a sketch: the tool's own press is what says which face, and an empty
# document gets a plane and the pen in one press
bpy.ops.cadcore.new_document()
check("an empty document has no solid", not props.has_body, props.status)
r = bpy.ops.cadcore.start_drawing(plane='xy')
check("Draw Your Own puts a plane down", any(f.kind == "plane" for f in props.features),
      str([f.kind for f in props.features]))
check("and picks it, so the pen knows where to draw",
      addon.pick.picked_plane() is not None, str(addon.pick.picked_plane()))
_src = (pathlib.Path(addon.__file__).parent / "operators" / "draw_op.py").read_text(encoding="utf-8")
check("and pressing on a face is enough to draw on it",
      "_face_to_act_on(context, event)" in _src,
      "draw_sketch falls back to the face under the cursor")

# picking a face points at the feature that made it: its numbers, and the
# sketch it came from, are then the ones drawn on the part
bpy.ops.cadcore.open(filepath=DOC)
props.feature_index = 0
addon.pick.show_the_feature_behind(bpy.context, "rounded/side")
check("a picked face selects the feature that made it",
      props.features[props.feature_index].name == "rounded",
      props.features[props.feature_index].name)
addon.pick.show_the_feature_behind(bpy.context, "plate/top")
check("and another face selects another", props.features[props.feature_index].name == "plate",
      props.features[props.feature_index].name)
_geom = addon.state.get_client(bpy.context).call("sketch_geometry", sketch="profile")
_cons = addon.state.get_client(bpy.context).call("sketch_constraints", sketch="profile")["constraints"]
check("a sketch is drawn where it lies, not only in the editor",
      hasattr(addon.overlay, "_draw_sketch_shape") and bool(_geom["segments"]),
      "%d segments, %d points" % (len(_geom["segments"]), len(_geom["points"])))
_marks = [c["type"] for c in _cons if c["type"] in addon.overlay.MARKS]
check("and every hold on it has a mark of its own", len(_marks) >= 4,
      str(sorted(set(_marks))))
_missing = sorted({c["type"] for c in _cons} - set(addon.overlay.MARKS) - {"distance"})
check("no constraint is left without one", not _missing, str(_missing))

# a drawn profile is cut in; standing it off is the same profile the other way
bpy.ops.cadcore.open(filepath=DOC)
_drawn2 = addon.state.get_client(bpy.context).call(
    "add_sketch", face="plate/top", points=[[-8, -5], [8, -5], [8, 5], [-8, 5]],
    lines=[[0, 1], [1, 2], [2, 3], [3, 0]], operation="pocket", depth=3)
addon.state._apply_build(bpy.context, _drawn2)
_cut = props.volume
props.feature_index = [f.name for f in props.features].index(_drawn2["feature"])
r = bpy.ops.cadcore.flip_pocket()
check("a pocket can stand off instead", r == {'FINISHED'} and props.volume > _cut,
      "%.0f -> %.0f mm3 | %s" % (_cut, props.volume, props.status))
r = bpy.ops.cadcore.flip_pocket()
check("and go back to being cut in", r == {'FINISHED'} and props.volume == pytest_approx(_cut),
      "%.0f mm3 | %s" % (props.volume, props.status))

# --- requirements: said once, checked on every rebuild ---------------------------
bpy.ops.cadcore.open(filepath=DOC)
check("no requirements to begin with", len(props.requirements) == 0)
r = bpy.ops.cadcore.add_requirement(quantity='bbox_max', compare='<=', value="125", req_id="fits")
check("a requirement from the panel", r == {'FINISHED'} and len(props.requirements) == 1,
      props.status)
check("and it is met", props.requirements[0].ok, "%s now %s" % (props.requirements[0].text,
                                                              props.requirements[0].got))
for p in props.parameters:
    if p.name == "width":
        p.value = 130.0
        break
check("an edit that breaks it turns it red in the same refresh",
      props.requirements[0].ok is False and props.requirements[0].got.startswith("130"),
      "%s now %s" % (props.requirements[0].text, props.requirements[0].got))
check("and the edit itself went through", props.volume > 0 and props.status_is_error is False,
      props.status)
r = bpy.ops.cadcore.remove_requirement(req_id="fits")
check("dropped from the panel", r == {'FINISHED'} and len(props.requirements) == 0, props.status)

# --- the three new buttons, pressed -------------------------------------------
bpy.ops.cadcore.open(filepath=DOC)
body, table, attr = cad()
select_faces("plate/top")
volume = props.volume
r = bpy.ops.cadcore.add_profile(profile_shape='circle', profile_r=6.0)
check("a profile from the panel", r == {'FINISHED'}, props.status)
check("and it is a sketch that can be used",
      any(f.name.startswith("circle") for f in props.features),
      str([f.name for f in props.features]))

# a new document, and the first solid from nothing
r = bpy.ops.cadcore.new_document()
check("a new document opens", r == {'FINISHED'} and len(props.features) == 0, props.status)
r = bpy.ops.cadcore.start(shape='rect', plane='xy', size_x=40.0, size_y=20.0, size_z=10.0)
check("a rectangle extruded off the ground plane", r == {'FINISHED'}, props.status)
check("as plane, sketch and extrude, in one step",
      [f.name for f in props.features] == ["plane1", "rect1", "extrude1"],
      str([f.name for f in props.features]))
check("and it has faces to pick", props.faces == 6, str(props.faces))
# a Blender tool moves a vertex: the kernel's mesh comes back
body = addon.sync.body()
before = tuple(body.data.vertices[0].co)
body.data.vertices[0].co.z += 5.0
check("a moved vertex is noticed", addon.sync.changed_by_hand(body))
addon.state.guard_mesh()
check("and the kernel's mesh is drawn again", tuple(body.data.vertices[0].co) == before
      and not addon.sync.changed_by_hand(body), props.status)
check("and the panel says why", "Bake" in props.status, props.status)
# Bake: the body becomes a plain mesh, the CAD draws a new one next rebuild
r = bpy.ops.cadcore.bake()
check("bake makes a plain Blender mesh", r == {'FINISHED'} and addon.sync.body() is None
      and bpy.data.objects.get("part") is not None and "cad_face" not in bpy.data.objects["part"].data.attributes
      and bpy.data.objects.get("cad_edges") is None, props.status)
r = bpy.ops.cadcore.rebuild()
check("and the CAD draws a fresh cad_body beside it", r == {'FINISHED'} and addon.sync.body() is not None
      and bpy.data.objects.get("part") is not None, props.status)
body = addon.sync.body()
bpy.context.view_layer.objects.active = body
body.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
import bmesh
bm = bmesh.from_edit_mesh(body.data)
layer = bm.faces.layers.int.get("cad_face")
for f in bm.faces:
    f.select = (f[layer] == 0)
bmesh.update_edit_mesh(body.data)
from cadcore_bridge import sync as _sync
picked = _sync.selected_face_names(body)
check("a face picked in edit mode is read by name", len(picked) == 1, str(picked))
bpy.ops.object.mode_set(mode='OBJECT')
props.feature_index = [f.name for f in props.features].index("extrude1")
r = bpy.ops.cadcore.remove_feature()
check("removing the extrude leaves the sketch on screen",
      r == {'FINISHED'} and len(addon.sync.body().data.polygons) == 0
      and len(bpy.data.objects["cad_edges"].data.edges) >= 8, props.status)
wire = json.loads(bpy.data.objects["cad_edges"]["cad_edge_table"])
check("as the sketch's own segments and the plane", any(n.startswith("rect1/") for n in wire)
      and "plane1" in wire, str(wire))
r = bpy.ops.cadcore.undo(redo=False)
check("and undoing the removal brings the solid back", r == {'FINISHED'}
      and len(addon.sync.body().data.polygons) == 12, props.status)
try:                                     # a refusal is an exception headless
    r = bpy.ops.cadcore.start(shape='box')
except RuntimeError as exc:
    r = str(exc)
check("a second start is refused while there is a solid", "solid already" in str(r), str(r)[:80])
# take the solid and its sketch away: the plane stays, and Start is back
for fid in ("extrude1", "rect1"):
    props.feature_index = [f.name for f in props.features].index(fid)
    bpy.ops.cadcore.remove_feature()
check("with only a plane left there is no body", not props.has_body and [f.name for f in props.features] == ["plane1"],
      str([f.name for f in props.features]))
r = bpy.ops.cadcore.start(shape='circle', radius=8.0, size_z=5.0)
check("starting again draws on the plane that is there", r == {'FINISHED'}
      and [f.name for f in props.features] == ["plane1", "circle1", "extrude1"] and props.has_body,
      str([f.name for f in props.features]))
bpy.ops.cadcore.open(filepath=DOC)

# the Ask box, with a stand-in for the model: it asks for a box, then a fillet
# on an edge that is not there, then says it is done
from cadcore_bridge.link import assistant as _assistant
bpy.ops.cadcore.new_document()
_script = iter([
    {"stop_reason": "tool_use", "content": [
        {"type": "text", "text": "Making the block."},
        {"type": "tool_use", "id": "t1", "name": "add_feature",
         "input": {"type": "box", "args": {"size": [30, 20, 10]}}}]},
    {"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "t2", "name": "add_fillet",
         "input": {"edges": ["nowhere/+z|nowhere/+x"], "radius": 2}}]},
    {"stop_reason": "end_turn", "content": [
        {"type": "text", "text": "The block is there; the edge you named does not exist."}]},
])
_seen = []
def _fake(payload):
    _seen.append(payload)
    return next(_script)
why = _assistant.ask(bpy.context, "a 30 x 20 x 10 block", send=_fake)
check("an Ask starts", why is None, str(why))
_assistant.pump_until_done(bpy.context)
check("the question carries what is on screen",
      _seen[0]["messages"][0]["content"].startswith("[From Blender]")
      and "a 30 x 20 x 10 block" in _seen[0]["messages"][0]["content"]
      and "No solid yet" in _seen[0]["messages"][0]["content"], _seen[0]["messages"][0]["content"][:120])
check("the model got the tools and the guide",
      len(_seen) == 3 and any(t["name"] == "add_fillet" for t in _seen[0]["tools"])
      and "parametric CAD kernel" in _seen[0]["system"], str(len(_seen)))
check("its first call made the block", [f.name for f in props.features] == ["box1"] and props.faces == 6,
      str([f.name for f in props.features]))
kinds = [l.kind for l in props.transcript]
check("a refused call went back to the model as an error",
      "refused" in kinds and [m for m in _seen[2]["messages"] if m["role"] == "user"][-1]["content"][0]["is_error"],
      str(kinds))
check("and the panel shows what was said and done",
      kinds[0] == "said" and kinds[-1] == "said" and "did" in kinds, str(kinds))
_prefs = addon.state.prefs(bpy.context)
_was = _prefs.assistant_backend
_prefs.assistant_backend = 'api'
check("without a key there is a plain reason", "API key" in (_assistant.ask(bpy.context, "anything") or ""))
_prefs.assistant_backend = _was
props.ask = ""
_long = bpy.data.texts.new("Ask")
_long.write("a plate,\nwith four holes,\nrounded corners")
check("a long question in the Ask text makes Ask pressable", bpy.ops.cadcore.ask.poll())
bpy.data.texts.remove(_long)
_again = iter([{"stop_reason": "end_turn", "content": [{"type": "text", "text": "Sure."}]}])
_seen2 = []
def _fake2(payload):
    _seen2.append(payload)
    return next(_again)
_assistant.ask(bpy.context, "and round the corners", send=_fake2)
_assistant.pump_until_done(bpy.context)
check("the next question continues the conversation",
      len(_seen2) == 1 and len(_seen2[0]["messages"]) > 2 and _seen2[0]["messages"][0]["content"].endswith("a 30 x 20 x 10 block"),
      str(len(_seen2[0]["messages"]) if _seen2 else 0))
bpy.ops.cadcore.ask_clear()
check("and Clear forgets it", len(props.transcript) == 0)
# the CLI backends, with stand-ins that print what claude -p and codex exec print
_fake_dir = os.path.join(REPO, "build")
_claude_fake = os.path.join(_fake_dir, "fake_claude.py")
with open(_claude_fake, "w", encoding="utf-8") as f:
    f.write("""import json
for e in [
  {"type": "system", "subtype": "init", "session_id": "s-123"},
  {"type": "assistant", "message": {"content": [{"type": "text", "text": "Making the block."},
      {"type": "tool_use", "id": "t1", "name": "mcp__cadcore__add_feature", "input": {"type": "box", "args": {"size": [1, 2, 3]}}}]}},
  {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "unresolved_reference: no such face"}]}},
  {"type": "assistant", "message": {"content": [{"type": "text", "text": "That face is not there; done otherwise."}]}},
  {"type": "result", "subtype": "success", "session_id": "s-123", "result": "done", "is_error": False}]:
    print(json.dumps(e))
""")
_codex_fake = os.path.join(_fake_dir, "fake_codex.py")
with open(_codex_fake, "w", encoding="utf-8") as f:
    f.write("""import json
for e in [
  {"type": "thread.started", "thread_id": "th-9"},
  {"type": "item.started", "item": {"type": "mcp_tool_call", "server": "cadcore", "tool": "add_feature", "arguments": {"type": "box"}}},
  {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "cadcore", "tool": "add_feature", "arguments": {"type": "box"}, "status": "completed"}},
  {"type": "item.completed", "item": {"type": "agent_message", "text": "The block is there."}},
  {"type": "turn.completed"}]:
    print(json.dumps(e))
""")
for _kind, _script, _sid in (("claude", _claude_fake, "s-123"), ("codex", _codex_fake, "th-9")):
    _cli = _assistant.CliRun("a block", [sys.executable, _script], _kind)
    _cli.start()
    import time as _time
    for _ in range(200):
        if _cli.done:
            break
        _time.sleep(0.05)
    _kinds = [k for k, _ in _cli.lines]
    check("%s output becomes a transcript" % _kind, _cli.done and _cli.error is None and "said" in _kinds and
          ("did" in _kinds or "refused" in _kinds), str(_cli.lines))
    check("and its session id is kept for the next question", _cli.session == _sid, str(_cli.session))
_bad = _assistant.CliRun("x", [sys.executable, "-c", "import sys; sys.exit(3)"], "claude")
_bad.start()
for _ in range(200):
    if _bad.done:
        break
    _time.sleep(0.05)
check("a CLI that fails says so", _bad.done and _bad.error is not None and "code 3" in _bad.error, str(_bad.error))
# the CLI backends are sent the situation too, not only the words typed
_taken = {}
_real_cli, _real_cmd = _assistant.CliRun, _assistant._cli_command


class _Spy(_real_cli):
    def __init__(self, question, command, kind, asked=""):
        _real_cli.__init__(self, question, command, kind, asked)
        _taken["asked"], _taken["shown"] = self.asked, self.question

    def start(self):
        self.done = True


_assistant.CliRun = _Spy
_assistant._cli_command = lambda context, kind: ([sys.executable, "-c", "pass"], None)
_prefs.assistant_backend, _prefs.assistant_stages = 'claude', True
_assistant.ask(bpy.context, "round the corners")
_assistant.CliRun, _assistant._cli_command = _real_cli, _real_cmd
_prefs.assistant_backend = _was
check("the CLI is sent what is on screen, not only the words typed",
      _taken.get("asked", "").startswith("[From Blender]")
      and "Work in stages" in _taken.get("asked", "")
      and _taken.get("shown") == "round the corners", _taken.get("asked", "")[:120])
_assistant.forget()
bpy.ops.cadcore.open(filepath=DOC)

# a drawn sketch's dimensions have a place on the model
from cadcore_bridge.ui import overlay as _overlay
_client = addon.state.get_client(bpy.context)
_drawn = _client.call("add_sketch", face="plate/top", points=[[-10, -5], [10, -5], [10, 5], [-10, 5]],
                      lines=[[0, 1], [1, 2], [2, 3], [3, 0]], operation="pocket", depth=2.0)
addon.state._apply_build(bpy.context, _drawn)
_sketch = _drawn["feature"] + "_profile"
_geometry = _client.call("sketch_geometry", sketch=_sketch)
_constraints = _client.call("sketch_constraints", sketch=_sketch)["constraints"]
_values = {p.name: p.value for p in props.parameters}
_labels = _overlay.sketch_labels(_geometry, _constraints, _values)
check("a drawn rectangle carries two dimensions on the model", len(_labels) == 2, str([l["text"] for l in _labels]))
check("each sits between the points it measures, on the plane",
      all(abs(l["at"][i] - (l["ends"][0][i] + l["ends"][1][i]) / 2) < 1e-6 for l in _labels for i in range(3))
      and all(l["name"] in _values for l in _labels), str(_labels[:1]))
props.feature_index = [f.name for f in props.features].index(_sketch)
check("the sketch is found from the feature made from it",
      _overlay._sketch_for(props) == _sketch, str(_overlay._sketch_for(props)))
props.feature_index = [f.name for f in props.features].index(_drawn["feature"])
check("and from the pocket it was drawn for", _overlay._sketch_for(props) == _sketch,
      str(_overlay._sketch_for(props)))

# the gallery: every shipped example has a picture, and opens as a copy
from cadcore_bridge.ui import gallery as _gallery
_names = [i[0] for i in _gallery.items(None, bpy.context)]
# the gallery is a chosen few, not every shipped example: the rest are what
# the tests build. Each one it does offer must have a picture and a document.
check("the gallery offers what it says it offers",
      _names == list(_gallery.ABOUT), "%s vs %s" % (_names, list(_gallery.ABOUT)))
check("and every one of them is a document that is shipped",
      all(os.path.exists(os.path.join(_gallery.folder(), n + ".json")) for n in _names),
      str(_names))
props.example = "cup"
r = bpy.ops.cadcore.start_example()
check("an example opens as a copy", r == {'FINISHED'} and props.has_document and props.doc_path == ""
      and not props.saved and props.faces > 0, props.status)
check("and the example file itself is untouched",
      not os.path.exists(os.path.join(REPO, "examples", "cup.autosave.json")))
bpy.ops.cadcore.open(filepath=DOC)

# the way in from object mode: Pick puts the body in edit mode, faces or edges
if bpy.context.active_object is not None and bpy.context.active_object.mode == 'EDIT':
    bpy.ops.object.mode_set(mode='OBJECT')
r = bpy.ops.cadcore.pick(what='EDGE')
check("Pick goes to edit mode with the right select mode",
      r == {'FINISHED'} and addon.sync.body().mode == 'EDIT'
      and tuple(bpy.context.tool_settings.mesh_select_mode) == (False, True, False), str(addon.sync.body().mode))
# there is one pick tool per mode, and only the active one binds a click. In
# object mode with the edit-mode tool never handed over, the arrow on a picked
# face cannot be grabbed and the right-click menu is the only way left.
_tool = None
for _area in bpy.context.screen.areas:
    if _area.type == 'VIEW_3D':
        _region = next((rg for rg in _area.regions if rg.type == 'WINDOW'), None)
        with bpy.context.temp_override(area=_area, region=_region):
            _tool = bpy.context.workspace.tools.from_space_view3d_mode(
                bpy.context.mode, create=False)
        break
check("and the pick tool follows it into that mode",
      _tool is not None and _tool.idname == "cadcore.tool_pick_edit",
      _tool.idname if _tool is not None else "no tool")
# the click that picks a face enters edit mode too, and it is the tool in
# hand that has to arrive there: `carry_the_tool` is what a mode change runs
check("a tool in hand is the same tool after a mode change",
      addon.state.twin("cadcore.tool_press_pull", 'EDIT_MESH') == "cadcore.tool_press_pull_edit"
      and addon.state.twin("cadcore.tool_press_pull_edit", 'OBJECT') == "cadcore.tool_press_pull"
      and addon.state.twin("builtin.select_box", 'EDIT_MESH') == "cadcore.tool_pick_edit",
      addon.state.twin("builtin.select_box", 'EDIT_MESH'))
bpy.ops.object.mode_set(mode='OBJECT')

# two faces on two parts, mated by the pick alone
r = bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples", "assembly.json"))
_faces = addon.state.get_client(bpy.context).call("describe_faces")["faces"]
_flat = [f["name"] for f in _faces if f.get("shape") == "plane" and ":" in f["name"]]
_pair = next(([a, b] for a in _flat for b in _flat if a.split(":")[0] != b.split(":")[0]), None)
check("the assembly has flat faces on two parts", _pair is not None, str(_flat[:6]))
_body = addon.sync.body()
bpy.ops.object.mode_set(mode='OBJECT') if _body.mode == 'EDIT' else None
addon.sync.select_faces(_pair)
check("Mate is offered for two faces of two parts", bpy.ops.cadcore.mate_faces.poll())
try:
    r = bpy.ops.cadcore.mate_faces()
    _mated = r == {'FINISHED'} and any(f.kind == "mate" for f in props.features)
    _why = props.status
except RuntimeError as exc:                     # a typed refusal is fine; a traceback is not
    _mated, _why = "Traceback" not in str(exc) and "internal_error" not in str(exc), str(exc)[:120]
check("and mating them is a feature, or a refusal by kind", _mated, _why)
bpy.ops.cadcore.open(filepath=DOC)

r = bpy.ops.cadcore.checkpoint(name="before")
check("a checkpoint from the panel", r == {'FINISHED'} and "kept" in props.status,
      props.status)
kept = props.volume
select_faces("plate/top")
bpy.ops.cadcore.hole_face()
r = bpy.ops.cadcore.restore(name="before")
check("and going back to it", r == {'FINISHED'} and abs(props.volume - kept) < 1e-6,
      "%.0f -> %.0f mm3 (%s)" % (kept, props.volume, props.status))
# a checkpoint is a shape of the document that made it. Restoring it into
# another document put A's shape under B's path, and Save then wrote A over B.
bpy.ops.cadcore.open(filepath=os.path.join(REPO, "examples", "bracket.json"))
try:
    r = bpy.ops.cadcore.restore(name="before")
    _said = str(r)
except RuntimeError as exc:
    _said = str(exc)
check("and it does not follow into the next document opened",
      "no checkpoint" in _said and props.volume != kept, _said[:80])

print("VERIFY RESULT", "all ok" if not fails else "FAILED: " + ", ".join(fails))
sys.exit(0 if not fails else 1)

