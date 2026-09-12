"""Assemblies: parts mated by face name, and the interference that follows."""
import json

import pytest

from cadcore import Document, describe
from cadcore.evaluation.graph import Evaluator
from cadcore.geometry.kernel import CadError
from cadcore.service.server import Session

DOC = "examples/assembly.json"


def variant(tmp_path, **parameters):
    raw = json.loads(open(DOC, encoding="utf-8").read())
    raw["parameters"].update(parameters)
    path = tmp_path / "variant.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    # sub-documents resolve relative to the document, so the variant stays beside them
    return str(path)


def test_parts_keep_their_own_names_scoped(tmp_path):
    body, _ = (lambda ev: (ev.build(), ev))(Evaluator(Document.load(DOC)))
    names = describe(body)["face_names"]
    assert "base:plate/+z" in names and "bush:flange/side" in names
    # the parts stay separate solids: an assembly is not a boolean
    assert describe(body)["volume_mm3"] > 0


def test_a_mate_places_a_part_by_face_name():
    session = Session()
    session.op_open(DOC)
    session.op_build()
    out = session.op_interference()
    assert out["parts"] == ["base", "fitted"]
    assert out["clear"] is True


def test_interference_is_measured_not_guessed(tmp_path):
    """Guards: a bush wider than its bore reports an interference with a volume."""
    import shutil, os
    for name in ("bracket.json", "assembly.json"):
        shutil.copy(f"examples/{name}", tmp_path / name)
    os.makedirs(tmp_path / "parts", exist_ok=True)
    shutil.copy("examples/parts/bush.json", tmp_path / "parts" / "bush.json")

    path = variant(tmp_path, clearance=-1.5)
    session = Session()
    session.op_open(path)
    session.op_build()
    out = session.op_interference()
    assert out["clear"] is False
    clash = out["interferences"][0]
    assert clash["parts"] == ["base", "fitted"]
    assert clash["volume_mm3"] > 100


def test_a_part_driven_outside_its_own_envelope_is_refused(tmp_path):
    import shutil, os
    for name in ("bracket.json", "assembly.json"):
        shutil.copy(f"examples/{name}", tmp_path / name)
    os.makedirs(tmp_path / "parts", exist_ok=True)
    shutil.copy("examples/parts/bush.json", tmp_path / "parts" / "bush.json")

    path = variant(tmp_path, clearance=5.0)         # makes the wall too thin
    with pytest.raises(CadError) as exc:
        Evaluator(Document.load(path)).build()
    assert exc.value.kind == "outside_envelope"
    assert "outer_d" in exc.value.detail["reason"]


def test_a_mate_needs_the_right_kind_of_face():
    doc = Document.load(DOC)
    for f in doc.features:
        if f.type == "mate":
            f.args["faces"] = ["bush:flange/+z", "base:bore_tool/side"]
    with pytest.raises(CadError) as exc:
        Evaluator(doc).build()
    assert exc.value.kind == "not_a_cylinder"


def test_the_bill_of_materials_counts_what_a_part_is(tmp_path):
    """BOM quantity counts identical parts: two differently bored bushes are
    two lines, four of the same bush are one line."""
    session = Session()
    session.op_open(DOC)
    session.op_build()
    bom = session.op_bill_of_materials()

    parts = {line["part"]: line for line in bom["parts"]}
    assert set(parts) == {"base", "bush"}
    assert parts["bush"]["quantity"] == 1
    assert parts["bush"]["placed_as"] == ["fitted"]      # counted where it ended up
    assert parts["base"]["volume_mm3"] > parts["bush"]["volume_mm3"]


def test_a_part_says_what_it_is_made_of_or_its_document_does():
    """Material precedence: the part, then its document's meta, then the
    analysis material."""
    session = Session()
    session.op_open(DOC)
    session.op_build()
    parts = {line["part"]: line for line in session.op_bill_of_materials()["parts"]}

    assert parts["bush"]["material"] == "SUS304"        # the assembly says so
    assert parts["base"]["material"] == "A6061"         # bracket.json's own meta
    assert parts["base"]["mass_g"] == pytest.approx(
        parts["base"]["volume_mm3"] * 2.70e-9 * 1e6, abs=1e-3)


def test_the_bill_nests_where_the_assembly_does():
    """A part that is an assembly is one line with its own parts under it, and
    `flat` multiplies the quantities through."""
    session = Session()
    session.op_open("examples/assembly_nested.json")
    session.op_build()
    bom = session.op_bill_of_materials()
    lines = {line["part"]: line for line in bom["parts"]}
    assert set(lines) == {"rail", "left", "right"}          # the two bores differ
    assert lines["left"]["kind"] == "assembly"
    assert [(p["part"], p["quantity"]) for p in lines["left"]["parts"]] == [("base", 1), ("bush", 1)]
    assert lines["left"]["mass_g"] == pytest.approx(
        sum(p["mass_g"] for p in lines["left"]["parts"]), abs=1e-3)
    assert [(f["part"], f["quantity"]) for f in bom["flat"]] == [
        ("left:base", 1), ("left:bush", 1), ("rail", 1), ("right:base", 1), ("right:bush", 1)]


def test_a_pattern_of_a_part_counts_its_copies(tmp_path):
    import os, shutil
    os.makedirs(tmp_path / "parts", exist_ok=True)
    shutil.copy("examples/parts/bush.json", tmp_path / "parts" / "bush.json")
    raw = {"features": [
        {"id": "bush", "type": "part", "document": "parts/bush.json", "material": "SUS304"},
        {"id": "row", "type": "pattern", "body": "bush", "count": 4, "spacing": 50,
         "direction": [1, 0, 0]},
        {"id": "asm", "type": "assemble", "bodies": ["row"]}],
        "result": "asm"}
    path = tmp_path / "row.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    session = Session()
    session.op_open(str(path))
    session.op_build()
    bom = session.op_bill_of_materials()
    assert [(line["part"], line["quantity"]) for line in bom["parts"]] == [("bush", 4)]
    one = bom["parts"][0]
    assert one["volume_mm3"] * 4 == pytest.approx(session.op_build()["volume_mm3"], rel=1e-6)
    assert bom["flat"][0]["quantity"] == 4
