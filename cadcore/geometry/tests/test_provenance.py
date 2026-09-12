"""Body notes (stock, bends, patch gaps) are merged by one declared rule.

`cadcore/geometry/core/provenance.py` declares per note kind how two bodies'
notes combine. Merging per kind rather than concatenating matters when a
body is joined to a copy of itself: a bracket mirrored onto itself with
`merge` would otherwise carry each bend twice and double its allowance.
Rigid moves keep bends, because the blank is laid out relative to the bends.
"""
import ast
import pathlib
import re

import pytest

from .. import kernel
from ..core import provenance
from cadcore.evaluation.api import build
from cadcore.model.document import Document
from cadcore.errors import CadError

ROOT = pathlib.Path(__file__).resolve().parents[3]

BRACKET = {
    "meta": {"name": "a bracket with two bends"},
    "parameters": {"thickness": 2.0, "width": 60.0, "depth": 40.0,
                   "wall": 20.0, "lip": 8.0, "radius": 2.0},
    "features": [
        {"id": "blank", "type": "sketch",
         "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
         "points": {"a": [0, 0], "b": ["depth", 0],
                    "c": ["depth", "width"], "d": [0, "width"]},
         # not "top": the sheet names its own faces `top` and `bottom`, and a
         # line of the same name makes `plate/top` ambiguous
         "lines": {"front": ["a", "b"], "right": ["b", "c"],
                   "back": ["c", "d"], "left": ["d", "a"]},
         "allow_underconstrained": True},
        {"id": "plate", "type": "sheet", "sketch": "blank",
         "thickness": "thickness"},
        {"id": "wall", "type": "flange", "body": "plate",
         "edge": "plate/right|plate/top", "length": "wall",
         "angle": 90, "radius": "radius"},
    ],
    "result": "wall",
}


def _built(raw, result=None):
    import copy

    doc = Document.from_dict(copy.deepcopy(raw))
    if result:
        doc.result = result
    return build(doc)[0]


def test_a_bracket_records_its_bends():
    body = _built(BRACKET)
    assert len(body.notes["bends"]) == 1
    assert body.notes["sheet"]["thickness_mm"] == 2.0


def test_mirroring_a_bracket_onto_itself_does_not_give_it_its_bends_twice():
    """Guards: a bracket mirrored onto itself keeps one record per bend."""
    import copy

    raw = copy.deepcopy(BRACKET)
    raw["features"].append({"id": "both", "type": "mirror", "body": "wall",
                            "plane": [0, 1, 0], "merge": True})
    raw["result"] = "both"
    one, both = _built(BRACKET), _built(raw)
    assert len(both.notes["bends"]) == len(one.notes["bends"])


def test_a_part_joined_to_a_copy_of_itself_will_not_be_laid_out_flat():
    """Guards: a body joined to a copy of itself has an incomplete bend record
    and is refused a flat pattern."""
    import copy

    raw = copy.deepcopy(BRACKET)
    raw["features"].append({"id": "both", "type": "mirror", "body": "wall",
                            "plane": [0, 1, 0], "merge": True})
    raw["result"] = "both"
    with pytest.raises(CadError) as caught:
        kernel.flat_pattern(_built(raw))
    assert caught.value.kind == "not_sheet_metal"
    assert "copy of itself" in caught.value.message


def test_a_part_that_has_not_been_moved_still_lays_out():
    flat = kernel.flat_pattern(_built(BRACKET))
    assert flat["bend_allowance_mm"] > 0
    assert flat["thickness_mm"] == 2.0


