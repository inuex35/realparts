"""Opening, editing and undoing a document.

Every operation here is one edit inside the guard that snapshots the document
first: a refusal leaves nothing behind, a success is the next undo step.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ...model import authoring
from ... import features
from ...model.document import Document, Feature, autosave_path
from ...evaluation.graph import Evaluator
from ...errors import CadError, short
from ...features.declare.registry import BODY_KINDS

#: a file of somebody else's that opens as a document of one feature
IMPORTERS = {".step": "import_step", ".stp": "import_step",
             ".iges": "import_iges", ".igs": "import_iges", ".brep": "import_brep",
             ".stl": "import_mesh", ".obj": "import_mesh", ".3mf": "import_mesh",
             ".gltf": "import_mesh", ".glb": "import_mesh"}


#: what `apply` may run: edits of the open document, each inside the edit
#: guard, so a refused step undoes them all. Nothing that writes a file or
#: changes which document is open (tests/test_long_edits.py holds this list
#: to the guard)
EDITS_IN_A_BATCH = frozenset({
    "add_feature", "set_parameter", "set_parameters", "flip_pocket", "edit_feature",
    "reattach", "suppress_feature", "remove_feature", "move_feature",
    "add_fillet", "add_flange", "add_chamfer", "add_pocket", "add_hole", "add_shell",
    "add_thread", "move_face", "delete_faces", "add_thicken", "add_cap", "add_mirror",
    "add_pattern", "add_draft", "add_plane", "add_split", "add_emboss", "add_coil", "set_meta",
    "add_profile", "add_sketch", "add_constraint", "remove_constraint",
    "set_constraint_value", "drag_point",
    "add_requirement", "remove_requirement"})


def _first_solid(doc: Document, exclude: str) -> str | None:
    """The earliest feature that makes a solid -- where a moved feature lands
    when it is asked to go to the front of the history."""
    for f in doc.features:
        if f.id != exclude and f.type != "sketch":
            return f.id
    return None


def _summary(doc: Document, f: Feature) -> str:
    """A one-glance description of a feature for a list row: the numbers, not the expressions."""
    args = f.args

    def n(value) -> str:
        try:
            got = doc.evaluate(value)
        except CadError:
            return str(value)
        if isinstance(got, list):
            return " x ".join(n(v) for v in got)
        if isinstance(got, (int, float)) and not isinstance(got, bool):
            return ("%g" % got) if abs(got) < 1e6 else "%.3g" % got
        return str(got)

    if f.type in ("cut", "fuse", "common"):
        joint = {"cut": "-", "fuse": "+", "common": "&"}[f.type]
        return "%s %s %s" % (args.get("target"), joint, args.get("tool"))
    if f.type in ("fillet", "chamfer"):
        edges = args.get("edges")
        many = len(edges) if isinstance(edges, list) else None
        size = n(args.get("radius", args.get("distance")))
        return "r %s on %s" % (size, "%d edges" % many if many else "a query")
    if f.type == "box":
        return n(args.get("size"))
    if f.type == "cylinder":
        # through `n`, like everything else here: a radius that does not
        # resolve must not stop a broken document being listed
        return "d%s x %s" % (n(_twice(doc, args.get("radius"))),
                             n(args.get("height")))
    if f.type in ("extrude", "pocket", "boss", "rib"):
        far = args.get("distance", args.get("depth"))
        return "%s %s %s" % (args.get("sketch") or args.get("face") or "",
                             "up" if f.type != "pocket" else "down",
                             n(far) if far is not None else args.get("until", ""))
    if f.type == "hole":
        return "d%s on %s" % (n(args.get("diameter", "?")), args.get("face", ""))
    if f.type == "part":
        return str(args.get("document", ""))
    keys = [k for k in ("size", "radius", "height", "depth", "distance", "thickness",
                        "angle", "count") if k in args]
    return " ".join("%s %s" % (k, n(args[k])) for k in keys)


def _twice(doc, value):
    """A diameter from a radius, or the radius as written if it will not."""
    try:
        return 2 * doc.evaluate(value)
    except CadError:
        return value


def _inputs_of(doc) -> dict:
    """What each feature consumes, asked of the feature types themselves."""
    evaluator = Evaluator(doc)
    return {f.id: evaluator.inputs(f) for f in doc.features}


def _unsaved_work(path: str) -> str | None:
    """A sidecar newer than the document it sits beside, if there is one.

    Newer, not merely present: a stale one is not unsaved work. A document
    that is not on disk at all (a STEP import) counts as older than anything.
    """
    beside = Path(autosave_path(path))
    try:
        if not beside.exists():
            return None
        here = Path(path)
        if not here.exists() or beside.stat().st_mtime > here.stat().st_mtime:
            return str(beside)
    except OSError:
        pass
    return None


def _repoint(feature, old: str, new) -> None:
    """Point every reference this feature makes at `old` somewhere else, nested ones included; None leaves it alone."""
    from ...features import handler
    from ...features.declare.schema import read_at, write_at

    if new is None:
        return
    try:
        kind = handler(feature.type)
    except CadError:
        return                            # refused later, by name, when it builds
    for path in kind.reference_paths(feature.args):
        if read_at(feature.args, path) == old:
            write_at(feature.args, path, new)


def _stands_for(feature) -> str | None:
    """The input a feature's result could be replaced by, or None."""
    from ... import features
    try:
        return features.handler(feature.type).passthrough(feature.args)
    except CadError:
        return None


