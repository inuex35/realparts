"""Surfaces through points and between boundary curves; a draft away from a parting plane."""
import math

import pytest

from cadcore.errors import CadError
from cadcore.model.document import Document
from cadcore.evaluation.graph import Evaluator
from cadcore.service.server import Session
from ..core.measure import area, is_solid, volume


def build(features, result):
    return Evaluator(Document.from_dict({"features": features, "result": result})).build(result)


def test_a_surface_through_a_grid_of_points_is_smooth_and_thickens():
    grid = [[[x, y, 3 * math.sin(x / 10)] for x in (0, 10, 20, 30, 40)] for y in (0, 10, 20, 30)]
    body = build([{"id": "s", "type": "surface", "method": "points", "grid": grid}], "s")
    assert not is_solid(body) and 1200 < area(body) < 1300
    assert [n for n, _ in body.names] == ["s/patch"]
    thick = build([{"id": "s", "type": "surface", "method": "points", "grid": grid},
                   {"id": "t", "type": "thicken", "body": "s", "thickness": 2}], "t")
    assert is_solid(thick) and volume(thick) == pytest.approx(area(body) * 2, rel=0.02)


def test_a_grid_with_ragged_rows_is_refused():
    with pytest.raises(CadError) as caught:
        build([{"id": "s", "type": "surface", "method": "points",
                "grid": [[[0, 0, 0], [10, 0, 0]], [[0, 10, 0]]]}], "s")
    assert caught.value.kind == "bad_arguments"


def curve(name, a, b, bulge):
    """An open sketch: a spline from a to b bowing up by bulge, in world coordinates."""
    mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, bulge]
    return {"id": name, "type": "sketch", "open": True, "allow_underconstrained": True,
            "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
            "points": {"p": a[:2], "m": mid[:2], "q": b[:2]},
            "lines": {"seg": ["p", "q"]}} if bulge == 0 else \
           {"id": name, "type": "sketch", "open": True, "allow_underconstrained": True,
            "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
            "points": {"p": a[:2], "m": mid[:2], "q": b[:2]},
            "splines": {"seg": {"through": ["p", "m", "q"]}}}


def test_a_patch_between_four_curves_spans_them():
    sketches = [curve("c1", [0, 0, 0], [20, 0, 0], 0), curve("c2", [20, 0, 0], [20, 20, 0], 0),
                curve("c3", [20, 20, 0], [0, 20, 0], 0), curve("c4", [0, 20, 0], [0, 0, 0], 0)]
    body = build(sketches + [{"id": "s", "type": "surface", "method": "boundary",
                              "sketches": ["c1", "c2", "c3", "c4"]}], "s")
    assert area(body) == pytest.approx(400, rel=1e-3)


@pytest.mark.parametrize("style", ["coons", "stretch"])
def test_a_patch_between_two_curves_is_ruled_between_them(style):
    sketches = [curve("c1", [0, 0, 0], [20, 0, 0], 0), curve("c2", [0, 30, 0], [20, 30, 0], 0)]
    body = build(sketches + [{"id": "s", "type": "surface", "method": "boundary",
                              "sketches": ["c1", "c2"], "style": style}], "s")
    assert area(body) == pytest.approx(600, rel=1e-3)


def test_too_many_curves_are_refused():
    sketches = [curve(f"c{k}", [0, 0, 0], [20, 0, 0], 0) for k in range(5)]
    with pytest.raises(CadError) as caught:
        build(sketches + [{"id": "s", "type": "surface", "method": "boundary",
                           "sketches": [f"c{k}" for k in range(5)]}], "s")
    assert caught.value.kind == "bad_arguments"


def test_a_draft_away_from_a_parting_plane_tapers_both_halves():
    session = Session(autosave=False)
    session.op_new_document()
    session.op_add_feature(type="box", args={"size": [40, 20, 10]}, feature_id="blk")
    session.op_build()
    session.op_add_feature(type="plane", args={"origin": [0, 0, 0], "normal": [0, 0, 1]},
                           feature_id="part")
    out = session.op_add_feature(type="draft", args={"body": "blk", "faces": ["blk/+x", "blk/-x"],
                                                      "angle": 5, "parting": "part"}, feature_id="d")
    lean = 5 * math.tan(math.radians(5))
    wedge = 0.5 * 5 * lean * 20                    # one face, one half
    assert abs(out["volume_mm3"] - 8000) == pytest.approx(4 * wedge, rel=1e-3)
    top = session.op_section(normal=[0, 0, 1], origin=[0, 0, 4.99])
    ends = session.op_section(normal=[0, 0, 1], origin=[0, 0, 4.5])
    widest = max(abs(p[0]) for c in top["curves"] for p in c["points"])
    narrower = max(abs(p[0]) for c in ends["curves"] for p in c["points"])
    assert widest - narrower == pytest.approx((4.99 - 4.5) * math.tan(math.radians(5)), abs=0.005)
    names = set(out["face_names"])
    assert "blk/+x@0" in names and "blk/+x@1" in names, "the face the plane crosses is two pieces"
