"""STEP out and in as an assembly: one product per part, nested as the document nests."""
import re

import pytest

from cadcore.geometry.core.measure import volume
from cadcore.geometry.io.exchange import assembly_tree, import_step
from cadcore.service.server import Session


def products(path) -> list:
    return re.findall(r"PRODUCT\('([^']*)'", open(path, encoding="utf-8").read())


def built(document: str) -> Session:
    session = Session(autosave=False)
    session.op_open(document)
    session.op_build()
    return session


def test_an_assembly_goes_out_as_named_products(tmp_path):
    session = built("examples/linkage.json")
    out = session.op_export_step(str(tmp_path / "linkage.step"))
    assert out["parts"] == ["frame", "crank", "coupler", "rocker"]
    assert products(out["path"]) == ["four-bar linkage", "frame", "crank", "coupler", "rocker"]


def test_a_sub_assembly_nests_in_the_file(tmp_path):
    session = built("examples/assembly_nested.json")
    out = session.op_export_step(str(tmp_path / "nested.step"))
    assert out["parts"] == ["rail", "left:base", "left:bush", "right:base", "right:bush"]
    assert products(out["path"]) == ["two bushed brackets on a rail", "rail",
                                     "left", "base", "bush", "right", "base", "bush"]


def test_what_goes_out_comes_back_with_its_parts_named(tmp_path):
    session = built("examples/assembly_nested.json")
    path = str(tmp_path / "nested.step")
    session.op_export_step(path)
    body = import_step("imp", path)
    scopes = sorted({name.rsplit(":", 1)[0] for name in body.face_names()})
    assert scopes == ["left:base", "left:bush", "rail", "right:base", "right:bush"]
    assert volume(body) == pytest.approx(session.op_build()["volume_mm3"], rel=1e-6)
    tree = assembly_tree(body)
    assert set(tree) == {"rail", "left", "right"} and set(tree["left"]) == {"base", "bush"}


def test_one_part_is_not_an_assembly(tmp_path):
    session = built("examples/bracket.json")
    out = session.op_export_step(str(tmp_path / "bracket.step"))
    assert "parts" not in out
    assert assembly_tree(session.body) is None
    assert import_step("imp", out["path"]).face_names()[0].startswith("imp/")
