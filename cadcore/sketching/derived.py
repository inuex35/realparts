"""Geometry a sketch derives from its own segments: offsets, trims, slots.

Each of these is *a relationship written down*, not a copy: an offset follows
the edge it was offset from, a trim stays a trim of the two segments that made
it, and a slot is one named thing rather than four. The alternative -- drawing
the result -- is how a sketch stops meaning anything when a dimension moves.
"""
from __future__ import annotations

import math

from .. import names
from ..errors import CadError


def _add_offset(gcs, name: str, spec: dict, pid: dict, lid: dict, ends: dict,
                defined: dict, start: dict, evaluate) -> None:
    """A line held parallel to another at a distance -- an offset that follows.

    Written as constraints rather than as copied geometry on purpose: the offset
    is *the relationship*, so moving the source line moves this one, and the
    distance is a parameter the panel can drive. A copied segment would be right
    once.

    The two links are constraint scaffolding, not profile: they are given to the
    solver but never enter the loop walk, so they cannot end up as faces.
    """
    source = spec.get("of")
    if source not in lid:
        raise CadError("unknown_line", f"offset {name!r} has no line {source!r}",
                       {"known": sorted(lid)})
    distance = float(evaluate(spec["distance"]))
    if abs(distance) < 1e-9:
        raise CadError("bad_parameter", f"offset {name!r} needs a non-zero distance")

    a, b = defined[source]
    (ax, ay), (bx, by) = start[a], start[b]
    length = math.hypot(bx - ax, by - ay)
    if length < 1e-9:
        raise CadError("degenerate_segment", f"cannot offset {source!r}: it has no length")
    side = -1.0 if str(spec.get("side", "left")).lower() == "right" else 1.0
    nx, ny = -(by - ay) / length * side, (bx - ax) / length * side

    moved = {}
    for label, (px, py) in (("a", (ax, ay)), ("b", (bx, by))):
        key = f"{name}_{label}"
        moved[label] = key
        start[key] = (px + nx * distance, py + ny * distance)
        pid[key] = gcs.add_point(*start[key])

    lid[name] = gcs.add_line(pid[moved["a"]], pid[moved["b"]])
    ends[name] = (moved["a"], moved["b"])
    defined[name] = ends[name]
    for label, anchor in (("a", a), ("b", b)):
        link = gcs.add_line(pid[anchor], pid[moved[label]])
        gcs.perpendicular(link, lid[source])
        gcs.p2p_distance(pid[anchor], pid[moved[label]],
                         gcs.add_param(abs(distance), fixed=True))


def _add_trim(gcs, name: str, spec: dict, pid: dict, lid: dict, ends: dict,
              defined: dict, start: dict) -> None:
    """Cut a line where another crosses it, and keep one side.

    The cut point is *constrained* onto both lines rather than computed once, so
    the trim re-answers itself when either line moves. The kept piece is named
    the way the 3D side names a face a boolean split -- ``west@0`` -- because it
    is the same event, and a reference written against it should read the same.

    The original line stays in the solver as the carrier of its constraints; it
    just leaves the profile.
    """
    source = spec.get("of") or name
    cutter = spec.get("at")
    for label, line in (("of", source), ("at", cutter)):
        if line not in lid:
            raise CadError("unknown_line", f"trim {name!r} has no line {line!r} to {label}",
                           {"known": sorted(lid)})
    keep = str(spec.get("keep", "start")).lower()
    if keep not in ("start", "end"):
        raise CadError("bad_arguments", "keep must be 'start' or 'end'", {"given": keep})

    a, b = defined[source]
    c, d = defined[cutter]
    crossing = _intersection(start[a], start[b], start[c], start[d])
    if crossing is None:
        raise CadError("no_intersection",
                       f"{source!r} and {cutter!r} do not cross",
                       {"hint": "parallel lines cannot trim each other"})

    # one crossing, one point: trimming A at B and B at A meet at the same
    # place, and if each made its own point the profile would never close
    key = "x_" + "_".join(sorted([source, cutter]))
    if key not in pid:
        start[key] = crossing
        pid[key] = gcs.add_point(*crossing)
        gcs.point_on_line(pid[key], lid[source])
        gcs.point_on_line(pid[key], lid[cutter])

    kept = names.piece(source, 0 if keep == "start" else 1)
    first, second = (a, key) if keep == "start" else (key, b)
    lid[kept] = gcs.add_line(pid[first], pid[second])
    ends[kept] = defined[kept] = (first, second)
    ends.pop(source, None)               # still a constraint carrier, no longer profile


