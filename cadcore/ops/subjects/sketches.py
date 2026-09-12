"""Sketches: reading one, dragging it, and editing its constraints.

The constraints are the design intent, so they are first-class here rather than
something to be edited by rewriting the document -- and the freedom of each
point is answered by the same solver that a drag would use, so what the viewport
draws and what a drag does cannot disagree.
"""
from __future__ import annotations

import math

from ...sketching.autodim import dimension, draft_spec
from ...model.document import Document, Feature, names_in
from ... import names
from ...errors import CadError
from ...geometry.core.measure import face_frame
from ...sketching import extent as sketch_extent
from ...sketching import solve as solve_sketch
from ...model import authoring


def _closed(points: dict) -> dict:
    """The points in order, joined into a closed loop."""
    keys = list(points)
    return {"points": points,
            "lines": {"s%d" % i: [keys[i], keys[(i + 1) % len(keys)]]
                      for i in range(len(keys))}}


def _rect(size: dict) -> dict:
    w, h = size["width"] / 2, size["height"] / 2
    return _closed({"a": (-w, -h), "b": (w, -h), "c": (w, h), "d": (-w, h)})


def _circle(size: dict) -> dict:
    return {"points": {"o": (0.0, 0.0)},
            "circles": {"round": {"centre": "o", "radius": size["radius"]}}}


def _slot(size: dict) -> dict:
    """A slot as the sketch language spells one: a centreline and a width."""
    half = size["length"] / 2
    return {"points": {"a": (-half, 0.0), "b": (half, 0.0)},
            "slots": {"way": {"from": "a", "to": "b", "width": size["radius"] * 2}}}


def _ellipse(size: dict) -> dict:
    return {"points": {"o": (0.0, 0.0)},
            "ellipses": {"oval": {"centre": "o", "major": size["width"] / 2,
                                  "minor": size["height"] / 2}}}


def _text(size: dict) -> dict:
    return {"points": {}, "texts": {"words": {"text": size["text"], "at": [0, 0],
                                              "height": size["height"]}}}


def _polygon(size: dict) -> dict:
    import math

    sides = max(3, int(size["sides"]))
    radius = size["radius"]
    return _closed({"p%d" % i: (radius * math.cos(2 * math.pi * i / sides),
                                radius * math.sin(2 * math.pi * i / sides))
                    for i in range(sides)})


