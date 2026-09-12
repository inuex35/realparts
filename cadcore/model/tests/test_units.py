"""A document not written in millimetres, converted whole or not at all.

The suite already asserted that an inch box builds the same solid as a
millimetre one. A box's size is a declared argument, and the converter walks
the declaration, so that test could only ever pass. A *sketch* is declared
`Anything()` -- the shape of it is the sketch language, not the argument schema
-- and the converter walked straight past it: a one inch square extruded a
quarter of an inch came out 1 mm by 1 mm by 6.35 mm, a part 645 times too small
by volume, with nothing refused and nothing said.

So these are about the parts of a document the declaration cannot describe, and
they are written as measurements of the finished solid wherever they can be: a
test that reads back the same numbers the converter wrote would have passed
against the bug.
"""
import copy

import pytest

from cadcore.evaluation.api import build, describe
from cadcore.model.document import Document
from cadcore.errors import CadError

INCH_PLATE = {
    "meta": {"name": "a one inch square, drawn in inches"},
    "unit": "in",
    "parameters": {"thick": 0.25},
    "features": [
        {"id": "square", "type": "sketch",
         "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
         "points": {"a": [0, 0], "b": [1, 0], "c": [1, 1], "d": [0, 1]},
         "lines": {"s": ["a", "b"], "e": ["b", "c"],
                   "n": ["c", "d"], "w": ["d", "a"]},
         "allow_underconstrained": True},
        {"id": "plate", "type": "extrude", "sketch": "square", "distance": "thick"},
    ],
    "result": "plate",
}


def _built(raw: dict):
    return describe(build(Document.from_dict(copy.deepcopy(raw)))[0])


def test_a_sketch_drawn_in_inches_is_a_part_measured_in_inches():
    """One inch by one inch by a quarter, which is 4096.766 cubic millimetres.

    It used to be 6.35: the extrude's distance was converted and the square it
    swept was not, so two of the part's three dimensions were 25.4 times too
    small.
    """
    assert _built(INCH_PLATE)["volume_mm3"] == pytest.approx(25.4 ** 3 * 0.25,
                                                             abs=1e-3)


def test_the_same_shape_written_in_millimetres_is_the_same_solid():
    metric = copy.deepcopy(INCH_PLATE)
    metric["unit"] = "mm"
    metric["parameters"]["thick"] = 6.35
    square = metric["features"][0]["points"]
    square.update({"b": [25.4, 0], "c": [25.4, 25.4], "d": [0, 25.4]})
    assert _built(metric)["volume_mm3"] == pytest.approx(
        _built(INCH_PLATE)["volume_mm3"], abs=1e-6)


def test_every_kind_of_sketch_length_is_converted():
    raw = copy.deepcopy(INCH_PLATE)
    sketch = raw["features"][0]
    sketch["arcs"] = {"a1": {"centre": "a", "from": "b", "to": "c", "radius": 0.4}}
    sketch["circles"] = {"c1": {"centre": "a", "radius": 0.5}}
    sketch["slots"] = {"s1": {"from": "a", "to": "b", "width": 0.2}}
    sketch["offsets"] = {"o1": {"of": "s", "distance": 0.3}}
    sketch["constraints"] = [{"type": "fix", "point": "a", "at": [0.5, 0.25]}]
    doc = Document.from_dict(copy.deepcopy(raw))
    args = doc.feature("square").args
    assert args["points"]["b"] == [25.4, 0.0]
    assert args["arcs"]["a1"]["radius"] == pytest.approx(10.16)
    assert args["circles"]["c1"]["radius"] == pytest.approx(12.7)
    assert args["slots"]["s1"]["width"] == pytest.approx(5.08)
    assert args["offsets"]["o1"]["distance"] == pytest.approx(7.62)
    assert args["constraints"][0]["at"] == [pytest.approx(12.7), pytest.approx(6.35)]


