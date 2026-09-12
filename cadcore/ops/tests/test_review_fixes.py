"""Fixes from the second external review: schema, load_json, expressions, restore."""
import json
import math
from copy import deepcopy
from pathlib import Path

import pytest

from cadcore.errors import CadError
from cadcore.model.document import Document
from cadcore.ops.session import Session
from cadcore.service import resources, server


def _required_are_properties(schema, where="schema"):
    if isinstance(schema, dict):
        if "required" in schema and "properties" in schema:
            missing = set(schema["required"]) - set(schema["properties"])
            assert not missing, f"{where}: required names not in properties: {missing}"
        for k, v in schema.items():
            _required_are_properties(v, f"{where}.{k}")
    elif isinstance(schema, list):
        for i, v in enumerate(schema):
            _required_are_properties(v, f"{where}[{i}]")


def test_every_required_name_in_the_document_schema_is_a_property():
    _required_are_properties(resources.document_schema())


def test_load_json_keeps_the_old_document_when_the_new_one_does_not_build(tmp_path):
    s = Session(autosave=False)
    s.op_new_document()
    s.op_add_feature("box", {"size": [10, 10, 10]}, feature_id="box1")
    s.op_add_feature("fillet", {"body": "box1", "edges": ["box1/+x|box1/+z"], "radius": 1}, feature_id="f1")
    before = s.op_document_json()["document"]
    bad = deepcopy(before)
    bad["features"][1]["radius"] = 500              # a fillet the box cannot take
    with pytest.raises(CadError):
        s.op_load_json(bad)
    assert s.op_document_json()["document"] == before
    assert s.body is not None


def test_the_line_server_spells_what_json_cannot():
    text = json.dumps({"a": Path("/x"), "b": {1, 2}, "c": (3, 4)}, default=server._spelled)
    assert json.loads(text) == {"a": "/x", "b": [1, 2], "c": [3, 4]}


def test_joining_two_lists_respects_the_size_limit():
    doc = Document.from_dict({"parameters": {"p0": "[1, 2] * 400"}, "result": "x", "features": []})
    for k in range(1, 12):
        doc.parameters[f"p{k}"] = f"p{k - 1} + p{k - 1}"
    with pytest.raises(CadError) as caught:
        doc.evaluate("p11")
    assert caught.value.kind == "expression_too_big"


def test_loading_an_inch_document_leaves_the_caller_dictionary_alone():
    raw = {"unit": "in", "parameters": {"w": 1.0}, "parameters_bounds": {"w": [0.5, 2.0]},
           "features": [{"id": "box1", "type": "box", "size": [1, 1, 1]}], "result": "box1"}
    kept = deepcopy(raw)
    Document.from_dict(raw)
    Document.from_dict(raw)
    assert raw == kept


def test_in_envelope_reads_an_expression_parameter():
    doc = Document(parameters={"a": 4, "b": "a * 2"}, bounds={"b": [0, 10]})
    ok, why = doc.in_envelope()
    assert ok, why
    doc.parameters["a"] = 8
    ok, why = doc.in_envelope()
    assert not ok and "b=" in why


def test_restore_brings_the_requirements_back(tmp_path):
    s = Session(autosave=False)
    s.op_new_document()
    s.op_add_feature("box", {"size": [10, 10, 10]}, feature_id="box1")
    s.op_add_requirement("mass_g", "<=", 100, name="light", material="steel")
    s.op_checkpoint("with")
    s.op_remove_requirement("light")
    assert not s.doc.requirements
    s.op_restore("with")
    assert [r["id"] for r in s.doc.requirements] == ["light"]


def test_a_refused_edit_leaves_nothing_built_on_the_edited_document():
    """The evaluator kept after a refusal was built on the object the edit
    changed; a read that used it answered for a document nobody had."""
    s = Session(autosave=False)
    s.op_new_document()
    s.op_add_feature("box", {"size": [10, 10, 10]}, feature_id="box1")
    with pytest.raises(CadError):
        s.op_add_feature("fillet", {"body": "box1", "edges": ["box1/+x|box1/+z"], "radius": 500},
                         feature_id="f1")
    assert [f.id for f in s.doc.features][:1] == ["box1"] and "f1" not in {f.id for f in s.doc.features}
    assert s.op_build()["faces"] == 6
    assert s.evaluator.doc is s.doc


