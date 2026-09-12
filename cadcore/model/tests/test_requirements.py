"""Requirements: what the part must be, checked on every build.

`asserts` guard the parameters and a study's `require` guards a solve. The
gap between them was the thing a designer says first -- under 150 grams, fits
the box, prints, one solid -- which lived in a head or a transcript, where an
edit three steps later broke it without anybody being told. A requirement is
a row the kernel measures after every rebuild, reported and never enforced:
a part half-made is allowed to be too heavy.
"""
from __future__ import annotations

import os

import pytest

from cadcore.model import requirements
from cadcore.errors import CadError
from cadcore.ops.session import Session

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BRACKET = os.path.join(HERE, "examples", "bracket.json")


@pytest.fixture
def bracket():
    session = Session(autosave=False)
    session.op_open(path=BRACKET)
    session.op_build()
    return session


def test_a_requirement_is_answered_on_the_build_that_added_it(bracket):
    out = bracket.op_add_requirement(quantity="mass_g", compare="<=", value=150, name="light")
    row = out["requirements"][0]
    assert row["id"] == "light" and row["quantity"] == "mass_g"
    assert row["got"] > 150 and row["ok"] is False        # the bracket is 187 g
    assert row["unit"] == "g"


def test_and_on_every_build_after_it(bracket):
    bracket.op_add_requirement(quantity="mass_g", compare="<=", value=150, name="light")
    thinner = bracket.op_set_parameter(name="thickness", value=4)
    assert thinner["requirements"][0]["ok"] is True
    thicker = bracket.op_set_parameter(name="thickness", value=10)
    assert thicker["requirements"][0]["ok"] is False


def test_a_value_may_be_an_expression_over_the_parameters(bracket):
    out = bracket.op_add_requirement(quantity="bbox_max", compare="<=", value="width + 5")
    row = out["requirements"][0]
    assert row["value"] == pytest.approx(85.0) and row["got"] == pytest.approx(80.0)
    assert row["ok"]
    wider = bracket.op_set_parameter(name="width", value=100)
    assert wider["requirements"][0]["value"] == pytest.approx(105.0)
    assert wider["requirements"][0]["ok"]              # the bound moved with it


def test_it_is_status_and_not_a_gate(bracket):
    """The edit that breaks a requirement goes through; the row says so."""
    bracket.op_add_requirement(quantity="bbox_max", compare="<=", value=80, name="fits")
    out = bracket.op_set_parameter(name="width", value=120)
    assert out["requirements"][0]["ok"] is False
    assert bracket.doc.parameters["width"] == 120


def test_the_quantities_come_from_the_table_that_refuses_one(bracket):
    kinds = bracket.op_requirement_kinds()
    assert set(kinds["quantities"]) == set(requirements.QUANTITIES)
    assert set(kinds["compare"]) == set(requirements.OPS)
    with pytest.raises(CadError) as raised:
        bracket.op_add_requirement(quantity="weight", compare="<=", value=1)
    assert raised.value.kind == "unknown_quantity"
    assert "mass_g" in raised.value.detail["available"]
    with pytest.raises(CadError) as raised:
        bracket.op_add_requirement(quantity="mass_g", compare="~", value=1)
    assert raised.value.kind == "bad_requirement"
    with pytest.raises(CadError) as raised:
        bracket.op_add_requirement(quantity="mass_g", compare="<=", value=1, min_wall=2)
    assert raised.value.kind == "unknown_argument"     # min_wall is printable's


def test_a_refused_requirement_leaves_the_document_as_it_was(bracket):
    before = bracket.op_document_json()["document"]
    with pytest.raises(CadError):
        bracket.op_add_requirement(quantity="weight", compare="<=", value=1)
    assert bracket.op_document_json()["document"] == before


def test_removing_and_undoing(bracket):
    bracket.op_add_requirement(quantity="solid", compare="==", value=1, name="one")
    bracket.op_add_requirement(quantity="faces", compare=">=", value=5, name="faces")
    out = bracket.op_remove_requirement(name="one")
    assert [r["id"] for r in out["requirements"]] == ["faces"]
    with pytest.raises(CadError) as raised:
        bracket.op_remove_requirement(name="one")
    assert raised.value.kind == "unknown_requirement"
    back = bracket.op_undo()
    assert [r["id"] for r in back["requirements"]] == ["one", "faces"]


def test_a_duplicate_id_is_refused(bracket):
    bracket.op_add_requirement(quantity="solid", compare="==", value=1, name="one")
    with pytest.raises(CadError) as raised:
        bracket.op_add_requirement(quantity="faces", compare=">=", value=1, name="one")
    assert raised.value.kind == "duplicate_id"


def test_requirements_survive_a_save_and_a_load(bracket, tmp_path):
    bracket.op_add_requirement(quantity="mass_g", compare="<=", value=150, name="light")
    path = str(tmp_path / "with_req.json")
    bracket.op_save(path=path)
    again = Session(autosave=False)
    again.op_open(path=path)
    out = again.op_build()
    assert out["requirements"][0]["id"] == "light"
    assert again.doc.requirements == bracket.doc.requirements


def test_a_row_that_cannot_be_measured_does_not_take_the_others_down(bracket):
    """One dead gauge is not a reason to switch the dashboard off."""
    bracket.op_add_requirement(quantity="mass_g", compare="<=", value=150, name="a")
    bracket.op_add_requirement(quantity="mass_g", compare="<=", value=150, name="b",
                               material="A6061")
    # break one after the fact, the way a hand-edited file could
    bracket.doc.requirements[1]["material"] = "unobtainium"
    out = bracket.op_build()
    rows = {r["id"]: r for r in out["requirements"]}
    assert rows["a"]["ok"] is False and rows["a"]["got"] > 0
    assert rows["b"]["ok"] is False and "unknown_material" in rows["b"]["error"]


def test_the_studies_rows_go_stale_when_the_document_changes(bracket):
    """A safety factor computed for a different part is not a fact about this one."""
    assert bracket.op_requirements()["studies"] == []
    pytest.importorskip("ngsolve", reason="the studies need requirements-sim.txt")
    bracket.op_simulate()
    rows = bracket.op_requirements()["studies"]
    assert rows and all(r["stale"] is False for r in rows)
    assert {"study", "quantity", "op", "value", "got", "ok"} <= set(rows[0])
    bracket.op_set_parameter(name="thickness", value=9)
    assert all(r["stale"] is True for r in bracket.op_requirements()["studies"])


def test_the_document_schema_knows_the_key():
    from cadcore.service.resources import document_schema

    props = document_schema()["properties"]["requirements"]["items"]["properties"]
    assert set(props["quantity"]["enum"]) == set(requirements.QUANTITIES)
