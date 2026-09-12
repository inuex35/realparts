"""Authoring features from a selection.

Each of these turns a pick -- these faces, those edges -- into a named feature
in the document. They are deliberately thin: the work of writing a feature is in
:mod:`cadcore.model.authoring`, and the work of building it is in
:mod:`cadcore.features`. What is here is the part that needs the *session*: what
is currently built, and which faces exist to be named.
"""
from __future__ import annotations

from ...model import authoring, fasteners
from ...model.document import Feature
from ...errors import CadError
from ...geometry import kernel
from ...geometry.core.measure import face_frame


def _rotate(v, axis, degrees):
    """v turned about axis by degrees (Rodrigues)."""
    import math

    a = [x / (sum(y * y for y in axis) ** 0.5) for x in axis]
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    cross = [a[1] * v[2] - a[2] * v[1], a[2] * v[0] - a[0] * v[2], a[0] * v[1] - a[1] * v[0]]
    dot = sum(x * y for x, y in zip(a, v))
    return [v[i] * c + cross[i] * s + a[i] * dot * (1 - c) for i in range(3)]


def _tipped(normal, x_axis, tilt: float, turn: float):
    """A ground plane tipped about its own x axis, then turned about world z."""
    if tilt:
        normal = _rotate(normal, x_axis, tilt)
    if turn:
        normal = _rotate(normal, [0.0, 0.0, 1.0], turn)
        x_axis = _rotate(x_axis, [0.0, 0.0, 1.0], turn)
    return normal, x_axis


def _some_x_axis(normal):
    """An x axis in the plane, for a plane given by its normal alone."""
    n = [float(c) for c in normal]
    if sum(c * c for c in n) < 1e-18:
        raise CadError("degenerate_frame", "a plane's normal has to point somewhere",
                       {"normal": list(normal)})
    up = [0.0, 0.0, 1.0] if abs(n[2]) < 0.9 else [1.0, 0.0, 0.0]
    x = [up[1] * n[2] - up[2] * n[1], up[2] * n[0] - up[0] * n[2], up[0] * n[1] - up[1] * n[0]]
    length = sum(c * c for c in x) ** 0.5
    return [c / length for c in x]


#: the three planes through the origin, by the axes they hold: normal, x axis.
#: `xz` faces -y so that its x is x and its y is up
GROUND_PLANES = {"xy": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
                 "xz": ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0)),
                 "yz": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))}


