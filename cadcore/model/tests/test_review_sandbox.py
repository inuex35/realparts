"""The expression sandbox refuses by kind at every edge, and a document of another's
making is refused by kind rather than crashing."""
import pytest

from cadcore import names
from cadcore.errors import CadError
from cadcore.model.document import Document, names_in


def doc(**parameters) -> Document:
    return Document.from_dict({"parameters": parameters, "features": [], "result": None})


@pytest.mark.parametrize("text", ["1e999", "9" * 40, "0.5**-200", "10**-31", "1+" * 20000 + "1"])
def test_a_number_that_is_not_a_size_is_refused_by_kind(text):
    with pytest.raises(CadError) as caught:
        doc().evaluate(text)
    assert caught.value.kind == "expression_too_big"


def test_names_in_mentions_parameters_not_functions():
    assert names_in("max(w, 2*t)") == {"w", "t"}


def test_an_inch_document_with_an_expression_bound_is_read():
    raw = {"unit": "in", "parameters": {"t": 1.0, "w": 4.0},
           "parameters_bounds": {"t": [0.5, "w/2"]}, "features": [], "result": None}
    d = Document.from_dict(raw)
    assert d.parameters["t"] == 25.4 and d.bounds["t"][0] == pytest.approx(12.7)
    assert d.bounds["t"][1] == "w/2"


@pytest.mark.parametrize("raw", [
    {"features": [], "drawing": None, "result": None},
    {"features": [], "parameter_units": None, "result": None},
])
def test_a_null_section_is_read_as_absent(raw):
    Document.from_dict(dict(raw, parameters={}))


@pytest.mark.parametrize("raw", [
    {"features": [], "parameter_units": [1], "result": None},
    {"features": [], "parameters": {"t": 1}, "parameters_bounds": {"t": [1, 2, 3]}, "result": None},
    {"features": [], "parameters": {"t": 1}, "parameters_bounds": {"t": 5}, "result": None},
])
def test_a_section_of_the_wrong_shape_is_refused_by_kind(raw):
    with pytest.raises(CadError) as caught:
        Document.from_dict(raw)
    assert caught.value.kind == "bad_arguments"


def test_a_document_owns_its_lists():
    raw = {"parameters": {}, "features": [{"id": "b", "type": "box", "size": [1, 2, 3]}], "result": "b"}
    d = Document.from_dict(raw)
    d.features[0].args["size"][0] = 99
    assert raw["features"][0]["size"][0] == 1


def test_a_boundary_edge_is_known_by_its_separator_not_a_substring():
    assert names.is_boundary(names.boundary("lid/+z"))
    assert names.is_boundary(names.boundary("lid/+z", 2))
    assert not names.is_boundary(names.edge("lid/open", "lid/+z"))


def test_a_name_check_says_what_it_is_checking():
    with pytest.raises(CadError) as caught:
        names.check_id("top#2", "sketch line name")
    assert "sketch line name" in caught.value.message and caught.value.kind == "bad_arguments"
