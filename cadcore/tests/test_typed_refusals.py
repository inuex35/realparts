"""A wrong input is refused by kind, not reported as a Python exception.

A program reads a refusal, so it has to be a code, not a traceback: each of
these came out as internal_error, KeyError, IndexError or ValueError.
"""
from __future__ import annotations

import pytest

from cadcore.errors import CadError
from cadcore.model.document import Document
from cadcore.service.server import Session, handle


def test_a_feature_missing_what_its_handler_reads_is_said_by_name():
    s = Session(autosave=False)
    s.op_new_document()
    s.op_add_feature("box", {"size": [10, 10, 10]})
    with pytest.raises(CadError) as refused:
        s.op_add_feature("pattern", {"body": "box1", "count": 3})     # no direction
    assert refused.value.kind == "missing_argument"
    assert refused.value.detail["argument"] == "direction"


def test_a_constraint_missing_its_value_is_a_bad_constraint():
    from cadcore.sketching import solve

    spec = {"plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
            "points": {"a": [0, 0], "b": [10, 0]}, "lines": {"l": ["a", "b"]},
            "constraints": [{"type": "distance", "points": ["a", "b"]}],
            "allow_underconstrained": True}
    with pytest.raises(CadError) as refused:
        solve(spec, lambda v: v)
    assert refused.value.kind == "bad_constraint"
    assert refused.value.detail["index"] == 0


def test_a_requirement_the_pattern_takes_and_float_refuses():
    from cadcore.simulation.study import Requirement

    for text in (">= 1e", ">= 2..3", ">= +-1"):
        with pytest.raises(CadError) as refused:
            Requirement.parse("mass_g", text)
        assert refused.value.kind == "bad_requirement"
    assert Requirement.parse("mass_g", ">= 2").value == 2.0


def test_a_document_with_the_wrong_shape_is_refused_by_kind():
    for raw in ({"parameters": [1, 2]}, {"features": {"id": "b"}},
                {"parameters_bounds": [0, 1]}, {"result": 3}, {"format": True, "features": "x"}):
        with pytest.raises(CadError) as refused:
            Document.from_dict(raw)
        assert refused.value.kind in ("bad_arguments", "unknown_format"), raw


def test_the_server_refuses_a_value_of_the_wrong_type_before_the_call():
    s = Session(autosave=False)
    s.op_new_document()
    reply = handle(s, {"op": "set_parameter", "name": "w", "value": {"a": 1}})
    assert reply["ok"] is False and reply["kind"] == "bad_arguments"
    assert "value" in reply["message"]
    reply = handle(s, {"op": "apply", "ops": {"op": "x"}})
    assert reply["ok"] is False and reply["kind"] == "bad_arguments"
    reply = handle(s, {"op": "add_feature", "type": 7})
    assert reply["ok"] is False and reply["kind"] == "bad_arguments"
    # an int where a float is declared is fine, and None where allowed
    reply = handle(s, {"op": "add_plane", "on": "xy", "offset": 5})
    assert reply["ok"] is True
