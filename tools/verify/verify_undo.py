"""Blender's own undo, and the kernel following it; needs a window, since `ed.undo` polls for a screen.

    blender --window-geometry 0 0 900 700 -P tools/verify/verify_undo.py

Checks that an operator pushes an undo step, that undo takes the kernel
back with it, and that running the operator again replaces the edit (F9).
"""
import json
import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
DOC = argv[0] if argv else os.path.join(REPO, "examples", "bracket.json")

failures = []


def check(what: str, ok: bool, detail: str = "") -> None:
    print("UNDO %-54s %s %s" % (what, "ok" if ok else "FAIL", detail), flush=True)
    if not ok:
        failures.append(what)


def view3d():
    return next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')


def here():
    """The properties, fetched again every time.

    Never held across an undo: Blender replaces the scene datablock, and a
    reference taken before points at freed memory afterwards -- which is a
    segfault on the next attribute read, not an exception.
    """
    return bpy.context.scene.cadcore


def revision() -> int:
    """Which state the kernel is at: the number the scene and the mesh remember."""
    from cadcore_bridge import state
    return int(state.get_client(bpy.context).call("describe_document").get("revision", 0))


def depth() -> int:
    """What the kernel itself says, not what the panel remembers."""
    from cadcore_bridge import state
    return int(state.get_client(bpy.context).call("describe_document")
               .get("undo_depth", 0))


def select_faces(*names) -> None:
    """Pick faces by name: two that meet, so there is an edge to round."""
    ob = bpy.data.objects["cad_body"]
    table = json.loads(ob["cad_face_table"])
    attr = ob.data.attributes["cad_face"]
    for poly in ob.data.polygons:
        poly.select = table[attr.data[poly.index].value] in names


#: the checks run as a chain of timers rather than one function, because the
#: add-on answers an undo on a timer of its own -- `undo_post` fires while
#: Blender is still finishing, so the kernel is walked a moment afterwards.
#: A test that asserted straight after `ed.undo()` would be asking before the
#: answer exists, and would have been right to fail.
BEAT = 0.3
state = {}


def override():
    area = view3d()
    return dict(window=bpy.context.window, screen=bpy.context.screen, area=area,
                region=next(r for r in area.regions if r.type == 'WINDOW'))


def step_one():
    bpy.ops.cadcore.open(filepath=DOC)
    body = bpy.data.objects["cad_body"]
    bpy.ops.object.select_all(action='DESELECT')
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    with bpy.context.temp_override(**override()):
        check("undo is not usable until something is pushed",
              not bpy.ops.ed.undo.poll())
        bpy.ops.ed.undo_push(message="before the CAD edits")
        check("and is once it is", bpy.ops.ed.undo.poll())
        state["depth"], state["faces"] = depth(), here().faces
        select_faces("plate/+z", "plate/+y")
        out = bpy.ops.cadcore.fillet_selected(radius=2.0, kind="fillet")
    check("a fillet runs", out == {'FINISHED'}, str(out))
    check("and the kernel is one edit deeper", depth() == state["depth"] + 1,
          "%d -> %d" % (state["depth"], depth()))
    check("and the part has changed", here().faces != state["faces"],
          "%d -> %d faces" % (state["faces"], here().faces))
    return step(step_two)


def step_two():
    with bpy.context.temp_override(**override()):
        check("Ctrl+Z runs", bpy.ops.ed.undo() == {'FINISHED'})
    return step(step_three)


def step_three():
    check("and it was the fillet that came off, not something older",
          depth() == state["depth"], "kernel at %d, wanted %d"
          % (depth(), state["depth"]))
    check("so the part is the one from before", here().faces == state["faces"],
          "%d, wanted %d" % (here().faces, state["faces"]))
    # what F9 does: undo, then run the same operator with another number. Two
    # fillets deep here would mean Adjust Last Operation builds a stack
    select_faces("plate/+z", "plate/+y")
    with bpy.context.temp_override(**override()):
        again = bpy.ops.cadcore.fillet_selected(radius=4.0, kind="fillet")
    check("the adjusted fillet runs", again == {'FINISHED'}, str(again))
    check("and replaces the edit rather than stacking one",
          depth() == state["depth"] + 1, "kernel at %d, wanted %d"
          % (depth(), state["depth"] + 1))
    check("and it is the new radius that was built",
          here().faces != state["faces"], "%d faces" % here().faces)
    return step(step_four)


