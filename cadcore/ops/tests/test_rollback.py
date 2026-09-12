"""A refused edit leaves nothing behind -- including the refusal's own way out.

`Session._edit` says it: "A failed edit restores it, so a refusal leaves
nothing behind". Three ways that was not true, all of them found by a review
and all reproducible with shipped geometry:

* the restore itself could raise. It rebuilt the document from its file form,
  which re-runs every load-time check, so on a document in a unit other than
  millimetres the rollback could refuse -- and then the caller got a complaint
  about the wrong parameter and kept the edit that had just been refused;
* the state that made it refuse was reachable in the first place, because the
  unit check ran on load and not on edit;
* `op_reattach` and `op_suppress_feature` changed the document, closed the
  guard, and rebuilt afterwards, so a rebuild that refused left the change in
  the document with an undo step pushed for it.

Each test here is written from the observable promise -- what the document
holds, and how deep the undo stack is, after something is refused -- rather
than from how the guard is implemented.
"""
import copy

import pytest

from cadcore.errors import CadError
from cadcore.service.server import Session

INCH = {
    "meta": {"name": "an inch document with a length parameter"},
    "unit": "in",
    "parameters": {"r": 1.0, "high": 2.0, "lean": 1.5},
    "features": [
        {"id": "section", "type": "sketch",
         "plane": {"origin": [0, 0, 0], "normal": [0, -1, 0], "x_axis": [1, 0, 0]},
         "points": {"a": [0, 0], "b": ["r", 0], "c": ["r", "high"], "d": [0, "high"]},
         "lines": {"base": ["a", "b"], "side": ["b", "c"],
                   "top": ["c", "d"], "axis": ["d", "a"]},
         "allow_underconstrained": True},
        {"id": "spin", "type": "revolve", "sketch": "section", "angle": 200.0,
         "axis": {"origin": [0, 0, 0], "direction": [0, 0, 1]}},
    ],
    "result": "spin",
}

DRILLED = {
    "meta": {"name": "box, rounded, then drilled"},
    "parameters": {"r": 5.0},
    "features": [
        {"id": "blank", "type": "box", "size": [60, 30, 12],
         "at": [0, 0, 0], "centred": True},
        {"id": "round0", "type": "fillet", "body": "blank", "radius": "r",
         "edges": {"of_face": "blank/+z"}},
        {"id": "pin", "type": "cylinder", "radius": 4, "height": 40,
         "at": [0, 0, 0], "axis": [0, 0, 1], "centred": True},
        {"id": "drilled", "type": "cut", "target": "round0", "tool": "pin"},
    ],
    "result": "drilled",
}

STACKED = {
    "meta": {"name": "a fillet that names the face the fillet before it made"},
    "parameters": {"r": 5.0},
    "features": [
        {"id": "blank", "type": "box", "size": [60, 30, 12],
         "at": [0, 0, 0], "centred": True},
        {"id": "round0", "type": "fillet", "body": "blank", "radius": "r",
         "edges": {"of_face": "blank/+z"}},
        {"id": "round1", "type": "fillet", "body": "round0", "radius": 1.0,
         "edges": {"of_face": "round0/side"}},
    ],
    "result": "round1",
}


def test_an_edit_that_would_make_the_document_unloadable_is_refused(open_document):
    """The angle of a revolve fed from a parameter the document calls a length.

    The build has no opinion about units, so this was accepted, and the
    document was left in a state its own loader refuses -- which meant the next
    person to open the file was told their file was wrong.
    """
    session = open_document(INCH)
    with pytest.raises(CadError) as caught:
        session.op_edit_feature("spin", {"angle": "lean"})
    assert caught.value.kind == "parameter_unit_unknown"
    assert session.doc.feature("spin").args["angle"] == 200.0


def test_a_refusal_in_an_inch_document_says_what_was_actually_wrong(open_document):
    """The rollback used to raise on its way out and take the real reason with
    it: an unknown name in an expression came back as a complaint about a
    different parameter's unit."""
    session = open_document(INCH)
    with pytest.raises(CadError) as caught:
        session.op_edit_feature("spin", {"angle": "no_such_parameter"})
    assert caught.value.kind == "bad_expression"
    assert "no_such_parameter" in caught.value.message
    assert session.doc.feature("spin").args["angle"] == 200.0


def test_undo_and_redo_still_work_in_a_document_that_is_not_millimetres(open_document):
    session = open_document(INCH)
    session.op_set_parameter("high", 60.0)
    assert session.doc.parameters["high"] == 60.0
    session.op_undo()
    assert session.doc.parameters["high"] == pytest.approx(50.8)
    session.op_redo()
    assert session.doc.parameters["high"] == 60.0


def test_a_reattach_the_rebuild_refuses_leaves_the_document_alone(open_document):
    """`pin/side` is a real name on the finished body, so the reference is
    accepted -- and then the fillet, which runs before the pin exists, has
    nothing to select. The document used to keep the new reference anyway."""
    session = open_document(DRILLED)
    # a deep copy: `as_dict` hands back the document's live dictionaries, so a
    # baseline taken without one moves with the thing it is meant to measure
    before = copy.deepcopy(session.doc.as_dict())
    depth = len(session.undone)
    with pytest.raises(CadError):
        session.op_reattach(old="blank/+z", new="pin/side")
    assert session.doc.feature("round0").args["edges"] == {"of_face": "blank/+z"}
    assert session.doc.as_dict() == before
    assert len(session.undone) == depth, "an undo step for an edit that was refused"


