"""The editing session: one open document, and every operation on it.

The operations are the ``op_*`` methods of the mixins in ``subjects/``. What
is here is what they share: the open document, what is built from it, and
the guard (:meth:`Session._edit`) that makes an edit all-or-nothing.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

from ..evaluation.api import build, describe
from ..model.document import Document, autosave_path, check_ids_once
from ..errors import CadError
from .subjects.analysis import AnalysisOps
from .subjects.assembly import AssemblyOps
from ..features.declare.registry import BODY_KINDS
from .subjects.documents import DocumentOps
from .subjects.inspect import InspectOps
from .subjects.modelling import ModellingOps
from .subjects.requirements import RequirementOps
from .subjects.sketches import SketchOps

class Session(DocumentOps, SketchOps, ModellingOps, InspectOps, AnalysisOps,
              AssemblyOps, RequirementOps):
    """One open document, and everything that can be done to it.

    The mixins are chapters, not layers: they all work on the same three pieces
    of state -- the document, the body built from it, and the evaluator that
    built it -- and they all go through :meth:`_edit` to change anything.
    """

    def __init__(self, autosave: bool = True, root: str | None = None,
                 readable: tuple = ()):
        self.autosave = autosave               # off for a harness such as the soak
        self.doc: Document | None = None
        self.path: str | None = None
        self.body = None
        self.evaluator = None
        self.cache: dict = {}
        self.meshes: dict = {}                 # (body key, deflection) -> tessellation
        self.undone: list = []                 # document snapshots, oldest first
        self.redone: list = []
        self.view_upto: str | None = None      # history rolled back to this feature
        self.saved: bool = True                # whether the file holds this document
        self._undid = None                     # what the last edit cost, to give back
        self._next_revision = 1                # the next state number to hand out
        #: "all" hands back every face and edge name on every build; "changed"
        #: hands back what appeared and what went. The panel needs the whole
        #: list to redraw and pays nothing for it; an assistant is given the
        #: same 19 KB after every edit and pays for all of it, twice -- once to
        #: read and once to keep. `reply_style` is how it asks for the other.
        self.names_in_reply = "all"
        self._last_names: tuple = ((), ())
        self._editing = 0                      # how deep the edit guard is
        #: documents kept under a name, to come back to. A design that is being
        #: explored forks, and counting undo steps back to the fork is a way of
        #: losing one of the branches
        self.checkpoints: dict = {}
        #: an open drag: (document, _, _, redo stack, saved) from before it
        #: began. While one is open an edit is a trial, not an undo step
        self._drag = None
        #: where this session may read and write, or None for anywhere. A
        #: library session is somebody's own code in their own process and is
        #: not confined; a *served* one -- the line protocol, the MCP adapter --
        #: sets a root, because that is where "an assistant given this cannot
        #: write files" was being read. See cadcore/ops/workspace.py
        self.root: str | None = root
        #: folders a fenced session may read from besides its root: the shipped examples
        self.readable: tuple = tuple(readable)

    def _path(self, path, writing: bool) -> str:
        """A path this session is allowed to use, or a refusal by name."""
        from . import workspace

        try:
            return workspace.inside(self.root, path, writing)
        except CadError:
            if writing:
                raise
            for other in self.readable:
                try:
                    return workspace.inside(other, path, writing)
                except CadError:
                    continue
            raise

    def _stamp_revision(self) -> None:
        """This state of the document gets a number no other state has had."""
        self.doc.revision = self._next_revision
        self._next_revision += 1

    def _fenced(self, doc):
        """The document with this session's fence on the files it names."""
        from . import workspace

        if self.root is not None:
            doc.fence = lambda path: self._path(path, writing=False)
        return doc

    # -- helpers ------------------------------------------------------------
    def _require_doc(self) -> Document:
        if self.doc is None:
            raise CadError("no_document", "open a document first")
        return self.doc

    def _rebuild(self) -> dict:
        doc = self._require_doc()
        target = self.view_upto
        if target is not None and not any(f.id == target for f in doc.features):
            target = self.view_upto = None
        if not self._makes_a_body(doc, upto=target):
            return self._prepare_only(doc, target)
        self.body, self.evaluator = build(doc, self.cache, target)
        info = describe(self.body)
        # `describe` is a function of the body, and a body has no unit -- the
        # kernel is millimetres and its keys say so. The document has the unit,
        # so this is where a figure gets reported in what it was drawn in
        info["unit"] = doc.unit
        if doc.unit != "mm":
            per = doc.per_mm
            info[f"volume_{doc.unit}3"] = round(info["volume_mm3"] / per ** 3, 6)
            info[f"area_{doc.unit}2"] = round(info["area_mm2"] / per ** 2, 6)
        info["stats"] = self.evaluator.stats
        info["rolled_back_to"] = self.view_upto
        info["revision"] = doc.revision
        faces = tuple(info.get("face_names") or ())
        edges = tuple(info.get("edge_names") or ())
        if self.names_in_reply == "changed":
            was_faces, was_edges = self._last_names
            info["face_names_added"] = sorted(set(faces) - set(was_faces))
            info["face_names_gone"] = sorted(set(was_faces) - set(faces))
            info["edge_names_added"] = sorted(set(edges) - set(was_edges))
            info["edge_names_gone"] = sorted(set(was_edges) - set(edges))
            info["face_names"] = len(faces)
            info["edge_names"] = len(edges)
            info["names"] = "changed only; ask describe_faces or reply_style " \
                            "for the whole list"
        self._last_names = (faces, edges)
        if "assembly" in self.body.notes:
            # what the mates left free, beside the geometry they placed: the
            # same answer a sketch has always given, for the same reason
            info["freedom"] = {k: v for k, v in self.body.notes["assembly"].items()
                               if k != "poses"}
        if self.body.notes:
            info["notes"] = dict(self.body.notes)
        # what the part must be, answered on every build: the rows are cheap
        # (a printability scan is the one that is not, and it says so)
        if doc.requirements:
            info["requirements"] = self._requirements_status(info)
        return info

    @staticmethod
    def _makes_a_body(doc: Document, upto: str | None = None) -> bool:
        """Does anything up to `upto` (or the whole document) make a body?

        A plane and a sketch with no extrude yet make none, and that is not
        an error: the document is under construction.
        """
        from .. import features

        if upto is not None:
            kind = next((f.type for f in doc.features if f.id == upto), None)
            return kind is not None and features.handler(kind).produces in BODY_KINDS
        return any(features.handler(f.type).produces in BODY_KINDS for f in doc.features)

    def _prepare_only(self, doc: Document, upto: str | None = None) -> dict:
        """Evaluate a document that has no body yet, and say so."""
        from ..evaluation.graph import Evaluator

        self.evaluator = Evaluator(doc, self.cache)
        if doc.features:
            self.evaluator.prepare(upto or doc.features[-1].id)
        self.body = None
        self._last_names = ((), ())
        return {"kind": "nothing", "under_construction": True,
                "volume_mm3": 0.0, "area_mm2": 0.0, "open_boundaries": 0,
                "faces": 0, "edges": 0, "face_names": [], "edge_names": [],
                "aliases": {}, "dropped": [], "unit": doc.unit,
                "stats": self.evaluator.stats, "rolled_back_to": upto,
                "hint": "nothing here makes a solid yet: extrude or revolve a "
                        "sketch, or add a box or a cylinder"}

    def _ensure_body(self) -> None:
        """Rebuild if needed, and refuse if there is still nothing to work on."""
        if self.body is None:
            self._rebuild()
        if self.body is None:
            doc = self._require_doc()
            raise CadError("no_solid",
                           "nothing in this document makes a solid yet",
                           {"features": [f.id for f in doc.features],
                            "hint": "extrude or revolve a sketch, or add a box"})

    def _requirements_status(self, built: dict | None = None) -> list:
        from ..analysis import requirements

        doc = self._require_doc()
        if built is None:
            built = describe(self.body)
        return requirements.status(
            doc, self.body, built,
            lambda **kw: self.op_printability(**kw))

    def _studies_status(self) -> list:
        """The studies' own `require` rows from the last simulate, or nothing.

        Marked stale once the document has changed since: a safety factor
        computed for a different part is not a fact about this one.
        """
        seen = getattr(self, "_studies_seen", None)
        if seen is None:
            return []
        stamp, rows = seen
        stale = stamp != self._document_stamp()
        return [dict(r, stale=stale) for r in rows]

    def _document_stamp(self) -> str:
        import hashlib
        import json

        return hashlib.sha1(json.dumps(self._require_doc().as_dict(), sort_keys=True,
                                       default=str).encode()).hexdigest()

    def _apply(self, edit) -> dict:
        """Put an authored edit into the document, or leave it untouched.

        One place where features and parameters reach the document, so there is
        one place where a failed rebuild has to undo them -- the bugs this
        prevents are the ones where half an edit survives a refusal.
        """
        doc = self._require_doc()
        with self._edit():
            doc.parameters.update(edit.parameters)
            doc.features.extend(edit.features)
            check_ids_once(doc.features)
            doc.result = edit.result
            out = self._rebuild()
        out["feature"] = edit.result
        out.update({k: v for k, v in edit.info.items() if v is not None})
        out["parameters"] = dict(doc.parameters)
        return out

    @contextmanager
    def _edit(self):
        """Run a document edit so that it can be undone, or never happened.

        Snapshot first; a refusal restores, a success is the next undo step.
        This is the only place the document is allowed to change.
        """
        doc = self._require_doc()
        if self._editing:            # an edit inside an edit is part of the outer one
            self._editing += 1
            try:
                yield doc
            finally:
                self._editing -= 1
            return
        self._editing = 1
        before = doc.snapshot()      # a real copy: the live dicts would move with the edit
        if self._drag is None:
            self._stamp_revision()   # now, so the rebuild inside reports the new state
        # and everything a rebuild rewrites, or a refused edit leaves them as it made them
        body_before, evaluator_before = self.body, self.evaluator
        view_before, names_before = self.view_upto, self._last_names
        try:
            yield doc
            doc.check_units()        # a document its own loader would refuse is refused here
        except Exception:
            self._restore(before)
            self.view_upto, self._last_names = view_before, names_before
            if evaluator_before is not None and evaluator_before.doc is doc:
                # built on the edited object: rebuilt from the cache, now, so a
                # reader that looks at `body` sees the state before the edit
                self.body = self.evaluator = None
                if body_before is not None:
                    try:
                        self._rebuild()
                    except CadError:
                        pass
            else:
                self.body, self.evaluator = body_before, evaluator_before
            raise
        finally:
            self._editing = 0
        if self._drag is not None:
            return                   # a trial inside a drag: end_drag records the lot as one
        # what this edit cost, so an op that turns out to be a no-op (a drag
        # of a held point) can hand it back: see _forget_last_edit
        self._undid = (list(self.redone), self.saved)
        self.undone.append(before)
        del self.undone[:-64]
        self.redone.clear()
        self.saved = False
        self._autosave()

    def _autosave(self) -> None:
        """Write the edit beside the document, under its own name.

        Not a save: the document's own file is untouched until asked. It is
        what makes a kernel crash cost the last operation, not the session.
        """
        if not self.path or not self.autosave:
            return                        # nowhere to put it, or nobody wants it
        try:
            target = Path(autosave_path(self.path))
            tmp = target.with_name(target.name + ".saving")   # whole or not at all, like save
            tmp.write_text(json.dumps(self.doc.as_dict(), indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, target)
        except OSError:
            pass          # a read-only directory is not a reason to refuse an edit

    def _drop_autosave(self) -> None:
        """Forget the sidecar: what it held is in the document now."""
        if not self.path:
            return
        try:
            Path(autosave_path(self.path)).unlink(missing_ok=True)
        except OSError:
            pass

    def _forget_last_edit(self) -> None:
        """Undo the edit just made, without it having been an edit at all.

        For an op that has to try something to find out whether it does
        anything (dragging a held point); `op_undo` would leave it on the redo stack.
        """
        if self._editing or self._drag is not None:
            return          # part of an outer edit or a drag, which records or forgets it whole
        if self.undone:
            self._restore(self.undone.pop())
            self._rebuild()
        redone, saved = getattr(self, "_undid", None) or (None, None)
        if redone is not None:
            self.redone[:] = redone
            self.saved = saved
            if saved:
                self._drop_autosave()
            else:
                self._autosave()
            self._undid = None

    def _restore(self, snapshot: "Document") -> None:
        """Put a snapshot back: plain assignment, so it cannot fail and hide the caller's refusal."""
        self.doc = snapshot
