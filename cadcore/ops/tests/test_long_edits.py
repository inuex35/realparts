"""Editing for a long time, switching documents, and failing halfway.

Each piece of the session -- the undo stack, the checkpoints, the batch, the
open drag -- was right on its own. These are the runs of operations that
crossed two of them and came out wrong.
"""
from __future__ import annotations

import inspect
import os

import pytest

from cadcore.errors import CadError
from cadcore.ops.subjects import documents, modelling, requirements, sketches
from cadcore.ops.subjects.documents import EDITS_IN_A_BATCH
from cadcore.service.server import Session

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
BRACKET = os.path.join(HERE, "examples", "bracket.json")


def _open(path=BRACKET) -> Session:
    s = Session(autosave=False)
    s.op_open(path=path)
    return s


def _edge(s: Session) -> str:
    """An edge of the bracket's top between two faces of the plate, by a name
    the edge table carries (a split face's "@n" is not on the edge table)."""
    s.op_build()
    return next(e for e in s.op_select_edges(query={"of_face": "plate/+z"})["edges"]
                if all(side.startswith("plate/") and "@" not in side
                       for side in e.split("|")))


# -- a drag past the undo cap ---------------------------------------------------
def test_seventy_previews_are_one_undo_step():
    """A drag previews by editing the real document, once per mouse move.

    Recorded as steps, the previews pushed the real ones off the front of a
    stack capped at 64, and the count the drag kept to cut them back by then
    pointed at nothing: Ctrl+Z landed on a mid-drag shape. A drag is now a
    run of trials the kernel records as one step, however long it ran.
    """
    s = _open()
    volume = s.op_build()["volume_mm3"]
    depth = len(s.undone)
    s.op_begin_drag()
    edge = _edge(s)
    made = s.op_add_fillet(edges=[edge], radius=0.5)["feature"]
    for step in range(1, 70):
        s.op_edit_feature(feature_id=made, args={"radius": 0.5 + step * 0.02})
    assert len(s.undone) == depth, "a preview was recorded as a step"
    s.op_reset_drag()
    assert s.op_build()["volume_mm3"] == pytest.approx(volume)
    s.op_add_fillet(edges=[edge], radius=1.5)
    out = s.op_end_drag(keep=True)
    assert out["undo_depth"] == depth + 1
    assert out["volume_mm3"] < volume
    assert s.op_undo()["volume_mm3"] == pytest.approx(volume), \
        "undo landed somewhere in the drag, not before it"
    assert s.op_redo()["volume_mm3"] < volume


def test_a_cancelled_drag_never_happened():
    s = _open()
    volume = s.op_build()["volume_mm3"]
    s.op_set_parameter(name="thickness", value=9.0)
    s.op_undo()                                    # so there is something to redo
    redo_before, features_before = len(s.redone), len(s.doc.features)
    edge = _edge(s)
    s.op_begin_drag()
    made = s.op_add_fillet(edges=[edge], radius=1.0)["feature"]
    s.op_edit_feature(feature_id=made, args={"radius": 2.0})
    out = s.op_end_drag(keep=False)
    assert out["volume_mm3"] == pytest.approx(volume)
    assert len(s.redone) == redo_before, "cancelling a drag cost the redo stack"
    assert len(s.doc.features) == features_before, "the drag's fillet stayed"


def test_a_drag_left_open_is_dropped_by_the_next():
    s = _open()
    volume = s.op_build()["volume_mm3"]
    s.op_begin_drag()
    s.op_add_fillet(edges=[_edge(s)], radius=1.0)
    s.op_begin_drag()                               # the earlier one was abandoned
    assert s.op_build()["volume_mm3"] == pytest.approx(volume)
    # and cancelling *this* one lands on the original, not on the abandoned
    # drag's preview: the start snapshot was taken after the drop, not before
    s.op_add_fillet(edges=[_edge(s)], radius=2.0)
    out = s.op_end_drag(keep=False)
    assert out["volume_mm3"] == pytest.approx(volume), "the abandoned preview came back"
    assert not any(f.type == "fillet" and f.id.startswith("fillet") for f in s.doc.features[len(s.doc.features) - 1:])
    with pytest.raises(CadError) as refused:
        s.op_end_drag()
    assert refused.value.kind == "bad_arguments"


