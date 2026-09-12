"""Drive the add-on at random, the way a person would, and fail on any crash.

    blender -b -noaudio --factory-startup -P tools/verify/soak_addon.py -- [steps] [seed]

A refusal with a kind is counted; a traceback, an untyped kernel error, or a
scene that disagrees with the panel is a failure, and the seed reproduces it.
"""
import os
import random
import re
import sys
import traceback

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
STEPS = int(argv[0]) if argv else 60
SEED = int(argv[1]) if len(argv) > 1 else 1
rng = random.Random(SEED)
os.makedirs(os.path.join(REPO, "build"), exist_ok=True)
open(os.path.join(REPO, "build", "soak_live.txt"), "w", encoding="utf-8").close()

bpy.ops.preferences.addon_enable(module="cadcore_bridge")
import cadcore_bridge as addon                                      # noqa: E402

props = bpy.context.scene.cadcore
failures: list = []
refusals: dict = {}
log: list = []

#: what a refusal from the kernel looks like when Blender turns it into an
#: exception headless: "kind: message"
REFUSAL = re.compile(r"^(Error: )?[a-z_]+: ")


def body():
    return addon.sync.body()


def to_object_mode():
    ob = body()
    if ob is not None and ob.mode == 'EDIT':
        bpy.ops.object.mode_set(mode='OBJECT')


def pick(faces=0, edges=0):
    """Select a few faces or edges on the body, in edit mode, like a person."""
    import bmesh

    to_object_mode()
    ob = body()
    if ob is None or not len(ob.data.polygons):
        return False
    for other in bpy.context.view_layer.objects:
        other.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(ob.data)
    layer = bm.faces.layers.int.get("cad_face")
    for f in bm.faces:
        f.select = False
    for e in bm.edges:
        e.select = False
    if faces:
        wanted = set(rng.sample(sorted({f[layer] for f in bm.faces}), k=min(faces, len({f[layer] for f in bm.faces}))))
        for f in bm.faces:
            f.select = f[layer] in wanted
    if edges:
        rims = [e for e in bm.edges if e.is_boundary]
        for e in rng.sample(rims, k=min(edges, len(rims))):
            e.select = True
    bmesh.update_edit_mesh(ob.data)
    return True


def pick_and_keep_picking(faces: int = 1):
    """Pick, let a rebuild land while still in edit mode, and check the pick survived."""
    if not pick(faces=faces):
        return False
    ob = body()
    before = sorted(addon.sync.selected_face_names(ob))
    if len(props.parameters):
        item = props.parameters[rng.randrange(len(props.parameters))]
        item.value = round(item.value * rng.uniform(0.8, 1.2), 3) if item.value else 5.0
    else:
        bpy.ops.cadcore.rebuild()
    addon.throttle.flush()
    after = sorted(addon.sync.selected_face_names(body()))
    if ob.mode != 'EDIT' or before != after:
        raise AssertionError("the pick did not survive the rebuild: %s -> %s (mode %s)"
                             % (before, after, ob.mode))
    return True


LIVE = os.path.join(REPO, "build", "soak_live.txt")     # one line per step, written at once


def note(text: str) -> None:
    """Blender holds stdout until it exits; a hung step would leave no trace."""
    import time

    with open(LIVE, "a", encoding="utf-8") as file:
        file.write("%s %s\n" % (time.strftime("%H:%M:%S"), text))


def run(label, fn):
    """Run one step; sort what comes back into ok / refused / failed."""
    note("begin " + label)
    try:
        result = fn()
    except RuntimeError as exc:
        text = str(exc).strip()
        if "Traceback" in text or "internal_error" in text or not REFUSAL.search(text):
            failures.append((label, text[:3000]))
            print("SOAK %-32s FAILED %s" % (label, text[:160]))
        else:
            kind = REFUSAL.search(text).group(0).replace("Error: ", "").rstrip(": ")
            refusals[kind] = refusals.get(kind, 0) + 1
            print("SOAK %-32s refused %s" % (label, kind))
        return
    except Exception as exc:                                        # noqa: BLE001
        failures.append((label, "%s: %s" % (type(exc).__name__, exc)))
        print("SOAK %-32s FAILED %s: %s" % (label, type(exc).__name__, exc))
        traceback.print_exc()
        return
    print("SOAK %-32s %s" % (label, result))
    if label.split(" ", 1)[-1].startswith("checkpoint") and result == {'FINISHED'}:
        kept.append("soak%d" % int(label.split()[0]))
    if "internal_error" in (props.status or "") or "Traceback" in (props.status or ""):
        failures.append((label, "status: " + props.status[:200]))
        print("SOAK %-32s FAILED status %s" % (label, props.status[:160]))
    consistent()


