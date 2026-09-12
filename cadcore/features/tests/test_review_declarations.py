"""What the declarations say is what the evaluator does: an until that cannot be
met is refused, a rib measures along its own direction, a hinge's limit is degrees
in any unit, a null is an argument left out, and a part cannot contain itself."""
import json

import pytest

from cadcore.errors import CadError
from cadcore.evaluation.graph import Evaluator
from cadcore.model.document import Document
from cadcore.service.server import Session

RECT = {"points": {"a": [-5, -5], "b": [5, -5], "c": [5, 5], "d": [-5, 5]},
        "lines": {"s": ["a", "b"], "e": ["b", "c"], "n": ["c", "d"], "w": ["d", "a"]},
        "constraints": [{"type": "fix", "point": p, "at": at}
                        for p, at in {"a": [-5, -5], "b": [5, -5], "c": [5, 5], "d": [-5, 5]}.items()]}


def block() -> Session:
    s = Session(autosave=False)
    s.op_new_document()
    s.op_add_feature("box", {"size": [40, 40, 10]}, feature_id="blk")
    s.op_add_feature("sketch", dict(RECT, on={"body": "blk", "face": "blk/+z"}), feature_id="sq")
    return s


def test_an_until_face_that_is_not_square_to_the_sweep_is_refused():
    s = block()
    s.op_add_feature("plane", {"origin": [0, 0, 30], "normal": [0, 0.6, 0.8]}, feature_id="tilt")
    with pytest.raises(CadError) as caught:
        s.op_add_feature("extrude", {"sketch": "sq", "until": {"plane": "tilt"}}, feature_id="post")
    assert caught.value.kind == "nothing_to_stop_at"
    s.op_add_feature("plane", {"origin": [0, 0, 30], "normal": [0, 0, 1]}, feature_id="lid")
    out = s.op_add_feature("extrude", {"sketch": "sq", "until": {"plane": "lid"}}, feature_id="post")
    assert out["volume_mm3"] == pytest.approx(10 * 10 * 25)


def test_a_rib_measures_its_until_along_its_own_direction():
    doc = Document(parameters={}, features=[
        __import__("cadcore.model.document", fromlist=["Feature"]).Feature("base", "box", {"size": [60, 40, 8], "at": [0, 0, 0], "centred": False}),
        __import__("cadcore.model.document", fromlist=["Feature"]).Feature("line", "sketch", {
            "open": True,
            "plane": {"origin": [0, 20, 0], "normal": [0, 1, 0], "x_axis": [1, 0, 0]},
            "points": {"a": [8, -30], "b": [40, -30]},      # v runs down -z here: z = 30
            "lines": {"run": ["a", "b"]},
            "constraints": [{"type": "fix", "point": "a", "at": [8, -30]},
                            {"type": "fix", "point": "b", "at": [40, -30]}]}),
        __import__("cadcore.model.document", fromlist=["Feature"]).Feature("web", "rib", {
            "body": "base", "sketch": "line", "thickness": 3, "direction": [0, 1],
            "until": {"face": "base/+z"}})],
        result="web")
    from cadcore.geometry import kernel
    body = Evaluator(doc).build()
    assert kernel.volume(body) == pytest.approx(60 * 40 * 8 + 32 * 22 * 3, rel=1e-6)


def test_a_hinge_limit_is_degrees_in_an_inch_document_and_a_slider_s_is_length():
    raw = {"unit": "in", "parameters": {}, "result": "asm", "features": [
        {"id": "a", "type": "box", "size": [1, 1, 1]}, {"id": "b", "type": "box", "size": [1, 1, 1]},
        {"id": "asm", "type": "assemble", "bodies": ["a", "b"], "mates": [
            {"kind": "hinge", "faces": ["a:a/side", "b:b/side"], "min": -30, "max": 30},
            {"kind": "slider", "faces": ["a:a/side", "b:b/side"], "min": -1, "max": 1}]}]}
    doc = Document.from_dict(raw)
    hinge, slider = doc.features[-1].args["mates"]
    assert (hinge["min"], hinge["max"]) == (-30, 30)
    assert (slider["min"], slider["max"]) == pytest.approx((-25.4, 25.4))


def test_a_fill_s_loop_is_an_index_in_any_unit():
    raw = {"unit": "in", "parameters": {}, "result": "patch", "features": [
        {"id": "s", "type": "surface", "method": "planar", "sketch": "sq"},
        {"id": "patch", "type": "fill", "body": "s", "loop": 1}]}
    assert Document.from_dict(raw).features[-1].args["loop"] == 1


def test_a_null_argument_is_the_argument_left_out():
    s = Session(autosave=False)
    s.op_new_document()
    out = s.op_add_feature("box", {"size": [10, 10, 10], "at": None}, feature_id="b")
    assert out["volume_mm3"] == 1000 and "at" not in s.doc.features[0].args
    out = s.op_add_feature("pattern", {"body": "b", "count": 2, "spacing": 20, "direction": [1, 0, 0],
                                        "axis": None}, feature_id="row")
    assert out["volume_mm3"] == pytest.approx(2000)


def test_a_sketch_with_a_file_and_an_on_keeps_the_file(tmp_path):
    import pathlib
    data = pathlib.Path(__file__).resolve().parents[2] / "sketching" / "tests" / "data" / "plate.dxf"
    s = Session(autosave=False)
    s.op_new_document()
    s.op_add_feature("box", {"size": [200, 200, 10]}, feature_id="blk")
    s.op_add_feature("sketch", {"on": {"body": "blk", "face": "blk/+z"},
                                "file": {"path": str(data)}}, feature_id="drawn")
    s.evaluator.prepare("drawn")
    assert len(s.evaluator.sketches["drawn"].loops) == 2


def test_a_part_that_contains_itself_is_refused_by_kind(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps({"features": [{"id": "inner", "type": "part", "document": "b.json"}],
                             "result": "inner"}), encoding="utf-8")
    b.write_text(json.dumps({"features": [{"id": "inner", "type": "part", "document": "a.json"}],
                             "result": "inner"}), encoding="utf-8")
    with pytest.raises(CadError) as caught:
        Evaluator(Document.load(str(a))).build()
    assert caught.value.kind == "circular_part"


def test_a_missing_nested_argument_is_named_not_raised_raw():
    s = Session(autosave=False)
    s.op_new_document()
    s.op_add_feature("box", {"size": [40, 40, 10]}, feature_id="blk")
    with pytest.raises(CadError) as caught:
        s.op_add_feature("hole", {"body": "blk", "face": "blk/+z", "diameter": 4, "depth": 5,
                                  "pattern": {"count": 3}}, feature_id="h")
    assert caught.value.kind == "missing_argument" and caught.value.detail["argument"] == "direction"


def test_ten_and_ten_point_zero_are_one_cache_key():
    def key(size):
        ev = Evaluator(Document.from_dict({"features": [{"id": "b", "type": "box", "size": size}], "result": "b"}))
        ev.build()
        return ev.keys["b"]
    assert key([10, 10, 10]) == key([10.0, 10.0, 10.0])
