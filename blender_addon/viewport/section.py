"""Cut the view at a face and see inside.

Only the 3D view is clipped: no feature is made, nothing rebuilds, and the
document does not change. The cut lives on the scene so the panel can slide
it and so it survives the tool that made it.
"""
from __future__ import annotations

import bpy
import mathutils
from bpy.props import BoolProperty, FloatProperty

from . import clipping, modal
from ..link import sync
from .live import _face_to_act_on, _screen
from ..link.client import ServerError
from ..link.state import get_client, report_error


def _views(context):
    """Every 3D view of the window, with its regions. Empty with no window."""
    screen = getattr(context, "screen", None)
    for area in getattr(screen, "areas", ()):
        if area.type != 'VIEW_3D':
            continue
        for space in area.spaces:
            if space.type != 'VIEW_3D':
                continue
            regions = list(space.region_quadviews) or [space.region_3d]
            yield area, [r for r in regions if r is not None]


def editing() -> bool:
    """Is any body in edit mode? Blender crashes if it is and the view is clipped.

    Setting `use_clip_planes` raises RV3D_CLIPPING, but only Blender's own
    Alt+B operator fills `rv3d->clipbb`, and `ED_view3d_clipping_local` reads
    that pointer for every object in edit mode.
    """
    return any(ob.mode == 'EDIT' for ob in sync.bodies())


def show(context, cut: tuple) -> bool:
    """Clip every 3D view at `cut`; False if it had to leave the view alone.

    All six slots hold the one plane; any one of them clips.
    """
    if editing():
        hide(context)
        return False
    for area, regions in _views(context):
        for region in regions:
            region.clip_planes = [cut] * 6
            region.use_clip_planes = True
        area.tag_redraw()
    return True


def hide(context) -> None:
    """Take the clipping off every 3D view."""
    for area, regions in _views(context):
        for region in regions:
            region.use_clip_planes = False
        area.tag_redraw()


def frame_of(context):
    """The cut's face frame, from what the scene remembers."""
    props = context.scene.cadcore
    return {"origin": tuple(props.section_origin), "normal": tuple(props.section_normal)}


def refresh(context) -> None:
    """Put the scene's cut on the views, or take it off. Called when a section
    property changes, so the panel's slider moves the cut as it is dragged."""
    props = getattr(context.scene, "cadcore", None)
    if props is None:
        return
    if not props.section_on:
        hide(context)
        return
    if not show(context, clipping.plane(frame_of(context), props.section_offset,
                                        props.section_flip)):
        props.section_on = False


def remember(context, frame: dict, on_what: str) -> None:
    """Store which face the cut is on, and where that face is."""
    props = context.scene.cadcore
    props.section_face = on_what
    props.section_origin = tuple(frame["origin"])
    props.section_normal = clipping.unit(frame["normal"])


def clear(context) -> None:
    props = getattr(context.scene, "cadcore", None)
    if props is None:
        hide(context)
        return
    props.section_face = ""
    props.section_on = False            # its update takes the clipping off


