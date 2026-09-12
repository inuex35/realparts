"""Sheet metal: one thickness everywhere, and a flat pattern that is computed."""
import math

import pytest

from cadcore.geometry import kernel
from cadcore.errors import CadError
from ..solids.sheet import bend_allowance, flange
from cadcore.geometry.core.occ import bounds


def blank(thickness: float = 2.0):
    return kernel.box("sheet", [80, 50, thickness], (0, 0, 0), centred=False)


def test_a_flange_adds_the_material_it_bends():
    """The flap is an arc plus a flat, both at the sheet's thickness."""
    sheet = blank()
    out = flange("wall", sheet, "sheet/+x|sheet/+z", length=20, angle=90, radius=2)

    quarter = math.pi / 4 * ((2 + 2) ** 2 - 2 ** 2)      # the bend, seen end on
    added = 50 * (20 * 2 + quarter)
    assert kernel.volume(out) == pytest.approx(kernel.volume(sheet) + added, rel=0.02)


def test_the_sign_of_the_angle_says_which_way_it_folds():
    """A positive angle folds towards the face normal, a negative one away."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    heights = {}
    for angle in (90, -90):
        out = flange("wall", blank(), "sheet/+x|sheet/+z", length=20, angle=angle,
                     radius=2)
        box = Bnd_Box()
        BRepBndLib.Add_s(out.shape, box)
        heights[angle] = (round(bounds(box)[2], 2), round(bounds(box)[5], 2))
    # an up bend never goes below z=0, a down bend never above z=2; the flap
    # tip reaches thickness + radius + length past the far surface
    assert heights[90] == (0.0, 24.0)           # up: the flap stands above
    assert heights[-90] == (-22.0, 2.0)         # down: it hangs below


def test_a_flange_names_its_faces_for_what_they_are():
    """Faces are named by role (`bend`, `face`, `end`), so the next flange can
    refer to the end of this flap."""
    out = flange("wall", blank(), "sheet/+x|sheet/+z", length=20, angle=90, radius=2)
    assert {"wall/bend", "wall/bend_out", "wall/face", "wall/back", "wall/end"} \
        <= set(out.face_names())
    assert "wall/end|wall/face" in out.edge_table()   # where the next one bends


def test_the_thickness_is_measured_not_asked_for():
    """The thickness is measured off the part, not taken from an argument."""
    out = flange("wall", blank(1.5), "sheet/+x|sheet/+z", length=10, radius=1.5)
    assert out.notes["bends"][0]["thickness_mm"] == pytest.approx(1.5, abs=1e-3)


def test_the_allowance_is_the_neutral_axis_not_the_inside():
    """The allowance is taken on the neutral axis; K=0.44 is the usual factor
    for mild steel and aluminium."""
    assert bend_allowance(90, 2.0, 2.0) == pytest.approx(
        math.radians(90) * (2.0 + 0.44 * 2.0))
    # a tighter radius eats less; a thicker sheet eats more
    assert bend_allowance(90, 1.0, 2.0) < bend_allowance(90, 2.0, 2.0)
    assert bend_allowance(90, 2.0, 3.0) > bend_allowance(90, 2.0, 2.0)


def test_the_flat_pattern_comes_from_the_bends(open_example):
    """The flat pattern comes from the recorded bends, not from unfolding the
    solid: the developed length depends on material stretch."""
    session = open_example("sheet_bracket.json")
    flat = session.op_flat_pattern()

    assert flat["thickness_mm"] == pytest.approx(2.0, abs=1e-3)
    assert [b["feature"] for b in flat["bends"]] == ["wall", "lip"]
    assert [b["angle_deg"] for b in flat["bends"]] == [90.0, -90.0]
    assert flat["added_mm"] == pytest.approx(
        sum(b["allowance_mm"] + b["length_mm"] for b in flat["bends"]), abs=1e-3)


def test_the_bends_survive_the_features_that_come_after(open_example):
    """Guards: a boolean after the bends must keep the bend record."""
    session = open_example("sheet_bracket.json")
    assert len(session.op_flat_pattern()["bends"]) == 2
    edges = session.op_select_edges({"of_face": "plate/top"})["edges"]
    session.op_add_fillet(edges=edges[:2], radius=1.0)
    assert len(session.op_flat_pattern()["bends"]) == 2


def test_a_part_with_no_bends_is_not_sheet_metal(open_document):
    session = open_document({
        "parameters": {}, "features": [
            {"id": "block", "type": "box", "size": [10, 10, 10]}],
        "result": "block"})
    with pytest.raises(CadError) as exc:
        session.op_flat_pattern()
    assert exc.value.kind == "not_sheet_metal"


def test_a_flange_needs_an_edge_that_exists():
    with pytest.raises(CadError) as exc:
        flange("wall", blank(), "no/such|edge", length=10)
    assert exc.value.kind == "unresolved_reference"


# --- the blank ------------------------------------------------------------------

def test_the_flat_pattern_has_an_outline_and_it_adds_up():
    """The blank is laid out from the base sketch loop plus each flange's
    bend line and reach, so its area is the base area plus edge length times
    developed length per bend."""
    from cadcore.model.document import Document
    from cadcore.evaluation.graph import Evaluator
    from ..solids.sheet import flat_pattern

    body = Evaluator(Document.load("examples/sheet_bracket.json")).build()
    flat = flat_pattern(body)

    base, wall, lip = flat["blank"]
    assert len(flat["blank"]) == 1 + len(flat["bends"])
    assert len(flat["bend_lines"]) == len(flat["bends"])

    want = 90 * 60
    for bend in flat["bends"]:
        want += 60 * (bend["length_mm"] + bend["allowance_mm"])
    assert flat["blank_area_mm2"] == pytest.approx(want, abs=1e-3)

    # and the flaps are laid end to end, each starting where the last developed to
    assert flat["extent_mm"][1] == pytest.approx(60.0)
    assert flat["extent_mm"][0] == pytest.approx(
        90 + sum(b["length_mm"] + b["allowance_mm"] for b in flat["bends"]),
        abs=1e-3)
    assert wall[0][0] == pytest.approx(90.0)
    assert lip[0][0] == pytest.approx(wall[2][0])       # the lip starts at the
                                                        # wall's developed end


def test_a_flap_bent_off_another_inherits_which_way_it_unfolds():
    """A flange off another flap cannot project its outward direction into
    the sheet plane (the projection is zero), so it inherits the parent's
    direction and starts at the parent's developed end."""
    from cadcore.model.document import Document
    from cadcore.evaluation.graph import Evaluator
    from ..solids.sheet import flat_pattern

    body = Evaluator(Document.load("examples/sheet_bracket.json")).build()
    flat = flat_pattern(body)
    lip = flat["blank"][2]
    width = max(p[0] for p in lip) - min(p[0] for p in lip)
    bend = flat["bends"][1]
    assert width == pytest.approx(bend["length_mm"] + bend["allowance_mm"], abs=1e-3)
    # the folded lip sits 30.5 mm up the wall; developed, it is further out
    assert min(p[0] for p in lip) > 90 + 30.5