def test_an_angle_constraint_is_still_degrees_and_a_direction_still_a_direction():
    """The other half of the same bug: a converter that cannot tell a
    constraint's length from its angle turns 30 degrees into 762 of them."""
    raw = copy.deepcopy(INCH_PLATE)
    raw["features"][0]["constraints"] = [
        {"type": "angle", "lines": ["s", "e"], "value": 30.0},
        {"type": "distance", "points": ["a", "b"], "value": 1.0},
    ]
    args = Document.from_dict(raw).feature("square").args
    assert args["constraints"][0]["value"] == 30.0
    assert args["constraints"][1]["value"] == pytest.approx(25.4)
    assert args["plane"]["normal"] == [0, 0, 1]


def test_a_sketch_comes_back_out_in_the_unit_it_was_written_in():
    """Converting in and out have to be inverses, or saving a file grows it by
    25.4 every time."""
    doc = Document.from_dict(copy.deepcopy(INCH_PLATE))
    written = doc.as_dict()["features"][0]
    assert written["points"]["b"] == [1.0, 0.0]
    assert written["points"]["c"] == [1.0, 1.0]


def test_a_number_handed_to_another_document_is_refused_rather_than_guessed_at():
    """A `part` drives a sub-document, and the sub-document says which of its
    own parameters are lengths. From here that is not knowable, so a plain
    number is refused: converting it on a guess and leaving it alone in silence
    are both wrong, and one of them was what happened."""
    raw = {"unit": "in", "parameters": {"w": 2.0}, "result": "p",
           "features": [{"id": "p", "type": "part", "document": "other.json",
                         "parameters": {"w": 1.5}}]}
    with pytest.raises(CadError) as caught:
        Document.from_dict(copy.deepcopy(raw))
    assert caught.value.kind == "parameter_unit_unknown"
    assert "p.w" in caught.value.message

    # an expression is written over parameters this document has already
    # converted, so it passes through untouched and is allowed
    raw["features"][0]["parameters"] = {"w": "w/2"}
    assert Document.from_dict(raw).feature("p").args["parameters"]["w"] == "w/2"


def test_millimetre_documents_are_not_affected_by_any_of_this():
    """Nothing is multiplied in millimetres, so nothing can be multiplied by
    the wrong thing -- and a part feature with a plain number stays legal."""
    raw = {"parameters": {}, "result": "p",
           "features": [{"id": "p", "type": "part", "document": "other.json",
                         "parameters": {"w": 1.5}}]}
    assert Document.from_dict(raw).feature("p").args["parameters"]["w"] == 1.5


def test_an_inch_documents_requirements_and_studies_are_in_inches_too(tmp_path):
    """`bbox_x <= 8.5` in an inch document is 215.9 mm, not 8.5; `mesh_size:
    0.25` is a quarter inch, not a quarter millimetre. And saved back, both
    read as they were written."""
    import json

    from cadcore.model.document import Document

    raw = {"unit": "in", "parameters": {}, "features": [
        {"id": "b", "type": "box", "size": [8, 2, 1]}],
        "requirements": [{"id": "fits", "quantity": "bbox_x", "compare": "<=", "value": 8.5},
                         {"id": "light", "quantity": "mass_g", "compare": "<=", "value": 500}],
        "studies": [{"id": "s", "type": "static_structural", "fix": ["b/-x"],
                     "loads": [{"face": "b/+x", "force": [0, 0, -10]}], "mesh_size": 0.25}],
        "result": "b"}
    doc = Document.from_dict(raw)
    assert doc.requirements[0]["value"] == pytest.approx(8.5 * 25.4)
    assert doc.requirements[1]["value"] == 500          # grams are grams
    assert doc.studies[0]["mesh_size"] == pytest.approx(0.25 * 25.4)
    assert doc.studies[0]["loads"][0]["force"] == [0, 0, -10]   # newtons are newtons
    back = doc.as_dict()
    assert back["requirements"][0]["value"] == pytest.approx(8.5)
    assert back["studies"][0]["mesh_size"] == pytest.approx(0.25)
    # and the requirement is judged in millimetres: an 8 inch box is under 8.5
    from cadcore.ops.session import Session
    session = Session(autosave=False)
    session.op_load_json(document=raw)
    row = session.op_build()["requirements"][0]
    assert row["ok"] is True and row["got"] == pytest.approx(8 * 25.4)
