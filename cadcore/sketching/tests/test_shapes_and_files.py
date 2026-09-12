"""Ellipses in the solver, and DXF / SVG read into the sketch language."""
import math
import pathlib

import pytest

from cadcore.errors import CadError
from cadcore.sketching import solve
from cadcore.sketching.files import read_dxf, read_svg, read_file

DATA = pathlib.Path(__file__).resolve().parent / "data"


def test_an_ellipse_solves_with_its_centre_and_keeps_its_sizes():
    out = solve({"points": {"o": [3, 4]},
                 "ellipses": {"e": {"centre": "o", "major": 10, "minor": 4, "angle": 30}},
                 "constraints": [{"type": "fix", "point": "o", "at": [3, 4]}]}, lambda v: v)
    seg = out.loops[0][0]
    assert seg.kind == "ellipse" and seg.centre == (3.0, 4.0)
    assert seg.radius == pytest.approx(10) and seg.minor == pytest.approx(4)
    assert math.degrees(seg.angle) == pytest.approx(30)
    far = max(math.dist(p, seg.centre) for p in seg.sample(64))
    assert far == pytest.approx(10, abs=1e-6)


def test_an_ellipse_wider_than_it_is_long_is_refused():
    with pytest.raises(CadError) as caught:
        solve({"points": {"o": [0, 0]},
               "ellipses": {"e": {"centre": "o", "major": 4, "minor": 10}}}, lambda v: v)
    assert caught.value.kind == "bad_parameter"


def test_a_dxf_comes_in_pinned_where_it_was_drawn():
    spec = read_dxf(str(DATA / "plate.dxf"))
    assert len(spec["lines"]) == 4 and len(spec["circles"]) == 1
    assert all(c["type"] == "fix" for c in spec["constraints"])
    out = solve(spec, lambda v: v)
    assert out.dof == 0 and len(out.loops) == 2


def test_a_dxf_can_be_scaled_on_the_way_in():
    spec = read_dxf(str(DATA / "plate.dxf"), scale=0.5)
    assert spec["circles"]["fc2"]["radius"] == 1.5 if "fc2" in spec["circles"] else \
        list(spec["circles"].values())[0]["radius"] == 1.5


def test_an_svg_path_with_an_arc_becomes_lines_and_an_arc():
    spec = read_svg(str(DATA / "shape.svg"))
    assert len(spec["arcs"]) == 1 and len(spec["circles"]) == 1 and len(spec["lines"]) == 3
    out = solve(spec, lambda v: v)
    assert len(out.loops) == 2
    assert all(p[1] <= 0 for p in out.points.values()), "svg's y runs down; the sketch's runs up"


def test_a_bulged_polyline_is_arcs():
    from cadcore.sketching.files import _Drawing, _bulged
    drawing = _Drawing("f", 1.0)
    _bulged(drawing, [(0, 0), (10, 0)], [1.0], False)          # a half circle bowing left
    arc = list(drawing.arcs.values())[0]
    assert arc["radius"] == pytest.approx(5) and arc["ccw"]


def test_a_file_of_the_wrong_kind_is_refused():
    with pytest.raises(CadError) as caught:
        read_file(str(DATA / "plate.txt"))
    assert caught.value.kind == "unknown_format"
