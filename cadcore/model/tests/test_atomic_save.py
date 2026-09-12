"""A save lands whole or not at all, and a drag is one undo step."""
from __future__ import annotations

import os

import pytest

from cadcore.errors import CadError
from cadcore.ops.session import Session

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def test_save_writes_beside_and_moves_over(tmp_path):
    s = Session(autosave=False)
    s.op_open(path=os.path.join(HERE, "examples", "bracket.json"))
    target = tmp_path / "out.json"
    s.op_save(path=str(target))
    assert target.exists()
    assert not (tmp_path / "out.json.saving").exists()


def test_a_drag_is_one_undo_step_or_none():
    s = Session(autosave=False)
    s.op_open(path=os.path.join(HERE, "examples", "bracket.json"))
    depth = s.op_describe_document()["undo_depth"]
    s.op_begin_drag()
    for t in (9, 10, 11, 12):                       # four previews
        s.op_set_parameter(name="thickness", value=t)
    assert s.op_describe_document()["undo_depth"] == depth, "a preview became a step"
    s.op_reset_drag()
    s.op_set_parameter(name="thickness", value=12)  # the one real edit
    assert s.op_end_drag(keep=True)["undo_depth"] == depth + 1
    s.op_undo()
    assert s.doc.parameters["thickness"] == 8         # straight to before the drag
    # and a cancelled drag leaves no step at all
    depth = s.op_describe_document()["undo_depth"]
    s.op_begin_drag()
    s.op_set_parameter(name="thickness", value=5)
    assert s.op_end_drag(keep=False)["undo_depth"] == depth
    assert s.doc.parameters["thickness"] == 8
    with pytest.raises(CadError):
        s.op_end_drag()
