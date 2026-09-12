"""Every mark the overlay draws on the part and every pick made on one.

Where the arrow, the grips, the pencil, the sketch's points and lines and
the labels last landed in region pixels, and what is picked among them. A
leaf: the overlay writes here, the pick tool reads here, and neither has to
import the other. Lists and dicts are mutated, never rebound, so every
module holding one holds the same one.
"""
from __future__ import annotations

import math

from ..link import sync

#: what the kernel said about a face, kept between menu draws: (face name,
#: undo depth) -> its shape, and -> its frame. Dropped on every build
FACE_SHAPES: dict = {}
FACE_FRAMES: dict = {}


def forget_faces() -> None:
    """A rebuilt document replaced the mesh: what was known about faces is stale."""
    FACE_SHAPES.clear()
    FACE_FRAMES.clear()


#: every label the overlay drew this frame: (x, y, width, height, name)
LABELS: list = []
#: work plane id -> frame, refreshed on every build
PLANES: dict = {}


def plane_size() -> float:
    across = max((max(ob.dimensions) for ob in sync.bodies()), default=0.0)
    return max(20.0, 0.6 * across) if across > 0 else 40.0


#: the push/pull handle on the picked face: face name, origin and normal (world)
HANDLE: dict = {}
#: where the handle's tip was last drawn, in region pixels: (x, y, radius)
HANDLE_TIP: list = []
#: the rest of what a picked flat face offers, beside the arrow: a pencil to
#: draw on it, a plane to pull off it. Both end in the pen, so neither asks
#: for the face again afterwards. kind -> (x, y, radius), refilled every draw.
FACE_TIPS: dict = {}
#: the order they are drawn in; what each starts is in `_face_handle`
FACE_HANDLES = ("draw", "plane")

#: the face a part being dragged would settle against, while it would
SNAP: list = [None]
#: the Undo beside the assistant's last change, in region pixels:
#: (left, bottom, width, height), empty while nothing is shown
UNDO_BUTTON: list = []
#: The ring a plane is being turned on: centre, axis and zero direction in
#: world space, the radius in millimetres, and where the drag has got to.
#: None between drags.
RING: list = [None]
#: The value a drag is at, drawn beside the cursor: (text, x, y) in region
#: pixels, or None between drags. The header says it too, and the header is
#: not where the eye is -- the eye is on the thing being dragged.
BADGE: list = [None]
#: why the value the cursor is at will not build, while it will not. The part
#: on screen is the last one that did, so this is the whole of the news.
BADGE_REFUSED: list = [None]
#: What the cursor is over, for the overlay to show before the click: one of
#: "handle", "point", "label", "corner", "edge", "face", with what it is and,
#: for an edge or a face, the world-space lines to draw it with.
HOVER: dict = {}
#: where the cursor was when that was worked out, and how many times Tab has
#: been pressed there: each press steps the pick out, corner -> edge -> face
HOVER_AT: list = [-1, -1]
CYCLE = [0]


def forget_hover() -> None:
    HOVER.clear()
    HOVER_AT[0] = HOVER_AT[1] = -1
    CYCLE[0] = 0


def step_out() -> bool:
    """Take the pick one step out from what is under the cursor.

    False when the face is all there is: the key that asked for this is Tab,
    and with nothing to step out to it belongs to Blender.
    """
    if HOVER.get("what") not in ("corner", "edge"):
        return False
    CYCLE[0] += 1
    return True


#: the work plane that is picked, if the pick is a plane. Read it through
#: `picked_plane`, never straight: a click clears it, but opening another
#: document or removing the plane does not.
PICKED_PLANE: list = [None]


def picked_plane() -> str | None:
    """The picked work plane, if the document still has one by that name."""
    name = PICKED_PLANE[0]
    if name is not None and name not in PLANES:
        PICKED_PLANE[0] = None
        return None
    return name


