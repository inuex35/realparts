"""A sketch pressed onto a face: raised, cut, projected or wrapped; and projection read back."""
import math

import pytest

from cadcore.errors import CadError
from cadcore.service.server import Session


def cylinder() -> Session:
    session = Session(autosave=False)
    session.op_new_document()
    session.op_add_feature(type="cylinder", args={"radius": 10, "height": 30}, feature_id="can")
    session.op_build()
    return session


SQUARE = {"points": {"a": [-4, 0], "b": [4, 0], "c": [4, 8], "d": [-4, 8]},
          "lines": {"s": ["a", "b"], "e": ["b", "c"], "n": ["c", "d"], "w": ["d", "a"]},
          "allow_underconstrained": True,
          "plane": {"origin": [0, -20, 0], "normal": [0, -1, 0], "x_axis": [1, 0, 0]}}


def slab_volume(r0: float, r1: float, half_width: float, height: float) -> float:
    """The ring between two radii, cut to a strip |x| < half_width, times the height."""
    n = 4000
    return sum((math.sqrt(r1 * r1 - x * x) - math.sqrt(r0 * r0 - x * x)) * 2 * half_width / n
               for x in [-half_width + 2 * half_width * (k + 0.5) / n for k in range(n)]) * height


def test_a_projected_emboss_raises_the_sketch_off_a_curved_face():
    session = cylinder()
    before = session.op_build()["volume_mm3"]
    session.op_add_feature(type="sketch", args=SQUARE, feature_id="sq")
    out = session.op_add_feature(type="emboss", args={"body": "can", "face": "can/side",
                                                       "sketch": "sq", "depth": 1.5}, feature_id="mark")
    assert out["volume_mm3"] - before == pytest.approx(slab_volume(10, 11.5, 4, 8), rel=1e-3)
    assert any(n.startswith("mark/") for n in out["face_names"])


def test_a_cut_emboss_takes_the_slab_away():
    session = cylinder()
    before = session.op_build()["volume_mm3"]
    session.op_add_feature(type="sketch", args=SQUARE, feature_id="sq")
    out = session.op_add_feature(type="emboss", args={"body": "can", "face": "can/side",
                                                       "sketch": "sq", "depth": 1.5, "cut": True})
    assert before - out["volume_mm3"] == pytest.approx(slab_volume(8.5, 10, 4, 8), rel=1e-3)


def test_a_wrapped_emboss_goes_round_the_cylinder():
    session = cylinder()
    before = session.op_build()["volume_mm3"]
    half = math.pi * 10 * 0.75             # three quarters of the way round, as u
    band = {"points": {"a": [-half, 10], "b": [half, 10], "c": [half, 14], "d": [-half, 14]},
            "lines": {"s": ["a", "b"], "e": ["b", "c"], "n": ["c", "d"], "w": ["d", "a"]},
            "allow_underconstrained": True,
            "plane": {"origin": [0, -20, 0], "normal": [0, -1, 0], "x_axis": [1, 0, 0]}}
    session.op_add_feature(type="sketch", args=band, feature_id="band")
    out = session.op_add_feature(type="emboss", args={"body": "can", "face": "can/side",
                                                       "sketch": "band", "depth": 1, "wrap": True})
    # a band 4 high, 1 thick, three quarters of the way round at mean radius 10.5
    expected = 0.75 * 2 * math.pi * 10.5 * 4 * 1
    assert out["volume_mm3"] - before == pytest.approx(expected, rel=0.02)


def test_wrapping_on_a_flat_face_is_refused_by_kind():
    session = cylinder()
    session.op_add_feature(type="sketch", args=SQUARE, feature_id="sq")
    with pytest.raises(CadError) as caught:
        session.op_add_feature(type="emboss", args={"body": "can", "face": "can/+z",
                                                     "sketch": "sq", "depth": 1, "wrap": True})
    assert caught.value.kind == "not_a_cylinder"


def test_a_sketch_that_misses_the_face_is_refused():
    session = cylinder()
    away = dict(SQUARE, points={"a": [40, 0], "b": [48, 0], "c": [48, 8], "d": [40, 8]})
    session.op_add_feature(type="sketch", args=away, feature_id="sq")
    with pytest.raises(CadError) as caught:
        session.op_add_feature(type="emboss", args={"body": "can", "face": "can/side",
                                                     "sketch": "sq", "depth": 1})
    assert caught.value.kind in ("empty_result", "boolean_failed")


def test_a_projection_is_read_back_as_curves_on_the_body():
    session = cylinder()
    session.op_add_feature(type="sketch", args=SQUARE, feature_id="sq")
    out = session.op_project(sketch="sq")
    assert out["curves"] and out["length_mm"] > 8 * 4          # curved round the can
    assert all(abs(math.hypot(p[0], p[1]) - 10) < 1e-3
               for c in out["curves"] for p in c["points"])
    assert all(-1e-6 <= p[2] <= 8 + 1e-6 for c in out["curves"] for p in c["points"])


def test_a_curved_edge_projects_into_a_sketch_as_a_spline():
    session = cylinder()
    edge = next(e for e in session.op_select_edges(query={"of_face": "can/+z"})["edges"])
    session.op_add_feature(type="sketch", args={
        "on": {"body": "can", "face": "can/+z"},
        "projections": {"rim": {"edge": edge}}}, feature_id="top")
    session.evaluator.prepare("top")
    sketch = session.evaluator.sketches["top"]
    assert sketch.loops and sketch.loops[0][0].kind in ("spline", "circle")