# -- checkpoints belong to the document ----------------------------------------
def test_a_checkpoint_does_not_outlive_its_document():
    """Checkpoint in A, open B, restore A's checkpoint: B's path, A's shape.

    Saved, that wrote A over B. A checkpoint is a shape of the document that
    made it, and goes when that document does.
    """
    a = _open()
    a.op_checkpoint(name="before")
    a.op_open(path=os.path.join(HERE, "examples", "cup.json"))
    assert a.op_checkpoints()["checkpoints"] == []
    with pytest.raises(CadError) as refused:
        a.op_restore(name="before")
    assert refused.value.kind == "unknown_reference"

    b = _open()
    b.op_checkpoint(name="before")
    b.op_new_document()
    assert b.op_checkpoints()["checkpoints"] == []

    c = _open()
    c.op_checkpoint(name="before")
    c.op_load_json(document=c.op_document_json()["document"])
    assert c.op_checkpoints()["checkpoints"] == []


def test_a_restore_is_the_whole_document():
    """Meta and unit come back with the shape, and the path stays put."""
    s = _open()
    s.op_checkpoint(name="before")
    s.doc.meta["name"] = "renamed"
    s.op_set_parameter(name="thickness", value=9.0)
    path_before = s.path
    out = s.op_restore(name="before")
    assert out["restored"] == "before"
    assert s.doc.meta.get("name") != "renamed"
    assert s.doc.parameters["thickness"] == 8
    assert s.path == path_before


# -- a batch is edits, and only edits -------------------------------------------
def test_a_batch_refuses_what_it_cannot_take_back(tmp_path):
    """An undo, an export or a read in a batch that then fails.

    The document came back and the reply said nothing was applied; the undo
    stack stayed changed and the file stayed written. Only what the edit
    guard undoes is allowed in, and an op that is not an op is said to be.
    """
    s = _open()
    s.op_set_parameter(name="thickness", value=9.0)
    depth = len(s.undone)
    target = tmp_path / "out.step"
    for first in ({"op": "export_step", "path": str(target)},
                  {"op": "describe_faces"},
                  {"op": "undo"},
                  {"op": "checkpoint", "name": "x"}):
        with pytest.raises(CadError) as refused:
            s.op_apply(ops=[first, {"op": "add_fillet", "edges": ["no|such"], "radius": 1}])
        assert refused.value.kind == "bad_arguments"
        assert refused.value.detail["step"] == 0
        assert "allowed" in refused.value.detail
    assert len(s.undone) == depth, "a refused batch changed the undo stack"
    assert not target.exists(), "a refused batch wrote a file"
    assert s.op_checkpoints()["checkpoints"] == []
    with pytest.raises(CadError) as refused:
        s.op_apply(ops=[{"op": "no_such_thing"}])
    assert refused.value.kind == "unknown_op"


def test_the_batch_list_is_exactly_the_edit_guard():
    """Every name allowed in a batch reaches `_edit`, and every op that
    reaches it is on the list -- so the list cannot drift from the guard."""
    sources = {}
    for module in (documents, modelling, sketches, requirements):
        for name, member in inspect.getmembers(module):
            if not inspect.isclass(member):
                continue
            for attr, fn in vars(member).items():
                if attr.startswith("op_") and callable(fn):
                    sources[attr[3:]] = inspect.getsource(fn)
    guarded = {name for name, src in sources.items()
               if "self._edit(" in src or "self._apply(" in src}
    # an op that hands its work to a guarded one is guarded too
    grew = True
    while grew:
        grew = False
        for name, src in sources.items():
            if name not in guarded and any("self.op_%s(" % g in src for g in guarded):
                guarded.add(name)
                grew = True
    # the ones that manage the history itself go through the guard but are
    # not edits of the shape; a batch has no business with them
    history = {"restore", "undo", "redo", "rollback", "apply"}
    assert EDITS_IN_A_BATCH <= guarded, sorted(EDITS_IN_A_BATCH - guarded)
    assert guarded - history <= EDITS_IN_A_BATCH, \
        "guarded edits missing from the batch list: %s" % sorted(guarded - history - EDITS_IN_A_BATCH)


def test_a_batch_of_edits_still_works():
    s = _open()
    volume = s.op_build()["volume_mm3"]
    out = s.op_apply(ops=[{"op": "add_fillet", "edges": [_edge(s)], "radius": 1.0},
                          {"op": "set_parameter", "name": "thickness", "value": 9.0}])
    assert out["applied"] == ["add_fillet", "set_parameter"]
    assert out["volume_mm3"] != pytest.approx(volume)
    assert s.op_undo()["volume_mm3"] == pytest.approx(volume), "a batch is one undo step"


