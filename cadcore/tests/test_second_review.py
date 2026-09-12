"""Regression tests from a second code review: behaviour that passed the suite
while being wrong."""
import json
import math

import pytest
from conftest import plate

from cadcore.model.document import Document
from cadcore.errors import CadError
from cadcore.evaluation.graph import Evaluator
from cadcore.geometry.kernel import volume
from cadcore.service.server import Session
from cadcore.geometry.core.occ import bounds


def _build(document: dict, cache: dict | None = None):
    doc = Document.from_dict(document, None)
    evaluator = Evaluator(doc, cache if cache is not None else {})
    return evaluator.build(), evaluator


def _zspan(body) -> tuple:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.Add_s(body.shape, box)
    return (bounds(box)[2], bounds(box)[5])


def test_two_identical_features_keep_their_own_face_names():
    """Guards: the cache key includes the feature's own id, so two features
    with identical arguments keep their own face names."""
    body, _ = _build({"parameters": {}, "result": "f", "features": [
        {"id": "b1", "type": "box", "size": [10, 10, 10]},
        {"id": "b2", "type": "box", "size": [10, 10, 10]},
        {"id": "t", "type": "translate", "body": "b2", "offset": [6, 0, 0]},
        {"id": "f", "type": "fuse", "target": "b1", "tool": "t"}]})
    assert any(name.startswith("b2/") for name in body.face_names())


def test_a_midplane_depends_on_the_body_it_sits_between():
    """Guards: `between` references are graph dependencies. The plane orders
    after its body, and under a persistent cache the sketch on it moves when
    the body's height changes."""
    document = {"parameters": {"h": 20.0}, "result": "boss", "features": [
        # deliberately listed before the box it refers to
        {"id": "mid", "type": "plane", "between": [
            {"body": "blk", "face": "blk/+z"}, {"body": "blk", "face": "blk/-z"}]},
        {"id": "blk", "type": "box", "size": [30, 30, "h"], "at": [0, 0, "h"]},
        {"id": "sk", "type": "sketch", "on": {"plane": "mid"},
         "allow_underconstrained": True,
         "points": {"a": [-5, -5], "b": [5, -5], "c": [5, 5], "d": [-5, 5]},
         "lines": {"s": ["a", "b"], "e": ["b", "c"], "n": ["c", "d"],
                   "w": ["d", "a"]},
         "constraints": [{"type": "fix", "point": "a", "at": [-5, -5]}]},
        {"id": "boss", "type": "extrude", "sketch": "sk", "distance": 60}]}

    cache: dict = {}
    body, _ = _build(document, cache)
    assert _zspan(body) == pytest.approx((20.0, 80.0), abs=1e-6)

    document["parameters"]["h"] = 60.0           # the midplane moves to z=60
    moved, _ = _build(document, cache)           # same cache, like a session's
    assert _zspan(moved) == pytest.approx((60.0, 120.0), abs=1e-6)


def test_a_mirror_plane_is_usable_in_both_spellings():
    """A mirror plane may be a bare normal or {origin, normal}; declaration
    and handler accept both."""
    features = [{"id": "b", "type": "box", "size": [10, 10, 10], "at": [20, 0, 0]},
                {"id": "m", "type": "mirror", "body": "b", "plane": [1, 0, 0]}]
    body, _ = _build({"parameters": {}, "result": "m", "features": features})
    assert volume(body) == pytest.approx(2000.0, rel=1e-6)

    spelled = json.loads(json.dumps(features))
    spelled[1]["plane"] = {"origin": [5, 0, 0], "normal": [1, 0, 0]}
    body, _ = _build({"parameters": {}, "result": "m", "features": spelled})
    assert volume(body) == pytest.approx(2000.0, rel=1e-6)


def test_an_unmerged_pattern_keeps_every_instance():
    """merge=false gives every instance, unfused."""
    body, _ = _build({"parameters": {}, "result": "row", "features": [
        {"id": "pin", "type": "cylinder", "radius": 3, "height": 10},
        {"id": "row", "type": "pattern", "body": "pin", "count": 4,
         "spacing": 20, "direction": [1, 0, 0], "merge": False}]})
    assert volume(body) == pytest.approx(4 * math.pi * 9 * 10, rel=1e-6)
    assert "pin/side~3" in body.face_names()