def consistent():
    """The scene and the panel say the same thing."""
    ob = body()
    if props.has_body:
        if ob is None or not len(ob.data.polygons):
            failures.append(("consistency", "panel says there is a body, the scene has none"))
        elif ob.get("cad_face_table") is None:
            failures.append(("consistency", "the body has no face table"))
    elif ob is not None and len(ob.data.polygons):
        failures.append(("consistency", "panel says no body, the scene shows one"))


def at(fid):
    props.feature_index = [f.name for f in props.features].index(fid)


kept: list = []                                   # checkpoint names taken so far


def open_example(name):
    """Open a copy of a shipped example, so the walk never writes into examples/."""
    import shutil

    src = os.path.join(REPO, "examples", name + ".json")
    dst = os.path.join(REPO, "build", "soak_%s.json" % name)
    shutil.copy(src, dst)
    side = dst.replace(".json", ".autosave.json")
    if os.path.exists(side):
        os.remove(side)
    return bpy.ops.cadcore.open(filepath=dst)


def open_step():
    """Open somebody else's STEP file: the part a buyer already has."""
    import shutil

    src = os.path.join(REPO, "cadcore", "geometry", "tests", "data", "foreign.step")
    dst = os.path.join(REPO, "build", "soak_foreign.step")
    shutil.copy(src, dst)
    for stale in (dst.replace(".step", ".json"), dst.replace(".step", ".autosave.json")):
        if os.path.exists(stale):
            os.remove(stale)
    return bpy.ops.cadcore.open(filepath=dst)


def hold_on_a_sketch():
    """Pick points or lines of a sketch the way the overlay lets a person, and
    add a hold that fits them. The pick lists are what the pick tool fills."""
    sketches = [f.name for f in props.features if f.kind == "sketch"]
    if not sketches:
        return {'CANCELLED'}
    name = rng.choice(sketches)
    try:
        geometry = addon.state.get_client(bpy.context).call("sketch_geometry", sketch=name)
    except Exception:                                            # noqa: BLE001
        return {'CANCELLED'}                    # not built: nothing is on screen either
    what = rng.choice(["point", "line"])
    pool = (sorted(geometry["points"]) if what == "point"
            else [s["name"] for s in geometry["segments"]])
    if len(pool) < 2:
        return {'CANCELLED'}
    picks = [(what, n) for n in rng.sample(pool, rng.randint(1, 2))]
    addon.pick.SKETCH[0] = name
    addon.pick.PICKED_SKETCH[:] = picks
    addon.pick.PICKED_ON[0] = name
    fits = addon.holding.fitting(picks)
    if not fits:
        return {'CANCELLED'}
    to_object_mode()
    return bpy.ops.cadcore.hold_sketch(kind=rng.choice(fits))


