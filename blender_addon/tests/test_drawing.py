"""The parts of viewport drawing that can be decided without a viewport."""
import importlib.util
import math
import pathlib

import pytest

# Loaded by path: importing the add-on package would import bpy.
_path = pathlib.Path(__file__).resolve().parents[2] / "blender_addon" / "viewport" / "drawing.py"
_spec = importlib.util.spec_from_file_location("cad_drawing", _path)
drawing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drawing)

FRAME = {"origin": [10.0, 0.0, 5.0], "normal": [0.0, 0.0, 1.0], "x_axis": [1.0, 0.0, 0.0]}
TILTED = {"origin": [0.0, 0.0, 0.0], "normal": [0.0, 1.0, 0.0], "x_axis": [1.0, 0.0, 0.0]}


def test_the_cursor_lands_on_the_sketch_plane():
    hit = drawing.ray_plane((10.0, 4.0, 25.0), (0.0, 0.0, -1.0), FRAME)
    assert hit == pytest.approx((10.0, 4.0, 5.0))
    assert drawing.ray_plane((0, 0, 25), (1, 0, 0), FRAME) is None      # parallel


def test_face_coordinates_round_trip():
    for frame in (FRAME, TILTED):
        for uv in ((0, 0), (12.5, -3.25)):
            assert drawing.to_uv(drawing.to_3d(uv, frame), frame) == pytest.approx(uv)


def test_snapping_prefers_an_existing_point_then_the_axes():
    points = [(0.0, 0.0), (30.0, 0.0)]
    landed, how = drawing.snap((30.4, 0.6), points)
    assert landed == (30.0, 0.0) and how["kind"] == "point" and how["index"] == 1

    landed, how = drawing.snap((30.0, 12.0), [], previous=(30.9, 0.0))
    assert how["kind"] == "vertical" and landed == (30.9, 12.0)

    landed, how = drawing.snap((14.0, 0.4), [], previous=(0.0, 0.0))
    assert how["kind"] == "horizontal" and landed == (14.0, 0.0)

    landed, how = drawing.snap((14.0, 9.0), [], previous=(0.0, 0.0))
    assert how["kind"] == "free" and landed == (14.0, 9.0)


def test_a_loop_closes_only_on_the_first_point():
    points = [(0.0, 0.0), (30.0, 0.0), (30.0, 14.0)]
    assert drawing.closes_loop((0.5, 0.4), points)
    assert not drawing.closes_loop((30.0, 14.0), points)
    assert not drawing.closes_loop((0.0, 0.0), points[:2])      # two points is not a loop


def test_polyline_and_circle_helpers():
    assert drawing.polyline([(0, 0), (1, 0), (1, 1)], closed=True) == [[0, 1], [1, 2], [2, 0]]
    ring = drawing.circle_points((2.0, 0.0), 3.0)
    assert ring[0] == pytest.approx((5.0, 0.0))
    assert all(math.dist(p, (2.0, 0.0)) == pytest.approx(3.0) for p in ring)



def test_the_pen_lands_on_what_the_part_already_has():
    """A point already drawn wins, then a corner or hole centre of the face,
    then alignment with the last point. Drawing on something means reaching
    for what is there, and the pen used to see only its own marks."""
    at, hint = drawing.snap((10.1, 10.1), [], None, 1.0, 1.0, anchors=[(10.0, 10.0)])
    assert hint["kind"] == "on_part" and at == (10.0, 10.0)
    # one already drawn is nearer to hand than one on the part
    at, hint = drawing.snap((0.1, 0.1), [(0.0, 0.0)], None, 1.0, 1.0,
                            anchors=[(0.05, 0.05)])
    assert hint["kind"] == "point"
    # and a miss is still a miss
    assert drawing.snap((50.0, 50.0), [], None, 1.0, 1.0, anchors=[(10.0, 10.0)])[1] == \
        {"kind": "free"}


def test_the_depth_follows_the_drag_up_and_never_reaches_zero():
    # nothing moved: the default depth stands
    assert drawing.depth_from_drag(4.0, 0.0, 0.1) == pytest.approx(4.0)
    # 100 px up at 0.1 mm/px is 10 mm deeper, snapped to the half millimetre
    assert drawing.depth_from_drag(4.0, 103.0, 0.1) == pytest.approx(14.5)
    # dragging down past nothing leaves the least depth that still builds
    assert drawing.depth_from_drag(4.0, -400.0, 0.1) == pytest.approx(0.01)
    assert drawing.depth_from_drag(4.0, 7.0, 0.1, step=0.0) == pytest.approx(4.7)


def test_a_rectangle_is_four_corners_from_two():
    corners = drawing.rectangle_points((0.0, 0.0), (30.0, 20.0))
    assert corners == [(0.0, 0.0), (30.0, 0.0), (30.0, 20.0), (0.0, 20.0)]
    # a loop through them closes into four sides
    assert drawing.polyline(corners, closed=True) == [[0, 1], [1, 2], [2, 3], [3, 0]]
    # opposite corners in any diagonal still give a real rectangle
    back = drawing.rectangle_points((30.0, 20.0), (0.0, 0.0))
    assert back == [(30.0, 20.0), (0.0, 20.0), (0.0, 0.0), (30.0, 0.0)]
