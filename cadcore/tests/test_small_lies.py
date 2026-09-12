"""Answers from the kernel that were slightly other than what happened. None
is a wrong solid; each misleads whoever acts on the answer."""
import pytest

from cadcore.geometry import kernel
from cadcore.errors import CadError
from cadcore.model.document import Document


def test_a_face_a_cut_renamed_is_not_a_face_that_is_gone():
    """A cut reverses its tool's axis roles (the drill's bottom becomes the
    pocket floor `t/+z`); a renamed face is not reported in `dropped`."""
    block = kernel.box("p1", (40.0, 30.0, 20.0))
    drill = kernel.cylinder("t", 5.0, 8.0, at=(0.0, 0.0, 4.0), centred=False)
    pocket = kernel.cut("c", block, drill)

    live = {name for name, _ in pocket.names}
    assert "t/+z" in live, "the pocket floor"
    assert not set(pocket.dropped) & live, "a name cannot be both gone and there"
    assert pocket.dropped == [], pocket.dropped


def test_a_root_of_a_negative_number_is_not_a_size():
    doc = Document.from_dict({"parameters": {}, "result": "x", "features": []})
    with pytest.raises(CadError) as caught:
        doc.evaluate("(-2) ** 0.5")
    assert caught.value.kind == "bad_expression"
    assert "real" in caught.value.message


def test_a_list_to_a_power_is_refused_for_being_a_list():
    """`[1, 2] ** 2` is refused as `bad_expression`, not as too big."""
    doc = Document.from_dict({"parameters": {}, "result": "x", "features": []})
    with pytest.raises(CadError) as caught:
        doc.evaluate("[1, 2] ** 2")
    assert caught.value.kind == "bad_expression"
    assert "two numbers" in caught.value.message


def test_a_second_thread_does_not_erase_the_first():
    """`notes["threads"]` records every thread; `notes["thread"]` is the
    latest one."""
    shaft = kernel.cylinder("shaft", 8.0, 90.0, at=(0.0, 0.0, 0.0), centred=False)
    once = kernel.thread("t1", shaft, "shaft/side", pitch=2.0, length=20.0,
                         start=2.0)
    assert [t["length_mm"] for t in once.notes["threads"]] == [20.0]
    assert once.notes["thread"] is once.notes["threads"][-1]


def test_what_is_broken_can_be_asked_when_a_feature_reference_is_broken():
    """`broken_references` reports a feature that names a missing feature,
    rather than failing because `Evaluator.order()` refuses the document."""
    from cadcore.service.server import Session

    session = Session(autosave=False)
    session.op_open("examples/bracket.json")
    session.op_build()
    session.doc.feature("round_corners").args["body"] = "no_such_feature"

    out = session.op_broken_references()
    assert out["built_upto"] == "root_fillet"
    named = [(b["feature"], b["name"], b["why"]) for b in out["broken"]]
    assert ("round_corners", "no_such_feature", "no such feature") in named


def test_a_drag_that_moves_nothing_gives_back_everything_it_took():
    """A held drag (one that moves nothing) leaves the redo stack, the saved
    flag and the sidecar as they were, even though `_edit` had to try it."""
    import json
    import os
    import tempfile

    from cadcore.service.server import Session

    document = json.load(open("examples/sketch_plate.json", encoding="utf-8"))
    path = os.path.join(tempfile.mkdtemp(), "plate.json")
    open(path, "w", encoding="utf-8").write(json.dumps(document))
    sidecar = path.replace(".json", ".autosave.json")

    session = Session(autosave=True)
    session.op_open(path)
    session.op_build()
    sketch = next(f.id for f in session.doc.features if f.type == "sketch")

    assert session.saved and not os.path.exists(sidecar)
    assert session.op_drag_point(sketch=sketch, point="o", to=[500.0, 500.0])["held"]
    assert session.saved, "a drag that moved nothing marked the document unsaved"
    assert not os.path.exists(sidecar), "and wrote a sidecar for it"

    session.op_set_parameter("width", 118.0)
    session.op_undo()
    assert len(session.redone) == 1
    assert session.op_drag_point(sketch=sketch, point="o", to=[500.0, 500.0])["held"]
    assert len(session.redone) == 1, "a held drag threw away the redo stack"
