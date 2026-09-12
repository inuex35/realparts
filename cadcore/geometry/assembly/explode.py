"""The exploded view: each part moved out along the axis its mates hold it by."""
from __future__ import annotations

import math

from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box

from .assembly import mate_geometry
from ..core.naming import Body
from ..core.occ import bounds

#: the kinds whose axis a part comes out along; the rest name a flat face
AXIAL = ("concentric", "hinge", "slider", "screw", "gear", "tangent", "distance")


def offsets(parts: dict, mates: list, ground: str | None = None,
            factor: float = 1.0) -> dict:
    """An offset (mm, world) per part that takes the assembly apart.

    A part moves along the axis or the normal of the first mate that holds it,
    away from the part it is mated to, by ``factor`` times its own extent that
    way; a part no mate names moves away from the middle of the assembly.
    The ground, or the first part, stays.
    """
    boxes = {name: _box(body) for name, body in parts.items()}
    centres = {name: [(b[i] + b[i + 3]) / 2 for i in range(3)] for name, b in boxes.items()}
    middle = [sum(c[i] for c in centres.values()) / len(centres) for i in range(3)]
    ground = ground if ground in parts else next(iter(parts))
    ways = {}
    for name, body in parts.items():
        if name == ground:
            continue
        direction = _held_direction(name, body, parts, mates, centres)
        if direction is None:
            direction = [centres[name][i] - middle[i] for i in range(3)]
            if _norm(direction) < 1e-6:
                direction = [0.0, 0.0, 1.0]
            direction = _unit(direction)
        ways[name] = direction
    out = {ground: [0.0, 0.0, 0.0]}
    # parts stacked the same way move in step, the farthest out farthest,
    # so a pile of links comes apart instead of moving as one
    stacks: dict = {}
    for name, direction in ways.items():
        stacks.setdefault(tuple(round(c, 3) for c in direction), []).append(name)
    for direction, names in stacks.items():
        along = lambda n: sum(centres[n][i] * direction[i] for i in range(3))   # noqa: E731
        so_far = 0.0
        for name in sorted(names, key=along):
            so_far += _extent(boxes[name], direction) * factor
            out[name] = [round(direction[i] * so_far, 4) + 0.0 for i in range(3)]
    return out


def _held_direction(name: str, body: Body, parts: dict, mates: list, centres: dict):
    """The way out along the first mate that names a face of this part."""
    for mate in mates:
        faces = mate.get("faces") or []
        if len(faces) != 2:
            continue
        mine = next((face for face in faces if body.face(face) is not None), None)
        if mine is None:
            continue
        other_face = faces[1] if mine == faces[0] else faces[0]
        other = next((n for n, b in parts.items() if n != name and b.face(other_face) is not None),
                     None)
        g = mate_geometry(body, mine)
        direction = g.get("direction")
        if direction is None:
            continue
        direction = _unit(direction)
        if other is not None:
            away = [centres[name][i] - centres[other][i] for i in range(3)]
            if sum(away[i] * direction[i] for i in range(3)) < 0:
                direction = [-c for c in direction]
        elif g["shape"] == "plane":
            direction = [-c for c in direction]      # its own face points at the other part
        return direction
    return None


def _box(body: Body) -> tuple:
    box = Bnd_Box()
    BRepBndLib.Add_s(body.shape, box)
    return bounds(box)


def _extent(box: tuple, direction) -> float:
    """How long the box is along a direction."""
    return sum(abs(direction[i]) * (box[i + 3] - box[i]) for i in range(3))


def _norm(v) -> float:
    return math.sqrt(sum(c * c for c in v))


def _unit(v) -> list:
    length = _norm(v) or 1.0
    return [c / length for c in v]
