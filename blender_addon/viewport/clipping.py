"""The plane a section view cuts on, free of Blender so it can be tested.

Blender keeps what satisfies ``a*x + b*y + c*z + d <= 0``, so a clip plane's
normal points at the side that goes away.
"""
from __future__ import annotations

import math


def unit(vector) -> tuple:
    """`vector` at length one; a vector of no length comes back as +Z."""
    length = math.sqrt(sum(v * v for v in vector))
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return tuple(v / length for v in vector)


def plane(frame: dict, offset: float = 0.0, flip: bool = False) -> tuple:
    """The clip plane at a face or work plane, as `(a, b, c, d)`.

    `offset` slides it along the face normal, positive the way the normal
    points. The side the normal points at is the side that goes away, so at
    offset 0 the cut is the face itself and what the face looks at is gone;
    `flip` keeps that side and drops the other.
    """
    normal = unit(frame["normal"])
    at = tuple(o + offset * n for o, n in zip(frame["origin"], normal))
    if flip:
        normal = tuple(-n for n in normal)
    return normal + (-sum(a * b for a, b in zip(normal, at)),)


def keeps(cut: tuple, point) -> bool:
    """Whether `point` survives the clip."""
    return sum(a * b for a, b in zip(cut, point)) + cut[3] <= 0.0