class SketchOps:
    """Mixed into :class:`cadcore.ops.session.Session`."""

    def op_add_profile(self, shape: str, face: str | None = None,
                       plane: str | None = None, at=(0.0, 0.0),
                       width: float = 20.0, height: float = 10.0,
                       radius: float = 5.0, sides: int = 6,
                       length: float = 20.0, rotation: float = 0.0,
                       text: str | None = None, feature_id: str | None = None) -> dict:
        """A shape anybody draws, as one call: rect, circle, slot, polygon, ellipse or text.

        ``shape``     ``rect``, ``circle``, ``slot``, ``polygon``, ``ellipse`` (width by
                      height) or ``text`` (``text`` at font size ``height``)
        ``face``      a named face to draw on, or ``plane`` for a work plane
        ``at``        where its centre goes, in the face's own frame
        ``rotation``  degrees, about the face's normal

        `add_sketch` takes points, segments and constraints, which is the right
        interface for a mouse and a long way round for "a 20 by 10 rectangle
        there". Four shapes cover most of what a part is made of, and each one
        arrives fully constrained -- so the thing it makes can still be edited
        by its dimensions afterwards rather than by moving points.

        Makes the sketch; extrude, pocket or revolve it with the feature that
        wants it.
        """
        import math

        doc = self._require_doc()
        makers = {"rect": _rect, "circle": _circle, "slot": _slot,
                  "polygon": _polygon, "ellipse": _ellipse, "text": _text}
        if shape == "text" and not text:
            raise CadError("bad_arguments", "a text profile needs the text", {"shape": shape})
        if shape not in makers:
            raise CadError("bad_arguments",
                           f"a profile is one of: {', '.join(sorted(makers))}",
                           {"given": shape})
        if (face is None) == (plane is None):
            raise CadError("bad_arguments",
                           "a profile is drawn on a face or on a plane, and one "
                           "of the two has to be said",
                           {"face": face, "plane": plane})
        sizes = {"width": float(width), "height": float(height),
                 "radius": float(radius), "sides": int(sides),
                 "length": float(length), "text": text}
        drawn = makers[shape](sizes)
        turn = math.radians(float(rotation))
        cx, cy = float(at[0]), float(at[1])
        points = {name: [round(cx + x * math.cos(turn) - y * math.sin(turn), 6),
                         round(cy + x * math.sin(turn) + y * math.cos(turn), 6)]
                  for name, (x, y) in drawn["points"].items()}

        args = {"points": points, "allow_underconstrained": True}
        args.update({k: v for k, v in drawn.items() if k != "points"})
        if shape == "text":
            args["texts"]["words"].update({"at": [round(cx, 6), round(cy, 6)],
                                           "angle": float(rotation)})
            args.pop("points")
        # a face belongs to a body, and which body is not something to guess
        # from the name: the sketch feature resolves the frame against the body
        # as it is at that point in the chain
        args["on"] = ({"body": doc.result or doc.features[-1].id, "face": face}
                      if face else {"plane": plane})
        fid = feature_id or authoring.fresh_id(doc, shape)
        with self._edit():
            doc.features.append(Feature(fid, "sketch", args))
            out = self._rebuild()
        out["feature"] = fid
        out["profile"] = shape
        return out

    def op_profile_shapes(self) -> dict:
        """What `add_profile` can draw, so a caller never guesses at the word."""
        return {"shapes": ["circle", "ellipse", "polygon", "rect", "slot", "text"]}

    def op_add_sketch(self, face: str | None = None, points: list | None = None,
                      lines: list | None = None, circles: list | None = None,
                      operation: str = "pocket", depth: float = 5.0,
                      feature_id: str | None = None, until: str | dict | None = None,
                      symmetric: bool = False, plane: str | None = None,
                      arcs: list | None = None, ellipses: list | None = None,
                      slots: list | None = None) -> dict:
        """Take a sketch drawn on a face and make it a dimensioned feature.

        The drawing arrives as points and segments in the face's own frame. What
        goes into the document is not those coordinates but the intent read from
        them: near-axis segments become horizontal and vertical constraints, and
        lengths become named dimensions, added only while the sketch still has
        freedom left. Draw roughly, get a parametric sketch.
        """
        doc = self._require_doc()
        if operation not in ("pocket", "boss", "extrude"):
            raise CadError("bad_arguments",
                           f"operation must be pocket, boss or extrude, not {operation!r}")
        if (face is None) == (plane is None):
            raise CadError("bad_arguments", "a sketch is drawn on a face or on a plane, "
                                            "and one of the two has to be said")
        if plane is not None:
            frame = self._plane_frame(plane)
        else:
            self._ensure_body()
            frame = face_frame(self.body, face)
        points = points or []
        curved = bool(circles or arcs or ellipses or slots)
        if len(points) < 2 and not curved:
            # one circle is a whole profile on its own -- its only point is
            # the centre, and refusing it broke the viewport's simplest gesture
            raise CadError("empty_sketch", "a drawn sketch needs at least two points")

        fid = feature_id or authoring.fresh_id(doc, operation)
        frame_plane = {k: frame[k] for k in ("origin", "normal", "x_axis")}
        spec, order = draft_spec(points, lines or [], circles or [], frame_plane,
                                 arcs=arcs or (), ellipses=ellipses or (),
                                 slots=slots or ())

        def evaluate(value, extra):
            return Document(parameters={**doc.parameters, **extra}).evaluate(value)

        spec, dimensions, dof = dimension(spec, order, fid, solve_sketch, evaluate)
        # a curved primitive is placed whole and left undimensioned, so leftover
        # freedom is expected there; only a pure line drawing must come out tight
        pinned = bool(arcs or ellipses or slots)
        if dof > 0 and not pinned:
            raise CadError("sketch_underconstrained",
                           f"{dof} degree(s) of freedom left after dimensioning",
                           {"hint": "the drawing may have a segment that cannot be measured"})
        if pinned:
            spec["allow_underconstrained"] = True
        return self._apply(authoring.drawn_sketch(doc, face, spec, dimensions, operation,
                                                  depth, fid, until, symmetric, plane=plane))

    def _plane_frame(self, plane: str) -> dict:
        """A work plane's frame, evaluated if it is not yet."""
        doc = self._require_doc()
        if not any(f.id == plane and f.type == "plane" for f in doc.features):
            raise CadError("unknown_feature", f"no work plane called {plane!r}",
                           {"planes": [f.id for f in doc.features if f.type == "plane"]})
        if self.evaluator is None:
            self._rebuild()
        if plane not in self.evaluator.planes:
            # a plane nothing uses yet is not an ancestor of the result, so
            # the build did not evaluate it; evaluate it on its own
            self.evaluator.prepare(plane)
        frame = self.evaluator.planes.get(plane) if self.evaluator else None
        if frame is None:
            raise CadError("not_built", f"{plane!r} was not evaluated",
                           {"hint": "it may sit after the feature the view is rolled back to"})
        return {k: list(frame[k]) for k in ("origin", "normal", "x_axis")}

    def op_planes(self) -> dict:
        """Every work plane in the document, with its frame."""
        doc = self._require_doc()
        if self.evaluator is None and doc.features:
            self._rebuild()
        if self.evaluator is not None:
            for f in doc.features:
                if f.type == "plane" and f.id not in self.evaluator.planes:
                    self.evaluator.prepare(f.id)       # not an ancestor of the result
        planes = self.evaluator.planes if self.evaluator else {}
        return {"planes": {name: {k: list(frame[k]) for k in ("origin", "normal", "x_axis")}
                           for name, frame in planes.items()}}

    def op_plane_frame(self, plane: str) -> dict:
        """One work plane's frame: origin, normal, x axis."""
        return self._plane_frame(plane)

    def op_sketch_geometry(self, sketch: str) -> dict:
        """A solved sketch, in the shape a viewport needs to draw it.

        Points and segments in the sketch's own frame, plus the frame itself, so
        the caller can put them on screen without knowing anything about how the
        sketch was solved.
        """
        doc = self._require_doc()
        feature = doc.feature(sketch)
        if feature.type != "sketch":
            raise CadError("not_a_sketch", f"{sketch!r} is a {feature.type}",
                           {"feature": sketch})
        if self.evaluator is None or sketch not in self.evaluator.sketches:
            self._rebuild()
        solved = self.evaluator.sketches.get(sketch)
        if solved is None:
            raise CadError("not_built", f"{sketch!r} was not evaluated",
                           {"hint": "it may not feed the current result"})
        segments = []
        for loop in solved.loops:
            for segment in loop:
                segments.append({"name": segment.name, "kind": segment.kind,
                                 "start": list(segment.start), "end": list(segment.end),
                                 "centre": (list(segment.centre) if segment.centre
                                            else None),
                                 "radius": segment.radius, "ccw": segment.ccw})
        return {"sketch": sketch,
                "plane": {"origin": list(solved.plane.origin),
                          "normal": list(solved.plane.normal),
                          "x_axis": list(solved.plane.x_axis)},
                "points": {name: list(uv) for name, uv in solved.points.items()},
                "segments": segments,
                "free": self._free_points(sketch, solved),
                "dof": solved.dof,
                "editable": bool(feature.args.get("allow_underconstrained")) or solved.dof > 0}

    def _free_points(self, sketch: str, solved) -> dict:
        """Which points a drag could move, probed one at a time.

        A drag moves what the constraints leave free and nothing else, so the
        viewport should be able to say which points those are *before* the
        designer pulls on one. Each point is answered by re-solving the sketch
        alone from a nudged start: land back on the old spot and the point is
        held. The solid is not rebuilt, so this costs one small solve per point.
        """
        spec = self.evaluator.sketch_specs.get(sketch) if self.evaluator else None
        if spec is None:
            return {name: True for name in solved.points}
        points = spec.get("points") or {}
        scale = max(1e-6, sketch_extent(solved.points))
        nudge, tolerance = scale * 0.02, max(1e-6, scale * 1e-4)
        doc = self._require_doc()
        free = {}
        for name, uv in solved.points.items():
            start = points.get(name)
            if start is None:                     # a point the solver derived, not one given
                free[name] = False
                continue
            moved = [doc.evaluate(start[0]) + nudge, doc.evaluate(start[1]) + nudge]
            try:
                probe = solve_sketch(dict(spec, points={**points, name: moved},
                                          allow_underconstrained=True), doc.evaluate)
            except CadError:
                free[name] = True                 # the drag itself will say what happened
                continue
            landed = probe.points.get(name)
            free[name] = landed is not None and math.dist(landed, uv) > tolerance
        return free

    def op_sketch_constraints(self, sketch: str) -> dict:
        """Every constraint on a sketch, numbered, with what it refers to."""
        feature = self._sketch_feature(sketch)
        constraints = feature.args.get("constraints") or []
        solved = self.evaluator.sketches.get(sketch) if self.evaluator else None
        return {"sketch": sketch,
                "constraints": [dict(c, index=i) for i, c in enumerate(constraints)],
                "dof": solved.dof if solved else None,
                "free": self._free_points(sketch, solved) if solved else {}}

    def op_add_constraint(self, sketch: str, constraint: dict) -> dict:
        """Add one constraint. If the sketch then will not solve, nothing happened."""
        feature = self._sketch_feature(sketch)
        if not isinstance(constraint, dict) or not constraint.get("type"):
            raise CadError("bad_arguments", "a constraint needs a type",
                           {"given": constraint})
        with self._edit():
            feature.args.setdefault("constraints", []).append(dict(constraint))
            out = self._rebuild()
        out["constraint"] = len(feature.args["constraints"]) - 1
        return out

    def op_remove_constraint(self, sketch: str, index: int) -> dict:
        """Take a constraint off -- which usually leaves the sketch free to move."""
        feature = self._sketch_feature(sketch)
        constraints = feature.args.get("constraints") or []
        if not 0 <= int(index) < len(constraints):
            raise CadError("unknown_constraint", f"the sketch has no constraint {index}",
                           {"count": len(constraints)})
        with self._edit():
            removed = constraints.pop(int(index))
            # a sketch that is now under-constrained is a legitimate state to be
            # in while editing, so say so rather than refusing the removal
            feature.args["allow_underconstrained"] = True
            out = self._rebuild()
        out["removed"] = removed
        return out

    def op_set_constraint_value(self, sketch: str, index: int, value) -> dict:
        """Retype a dimension: the number, or the name of a parameter."""
        feature = self._sketch_feature(sketch)
        constraints = feature.args.get("constraints") or []
        if not 0 <= int(index) < len(constraints):
            raise CadError("unknown_constraint", f"the sketch has no constraint {index}",
                           {"count": len(constraints)})
        if "value" not in constraints[int(index)]:
            raise CadError("not_a_dimension",
                           f"constraint {index} is a {constraints[int(index)].get('type')!r}, "
                           "which carries no value",
                           {"index": int(index)})
        with self._edit():
            constraints[int(index)]["value"] = value
            out = self._rebuild()
        return out

    def _sketch_feature(self, sketch: str):
        feature = self._require_doc().feature(sketch)
        if feature.type != "sketch":
            raise CadError("not_a_sketch", f"{sketch!r} is a {feature.type}",
                           {"feature": sketch})
        return feature

    def op_sketch_of(self, face: str) -> dict:
        """Which sketch made this face, if any -- the way a click finds one.

        A face carries the id of the feature that made it, and a feature says
        which sketch it swept, so a pick in the viewport can land on the sketch
        without the caller keeping a map.
        """
        doc = self._require_doc()
        owner = names.feature_of(face)
        candidates = [f for f in doc.features if f.id == owner]
        for feature in candidates:
            if feature.type == "sketch":
                return {"sketch": feature.id, "via": "the face is the sketch"}
            named = feature.args.get("sketch")
            if isinstance(named, str):
                return {"sketch": named, "via": feature.id}
        raise CadError("no_sketch_behind_it", f"nothing sketched made {face!r}",
                       {"feature": owner})

    def op_drag_point(self, sketch: str, point: str, to: list,
                      tolerance: float = 1e-4) -> dict:
        """Drag a sketch point: whatever is free moves, the rest holds.

        Dragging does not rewrite dimensions. It moves the point to where the
        cursor is and re-solves, so the constraints decide what actually
        happens -- which on a fully constrained sketch is *nothing*. That is the
        honest answer, and it is more useful than a silent no: the reply names
        the constraints holding the point and the parameters that would move it,
        so the caller can offer to edit one.
        """
        doc = self._require_doc()
        feature = doc.feature(sketch)
        if feature.type != "sketch":
            raise CadError("not_a_sketch", f"{sketch!r} is a {feature.type}",
                           {"feature": sketch})
        points = feature.args.get("points") or {}
        if point not in points:
            raise CadError("unknown_point", f"the sketch has no point {point!r}",
                           {"known": sorted(points)})

        before = self._solved_points(sketch)
        with self._edit():
            points[point] = [float(to[0]), float(to[1])]
            out = self._rebuild()
            after = self._solved_points(sketch)      # inside: it can raise too

        moved = {name: after[name] for name in after
                 if math.dist(after[name], before.get(name, after[name])) > tolerance}
        if not moved:
            held = self._holding(feature, point)
            # the drag changed nothing, so it was not an edit: it is taken back
            # out of the history rather than undone, which would leave a no-op
            # sitting on the redo stack waiting to be "put back"
            self._forget_last_edit()
            return {"moved": {}, "held": True, "point": point, **held}
        out.update({"moved": moved, "held": False, "point": point})
        return out

    def _solved_points(self, sketch: str) -> dict:
        if self.evaluator is None or sketch not in self.evaluator.sketches:
            self._rebuild()
        solved = self.evaluator.sketches.get(sketch)
        return dict(solved.points) if solved else {}

    def _holding(self, feature, point: str) -> dict:
        """What is stopping this point, and which parameters would let it move.

        The parameters are the ones named by the constraints *on this point* --
        not every parameter mentioned anywhere in the sketch, which is what a
        substring search over the whole constraint list gave: a parameter called
        ``w`` matched ``width * 2`` and the reply offered to edit the wrong
        number.

        A constraint that names a *line* holds that line's endpoints just as
        firmly, and an angle is one of those and carries a number the caller
        could edit. Leaving them out would have answered "nothing is holding
        this point" about a point that would not move.
        """
        doc = self._require_doc()
        lines = feature.args.get("lines") or {}
        through = {name for name, ends in lines.items() if point in tuple(ends)}
        def of_names(c) -> tuple:
            # `of` is a list in some spellings and a single name in others
            # (midpoint) -- spreading a string put its *characters* in the set
            of = c.get("of")
            return (of,) if isinstance(of, str) else tuple(of or ())

        constraints = [c for c in feature.args.get("constraints") or []
                       if point in (c.get("point"), *(c.get("points") or []))
                       or through.intersection(
                           (c.get("line"), *(c.get("lines") or []),
                            *of_names(c)))]
        movers = set()
        for constraint in constraints:
            value = constraint.get("value")
            if not isinstance(value, str):
                continue
            for name in doc.parameters:
                if name in names_in(value):
                    movers.add(name)
        return {"held_by": constraints, "parameters": sorted(movers)}
