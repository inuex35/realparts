"""Turning solved segments into loops: which is the outline, which are holes.

A sketch is a set of named segments, not an ordered path, so the loops have to
be found rather than assumed -- and the largest one is the outline, which is how
a plate with a bolt pattern is one sketch rather than five features.
"""
from __future__ import annotations

import math

from ..errors import CadError
from .model import Segment


def _loops(ends: dict, segments: dict, open_path: bool = False) -> list:
    """Walk the segments into closed loops, outline first.

    More than one loop is not an error any more: a plate with four bolt holes is
    one sketch with five loops, and the extrusion turns the rest into holes. The
    outline is the loop with the largest area, which is the only reading that
    survives someone drawing the holes first.
    """
    by_point: dict[str, list] = {}
    for name, (a, b) in ends.items():
        by_point.setdefault(a, []).append(name)
        by_point.setdefault(b, []).append(name)
    bad = [p for p, segs in by_point.items() if len(segs) != 2]
    if open_path:
        # a sweep path is a chain, not a loop: exactly two loose ends
        if len(bad) != 2 or any(len(by_point[p]) != 1 for p in bad):
            raise CadError("open_profile", "an open sketch must be one unbranched chain",
                           {"loose_ends": bad[:8]})
        return [_chain(ends, segments, by_point, bad[0])]
    if bad:
        raise CadError("open_profile", "the sketch does not form closed loops",
                       {"points_with_wrong_valence": bad[:8],
                        "hint": "every point must join exactly two segments"})

    loops: list[list[Segment]] = []
    unused = dict(ends)
    while unused:
        first = next(iter(unused))
        a0, cur = unused.pop(first)
        loop = [segments[first]]
        while cur != a0:
            nxt = next((s for s in by_point[cur] if s in unused), None)
            if nxt is None:
                raise CadError("open_profile", "the segments do not close",
                               {"stopped_at": cur})
            a, b = unused.pop(nxt)
            seg = segments[nxt]
            if a != cur:                       # the walk crosses this one backwards
                seg = _reversed(seg)
                a, b = b, a
            loop.append(seg)
            cur = b
        loops.append(loop)

    for name, seg in segments.items():
        if seg.kind in ("circle", "ellipse"):
            loops.append([seg])
    if not loops:
        raise CadError("empty_sketch", "a sketch needs at least one closed loop")
    loops.sort(key=_area, reverse=True)
    return loops


def _chain(ends: dict, segments: dict, by_point: dict, start: str) -> list:
    """Walk an open sketch from one loose end to the other."""
    chain, unused, cur = [], dict(ends), start
    while True:
        nxt = next((s for s in by_point.get(cur, []) if s in unused), None)
        if nxt is None:
            break
        a, b = unused.pop(nxt)
        seg = segments[nxt]
        if a != cur:
            seg = _reversed(seg)
            a, b = b, a
        chain.append(seg)
        cur = b
    if unused:
        raise CadError("open_profile", "the path does not run end to end",
                       {"unreached": sorted(unused)})
    return chain


def _reversed(seg: Segment) -> Segment:
    return Segment(seg.name, seg.kind, seg.end, seg.start, seg.centre, seg.radius,
                   not seg.ccw, list(reversed(seg.through)))


def _area(loop: list) -> float:
    if len(loop) == 1 and loop[0].kind == "circle":
        return math.pi * loop[0].radius ** 2
    if len(loop) == 1 and loop[0].kind == "ellipse":
        return math.pi * loop[0].radius * loop[0].minor
    pts = []
    for seg in loop:
        if seg.kind in ("arc", "circle", "ellipse"):
            pts.extend(seg.sample()[:-1])       # walked, so a ring's hole is not the outline
        elif seg.kind == "spline":
            pts.append(seg.start)
            pts.extend(seg.through[1:-1])
        else:
            pts.append(seg.start)
    total = 0.0
    for i, (x0, y0) in enumerate(pts):
        x1, y1 = pts[(i + 1) % len(pts)]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2
