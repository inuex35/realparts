"""Operation fuzzing: drive the editing session at random.

Parameter fuzzing covers dimension changes; this covers the state machine of
adding, removing, undoing and rolling back. Every step checks that a
successful edit leaves a document that builds and whose names resolve, that
a refused edit leaves the document exactly as it was, and that undo and redo
restore the states on either side of the edit. Failures carry the seed and
the operation log, so a run reproduces.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

from ..errors import CadError
from ..ops.session import Session


@dataclass
class Soak:
    attempted: int = 0
    succeeded: int = 0
    refused: dict = field(default_factory=dict)      # kind -> count
    violations: list = field(default_factory=list)
    log: list = field(default_factory=list)

    def note(self, kind: str) -> None:
        self.refused[kind] = self.refused.get(kind, 0) + 1


def _snapshot(session: Session) -> str:
    return json.dumps(session.doc.as_dict(), sort_keys=True, default=str)


def _planar_faces(session: Session) -> list:
    from ..geometry.core.measure import face_frame

    out = []
    for name in session.body.face_names():
        try:
            face_frame(session.body, name)
        except CadError:
            continue
        out.append(name)
    return out


#: the feature arguments a person turns a dial on
NUMBERS = frozenset({"radius", "distance", "depth", "thickness", "angle", "height",
                     "offset", "length", "width", "size"})


def _operation(rng: random.Random, session: Session) -> tuple:
    """Pick an edit that makes sense for the model as it stands right now."""
    if session.body is None:
        # rolled back to a plane or a sketch: there is no body to pick a face
        # of, so the one move is to look at the whole model again
        return "rollback", {"feature_id": None}
    faces = _planar_faces(session)
    edges = sorted(session.body.edge_table())
    features = [f.id for f in session.doc.features]
    parameters = sorted(session.doc.parameters)
    choices = []

    # only plain numbers: a parameter written as an expression (`w: "h*2"`)
    # cannot be scaled by a float
    numeric = [n for n in parameters
               if isinstance(session.doc.parameters[n], (int, float))
               and not isinstance(session.doc.parameters[n], bool)]
    if numeric:
        name = rng.choice(numeric)
        lo, hi = session.doc.bounds.get(name, (None, None))
        base = session.doc.parameters[name]
        value = (round(rng.uniform(lo, hi), 3) if lo is not None
                 else round(base * rng.uniform(0.7, 1.4), 3))
        choices.append(("set_parameter", {"name": name, "value": value}))
    if edges:
        picked = rng.sample(edges, k=min(len(edges), rng.randint(1, 2)))
        choices.append(("add_fillet", {"edges": picked, "radius": round(rng.uniform(0.5, 4), 2),
                                       "kind": rng.choice(["fillet", "chamfer"])}))
    if faces:
        face = rng.choice(faces)
        choices.append(("add_pocket", {"face": face, "depth": round(rng.uniform(1, 6), 2),
                                       "width": round(rng.uniform(4, 20), 2),
                                       "height": round(rng.uniform(4, 20), 2),
                                       "kind": rng.choice(["pocket", "boss"])}))
        choices.append(("add_hole", {"face": face, "standard": rng.choice(["M3", "M5", "M6"]),
                                     "fit": rng.choice(["tapped", "normal"]),
                                     "seat": rng.choice(["none", "counterbore"])}))
        half = round(rng.uniform(4, 12), 2)
        choices.append(("add_sketch", {
            "face": face, "operation": rng.choice(["pocket", "boss"]),
            "depth": round(rng.uniform(1, 5), 2),
            "points": [[-half, -half], [half, -half], [half, half], [-half, half]],
            "lines": [[0, 1], [1, 2], [2, 3], [3, 0]]}))
        choices.append(("add_shell", {"open": [rng.choice(faces)],
                                      "thickness": round(rng.uniform(1, 3), 2)}))
    # sketch operations edit constraints rather than add features
    sketches = [f.id for f in session.doc.features if f.type == "sketch"]
    if sketches:
        sketch = rng.choice(sketches)
        try:
            geometry = session.op_sketch_geometry(sketch)
            listed = session.op_sketch_constraints(sketch)["constraints"]
        except CadError:
            geometry, listed = None, []
        if geometry and geometry["points"]:
            point = rng.choice(sorted(geometry["points"]))
            at = geometry["points"][point]
            choices.append(("drag_point", {
                "sketch": sketch, "point": point,
                "to": [round(at[0] + rng.uniform(-8, 8), 3),
                       round(at[1] + rng.uniform(-8, 8), 3)]}))
            other = rng.choice(sorted(geometry["points"]))
            choices.append(("add_constraint", {
                "sketch": sketch,
                "constraint": rng.choice([
                    {"type": "coincident", "points": [point, other]},
                    {"type": "fix", "point": point, "at": list(at)},
                    {"type": "distance", "points": [point, other], "value": 20}])}))
        if listed:
            index = rng.randrange(len(listed))
            choices.append(("remove_constraint", {"sketch": sketch, "index": index}))
            dimensions = [c["index"] for c in listed if "value" in c]
            if dimensions:
                choices.append(("set_constraint_value", {
                    "sketch": sketch, "index": rng.choice(dimensions),
                    "value": round(rng.uniform(5, 60), 2)}))
    # change a number on a feature that is already there: a radius, a depth, a
    # distance. This is what a person does most, and it is where a face or an
    # edge a later feature names can vanish
    numeric_args = []
    for f in session.doc.features:
        for key, value in f.args.items():
            if key in NUMBERS and isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric_args.append((f.id, key, value))
    if numeric_args:
        fid, key, value = rng.choice(numeric_args)
        choices.append(("edit_feature", {"feature_id": fid,
                                         "args": {key: round(value * rng.uniform(0.4, 1.8), 3)}}))
    if features:
        choices.append(("remove_feature", {"feature_id": rng.choice(features)}))
        choices.append(("move_feature", {"feature_id": rng.choice(features),
                                         "after": rng.choice(features)}))
        choices.append(("rollback", {"feature_id": rng.choice(features + [None])}))
    return rng.choice(choices)


def run(path: str, steps: int = 200, seed: int = 0, undo_probability: float = 0.25) -> Soak:
    rng = random.Random(seed)
    session = Session(autosave=False)
    session.op_open(path)
    session.op_build()
    out = Soak()

    for step in range(steps):
        name, args = _operation(rng, session)
        before = _snapshot(session)
        out.attempted += 1
        try:
            getattr(session, f"op_{name}")(**args)
        except CadError as exc:
            out.note(exc.kind)
            after = _snapshot(session)
            if after != before:
                out.violations.append({"step": step, "op": name, "args": args,
                                       "why": "a refused edit changed the document",
                                       "kind": exc.kind})
            continue
        except Exception as exc:                                # noqa: BLE001
            out.violations.append({"step": step, "op": name, "args": args,
                                   "why": f"untyped error: {type(exc).__name__}: {exc}"})
            session = Session(autosave=False)
            session.op_open(path)
            session.op_build()
            continue

        out.succeeded += 1
        out.log.append({"step": step, "op": name, "args": args})
        problem = _check(session)
        if problem:
            out.violations.append({"step": step, "op": name, "args": args, "why": problem})

        after = _snapshot(session)
        # an operation that changed nothing has nothing to undo: dragging a
        # fully held point succeeds and leaves the document as it was
        if name != "rollback" and after != before and rng.random() < undo_probability:
            try:
                session.op_undo()
            except CadError:
                continue
            if _snapshot(session) != before:
                out.violations.append({"step": step, "op": name, "args": args,
                                       "why": "undo did not restore the previous document"})
                continue
            # redo must put back exactly what undo took away
            try:
                session.op_redo()
            except CadError as exc:
                out.violations.append({"step": step, "op": name, "args": args,
                                       "why": f"redo refused after undo: {exc.kind}"})
                continue
            if _snapshot(session) != after:
                out.violations.append({"step": step, "op": name, "args": args,
                                       "why": "redo did not restore the edit"})
    return out


def _check(session: Session) -> str | None:
    """The model still builds, its names resolve, and its mesh carries only
    names the body has."""
    from ..evaluation.api import describe

    if session.body is None:
        # under construction, or rolled back to a datum: nothing to check
        # until there is a body again
        return None
    try:
        info = describe(session.body)
    except Exception as exc:                                    # noqa: BLE001
        return f"the built body cannot be described: {exc}"
    names = set(info["face_names"])
    for name in names:
        if session.body.face(name) is None:
            return f"face name {name!r} does not resolve on the body it came from"
    problem = _roles_still_true(session)
    if problem:
        return problem
    for edge in info["edge_names"]:
        owners = session.body.edge_owners(edge)
        if not owners:
            return f"edge {edge!r} has no record of the faces it came from"
        for part in owners:
            if part not in names and session.body.canonical(part) not in names:
                return f"edge {edge!r} names a face that is not there"
    try:
        mesh = session.op_tessellate(0.6)
    except CadError as exc:
        return f"tessellation refused on a built body: {exc.kind}"
    unknown = set(mesh["face_table"]) - names
    if unknown:
        return f"the mesh carries names the body does not have: {sorted(unknown)[:3]}"
    return None


def _roles_still_true(session: Session) -> str | None:
    """A face called ``+z`` still faces that way.

    Pockets, holes and sketch planes read the role in a name to decide
    direction. The test is loose on purpose: a draft may tilt a face and keep
    its name, but a face must not end up nearer the opposite axis than its own.
    """
    from ..geometry.core.measure import face_frame
    from .. import names

    for name, _ in session.body.names:
        axis = names.axis_of(names.parse(name).role)
        if axis is None:
            continue
        try:
            normal = face_frame(session.body, name)["normal"]
        except CadError:
            continue                          # not planar any more
        if sum(normal[i] * axis[i] for i in range(3)) < 0.5:
            return (f"face {name!r} no longer faces {names.parse(name).role}: "
                    f"its normal is {tuple(round(c, 3) for c in normal)}")
    return None