class ModellingOps:
    """Mixed into :class:`cadcore.ops.session.Session`."""

    def op_add_fillet(self, edges: list, radius: float, feature_id: str | None = None,
                      kind: str = "fillet") -> dict:
        """Round (or chamfer) the given edges, by name.

        The edges arrive as names, not indices, which is the whole point: the
        feature that lands in the document says "the edge between plate/+z and
        plate/+x", and that survives the next parameter change.
        """
        doc = self._require_doc()
        return self._apply(authoring.edge_feature(doc, kind, edges, radius, feature_id))

    def op_add_flange(self, edge: str, length: float, angle: float = 90.0,
                      radius: float | None = None, feature_id: str | None = None) -> dict:
        """Bend a flap up from an edge of a sheet metal part.

        The edge is named, so the flange stays on that edge when the blank
        changes size. `radius` is the inside radius, the sheet's thickness by
        default; `angle` is how far it turns, 90 degrees by default.
        """
        doc = self._require_doc()
        return self._apply(authoring.flange(doc, edge, length, angle, radius, feature_id))

    def op_add_chamfer(self, edges: list, distance: float,
                       feature_id: str | None = None) -> dict:
        """Cut the given edges back at 45 degrees, by name."""
        return self.op_add_fillet(edges, distance, feature_id, kind="chamfer")

    def op_add_pocket(self, face: str, depth: float, width: float, height: float,
                      kind: str = "pocket", at=(0.0, 0.0), feature_id: str | None = None,
                      count: int = 1, spacing: float = 0.0, direction=None,
                      until: str | dict | None = None, symmetric: bool = False) -> dict:
        """Sketch a rectangle on a named face and cut (or add) a prism through it.

        This is the gesture the viewport wants: click a face, get a pocket. What
        makes it parametric rather than a one-off edit is that the sketch is
        placed *on the face name*, so the pocket follows that face when the part
        changes, and its size and depth become document parameters.
        """
        doc = self._require_doc()
        self._ensure_body()
        frame = face_frame(self.body, face)              # refuse early, refuse typed
        edit = authoring.pocket(doc, face, width, height, depth, kind, at, count,
                                spacing, self._world_direction(direction, frame),
                                feature_id, until, symmetric)
        edit.info["frame"] = frame
        return self._apply(edit)

    def op_add_hole(self, face: str, diameter: float | None = None,
                    depth: float | None = None, counterbore: dict | None = None,
                    countersink: dict | None = None, at=(0.0, 0.0), count: int = 1,
                    spacing: float = 0.0, direction=None, standard: str | None = None,
                    fit: str = "normal", seat: str = "none",
                    feature_id: str | None = None) -> dict:
        """Drill a hole in a named face, seat and all, as one feature.

        Either give the numbers, or give a standard: "M6 tapped" and "M8 normal
        clearance, counterbored" resolve to the usual ISO sizes and carry the
        note that belongs on the drawing.
        """
        doc = self._require_doc()
        note = None
        if standard:
            spec = fasteners.resolve(standard, fit, seat)
            diameter = diameter or spec["diameter"]
            counterbore = counterbore or spec.get("counterbore")
            countersink = countersink or spec.get("countersink")
            note = spec["note"]
        if diameter is None:
            raise CadError("bad_arguments", "a hole needs a diameter or a standard")
        self._ensure_body()
        frame = face_frame(self.body, face)              # refuse early, refuse typed
        edit = authoring.hole(doc, face, diameter, depth, counterbore, countersink, at,
                              count, spacing, self._world_direction(direction, frame),
                              standard, fit, seat, note, feature_id)
        edit.info["frame"] = frame
        return self._apply(edit)

    def op_add_shell(self, open: list, thickness: float,
                     feature_id: str | None = None) -> dict:
        """Hollow the current body, opening it at the named faces."""
        doc = self._require_doc()
        self._require_faces(open)
        return self._apply(authoring.shell(doc, open, thickness, feature_id))

    def op_add_thread(self, face: str, standard: str | None = None,
                      pitch: float | None = None, length: float | None = None,
                      clearance: float = 0.0, feature_id: str | None = None) -> dict:
        """Cut a real thread on a named cylindrical face."""
        doc = self._require_doc()
        self._require_faces([face])
        return self._apply(authoring.thread(doc, face, standard, pitch, length,
                                            clearance, feature_id))

    def op_move_face(self, face: str, distance: float,
                     feature_id: str | None = None) -> dict:
        """Push or pull a named planar face along its own normal."""
        doc = self._require_doc()
        self._require_faces([face])
        return self._apply(authoring.move_face(doc, face, distance, feature_id))

    def op_delete_faces(self, faces: list, heal: bool = True,
                        feature_id: str | None = None) -> dict:
        """Remove faces -- healing the wound, or leaving it open on purpose."""
        doc = self._require_doc()
        self._require_faces(faces)
        return self._apply(authoring.delete_faces(doc, faces, heal, feature_id))

    def op_add_thicken(self, thickness: float, feature_id: str | None = None) -> dict:
        """Give an open surface a wall thickness."""
        doc = self._require_doc()
        return self._apply(authoring.thicken(doc, thickness, feature_id))

    def op_add_cap(self, continuity: str = "G0",
                   feature_id: str | None = None) -> dict:
        """Patch every opening in the current shape and sew it into a solid."""
        doc = self._require_doc()
        return self._apply(authoring.cap(doc, continuity, feature_id))

    def op_add_mirror(self, face: str, merge: bool = True,
                      feature_id: str | None = None) -> dict:
        """Reflect the body about a picked flat face.

        The face is the mirror, not the thing mirrored: pick the wall the part
        is symmetric about. For a symmetry plane that is not a face, make a
        work plane between two faces first and pick that.
        """
        doc = self._require_doc()
        self._require_faces([face])
        frame = face_frame(self.body, face)
        return self._apply(authoring.mirror(doc, frame["origin"], frame["normal"],
                                            merge, feature_id))

    def op_add_pattern(self, count: int, face: str | None = None,
                       step: float | None = None, spacing: float | None = None,
                       direction=None, merge: bool = True,
                       feature_id: str | None = None) -> dict:
        """Repeat the body: around a picked round face, or along a direction.

        With a round face the copies go round its axis, evenly over a full turn
        unless `step` says how far to turn between them. Without a face they go
        along `direction`, `spacing` apart.
        """
        from ...geometry.assembly.assembly import axis_of

        doc = self._require_doc()
        if int(count) < 2:
            raise CadError("bad_parameter", "a pattern needs two or more",
                           {"count": count})
        if face is not None:
            self._require_faces([face])
            origin, axis, _ = axis_of(self.body, face)
            return self._apply(authoring.ring(doc, count, origin, axis, step,
                                              merge, feature_id))
        if spacing is None:
            raise CadError("missing_argument",
                           "a pattern along a direction needs a spacing",
                           {"hint": "or pick a round face to go around it"})
        return self._apply(authoring.row(doc, count, spacing,
                                         direction or [1, 0, 0], merge, feature_id))

    def op_add_draft(self, angle: float, faces: list | None = None,
                     neutral: str | None = None, direction=None,
                     parting_face: str | None = None, parting_offset: float | None = None,
                     feature_id: str | None = None) -> dict:
        """Taper the named faces about a neutral face, or away from a parting plane.

        ``parting_face`` puts the parting plane ``parting_offset`` mm off that
        flat face, along its normal -- halfway through the part when not given,
        since a plane in the face itself splits nothing -- and drafts each side
        away from it; with no ``faces`` given, every wall standing square to the
        plane is drafted.
        """
        doc = self._require_doc()
        faces = list(faces or [])
        if parting_face:
            self._require_faces(faces + [parting_face])
            if parting_offset is None:
                parting_offset = self._halfway_in(parting_face)
            if not faces:
                faces = self._walls_square_to(parting_face)
                if not faces:
                    raise CadError("empty_selection", "no wall stands square to that face",
                                   {"parting_face": parting_face})
            return self._apply(authoring.draft_from(doc, faces, angle, parting_face,
                                                    parting_offset, feature_id))
        if not faces:
            raise CadError("bad_arguments", "a draft needs the faces to taper",
                           {"hint": "or a parting_face, which picks the walls itself"})
        self._require_faces(faces + ([neutral] if neutral else []))
        if neutral is not None and direction is None:
            direction = face_frame(self.body, neutral)["normal"]
        return self._apply(authoring.draft(doc, faces, angle, neutral, direction,
                                           feature_id))

    def _halfway_in(self, face: str) -> float:
        """The offset along a face's normal to the middle of the part behind it (negative)."""
        frame = face_frame(self.body, face)
        o, n = frame["origin"], frame["normal"]
        lo, hi = kernel.bounds_of(self.body)
        depth = min(sum((corner[i] - o[i]) * n[i] for i in range(3))
                    for corner in ((x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                                   for z in (lo[2], hi[2])))
        return round(depth / 2, 3)

    def _walls_square_to(self, face: str) -> list:
        """The flat and round faces whose direction lies in the plane of ``face``."""
        normal = face_frame(self.body, face)["normal"]
        walls = []
        for entry in self.op_describe_faces()["faces"]:
            direction = entry.get("normal") or entry.get("axis")
            if direction is None or entry["name"] == face:
                continue
            if abs(sum(direction[i] * normal[i] for i in range(3))) < 1e-3:
                walls.append(entry["name"])
        return walls

    def op_add_emboss(self, face: str, text: str, depth: float = 1.0, height: float | None = None,
                      cut: bool = False, wrap: bool = False, at: list | None = None,
                      feature_id: str | None = None) -> dict:
        """Words raised off a face, or cut into it, from a font: one drag sets the depth.

        ``height`` is the font size, a quarter of the face's width by default;
        ``at`` where the words start in the face's own frame, centred by
        default; ``wrap`` winds them round a cylindrical face instead of
        projecting them. A negative ``depth`` cuts.
        """
        doc = self._require_doc()
        self._require_faces([face])
        info = next((f for f in self.op_describe_faces()["faces"] if f["name"] == face), {})
        if height is None:
            height = max(1.0, round((float(info.get("area_mm2", 100.0)) ** 0.5) / 4, 2))
        cut = bool(cut) or float(depth) < 0
        plane = None
        if info.get("shape") == "cylinder":
            # the words are drawn on the plane touching the can where its x axis meets it,
            # u round the can and v along it, and wrapped on from there
            span = kernel.axis_span(self.body, face)
            start = kernel.helix_start(span["foot"], span["direction"], span["radius"])
            middle = [start["start"][i] + span["direction"][i] * span["length"] / 2 for i in range(3)]
            plane = {"origin": middle, "normal": start["x_axis"], "x_axis": start["tangent"]}
            wrap = True
        elif info.get("shape") != "plane":
            raise CadError("non_planar_face", f"words go on a flat or a round face; {face!r} is neither",
                           {"face": face, "shape": info.get("shape")})
        return self._apply(authoring.emboss(doc, face, text, height, abs(float(depth)), cut,
                                            wrap, at, feature_id, plane))

    def op_add_coil(self, face: str, wire: float | None = None, pitch: float | None = None,
                    turns: float | None = None, feature_id: str | None = None) -> dict:
        """A wire wound round a cylindrical face and joined to it: a coil, a thread of round stock.

        ``wire`` is the wire's diameter (a fifth of the radius by default),
        ``pitch`` the rise per turn (twice the wire), ``turns`` how many (as
        many as the face is long). The coil is a sweep along a helix fused on,
        so its pitch is a parameter afterwards; remove the fuse to keep it a
        part of its own.
        """
        doc = self._require_doc()
        self._require_faces([face])
        span = kernel.axis_span(self.body, face)
        wire = float(wire) if wire else max(0.5, round(span["radius"] / 5, 2))
        pitch = float(pitch) if pitch else 2 * wire
        turns = float(turns) if turns else max(1.0, float(int(span["length"] / pitch)))
        # a little into the face, on the side the material is: the join holds
        radius = span["radius"] + (-wire if span["bore"] else wire) * 0.4
        frame = kernel.helix_start(span["foot"], span["direction"], radius)
        return self._apply(authoring.coil(doc, face, wire, pitch, turns, frame["start"],
                                          frame["tangent"], frame["x_axis"], span["foot"],
                                          span["direction"], radius, feature_id))

    def op_add_split(self, face: str | None = None, offset: float = 0.0,
                     keep: str = "above", normal: list | None = None,
                     origin: list | None = None, feature_id: str | None = None) -> dict:
        """Cut the body in two at a plane and keep one side, or both.

        ``face`` with ``offset``  the plane is that far off the named flat
                face, along its normal, and follows the face.
        ``normal`` and ``origin``  a plane given outright.
        ``keep``  ``above`` (the side the normal points to), ``below``, or
                ``both`` -- two solids in one body, for a part made in halves.
        """
        doc = self._require_doc()
        if face:
            self._require_faces([face])
        return self._apply(authoring.split(doc, face, offset, keep, normal, origin, feature_id))

    def op_add_plane(self, face: str | None = None, offset: float = 0.0,
                     between: list | None = None, on: str | None = None,
                     tilt: float = 0.0, turn: float = 0.0, about: str | None = None,
                     angle: float = 0.0,
                     normal: list | None = None, x_axis: list | None = None,
                     origin: list | None = None, feature_id: str | None = None) -> dict:
        """A work plane: off a face, turned about one of its edges, halfway
        between two faces, or one of the ground planes.

        ``about``  a straight edge of the same body, with ``angle`` in degrees:
                the plane hangs on that edge and turns about it, so the two
                stay touching however the face behind it moves. Needs ``face``.
        ``on``  ``xy``, ``xz`` or ``yz`` -- a plane through the origin, offset
                along its normal. The one way to start a sketch in an empty
                document: every other placement needs a face, and a new
                document has none.
        ``tilt``  degrees to tip that plane about its own x axis
        ``turn``  degrees to turn it about the world z axis
        ``normal``, ``x_axis``, ``origin``  a plane given outright instead

        It builds nothing, so the reply is the frame itself -- which is what a
        caller wants next, to place a sketch on it.
        """
        doc = self._require_doc()
        fid = feature_id or authoring.fresh_id(doc, "plane")
        name = f"{fid}_offset"
        values = {name: float(offset)}
        if on is not None or normal is not None:
            if on is not None:
                if on not in GROUND_PLANES:
                    raise CadError("bad_arguments", "a ground plane is one of: xy, xz, yz",
                                   {"given": on})
                normal, x_axis = (list(v) for v in GROUND_PLANES[on])
                normal, x_axis = _tipped(normal, x_axis, float(tilt), float(turn))
            elif x_axis is None:
                x_axis = _some_x_axis(normal)
            args = {"origin": [float(c) for c in (origin or [0.0, 0.0, 0.0])],
                    "normal": [float(c) for c in normal],
                    "x_axis": [float(c) for c in x_axis], "offset": name}
            target = doc.result             # unset until something makes a body
        elif between:
            self._ensure_body()
            target = authoring.target_of(doc)
            self._require_faces(between)
            args = {"between": [{"body": target, "face": f} for f in between],
                    "offset": name}
        elif face:
            self._ensure_body()
            target = authoring.target_of(doc)
            self._require_faces([face])
            args = {"from": {"body": target, "face": face}, "offset": name}
            if about:
                args["about"] = {"body": target, "edge": about}
                args["angle"] = f"{fid}_angle"
                values[args["angle"]] = float(angle)
        elif about:
            raise CadError("bad_arguments",
                           "a plane turns about an edge of the face it comes off; "
                           "say which face too", {"about": about})
        else:
            raise CadError("bad_arguments",
                           "a work plane needs a face, two to sit between, or a "
                           "ground plane (`on`: xy, xz, yz)")

        edit = authoring.Edit([Feature(fid, "plane", args)], values,
                              doc.result or target, {"plane": fid})
        with self._edit():               # the frame is part of the edit: if it
            out = self._apply(edit)      # cannot be worked out, nothing is kept
            feature = doc.feature(fid)
            out["frame"] = (self.evaluator.planes.get(fid)
                            or self.evaluator.frame_of(feature, feature.args, doc.evaluate))
        out["feature"] = fid
        return out

    @staticmethod
    def _world_direction(direction, frame: dict):
        """A repeat direction given in the face's own axes, if it is 2-D.

        Found by using it: ``at`` is in face coordinates and the pattern
        direction was in world coordinates, so a row of holes drawn "22 mm up
        the face" marched off in a different direction than the one that placed
        them. Two numbers now mean the face's own u and v.
        """
        if direction is None or len(direction) != 2:
            return direction
        x, normal = frame["x_axis"], frame["normal"]
        y = (normal[1] * x[2] - normal[2] * x[1], normal[2] * x[0] - normal[0] * x[2],
             normal[0] * x[1] - normal[1] * x[0])
        u, v = float(direction[0]), float(direction[1])
        return [x[i] * u + y[i] * v for i in range(3)]
