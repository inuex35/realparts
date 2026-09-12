"""Assemblies placed by solving all mates together.

The solver reports remaining degrees of freedom per part, as motions. The
checks are solver-independent facts: a bush in a bore can spin and slide
only, a bush seated by its flange lands where the measured offset put it,
and two mates that disagree cannot both be satisfied.
"""
import json
import math
import shutil

import numpy as np
import pytest

from ..assembly import placement
from ..assembly.assembly import interference
from cadcore.model.document import Document
from cadcore.errors import CadError
from cadcore.evaluation.graph import Evaluator
from cadcore.service.server import Session

BORE = "base:bore_tool/side"
BARREL = "bush:barrel/side"
FLANGE = "bush:flange/+z"
WALL = "base:wall/-y@1"


@pytest.fixture(scope="module")
def parts():
    """The two parts of the shipped assembly, each where it was modelled."""
    ev = Evaluator(Document.load("examples/assembly.json"))
    ev.build()
    return {"base": ev.body_of("base"), "bush": ev.body_of("bush")}


def solve(parts, mates, **kw):
    return placement.solve(parts, mates, ground="base", **kw)


def concentric(**extra) -> dict:
    return dict({"kind": "concentric", "faces": [BARREL, BORE], "flip": False}, **extra)


def seated(**extra) -> dict:
    return dict({"kind": "planar", "faces": [FLANGE, WALL]}, **extra)


# --- what a mate takes away -----------------------------------------------------

def test_a_shaft_in_a_bore_can_spin_and_slide_and_nothing_else(parts):
    """A concentric mate: four equations, two degrees of freedom, reported as
    motions (slide along the axis, turn about it through a point)."""
    out = solve(parts, [concentric()])

    assert out["equations"] == 4 and out["constrained"] == 4
    assert out["dof"] == 2
    free = out["freedom"]["bush"]
    assert free["dof"] == 2
    assert free["slides_along"] == [[0.0, 1.0, 0.0]]            # along the bore
    turn = free["turns_about"][0]
    assert turn["direction"] == [0.0, 1.0, 0.0]
    assert turn["through"][2] == pytest.approx(34.1, abs=1e-3)  # on the bore's axis


def test_an_offset_is_a_constraint_only_when_it_is_written(parts):
    """An omitted offset leaves the part free to slide along the axis; only a
    written offset pins it."""
    assert solve(parts, [concentric()])["dof"] == 2
    assert solve(parts, [concentric(offset=10.0)])["dof"] == 1


def test_two_mates_hold_one_part(parts):
    """Two mates on one part are solved together: the concentric holds the
    axis, the planar holds the seat, and one rotation remains."""
    out = solve(parts, [concentric(), seated()])

    assert out["solved"] and not out["conflicts"]
    assert out["dof"] == 1
    assert out["freedom"]["bush"]["turns_about"][0]["direction"] == [0.0, 1.0, 0.0]
    assert out["freedom"]["bush"]["slides_along"] == []


def test_the_seat_from_a_face_is_where_the_measured_offset_put_it(parts):
    """The flange-against-wall seat gives the same pose as the measured
    `seat: 10` offset in `assembly.json`."""
    measured = solve(parts, [concentric(offset=10.0)])["poses"]["bush"][1]
    from_the_face = solve(parts, [concentric(), seated()])["poses"]["bush"][1]

    assert np.allclose(measured, from_the_face, atol=1e-6), (measured, from_the_face)


def test_a_solved_assembly_does_not_put_the_parts_through_each_other(parts):
    """The solved pose leaves the parts clear of each other."""
    out = solve(parts, [concentric(), seated()])
    placed = placement.moved(parts["bush"], out["poses"]["bush"])

    assert interference({"base": parts["base"], "bush": placed}) == []


# --- what it refuses, and what it only reports ----------------------------------

def test_mates_that_disagree_are_refused(parts):
    """Guards: two different offsets along one axis are refused as conflicts,
    not returned as a least-squares compromise."""
    out = solve(parts, [concentric(offset=10.0), concentric(offset=25.0)])

    assert not out["solved"]
    assert len(out["conflicts"]) == 2
    assert all(c["error"] > 1.0 for c in out["conflicts"])


def test_saying_the_same_thing_twice_is_reported_and_not_refused(parts):
    """An agreeing redundant mate is reported, not refused; two mates saying
    one thing share the redundancy evenly."""
    out = solve(parts, [concentric(offset=10.0), concentric(offset=10.0)])

    assert out["solved"] and not out["conflicts"]
    assert out["equations"] == 10 and out["constrained"] == 5
    assert {m["mate"] for m in out["redundant"]} == {0, 1}
    assert all(m["share"] == pytest.approx(1 / math.sqrt(2), abs=1e-3)
               for m in out["redundant"])


def test_an_assembly_needs_something_to_stand_on(parts):
    with pytest.raises(CadError) as exc:
        placement.solve(parts, [concentric()], ground="nothing")
    assert exc.value.kind == "unknown_feature"

    with pytest.raises(CadError) as exc:
        placement.solve({}, [])
    assert exc.value.kind == "not_an_assembly"