def step_four():
    """The same from edit mode, where a click's undo step holds the mesh and not the scene.

    A script's `bpy.ops` call pushes nothing, so the push a click would make is
    made here by hand, in edit mode, the way Blender makes it after a click.
    """
    from cadcore_bridge.link import sync
    body = bpy.data.objects["cad_body"]
    bpy.context.view_layer.objects.active = body
    with bpy.context.temp_override(**override()):
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.ed.undo_push(message="CAD: pick")            # what the pick tool pushes
        bpy.ops.mesh.select_mode(type='FACE')
        sync.select_faces(["plate/+z"])
        state["depth4"], state["faces4"] = depth(), here().faces
        out = bpy.ops.cadcore.pocket_face('INVOKE_DEFAULT', pocket_depth=2.0)
        bpy.ops.ed.undo_push(message="CAD: pocket")
        check("a pocket runs from edit mode", out == {'FINISHED'}, str(out))
        check("and the body is still in edit mode", body.mode == 'EDIT', body.mode)
        check("and the kernel is one edit deeper", depth() == state["depth4"] + 1,
              "%d -> %d" % (state["depth4"], depth()))
        check("and the picked face is still picked",
              sync.selected_face_names(body) == ["plate/+z"], str(sync.selected_face_names(body)))
        state["faces_pocket"] = here().faces
        sync.select_faces(["plate/+z", "pocket1/north"])       # the pocket's rim: a fresh edge
        out = bpy.ops.cadcore.fillet_selected('INVOKE_DEFAULT', radius=1.0, kind="fillet")
        bpy.ops.ed.undo_push(message="CAD: fillet")
        check("a fillet runs after it, still in edit mode", out == {'FINISHED'}, str(out))
        check("two edits deeper now", depth() == state["depth4"] + 2,
              "%d, wanted %d" % (depth(), state["depth4"] + 2))
    return step(step_five)


def step_five():
    with bpy.context.temp_override(**override()):
        check("Ctrl+Z runs in edit mode", bpy.ops.ed.undo() == {'FINISHED'})
    return step(step_six)


def step_six():
    from cadcore_bridge.link import sync
    body = bpy.data.objects["cad_body"]
    check("the fillet came off and the kernel followed the mesh",
          depth() == state["depth4"] + 1, "kernel at %d, wanted %d" % (depth(), state["depth4"] + 1))
    check("still in edit mode", body.mode == 'EDIT', body.mode)
    check("and the mesh on screen is the pocketed part",
          sync.remembered_revision(body) == revision() and here().faces == state["faces_pocket"],
          "mesh revision %s, kernel %s, %d faces" % (sync.remembered_revision(body), revision(), here().faces))
    with bpy.context.temp_override(**override()):
        check("Ctrl+Z runs again", bpy.ops.ed.undo() == {'FINISHED'})
    return step(step_seven)


def step_seven():
    from cadcore_bridge.link import sync
    body = bpy.data.objects["cad_body"]
    check("the pocket came off too", depth() == state["depth4"],
          "kernel at %d, wanted %d" % (depth(), state["depth4"]))
    check("so the part is the one from before the pocket", here().faces == state["faces4"],
          "%d, wanted %d" % (here().faces, state["faces4"]))
    bpy.context.view_layer.objects.active = body
    with bpy.context.temp_override(**override()):
        if body.mode != 'EDIT':
            bpy.ops.object.mode_set(mode='EDIT')
        sync.select_faces(["plate/+z"])
        again = bpy.ops.cadcore.pocket_face('INVOKE_DEFAULT', pocket_depth=3.0)
        bpy.ops.ed.undo_push(message="CAD: pocket")
    check("the adjusted pocket runs", again == {'FINISHED'}, str(again))
    check("and replaces the edit rather than stacking one",
          depth() == state["depth4"] + 1, "kernel at %d, wanted %d"
          % (depth(), state["depth4"] + 1))
    print("UNDO RESULT %s" % ("all ok" if not failures
                              else "FAILED: " + "; ".join(failures)), flush=True)
    bpy.ops.wm.quit_blender()
    return None


def step(fn):
    def go():
        try:
            return fn()
        except Exception as exc:                                    # noqa: BLE001
            import traceback
            traceback.print_exc()
            print("UNDO RESULT FAILED: %s" % exc, flush=True)
            bpy.ops.wm.quit_blender()
            return None
    bpy.app.timers.register(go, first_interval=BEAT)
    return None


def start():
    bpy.ops.preferences.addon_enable(module="cadcore_bridge")
    return step(step_one)


bpy.app.timers.register(start, first_interval=1.0)