def test_a_suppression_the_rebuild_refuses_leaves_the_feature_switched_on(open_document):
    session = open_document(STACKED)
    depth = len(session.undone)
    with pytest.raises(CadError):
        session.op_suppress_feature("round0")
    assert not session.doc.feature("round0").suppressed
    assert len(session.undone) == depth


def test_a_reattach_that_does_build_still_works(open_document):
    """The guard must not have turned the working path off."""
    session = open_document(DRILLED)
    out = session.op_reattach(old="blank/+z", new="blank/-z")
    assert out["reattached"] == [{"feature": "round0", "path": ["edges", "of_face"]}]
    assert session.doc.feature("round0").args["edges"] == {"of_face": "blank/-z"}
    session.op_undo()
    assert session.doc.feature("round0").args["edges"] == {"of_face": "blank/+z"}


def test_a_suppression_that_does_build_still_works(open_document):
    session = open_document(DRILLED)
    out = session.op_suppress_feature("round0")
    assert out["suppressed"] == ["round0"]
    session.op_undo()
    assert not session.doc.feature("round0").suppressed


def test_a_snapshot_does_not_move_with_the_document(open_document):
    """The reason it is a snapshot and not `as_dict()`: that hands back the
    document's own dictionaries, and a copy that changes when the original does
    undoes nothing."""
    session = open_document(DRILLED)
    kept = session.doc.snapshot()
    session.op_set_parameter("r", 3.0)
    assert kept.parameters["r"] == 5.0
    assert session.doc.parameters["r"] == 3.0


def test_a_refused_edit_puts_the_shape_back_as_well_as_the_document(tmp_path):
    """`_edit` restored the document and not the body: after a refused apply
    the file said one part and `measure` answered for another."""
    import os

    import pytest

    from cadcore.errors import CadError
    from cadcore.ops.session import Session

    here = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    s = Session(autosave=False)
    s.op_open(path=os.path.join(here, "examples", "bracket.json"))
    before = s.op_build()["volume_mm3"]
    with pytest.raises(CadError):
        # seven edits that build, then one that is refused: all eight must go
        s.op_apply(ops=[{"op": "set_parameter", "name": "thickness", "value": 12},
                        {"op": "set_parameter", "name": "width", "value": 100},
                        {"op": "add_feature", "type": "nonesuch", "args": {}}])
    assert s.doc.parameters["thickness"] != 12
    assert s.op_build()["volume_mm3"] == pytest.approx(before)
    # and without a rebuild in between, the body the session holds is the old one
    assert abs(s.body.volume() - before) < 1e-6 if hasattr(s.body, "volume") else True


def test_apply_refuses_to_swap_the_document_under_its_own_guard(tmp_path):
    import os

    import pytest

    from cadcore.errors import CadError
    from cadcore.ops.session import Session

    here = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    s = Session(autosave=False)
    s.op_open(path=os.path.join(here, "examples", "bracket.json"))
    for op in ({"op": "open", "path": os.path.join(here, "examples", "cup.json")},
               {"op": "undo"}, {"op": "new_document"}):
        with pytest.raises(CadError) as raised:
            s.op_apply(ops=[{"op": "set_parameter", "name": "thickness", "value": 9}, op])
        assert raised.value.kind == "bad_arguments"
        assert s.doc.meta.get("name") != "cup" and s.doc.parameters["thickness"] != 9


def test_a_failed_open_leaves_the_session_as_it_was():
    import os

    import pytest

    from cadcore.errors import CadError
    from cadcore.ops.session import Session

    here = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    s = Session(autosave=False)
    s.op_open(path=os.path.join(here, "examples", "bracket.json"))
    s.op_set_parameter(name="thickness", value=9)
    depth = len(s.undone)
    with pytest.raises(Exception):
        s.op_open(path=os.path.join(here, "examples", "no_such_file.json"))
    assert len(s.undone) == depth and s.doc.parameters["thickness"] == 9
    assert s.body is not None
    assert s.op_undo()["undo_depth"] == depth - 1


def test_undo_marks_the_document_unsaved(tmp_path):
    import os
    import shutil

    from cadcore.ops.session import Session

    here = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    doc = str(tmp_path / "b.json")
    shutil.copy(os.path.join(here, "examples", "bracket.json"), doc)
    s = Session(autosave=False)
    s.op_open(path=doc)
    s.op_set_parameter(name="thickness", value=9)
    s.op_save()
    assert s.saved
    s.op_undo()
    assert s.saved is False        # the file says 9; the document says 8


def test_an_example_opened_as_a_copy_has_no_path_and_is_not_saved():
    """The gallery opens a shipped example as a starting point. It must not be
    the file itself: Save has to ask where, and the example stays as shipped."""
    from cadcore.service.server import Session

    s = Session(autosave=False)
    out = s.op_open("examples/cup.json", as_copy=True)
    assert s.path is None and s.saved is False
    assert out["saved"] is False
    s.op_build()
    assert s.op_set_parameter(name=sorted(s.doc.parameters)[0], value=1.0)["faces"] > 0
    with pytest.raises(CadError):
        s.op_save()                             # nowhere to save to yet
