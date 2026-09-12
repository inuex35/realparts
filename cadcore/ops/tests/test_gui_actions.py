"""The operations the right-click menus call: one op, one drag, the same in every client."""
import math

import pytest

from cadcore.errors import CadError
from cadcore.service.server import Session


def block() -> Session:
    session = Session(autosave=False)
    session.op_new_document()
    session.op_add_feature(type="box", args={"size": [40, 20, 10]}, feature_id="blk")
    session.op_build()
    return session


def can() -> Session:
    session = Session(autosave=False)
    session.op_new_document()
    session.op_add_feature(type="cylinder", args={"radius": 10, "height": 30}, feature_id="can")
    session.op_build()
    return session


def test_a_split_from_a_face_is_one_feature_and_its_offset_is_the_drag():
    session = block()
    out = session.op_add_split(face="blk/+z", offset=-4.0, keep="below")
    assert out["volume_mm3"] == pytest.approx(40 * 20 * 6)
    assert session.doc.features[-1].type == "split" and out["feature"] == "split1"
    dragged = session.op_edit_feature(feature_id="split1", args={"offset": -2.0})
    assert dragged["volume_mm3"] == pytest.approx(40 * 20 * 8)
    session.op_edit_feature(feature_id="blk", args={"size": [40, 20, 20]})
    assert session.op_build()["volume_mm3"] == pytest.approx(40 * 20 * 18), "it follows the face"


def test_a_draft_from_a_face_picks_the_walls_itself():
    session = block()
    out = session.op_add_draft(angle=5.0, parting_face="blk/+z", parting_offset=-5.0)
    lean = 5 * math.tan(math.radians(5))
    wedges = 2 * (0.5 * 5 * lean * 20) * 2 + 2 * (0.5 * 5 * lean * 40) * 2
    assert abs(out["volume_mm3"] - 8000) == pytest.approx(wedges, rel=0.02)   # corners overlap
    faces = session.doc.features[-1].args["faces"]
    assert set(faces) == {"blk/+x", "blk/-x", "blk/+y", "blk/-y"}
    assert session.doc.parameters["draft1_angle"] == 5.0


def test_a_draft_with_no_faces_and_no_parting_is_refused():
    session = block()
    with pytest.raises(CadError) as caught:
        session.op_add_draft(angle=5.0)
    assert caught.value.kind == "bad_arguments"


def test_embossed_words_are_a_text_sketch_and_an_emboss_the_depth_edits():
    session = block()
    before = session.op_build()["volume_mm3"]
    out = session.op_add_emboss(face="blk/+z", text="OK", depth=1.0)
    assert out["volume_mm3"] > before and out["feature"] == "emboss1"
    assert [f.type for f in session.doc.features[-2:]] == ["sketch", "emboss"]
    raised = out["volume_mm3"] - before
    deeper = session.op_edit_feature(feature_id="emboss1", args={"depth": 2.0})
    assert deeper["volume_mm3"] - before == pytest.approx(2 * raised, rel=1e-3)
    cut = session.op_edit_feature(feature_id="emboss1", args={"depth": 1.0, "cut": True})
    assert before - cut["volume_mm3"] == pytest.approx(raised, rel=1e-3)


def test_a_negative_depth_cuts_and_wrap_goes_round_a_can():
    session = can()
    before = session.op_build()["volume_mm3"]
    out = session.op_add_emboss(face="can/side", text="AB", depth=-1.0, height=6, wrap=True)
    assert out["volume_mm3"] < before
    assert session.doc.features[-1].args["cut"] and session.doc.features[-1].args["wrap"]


def test_a_coil_winds_round_the_can_and_its_pitch_is_a_parameter():
    session = can()
    out = session.op_add_coil(face="can/side", wire=2.0, pitch=5.0, turns=4)
    assert out["feature"] == "coil1"
    sweep = session.doc.features[-2]
    assert sweep.type == "sweep" and sweep.args["helix"]["radius"] == pytest.approx(10.8)
    assert session.doc.features[-1].type == "fuse"
    assert session.doc.parameters["coil1_pitch"] == 5.0
    assert out["volume_mm3"] > math.pi * 100 * 30
    assert session.op_set_parameter(name="coil1_pitch", value=8.0)["volume_mm3"] > 0


def test_a_coil_needs_a_round_face():
    session = block()
    with pytest.raises(CadError) as caught:
        session.op_add_coil(face="blk/+z")
    assert caught.value.kind == "not_a_cylinder"


def test_the_document_s_material_and_colour_are_set_and_read_back():
    session = block()
    out = session.op_set_meta(material="S45C", colour="#ff8800")
    assert out["meta"]["material"] == "S45C"
    assert session.op_describe_document()["meta"]["colour"] == "#ff8800"
    assert session.op_mass_properties()["material"] == "S45C"
    session.op_set_meta(colour="")
    assert "colour" not in session.op_describe_document()["meta"]
    with pytest.raises(CadError) as caught:
        session.op_set_meta(colour="orange")
    assert caught.value.kind == "bad_parameter"
    session.op_undo()
    assert session.op_describe_document()["meta"]["colour"] == "#ff8800"
    session.op_undo()
    assert "material" not in session.op_describe_document()["meta"]


def test_a_draft_from_a_face_with_no_offset_parts_the_block_halfway():
    """The GUIs send no offset: a parting plane in the face itself would split nothing."""
    session = block()
    out = session.op_add_draft(angle=5.0, parting_face="blk/+z")
    assert out["volume_mm3"] > 8000
    assert session.doc.features[-1].args["parting_offset"] == pytest.approx(-5.0)


def test_a_coil_in_a_bore_winds_on_the_inside():
    session = block()
    session.op_add_feature(type="cylinder", args={"radius": 8, "height": 30, "at": [0, 0, 0]},
                           feature_id="hole")
    session.op_add_feature(type="cut", args={"target": "blk", "tool": "hole"}, feature_id="bored")
    before = session.op_build()["volume_mm3"]
    out = session.op_add_coil(face="hole/side", wire=2.0, pitch=5.0, turns=1)
    sweep = session.doc.features[-2]
    assert sweep.args["helix"]["radius"] == pytest.approx(7.2), "inside the bore, into its wall"
    wire = math.pi * 1.0 ** 2 * math.sqrt((2 * math.pi * 7.2) ** 2 + 5 ** 2)   # one turn of wire
    assert out["volume_mm3"] - before > 0.5 * wire


def test_a_split_s_offset_is_a_parameter_like_the_other_drags():
    session = block()
    session.op_add_split(face="blk/+z", offset=-4.0, keep="below")
    assert session.doc.parameters["split1_offset"] == -4.0
    assert session.op_set_parameter(name="split1_offset", value=-2.0)["volume_mm3"] == pytest.approx(40 * 20 * 8)