def a_step(step):
    features = [f.name for f in props.features]
    moves = []
    if not props.has_document:
        return run("new_document", lambda: bpy.ops.cadcore.new_document())
    if not props.has_body:
        shape = rng.choice(['box', 'cylinder', 'rect', 'circle'])
        moves.append(("start " + shape, lambda: (to_object_mode(), bpy.ops.cadcore.start(
            shape=shape, plane=rng.choice(['xy', 'xz', 'yz']),
            size_x=round(rng.uniform(10, 60), 1), size_y=round(rng.uniform(10, 40), 1),
            size_z=round(rng.uniform(4, 30), 1), radius=round(rng.uniform(4, 20), 1)))[1]))
        if rng.random() < 0.2:
            moves.append(("new_document", lambda: (to_object_mode(), bpy.ops.cadcore.new_document())[1]))
    else:
        kind = rng.choice(["fillet", "chamfer"])
        moves += [
            ("fillet on edges", lambda: (pick(edges=rng.randint(1, 2)), bpy.ops.cadcore.fillet_selected(
                radius=round(rng.uniform(0.5, 4), 2), kind=kind))[1]),
            ("fillet on faces", lambda: (pick(faces=rng.randint(1, 2)), bpy.ops.cadcore.fillet_selected(
                radius=round(rng.uniform(0.5, 3), 2), kind=kind))[1]),
            ("pocket", lambda: (pick(faces=1), bpy.ops.cadcore.pocket_face(
                pocket_depth=round(rng.uniform(1, 6), 1)))[1]),
            ("hole", lambda: (pick(faces=1), bpy.ops.cadcore.hole_face())[1]),
            ("fillet after a rebuild in edit mode", lambda: (
                pick_and_keep_picking(faces=1), bpy.ops.cadcore.fillet_selected(
                    radius=round(rng.uniform(0.5, 3), 2), kind=kind))[1]),
            ("shell", lambda: (pick(faces=1), bpy.ops.cadcore.shell(
                shell_thickness=round(rng.uniform(1, 3), 1)))[1]),
            ("draft", lambda: (pick(faces=2), bpy.ops.cadcore.draft(angle=round(rng.uniform(2, 15), 1)))[1]),
            ("work plane", lambda: (pick(faces=1), bpy.ops.cadcore.add_plane(offset=round(rng.uniform(2, 20), 1)))[1]),
            ("cut the view", lambda: (pick(faces=1), bpy.ops.cadcore.section_view(
                offset=round(rng.uniform(-20, 20), 1), flip=rng.random() < 0.5))[1]),
            ("show the whole part", lambda: bpy.ops.cadcore.section_clear()),
            ("move face", lambda: (pick(faces=1), bpy.ops.cadcore.move_face(
                move_distance=round(rng.uniform(-5, 8), 1)))[1]),
            ("profile on face", lambda: (pick(faces=1), bpy.ops.cadcore.add_profile(
                profile_shape=rng.choice(['rect', 'circle', 'slot', 'polygon'])))[1]),
            ("undo", lambda: (to_object_mode(), bpy.ops.cadcore.undo(redo=False))[1]),
            ("redo", lambda: (to_object_mode(), bpy.ops.cadcore.undo(redo=True))[1]),
            ("rebuild", lambda: (to_object_mode(), bpy.ops.cadcore.rebuild())[1]),
            ("check", lambda: (to_object_mode(), bpy.ops.cadcore.check())[1]),
        ]
        if any(f.kind == "sketch" for f in props.features):
            moves.append(("hold on a sketch", hold_on_a_sketch))
        if features:
            target = rng.choice(features)
            moves += [
                ("roll back to " + target, lambda: (to_object_mode(), at(target), bpy.ops.cadcore.rollback())[2]),
                ("roll forward", lambda: (to_object_mode(), bpy.ops.cadcore.rollback(clear=True))[1]),
                ("remove " + target, lambda: (to_object_mode(), at(target), bpy.ops.cadcore.remove_feature())[2]),
                ("suppress " + target, lambda: (to_object_mode(), at(target), bpy.ops.cadcore.suppress_feature())[2]),
            ]
        # dials: a parameter in the sidebar, a number on the selected feature
        if len(props.parameters):
            item = props.parameters[rng.randrange(len(props.parameters))]
            factor = rng.uniform(0.5, 1.6)

            def turn_parameter(item=item, factor=factor):
                item.value = round(item.value * factor, 3) if item.value else round(rng.uniform(1, 20), 3)
                addon.throttle.flush()
                return "parameter %s = %s" % (item.name, item.value)
            moves.append(("parameter " + item.name, turn_parameter))
        if features:
            def turn_argument(target=rng.choice(features), factor=rng.uniform(0.5, 1.6)):
                to_object_mode()
                props.feature_index = [f.name for f in props.features].index(target)
                numbers = [a for a in props.feature_args if a.value]
                if not numbers:
                    return "no number to turn on " + target
                arg = rng.choice(numbers)
                arg.value = round(arg.value * factor, 3)
                return "%s.%s = %s" % (target, arg.name, arg.value)
            moves.append(("turn a number", turn_argument))
            moves.append(("move feature", lambda: (to_object_mode(), at(rng.choice(features)),
                                                   bpy.ops.cadcore.move_feature(direction=rng.choice([-1, 1])))[2]))
        moves += [
            ("thread", lambda: (pick(faces=1), bpy.ops.cadcore.thread_face())[1]),
            ("mirror", lambda: (pick(faces=1), bpy.ops.cadcore.mirror())[1]),
            ("pattern", lambda: (pick(faces=1), bpy.ops.cadcore.pattern(
                count=rng.randint(2, 8)))[1]),
            ("delete faces", lambda: (pick(faces=1), bpy.ops.cadcore.delete_faces())[1]),
            ("split here", lambda: (pick(faces=1), bpy.ops.cadcore.split_here(
                offset=round(rng.uniform(-8, -1), 1)))[1]),
            ("emboss text", lambda: (pick(faces=1), bpy.ops.cadcore.emboss_text(
                text=rng.choice(["OK", "A1", "RP"]), depth=round(rng.uniform(0.5, 2), 1),
                cut=rng.random() < 0.5))[1]),
            ("draft from here", lambda: (pick(faces=1), bpy.ops.cadcore.draft_from_here(
                angle=round(rng.uniform(1, 8), 1)))[1]),
            ("coil round", lambda: (pick(faces=1), bpy.ops.cadcore.coil_round(
                pitch=round(rng.uniform(1, 6), 1)))[1]),
            ("draft check", lambda: (pick(faces=1), bpy.ops.cadcore.draft_check())[1]),
            ("mass", lambda: (to_object_mode(), bpy.ops.cadcore.mass())[1]),
            ("distance between", lambda: (pick(faces=2), bpy.ops.cadcore.measure(kind='distance'))[1]),
            ("add requirement", lambda: (to_object_mode(), bpy.ops.cadcore.add_requirement(
                quantity=rng.choice(['mass_g', 'bbox_max', 'volume_mm3', 'faces']),
                compare=rng.choice(['<=', '>=']), value=str(round(rng.uniform(1, 500), 1))))[1]),
            ("checkpoint", lambda: (to_object_mode(), bpy.ops.cadcore.checkpoint(name="soak%d" % step))[1]),
            ("export step", lambda: (to_object_mode(), bpy.ops.cadcore.export_step(
                filepath=os.path.join(REPO, "build", "soak.step")))[1]),
            ("drawing", lambda: (to_object_mode(), bpy.ops.cadcore.drawing(
                filepath=os.path.join(REPO, "build", "soak.svg")))[1]),
        ]
        if len(props.requirements):
            req = props.requirements[rng.randrange(len(props.requirements))].name
            moves.append(("remove requirement", lambda: (to_object_mode(),
                                                          bpy.ops.cadcore.remove_requirement(req_id=req))[1]))
        if kept:
            moves.append(("restore", lambda: (to_object_mode(), bpy.ops.cadcore.restore(name=rng.choice(kept)))[1]))
        if rng.random() < 0.03:
            moves.append(("bake", lambda: (to_object_mode(), bpy.ops.cadcore.bake())[1]))
        if rng.random() < 0.05:
            example = rng.choice(["bracket", "cup", "flange", "sketch_plate", "cast_cover", "bottle"])
            moves.append(("open " + example, lambda: (to_object_mode(), open_example(example))[1]))
        if rng.random() < 0.03:
            moves.append(("open a STEP file", lambda: (to_object_mode(), open_step())[1]))
        if rng.random() < 0.05:
            moves.append(("new_document", lambda: (to_object_mode(), bpy.ops.cadcore.new_document())[1]))
    label, fn = rng.choice(moves)
    log.append(label)
    run("%3d %s" % (step, label), fn)


for step in range(STEPS):
    a_step(step)
to_object_mode()

print("SOAK refusals:", dict(sorted(refusals.items())))
if failures:
    print("SOAK RESULT FAILED (seed %d): %d failure(s)" % (SEED, len(failures)))
    for label, why in failures:
        print("   ", label, "--", why)
    print("SOAK path:", " > ".join(log))
    sys.exit(1)
print("SOAK RESULT all ok (seed %d, %d steps)" % (SEED, STEPS))