class CADCORE_OT_section_view(modal.Typed, modal.ViewportTool, bpy.types.Operator):
    bl_idname = "cadcore.section_view"
    bl_label = "Cut the View Here"
    bl_description = ("Cut the view open at this face and look inside; drag to "
                      "slide the cut through the part. The part is not changed")
    bl_options = {'REGISTER'}

    offset: FloatProperty(name="Offset", default=0.0, options={'SKIP_SAVE'})
    flip: BoolProperty(name="Keep the other side", default=False, options={'SKIP_SAVE'})

    def _on(self, context, offset: float) -> None:
        # the cut is for looking, not editing, and edit mode plus a clipped
        # view is what crashes Blender
        if editing():
            bpy.ops.object.mode_set(mode='OBJECT')
        props = context.scene.cadcore
        props.section_offset = offset
        props.section_flip = self.flip
        props.section_on = True         # its update puts the cut on the views

    def _take(self, context, plane, face):
        """Store the cut's frame; the frame comes back, or None when refused."""
        if not plane and face is None:
            self.report({'ERROR'}, "pick a flat face, or a work plane, to cut on")
            return None
        try:
            frame = (get_client(context).call("plane_frame", plane=plane) if plane
                     else get_client(context).call("face_frame", face=face))
        except ServerError as exc:
            report_error(self, exc)
            return None
        remember(context, frame, plane or face)
        return frame

    def execute(self, context):
        """Run with the properties as they are: F9, a typed number and the
        assistant land here, where there is no cursor to read a face from."""
        from . import marks
        from .live import _one_face

        plane = marks.picked_plane()
        target = plane or _one_face(context)
        if target and target != context.scene.cadcore.section_face:
            if self._take(context, plane, None if plane else target) is None:
                return {'CANCELLED'}
        elif not context.scene.cadcore.section_face:
            self.report({'ERROR'}, "pick a flat face, or a work plane, to cut on")
            return {'CANCELLED'}
        self._on(context, self.offset)
        return {'FINISHED'}

    def invoke(self, context, event):
        from . import marks
        plane = marks.picked_plane()
        face = None if plane else _face_to_act_on(context, event)
        self.was_on = context.scene.cadcore.section_on
        self.was_offset = context.scene.cadcore.section_offset
        frame = self._take(context, plane, face)
        if frame is None:
            return {'CANCELLED'}
        self._aim(context, frame)
        self.start = mathutils.Vector((event.mouse_region_x, event.mouse_region_y))
        self._typed = ""
        self._on(context, 0.0)
        context.area.header_text_set(
            "Slide the cut · type a distance · F other side · Enter keeps · Esc drops")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _aim(self, context, frame: dict) -> None:
        """Which way the mouse slides the cut, and how many mm a pixel is worth."""
        origin = mathutils.Vector(frame["origin"])
        normal = mathutils.Vector(clipping.unit(frame["normal"]))
        near, far = _screen(context, origin), _screen(context, origin + normal * 10.0)
        if near is None or far is None or (far - near).length < 1e-3:
            # the face is edge-on, so its normal has no direction on screen:
            # slide with the mouse going up instead of refusing the tool
            self.axis = mathutils.Vector((0.0, 1.0))
            self.per_pixel = modal.pixel_size(context, tuple(origin))
        else:
            self.axis = far - near
            self.per_pixel = 10.0 / self.axis.length
            self.axis.normalize()

    def modal(self, context, event):
        if event.type in modal.NAVIGATION:
            return {'PASS_THROUGH'}
        if self._typing(event):
            typed = self._typed_value()
            if typed is not None:
                self.offset = typed
                self._on(context, typed)
            context.area.header_text_set("Distance: %s" % (self._typed or "0"))
            return {'RUNNING_MODAL'}
        if event.type == 'F' and event.value == 'PRESS':
            self.flip = not self.flip
            self._on(context, self.offset)
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE' and not self._typed:
            here = mathutils.Vector((event.mouse_region_x, event.mouse_region_y))
            self.offset = (here - self.start).dot(self.axis) * self.per_pixel
            self._on(context, self.offset)
            context.area.header_text_set("Cut at %.1f mm · F other side" % self.offset)
            return {'RUNNING_MODAL'}
        if event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            self._teardown(context)
            context.scene.cadcore.status = "the view is cut; clear it in the CAD panel"
            return {'FINISHED'}
        if event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            self._teardown(context)
            if self.was_on:
                self._on(context, self.was_offset)
            else:
                clear(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


class CADCORE_OT_section_clear(bpy.types.Operator):
    bl_idname = "cadcore.section_clear"
    bl_label = "Show the Whole Part"
    bl_description = "Stop cutting the view open"
    bl_options = {'REGISTER'}

    def execute(self, context):
        clear(context)
        return {'FINISHED'}