def test_the_blank_is_a_dxf_a_cutter_can_read(tmp_path):
    """Two layers, because they are two instructions: cut, and fold."""
    ezdxf = pytest.importorskip("ezdxf")

    from cadcore.service.server import Session

    session = Session()
    session.op_open("examples/sheet_bracket.json")
    session.op_build()
    out = session.op_flat_dxf(str(tmp_path / "blank.dxf"))
    assert out["bends"] == 2

    drawing = ezdxf.readfile(str(tmp_path / "blank.dxf"))
    assert drawing.dxfversion == "AC1015"
    assert drawing.header.get("$INSUNITS") == 4            # millimetres
    layers = {layer.dxf.name for layer in drawing.layers}
    assert {"BLANK", "BEND"} <= layers

    kinds = [entity.dxftype() for entity in drawing.modelspace()]
    assert kinds.count("LWPOLYLINE") == 3                  # base, wall, lip
    assert kinds.count("LINE") == 2                        # one per bend
    for entity in drawing.modelspace():
        if entity.dxftype() == "LWPOLYLINE":
            assert entity.closed, "a blank the cutter has to close itself"
            assert entity.dxf.layer == "BLANK"
        else:
            assert entity.dxf.layer == "BEND"


def test_a_part_that_was_not_laid_out_says_so_rather_than_writing_nothing():
    from cadcore.errors import CadError
    from ..solids.sheet import flat_dxf

    with pytest.raises(CadError) as exc:
        flat_dxf({"bends": [{"allowance_mm": 1.0}]})
    assert exc.value.kind == "no_flat_pattern"


