"""What a document may say in an expression, and what it may not.

Three things this file is about, all found by a review:

* a parameter written as an expression over the others. Every comment in
  `document.py` assumes they work -- `_scale_lengths` skips converting them
  because "an expression is a relationship between parameters that are already
  converted" -- and nothing resolved them, so `"height*2"` travelled through
  `evaluate` unchanged and reached `float()` as a ValueError raised from inside
  the kernel;
* `[1, 2] * 10**7`. `_DIGITS` closes the same denial of service for `9**9**9`,
  through the one operator nobody thought about: twenty million elements in a
  tenth of a second;
* a count in a document written in inches. `pattern`'s own count of 3 survives,
  because the converter reads the declaration -- but a *parameter* feeding it
  has no declaration, the default is "length", and `n: 3` arrived as 76.2.
"""
import pytest

from cadcore.evaluation.api import build, describe
from cadcore.model.document import Document
from cadcore.errors import CadError


def _volume(raw):
    import copy

    return describe(build(Document.from_dict(copy.deepcopy(raw)))[0])["volume_mm3"]


BOX = {"parameters": {}, "result": "b",
       "features": [{"id": "b", "type": "box", "size": ["width", 10, "deep"],
                     "at": [0, 0, 0], "centred": False}]}


def test_a_parameter_can_be_written_in_terms_of_another():
    raw = dict(BOX, parameters={"height": 10.0, "width": "height*2",
                                "deep": "width/4"})
    assert _volume(raw) == pytest.approx(20.0 * 10.0 * 5.0)


def test_and_moving_the_one_it_is_written_from_moves_it():
    raw = dict(BOX, parameters={"height": 20.0, "width": "height*2",
                                "deep": "width/4"})
    assert _volume(raw) == pytest.approx(40.0 * 10.0 * 10.0)


def test_a_parameter_defined_in_terms_of_itself_is_refused_by_name():
    doc = Document.from_dict({"parameters": {"a": "b", "b": "a"},
                              "result": "x", "features": []})
    with pytest.raises(CadError) as caught:
        doc.evaluate("a")
    assert caught.value.kind == "bad_expression"
    assert "in terms of itself" in caught.value.message


def test_a_repetition_that_would_eat_the_memory_is_refused():
    doc = Document.from_dict({"parameters": {}, "result": "x", "features": []})
    with pytest.raises(CadError) as caught:
        doc.evaluate("[1, 2] * (10 ** 7)")
    assert caught.value.kind == "expression_too_big"


def test_a_list_of_a_size_anybody_would_write_still_works():
    doc = Document.from_dict({"parameters": {}, "result": "x", "features": []})
    assert doc.evaluate("[1, 2] * 3") == [1, 2, 1, 2, 1, 2]


COUNTED = {"unit": "in", "parameters": {"n": 3.0, "w": 2.0}, "result": "p",
           "features": [
               {"id": "b", "type": "box", "size": ["w", 1, 1],
                "at": [0, 0, 0], "centred": False},
               {"id": "p", "type": "pattern", "body": "b", "count": "n",
                "spacing": "w", "direction": [1, 0, 0]}]}


def test_a_parameter_used_as_a_count_in_an_inch_document_is_refused():
    with pytest.raises(CadError) as caught:
        Document.from_dict(dict(COUNTED))
    assert caught.value.kind == "parameter_unit_unknown"
    assert "count" in caught.value.message
    assert '"n": "count"' in caught.value.detail["hint"]


def test_and_saying_what_it_is_makes_it_work():
    doc = Document.from_dict(dict(COUNTED, parameter_units={"n": "count"}))
    assert doc.parameters["n"] == 3.0
    assert doc.parameters["w"] == pytest.approx(50.8)


def test_the_same_document_in_millimetres_needs_no_declaration():
    raw = dict(COUNTED)
    raw.pop("unit")
    assert Document.from_dict(raw).parameters["n"] == 3.0


def test_a_chain_of_parameters_is_evaluated_once_each():
    """`p2 = p1+p1`, sixteen deep, took half a second and quadrupled per step:
    every mention re-evaluated the parameter below it. Forty deep is exact
    and instant when each is resolved once."""
    import time

    from cadcore.model.document import Document

    params = {"p1": 1}
    for i in range(2, 41):
        params["p%d" % i] = "p%d+p%d" % (i - 1, i - 1)
    started = time.perf_counter()
    assert Document(parameters=params).evaluate("p40") == 2 ** 39
    assert time.perf_counter() - started < 0.5


def test_a_number_with_too_many_digits_is_refused_however_it_is_reached():
    """The power guard looked at one power; a chain of products walked past it."""
    import pytest

    from cadcore.model.document import Document
    from cadcore.errors import CadError

    params = {"a": "10**29", "b": "a*a", "c": "b*b", "d": "c*c", "e": "d*d", "f": "e*e"}
    with pytest.raises(CadError) as raised:
        Document(parameters=params).evaluate("f")
    assert raised.value.kind == "expression_too_big"
    with pytest.raises(CadError) as raised:
        Document(parameters={"x": "1e308"}).evaluate("x*10")
    assert raised.value.kind == "expression_too_big"