def test_the_cap_still_holds_for_real_edits():
    """The cap is on real steps; the drag fix must not have loosened it."""
    s = _open()
    for i in range(70):
        s.op_set_parameter(name="thickness", value=8.0 + i * 0.01)
    assert len(s.undone) == 64


# -- a refusal after the rebuild is still all or nothing ------------------------
def test_a_bound_that_will_not_evaluate_keeps_nothing():
    """set_parameter rebuilt inside the guard and read the envelope outside it.

    A bound naming a parameter that does not exist raised there: the reply
    was a refusal, and the new value, an undo step and an autosave were all
    kept. What a reply is computed from is part of the edit.
    """
    s = _open()
    s.doc.bounds["thickness"] = [0, "no_such_parameter"]
    depth, was = len(s.undone), s.doc.parameters["thickness"]
    with pytest.raises(CadError):
        s.op_set_parameter(name="thickness", value=7.0)
    assert s.doc.parameters["thickness"] == was
    assert len(s.undone) == depth


def test_a_plane_whose_frame_cannot_be_worked_out_is_not_kept():
    s = _open()
    depth = len(s.doc.features)
    with pytest.raises(CadError):
        s.op_add_plane(normal=[0, 0, 0])          # no direction: no frame
    assert len(s.doc.features) == depth


def test_a_refused_edit_gives_back_the_rollback_view_and_the_last_names():
    """`_rebuild` drops the rollback view when its target is gone, and a reply
    rewrites the names the next `changed` reply is measured from. A refused
    edit put the document back and left those two as the refusal made them."""
    s = _open()
    s.op_rollback(feature_id="plate")
    assert s.view_upto == "plate"
    s.op_build()
    names = s._last_names
    with pytest.raises(CadError):
        s.op_set_parameter(name="no_such_parameter", value=1.0)
    assert s.view_upto == "plate"
    assert s._last_names == names


# -- a state has a number, and the number is how Blender asks for it ------------
def test_every_recorded_state_has_a_number_of_its_own():
    s = _open()
    first = s.doc.revision
    seen = {first}
    for i in range(3):
        rev = s.op_set_parameter(name="thickness", value=9.0 + i)["revision"]
        assert rev not in seen
        seen.add(rev)
    back = s.op_undo()["revision"]
    assert back in seen and back != rev
    assert s.op_redo()["revision"] == rev
    s.op_begin_drag()
    s.op_set_parameter(name="thickness", value=20.0)
    assert s.doc.revision == rev, "a preview took a number"
    assert s.op_end_drag(keep=False)["revision"] == rev
    assert s.op_describe_document()["revision"] == rev


def test_past_the_cap_an_undo_is_still_found_by_its_number():
    """Seventy edits leave the depth at 64 before and after the last one, so
    following by depth stopped following. By number it does not."""
    s = _open()
    revisions = []
    for i in range(70):
        revisions.append(s.op_set_parameter(name="thickness", value=8.0 + i * 0.01)["revision"])
    assert len(s.undone) == 64
    before_last = revisions[-2]
    out = s.op_goto_revision(revision=before_last)
    assert out["moved"] is True and s.doc.revision == before_last
    assert s.doc.parameters["thickness"] == pytest.approx(8.0 + 68 * 0.01)
    assert s.op_goto_revision(revision=before_last)["moved"] is False
    assert s.op_goto_revision(revision=revisions[-1])["moved"] is True
    with pytest.raises(CadError) as refused:
        s.op_goto_revision(revision=revisions[0])          # fell off the front
    assert refused.value.kind == "nothing_to_undo"


def test_a_restore_is_a_new_state_with_a_number_of_its_own():
    """Restoring a checkpoint wrote the checkpoint's number over the one the
    guard had just handed out, so the current state and one in the history
    shared a number and goto_revision could not tell them apart."""
    s = _open()
    s.op_checkpoint(name="before")
    was = s.doc.revision
    s.op_set_parameter(name="thickness", value=9.0)
    out = s.op_restore(name="before")
    assert out["revision"] != was
    assert out["revision"] == s.doc.revision
    assert all(d.revision != s.doc.revision for d in s.undone)
    assert s.op_goto_revision(revision=was)["moved"] is True
    assert s.doc.parameters["thickness"] == 8


def test_the_reply_that_ends_a_drag_carries_the_new_number():
    s = _open()
    s.op_begin_drag()
    preview = s.op_set_parameter(name="thickness", value=9.0)["revision"]
    kept = s.op_end_drag(keep=True)["revision"]
    assert preview == s.undone[-1].revision, "a preview took a number of its own"
    assert kept != preview and kept == s.doc.revision
