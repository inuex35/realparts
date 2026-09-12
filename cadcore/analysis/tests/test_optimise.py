"""The search obeys the document: it stays inside the declared envelope,
refuses when nothing was declared, and its result is an undoable edit.
"""
from __future__ import annotations

import pytest

from cadcore.errors import CadError
from cadcore.service.server import Session


@pytest.fixture()
def session():
    s = Session(autosave=False)
    s.op_open("examples/bracket.json")
    return s


def test_it_finds_something_smaller(session):
    before = session.op_build()["volume_mm3"]
    found = session.op_optimize(trials=20, objective="volume", seed=3,
                                params=["thickness", "wall"])
    assert found["built"]["volume_mm3"] < before
    assert found["feasible"] > 0


def test_and_it_is_an_edit_like_any_other(session):
    """Guards: the result of a search can be undone."""
    before = session.op_build()["volume_mm3"]
    session.op_optimize(trials=12, objective="volume", seed=1,
                        params=["thickness"])
    assert session.op_build()["volume_mm3"] != pytest.approx(before)
    assert session.op_undo()["volume_mm3"] == pytest.approx(before)


def test_it_can_look_without_touching(session):
    before = session.op_build()["volume_mm3"]
    session.op_optimize(trials=10, objective="volume", seed=2, apply=False,
                        params=["thickness"])
    assert session.op_build()["volume_mm3"] == pytest.approx(before)


def test_every_design_it_keeps_is_inside_the_envelope(session):
    found = session.op_optimize(trials=25, objective="volume", seed=5,
                                apply=False)
    doc = session.doc
    for line in found["history"]:
        for name, value in line["parameters"].items():
            low, high = doc.bounds[name]
            assert low <= value <= high, (name, value, low, high)


def test_it_moves_only_what_it_was_asked_to(session):
    was = dict(session.doc.parameters)
    found = session.op_optimize(trials=10, objective="volume", seed=4,
                                params=["thickness"])
    assert found["moved"] == ["thickness"]
    for name, value in was.items():
        if name != "thickness":
            assert session.doc.parameters[name] == value, name


def test_a_parameter_with_no_range_is_not_a_parameter_it_can_move(session):
    with pytest.raises(CadError) as raised:
        session.op_optimize(params=["not_a_parameter"], trials=4)
    assert raised.value.kind == "nothing_to_optimise"
    assert "declared" in raised.value.detail


def test_an_objective_it_does_not_have_is_refused(session):
    with pytest.raises(CadError) as raised:
        session.op_optimize(objective="cheapest", trials=2)
    assert raised.value.kind == "bad_parameter"


def test_a_search_with_no_study_that_weighs_the_part_says_so():
    """Guards: a document whose first study reports no mass raises a CadError,
    not an AttributeError."""
    import pytest

    from cadcore.model.document import Document
    from cadcore.errors import CadError
    from cadcore.analysis.optimise import search

    pytest.importorskip("ngsolve")
    doc = Document.from_dict({
        "parameters": {"t": 8.0},
        "parameters_bounds": {"t": [4.0, 12.0]},
        "result": "bar",
        "features": [{"id": "bar", "type": "box", "size": [200, 20, "t"],
                      "at": [0, 0, 0], "centred": False}],
        "studies": [{"id": "shake", "type": "modal", "mesh_size": 20.0,
                     "modes": 2, "fix": ["bar/-x"]}]})
    with pytest.raises(CadError) as caught:
        search(doc, trials=1)
    assert caught.value.kind == "nothing_to_optimise"


def test_a_sample_it_calls_inside_really_is_inside():
    """`place_inside` bisects toward the base design, and the last blend it
    tried is the one left in the trial's parameters -- which is the blend that
    failed as often as not. The fuzz was then reporting names lost by designs
    the document had ruled out.
    """
    import random

    from cadcore.analysis.optimise import place_inside
    from cadcore.model.document import Document

    doc = Document.load("examples/cast_cover.json")
    numeric = {k: v for k, v in doc.parameters.items() if isinstance(v, (int, float))}
    rng = random.Random(0)
    checked = 0
    for _ in range(40):
        trial = Document.load("examples/cast_cover.json")
        draw = {k: rng.uniform(*trial.bounds[k]) for k in numeric if k in trial.bounds}
        inside, _ = place_inside(trial, numeric, draw)
        if not inside:
            continue
        checked += 1
        held, why = trial.in_envelope()
        assert held, (why, {k: trial.parameters[k] for k in draw})
    assert checked > 20, checked
