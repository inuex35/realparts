"""The modal operator that draws a profile on a face.

It owns a draw handler, a cursor and a small state machine. The geometry
lives in :mod:`drawing`, where it can be tested without a window.
"""
from __future__ import annotations

import math

import bpy

from ..viewport import drawing, gpu_draw, modal
from ..viewport.live import _Live
from ..link import sync
from ..link.client import ServerError
from ..link.state import get_client, report_error


def _corners_of(face: str) -> list:
    """The world-space corners of a CAD face's polygons, on whichever part
    carries it. In edit mode the mesh lives in the BMesh and the attribute
    reads empty, so both are read the way the mode allows."""
    import json

    for body in sync.bodies():
        table = body.get("cad_face_table")
        if not table:
            continue
        table = json.loads(table)
        if face not in table:
            continue
        wanted = table.index(face)
        matrix = body.matrix_world
        if body.mode == 'EDIT':
            import bmesh

            bm = bmesh.from_edit_mesh(body.data)
            layer = bm.faces.layers.int.get("cad_face")
            if layer is None:
                return []
            return [matrix @ v.co for f in bm.faces if f[layer] == wanted for v in f.verts]
        attribute = body.data.attributes.get("cad_face")
        if attribute is None or len(attribute.data) != len(body.data.polygons):
            return []
        return [matrix @ body.data.vertices[i].co
                for polygon in body.data.polygons
                if attribute.data[polygon.index].value == wanted
                for i in polygon.vertices]
    return []