# --- concave blanks ------------------------------------------------------------
#
# `face_info(...)["centre"]` is a centre of mass, which for a U lies in the slot
# and for a ring in the hole; sheet code must use a point on the face instead.

NOTCHED = {
    "meta": {"name": "a plate with a notch, flanged at the bottom of it"},
    "parameters": {"t": 2.0, "flap": 12.0, "r": 2.0},
    "features": [
        {"id": "outline", "type": "sketch",
         "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
         "points": {"a": [0, 0], "b": [40, 0], "c": [40, 30], "d": [25, 30],
                    "e": [25, 10], "f": [15, 10], "g": [15, 30], "h": [0, 30]},
         "lines": {"s": ["a", "b"], "east": ["b", "c"], "n1": ["c", "d"],
                   "w1": ["d", "e"], "notch": ["e", "f"], "e1": ["f", "g"],
                   "n2": ["g", "h"], "west": ["h", "a"]},
         "allow_underconstrained": True},
        {"id": "plate", "type": "sheet", "sketch": "outline", "thickness": "t"},
        {"id": "flap", "type": "flange", "body": "plate",
         "edge": "plate/notch|plate/top", "length": "flap", "angle": 90.0,
         "radius": "r"},
    ],
    "result": "flap",
}


def test_a_notched_blank_can_be_flanged_at_all():
    """Guards: `_thickness` fires its ray from a point on the face, not the
    centre of mass, which for this outline lies in the notch."""
    import copy

    from cadcore.evaluation.api import build, describe
    from cadcore.model.document import Document

    describe(build(Document.from_dict(copy.deepcopy(NOTCHED)))[0])


def test_a_flange_at_the_bottom_of_a_notch_bends_away_from_the_material():
    """Guards: the outward direction of a flange is not taken from the centre
    of mass, or the flap at the notch is swept into the plate. The added
    volume is the check."""
    import copy
    import math

    from cadcore.evaluation.api import build, describe
    from cadcore.model.document import Document

    raw = copy.deepcopy(NOTCHED)
    raw["result"] = "plate"
    plate = describe(build(Document.from_dict(copy.deepcopy(raw)))[0])
    whole = describe(build(Document.from_dict(copy.deepcopy(NOTCHED)))[0])

    width, length, thick, radius = 10.0, 12.0, 2.0, 2.0
    flap = width * length * thick
    bend = width * (math.pi / 4) * ((radius + thick) ** 2 - radius ** 2)
    assert whole["volume_mm3"] - plate["volume_mm3"] == pytest.approx(
        flap + bend, rel=1e-3)


def test_a_point_on_a_face_is_on_it():
    """`point_on` returns a point that lies on the face."""
    import copy

    from cadcore.evaluation.api import build
    from cadcore.model.document import Document
    from ..solids.sheet import point_on
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_OUT
    from OCP.gp import gp_Pnt

    raw = copy.deepcopy(NOTCHED)
    raw["result"] = "plate"
    body = build(Document.from_dict(raw))[0]
    face = body.face("plate/top")
    here = point_on(face)
    classifier = BRepClass3d_SolidClassifier(body.shape)
    classifier.Perform(gp_Pnt(*here), 1e-6)
    assert classifier.State() != TopAbs_OUT