#: the grip on the picked edges: where it sits and which way it leans (world)
EDGE_HANDLE: dict = {}
#: a picked edge's grips, one per thing that can be done to it: a green disc
#: rounds it, an orange cut corner takes the corner off
KINDS = ("fillet", "chamfer")
#: where each of those was last drawn, in region pixels: kind -> (x, y, radius)
EDGE_TIPS: dict = {}
#: the word beside each grip, as a box a press may land in: kind -> (l, b, w, h)
EDGE_LABELS: dict = {}
#: the same for the face's marks ("arrow", "draw", "plane") -> (l, b, w, h)
FACE_LABELS: dict = {}
#: which handle the header is describing, so leaving it clears the line
HINTED: list = [None]
#: which one a drag in flight has hold of, so only that one is drawn while it
#: runs. None between drags.
DRAGGING: list = [None]


def _point_to_segment(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    qx, qy = ax + t * dx, ay + t * dy
    return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5


#: the sketch drawn on the part: which one, and where its points and lines
#: landed in region pixels. Refilled every draw, cleared when none is drawn
SKETCH: list = [None]
SKETCH_POINTS: list = []       # (x, y, point name)
SKETCH_LINES: list = []        # (segment name, [(x, y), ...])
#: what is picked on it, in click order: ("point" | "line", name)
PICKED_SKETCH: list = []
#: which sketch those picks belong to, so picks on one do not answer for another
PICKED_ON: list = [None]
#: the sketch the kernel named for the face that was picked, as (feature,
#: sketch): the overlay draws that one rather than guessing from the name.
SKETCH_BEHIND: list = [None, None]
#: the point a drag has hold of, and where the cursor is in the sketch's own
#: (u, v): the overlay draws the rubber band from it. None between drags.
SKETCH_DRAG: list = [None]


def forget_sketch() -> None:
    """No sketch is on screen, so nothing on one can be clicked."""
    SKETCH[0] = None
    SKETCH_POINTS.clear()
    SKETCH_LINES.clear()


def sketch_picks() -> list:
    """What is picked on the sketch that is on screen right now.

    Empty when the picks were made on a different sketch, or when none is
    drawn: what cannot be seen cannot be the context for a menu.
    """
    if SKETCH[0] is None or PICKED_ON[0] != SKETCH[0]:
        return []
    return list(PICKED_SKETCH)


def on_sketch_at(x: int, y: int, near: float = 12.0):
    """What of the drawn sketch is under (x, y): a point, else a line, else None.

    A point wins over the lines that meet at it: it is the smaller thing to
    aim at, so a click that could be either is the one that is harder to hit.
    """
    if SKETCH[0] is None:
        return None
    best, found = near, None
    for px, py, name in SKETCH_POINTS:
        distance = math.hypot(px - x, py - y)
        if distance <= best:
            best, found = distance, ("point", name)
    if found is not None:
        return found
    best = near
    for name, line in SKETCH_LINES:
        for a, b in zip(line, line[1:]):
            distance = _point_to_segment((x, y), a, b)
            if distance < best:
                best, found = distance, ("line", name)
    return found


def pick_on_sketch(hit, add: bool) -> None:
    """Record a click on the sketch. Shift adds; clicking one again drops it."""
    if PICKED_ON[0] != SKETCH[0]:
        PICKED_SKETCH.clear()
        PICKED_ON[0] = SKETCH[0]
    if not add:
        PICKED_SKETCH[:] = [hit]
    elif hit in PICKED_SKETCH:
        PICKED_SKETCH.remove(hit)
    else:
        PICKED_SKETCH.append(hit)


def forget_sketch_picks() -> None:
    """A click that landed elsewhere: the sketch is no longer the context."""
    PICKED_SKETCH.clear()
    PICKED_ON[0] = None


def remember_plane(name: str | None) -> None:
    """Say which work plane is picked. None clears it."""
    PICKED_PLANE[0] = name
