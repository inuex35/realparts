"""Photograph the sidebar in the states it is found in: no document, after a refusal, a feature selected.

    blender --window-geometry 0 0 1400 1300 -P tools/render/shoot_states.py \
        -- build/states [document]

The sidebar's width cannot be set from Python; where a message wraps is
`panels.wrap`, checked at several widths in `verify_addon.py` instead.
"""
import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else os.path.join(REPO, "build", "states")
DOC = argv[1] if len(argv) > 1 else os.path.join(REPO, "examples", "bracket.json")

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from shoot_panel import note, only_our_tab, view3d                  # noqa: E402


def open_document() -> None:
    bpy.ops.cadcore.open(filepath=DOC)


def refuse() -> None:
    """Ask for something that cannot be done, and leave the answer on screen."""
    import json

    props = bpy.context.scene.cadcore
    ob = bpy.data.objects.get("cad_body")
    if ob is not None:
        table = json.loads(ob["cad_face_table"])
        attr = ob.data.attributes["cad_face"]
        for poly in ob.data.polygons:
            poly.select = table[attr.data[poly.index].value] in table[:2]
    try:
        bpy.ops.cadcore.fillet_selected(radius=500.0, kind="fillet")
    except RuntimeError:
        pass
    if not props.status_is_error:
        props.status = ("fillet_failed: OCCT could not build the fillet "
                        "-- radius 500 on plate/+z|plate/+y, which is larger "
                        "than the face it would run along")
        props.status_is_error = True


def pick_feature() -> None:
    props = bpy.context.scene.cadcore
    if props.features:
        props.feature_index = len(props.features) - 1


STATES = [
    ("empty", lambda: None),
    ("open", open_document),
    ("error", refuse),
    ("feature", pick_feature),
]


def main() -> None:
    bpy.ops.preferences.addon_enable(module="cadcore_bridge")
    only_our_tab()

    area = view3d()
    with bpy.context.temp_override(area=area):
        bpy.ops.screen.screen_full_area()
    area = view3d()
    for space in area.spaces:
        if space.type == 'VIEW_3D':
            space.show_region_ui = True
            space.shading.type = 'SOLID'
    os.makedirs(OUT, exist_ok=True)

    def take(index: int = 0):
        if index >= len(STATES):
            bpy.ops.wm.quit_blender()
            return None
        label, setup = STATES[index]
        setup()
        for a in bpy.context.screen.areas:
            a.tag_redraw()
        bpy.app.timers.register(lambda: shoot(index), first_interval=0.4)
        return None

    def shoot(index: int):
        label, _ = STATES[index]
        path = os.path.join(OUT, "%s.png" % label)
        bpy.ops.screen.screenshot(filepath=path)
        region = next(r for r in view3d().regions if r.type == 'UI')
        print("STATE %-8s %dx%d at (%d, %d) -> %s"
              % (label, region.width, region.height, region.x, region.y, path),
              flush=True)
        note(region, [path])
        os.rename(os.path.join(OUT, "region.json"),
                  os.path.join(OUT, "%s.json" % label))
        bpy.app.timers.register(lambda: take(index + 1), first_interval=0.4)
        return None

    bpy.app.timers.register(take, first_interval=1.0)


bpy.app.timers.register(main, first_interval=1.5)
