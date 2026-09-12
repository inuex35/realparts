"""How far a feature goes, when that is a relationship rather than a number.

"Through all", "to the next face" and "up to that plane" are re-answered on
every rebuild, so they still hold after the part changes. These functions
take the evaluator as an argument rather than living on it.
"""
from __future__ import annotations

from ...geometry import kernel
from ...errors import CadError
from ...geometry.core.naming import Body


def extent(graph, f, a, ev, sketch, into: bool, body: Body | None = None, direction=None) -> tuple:
    """Resolve a depth or an end condition into a signed distance and the sketch to start from.

    ``into`` sends the feature against the sketch normal; the distance is
    then returned negative.
    """
    from dataclasses import replace

    normal = sketch.plane.normal
    sideways = direction is not None        # a rib: it runs in its plane, from its profile
    if direction is None:
        direction = [-c for c in normal] if into else list(normal)
    until = a.get("until")

    if until is None:
        given = a.get("depth", a.get("distance"))
        if given is None:
            raise CadError("missing_argument",
                           f"{f.id}: needs a depth, a distance, or an until",
                           {"feature": f.id})
        distance = float(ev(given))
    elif until == "through_all":
        if body is None:
            raise CadError("bad_arguments",
                           "'through all' needs a body to go through",
                           {"feature": f.id})
        distance = kernel.bounding_span(body) * 1.2
    elif until == "next":
        if body is None:
            raise CadError("bad_arguments", "'to next' needs a body to stop at",
                           {"feature": f.id})
        distance = reach_next(sketch, body, direction)
    elif isinstance(until, dict) and "face" in until:
        reference = graph.body_of(until.get("body")) if until.get("body") else body
        if reference is None:
            raise CadError("bad_arguments", "'to face' needs a body",
                           {"feature": f.id})
        frame = kernel.face_frame(reference, until["face"])
        distance = reach_plane(sketch, frame, direction, sideways)
    elif isinstance(until, dict) and "plane" in until:
        distance = reach_plane(sketch, graph.plane_of(until["plane"]), direction, sideways)
    else:
        raise CadError("unknown_end_condition",
                       f"end condition {until!r} is not implemented",
                       {"available": ["through_all", "next",
                                      {"face": "<name>"}, {"plane": "<id>"}]})

    if distance <= 1e-9:
        raise CadError("empty_result", "the sweep would have no length",
                       {"feature": f.id, "until": until})
    if a.get("symmetric"):
        # start half a depth back, so the feature straddles its sketch
        origin = [sketch.plane.origin[i] - direction[i] * distance / 2
                  for i in range(3)]
        sketch = replace(sketch, plane=replace(sketch.plane, origin=tuple(origin)))
    return (-distance if into else distance), sketch


def reach_plane(sketch, frame: dict, direction, from_profile: bool = False) -> float:
    """The distance forward to a face or plane; one behind the sketch is refused.

    Measured from the sketch plane's origin, or with ``from_profile`` from the
    profile's own points: a rib runs in its plane, so the plane's origin says
    nothing about how far it has to go.

    The absolute value is not taken: it would extrude away from a face behind
    the sketch by the right amount in the wrong direction. A face that is not
    square to the direction is refused too: the end of the sweep is flat, and
    a flat end cannot lie in a tilted plane.
    """
    normal = frame.get("normal")
    if normal is not None and abs(sum(normal[i] * direction[i] for i in range(3))) < 1 - 1e-6:
        raise CadError("nothing_to_stop_at",
                       "the face to stop at is not parallel to the sketch",
                       {"hint": "a sweep ends flat, so it can only stop at a face "
                                "square to its direction; give a depth instead"})
    starts = [sketch.plane.to_3d(*uv) for loop in sketch.loops for seg in loop
              for uv in (seg.start, seg.end)] if from_profile else []
    reach = max(sum((frame["origin"][i] - p[i]) * direction[i] for i in range(3))
                for p in starts or [sketch.plane.origin])
    if reach <= 1e-9:
        raise CadError("nothing_to_stop_at",
                       "the face to stop at is behind the sketch",
                       {"hint": "a feature only goes one way; flip the sketch or "
                                "pick a face in front of it"})
    return reach


def reach_next(sketch, body: Body, direction) -> float:
    """Distance to the first face the profile meets, sampled over the whole
    profile because the nearest face under any corner is what stops the cut."""
    hits = []
    for loop in sketch.loops:
        for segment in loop:
            for uv in (segment.start, segment.mid()):
                point = sketch.plane.to_3d(*uv)
                start = [point[i] + direction[i] * 1e-3 for i in range(3)]
                hit = kernel.first_hit(body.shape, start, direction, 1e-3, 0.2)
                if hit is not None:
                    hits.append(hit)
    if not hits:
        raise CadError("nothing_to_stop_at",
                       "'to next' found no face in that direction",
                       {"hint": "use through_all, or a depth"})
    return min(hits) + 1e-3


def along_path(sketch, count: int) -> list:
    """Points at equal arc length along a sketch, in 3D."""
    from ...geometry.solids.sweeps import points_along

    if count < 2:
        raise CadError("bad_parameter", "a pattern needs at least two instances",
                       {"count": count})
    return points_along(sketch, count)
