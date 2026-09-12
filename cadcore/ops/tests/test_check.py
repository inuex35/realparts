"""One call that answers "is this ready", and says why when it is not.

The pieces were all here and all separate, so each was something somebody had
to remember to run. What these ask is that the report is honest in both
directions: it does not pass a broken part, and it does not fail a sound one
for having an overhang.
"""
from __future__ import annotations

import pytest

from cadcore.service.server import Session


def check(path, **kw):
    session = Session(autosave=False)
    session.op_open(path)
    return session.op_check(**kw)


def by_name(report):
    return {line["check"]: line for line in report["checks"]}


def test_a_sound_part_passes():
    out = check("examples/bracket.json")
    assert out["ok"] is True
    assert out["failed"] == []
    said = by_name(out)
    assert said["builds"]["ok"]
    assert said["inside its own envelope"]["ok"]
    assert said["every reference resolves"]["ok"]


def test_an_overhang_is_advice_and_not_a_fault():
    """A bracket has an overhang. That is a support, not a defect.

    Failing the whole report for it is how a check becomes one nobody reads.
    """
    out = check("examples/bracket.json")
    printing = by_name(out)["a printer could make it"]
    assert printing["ok"] is False
    assert printing["severity"] == "advice"
    assert out["ok"] is True
    assert printing["check"] in out["warned"]


def test_a_face_a_cutting_tool_lost_is_not_a_broken_reference():
    """`bush:hole/+z` is the end cap of a cylinder used to cut a bore.

    It is *supposed* to disappear. Counting it as a fault failed the shipped
    assembly, which is exactly the false alarm that empties a report of
    meaning.
    """
    out = check("examples/assembly.json")
    names = by_name(out)["every reference resolves"]
    assert names["ok"] is True
    assert "bush:hole/+z" in names["detail"]["dropped"]


def test_an_assembly_is_asked_whether_its_parts_collide():
    out = check("examples/assembly.json")
    assert by_name(out)["the parts do not collide"]["ok"] is True


def test_a_part_on_its_own_is_not():
    """Nothing to collide with is not a check worth drawing a line for."""
    assert "the parts do not collide" not in by_name(check("examples/bracket.json"))


def test_leaving_the_envelope_is_a_fault():
    session = Session(autosave=False)
    session.op_open("examples/bracket.json")
    lo, hi = session.doc.bounds["thickness"]
    session.doc.parameters["thickness"] = hi + 1
    out = session.op_check()
    assert out["ok"] is False
    assert "inside its own envelope" in out["failed"]
    assert "thickness" in by_name(out)["inside its own envelope"]["said"]


def test_printing_can_be_left_out():
    out = check("examples/bracket.json", printing=False)
    assert "a printer could make it" not in by_name(out)


def test_every_line_says_which_kind_it_is():
    for line in check("examples/bracket.json")["checks"]:
        assert line["severity"] in ("must", "advice"), line