class DocumentOps:
    """Mixed into :class:`cadcore.ops.session.Session`."""

    def op_open(self, path: str, recover: bool = False, as_copy: bool = False) -> dict:
        """Open a document -- or a STEP, IGES, BREP or mesh file, wrapped in a one-feature document.

        A sidecar newer than the document is unsaved work: the reply says so
        (`unsaved_work`) rather than acting on it; `recover=True` opens the
        sidecar's content under the document's own path. `as_copy` forgets
        where the file came from, so a shipped example is never written over.
        """
        path = self._path(path, writing=False)
        # the file is read before anything of the old session is cleared: a
        # refusal leaves the old document and its history whole
        importer = IMPORTERS.get(Path(path).suffix.lower())
        if importer:
            # absolute, because the document's own source is that same file and
            # a relative path would then be resolved against its directory twice
            imported = str(Path(path).resolve())
            loaded = Document(features=[Feature("part", importer, {"path": imported})],
                              result="part", meta={"name": Path(path).stem,
                                                   "imported_from": imported})
            loaded.source = imported
            new_path, saved, waiting = str(Path(path).with_suffix(".json")), True, None
        else:
            waiting = _unsaved_work(path)
            loaded = Document.load(waiting if recover and waiting else path)
            loaded.source = path            # the sidecar's parts resolve like the document's
            new_path, saved = path, not (recover and waiting)
        # undo after opening B must not put A back, nor A's checkpoints
        self.undone.clear()
        self.redone.clear()
        self.checkpoints.clear()
        self._drag = None
        self.view_upto = None
        self.body = None
        self.evaluator = None
        self.meshes.clear()
        self.cache = {}
        self.doc = self._fenced(loaded)
        self._stamp_revision()
        # `as_copy`: read the file, forget where it came from. A shipped example
        # opened from the gallery is a starting point, not a file to write over
        self.path = None if as_copy else new_path
        self.saved = False if as_copy else saved
        if importer:
            return self.op_describe_document()
        consumes = _inputs_of(self.doc)
        return {"path": path, "parameters": dict(self.doc.parameters),
                "features": [{"id": f.id, "type": f.type, "inputs": consumes[f.id]}
                             for f in self.doc.features],
                "result": self.doc.result, "bounds": self.doc.bounds,
                "saved": self.saved,
                "unsaved_work": None if recover else waiting,
                "studies": [s.get("id", "study") for s in self.doc.studies]}

    def op_new_document(self, unit: str = "mm", name: str = "part") -> dict:
        """Start an empty document, in one of the units `new_document` lists on refusal."""
        from ...model.document import UNITS, Document

        unit = str(unit).lower()
        if unit not in UNITS:
            raise CadError("unknown_unit", f"no unit called {unit!r}", {"known": sorted(UNITS)})
        self.undone.clear()
        self.redone.clear()
        self.checkpoints.clear()
        self._drag = None
        self.view_upto = None
        self.body = None
        self.evaluator = None
        self.meshes.clear()
        self.cache.clear()
        self.doc = self._fenced(Document(parameters={}, features=[], result=None,
                                         meta={"name": name}, unit=unit))
        self._stamp_revision()
        self.path = None
        self.saved = False
        return {"unit": unit, "name": name, "features": [], "parameters": {}}

    def op_add_feature(self, type: str, args: dict | None = None,
                       feature_id: str | None = None,
                       after: str | None = None) -> dict:
        """Add a feature of any declared type, by its own declaration.

        The `add_*` operations are shorthands for the common ones; this takes
        every type `feature_types` lists. Refused by the declaration before
        anything is built: an argument it does not take is named, and so are
        the ones it does.
        """
        from ... import features
        from ... import names as naming

        doc = self._require_doc()
        kind = features.handler(type)          # refuses, and lists the types
        args = dict(args or {})
        fid = naming.check_id(feature_id) if feature_id else authoring.fresh_id(doc, type)
        if any(f.id == fid for f in doc.features):
            raise CadError("duplicate_id", f"there is already a feature {fid!r}",
                           {"feature": fid})
        kind.check(args, where=fid)
        if after is not None and not any(f.id == after for f in doc.features):
            raise CadError("unknown_feature", f"no feature {after!r} to add after",
                           {"known": [f.id for f in doc.features]})

        with self._edit():
            feature = Feature(fid, type, args)
            if after is None:
                doc.features.append(feature)
            else:
                at = next(i for i, f in enumerate(doc.features) if f.id == after)
                doc.features.insert(at + 1, feature)
            # the result is the last thing built unless somebody says otherwise
            if kind.produces in BODY_KINDS and (doc.result is None or after is None):
                doc.result = fid
            out = self._rebuild()
        out["feature"] = fid
        return out

    def op_apply(self, ops: list) -> dict:
        """Several operations as one edit and one undo step: all of them, or none.

        ``ops``  a list of ``{"op": "add_feature", ...}`` -- the same names and
                 arguments each operation takes on its own. A refusal says
                 which step and why, and the document is as it was.
        """
        if not isinstance(ops, list) or not ops:
            raise CadError("bad_arguments", "apply takes a list of operations",
                           {"got": type(ops).__name__})
        self._require_doc()
        done = []
        with self._edit():
            for index, step in enumerate(ops):
                if not isinstance(step, dict) or "op" not in step:
                    raise CadError("bad_arguments",
                                   "step %d is not an operation" % index,
                                   {"step": index, "given": short(step)})
                name = step["op"]
                if not isinstance(name, str) or not hasattr(self, "op_" + name):
                    raise CadError(
                        "unknown_op", f"no operation {name!r}",
                        {"step": index,
                         "available": sorted(m[3:] for m in dir(self)
                                             if m.startswith("op_"))})
                if name not in EDITS_IN_A_BATCH:
                    raise CadError(
                        "bad_arguments",
                        "step %d (%s) is not an edit of the open document, and apply "
                        "runs edits of the open document" % (index, name),
                        {"step": index, "operation": name,
                         "allowed": sorted(EDITS_IN_A_BATCH),
                         "hint": "only what the edit guard can take back goes in a "
                                 "batch: a file written or an undo stack cleared "
                                 "stays done however the batch ends"})
                method = getattr(self, "op_" + name)
                try:
                    method(**{k: v for k, v in step.items() if k != "op"})
                except CadError as refused:
                    # the caller's own refusal, with where it happened added:
                    # a kind is always a literal at its raise
                    refused.detail = {**(refused.detail or {}), "step": index,
                                      "operation": name, "applied": list(done),
                                      "hint": "nothing was applied: the document "
                                              "is as it was before this call"}
                    refused.message = "step %d (%s): %s" % (index, name,
                                                            refused.message)
                    raise
                done.append(name)
            out = self._rebuild()
        out["applied"] = done
        return out

    def op_checkpoint(self, name: str) -> dict:
        """Remember the document under a name, to come back to with `restore`; cheap, it is data not geometry."""
        doc = self._require_doc()
        if not isinstance(name, str) or not name.strip():
            raise CadError("bad_parameter", "a checkpoint needs a name",
                           {"given": name})
        self.checkpoints[name] = doc.snapshot()
        del_oldest = len(self.checkpoints) - 32
        for stale in list(self.checkpoints)[:max(del_oldest, 0)]:
            self.checkpoints.pop(stale)
        return {"checkpoint": name, "checkpoints": sorted(self.checkpoints)}

    def op_restore(self, name: str) -> dict:
        """Go back to a checkpoint -- as one undoable step, not by unwinding.

        On the same stack as everything else, so the way back from a restore is
        the same Ctrl+Z as the way back from a fillet.
        """
        if name not in self.checkpoints:
            raise CadError("unknown_reference", f"no checkpoint {name!r}",
                           {"checkpoints": sorted(self.checkpoints)})
        with self._edit() as doc:
            doc.take_content_from(self.checkpoints[name])   # not its source, fence or number
            out = self._rebuild()
        out["restored"] = name
        return out

    def op_begin_drag(self) -> dict:
        """Start a run of trial edits that `end_drag` records as one, or none.

        While a drag is open, edits are not recorded. A drag nobody ended is
        abandoned: beginning another one drops it.
        """
        self._require_doc()
        if self._drag is not None:
            self.op_end_drag(keep=False)
        # after the drop: the snapshot must not be the abandoned preview
        self._drag = (self.doc.snapshot(), None, None, list(self.redone), self.saved)
        return {"undo_depth": len(self.undone)}

    def op_reset_drag(self) -> dict:
        """Put the document back to where the drag began, and keep dragging."""
        if self._drag is None:
            raise CadError("bad_arguments", "no drag is open", {"hint": "begin_drag first"})
        snapshot = self._drag[0]
        self._restore(snapshot.snapshot())
        self.body, self.evaluator = None, None   # rebuilt from the content cache: the
        return self._rebuild()                   # old evaluator holds the last preview

    def op_end_drag(self, keep: bool = True) -> dict:
        """Finish the drag: what it made is one undo step, or it never happened."""
        if self._drag is None:
            raise CadError("bad_arguments", "no drag is open", {"hint": "begin_drag first"})
        snapshot, _, _, redone, saved = self._drag
        self._drag = None
        if keep:
            self.undone.append(snapshot)
            del self.undone[:-64]
            self.redone.clear()
            self._stamp_revision()
            self.saved = False
            self._autosave()
            out = self._rebuild()
        else:
            self._restore(snapshot)
            self.body, self.evaluator = None, None
            self.redone[:] = redone
            self.saved = saved
            out = self._rebuild()
        out["undo_depth"] = len(self.undone)
        return out

    def op_goto_revision(self, revision: int) -> dict:
        """Undo or redo until the document is the state with this number.

        `moved` says whether anything had to change; a number no longer in
        the history is refused.
        """
        revision = int(revision)
        doc = self._require_doc()
        if doc.revision == revision:
            return {"revision": revision, "moved": False, "undo_depth": len(self.undone)}
        out = None
        if any(d.revision == revision for d in self.undone):
            while self.doc.revision != revision:
                out = self.op_undo()
        elif any(d.revision == revision for d in self.redone):
            while self.doc.revision != revision:
                out = self.op_redo()
        else:
            raise CadError("nothing_to_undo",
                           "revision %d is no longer in the history" % revision,
                           {"revision": revision, "here": doc.revision,
                            "hint": "the kernel keeps 64 steps"})
        out["moved"] = True
        return out

    def op_checkpoints(self) -> dict:
        """What has been remembered, and what the document is now."""
        return {"checkpoints": sorted(self.checkpoints)}

    def op_document_json(self) -> dict:
        """The whole document, exactly as it would be saved (`describe_document` is the summary a panel draws)."""
        return {"document": deepcopy(self._require_doc().as_dict())}

    def op_load_json(self, document: dict) -> dict:
        """Replace this session's document with the one given, and build it; refused the way a file is."""
        from ...model.document import Document

        if not isinstance(document, dict):
            raise CadError("bad_arguments", "a document is an object",
                           {"got": type(document).__name__})
        source = self.doc.source if self.doc else None
        replaced = Document.from_dict(deepcopy(document), source)
        # the old document stays until the new one has built: a refusal from
        # the rebuild must not cost the session what it had
        kept = (self.doc, list(self.undone), list(self.redone), self.view_upto,
                self.body, self.evaluator, dict(self.meshes), dict(self.cache), self.saved,
                dict(self.checkpoints))
        self.undone.clear()
        self.redone.clear()
        self.checkpoints.clear()
        self._drag = None
        self.view_upto = None
        self.body = None
        self.evaluator = None
        self.meshes.clear()
        self.cache.clear()
        self.doc = self._fenced(replaced)
        self._stamp_revision()
        self.saved = False
        try:
            return self._rebuild()
        except Exception:
            (self.doc, self.undone[:], self.redone[:], self.view_upto, self.body,
             self.evaluator, self.meshes, self.cache, self.saved, checkpoints) = kept
            self.checkpoints.update(checkpoints)
            raise

    def op_save(self, path: str | None = None) -> dict:
        """Write the document out, and forget the sidecar it made obsolete."""
        doc = self._require_doc()
        target = path or self.path
        if not target:
            raise CadError("no_path", "this document has never been saved",
                           {"hint": "pass a path"})
        target = self._path(target, writing=True)
        doc.save(target)
        self._drop_autosave()               # under the *old* path, if it moved
        self.path = str(target)
        doc.source = str(target)            # the files it names resolve from where it is now
        self.saved = True
        return {"path": target, "saved": True}

    def op_describe_document(self) -> dict:
        """The document as the UI needs to redraw it after any edit: copies, not the live dictionaries."""
        doc = self._require_doc()
        consumes = _inputs_of(doc)
        return {"path": self.path, "parameters": dict(doc.parameters),
                # the envelope the fuzz test samples inside is also the right
                # range for a slider, so it goes out with the values
                "parameters_bounds": {k: list(v) for k, v in doc.bounds.items()},
                "result": doc.result,
                "meta": deepcopy(doc.meta) if isinstance(doc.meta, dict) else {},
                "rolled_back_to": self.view_upto,
                # whether the file holds what is on screen
                "saved": self.saved,
                "undo_depth": len(self.undone), "redo_depth": len(self.redone),
                "revision": doc.revision,
                "features": [{"id": f.id, "type": f.type, "inputs": consumes[f.id],
                              "summary": _summary(doc, f), "args": deepcopy(f.args),
                              "suppressed": f.suppressed}
                             for f in doc.features]}

    def op_build(self) -> dict:
        """Build the document as it stands, and describe what came out."""
        return self._rebuild()

    def op_set_meta(self, name: str | None = None, material: str | None = None,
                    colour: str | None = None) -> dict:
        """What the part is called, made of, and coloured: the document's own notes.

        The material is the one the bill of materials, mass and STEP use; the
        colour (#rrggbb) goes out to STEP. An empty string clears a field.
        """
        doc = self._require_doc()
        if colour:
            from ...geometry.io.exchange import rgb_of
            rgb_of(colour)                         # refused by name if it is not a colour
        with self._edit():
            if not isinstance(doc.meta, dict):
                doc.meta = {}
            for key, value in (("name", name), ("material", material), ("colour", colour)):
                if value is None:
                    continue
                if value == "":
                    doc.meta.pop(key, None)
                else:
                    doc.meta[key] = str(value)
        self.saved = False
        return {"meta": dict(doc.meta)}

    def op_set_parameter(self, name: str, value: float | str) -> dict:
        """Give one parameter a new value and rebuild.

        The value may be a number or an expression naming other parameters.
        """
        return self.op_set_parameters({name: value})

    def op_set_parameters(self, values: dict) -> dict:
        """Move several parameters and rebuild once; refused as a whole, every name checked before any value is written."""
        doc = self._require_doc()
        unknown = sorted(set(values) - set(doc.parameters))
        if unknown:
            raise CadError("unknown_parameter",
                           "no parameter " + ", ".join(repr(n) for n in unknown),
                           {"known": sorted(doc.parameters), "unknown": unknown})
        with self._edit():
            doc.parameters.update(values)
            out = self._rebuild()
            # inside the guard: a bound that will not evaluate raises here,
            # and outside it that was a refusal with the edit already kept
            ok, why = doc.in_envelope()
        out["in_envelope"] = ok
        out["envelope_note"] = why
        return out

    def op_flip_pocket(self, feature_id: str) -> dict:
        """Turn a pocket into a boss, or a boss into a pocket: same profile and depth, the other side of the face."""
        doc = self._require_doc()
        feature = doc.feature(feature_id)
        other = {"pocket": "boss", "boss": "pocket"}.get(feature.type)
        if other is None:
            raise CadError("bad_arguments",
                           f"{feature_id!r} is a {feature.type}, not a pocket or a boss",
                           {"feature": feature_id, "type": feature.type})
        was = feature.type
        with self._edit():
            feature.type = other
            out = self._rebuild()
        out["feature"] = feature_id
        out["was"], out["kind"] = was, other
        return out

    def op_edit_feature(self, feature_id: str, args: dict) -> dict:
        """Change a feature's arguments in place; a key set to null is removed; refused whole if it does not build."""
        doc = self._require_doc()
        feature = doc.feature(feature_id)
        with self._edit():
            for key, value in args.items():
                if key in ("id", "type"):
                    raise CadError("bad_arguments", "a feature's id and type are fixed",
                                   {"feature": feature_id})
                # "counterbore.diameter" edits one field of a nested argument:
                # the shape the panel offers, so the kernel has to take it
                if "." in key:
                    head, sub = key.split(".", 1)
                    inside = feature.args.get(head)
                    if not isinstance(inside, dict):
                        raise CadError("bad_arguments",
                                       f"{head!r} is not a nested argument "
                                       f"of {feature_id!r}",
                                       {"feature": feature_id, "argument": key})
                    if value is None:
                        inside.pop(sub, None)
                    else:
                        inside[sub] = value
                    continue
                if value is None:
                    feature.args.pop(key, None)
                else:
                    feature.args[key] = value
            out = self._rebuild()
        out["feature"] = feature_id
        out["args"] = feature.args
        return out

    def op_broken_references(self) -> dict:
        """Every name in the document that no longer points at anything, with what it could become.

        The candidates come from the naming vocabulary: a split face's pieces
        (`plate/+z@0`, `@1`) and a pattern's copies (`bore~1`) share the dead
        name's feature and role, so those are offered.
        """
        from ... import features
        from ...features.declare import schema

        doc = self._require_doc()
        body, upto = self._furthest()
        if body is None:
            raise CadError("nothing_builds",
                           "not one feature of this document builds",
                           {"features": [f.id for f in doc.features]})
        broken = []
        # a reference to a feature that is not there stops the build itself
        known = {f.id for f in doc.features}
        for feature in doc.features:
            try:
                kind = features.handler(feature.type)
            except CadError:
                continue
            for path in kind.reference_paths(feature.args):
                named = schema.read_at(feature.args, path)
                if named in known:
                    continue
                broken.append({"feature": feature.id, "path": path,
                               "name": named, "why": "no such feature",
                               "candidates": self._like(named, known)})
            paths = kind.geometry_names(feature.args)
            if not paths:
                continue
            # against the bodies as they were when this feature ran: a later
            # boolean splitting a face does not make an earlier reference wrong
            inputs = self._input_bodies(feature, kind) or [body]
            everywhere = set()
            for one in inputs:
                everywhere |= set(one.face_names()) | set(one.edge_table())
            for path, kind_of_name in paths:
                value = feature.args
                for step in path:
                    value = value[step]
                # a mate names one face on each of two parts: judged by its own input
                verdicts = [self._trouble(one, value, everywhere, kind_of_name)
                            for one in inputs]
                if None in verdicts:
                    continue
                trouble = "split" if "split" in verdicts else "missing"
                broken.append({"feature": feature.id, "path": path, "name": value,
                               "why": trouble,
                               "candidates": self._like(value, everywhere)})
        return {"broken": broken, "dropped": list(body.dropped),
                "aliases": dict(body.aliases), "built_upto": upto}

    def _input_bodies(self, feature, kind) -> list:
        """The bodies this feature's names are written against: its own inputs, each of them (a mate names two)."""
        doc = self._require_doc()
        wanted = [kind.passthrough(feature.args)] if kind.stands_for else []
        wanted += [r for r in kind.references(feature.args) if r]
        out, seen = [], set()
        for ref in wanted:
            if ref is None or ref in seen:
                continue
            seen.add(ref)
            try:
                out.append(Evaluator(doc, self.cache).build(ref))
            except CadError:
                continue
        return out

    def _furthest(self):
        """The body from as much of the document as still builds.

        Walked back from the end until a step stands up, each tried on the
        document trimmed to that feature (`order()` refuses a whole document
        with a dangling reference). The cache makes the walk cheap.
        """
        doc = self._require_doc()
        for index in range(len(doc.features) - 1, -1, -1):
            feature = doc.features[index]
            trimmed = doc.snapshot()
            trimmed.features = trimmed.features[:index + 1]
            trimmed.result = feature.id
            try:
                return Evaluator(trimmed, self.cache).build(feature.id), feature.id
            except CadError:
                continue
        return None, None

    def _trouble(self, body, name: str, live, kind: str = "name") -> str | None:
        """What is wrong with this reference, if anything: its face is gone (point elsewhere), or split (say which piece)."""
        from ... import names as naming

        if name in live:
            return None
        if kind != "name":
            # matched by base, so the pieces of a split face are found: only
            # a name that matches nothing at all is a problem here
            return None if any(naming.base(other) == naming.base(name)
                               for other in live) else "missing"
        resolved = body.canonical(name)
        if resolved != name and body.face(resolved) is not None:
            try:
                if naming.parse(resolved).piece is not None:
                    return "split"
            except CadError:
                pass
            return None                  # an alias to a whole face: still fine
        return "missing"

    def _like(self, name: str, live) -> list:
        """Names that are the same face or edge, differently numbered."""
        from ... import names as naming

        try:
            want = naming.parse(name)
        except CadError:
            return []
        out = []
        for other in sorted(live):
            try:
                theirs = naming.parse(other)
            except CadError:
                continue
            if (theirs.feature, theirs.role) == (want.feature, want.role):
                out.append(other)
        return out[:12]

    def op_reattach(self, old: str, new: str, feature_id: str | None = None) -> dict:
        """Point a reference at a different face or edge, wherever it is nested, and rebuild."""
        doc = self._require_doc()
        # from as much as still builds, because a repair is wanted precisely
        # when the whole thing does not
        body, _ = self._furthest()
        if body is None:
            raise CadError("nothing_builds",
                           "not one feature of this document builds",
                           {"features": [f.id for f in doc.features]})
        live = set(body.face_names()) | set(body.edge_table())
        if new not in live:
            raise CadError("unresolved_reference",
                           f"nothing built so far is called {new!r}",
                           {"candidates": self._like(new, live)})
        touched = []
        with self._edit():
            for feature in doc.features:
                if feature_id is not None and feature.id != feature_id:
                    continue
                try:
                    kind = features.handler(feature.type)
                except CadError:
                    continue
                for path, _kind in kind.geometry_names(feature.args):
                    holder = feature.args
                    for step in path[:-1]:
                        holder = holder[step]
                    if holder[path[-1]] == old:
                        holder[path[-1]] = new
                        touched.append({"feature": feature.id, "path": path})
            if not touched:
                raise CadError("unknown_reference",
                               f"no feature refers to {old!r}",
                               {"hint": "ask for broken_references to see what does"})
            # inside the guard: the rebuild can still refuse (`empty_selection`
            # for a fillet pointed at a face that is not where it is used)
            out = self._rebuild()
        out["reattached"] = touched
        out["status"] = "pointed %d reference%s from %s to %s" % (
            len(touched), "" if len(touched) == 1 else "s", old, new)
        return out

    def op_suppress_feature(self, feature_id: str, suppressed: bool = True) -> dict:
        """Switch a feature off (it hands its input through) or back on; refused for one with nothing to hand through."""
        doc = self._require_doc()
        target = next((f for f in doc.features if f.id == feature_id), None)
        if target is None:
            raise CadError("unknown_feature", f"no feature {feature_id!r}",
                           {"known": [f.id for f in doc.features]})
        if bool(suppressed) and _stands_for(target) is None:
            raise CadError(
                "cannot_suppress",
                f"{feature_id!r} is a {target.type} -- there is nothing for it "
                f"to hand through",
                {"id": feature_id, "type": target.type,
                 "hint": "a feature that makes a body out of nothing can be "
                         "removed but not switched off"})
        was = target.suppressed
        with self._edit():
            target.suppressed = bool(suppressed)
            # inside the guard: switching off can break a later reference
            out = self._rebuild()
        out["suppressed"] = [f.id for f in doc.features if f.suppressed]
        out["status"] = ("switched %s off" % feature_id if target.suppressed
                         else "switched %s back on" % feature_id)
        if was == target.suppressed:
            out["status"] = "%s was already %s" % (
                feature_id, "off" if was else "on")
        return out

    def op_remove_feature(self, feature_id: str) -> dict:
        """Delete a feature and re-link the chain across the gap.

        Dependents are pointed at its own input; a feature with no stand-in
        (a sketch an extrude still consumes) is refused with them listed.
        """
        doc = self._require_doc()
        target = next((f for f in doc.features if f.id == feature_id), None)
        if target is None:
            raise CadError("unknown_feature", f"no feature {feature_id!r}",
                           {"known": [f.id for f in doc.features]})
        # what this feature's result can stand in for, from its own declaration:
        # `body or target` was a hand-written guess that happened to be right
        passthrough = _stands_for(target)
        keep = [f for f in doc.features if f.id != feature_id]
        consumes = _inputs_of(doc)
        dependents = [f.id for f in keep if feature_id in consumes[f.id]]
        if dependents and passthrough is None:
            raise CadError("feature_in_use",
                           f"{feature_id!r} is consumed by {', '.join(dependents)}",
                           {"dependents": dependents})

        # re-linking is inside the guard too: the rebuild can still refuse
        with self._edit():
            for f in keep:
                _repoint(f, feature_id, passthrough)
            # a sketch exists only to feed something: it goes with its last consumer
            doc.features = keep
            if doc.result == feature_id:
                doc.result = passthrough or (keep[-1].id if keep else None)
            gone = [feature_id] + self._collect_orphans(doc)
            out = self._rebuild()
        out["removed"] = gone
        return out

    @staticmethod
    def _collect_orphans(doc: Document) -> list:
        """Sketches nothing consumes any more: they build nothing on their own."""
        gone = []
        while True:
            consumes = _inputs_of(doc)
            used = {i for names in consumes.values() for i in names}
            orphans = [f for f in doc.features
                       if f.type == "sketch" and f.id not in used and f.id != doc.result]
            if not orphans:
                break
            doc.features = [f for f in doc.features if f not in orphans]
            gone.extend(f.id for f in orphans)
        return gone

    def op_move_feature(self, feature_id: str, after: str | None = None) -> dict:
        """Move a feature to another point in the history, re-linking the chain; a boolean joining two branches cannot move."""
        doc = self._require_doc()
        feature = doc.feature(feature_id)
        key = "body" if "body" in feature.args else "target"
        if key not in feature.args or not isinstance(feature.args.get(key), str):
            raise CadError("not_a_chain_feature",
                           f"{feature_id!r} does not sit in a single chain",
                           {"args": sorted(feature.args)})
        if after is not None:
            if doc.feature(after).type == "sketch":
                raise CadError("no_solid",
                               f"{after!r} is a sketch: a feature cannot follow it",
                               {"hint": "move it after the feature that consumes the sketch"})
            if after in Evaluator(doc).descendants(feature_id):
                raise CadError("cyclic_graph", f"{after!r} already comes after {feature_id!r}")

        with self._edit():
            source = feature.args[key]
            for f in doc.features:                       # unlink: close the gap
                if f.id != feature_id:
                    _repoint(f, feature_id, source)
            if doc.result == feature_id:
                doc.result = source

            new_input = after if after is not None else _first_solid(doc, feature_id)
            for f in doc.features:                       # relink: open a new gap
                if f.id != feature_id:
                    _repoint(f, new_input, feature_id)
            if doc.result == new_input:
                doc.result = feature_id
            feature.args[key] = new_input

            order = [f for f in doc.features if f.id != feature_id]
            at = next((i for i, f in enumerate(order) if f.id == new_input), -1) + 1
            doc.features = order[:at] + [feature] + order[at:]
            out = self._rebuild()
        out["moved"] = feature_id
        out["after"] = new_input
        return out

    def op_rollback(self, feature_id: str | None = None) -> dict:
        """Look at the model as it was after a given feature -- or drop the view.

        Nothing is deleted: the later features are simply not evaluated, which
        is what makes this cheap as well as safe.
        """
        doc = self._require_doc()
        if feature_id is not None:
            doc.feature(feature_id)                  # refused by name if unknown
        # a plane or a sketch is a fine place to look from: the model as it
        # was then had no solid, and the view shows the sketch and the plane
        was, self.view_upto = self.view_upto, feature_id
        try:
            return self._rebuild()
        except CadError:
            self.view_upto = was
            raise

    def op_undo(self) -> dict:
        """Step back to the document as it was before the last edit."""
        if not self.undone:
            raise CadError("nothing_to_undo", "no edits to undo")
        # a snapshot, not as_dict(): as_dict hands back the document's live
        # dictionaries, so a redo step written that way moves with the document
        current = self.doc.snapshot()
        self._restore(self.undone.pop())
        self.redone.append(current)
        out = self._rebuilt_or_back(current, self.undone, self.redone)
        # the document on disk is the one that was saved; this one is a step
        # away from it now, and the sidecar should say so too
        self.saved = False
        self._autosave()
        out["undo_depth"] = len(self.undone)
        return out

    def _rebuilt_or_back(self, current, taken_from: list, given_to: list) -> dict:
        """Rebuild after a step; if that fails, take the step back so document and body agree."""
        try:
            return self._rebuild()
        except Exception:
            taken_from.append(self.doc)
            given_to.pop()
            self._restore(current)
            self._rebuild()
            raise

    def op_redo(self) -> dict:
        """Step forward again, undoing an undo."""
        if not self.redone:
            raise CadError("nothing_to_redo", "no edits to redo")
        current = self.doc.snapshot()
        self._restore(self.redone.pop())
        self.undone.append(current)
        out = self._rebuilt_or_back(current, self.redone, self.undone)
        self.saved = False
        self._autosave()
        out["undo_depth"] = len(self.undone)
        return out