def test_a_mate_needs_two_faces(parts):
    with pytest.raises(CadError) as exc:
        solve(parts, [{"kind": "concentric", "faces": [BARREL]}])
    assert exc.value.kind == "bad_arguments"


def test_a_face_no_part_has_is_named_as_such(parts):
    with pytest.raises(CadError) as exc:
        solve(parts, [{"kind": "concentric", "faces": [BARREL, "base:no/such"]}])
    assert exc.value.kind == "unresolved_reference"


def test_a_chain_of_parts_is_placed_all_the_way_along():
    """Each mate seeds whichever end is not yet placed. The seed decides which
    side of a plane a part lands on, so a bad seed gives a wrong answer."""
    from ..solids import primitives
    from ..assembly.assembly import prefixed

    parts = {name: prefixed(primitives.box("plate", [size, size, 10]), name)
             for name, size in (("a", 40), ("b", 20), ("c", 10))}
    out = placement.solve(parts, [
        {"kind": "fastened", "faces": ["b:plate/-z", "a:plate/+z"]},
        {"kind": "fastened", "faces": ["c:plate/-z", "b:plate/+z"]}], ground="a")

    assert out["dof"] == 0 and out["constrained"] == 12
    assert out["poses"]["b"][1][2] == pytest.approx(10.0, abs=1e-6)
    assert out["poses"]["c"][1][2] == pytest.approx(20.0, abs=1e-6)


def test_a_loop_of_mates_says_how_far_off_it_is(parts):
    """A closed loop of mates is solved as far as it goes and the error is
    reported per mate; the 10 mm shortfall is split three ways."""
    from ..solids import primitives
    from ..assembly.assembly import prefixed

    stack = {name: prefixed(primitives.box("plate", [20, 20, 10]), name)
             for name in ("a", "b", "c")}
    out = placement.solve(stack, [
        {"kind": "fastened", "faces": ["b:plate/-z", "a:plate/+z"]},
        {"kind": "fastened", "faces": ["c:plate/-z", "b:plate/+z"]},
        {"kind": "fastened", "faces": ["c:plate/-z", "a:plate/+z"]}], ground="a")

    assert not out["solved"]
    assert {c["mate"] for c in out["conflicts"]} == {0, 1, 2}
    assert all(c["error"] == pytest.approx(10 / 3, abs=0.1)
               for c in out["conflicts"])


def test_a_part_nobody_mated_is_six_degrees_free():
    """An unmated part reports six degrees of freedom, per part rather than
    as one total."""
    from ..solids import primitives
    from ..assembly.assembly import prefixed

    parts = {name: prefixed(primitives.box("plate", [20, 20, 10]), name)
             for name in ("a", "b", "c")}
    out = placement.solve(parts, [
        {"kind": "fastened", "faces": ["b:plate/-z", "a:plate/+z"]}], ground="a")

    assert out["dof"] == 6
    assert out["freedom"]["b"]["dof"] == 0
    loose = out["freedom"]["c"]
    assert loose["dof"] == 6
    # freedom is reported as the three axis turns and three axis slides, not
    # as the raw null-space basis
    assert [t["direction"] for t in loose["turns_about"]] == \
        loose["slides_along"] == [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]


# --- through the document -------------------------------------------------------

def test_the_solved_example_builds_and_reports_its_freedom(open_example):
    session = open_example("assembly_solved.json")
    report = session.op_build()["freedom"]

    assert report["ground"] == "base"
    assert report["dof"] == 1
    assert report["freedom"]["bush"]["dof"] == 1
    assert session.op_interference()["clear"]


def test_the_mates_survive_the_parts_changing_underneath_them(open_example):
    """Mates are written against faces, so the bush still seats after the
    bore and the bush grow."""
    session = open_example("assembly_solved.json")
    before = session.op_build()["freedom"]["freedom"]["bush"]

    session.op_set_parameter("bore", 15.0)
    after = session.op_build()["freedom"]

    assert after["solved"] and after["dof"] == 1
    assert after["freedom"]["bush"]["turns_about"][0]["direction"] == \
        before["turns_about"][0]["direction"]
    assert session.op_interference()["clear"]


def test_the_solved_example_is_the_ordered_one_in_a_different_hand(open_example):
    """The same two parts in the same place, with one fewer number said."""
    ordered = open_example("assembly.json").op_build()
    solved = open_example("assembly_solved.json").op_build()

    assert solved["volume_mm3"] == pytest.approx(ordered["volume_mm3"], abs=1e-3)


def test_an_over_constrained_assembly_refuses_to_build(tmp_path):
    """The refusal names the conflicting mates."""
    shutil.copy("examples/bracket.json", tmp_path / "bracket.json")
    shutil.copytree("examples/parts", tmp_path / "parts")
    document = json.loads(open("examples/assembly_solved.json", encoding="utf-8").read())
    document["features"][-1]["mates"] = [
        {"kind": "concentric", "faces": [BARREL, BORE], "flip": False, "offset": 10},
        {"kind": "concentric", "faces": [BARREL, BORE], "flip": False, "offset": 25}]
    path = tmp_path / "clash.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    session = Session()
    session.op_open(str(path))
    with pytest.raises(CadError) as exc:
        session.op_build()
    assert exc.value.kind == "over_constrained"
    assert len(exc.value.detail["conflicts"]) == 2
