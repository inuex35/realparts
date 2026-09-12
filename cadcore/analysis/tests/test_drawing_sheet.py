"""Drawings: projected from the model, dimensioned by name, measured at draw time."""
import pytest

from cadcore import Document
from cadcore.analysis.drawing import measure, project, sheet
from cadcore.evaluation.graph import Evaluator
from cadcore.geometry.kernel import CadError
from cadcore.service.server import Session


@pytest.fixture(scope="module")
def bracket():
    return Evaluator(Document.load("examples/bracket.json")).build()


def test_the_views_have_the_size_of_the_part(bracket):
    front = project(bracket, "front")
    assert (round(front.width), round(front.height)) == (80, 55)
    top = project(bracket, "top")
    assert (round(top.width), round(top.height)) == (80, 60)
    assert front.visible and front.hidden        # hidden lines are kept separate
    with pytest.raises(CadError) as exc:
        project(bracket, "oblique")
    assert exc.value.kind == "unknown_view"


def test_dimensions_are_measured_on_the_model(bracket):
    assert measure(bracket, "linear", ["plate/-x@0", "plate/+x@0"]) == pytest.approx(80)
    assert measure(bracket, "diameter", ["bore_tool/side"]) == pytest.approx(28)
    assert measure(bracket, "radius", ["h1/side"]) == pytest.approx(3.3)
    with pytest.raises(CadError) as exc:
        measure(bracket, "diameter", ["plate/+z"])
    assert exc.value.kind == "not_a_cylinder"
    with pytest.raises(CadError) as exc:
        measure(bracket, "linear", ["plate/+z", "plate/nope"])
    assert exc.value.kind == "unresolved_reference"


def test_the_sheet_carries_the_measurements_and_the_controls(bracket):
    doc = Document.load("examples/bracket.json")
    svg = sheet(bracket, doc.drawing, doc.meta)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    for text in ("FRONT", "TOP", "RIGHT", "80.0", "55.0", "⌀28.0", "camera bracket"):
        assert text in svg
    assert "⌖" in svg and "⌀0.10" in svg      # the position control and its value
    assert "⏥" in svg                          # flatness on the datum face


def test_the_drawing_cannot_disagree_with_the_model():
    """Guards: after a parameter change the sheet shows the new dimension without being told."""
    session = Session()
    session.op_open("examples/bracket.json")
    session.op_build()
    before = session.op_drawing()["svg"]
    assert ">80.0</text>" in before          # the dimension text, not a coordinate
    session.op_set_parameter("width", 96)
    after = session.op_drawing()["svg"]
    assert ">96.0</text>" in after and ">80.0</text>" not in after


def test_a_tolerance_has_to_name_something_real(bracket):
    doc = Document.load("examples/bracket.json")
    spec = dict(doc.drawing)
    spec["tolerances"] = [{"view": "front", "face": "bore_tool/side",
                           "kind": "spookiness", "value": 0.1}]
    with pytest.raises(CadError) as exc:
        sheet(bracket, spec, doc.meta)
    assert exc.value.kind == "unknown_tolerance"

    spec["tolerances"] = [{"view": "front", "face": "bore_tool/side",
                           "kind": "position", "value": 0.1, "datums": ["Z"]}]
    with pytest.raises(CadError) as exc:
        sheet(bracket, spec, doc.meta)
    assert exc.value.kind == "unknown_datum"


def test_a_section_cuts_the_part_and_hatches_what_it_cut(bracket):
    """Guards: hatching is clipped to the faces the cut exposed, and the kept
    half is the one behind the plane."""
    from ...geometry.solids import booleans, primitives
    from cadcore.analysis.drawing import section

    cut = section(bracket, "front", 0.0)

    assert cut.name == "front section"
    assert cut.hatch, "a section with nothing hatched is not a section"
    assert cut.hidden == []                      # nothing is hidden in a cut view

    # a section removes the material between the viewer and the cut: the front
    # viewer stands at -y, so a +y column survives a cut at y=0 and a -y one does not
    slab = primitives.box("slab", [40, 40, 10])
    far = booleans.fuse("f", slab, primitives.box("fc", [8, 8, 30], at=[0, 15, 20]))
    near = booleans.fuse("n", slab, primitives.box("nc", [8, 8, 30], at=[0, -15, 20]))
    assert section(far, "front", 0.0).bounds[3] > 30
    assert section(near, "front", 0.0).bounds[3] < 6


def test_a_section_that_misses_the_part_says_so(bracket):
    from cadcore.analysis.drawing import section

    with pytest.raises(CadError) as exc:
        section(bracket, "front", 500.0)
    assert exc.value.kind == "empty_section"


def test_a_sheet_can_ask_for_a_section(bracket):
    """Guards: `{"sections": {"front": 0}}` is a distance along the view's own normal."""
    svg = sheet(bracket, {"views": ["front", "top"], "sections": {"front": 0.0},
                          "sheet": "A4"})
    assert 'class="hatch"' in svg
    assert "FRONT SECTION" in svg
