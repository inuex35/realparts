"""Photograph the sidebar on a real display: nothing picked, and two faces picked.

    blender --window-geometry 0 0 1400 1300 -P tools/render/shoot_panel.py \
        -- build/ui [document]

The sidebar's active tab is read-only from Python, so the other tabs' panels
are unregistered for the duration; nothing in the add-on is touched.
"""
import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else os.path.join(REPO, "build", "ui")
DOC = argv[1] if len(argv) > 1 else os.path.join(REPO, "examples", "bracket.json")
#: the second shot picks whatever the document's first two faces are called,
#: because face names belong to the document -- `bracket.json` has `plate/+z`
#: where `sketch_plate.json` has `plate/top`, and naming one here would make
#: the picture silently wrong for every other document
SHOTS = [("empty", 0), ("picked", 2)]


def only_our_tab() -> None:
    """Put our panels in the tab that is already in front, and clear it.

    The sidebar's active tab is read-only from Python and there is no operator
    that sets it, so the tab cannot be brought to the front. The panels can be
    moved to the front tab instead: the built-in ones there are unregistered
    and ours are re-registered under that category. It is a camera trick and
    it changes nothing in the add-on -- `bl_category` is set on the class for
    the length of this process and the file on disk still says "CAD".

    Through `__subclasses__`, walked to the bottom: `dir(bpy.types)` lists
    names that were never registered, and the direct subclasses of `Panel` are
    mostly base classes -- the Transform panel that kept the Item tab alive is
    a grandchild.
    """
    def every(cls):
        for kid in cls.__subclasses__():
            yield kid
            yield from every(kid)

    sidebar = [cls for cls in every(bpy.types.Panel)
               if getattr(cls, "bl_space_type", "") == 'VIEW_3D'
               and getattr(cls, "bl_region_type", "") == 'UI']
    ours = [cls for cls in sidebar if getattr(cls, "bl_category", "") == "CAD"]
    front = "Item"
    gone = 0
    for cls in sidebar:
        if cls in ours:
            continue
        try:
            bpy.utils.unregister_class(cls)
            gone += 1
        except Exception:                                           # noqa: BLE001
            pass
    # parents before children, or Blender refuses the child
    ours.sort(key=lambda c: bool(getattr(c, "bl_parent_id", "")))
    for cls in ours:
        try:
            bpy.utils.unregister_class(cls)
        except Exception:                                           # noqa: BLE001
            pass
    for cls in ours:
        cls.bl_category = front
        bpy.utils.register_class(cls)
    print("SHOT put %d panels away, moved %d of ours to the %s tab"
          % (gone, len(ours), front))


def view3d():
    return next((a for a in bpy.context.screen.areas if a.type == 'VIEW_3D'), None)


def select(how_many: int) -> list:
    import json

    ob = bpy.data.objects.get("cad_body")
    if ob is None:
        return []
    table = json.loads(ob["cad_face_table"])
    attr = ob.data.attributes["cad_face"]
    wanted = set(table[:how_many])
    for poly in ob.data.polygons:
        poly.select = table[attr.data[poly.index].value] in wanted
    return sorted(wanted)


def note(region, paths: list) -> None:
    """Where the sidebar is, so the crop can happen outside.

    Blender's Python has no imaging library, and adding one to take a picture
    of a panel would be a strange dependency. The window is written whole and
    trimmed by the caller.
    """
    import json

    json.dump({"x": region.x, "y": region.y,
               "w": region.width, "h": region.height, "shots": paths},
              open(os.path.join(OUT, "region.json"), "w", encoding="utf-8"))


def main() -> None:
    bpy.ops.preferences.addon_enable(module="cadcore_bridge")
    bpy.ops.cadcore.open(filepath=DOC)
    only_our_tab()

    area = view3d()
    with bpy.context.temp_override(area=area):
        bpy.ops.screen.screen_full_area()      # the viewport, and nothing else
    area = view3d()
    for space in area.spaces:
        if space.type == 'VIEW_3D':
            space.show_region_ui = True
            space.shading.type = 'SOLID'
    os.makedirs(OUT, exist_ok=True)

    body = bpy.data.objects.get("cad_body")
    if body is not None:
        bpy.ops.object.select_all(action='DESELECT')
        body.select_set(True)
        bpy.context.view_layer.objects.active = body
        with bpy.context.temp_override(area=area,
                                       region=next(r for r in area.regions
                                                   if r.type == 'WINDOW')):
            bpy.ops.view3d.view_selected()

    def take(index: int = 0):
        if index >= len(SHOTS):
            bpy.ops.wm.quit_blender()
            return None
        label, picked = SHOTS[index]
        print("SHOT %-8s picking %s" % (label, select(picked)))
        for area in bpy.context.screen.areas:
            area.tag_redraw()               # or the shot is of the last state
        bpy.app.timers.register(lambda: shoot(index), first_interval=0.3)
        return None

    def shoot(index: int):
        label, _ = SHOTS[index]
        path = os.path.join(OUT, "%s.png" % label)
        bpy.ops.screen.screenshot(filepath=path)
        region = next(r for r in view3d().regions if r.type == 'UI')
        print("SHOT %-8s %dx%d at (%d, %d) -> %s"
              % (label, region.width, region.height, region.x, region.y, path))
        note(region, [os.path.join(OUT, "%s.png" % s) for s, _ in SHOTS])
        bpy.app.timers.register(lambda: take(index + 1), first_interval=0.3)
        return None

    bpy.app.timers.register(take, first_interval=1.0)


# guarded so the helpers above can be imported by another shooting script:
# Blender runs a `-P` file as `__main__`, and without this the import alone
# would start this script's own run in the middle of somebody else's
if __name__ == "__main__":
    bpy.app.timers.register(main, first_interval=1.5)
