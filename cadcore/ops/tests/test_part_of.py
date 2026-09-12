"""From a face to the body the assembly places, and to what places it."""
from __future__ import annotations

import os

import pytest

from cadcore.errors import CadError
from cadcore.ops.session import Session

TWO = {"parameters": {"dx": 10}, "features": [
    {"id": "a", "type": "box", "size": [30, 30, 10]},
    {"id": "b", "type": "cylinder", "radius": 6, "height": 20},
    {"id": "tb", "type": "translate", "body": "b", "offset": ["dx", 0, 0]},
    {"id": "asm", "type": "assemble", "bodies": ["a", "tb"]}], "result": "asm"}


def test_a_face_names_its_feature_and_the_body_is_found_through_the_chain():
    s = Session(autosave=False)
    s.op_load_json(document=TWO)
    said = s.op_part_of(face="b/side")
    assert said["body"] == "tb" and said["positioner"] == "tb" and said["mate"] is None
    assert said["offset"] == ["dx", 0, 0] and said["offset_mm"] == [10.0, 0.0, 0.0]
    assert s.op_part_of(face="a/+z")["body"] == "a"
    assert s.op_part_of(face="a/+z")["positioner"] is None


def test_a_part_placed_by_a_mate_says_so(tmp_path):
    import os

    here = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    s = Session(autosize=False) if False else Session(autosave=False)
    s.op_open(path=os.path.join(here, "examples", "assembly.json"))
    said = s.op_part_of(face="bush:barrel/side")
    assert said["body"] in ("fitted", "bush")
    assert said["positioner"] is None
    assert said["mate"] == "fitted"


def test_one_body_has_nothing_to_move_relative_to():
    s = Session(autosave=False)
    s.op_open(path=os.path.join(os.path.dirname(__file__), "..", "..", "..",
                                "examples", "bracket.json"))
    with pytest.raises(CadError) as raised:
        s.op_part_of(face="plate/+z")
    assert raised.value.kind == "no_assembly"
