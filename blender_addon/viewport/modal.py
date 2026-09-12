"""Shared plumbing for the modal viewport tools.

Navigation pass-through, mouse-to-plane projection, pixel size at a point,
and a draw handler that is installed once and removed safely.
"""
from __future__ import annotations

import bpy
import mathutils

from . import drawing

#: events a modal tool passes back to Blender so the user can orbit and zoom
NAVIGATION = frozenset({'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'})


def align_to_plane(context, frame: dict, fit: float | None = None) -> None:
    """Look straight at the sketch, with its X axis pointing screen-right.

    `fit`, in millimetres, is how far to stand back: half of what the view
    should span, so a work plane is a square with room round it rather than a
    colour the whole viewport has turned. Without it the distance stays.
    """
    rv3d = context.region_data
    if rv3d is None:
        return
    normal = mathutils.Vector(frame["normal"]).normalized()
    x_axis = mathutils.Vector(frame["x_axis"])
    x_axis = (x_axis - normal * x_axis.dot(normal)).normalized()
    y_axis = normal.cross(x_axis)
    rv3d.view_rotation = mathutils.Matrix((x_axis, y_axis, normal)).transposed().to_quaternion()
    rv3d.view_location = mathutils.Vector(frame["origin"])
    rv3d.view_perspective = 'ORTHO'
    if fit:
        rv3d.view_distance = fit
    context.area.tag_redraw()


def mouse_ray(context, event) -> tuple:
    """The ray under the mouse, as `(origin, direction)` tuples in scene space."""
    from bpy_extras import view3d_utils

    region, rv3d = context.region, context.region_data
    coord = (event.mouse_region_x, event.mouse_region_y)
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    return tuple(origin), tuple(direction)


def plane_hit(context, event, frame: dict):
    """Where the mouse's ray meets a sketch plane, or None when it misses."""
    origin, direction = mouse_ray(context, event)
    return drawing.ray_plane(origin, direction, frame)


def pixel_size(context, at) -> float:
    """Millimetres per pixel at `at`, for snap and hit radii and drag distances."""
    rv3d = context.region_data
    if rv3d is None:
        return 0.1
    if rv3d.is_perspective:
        depth = abs((rv3d.view_matrix @ mathutils.Vector(at)).z) or 1.0
        return depth * 2.0 / max(context.region.width, 1)
    return rv3d.view_distance * 2.0 / max(context.region.width, 1)


def say(context, event, header: str, badge: str | None = None) -> None:
    """The running value: in the header, and in a badge beside the cursor.

    Both, not one: the header is where Blender puts these and where nobody
    looks, and the badge is where the hand already is.
    """
    from . import marks

    marks.BADGE[0] = ((badge, event.mouse_region_x + 20, event.mouse_region_y + 20)
                     if badge else None)
    if context.area is not None:                # no area when a script drives this
        context.area.header_text_set(header)
        context.area.tag_redraw()


def refused(context, why: str) -> None:
    """The value the cursor is at will not build. The badge says so, in red,
    and the part on screen is left at the last one that did."""
    from . import marks

    marks.BADGE_REFUSED[0] = why
    if context.area is not None:
        context.area.tag_redraw()


def built(context) -> None:
    """It builds again: the badge goes back to its own colour."""
    from . import marks

    marks.BADGE_REFUSED[0] = None


def hush(context) -> None:
    """The drag is over: take the header line and the badge away."""
    from . import marks

    marks.BADGE[0] = None
    marks.BADGE_REFUSED[0] = None
    if context.area is not None:
        context.area.header_text_set(None)
        context.area.tag_redraw()


class Typed:
    """Digits typed during a drag stand in for the mouse."""

    _typed = ""

    def _typing(self, event) -> bool:
        """Collect a typed value; True if the key was part of one."""
        if event.value != 'PRESS':
            return False
        keys = {"ZERO": "0", "ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4", "FIVE": "5",
                "SIX": "6", "SEVEN": "7", "EIGHT": "8", "NINE": "9", "PERIOD": ".",
                "NUMPAD_PERIOD": ".", "MINUS": "-", "NUMPAD_MINUS": "-"}
        for i in range(10):
            keys["NUMPAD_%d" % i] = str(i)
        if event.type in keys:
            self._typed += keys[event.type]
        elif event.type == 'BACK_SPACE':
            self._typed = self._typed[:-1]
        else:
            return False
        return True

    def _typed_value(self):
        try:
            return float(self._typed) if self._typed not in ("", "-", ".", "-.") else None
        except ValueError:
            return None


class ViewportTool:
    """Mixin for a modal operator that draws in the viewport.

    `_watch` installs the draw handler; `_teardown` removes it and clears the
    header, and is safe to call more than once. The subclass's `modal` is
    wrapped so an exception tears down: Blender would otherwise keep the draw
    handler alive with no modal operator behind it.
    """

    _handle = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        inner = cls.__dict__.get("modal")
        if inner is None:
            return

        # Blender checks the signature at registration and refuses a fourth
        # argument, even a defaulted one.
        def guarded(inner):
            def modal(self, context, event):
                try:
                    return inner(self, context, event)
                except Exception as exc:                              # noqa: BLE001
                    import traceback
                    traceback.print_exc()
                    self._teardown(context)
                    context.scene.cadcore.status = "%s failed: %s" % (self.bl_label, exc)
                    context.scene.cadcore.status_is_error = True
                    return {'CANCELLED'}
            modal.__doc__ = inner.__doc__
            return modal
        cls.modal = guarded(inner)

    def _watch(self, context) -> None:
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw, (context,), 'WINDOW', 'POST_VIEW')

    def _teardown(self, context) -> None:
        if getattr(self, "_handle", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        hush(context)
