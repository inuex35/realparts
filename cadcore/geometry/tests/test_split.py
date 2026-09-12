"""Splitting a body, reading its section, and the boolean options."""
import pytest

from cadcore.errors import CadError
from cadcore.service.server import Session
from ..core.measure import volume


def block() -> Session:
    session = Session(autosave=False)
    session.op_new_document()
    session.op_add_feature(type="box", args={"size": [40, 20, 10]}, feature_id="blk")
    session.op_build()
    return session


def test_a_split_keeps_the_side_that_was_asked_for():
    session = block()
    out = session.op_add_split(face="blk/+z", offset=-4.0, keep="below")
    assert out["volume_mm3"] == pytest.approx(40 * 20 * 6)
    names = set(session.op_build()["face_names"])
    assert "split1/+z" in names, "the cut face is named for the split"
    assert "blk/+z" not in names and "blk/-z" in names


def test_the_other_side_and_both_sides():
    session = block()
    session.op_add_split(normal=[1, 0, 0], origin=[10, 0, 0], keep="above")
    assert session.op_build()["volume_mm3"] == pytest.approx(10 * 20 * 10)   # the box is centred
    session.op_undo()
    both = session.op_add_split(normal=[1, 0, 0], origin=[10, 0, 0], keep="both")
    assert both["volume_mm3"] == pytest.approx(40 * 20 * 10)
    from ..core.measure import solid_count
    assert solid_count(session.body.shape) == 2


def test_a_plane_that_misses_is_refused_by_kind():
    session = block()
    with pytest.raises(CadError) as caught:
        session.op_add_split(normal=[0, 0, 1], origin=[0, 0, 50])
    assert caught.value.kind == "split_failed"


def test_a_split_follows_the_face_it_was_placed_off():
    session = block()
    session.op_add_split(face="blk/+z", offset=-4.0, keep="below")
    session.op_set_parameter(name="blk_h", value=20) if "blk_h" in session.doc.parameters else None
    session.op_edit_feature(feature_id="blk", args={"size": [40, 20, 20]})
    assert session.op_build()["volume_mm3"] == pytest.approx(40 * 20 * 16)


def test_a_section_reads_the_curves_and_the_area():
    session = block()
    out = session.op_section(face="blk/+z", offset=-5.0)
    assert out["area_mm2"] == pytest.approx(40 * 20)
    assert out["length_mm"] == pytest.approx(2 * (40 + 20))
    assert {c["kind"] for c in out["curves"]} == {"line"} and len(out["curves"]) == 4
    session.op_add_hole(face="blk/+z", diameter=6.0, at=[0, 0])
    with_hole = session.op_section(normal=[0, 0, 1], origin=[0, 0, 5])
    assert any(c["kind"] == "circle" for c in with_hole["curves"])
    assert with_hole["area_mm2"] < out["area_mm2"]


def test_a_section_off_the_body_is_refused():
    session = block()
    with pytest.raises(CadError) as caught:
        session.op_section(normal=[0, 0, 1], origin=[0, 0, 99])
    assert caught.value.kind == "empty_section"


def test_glue_fuses_touching_blocks():
    session = block()
    session.op_add_feature(type="box", args={"size": [40, 20, 10], "at": [0, 0, 10]},
                           feature_id="top")
    out = session.op_add_feature(type="fuse", args={"target": "blk", "tool": "top", "glue": True},
                                 feature_id="stack")
    assert out["volume_mm3"] == pytest.approx(2 * 40 * 20 * 10)


def test_fuzzy_closes_a_hair_gap():
    session = block()
    session.op_add_feature(type="box", args={"size": [40, 20, 10], "at": [0, 0, 10.0005]},
                           feature_id="top")
    out = session.op_add_feature(type="fuse", args={"target": "blk", "tool": "top", "fuzzy": 0.01},
                                 feature_id="stack")
    assert out["kind"] == "solid"
