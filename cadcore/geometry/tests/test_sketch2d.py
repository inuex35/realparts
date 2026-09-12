"""Text outlines, outline offsets, ellipses, several outlines in one sketch, and a helix."""
import math
import pathlib

import pytest

from cadcore.model.document import Document
from cadcore.evaluation.graph import Evaluator
from cadcore.errors import CadError
from cadcore.service.server import Session
from ..core.measure import solid_count, volume

DATA = pathlib.Path(__file__).resolve().parents[2] / "sketching" / "tests" / "data"

RECT = {"points": {"a": [0, 0], "b": [20, 0], "c": [20, 10], "d": [0, 10]},
        "lines": {"s": ["a", "b"], "e": ["b", "c"], "n": ["c", "d"], "w": ["d", "a"]},
        "allow_underconstrained": True}


def build(features, result, source=None):
    doc = Document.from_dict({"features": features, "result": result})
    if source:
        doc.source = source
    return Evaluator(doc).build(result)


def test_an_ellipse_extrudes_to_its_area_and_keeps_its_name():
    body = build([{"id": "e", "type": "sketch", "points": {"o": [0, 0]},
                   "ellipses": {"el": {"centre": "o", "major": 10, "minor": 5}},
                   "constraints": [{"type": "fix", "point": "o", "at": [0, 0]}]},
                  {"id": "p", "type": "extrude", "sketch": "e", "distance": 2}], "p")
    assert volume(body) == pytest.approx(math.pi * 10 * 5 * 2, rel=1e-6)
    assert "p/el" in {n for n, _ in body.names}


def test_text_becomes_one_solid_per_letter_with_its_holes():
    body = build([{"id": "t", "type": "sketch", "texts": {"w": {"text": "AB", "height": 10}}},
                  {"id": "p", "type": "extrude", "sketch": "t", "distance": 1}], "p")
    assert solid_count(body.shape) == 2
    assert 0 < volume(body) < 10 * 10 * 2 * 1
    assert len({n for n, _ in body.names}) == len(body.names), "every face has its own name"


def test_text_can_be_cut_into_a_face_as_a_pocket():
    session = Session(autosave=False)
    session.op_new_document()
    session.op_add_feature(type="box", args={"size": [60, 20, 5]}, feature_id="blk")
    session.op_build()
    before = session.op_build()["volume_mm3"]
    out = session.op_add_profile(shape="text", face="blk/+z", text="OK", height=8, at=[-10, -3])
    session.op_add_feature(type="pocket", args={"body": "blk", "sketch": out["feature"], "depth": 1})
    assert session.op_build()["volume_mm3"] < before


def test_an_outline_offset_grows_and_rounds_the_corners():
    body = build([dict({"id": "s", "type": "sketch", "outline_offset": {"distance": 2}}, **RECT),
                  {"id": "p", "type": "extrude", "sketch": "s", "distance": 1}], "p")
    assert volume(body) == pytest.approx(24 * 14 - (4 - math.pi) * 4, rel=1e-6)


def test_an_inward_offset_with_sharp_corners_shrinks():
    body = build([dict({"id": "s", "type": "sketch",
                        "outline_offset": {"distance": -2, "join": "intersection"}}, **RECT),
                  {"id": "p", "type": "extrude", "sketch": "s", "distance": 1}], "p")
    assert volume(body) == pytest.approx(16 * 6, rel=1e-6)


def test_keeping_the_drawn_loop_beside_the_offset_makes_a_ring():
    body = build([dict({"id": "s", "type": "sketch",
                        "outline_offset": {"distance": 2, "keep": True}}, **RECT),
                  {"id": "p", "type": "extrude", "sketch": "s", "distance": 1}], "p")
    assert volume(body) == pytest.approx(24 * 14 - (4 - math.pi) * 4 - 200, rel=1e-6)


def test_two_separate_outlines_make_two_solids():
    body = build([{"id": "s", "type": "sketch",
                   "points": {"a": [0, 0], "b": [10, 0], "c": [10, 10], "d": [0, 10],
                              "e": [20, 0], "f": [30, 0], "g": [30, 10], "h": [20, 10]},
                   "lines": {"l1": ["a", "b"], "l2": ["b", "c"], "l3": ["c", "d"], "l4": ["d", "a"],
                             "m1": ["e", "f"], "m2": ["f", "g"], "m3": ["g", "h"], "m4": ["h", "e"]},
                   "allow_underconstrained": True},
                  {"id": "p", "type": "extrude", "sketch": "s", "distance": 2}], "p")
    assert solid_count(body.shape) == 2 and volume(body) == pytest.approx(400)


def test_a_dxf_and_an_svg_extrude_to_what_they_draw(tmp_path):
    for name, area in (("plate.dxf", 40 * 20 - math.pi * 9),
                       ("shape.svg", 30 * 10 + math.pi * 25 / 2 - math.pi * 4)):
        body = build([{"id": "s", "type": "sketch", "file": {"path": str(DATA / name)}},
                      {"id": "p", "type": "extrude", "sketch": "s", "distance": 1}], "p",
                     str(tmp_path / "doc.json"))
        assert volume(body) == pytest.approx(area, rel=1e-6), name


def test_a_helix_sweep_makes_a_spring():
    profile = {"id": "c", "type": "sketch", "points": {"o": [0, 0]},
               "circles": {"r": {"centre": "o", "radius": 1}},
               "constraints": [{"type": "fix", "point": "o", "at": [0, 0]}],
               "plane": {"origin": [10, 0, 0], "normal": [0, 1, 0], "x_axis": [1, 0, 0]}}
    body = build([profile, {"id": "spring", "type": "sweep", "profile": "c",
                            "helix": {"radius": 10, "pitch": 5, "height": 20}}], "spring")
    length = 4 * math.hypot(2 * math.pi * 10, 5)
    # the profile plane holds the axis, so the tube's cross-section is the circle
    # foreshortened by the pitch angle
    tilt = math.atan2(5, 2 * math.pi * 10)
    assert volume(body) == pytest.approx(math.pi * length * math.cos(tilt), rel=1e-3)
    assert "spring/r" in {n for n, _ in body.names}


def test_a_sweep_with_neither_path_nor_helix_is_refused():
    with pytest.raises(CadError) as caught:
        build([{"id": "c", "type": "sketch", "points": {"o": [0, 0]},
                "circles": {"r": {"centre": "o", "radius": 1}},
                "constraints": [{"type": "fix", "point": "o", "at": [0, 0]}]},
               {"id": "s", "type": "sweep", "profile": "c"}], "s")
    assert caught.value.kind == "bad_arguments"