def _intersection(a, b, c, d):
    """Where two lines cross, from where they were drawn."""
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    denominator = r[0] * s[1] - r[1] * s[0]
    if abs(denominator) < 1e-12:
        return None
    t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / denominator
    return (a[0] + r[0] * t, a[1] + r[1] * t)


def _add_slot(gcs, name: str, spec: dict, pid: dict, lid: dict, aid: dict, ends: dict,
              arcs: dict, start: dict, evaluate) -> None:
    """A slot, written as what it is: a centreline and a width.

    Drawn by hand it is four segments and six constraints, every time, and the
    four names that come out of it (``name/left`` and so on) are what a later
    fillet or a pocket wall has to be written against.
    """
    a, b = spec.get("from"), spec.get("to")
    for p in (a, b):
        if p not in start:
            raise CadError("unknown_point", f"slot {name!r} refers to point {p!r}",
                           {"known": sorted(start)})
    width = float(evaluate(spec["width"]))
    if width <= 0:
        raise CadError("bad_parameter", f"slot {name!r} needs a positive width")
    radius = width / 2

    ax, ay = start[a]
    bx, by = start[b]
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length < 1e-9:
        raise CadError("degenerate_segment", f"slot {name!r} has no length")
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux                       # left of the centreline

    corners = {}
    for label, (px, py), sign in (("al", (ax, ay), 1), ("ar", (ax, ay), -1),
                                  ("bl", (bx, by), 1), ("br", (bx, by), -1)):
        key = f"{name}_{label}"
        corners[label] = key
        start[key] = (px + nx * radius * sign, py + ny * radius * sign)
        pid[key] = gcs.add_point(*start[key])

    lid[f"{name}/left"] = gcs.add_line(pid[corners["al"]], pid[corners["bl"]])
    ends[f"{name}/left"] = (corners["al"], corners["bl"])
    lid[f"{name}/right"] = gcs.add_line(pid[corners["br"]], pid[corners["ar"]])
    ends[f"{name}/right"] = (corners["br"], corners["ar"])

    for label, centre, first, second, bulge in (
            ("end", b, corners["bl"], corners["br"], (ux, uy)),
            ("start", a, corners["ar"], corners["al"], (-ux, -uy))):
        rp = gcs.add_param(radius)
        t0 = math.atan2(start[first][1] - start[centre][1],
                        start[first][0] - start[centre][0])
        t1 = math.atan2(start[second][1] - start[centre][1],
                        start[second][0] - start[centre][0])
        # the end has to bulge *away* from the slot: pick the sweep that passes
        # through the outward direction, rather than assuming counterclockwise
        outward = math.atan2(bulge[1], bulge[0])
        sweep = (t1 - t0) % (2 * math.pi)
        ccw = ((outward - t0) % (2 * math.pi)) < sweep
        t1 = t0 + sweep if ccw else t0 - (2 * math.pi - sweep)
        arc = gcs.add_arc(pid[centre], pid[first], pid[second], rp,
                          gcs.add_param(t0), gcs.add_param(t1))
        key = f"{name}/{label}"
        aid[key] = arc
        arcs[key] = {"centre": centre, "ccw": ccw, "radius_param": rp}
        ends[key] = (first, second)
        gcs.arc_radius(arc, gcs.add_param(radius, fixed=True))
    # the sides run tangent to both ends: that is what makes it a slot rather
    # than four segments that happen to meet
    for side in (f"{name}/left", f"{name}/right"):
        for end in (f"{name}/start", f"{name}/end"):
            gcs.tangent_line_arc(lid[side], aid[end])