def test_the_panel_can_edit_one_field_of_a_nested_argument(open_document):
    """`counterbore.diameter` edits one field of a spec argument; a head that
    is not a nested argument is refused."""
    document = plate()
    document["features"].append(
        {"id": "h", "type": "hole", "body": "plate", "face": "plate/top",
         "diameter": 6, "counterbore": {"diameter": 11, "depth": 2}})
    document["result"] = "h"
    session = open_document(document)

    out = session.op_edit_feature("h", {"counterbore.diameter": 12.0})
    assert out["args"]["counterbore"]["diameter"] == 12.0
    with pytest.raises(CadError) as exc:
        session.op_edit_feature("h", {"nothing.diameter": 3.0})
    assert exc.value.kind == "bad_arguments"


def test_opening_another_document_forgets_the_first_ones_undo(write_document):
    """Opening a document clears the undo stack, so undo cannot put the
    previous document's content under the new path."""
    first = write_document(plate(), "a.json")
    second = write_document(plate(parameters={"t": 8}), "b.json")
    session = Session()
    session.op_open(first)
    session.op_build()
    session.op_set_parameter("t", 6)

    session.op_open(second)
    session.op_build()
    with pytest.raises(CadError) as exc:
        session.op_undo()
    assert exc.value.kind == "nothing_to_undo"


def test_a_missing_argument_is_a_refusal_not_a_crash():
    """A missing required argument is `missing_argument`, not a KeyError."""
    for feature in (
            {"id": "r", "type": "fillet", "body": "b", "edges": ["b/+z|b/+x"]},
            {"id": "r", "type": "hole", "body": "b", "face": "b/+z"}):
        with pytest.raises(CadError) as exc:
            _build({"parameters": {}, "result": "r", "features": [
                {"id": "b", "type": "box", "size": [20, 20, 20]}, feature]})
        assert exc.value.kind == "missing_argument"


def test_the_top_view_is_taken_from_above():
    """The top view is seen from +z: a boss on the +z face is visible in it,
    not hidden."""
    from ..geometry.solids import booleans, primitives
    from cadcore.analysis.drawing import project

    slab = primitives.box("slab", [40, 40, 10])
    boss = primitives.box("boss", [8, 8, 6], at=[10, 0, 8])
    part = booleans.fuse("f", slab, boss)

    top = project(part, "top", hidden=True)

    def on_the_boss(line) -> bool:
        return all(abs(u - 10) < 5.5 and abs(v) < 5.5 for u, v in line)

    # from above the boss's top edges are visible (the buried base may still
    # add hidden lines)
    assert any(on_the_boss(line) for line in top.visible)


def test_one_drawn_circle_is_a_whole_profile(open_example):
    """A single circle is a complete profile; its only point is the centre."""
    session = open_example("sketch_plate.json")
    before = session.op_build()["volume_mm3"]
    out = session.op_add_sketch(face="plate/top", points=[[0.0, 0.0]], lines=[],
                                circles=[{"centre": 0, "radius": 5}],
                                operation="pocket", depth=3)
    assert out["volume_mm3"] < before


def test_a_tangent_spelled_as_a_pair_of_picks_resolves():
    """`tangent` with `of: [pick, pick]` works out which is the line and which
    the curve, and refuses a pair with no curve."""
    from cadcore.sketching import solve

    square = {"points": {"a": [0, 0], "b": [20, 0], "c": [20, 20], "d": [0, 20],
                         "e": [10, 8]},
              "lines": {"base": ["a", "b"], "east": ["b", "c"],
                        "lid": ["c", "d"], "west": ["d", "a"]},
              "circles": {"ring": {"centre": "e", "radius": 6}},
              "allow_underconstrained": True}
    spec = dict(square, constraints=[{"type": "fix", "point": "a", "at": [0, 0]},
                                     {"type": "tangent", "of": ["base", "ring"]}])
    result = solve(spec, lambda v: v)
    a, b = result.points["a"], result.points["b"]
    e = result.points["e"]
    along = (b[0] - a[0], b[1] - a[1])
    reach = abs(along[0] * (e[1] - a[1]) - along[1] * (e[0] - a[0])) \
        / math.hypot(*along)
    assert reach == pytest.approx(6.0, abs=1e-6)

    two_lines = dict(square,
                     constraints=[{"type": "tangent", "of": ["base", "lid"]}])
    with pytest.raises(CadError) as exc:
        solve(two_lines, lambda v: v)
    assert exc.value.kind == "bad_arguments"
