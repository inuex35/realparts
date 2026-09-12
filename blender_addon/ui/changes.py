"""What the assistant just did, drawn on the part for a few seconds.

A transcript line says which operation ran. It does not say what happened to
the shape. This takes a picture of every CAD face before an assistant step
and compares it with the one after: what appeared, what went, what moved, and
which numbers changed. The overlay draws that answer on the part, and it goes
on its own or on the next click.
"""
from __future__ import annotations

import json
import time

import mathutils

from ..link import sync

#: how long the answer stays up: long enough to read, short enough that it is
#: gone before it is in the way
SECONDS = 7.0
#: how far a face's middle has to move before it counts as having moved
MOVED_MM = 0.05

#: the last comparison, and when it stops being drawn
SHOWING: dict = {}


def picture(context) -> dict:
    """Every CAD face with the corners that draw it, plus the numbers.

    Read through `foreach_get`, which is C, so taking this before every
    assistant step is not felt: the rebuild that follows costs far more.
    """
    faces: dict = {}
    for body in sync.bodies():
        table = body.get("cad_face_table")
        if table and body.mode != 'EDIT':      # an edit mesh is not in body.data
            faces.update(_runs(body, json.loads(table)))
    props = context.scene.cadcore
    return {"faces": faces, "numbers": {p.name: p.value for p in props.parameters}}


def _runs(body, table: list) -> dict:
    """CAD face name -> the world-space corner runs of its polygons."""
    mesh = body.data
    attribute = mesh.attributes.get("cad_face")
    if attribute is None or not len(mesh.polygons):
        return {}
    count = len(mesh.polygons)
    which, starts, totals = [0] * count, [0] * count, [0] * count
    attribute.data.foreach_get("value", which)
    mesh.polygons.foreach_get("loop_start", starts)
    mesh.polygons.foreach_get("loop_total", totals)
    loops = [0] * len(mesh.loops)
    mesh.loops.foreach_get("vertex_index", loops)
    flat = [0.0] * (len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", flat)
    matrix = body.matrix_world
    out: dict = {}
    for polygon in range(count):
        index = which[polygon]
        if not 0 <= index < len(table):
            continue
        run = []
        for loop in range(starts[polygon], starts[polygon] + totals[polygon]):
            at = loops[loop] * 3
            run.append(matrix @ mathutils.Vector(flat[at:at + 3]))
        out.setdefault(table[index], []).append(run)
    return out


def _middle(runs: list):
    """The middle of a face: the centre of the box round its corners (a mean
    of the corners moves when a hole re-triangulates the face)."""
    points = [point for run in runs for point in run]
    if not points:
        return None
    low = mathutils.Vector([min(p[i] for p in points) for i in range(3)])
    high = mathutils.Vector([max(p[i] for p in points) for i in range(3)])
    return (low + high) / 2


def settle(context, before: dict) -> None:
    """Compare the part with the picture taken before, and put up the answer."""
    if not before:
        return
    now = picture(context)
    was, is_now = before["faces"], now["faces"]
    added = sorted(set(is_now) - set(was))
    gone = sorted(set(was) - set(is_now))
    moved = []
    for name in set(was) & set(is_now):
        a, b = _middle(was[name]), _middle(is_now[name])
        if a is not None and b is not None and (b - a).length > MOVED_MM:
            moved.append((tuple(a), tuple(b)))
    numbers = [(name, before["numbers"][name], value)
               for name, value in now["numbers"].items()
               if name in before["numbers"] and before["numbers"][name] != value]
    if not (added or gone or moved or numbers):
        clear()
        return
    SHOWING.clear()
    SHOWING.update({
        "added": added,
        # the shape they had, because the shape itself is gone
        "gone": [run for name in gone for run in was[name]], "gone_faces": len(gone),
        "moved": moved, "numbers": numbers,
        "until": time.monotonic() + SECONDS})
    _redraw_when_it_ends(context)


def clear() -> None:
    SHOWING.clear()


def showing() -> bool:
    return bool(SHOWING) and time.monotonic() < SHOWING.get("until", 0.0)


def summary() -> str:
    """One line: what changed, for the status and for the label on the part."""
    parts = []
    for count, word in ((len(SHOWING.get("added", ())), "face"),
                        (SHOWING.get("gone_faces", 0), "gone"),
                        (len(SHOWING.get("moved", ())), "moved")):
        if count and word == "face":
            parts.append("%d new face%s" % (count, "" if count == 1 else "s"))
        elif count and word == "gone":
            parts.append("%d face%s taken off" % (count, "" if count == 1 else "s"))
        elif count:
            parts.append("%d moved" % count)
    for name, was, is_now in SHOWING.get("numbers", ()):
        parts.append("%s %.3g -> %.3g" % (name, was, is_now))
    return ", ".join(parts) or "nothing changed"


def _redraw_when_it_ends(context) -> None:
    """One timer, so the answer clears itself off the screen when it expires.

    Without it the last frame drawn stays until something else asks for a
    redraw, which in a still viewport can be a long time.
    """
    import bpy

    def wipe():
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return None

    try:
        bpy.app.timers.register(wipe, first_interval=SECONDS + 0.1)
    except Exception:                                                # noqa: BLE001
        pass
