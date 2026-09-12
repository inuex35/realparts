"""Evaluating the document as a DAG, with content-hash caching.

Each feature's cache key hashes its own resolved arguments plus the keys of
its inputs, so changing one parameter invalidates only the nodes downstream.
"""
from __future__ import annotations

import hashlib
import json
import math
import os

from .. import features
from ..features.declare.schema import read_at
from ..geometry.assembly.assembly import prefixed
from ..geometry.core import promises
from ..model.document import Document, Feature
from ..errors import CadError
from ..geometry.core.measure import edge_axis, face_frame, turned
from ..geometry.core.naming import Body

def _without(args, paths: list):
    """The arguments with the value at each reference path blanked out."""
    import copy

    out = copy.deepcopy(args)
    for path in paths:
        holder = out
        for step in path[:-1]:
            holder = holder[step]
        holder[path[-1]] = None
    return out


def _as_floats(value):
    """The same value with every number a float: 10 and 10.0 are one key."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return {k: _as_floats(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_floats(v) for v in value]
    return value


def _hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode())
        h.update(b"\0")
    return h.hexdigest()[:16]


def _stamp(doc, named: str, stamps: list, depth: int = 0) -> None:
    """Record a file's path, mtime and size and, for a sub-document, the
    files its own features name, up to four levels deep, so editing a STEP
    imported by a part invalidates the assembly that uses the part."""
    path = doc.resolve(named)
    stamps.append(path)
    if not os.path.exists(path):
        return
    stamps += [str(os.path.getmtime(path)), str(os.path.getsize(path))]
    if depth >= 4 or not path.lower().endswith(".json"):
        return
    try:
        sub = Document.load(path)
    except Exception:                                               # noqa: BLE001
        return          # not a document: its own stamp above still counts
    sub.fence = doc.fence                # the files it names go through the same fence
    for f in sub.features:
        try:
            kind = features.handler(f.type)
        except CadError:
            continue
        for inner in kind.files(f.args):
            _stamp(sub, inner, stamps, depth + 1)

class Evaluator:
    def __init__(self, doc: Document, cache: dict | None = None):
        self.doc = doc
        self.sketches: dict = {}
        self.sketch_specs: dict = {}       # the spec each sketch solved, for probing
        self.planes: dict = {}
        self.axes: dict = {}
        self.cache: dict[str, Body] = cache if cache is not None else {}
        self.parts_cache: dict = {}
        self.keys: dict[str, str] = {}
        self.ids = {f.id for f in doc.features}   # what a reference can name
        own = getattr(doc, "source", None)
        self.building: tuple = (os.path.realpath(own),) if own else ()   # the documents open above this one
        self.stats = {"evaluated": 0, "reused": 0}

    # -- what a feature consumes --------------------------------------------
    def inputs(self, f: Feature) -> list:
        """The features this one consumes, as declared by its feature type."""
        try:
            named = features.handler(f.type).references(f.args)
        except CadError:
            return []                      # an unknown type is refused elsewhere
        return [name for name in named if name in self.ids]

    def missing(self, f: Feature) -> list:
        """References that name a feature and do not find one."""
        try:
            named = features.handler(f.type).references(f.args)
        except CadError:
            return []
        return sorted({name for name in named if name not in self.ids})

    # -- ordering -----------------------------------------------------------
    def order(self) -> list[Feature]:
        # Unresolved references are checked before ordering: they are not
        # dependencies, so the graph would otherwise order fine and the
        # feature would fail later with a misleading error.
        adrift = {f.id: self.missing(f) for f in self.doc.features}
        adrift = {fid: names for fid, names in adrift.items() if names}
        if adrift:
            missing = sorted({name for names in adrift.values() for name in names})
            raise CadError("unknown_feature",
                           "features refer to ids that do not exist: "
                           + ", ".join(missing),
                           {"missing": missing, "referenced_by": sorted(adrift)})

        remaining = list(self.doc.features)
        done: set[str] = set()
        out: list[Feature] = []
        while remaining:
            progressed = False
            for f in list(remaining):
                if all(i in done for i in self.inputs(f)):
                    out.append(f)
                    done.add(f.id)
                    remaining.remove(f)
                    progressed = True
            if not progressed:
                raise CadError("cyclic_graph", "features form a cycle",
                               {"stuck": [f.id for f in remaining]})
        return out

    # -- evaluation ---------------------------------------------------------
    def key_of(self, f: Feature) -> str:
        """Hash a feature by its resolved arguments plus its inputs' keys.

        A string leaf that evaluates is hashed as both its text and its value:
        a sketch point name can look like a parameter, and the key has to
        follow the parameters (tests/test_cache_key.py).
        """
        def deep(v):
            if isinstance(v, dict):
                return {k: deep(x) for k, x in v.items()}
            if isinstance(v, list):
                return [deep(x) for x in v]
            try:
                resolved = self.doc.evaluate(v)
            except CadError:
                return v
            if isinstance(v, str) and resolved != v:
                return [v, resolved]
            return resolved

        # references are left out by where the declaration puts them: what
        # they name is in the parents' keys, and a rename must not change the key
        try:
            kind = features.handler(f.type)
        except CadError:
            kind = None                  # an unknown type is refused when built
        paths = kind.reference_paths(f.args) if kind else []
        resolved = deep(_without(f.args, paths))
        # each parent's key is filed under the argument it sits in: the same
        # parents in another role, or one of them twice, is another key
        parents = {}
        for path in paths:
            name = read_at(f.args, path)
            if name in self.ids:
                parents["/".join(str(step) for step in path)] = self.keys[name]

        # a feature that reads a file is keyed by the file's stamp too
        stamps = []
        for named in (kind.files(f.args) if kind else []):
            _stamp(self.doc, named, stamps)
        # The feature's own id is part of the key: its faces are named after
        # it, so two features with identical arguments are not interchangeable.
        return _hash(f.id, f.type, json.dumps(_as_floats(resolved), sort_keys=True, default=str),
                     _hash(*stamps) if stamps else "",
                     "off" if f.suppressed else "",
                     json.dumps(parents, sort_keys=True))

    def ancestors(self, target: str) -> set:
        """Every feature the target depends on, transitively."""
        by_id = {f.id: f for f in self.doc.features}
        if target not in by_id:
            raise CadError("unknown_feature", f"no feature {target!r}",
                           {"known": sorted(by_id)})
        seen, queue = set(), [target]
        while queue:
            fid = queue.pop()
            if fid in seen:
                continue
            seen.add(fid)
            queue.extend(i for i in self.inputs(by_id[fid]) if i in by_id)
        return seen

    def descendants(self, target: str) -> set:
        """Every feature that depends on the target, transitively."""
        out, changed = {target}, True
        while changed:
            changed = False
            for f in self.doc.features:
                if f.id not in out and any(i in out for i in self.inputs(f)):
                    out.add(f.id)
                    changed = True
        return out

    def build(self, target: str | None = None) -> Body:
        """Build the document, or only the ancestors of ``target``.

        Rolling back to an earlier feature is a build targeting it; the later
        features are not evaluated.
        """
        target = self.prepare(target)
        # body_of turns "that is not a solid" into a refusal with a kind;
        # reading the cache directly would raise KeyError for a sketch or datum.
        body = self.body_of(target)
        if body.promised_valid and not promises.sound(body.shape):
            # find the first feature whose body is unsound, so the refusal
            # names the step that broke it rather than the last one
            for f in self.order():
                made = self.cache.get(self.keys.get(f.id))
                if made is not None and getattr(made, "promised_valid", False):
                    promises.assure(made, f.id)
            promises.assure(body, target)
        return body

    def prepare(self, target: str | None = None) -> str:
        """Evaluate what ``target`` needs and return the target's id.

        Separate from `build` so a document with no solid yet (a plane and a
        sketch, before the extrude) can still be evaluated and refused.
        """
        target = target or self.doc.result or (self.doc.features[-1].id
                                               if self.doc.features else None)
        if target is None:
            raise CadError("empty_document", "no features to build")
        keep = self.ancestors(target)
        for f in self.order():
            if f.id not in keep:
                continue
            key = self.key_of(f)
            self.keys[f.id] = key
            if key in self.cache and f.type != "sketch":
                self.stats["reused"] += 1
                continue
            result = self._eval(f)
            if result is not None:
                self.cache[key] = result
            self.stats["evaluated"] += 1
        return target

    def body_of(self, fid: str) -> Body:
        """The solid a feature produced, or a `no_solid` refusal if it made none."""
        key = self.keys.get(fid)
        if key is None or key not in self.cache:
            kind = next((f.type for f in self.doc.features if f.id == fid), None)
            raise CadError("no_solid",
                           f"{fid!r} produces no solid" +
                           (f" (it is a {kind})" if kind else ""),
                           {"feature": fid, "type": kind})
        return self.cache[key]

    def build_part(self, f: Feature) -> Body:
        """Build another document and bring it in as one named part.

        The sub-evaluator shares `parts_cache`, so an assembly using the same
        part four times builds it once.
        """
        path = self.doc.resolve(f.args["document"])
        if not os.path.exists(path):
            raise CadError("file_not_found", f"no document at {path!r}",
                           {"feature": f.id})
        chain = list(self.building)
        if os.path.realpath(path) in chain:
            raise CadError("circular_part", f"{f.id!r} brings in a document that is already being built",
                           {"feature": f.id, "documents": chain + [os.path.realpath(path)],
                            "hint": "a part cannot contain itself, directly or through another"})
        sub = Document.load(path)
        sub.fence = self.doc.fence           # and so do the files the part names
        # the assembly may override its part's parameters
        for name, value in (f.args.get("parameters") or {}).items():
            if name not in sub.parameters:
                raise CadError("unknown_parameter",
                               f"{path} has no parameter {name!r}",
                               {"known": sorted(sub.parameters)})
            sub.parameters[name] = self.doc.evaluate(value)
        ok, why = sub.in_envelope()
        if not ok:
            raise CadError("outside_envelope", f"{f.id!r} is outside its own envelope",
                           {"reason": why, "document": path})
        target = f.args.get("result") or sub.result
        inner = Evaluator(sub, self.parts_cache)
        inner.building = self.building + (os.path.realpath(path),)
        return prefixed(inner.build(target), f.id)

    def plane_of(self, name: str) -> dict:
        """A work plane's frame by its feature id, or an unknown_plane refusal."""
        frame = self.planes.get(name)
        if frame is None:
            raise CadError("unknown_plane", f"no work plane called {name!r}",
                           {"known": sorted(self.planes)})
        return frame

    def sketch_of(self, name: str):
        sk = self.sketches.get(name)
        if sk is None:
            raise CadError("unknown_sketch", f"no sketch called {name!r}",
                           {"known": sorted(self.sketches)})
        return sk

    # -- feature types: handlers in :mod:`cadcore.features` register themselves

    @staticmethod
    def feature_types() -> list:
        return features.types()

    def _eval(self, f: Feature) -> Body:
        try:
            kind = features.handler(f.type)
            kind.check(f.args, where=f.id)
        except CadError as exc:
            exc.detail["id"] = f.id
            raise
        if f.suppressed:
            # A suppressed feature stands for one of its inputs. Which one
            # comes from the declaration: for a cut it is the target, not the tool.
            stands_for = kind.passthrough(f.args)
            if stands_for is None:
                raise CadError(
                    "cannot_suppress",
                    f"{f.id!r} is a {f.type} -- there is nothing for it to "
                    f"hand through",
                    {"id": f.id, "type": f.type,
                     "hint": "a feature that makes a body out of nothing can be "
                             "removed but not switched off"})
            return self.body_of(stands_for)
        try:
            return kind.build(self, f, f.args, self.doc.evaluate)
        except KeyError as exc:
            # an argument the declaration lets a document leave out, read anyway;
            # a nested one (a pattern's direction) is named the same way
            key = exc.args[0] if exc.args else None
            if isinstance(key, str):
                raise CadError("missing_argument",
                               f"{f.type} {f.id!r} needs {key!r}",
                               {"feature": f.id, "argument": key}) from exc
            raise

    def frame_of(self, f, a, ev) -> dict:
        """Resolve a work plane's frame against the bodies as they stand.

        Public so a caller can get the frame of a plane nothing consumes,
        which the graph would not evaluate.
        """
        offset = float(ev(a.get("offset", 0.0)))
        if "between" in a:
            frames = [face_frame(self.body_of(ref["body"]), ref["face"])
                      for ref in a["between"]]
            if len(frames) != 2:
                raise CadError("bad_arguments", "a midplane needs exactly two faces",
                               {"given": len(frames)})
            first, second = frames
            # not parallel: there is no midway to sit at
            square = sum(first["normal"][i] * second["normal"][i] for i in range(3))
            if abs(square) < 0.999:
                raise CadError("bad_arguments",
                               "a midplane needs two parallel faces; these meet at "
                               "%.0f degrees" % math.degrees(math.acos(max(-1.0, min(1.0, abs(square))))),
                               {"faces": [ref["face"] for ref in a["between"]]})
            origin = [(first["origin"][i] + second["origin"][i]) / 2 for i in range(3)]
            normal = first["normal"]
            return {"origin": [origin[i] + normal[i] * offset for i in range(3)],
                    "normal": normal, "x_axis": first["x_axis"]}
        if "from" in a:
            source = a["from"]
            frame = face_frame(self.body_of(source["body"]), source["face"])
            normal, x_axis, origin = frame["normal"], frame["x_axis"], frame["origin"]
            hinge = a.get("about")
            if hinge:
                # the plane hangs on the edge and turns about it, so the two
                # stay touching however the face behind it moves
                point, direction = edge_axis(self.body_of(hinge["body"]), hinge["edge"])
                angle = math.radians(float(ev(a.get("angle", 0.0))))
                normal = turned(normal, direction, angle)
                x_axis = turned(x_axis, direction, angle)
                origin = point
            return {"origin": [origin[i] + normal[i] * offset for i in range(3)],
                    "normal": normal, "x_axis": x_axis}
        normal = ev(a.get("normal", [0, 0, 1]))
        origin = ev(a.get("origin", [0, 0, 0]))
        return {"origin": [origin[i] + normal[i] * offset for i in range(3)],
                "normal": normal, "x_axis": ev(a.get("x_axis", [1, 0, 0]))}

    def axis_frame(self, spec) -> dict:
        """An axis argument: a named datum, or an inline origin and direction."""
        if isinstance(spec, str):
            frame = self.axes.get(spec)
            if frame is None:
                raise CadError("unknown_axis", f"no axis called {spec!r}",
                               {"known": sorted(self.axes)})
            return frame
        spec = spec or {}
        if isinstance(spec.get("axis"), str):
            return self.axis_frame(spec["axis"])
        return {"origin": spec.get("origin", [0, 0, 0]),
                "direction": spec.get("direction", [0, 0, 1])}

# Handlers register themselves on import; load them here rather than relying
# on whoever imports the evaluator first.
features.load()
