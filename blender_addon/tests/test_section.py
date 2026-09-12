"""The section view's plane, decided without a viewport."""
import importlib.util
import pathlib

import pytest

# Loaded by path: importing the add-on package would import bpy.
_path = pathlib.Path(__file__).resolve().parents[2] / "blender_addon" / "viewport" / "clipping.py"
_spec = importlib.util.spec_from_file_location("cad_clipping", _path)
clipping = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(clipping)

#: a face at x = 10 looking down +X, as `face_frame` gives it
FACE = {"origin": [10.0, 0.0, 0.0], "normal": [1.0, 0.0, 0.0]}


def test_the_face_looks_at_the_side_that_goes_away():
    cut = clipping.plane(FACE)
    assert clipping.keeps(cut, (9.0, 0.0, 0.0))          # behind the face: stays
    assert not clipping.keeps(cut, (11.0, 0.0, 0.0))     # in front of it: gone
    assert clipping.keeps(cut, (10.0, 5.0, -5.0))        # on the face: stays


def test_the_offset_slides_the_cut_along_the_normal():
    cut = clipping.plane(FACE, offset=-4.0)
    assert clipping.keeps(cut, (5.9, 0.0, 0.0))
    assert not clipping.keeps(cut, (6.1, 0.0, 0.0))


def test_flip_keeps_the_other_side():
    cut = clipping.plane(FACE, flip=True)
    assert not clipping.keeps(cut, (9.0, 0.0, 0.0))
    assert clipping.keeps(cut, (11.0, 0.0, 0.0))


def test_a_slanted_face_cuts_square_to_itself():
    frame = {"origin": [0.0, 0.0, 0.0], "normal": [3.0, 4.0, 0.0]}   # not unit length
    cut = clipping.plane(frame, offset=5.0)
    assert cut[:3] == pytest.approx((0.6, 0.8, 0.0))
    # 5 mm along the normal from the origin is exactly on the cut
    assert clipping.keeps(cut, (3.0, 4.0, 0.0))
    assert not clipping.keeps(cut, (3.6, 4.8, 0.0))


def test_a_normal_of_no_length_does_not_divide_by_zero():
    cut = clipping.plane({"origin": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 0.0]})
    assert cut[:3] == pytest.approx((0.0, 0.0, 1.0))
