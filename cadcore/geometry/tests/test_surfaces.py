"""Surfaces: the shapes that are not solids, and the ways back to solid."""
import pytest

from cadcore.geometry import kernel
from cadcore.model.document import Document
from cadcore.errors import CadError
from cadcore.evaluation.graph import Evaluator


def section(z, half_w, half_d, ids=("south", "east", "north", "west")):
    return {"points": {"p1": [-half_w, -half_d], "p2": [half_w, -half_d],
                       "p3": [half_w, half_d], "p4": [-half_w, half_d]},
            "lines": {ids[0]: ["p1", "p2"], ids[1]: ["p2", "p3"],
                      ids[2]: ["p3", "p4"], ids[3]: ["p4", "p1"]},
            "plane": {"origin": [0, 0, z], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
            "allow_underconstrained": True}


def skinned_document(thickness=2.0):
    return {
        "parameters": {"t": thickness},
        "features": [
            dict(id="lower", type="sketch", **section(0, 20, 15)),
            dict(id="upper", type="sketch", **section(20, 15, 10)),
            {"id": "skin", "type": "surface", "method": "skin",
             "sketches": ["lower", "upper"]},
            {"id": "walled", "type": "thicken", "body": "skin", "thickness": "t"}],
        "result": "walled"}


def build(document, target=None):
    doc = Document.from_dict(document)
    evaluator = Evaluator(doc)
    return evaluator.build(target), evaluator


# --- what a surface is ---------------------------------------------------------

def test_a_skin_is_open_and_says_so():
    body, ev = build(skinned_document(), target="skin")
    assert kernel.kind_of(body.shape) == "shell"
    assert kernel.volume(body) == 0.0            # nothing is enclosed, so nothing is claimed
    assert kernel.area(body) > 0
    assert body.face_names() == ["skin/south", "skin/east", "skin/north", "skin/west"]


def test_an_open_edge_has_a_name():
    """Boundary edges are what surface work refers to, so they are named."""
    body, _ = build(skinned_document(), target="skin")
    edges = sorted(body.edge_table())
    assert "skin/south|open" in edges and "skin/south|open#2" in edges
    loops = kernel.boundary_loops(body)
    assert len(loops) == 2 and all(len(loop) == 4 for loop in loops)


def test_thickening_closes_a_surface_into_a_solid():
    body, _ = build(skinned_document())
    assert kernel.kind_of(body.shape) == "solid"
    assert kernel.volume(body) > 0 and not kernel.boundary_loops(body)
    names = body.face_names()
    assert "skin/south" in names and "thick/south" not in names
    assert "walled/south" in names            # the far side, under the new feature
    assert any(n.startswith("walled/wall") for n in names)


def test_a_thickened_sheet_is_its_area_times_its_thickness():
    """A flat sheet is the one case with an exact volume."""
    plate = kernel.box("p", [40, 30, 2], (0, 0, 0), centred=False)
    sheet = kernel.delete_face("sheet", plate,
                               [n for n in plate.face_names() if n != "p/+z"],
                               heal=False)
    walled = kernel.thicken("wall", sheet, 1.5)
    assert kernel.volume(walled) == pytest.approx(40 * 30 * 1.5, rel=1e-6)
    assert "wall/+z" in walled.face_names()      # the far side of p/+z


def test_the_thickness_is_a_parameter_like_any_other():
    thin, _ = build(skinned_document(1.0))
    thick, _ = build(skinned_document(3.0))
    assert kernel.volume(thick) == pytest.approx(3 * kernel.volume(thin), rel=0.05)


# --- patching and closing ------------------------------------------------------

def test_a_deleted_face_leaves_a_hole_that_can_be_patched():
    """The surface round trip: open a solid, patch the opening, sew it shut."""
    box = kernel.box("b", [40, 30, 10], (0, 0, 0), centred=False)
    opened = kernel.delete_face("cut", box, ["b/+z"], heal=False)
    assert kernel.kind_of(opened.shape) == "shell"

    loops = kernel.boundary_loops(opened)
    assert len(loops) == 1
    patch = kernel.fill("lid", opened, loops[0], "G0")
    assert patch.notes["fill"]["gap_mm"] == pytest.approx(0.0, abs=1e-6)

    closed = kernel.sew("sewn", [opened, patch])
    assert kernel.kind_of(closed.shape) == "solid"
    assert kernel.volume(closed) == pytest.approx(12000.0, rel=1e-6)
    assert "lid/patch" in closed.face_names() and "b/-z" in closed.face_names()


def test_capping_does_the_whole_round_trip():
    box = kernel.box("b", [40, 30, 10], (0, 0, 0), centred=False)
    opened = kernel.delete_face("cut", box, ["b/+z", "b/-z"], heal=False)
    closed = kernel.cap("lid", opened)
    assert kernel.kind_of(closed.shape) == "solid"
    assert kernel.volume(closed) == pytest.approx(12000.0, rel=1e-6)


def test_capping_something_already_closed_is_refused():
    box = kernel.box("b", [10, 10, 10], (0, 0, 0))
    with pytest.raises(CadError) as exc:
        kernel.cap("lid", box)
    assert exc.value.kind == "already_closed"


# --- direct editing ------------------------------------------------------------

def test_deleting_a_face_with_healing_undoes_a_fillet():
    """Defeaturing: take the fillet away and let the neighbours close the gap."""
    box = kernel.box("b", [40, 30, 10], (0, 0, 0), centred=False)
    rounded = kernel.fillet("f", box, ["b/+x|b/+z"], 4.0)
    assert kernel.volume(rounded) < 12000.0

    plain = kernel.delete_face("plain", rounded,
                               [n for n in rounded.face_names() if n.startswith("f/")])
    assert kernel.volume(plain) == pytest.approx(12000.0, rel=1e-9)
    assert plain.dropped == ["f/side"]        # said once, not once per piece


def test_trimming_keeps_the_side_that_was_asked_for():
    plate = kernel.box("p", [40, 30, 2], (0, 0, 0), centred=False)
    sheet = kernel.delete_face("sheet", plate, ["p/-z"], heal=False)
    tool = kernel.box("t", [20, 30, 20], (0, 0, -5), centred=False)

    outside = kernel.trim("trim", sheet, tool, "outside")
    inside = kernel.trim("trim", sheet, tool, "inside")
    assert kernel.area(outside) + kernel.area(inside) == pytest.approx(
        kernel.area(sheet), rel=1e-6)
    assert "p/+z@0" in outside.face_names() and "p/+z@1" in inside.face_names()


def test_extending_a_face_makes_it_reach():
    plate = kernel.box("p", [40, 30, 2], (0, 0, 0), centred=False)
    one = kernel.delete_face("one", plate,
                             [n for n in plate.face_names() if n != "p/+z"], heal=False)
    bigger = kernel.extend("bigger", one, "p/+z", 10.0)
    assert kernel.area(bigger) == pytest.approx(60 * 50, rel=1e-6)
    assert bigger.canonical("p/+z") == "bigger/face"     # the old name still lands


# --- what a surface may not be asked to do -------------------------------------

def test_an_open_shell_refuses_the_things_that_need_a_solid(open_document):
    document = skinned_document()
    document["result"] = "skin"
    session = open_document(document)
    out = session.op_build()
    assert out["kind"] == "shell" and out["open_boundaries"] == 2

    with pytest.raises(CadError) as exc:
        session.op_printability()
    assert exc.value.kind == "not_a_solid"
    assert "thicken" in exc.value.detail["hint"]
