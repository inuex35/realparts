"""The geometry behind drawing on a face, free of Blender so it can be tested headlessly.

Projects the cursor onto the sketch plane, snaps to what is already drawn,
and decides when a chain has closed.
"""
from __future__ import annotations

import math


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def y_axis(frame: dict):
    return cross(frame["normal"], frame["x_axis"])


def ray_plane(origin, direction, frame: dict):
    """Where a view ray meets the sketch plane, or None if it runs parallel."""
    n = frame["normal"]
    denominator = sum(direction[i] * n[i] for i in range(3))
    if abs(denominator) < 1e-9:
        return None
    o = frame["origin"]
    t = sum((o[i] - origin[i]) * n[i] for i in range(3)) / denominator
    return tuple(origin[i] + direction[i] * t for i in range(3))


def to_uv(point, frame: dict):
    """A 3D point in the face's (u, v) coordinates."""
    o, x, y = frame["origin"], frame["x_axis"], y_axis(frame)
    d = [point[i] - o[i] for i in range(3)]
    return (sum(d[i] * x[i] for i in range(3)), sum(d[i] * y[i] for i in range(3)))


def to_3d(uv, frame: dict):
    o, x, y = frame["origin"], frame["x_axis"], y_axis(frame)
    return tuple(o[i] + x[i] * uv[0] + y[i] * uv[1] for i in range(3))


def snap(uv, points: list, previous=None, radius: float = 1.5,
         axis_tolerance: float = 1.5, anchors=()):
    """Snap a drawn point: onto one already drawn, onto one the part already
    has, then into horizontal or vertical alignment with `previous`, else the
    raw position.

    `anchors` are the corners of the face and the centres of the holes through
    it -- what a person is reaching for when they draw on something that is
    already there. Returns `(uv, hint)`; the hint's `kind` names which applied.
    """
    for i, p in enumerate(points):
        if math.dist(uv, p) <= radius:
            return tuple(p), {"kind": "point", "index": i}
    for p in anchors:
        if math.dist(uv, p) <= radius:
            return tuple(p), {"kind": "on_part"}
    if previous is not None:
        du, dv = uv[0] - previous[0], uv[1] - previous[1]
        if abs(dv) <= axis_tolerance and abs(du) > abs(dv):
            return (uv[0], previous[1]), {"kind": "horizontal"}
        if abs(du) <= axis_tolerance and abs(dv) > abs(du):
            return (previous[0], uv[1]), {"kind": "vertical"}
    return tuple(uv), {"kind": "free"}


def closes_loop(uv, points: list, radius: float = 1.5) -> bool:
    return len(points) >= 3 and math.dist(uv, points[0]) <= radius


def polyline(points: list, closed: bool = False, skip=()) -> list:
    """Line segments through the points, as pairs of indices.

    ``skip`` names indices the chain does not pass through (circle centres
    share the point list).
    """
    chain = [i for i in range(len(points)) if i not in set(skip)]
    pairs = [[a, b] for a, b in zip(chain, chain[1:])]
    if closed and len(chain) >= 3:
        pairs.append([chain[-1], chain[0]])
    return pairs


def circle_points(centre, radius: float, segments: int = 48) -> list:
    return [(centre[0] + radius * math.cos(2 * math.pi * k / segments),
             centre[1] + radius * math.sin(2 * math.pi * k / segments))
            for k in range(segments + 1)]


def polygon_points(centre, rim, sides: int) -> list:
    """The vertices of a regular polygon centred on ``centre`` with one vertex
    at ``rim``, wound anticlockwise. ``sides`` is clamped to at least three, so
    a stray value never makes a degenerate loop."""
    sides = max(3, int(sides))
    cx, cy = centre
    a0 = math.atan2(rim[1] - cy, rim[0] - cx)
    radius = math.dist(centre, rim)
    return [(cx + radius * math.cos(a0 + 2 * math.pi * k / sides),
             cy + radius * math.sin(a0 + 2 * math.pi * k / sides))
            for k in range(sides)]


def ellipse_points(centre, rx: float, ry: float, segments: int = 64) -> list:
    """A closed loop tracing the axis-aligned ellipse of half-widths ``rx`` and
    ``ry`` about ``centre``, for previewing before the kernel makes the real one."""
    return [(centre[0] + rx * math.cos(2 * math.pi * k / segments),
             centre[1] + ry * math.sin(2 * math.pi * k / segments))
            for k in range(segments + 1)]


def point_to_line(p, a, b) -> float:
    """The perpendicular distance from ``p`` to the line through ``a`` and
    ``b`` (zero when ``a`` and ``b`` coincide)."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length < 1e-12:
        return math.dist(p, a)
    return abs((px - ax) * dy - (py - ay) * dx) / length


def slot_outline(a, b, width: float, segments: int = 24) -> list:
    """A closed loop tracing a slot (obround): the capsule of radius
    ``width`` / 2 around the centreline ``a``-``b``. For preview only; the
    kernel builds the exact slot."""
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length < 1e-12:
        return circle_points(a, width / 2, segments)
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    r = width / 2
    pts = []
    # cap around b, sweeping from the left side to the right
    base_b = math.atan2(ny, nx)
    for k in range(segments + 1):
        t = base_b - math.pi * k / segments
        pts.append((bx + r * math.cos(t), by + r * math.sin(t)))
    # cap around a, the other way
    base_a = math.atan2(-ny, -nx)
    for k in range(segments + 1):
        t = base_a - math.pi * k / segments
        pts.append((ax + r * math.cos(t), ay + r * math.sin(t)))
    pts.append(pts[0])
    return pts


def depth_from_drag(base: float, pixels: float, mm_per_pixel: float,
                    step: float = 0.5, low: float = 0.01) -> float:
    """The depth a closed profile is given while the mouse adjusts it.

    `pixels` is how far the cursor has moved up the screen since the loop
    closed; up is deeper, whichever way the feature goes. The result snaps to
    `step` and never falls below `low`: a zero-depth feature does not build.
    """
    value = base + pixels * mm_per_pixel
    if step > 0:
        value = round(value / step) * step
    return max(low, value)


def rectangle_points(a, b) -> list:
    """The four corners of the axis-aligned rectangle with `a` and `b` at
    opposite corners, in the sketch's own (u, v) frame, wound anticlockwise.

    Two clicks make a rectangle: the corner and the corner across from it.
    Drawing four separate lines for the commonest shape there is was the step
    the pen most often asked for and least needed.
    """
    (ua, va), (ub, vb) = a, b
    return [(ua, va), (ub, va), (ub, vb), (ua, vb)]
