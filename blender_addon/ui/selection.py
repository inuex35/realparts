"""What is picked in the viewport, and which tools that allows.

Each operator declares what it needs (``wants``, ``picks``); the panel reads
the same declaration to grey out buttons. Everything here runs from
``draw()`` on every mouse move, so it counts (cheap, in any mode) and names
only in object mode, where the selection has been flushed from the BMesh.
"""
from __future__ import annotations


import bpy

from ..link import sync
from ..viewport import marks
from ..operators.edits import enough, needed


def _class_of(idname: str):
    """The operator class behind an idname such as ``cadcore.pocket_face``.

    Looked up through `bpy.types` so this module does not import :mod:`ops`.
    """
    head, _, tail = idname.partition(".")
    return getattr(bpy.types, "%s_OT_%s" % (head.upper(), tail), None)


def counts(context) -> tuple:
    """(picked faces, picked edges) -- edges count only when no face is picked."""
    if sync.body() is None:
        return 0, 0
    faces, pairs = sync.picked()
    return len(faces), (0 if faces else len(pairs))




def names(context, limit: int = 4) -> list:
    """The CAD names of the picked faces, at most ``limit`` + 1 of them."""
    if sync.body() is None:
        return []
    return sync.selected_face_names()[:limit + 1]
# what the kernel said about a face is kept in viewport/marks.py, the leaf
# both the overlay and link/state read, and dropped there on every build
_shapes = marks.FACE_SHAPES
_frames = marks.FACE_FRAMES


def face_frames(context, names: list) -> list:
    """The frames of the named faces, or [] if any of them is not flat."""
    from ..link.state import get_client
    from ..link.client import ServerError

    depth = int(context.scene.cadcore.undo_depth)
    out = []
    for name in names:
        key = (name, depth)
        if key not in _frames:
            if len(_frames) > 16:
                _frames.clear()
            try:
                _frames[key] = get_client(context).call("face_frame", face=name)
            except ServerError:
                _frames[key] = None          # curved: it has no one frame
        if _frames[key] is None:
            return []
        out.append(_frames[key])
    return out


def parallel(context, names: list) -> bool:
    """Whether the two named faces are flat and face the same way, or opposite.

    A midplane between faces that meet at an angle has no midway to sit at,
    so the menu does not offer one.
    """
    frames = face_frames(context, names) if len(names) == 2 else []
    if len(frames) != 2:
        return False
    a, b = (f["normal"] for f in frames)
    return abs(sum(a[i] * b[i] for i in range(3))) > 0.999


def face_shape(context, names: list) -> str | None:
    """What kind of surface the one named face is ("plane", "cylinder", ...), asked of the kernel."""
    if len(names) != 1:
        return None
    from ..link.state import get_client
    key = (names[0], int(context.scene.cadcore.undo_depth))
    if key not in _shapes:
        try:
            faces = get_client(context).call("describe_faces", query={"of_face": names[0]})["faces"]
            _shapes.clear()
            _shapes[key] = faces[0].get("shape") if faces else None
        except Exception:                                           # noqa: BLE001
            return None
    return _shapes[key]


def blocked(idname: str, picked: tuple) -> str | None:
    """Why this tool cannot run with the current selection, or None if it can.

    Read from the operator's ``wants``/``picks``; an operator declaring
    neither is always available.
    """
    kind = _class_of(idname)
    wants = getattr(kind, "wants", None) if kind else None
    if wants is None:
        return None
    picks = getattr(kind, "picks", "faces")
    have = picked[1] if picks == "edges" else picked[0]
    # An edge tool also accepts faces; the kernel resolves the edges between
    # them.
    if picks == "edges" and not have:
        have = picked[0]
    return None if enough(wants, have) else needed(wants, picks)


def summary(picked: tuple) -> str:
    """The picked counts in words, e.g. "2 faces · 1 edge"."""
    faces, edges = picked
    parts = []
    if faces:
        parts.append("%d face%s" % (faces, "" if faces == 1 else "s"))
    if edges:
        parts.append("%d edge%s" % (edges, "" if edges == 1 else "s"))
    return " · ".join(parts) if parts else "nothing picked"