def test_two_features_with_one_id_are_refused_by_kind():
    s = Session(autosave=False)
    s.op_new_document()
    s.op_add_feature("box", {"size": [10, 10, 10]}, feature_id="box1")
    with pytest.raises(CadError) as raised:
        s.op_add_fillet(["box1/+x|box1/+z"], 1.0, feature_id="box1")
    assert raised.value.kind == "duplicate_id"
    with pytest.raises(CadError) as raised:
        s.op_add_feature("box", {"size": [5, 5, 5]}, feature_id="box1")
    assert raised.value.kind == "duplicate_id"
    twice = {"result": "b", "features": [{"id": "b", "type": "box", "size": [1, 1, 1]},
                                         {"id": "b", "type": "box", "size": [2, 2, 2]}]}
    with pytest.raises(CadError) as raised:
        Document.from_dict(twice)
    assert raised.value.kind == "duplicate_id"
    with pytest.raises(CadError) as raised:
        s.op_load_json(twice)
    assert raised.value.kind == "duplicate_id"
    assert [f.id for f in s.doc.features] == ["box1"]


def test_studies_without_the_solver_are_refused_by_kind(monkeypatch):
    import sys

    from cadcore.model.document import Document

    monkeypatch.setitem(sys.modules, "ngsolve", None)      # `import ngsolve` now raises ImportError
    s = Session(autosave=False)
    s.op_load_json({"result": "bar", "parameters": {},
                    "features": [{"id": "bar", "type": "box", "size": [100, 20, 8]}],
                    "studies": [{"id": "bend", "type": "static_structural", "mesh_size": 10.0,
                                 "fix": ["bar/-x"], "loads": [{"faces": ["bar/+z"], "force": [0, 0, -100]}]}]})
    with pytest.raises(CadError) as raised:
        s.op_simulate()
    assert raised.value.kind == "missing_dependency"
    from cadcore.analysis.optimise import search
    doc = Document.from_dict(dict(s.op_document_json()["document"], parameters_bounds={}))
    with pytest.raises(CadError) as raised:
        search(doc, trials=1)
    assert raised.value.kind in ("missing_dependency", "nothing_to_optimise")


def test_a_unit_that_is_not_one_is_refused_before_anything_is_built():
    s = Session(autosave=False)
    with pytest.raises(CadError) as raised:
        s.op_new_document(unit="furlong")
    assert raised.value.kind == "unknown_unit"


def test_saving_elsewhere_moves_where_the_document_s_files_resolve_from(tmp_path):
    s = Session(autosave=False)
    s.op_new_document()
    s.op_add_feature("box", {"size": [1, 1, 1]}, feature_id="b")
    s.op_save(str(tmp_path / "a" / "part.json")) if (tmp_path / "a").mkdir() or True else None
    (tmp_path / "b").mkdir()
    s.op_save(str(tmp_path / "b" / "part.json"))
    assert s.doc.source == str(tmp_path / "b" / "part.json")
    assert s.doc.resolve("pin.json") == str(tmp_path / "b" / "pin.json")


def test_the_autosave_is_whole_or_not_at_all(tmp_path):
    from cadcore.model.document import autosave_path

    path = tmp_path / "part.json"
    s = Session()
    s.op_new_document()
    s.op_save(str(path))
    s.op_add_feature("box", {"size": [1, 1, 1]}, feature_id="b")
    sidecar = Path(autosave_path(str(path)))
    assert sidecar.exists() and not sidecar.with_name(sidecar.name + ".saving").exists()
    assert json.loads(sidecar.read_text(encoding="utf-8"))["features"][0]["id"] == "b"


def test_a_fenced_session_may_still_read_the_shipped_examples(tmp_path):
    from cadcore.service.web import EXAMPLES

    s = Session(autosave=False, root=str(tmp_path), readable=(str(EXAMPLES),))
    out = s.op_open(str(EXAMPLES / "bracket.json"), as_copy=True)
    assert out["features"] and s.path is None
    with pytest.raises(CadError) as raised:
        s.op_save(str(EXAMPLES / "bracket.json"))
    assert raised.value.kind == "bad_path"


def test_an_argument_of_the_wrong_shape_is_a_refusal_not_a_traceback():
    s = Session(autosave=False)
    s.op_new_document()
    reply = server.handle(s, {"op": "add_feature", "type": "box", "args": {"size": "big"}})
    assert reply["ok"] is False and reply["kind"] in ("bad_arguments", "bad_parameter")
    reply = server.handle(s, {"op": "add_plane", "face": None, "normal": "up"})
    assert reply["ok"] is False and reply["kind"] != "internal_error"
