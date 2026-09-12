"""Belt, slot and cam mates, and the limits a hinge or a slider may be given."""
import json
import math

import numpy as np
import pytest

from cadcore.errors import CadError
from cadcore.model.document import Document
from cadcore.evaluation.graph import Evaluator
from ..assembly import placement


def part_file(tmp_path, name: str, features: list, result: str) -> None:
    (tmp_path / f"{name}.json").write_text(json.dumps({"features": features, "result": result}),
                                           encoding="utf-8")


@pytest.fixture()
def shop(tmp_path):
    """A frame with two bores 50 apart, two pulleys, a pin, a plate and a follower."""
    part_file(tmp_path, "frame", [
        {"id": "plate", "type": "box", "size": [100, 40, 10]},
        {"id": "b1", "type": "cylinder", "radius": 5, "height": 20, "at": [-25, 0, 0]},
        {"id": "c1", "type": "cut", "target": "plate", "tool": "b1"},
        {"id": "b2", "type": "cylinder", "radius": 5, "height": 20, "at": [25, 0, 0]},
        {"id": "c2", "type": "cut", "target": "c1", "tool": "b2"}], "c2")
    part_file(tmp_path, "small", [{"id": "hub", "type": "cylinder", "radius": 5, "height": 10},
                                  {"id": "rim", "type": "cylinder", "radius": 10, "height": 4, "at": [0, 0, 7]},
                                  {"id": "wheel", "type": "fuse", "target": "hub", "tool": "rim"}], "wheel")
    part_file(tmp_path, "big", [{"id": "hub", "type": "cylinder", "radius": 5, "height": 10},
                                {"id": "rim", "type": "cylinder", "radius": 20, "height": 4, "at": [0, 0, 7]},
                                {"id": "wheel", "type": "fuse", "target": "hub", "tool": "rim"}], "wheel")
    part_file(tmp_path, "pin", [{"id": "pin", "type": "cylinder", "radius": 3, "height": 30}], "pin")
    return tmp_path


def assembly(tmp_path, parts: list, mates: list, drive: list | None = None):
    features = [{"id": name, "type": "part", "document": f"{document}.json"}
                for name, document in parts]
    asm = {"id": "asm", "type": "assemble", "bodies": [n for n, _ in parts], "mates": mates}
    if drive:
        asm["drive"] = drive
    doc = Document.from_dict({"features": features + [asm], "result": "asm"})
    doc.source = str(tmp_path / "asm.json")
    return Evaluator(doc).build("asm").notes["assembly"]


def turned_about_z(pose) -> float:
    r = np.asarray(pose[0])
    return math.degrees(math.atan2(r[1, 0], r[0, 0]))


def test_a_belt_turns_both_wheels_the_same_way_in_the_ratio_of_their_rims(shop):
    mates = [{"kind": "hinge", "faces": ["a:hub/side", "frame:b1/side"], "offset": 0},
             {"kind": "hinge", "faces": ["b:hub/side", "frame:b2/side"], "offset": 0},
             {"kind": "belt", "faces": ["a:rim/side", "b:rim/side"]}]
    rest = assembly(shop, [("frame", "frame"), ("a", "small"), ("b", "big")], mates)
    assert rest["solved"] and rest["dof"] == 1
    driven = assembly(shop, [("frame", "frame"), ("a", "small"), ("b", "big")], mates,
                      drive=[{"part": "a", "turn": 90}])
    a, b = turned_about_z(driven["poses"]["a"]), turned_about_z(driven["poses"]["b"])
    assert abs(a) == pytest.approx(90, abs=0.1)
    assert b == pytest.approx(a / 2, abs=0.1), "same sense, half the turn"


def test_a_belt_does_not_hold_the_centre_distance_unless_asked(shop):
    mates = [{"kind": "hinge", "faces": ["a:hub/side", "frame:b1/side"], "offset": 0},
             {"kind": "belt", "faces": ["a:rim/side", "b:rim/side"]}]
    out = assembly(shop, [("frame", "frame"), ("a", "small"), ("b", "big")], mates)
    assert out["dof"] == 1 + 3, "b keeps its position; its turn follows a's"


def test_a_slot_holds_the_pin_against_the_side_and_lets_it_slide(shop):
    mates = [{"kind": "slot", "faces": ["pin:pin/side", "frame:plate/+y"]}]
    out = assembly(shop, [("frame", "frame"), ("pin", "pin")], mates)
    assert out["solved"] and out["equations"] == 2 and out["dof"] == 4
    axis_point = np.asarray(out["poses"]["pin"][1])
    assert axis_point[1] == pytest.approx(20 + 3, abs=1e-4), "the pin's axis is a radius off the side"
    free = out["freedom"]["pin"]
    assert any(abs(d[0]) > 0.99 for d in free["slides_along"]), "it slides along the slot"


def test_a_slot_needs_a_round_pin_and_a_flat_side(shop):
    with pytest.raises(CadError) as caught:
        assembly(shop, [("frame", "frame"), ("pin", "pin")],
                 [{"kind": "slot", "faces": ["pin:pin/+z", "frame:plate/+y"]}])
    assert caught.value.kind == "bad_mate_faces"


def test_a_cam_keeps_the_follower_touching_the_face(shop):
    # the pin lies across the plate (its axis along y) and rolls on the top face
    mates = [{"kind": "parallel", "faces": ["pin:pin/side", "frame:plate/+y"]},
             {"kind": "cam", "faces": ["pin:pin/side", "frame:plate/+z"]}]
    out = assembly(shop, [("frame", "frame"), ("pin", "pin")], mates)
    assert out["solved"]
    assert abs(np.asarray(out["poses"]["pin"][1])[2] - 5) == pytest.approx(3, abs=1e-3), \
        "the follower's axis is one radius off the cam face"


def test_a_hinge_stops_at_its_limit(shop):
    mates = [{"kind": "hinge", "faces": ["a:hub/side", "frame:b1/side"], "offset": 0,
              "min": -45, "max": 45}]
    within = assembly(shop, [("frame", "frame"), ("a", "small")], mates, drive=[{"part": "a", "turn": 30}])
    assert abs(turned_about_z(within["poses"]["a"])) == pytest.approx(30, abs=0.1)
    with pytest.raises(CadError) as caught:
        assembly(shop, [("frame", "frame"), ("a", "small")], mates, drive=[{"part": "a", "turn": 60}])
    assert caught.value.kind == "no_freedom" and "45" in caught.value.message


def test_a_limit_on_the_wrong_kind_is_refused(shop):
    with pytest.raises(CadError) as caught:
        assembly(shop, [("frame", "frame"), ("a", "small")],
                 [{"kind": "concentric", "faces": ["a:hub/side", "frame:b1/side"], "max": 45}])
    assert caught.value.kind == "bad_arguments"
