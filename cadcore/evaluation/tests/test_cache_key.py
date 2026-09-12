"""Guards `Evaluator.key_of` in both directions.

A key that changes when it need not costs a rebuild; a key that stays the
same when it should change hands back a solid that is not the document's.
The key evaluates each string leaf tolerantly, so it cannot tell an
expression from a sketch point name that matches a parameter: with
parameters `a` and `b` both equal to 1 and points `a` and `b`, two different
profiles must still get different keys.
"""
from __future__ import annotations

import pytest

from cadcore.model.document import Document
from cadcore.evaluation.graph import Evaluator
from cadcore.ops.session import Session

#: A pentagon with one reflex corner: exchanging two vertices leaves a simple
#: polygon of a different size.
POINTS = {"e1": [0, 0], "a": [5, 5], "b": [20, 0], "c1": [20, 20], "d1": [0, 20]}
ONE_WAY = {"l1": ["e1", "a"], "l2": ["a", "b"], "l3": ["b", "c1"],
           "l4": ["c1", "d1"], "l5": ["d1", "e1"]}
THE_OTHER = {"l1": ["e1", "b"], "l2": ["b", "a"], "l3": ["a", "c1"],
             "l4": ["c1", "d1"], "l5": ["d1", "e1"]}


def document(lines, **parameters) -> dict:
    return {
        "parameters": {"a": 1, "b": 1, **parameters},
        "features": [
            {"id": "profile", "type": "sketch",
             "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1],
                       "x_axis": [1, 0, 0]},
             "points": POINTS, "lines": lines,
             "constraints": [{"type": "fix", "point": p, "at": at}
                             for p, at in POINTS.items()]},
            {"id": "solid", "type": "extrude", "sketch": "profile",
             "distance": 10}],
        "result": "solid"}


def key(raw: dict, feature: str = "profile") -> str:
    evaluator = Evaluator(Document.from_dict(raw))
    evaluator.build()
    return evaluator.keys[feature]


def test_two_different_sketches_do_not_share_a_key():
    assert key(document(ONE_WAY)) != key(document(THE_OTHER))


def test_the_edit_that_was_thrown_away_is_kept():
    """Guards: through an edit, which keeps the cache (loading clears it)."""
    session = Session()
    session.op_load_json(document=document(ONE_WAY))
    assert session.op_build()["volume_mm3"] == pytest.approx(3500)
    session.op_edit_feature(feature_id="profile", args={"lines": THE_OTHER})
    after = session.op_build()["volume_mm3"]

    cold = Session()
    cold.op_load_json(document=document(THE_OTHER))
    assert after == pytest.approx(cold.op_build()["volume_mm3"])
    assert after == pytest.approx(2500)


def test_a_name_that_reads_like_a_parameter_is_still_a_name():
    """The two lines resolve to the same numbers and are not the same line."""
    one = Document.from_dict(document(ONE_WAY))
    other = Document.from_dict(document(THE_OTHER))
    walk = Evaluator(one)
    # both `a` and `b` evaluate, to the same value
    assert one.evaluate("a") == one.evaluate("b") == 1
    assert walk.key_of(one.features[0]) != Evaluator(other).key_of(
        other.features[0])


def test_an_expression_still_drives_the_key():
    """Guards: keeping the text of a resolved string must not stop the key
    following the parameter's value."""
    thin = document(ONE_WAY, height=10)
    thin["features"][1]["distance"] = "height"
    thick = document(ONE_WAY, height=25)
    thick["features"][1]["distance"] = "height"
    assert key(thin, "solid") != key(thick, "solid")

    session = Session()
    session.op_load_json(document=thin)
    assert session.op_build()["volume_mm3"] == pytest.approx(3500)
    session.op_set_parameter(name="height", value=25)
    assert session.op_build()["volume_mm3"] == pytest.approx(8750)


def test_the_same_document_twice_is_the_same_key():
    """A cache that never hits is not a cache."""
    assert key(document(ONE_WAY)) == key(document(ONE_WAY))


def _two_solids(**cut) -> dict:
    return {"features": [
        {"id": "a", "type": "box", "size": [30, 30, 10]},
        {"id": "b", "type": "cylinder", "radius": 10, "height": 20},
        {"id": "c", "type": "cut", **cut}], "result": "c"}


def test_swapping_target_and_tool_is_a_different_key():
    """cut{target: a, tool: b} and cut{tool: a, target: b} name the same two
    parents and are two different solids. Keyed by a list of parent keys in
    order of appearance they were one key, and a shared cache handed the
    second document the first one's body."""
    from cadcore.evaluation.api import build
    from cadcore.geometry.core.measure import volume

    cache: dict = {}
    first, _ = build(Document.from_dict(_two_solids(target="a", tool="b")), cache)
    second, ev = build(Document.from_dict(_two_solids(target="b", tool="a")), cache)
    fresh, _ = build(Document.from_dict(_two_solids(target="b", tool="a")), {})
    assert volume(second) == pytest.approx(volume(fresh))
    assert volume(second) != pytest.approx(volume(first))
    assert ev.stats["evaluated"] >= 1, "the swapped cut was served from the cache"


def test_a_repeated_reference_is_part_of_the_key():
    """loft.sketches [s0, s0, s1] and [s0, s1] name the same features."""
    doc = Document.from_dict({"features": [
        {"id": "s0", "type": "sketch", "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1],
                                                 "x_axis": [1, 0, 0]},
         "points": {"p": [0, 0], "q": [10, 0], "r": [10, 10]},
         "lines": {"l1": ["p", "q"], "l2": ["q", "r"], "l3": ["r", "p"]},
         "constraints": [{"type": "fix", "point": n, "at": at} for n, at in
                         {"p": [0, 0], "q": [10, 0], "r": [10, 10]}.items()]},
        {"id": "s1", "type": "sketch", "plane": {"origin": [0, 0, 5], "normal": [0, 0, 1],
                                                 "x_axis": [1, 0, 0]},
         "points": {"p": [0, 0], "q": [10, 0], "r": [10, 10]},
         "lines": {"l1": ["p", "q"], "l2": ["q", "r"], "l3": ["r", "p"]},
         "constraints": [{"type": "fix", "point": n, "at": at} for n, at in
                         {"p": [0, 0], "q": [10, 0], "r": [10, 10]}.items()]},
        {"id": "lofted", "type": "loft", "sketches": ["s0", "s1"]}], "result": "lofted"})
    ev = Evaluator(doc, {})
    for sketch in ("s0", "s1"):
        ev.prepare(sketch)                 # the parents' keys; the loft itself is not built
    keys = {}
    for sketches in (["s0", "s1"], ["s0", "s0", "s1"]):
        doc.feature("lofted").args["sketches"] = list(sketches)
        keys[tuple(sketches)] = ev.key_of(doc.feature("lofted"))
    assert keys[("s0", "s1")] != keys[("s0", "s0", "s1")]


def test_a_name_equal_to_a_feature_id_stays_in_the_key():
    """A Name argument is not a reference because its text matches an id."""
    doc = Document.from_dict({"features": [
        {"id": "top", "type": "box", "size": [10, 10, 10]},
        {"id": "h", "type": "hole", "body": "top", "face": "top/+z", "diameter": 3}],
        "result": "h"})
    ev = Evaluator(doc, {})
    ev.prepare("h")
    one = ev.keys["h"]
    doc.feature("h").args["face"] = "top/-z"
    ev = Evaluator(doc, {})
    ev.prepare("h")
    assert ev.keys["h"] != one
