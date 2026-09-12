"""Sketch input is refused by kind, named by the rules of a face name, and read
from a drawing file the way the file meant it."""
import os

import pytest

from cadcore.errors import CadError
from cadcore.sketching import solve
from cadcore.sketching.files import read_dxf, read_svg

FIX = lambda points: [{"type": "fix", "point": p, "at": at} for p, at in points.items()]   # noqa: E731


def test_the_outline_is_the_loop_with_the_biggest_real_area():
    """A ring of two arcs round a hole: the polygon of arc ends is smaller than the hole."""
    points = {"o": [0, 0], "a": [-10, 0], "b": [10, 0], "h": [0, 0]}
    spec = {"points": points,
            "arcs": {"top": {"centre": "o", "from": "a", "to": "b", "ccw": False},
                     "bottom": {"centre": "o", "from": "b", "to": "a", "ccw": False}},
            "circles": {"hole": {"centre": "h", "radius": 8.5}},
            "constraints": FIX(points), "allow_underconstrained": True}
    out = solve(spec, lambda v: v)
    assert len(out.loops) == 2
    assert {seg.kind for seg in out.loops[0]} == {"arc"}, "the outline is the ring, not its hole"


@pytest.mark.parametrize("name", ["top#2", "top:x", "n|m", "a/b", "a b"])
def test_a_segment_name_is_read_like_a_face_name(name):
    points = {"a": [0, 0], "b": [10, 0], "c": [10, 5]}
    with pytest.raises(CadError) as caught:
        solve({"points": points, "lines": {name: ["a", "b"], "e": ["b", "c"], "w": ["c", "a"]},
               "constraints": FIX(points)}, lambda v: v)
    assert caught.value.kind == "bad_arguments"


def test_a_sketch_of_the_wrong_shape_is_refused_by_kind():
    with pytest.raises(CadError) as caught:
        solve({"points": None, "lines": {}}, lambda v: v)
    assert caught.value.kind in ("bad_arguments", "empty_sketch")
    points = {"a": [0, 0], "b": [10, 0], "c": [10, 5]}
    with pytest.raises(CadError) as caught:
        solve({"points": points, "lines": {"s": ["a", "b"], "e": ["b", "c"], "w": ["c", "a"]},
               "constraints": ["fix"]}, lambda v: v)
    assert caught.value.kind == "bad_constraint"


def _dxf(entities: str) -> str:
    return "0\nSECTION\n2\nENTITIES\n" + entities + "0\nENDSEC\n0\nEOF\n"


def test_a_polyline_with_one_bowed_corner_keeps_its_bulge(tmp_path):
    text = _dxf("0\nLWPOLYLINE\n70\n1\n10\n0\n20\n0\n10\n20\n20\n0\n10\n20\n20\n10\n42\n0.4142\n10\n0\n20\n10\n")
    path = tmp_path / "corner.dxf"
    path.write_text(text, encoding="utf-8")
    spec = read_dxf(str(path))
    assert len(spec["arcs"]) == 1 and len(spec["lines"]) == 3


def test_an_old_polyline_s_placeholder_point_is_not_a_vertex(tmp_path):
    text = _dxf("0\nPOLYLINE\n70\n1\n10\n0\n20\n0\n"
                "0\nVERTEX\n10\n0\n20\n0\n0\nVERTEX\n10\n20\n20\n0\n0\nVERTEX\n10\n20\n20\n10\n"
                "0\nVERTEX\n10\n0\n20\n10\n0\nSEQEND\n")
    path = tmp_path / "old.dxf"
    path.write_text(text, encoding="utf-8")
    spec = read_dxf(str(path))
    assert len(spec["points"]) == 4 and len(spec["lines"]) == 4


def test_a_line_missing_a_coordinate_reads_as_zero_not_a_crash(tmp_path):
    path = tmp_path / "half.dxf"
    path.write_text(_dxf("0\nLINE\n10\n0\n20\n0\n11\n10\n"), encoding="utf-8")
    assert len(read_dxf(str(path))["lines"]) == 1


def test_an_svg_transform_and_viewbox_place_the_drawing(tmp_path):
    path = tmp_path / "moved.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm" viewBox="0 0 100 50">'
                    '<g transform="translate(30,10)"><rect x="0" y="0" width="10" height="5"/></g>'
                    '<g transform="scale(2) rotate(90)"><circle cx="5" cy="0" r="2"/></g></svg>',
                    encoding="utf-8")
    spec = read_svg(str(path))
    xs = sorted({p[0] for p in spec["points"].values()})
    assert xs[0] == pytest.approx(0.0) and 60.0 in [pytest.approx(x) for x in xs] and 80.0 in [pytest.approx(x) for x in xs]
    circle = list(spec["circles"].values())[0]
    assert circle["radius"] == pytest.approx(8.0)                    # 2 * 2 (scale) * 2 (viewBox)
    assert spec["points"][circle["centre"]] == pytest.approx([0.0, -20.0])


def test_svg_numbers_with_signs_exponents_and_packed_arc_flags_parse(tmp_path):
    path = tmp_path / "terse.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0 L 1e+2 0 L100-10 A 5 5 0 01 30 10 Z"/>'
                    '<rect x="1px" y="2mm" width="1in" height="4"/></svg>', encoding="utf-8")
    spec = read_svg(str(path))
    assert len(spec["arcs"]) == 1 and any(abs(p[0] - 100) < 1e-9 for p in spec["points"].values())
    assert any(abs(p[0] - (1 + 25.4)) < 1e-9 for p in spec["points"].values())


def test_a_cut_short_path_and_an_unreadable_file_are_refused_by_kind(tmp_path):
    path = tmp_path / "short.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0 L 10"/></svg>', encoding="utf-8")
    with pytest.raises(CadError) as caught:
        read_svg(str(path))
    assert caught.value.kind == "bad_path"
    with pytest.raises(CadError) as caught:
        read_dxf(str(tmp_path))
    assert caught.value.kind == "bad_path"
    with pytest.raises(CadError) as caught:
        read_svg(str(tmp_path))
    assert caught.value.kind == "bad_path"


def test_a_midpoint_holds_the_middle_of_one_line_on_another():
    points = {"a": [0, 0], "b": [10, 0], "c": [16, 4]}
    spec = {"points": points, "lines": {"base": ["a", "b"], "post": ["b", "c"]},
            "constraints": [{"type": "fix", "point": "a", "at": [0, 0]},
                            {"type": "fix", "point": "b", "at": [10, 0]},
                            {"type": "midpoint", "of": "post", "line": "base"}],
            "open": True, "allow_underconstrained": True}
    out = solve(spec, lambda v: v)
    assert out.points["c"][1] == pytest.approx(0.0, abs=1e-6), "post's middle sits on base's line"