@pytest.mark.parametrize("move", [
    {"type": "translate", "offset": [100, 0, 0]},
    {"type": "pattern", "count": 2, "spacing": 300.0, "direction": [1, 0, 0]},
])
def test_moving_a_part_does_not_change_the_blank_it_is_cut_from(move):
    """The layout is walked relative to the bends, so a rigid move does not
    change the blank; a move must keep the bend notes."""
    import copy

    raw = copy.deepcopy(BRACKET)
    raw["features"].append(dict({"id": "moved", "body": "wall"}, **move))
    raw["result"] = "moved"
    here, there = kernel.flat_pattern(_built(BRACKET)), kernel.flat_pattern(_built(raw))
    assert there["bend_allowance_mm"] == pytest.approx(here["bend_allowance_mm"])
    assert there["blank"] == here["blank"]
    assert there["extent_mm"] == here["extent_mm"]


def test_a_thickness_is_measured_from_the_face_it_starts_at():
    """The thickness ray starts a hair inside the material so it does not hit
    the face it leaves; that offset is added back so 2.0 mm reads as 2.0."""
    assert kernel.flat_pattern(_built(BRACKET))["thickness_mm"] == 2.0


def test_joining_two_lists_does_not_repeat_what_is_already_there():
    a = type("B", (), {"notes": {"bends": [{"at": 1}, {"at": 2}]}})()
    b = type("B", (), {"notes": {"bends": [{"at": 2}, {"at": 3}]}})()
    assert provenance.merged([a, b])["bends"] == [{"at": 1}, {"at": 2}, {"at": 3}]


def test_the_first_body_with_a_one_of_a_kind_note_keeps_it():
    a = type("B", (), {"notes": {"sheet": {"thickness_mm": 2.0}}})()
    b = type("B", (), {"notes": {"sheet": {"thickness_mm": 3.0}}})()
    assert provenance.merged([a, b])["sheet"]["thickness_mm"] == 2.0


def test_every_note_this_kernel_writes_says_how_two_of_them_combine():
    """Every note key written anywhere in `cadcore` has a rule in
    `provenance.NOTES`."""
    written = set()
    for path in sorted(ROOT.rglob("cadcore/**/*.py")):
        if path.name == "provenance.py":
            continue
        text = path.read_text(encoding="utf-8")
        written |= set(re.findall(r'notes\["([a-z_]+)"\]', text))
        written |= set(re.findall(r'notes\.setdefault\("([a-z_]+)"', text))
    undeclared = sorted(written - set(provenance.NOTES))
    assert not undeclared, (
        "a note nothing says how to combine: " + ", ".join(undeclared)
        + " -- add it to provenance.NOTES, saying how two of them merge and "
          "whether it is still true after the body has been moved")


def test_a_note_nobody_declared_is_treated_as_the_careful_case():
    """An undeclared note is kept once (`merge == "one"`), which cannot
    invent an entry."""
    assert provenance.rule("something_nobody_declared").merge == "one"


def test_a_join_that_swallows_a_copy_says_so():
    same = [{"at": 1}]
    a = type("B", (), {"notes": {"bends": list(same)}})()
    b = type("B", (), {"notes": {"bends": list(same)}})()
    joined = provenance.merged([a, b], joining=True)
    assert joined["bends"] == same, "kept once"
    assert joined[provenance.LOST] == [{"note": "bends",
                                        "why": "joined with a copy of itself"}]
    beside = provenance.merged([a, b])
    assert provenance.LOST not in beside, \
        "two parts kept side by side are still two parts"


def test_nothing_hand_builds_a_body_and_forgets_what_it_recorded():
    """Guards: `Body(shape, names, aliases, dropped)` leaves `notes` at its
    default; a body rebuilt from another must carry its provenance too."""
    guilty = []
    for path in sorted(ROOT.rglob("cadcore/**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") == "Body"):
                continue
            if len(node.args) != 4:
                continue                  # 0-2 args: made from nothing; 5: carries
            # four positional args: aliases and dropped were carried, notes was not
            carried = any(isinstance(a, ast.Call) and a.args for a in node.args[2:])
            if carried:
                guilty.append("%s:%d" % (path.relative_to(ROOT), node.lineno))
    assert not guilty, ("a body rebuilt from another one, carrying its aliases "
                        "and dropping what it recorded: " + ", ".join(guilty))