class CADCORE_OT_draw_sketch(modal.ViewportTool, _Live, bpy.types.Operator):
    bl_idname = "cadcore.draw_sketch"
    bl_label = "Draw on Face"
    bl_description = ("Draw a profile on the selected face; it becomes a dimensioned, "
                      "fully constrained sketch")

    wants = 1                 # the face to work on
    picks = "faces"

    def invoke(self, context, event):
        from ..viewport import pick
        from ..viewport.live import _face_to_act_on

        from ..viewport import live

        self.plane = pick.picked_plane()
        faces = [] if self.plane else sync.selected_face_names()
        if len(faces) != 1 and not self.plane:
            # reaching for the tool over a face is also a way of picking it
            under = live._face_to_act_on(context, event)
            faces = [under] if under else faces
        if len(faces) != 1 and not self.plane:
            # the tool's own press is how a person says where to draw, and
            # asking them to pick it first with a different tool is a step
            # that does not need to be there. A plane in front of the face
            # wins, the way it does for the pick.
            here = (event.mouse_region_x, event.mouse_region_y)
            plane, on_plane = pick._plane_at(context, here)
            face, on_face = pick._face_at(context, here)
            if plane is not None and (face is None
                                      or pick._nearer(context, here, on_plane, on_face)):
                pick.remember_plane(plane)
                self.plane = plane
            elif face is not None:
                faces = [_face_to_act_on(context, event)]
        if len(faces) != 1 and not self.plane:
            self.report({'ERROR'}, "press on a flat face, or pick a work plane, to draw on")
            return {'CANCELLED'}
        try:
            self.frame = (get_client(context).call("plane_frame", plane=self.plane) if self.plane
                          else get_client(context).call("face_frame", face=faces[0]))
        except ServerError as exc:
            return report_error(self, exc)

        modal.align_to_plane(context, self.frame, fit=self._fit(context))
        self.face = faces[0] if faces else self.plane
        self.stage = 'DRAW'           # then 'DEPTH', once the profile is closed
        self.points: list = []
        self.circles: list = []
        self.slots: list = []          # {"from": i, "to": j, "width": w}
        self.ellipses: list = []       # {"centre": i, "major": a, "minor": b}
        self.sides = 6                 # regular-polygon side count
        self.centres: set = set()      # point indices that are not profile vertices
        self.slot_b = None             # a slot's second centre, while it is placed
        self.arcs: list = []           # reserved for arc segments
        self.mode = 'LINE'
        self.centre = None
        self.rect = None                 # the rectangle's first corner, once set
        self.cursor = None
        self.hint: dict = {}
        self.anchors = self._anchors(context)
        self._btn_rects = []
        self._btn_handle = None
        self._mouse = None
        self._palette = None
        self._watch(context)
        self._btn_handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_buttons, (context,), 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set(
            "Draw on %s -- click to place, click the first point to close | "
            "right-click for shapes | Backspace undo | Enter finish | Esc cancel"
            % self.face)
        return {'RUNNING_MODAL'}

    # -- events -------------------------------------------------------------
    def modal(self, context, event):
        if event.type in modal.NAVIGATION:
            return {'PASS_THROUGH'}                      # orbit and zoom stay Blender's
        if self.stage == 'DEPTH':
            return self._depth_modal(context, event)
        context.area.tag_redraw()

        if event.type == 'MOUSEMOVE':
            self._track(context, event)
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if self._palette is not None:                # the shape picker is up
                picked = self._button_at(event.mouse_region_x, event.mouse_region_y)
                if picked is not None:
                    self.mode = picked
                    self.centre = None
                    self.rect = None
                self._palette = None                     # a pick or a miss both close it
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}
            self._track(context, event)
            if self.cursor is None:
                return {'RUNNING_MODAL'}
            if self.mode == 'CIRCLE':
                if self.centre is None:
                    self.centre = self.cursor
                else:
                    radius = math.dist(self.centre, self.cursor)
                    if radius > 1e-6:
                        self.points.append(self.centre)
                        self.centres.add(len(self.points) - 1)
                        self.circles.append({"centre": len(self.points) - 1,
                                             "radius": radius})
                    self.centre = None
                    self.mode = 'LINE'
                return {'RUNNING_MODAL'}
            if self.mode == 'RECT':
                # two clicks: a corner, then the corner across from it
                if self.rect is None:
                    self.rect = self.cursor
                    return {'RUNNING_MODAL'}
                if math.dist(self.rect, self.cursor) < 1e-6:
                    return {'RUNNING_MODAL'}          # a zero-area rectangle is nothing
                self.points = list(drawing.rectangle_points(self.rect, self.cursor))
                self.rect = None
                return self._close(context, event, closed=True)
            if self.mode == 'POLYGON':
                # click the centre, then a vertex; the radius click closes it
                if self.centre is None:
                    self.centre = self.cursor
                    return {'RUNNING_MODAL'}
                if math.dist(self.centre, self.cursor) < 1e-6:
                    return {'RUNNING_MODAL'}
                self.points = list(drawing.polygon_points(
                    self.centre, self.cursor, self.sides))
                self.centre = None
                return self._close(context, event, closed=True)
            if self.mode == 'ELLIPSE':
                # centre, then a corner of its bounding box (axis-aligned)
                if self.centre is None:
                    self.centre = self.cursor
                    return {'RUNNING_MODAL'}
                major = abs(self.cursor[0] - self.centre[0])
                minor = abs(self.cursor[1] - self.centre[1])
                if major < 1e-6 or minor < 1e-6:
                    return {'RUNNING_MODAL'}
                idx = len(self.points)
                self.points.append(tuple(self.centre))
                self.centres.add(idx)
                self.ellipses.append({"centre": idx, "major": major, "minor": minor})
                self.centre = None
                return self._close(context, event, closed=True)
            if self.mode == 'SLOT':
                # two centres, then a click that sets the width off the centreline
                if self.centre is None:
                    self.centre = self.cursor
                    return {'RUNNING_MODAL'}
                if self.slot_b is None:
                    if math.dist(self.centre, self.cursor) < 1e-6:
                        return {'RUNNING_MODAL'}
                    self.slot_b = self.cursor
                    return {'RUNNING_MODAL'}
                width = 2.0 * drawing.point_to_line(self.cursor, self.centre, self.slot_b)
                if width < 1e-6:
                    return {'RUNNING_MODAL'}
                ia, ib = len(self.points), len(self.points) + 1
                self.points.append(tuple(self.centre))
                self.points.append(tuple(self.slot_b))
                self.centres.update({ia, ib})
                self.slots.append({"from": ia, "to": ib, "width": width})
                self.centre = None
                self.slot_b = None
                return self._close(context, event, closed=True)
            if drawing.closes_loop(self.cursor, self._chain()):
                return self._close(context, event, closed=True)
            self.points.append(self.cursor)
            return {'RUNNING_MODAL'}

        if event.type == 'C' and event.value == 'PRESS':
            self.mode = 'CIRCLE' if self.mode != 'CIRCLE' else 'LINE'
            self.centre = None
            self.rect = None
            return {'RUNNING_MODAL'}

        if event.type == 'R' and event.value == 'PRESS':
            self.mode = 'RECT' if self.mode != 'RECT' else 'LINE'
            self.centre = None
            self.rect = None
            return {'RUNNING_MODAL'}

        _sides = {'THREE': 3, 'FOUR': 4, 'FIVE': 5, 'SIX': 6, 'SEVEN': 7, 'EIGHT': 8,
                  'NUMPAD_3': 3, 'NUMPAD_4': 4, 'NUMPAD_5': 5, 'NUMPAD_6': 6,
                  'NUMPAD_7': 7, 'NUMPAD_8': 8}
        if event.type in _sides and event.value == 'PRESS':
            self.sides = _sides[event.type]
            if self.mode != 'POLYGON':
                self.mode = 'POLYGON'
                self.centre = None
                self.rect = None
            return {'RUNNING_MODAL'}

        if event.type == 'BACK_SPACE' and event.value == 'PRESS':
            if self.centre is not None:
                self.centre = None
            elif self.rect is not None:
                self.rect = None
            elif self.points:
                last = len(self.points) - 1
                self.points.pop()
                if last in self.centres:      # remove its circle too
                    self.centres.discard(last)
                    self.circles = [c for c in self.circles if c["centre"] != last]
            return {'RUNNING_MODAL'}

        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            return self._close(context, event, closed=True)

        if event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
            # pop the shape picker where the mouse is, or put it away again
            self._palette = None if self._palette else (
                event.mouse_region_x, event.mouse_region_y)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        if event.type == 'ESC' and event.value == 'PRESS':
            self._teardown(context)
            context.scene.cadcore.status = "drawing cancelled"
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def cancel(self, context):
        """Blender took the operator away (the window changed). With the pen
        out there is only the draw handler to take down; with the depth being
        dragged, `_Live` drops the feature as well."""
        if self.stage == 'DEPTH':
            return _Live.cancel(self, context)
        self._teardown(context)

    # -- the depth, once the profile is closed --------------------------------
    def _depth_modal(self, context, event):
        """The feature is on the screen at the default depth; the mouse and
        the keyboard change it until Enter keeps it or Esc takes it away."""
        if self._typing(event):
            typed = self._typed_value()
            if typed is not None and typed > 0:
                modal.say(context, event, self._depth_header("%s mm (typed)" % self._typed),
                          self._typed + " mm")
                self._preview(context, typed, self._make, self._edit)
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE' and not self._typed:
            pixels = event.mouse_region_y - self.start_y
            # a small nudge keeps the default depth: closing the loop should
            # not become a depth change by accident
            deadzone = 8.0
            if abs(pixels) <= deadzone:
                pixels = 0.0
            else:
                pixels -= deadzone if pixels > 0 else -deadzone
            step = 0.1 if event.ctrl else 0.5
            value = drawing.depth_from_drag(self.base_depth, pixels, self.per_pixel, step=step)
            if self._value is not None and abs(value - self._value) < 1e-9:
                return {'RUNNING_MODAL'}
            modal.say(context, event, self._depth_header("%.2f mm" % value), "%.2f mm" % value)
            self._preview(context, value, self._make, self._edit)
            return {'RUNNING_MODAL'}
        if (event.type == 'LEFTMOUSE' and event.value == 'PRESS') or \
                (event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS'):
            if self._value is None:
                return self._cancel(context)
            return self._keep(context)
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            # the feature that was on the screen goes with the drag
            self._cancel(context)
            context.scene.cadcore.status = "drawing dropped"
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def _depth_header(self, value: str) -> str:
        return ("%s on %s: %s -- drag up for deeper, or type a number | "
                "Ctrl fine | click or Enter to keep | Esc to drop"
                % (self.operation.capitalize(), self.face, value))

    def _make(self, value):
        where = {"plane": self.plane} if self.plane else {"face": self.face}
        spec = {"points": [list(p) for p in self.points],
                "lines": self.lines, "circles": self.circles,
                "operation": self.operation, "depth": round(value, 3), **where}
        if self.slots:
            spec["slots"] = self.slots
        if self.ellipses:
            spec["ellipses"] = self.ellipses
        if self.arcs:
            spec["arcs"] = self.arcs
        return ("add_sketch", spec)

    def _edit(self, value):
        return ("set_parameter", {"name": self.feature + "_depth", "value": round(value, 3)})

    def _keep(self, context):
        props = context.scene.cadcore
        out = _Live._finish(self, context, self._make, "sketch")
        if 'FINISHED' not in out:
            return out
        feature = self.feature
        props.status = "%s from %d point(s), %.2f mm deep" % (
            feature, len(self.points), self._value)
        # show the sketch's dimensions where they were drawn, ready to be typed over
        names = [f.name for f in props.features]
        if feature in names:
            props.feature_index = names.index(feature)
        if not props.show_dimensions:
            bpy.ops.cadcore.show_dimensions()
        self.report({'INFO'}, props.status)
        return {'FINISHED'}

    def _fit(self, context) -> float | None:
        """How far to stand back so the whole plane, or the part, is in view."""
        from ..viewport.marks import plane_size

        if self.plane:
            return plane_size() * 1.6
        across = max((max(ob.dimensions) for ob in sync.bodies()), default=0.0)
        return across * 0.9 if across > 0 else None

    def _track(self, context, event) -> None:
        """Set the snapped cursor position on the sketch plane, or None on a miss."""
        self._mouse = (event.mouse_region_x, event.mouse_region_y)
        hit = modal.plane_hit(context, event, self.frame)
        if hit is None:
            self.cursor = None
            return
        uv = drawing.to_uv(hit, self.frame)
        # snap radii are in pixels, converted to millimetres at the cursor depth
        scale = modal.pixel_size(context, drawing.to_3d(uv, self.frame))
        chain = self._chain()
        previous = self.centre or (chain[-1] if chain else None)
        self.cursor, self.hint = drawing.snap(
            uv, self.points, previous, radius=12 * scale, axis_tolerance=8 * scale,
            anchors=self.anchors)

    def _anchors(self, context) -> list:
        """Points on the part worth landing on, in the sketch's own frame.

        The corners of the face being drawn on, and the centre of every round
        face through it. Read once: they do not move while the pen is out.
        """
        if self.plane or not self.face:
            return []                    # a work plane has no part under it yet
        out, seen = [], set()
        for point in _corners_of(self.face):
            key = tuple(round(c, 4) for c in point)
            if key not in seen:
                seen.add(key)
                out.append(drawing.to_uv(tuple(point), self.frame))
        try:
            from ..viewport.live import Snapper

            described = get_client(context).call("describe_faces")["faces"]
            out += [uv for uv, _ in Snapper(described, self.frame, self.face).points]
        except ServerError:
            pass
        return out

    def _chain(self) -> list:
        """The drawn points without the circle centres."""
        return [p for i, p in enumerate(self.points) if i not in self.centres]

    # -- drawing ------------------------------------------------------------
    def _draw(self, context) -> None:
        import gpu

        gpu.state.blend_set('ALPHA')
        gpu.state.line_width_set(2.0)
        # the sketch lies on the face it will cut, so disable depth testing
        gpu.state.depth_test_set('NONE')

        def stroke(uvs, colour):
            gpu_draw.line_strip([drawing.to_3d(p, self.frame) for p in uvs], colour)

        chain = self._chain()
        if self.cursor is not None and self.mode == 'LINE':
            chain = chain + [self.cursor]
        stroke(chain, (0.95, 0.65, 0.15, 1.0))
        for circle in self.circles:
            stroke(drawing.circle_points(self.points[circle["centre"]], circle["radius"]),
                   (0.95, 0.65, 0.15, 1.0))
        if self.mode == 'CIRCLE' and self.centre is not None and self.cursor is not None:
            stroke(drawing.circle_points(self.centre, math.dist(self.centre, self.cursor)),
                   (0.4, 0.8, 1.0, 1.0))
        if self.mode == 'RECT' and self.rect is not None and self.cursor is not None:
            corners = drawing.rectangle_points(self.rect, self.cursor)
            stroke(corners + [corners[0]], (0.4, 0.8, 1.0, 1.0))
        if self.mode == 'POLYGON' and self.centre is not None and self.cursor is not None:
            poly = drawing.polygon_points(self.centre, self.cursor, self.sides)
            stroke(poly + [poly[0]], (0.4, 0.8, 1.0, 1.0))
        if self.mode == 'ELLIPSE' and self.centre is not None and self.cursor is not None:
            oval = drawing.ellipse_points(self.centre,
                                          abs(self.cursor[0] - self.centre[0]),
                                          abs(self.cursor[1] - self.centre[1]))
            stroke(oval, (0.4, 0.8, 1.0, 1.0))
        if self.mode == 'SLOT' and self.centre is not None and self.cursor is not None:
            if self.slot_b is None:
                stroke([self.centre, self.cursor], (0.4, 0.8, 1.0, 1.0))
            else:
                width = 2.0 * drawing.point_to_line(self.cursor, self.centre, self.slot_b)
                stroke(drawing.slot_outline(self.centre, self.slot_b, width),
                       (0.4, 0.8, 1.0, 1.0))

        gpu_draw.points([drawing.to_3d(p, self.frame) for p in self.points],
                        (1.0, 1.0, 1.0, 1.0), 9.0)
        if self.cursor is not None and self.hint.get("kind") in ("point", "on_part", "horizontal",
                                                                "vertical"):
            gpu_draw.points([drawing.to_3d(self.cursor, self.frame)],
                            (0.2, 1.0, 0.5, 1.0), 14.0)
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.blend_set('NONE')

    # -- the shape buttons --------------------------------------------------
    #: internal id and the icon drawn for each drawing mode, in bar order
    _BUTTONS = ("LINE", "RECT", "CIRCLE", "POLYGON", "SLOT", "ELLIPSE")

    @staticmethod
    def _round_rect(x, y, w, h, r, steps=4):
        """Filled-polygon points for a rounded rectangle, corners first so the
        list also works as a LINE_LOOP outline."""
        import math
        r = min(r, w / 2, h / 2)
        pts = []
        # centre of each corner arc, and the angle it sweeps
        corners = (
            (x + w - r, y + h - r, 0.0),
            (x + r,     y + h - r, math.pi / 2),
            (x + r,     y + r,     math.pi),
            (x + w - r, y + r,     math.pi * 1.5),
        )
        for cx, cy, a0 in corners:
            for i in range(steps + 1):
                a = a0 + (math.pi / 2) * (i / steps)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        return pts

    def _draw_buttons(self, context) -> None:
        """A Line / Rectangle / Circle switch centred at the bottom of the
        viewport, so the shapes need no remembered key. One dark rounded bar
        with three icon cells; `_button_at` reads the same cells back to turn a
        click into a mode."""
        import math
        import gpu
        from gpu_extras.batch import batch_for_shader

        region = context.region
        self._btn_rects = []
        if self._palette is None:                        # nothing until right-click
            return

        n = len(self._BUTTONS)
        cell, pad = 52, 6
        bar_w = n * cell + 2 * pad
        bar_h = cell + 2 * pad
        # open on the summon point and stay put, so the icons never flee the
        # mouse; the point sits at the bar centre and is clamped to the region
        px, py = self._palette
        margin = 8
        bar_x = int(min(max(px - bar_w / 2, margin), region.width - bar_w - margin))
        bar_y = int(min(max(py - bar_h / 2, margin), region.height - bar_h - margin))
        accent = (0.98, 0.86, 0.35)

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        gpu.state.line_width_set(1.0)

        def fill(pts, col):
            shader.bind()
            shader.uniform_float("color", col)
            batch_for_shader(shader, 'TRI_FAN', {"pos": pts}).draw(shader)

        def stroke(pts, col, width=1.0, loop=True):
            gpu.state.line_width_set(width)
            shader.bind()
            shader.uniform_float("color", col)
            kind = 'LINE_LOOP' if loop else 'LINE_STRIP'
            batch_for_shader(shader, kind, {"pos": pts}).draw(shader)
            gpu.state.line_width_set(1.0)

        # the bar itself: a soft dark slab with a hairline edge
        bar = self._round_rect(bar_x, bar_y, bar_w, bar_h, 12)
        fill(bar, (0.11, 0.12, 0.14, 0.90))
        stroke(bar, (1.0, 1.0, 1.0, 0.08), 1.0)

        for i, mode in enumerate(self._BUTTONS):
            active = self.mode == mode
            cx0 = bar_x + pad + i * cell
            cy0 = bar_y + pad
            # the active cell gets a muted accent pad behind its icon
            if active:
                fill(self._round_rect(cx0 + 3, cy0 + 3, cell - 6, cell - 6, 9),
                     (accent[0], accent[1], accent[2], 0.16))
                stroke(self._round_rect(cx0 + 3, cy0 + 3, cell - 6, cell - 6, 9),
                       (accent[0], accent[1], accent[2], 0.55), 1.0)
            icon = (accent[0], accent[1], accent[2], 1.0) if active                 else (0.72, 0.74, 0.78, 1.0)
            cx, cy = cx0 + cell / 2, cy0 + cell / 2
            r = 11
            if mode == "LINE":
                stroke([(cx - r, cy - r), (cx + r, cy + r)], icon, 2.0, loop=False)
            elif mode == "RECT":
                stroke([(cx - r, cy - r), (cx + r, cy - r),
                        (cx + r, cy + r), (cx - r, cy + r)], icon, 2.0)
            elif mode == "CIRCLE":
                ring = [(cx + r * math.cos(t), cy + r * math.sin(t))
                        for t in [k / 24 * 2 * math.pi for k in range(24)]]
                stroke(ring, icon, 2.0)
            elif mode == "POLYGON":  # a hexagon, a vertex pointing up
                poly = [(cx + r * math.cos(math.pi / 2 + k * math.pi / 3),
                         cy + r * math.sin(math.pi / 2 + k * math.pi / 3))
                        for k in range(6)]
                stroke(poly, icon, 2.0)
            elif mode == "SLOT":     # an obround lying on its side
                stroke(self._round_rect(cx - r, cy - r * 0.55,
                                        2 * r, r * 1.1, r * 0.55, steps=6), icon, 2.0)
            else:  # ELLIPSE -- wider than tall
                oval = [(cx + r * math.cos(t), cy + r * 0.6 * math.sin(t))
                        for t in [k / 24 * 2 * math.pi for k in range(24)]]
                stroke(oval, icon, 2.0)
            self._btn_rects.append((cx0, cy0, cell, cell, mode))

        gpu.state.blend_set('NONE')

    def _button_at(self, x, y):
        """The mode whose button is under `(x, y)` in region pixels, or None."""
        for bx, by, bw, bh, mode in getattr(self, "_btn_rects", []):
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return mode
        return None

    def _remove_buttons(self) -> None:
        if getattr(self, "_btn_handle", None) is not None:
            import bpy as _bpy
            _bpy.types.SpaceView3D.draw_handler_remove(self._btn_handle, 'WINDOW')
            self._btn_handle = None
        self._btn_rects = []

    def _teardown(self, context) -> None:
        self._remove_buttons()
        modal.ViewportTool._teardown(self, context)

    # -- ending -------------------------------------------------------------
    def _close(self, context, event, closed: bool):
        """The profile is complete: build it at the default depth right away,
        and stay for the depth to be dragged or typed. `_Live` then keeps or
        drops it through its own `_finish` and `_cancel`."""
        props = context.scene.cadcore
        self.lines = drawing.polyline(self.points, closed=closed, skip=self.centres)
        if not (self.lines or self.circles or self.slots or self.ellipses or self.arcs):
            self._teardown(context)
            props.status = "nothing drawn"
            return {'CANCELLED'}
        # the pen is put away, but the header and badge stay for the depth
        self._remove_buttons()
        if getattr(self, "_handle", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        # on a plane with no solid yet, the only thing to make is a solid
        self.operation = (props.pocket_kind if props.pocket_kind in ("pocket", "boss") else "pocket")
        if self.plane and not props.has_body:
            self.operation = "extrude"
        self.base_depth = props.pocket_depth
        # depth grows with the drag at a rate the depth itself sets, so the
        # gesture feels the same however far the view is zoomed out to fit the
        # plane -- tying it to pixel size made a fitted plane hair-trigger
        self.per_pixel = max(self.base_depth, 1.0) / 200.0
        self.start_y = event.mouse_region_y
        self.stage = 'DEPTH'
        self._begin(context)
        self._preview(context, self.base_depth, self._make, self._edit)
        context.area.header_text_set(self._depth_header("%.2f mm" % self.base_depth))
        return {'RUNNING_MODAL'}
