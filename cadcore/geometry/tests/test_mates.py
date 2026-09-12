"""Every mate kind, what it holds and what it leaves free, and driving what is left."""
import math

import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
from OCP.gp import gp_Pnt

from cadcore.errors import CadError
from cadcore.geometry.assembly import placement
from cadcore.geometry.assembly.assembly import (MATES, NEEDS, mate_geometry, mate_transform,
                                                prefixed, transformed)
from cadcore.geometry.core.naming import Body, faces_of, role_names
from cadcore.geometry.kernel import box, cut, cylinder


def sphere(fid, radius, at=(0.0, 0.0, 0.0)) -> Body:
    shape = BRepPrimAPI_MakeSphere(gp_Pnt(*at), radius).Shape()
    return Body(shape, role_names(fid, faces_of(shape)))


@pytest.fixture(scope="module")
def plate():
    """A plate with a bore through it, at (30, 20)."""
    solid = cut("bored", box("plate", (60, 40, 10), centred=False),
                cylinder("bore", 5, 30, at=(30, 20, 5)))
    return prefixed(solid, "base")


@pytest.fixture(scope="module")
def pin():
    return prefixed(cylinder("pin", 5, 20, centred=False), "pin")


@pytest.fixture(scope="module")
def block():
    return prefixed(box("blk", (10, 10, 10), centred=False), "blk")


def solve(parts, *mates, ground=None):
    return placement.solve(parts, list(mates), ground=ground or next(iter(parts)))


def turned_about_z(pose) -> float:
    R = np.asarray(pose[0])
    return math.degrees(math.atan2(R[1, 0], R[0, 0]))


def test_every_kind_says_what_it_needs():
    assert set(NEEDS) == set(MATES)


def test_a_hinge_leaves_one_turn(plate, pin):
    out = solve({"base": plate, "pin": pin},
                {"kind": "hinge", "faces": ["pin:pin/side", "base:bore/side"]})
    assert out["dof"] == 1 and not out["conflicts"]
    free = out["freedom"]["pin"]
    assert free["slides_along"] == [] and len(free["turns_about"]) == 1
    assert free["turns_about"][0]["through"][:2] == [30.0, 20.0]     # about the bore


def test_a_hinge_centres_the_pin_in_the_hole(plate, pin):
    out = solve({"base": plate, "pin": pin},
                {"kind": "hinge", "faces": ["pin:pin/side", "base:bore/side"]})
    placed = placement.moved(pin, out["poses"]["pin"])
    assert mate_geometry(placed, "pin:pin/side")["mid"][2] == pytest.approx(5.0, abs=1e-6)


def test_a_slider_leaves_one_slide(plate, pin):
    out = solve({"base": plate, "pin": pin},
                {"kind": "slider", "faces": ["pin:pin/side", "base:bore/side"]})
    assert out["dof"] == 1
    assert out["freedom"]["pin"]["turns_about"] == []
    assert out["freedom"]["pin"]["slides_along"] == [[0.0, 0.0, 1.0]]


def test_a_screw_couples_the_turn_to_the_advance(plate, pin):
    parts = {"base": plate, "pin": pin}
    mates = [{"kind": "screw", "faces": ["pin:pin/side", "base:bore/side"], "pitch": 2.0}]
    out = solve(parts, *mates)
    assert out["dof"] == 1
    driven = placement.drive(parts, mates, [{"part": "pin", "turn": 90}], ground="base")
    before = out["poses"]["pin"][1][2]
    after = driven["poses"]["pin"][1][2]
    assert abs(after - before) == pytest.approx(0.5, abs=1e-6)        # a quarter turn
    lefty = placement.drive(parts, [dict(mates[0], hand="left")],
                            [{"part": "pin", "turn": 90}], ground="base")
    assert (lefty["poses"]["pin"][1][2] - before) == pytest.approx(-(after - before), abs=1e-6)


def test_a_screw_needs_a_pitch(plate, pin):
    with pytest.raises(CadError) as exc:
        solve({"base": plate, "pin": pin},
              {"kind": "screw", "faces": ["pin:pin/side", "base:bore/side"]})
    assert exc.value.kind == "missing_argument"


def test_parallel_perpendicular_and_angle_hold_only_a_direction(plate, block):
    parts = {"base": plate, "blk": block}
    for kind, rows in (("parallel", 2), ("perpendicular", 1), ("angle", 1)):
        out = solve(parts, {"kind": kind, "faces": ["blk:blk/+z", "base:plate/+z"],
                            "angle": 30})
        assert out["equations"] == rows and out["dof"] == 6 - rows, kind
    out = solve(parts, {"kind": "angle", "faces": ["blk:blk/+z", "base:plate/+z"],
                        "angle": 30, "flip": False})
    up = placement._turn(out["poses"]["blk"], (0.0, 0.0, 1.0))
    assert math.degrees(math.acos(float(up @ np.array([0.0, 0.0, 1.0])))) == pytest.approx(30, abs=1e-4)


def test_a_distance_between_planes_is_a_gap(plate, block):
    out = solve({"base": plate, "blk": block},
                {"kind": "distance", "faces": ["blk:blk/-z", "base:plate/+z"], "offset": 3})
    assert not out["conflicts"] and out["dof"] == 3
    assert out["poses"]["blk"][1][2] == pytest.approx(13.0, abs=1e-6)   # 10 thick + 3 gap


def test_tangent_puts_a_pin_on_a_plate(plate, pin):
    out = solve({"base": plate, "pin": pin},
                {"kind": "tangent", "faces": ["pin:pin/side", "base:plate/+z"]})
    assert not out["conflicts"]
    placed = placement.moved(pin, out["poses"]["pin"])
    g = mate_geometry(placed, "pin:pin/side")
    assert abs(g["direction"][2]) < 1e-6                     # lying along the plate
    assert g["mid"][2] == pytest.approx(15.0, abs=1e-6)      # 10 + its own radius


def test_tangent_cylinders_touch_outside_or_inside(pin):
    thin = prefixed(cylinder("pin", 3, 20, centred=False), "thin")
    parts = {"pin": pin, "thin": thin}
    outside = solve(parts, {"kind": "tangent", "faces": ["thin:pin/side", "pin:pin/side"]})
    inside = solve(parts, {"kind": "tangent", "faces": ["thin:pin/side", "pin:pin/side"],
                           "flip": False})
    apart = lambda out: float(np.linalg.norm(out["poses"]["thin"][1][:2]))    # noqa: E731
    assert apart(outside) == pytest.approx(8.0, abs=1e-6)
    assert apart(inside) == pytest.approx(2.0, abs=1e-6)


def test_a_ball_joint_holds_a_point_and_leaves_every_turn():
    a, b = prefixed(sphere("b1", 4), "a"), prefixed(sphere("b2", 4, (20, 0, 0)), "b")
    out = solve({"a": a, "b": b}, {"kind": "ball", "faces": ["b:b2/ball", "a:b1/ball"]})
    assert out["dof"] == 3
    assert out["freedom"]["b"]["slides_along"] == [] and len(out["freedom"]["b"]["turns_about"]) == 3
    assert np.allclose(out["poses"]["b"][1], [-20.0, 0.0, 0.0])


def test_a_ball_needs_two_spheres(plate):
    b = prefixed(sphere("b2", 4), "b")
    with pytest.raises(CadError) as exc:
        solve({"base": plate, "b": b}, {"kind": "ball", "faces": ["b:b2/ball", "base:plate/+z"]})
    assert exc.value.kind == "bad_mate_faces"
    assert "ball" in exc.value.message and "plane" in exc.value.message


def gear_train():
    base = cut("p2", box("plate", (60, 40, 10), centred=False), cylinder("bore1", 5, 30, at=(15, 20, 5)))
    base = prefixed(cut("p3", base, cylinder("bore2", 5, 30, at=(35, 20, 5))), "base")
    parts = {"base": base,
             "g1": prefixed(cylinder("axle", 5, 20, centred=False), "g1"),
             "g2": prefixed(cylinder("axle", 5, 20, centred=False), "g2")}
    mates = [{"kind": "hinge", "faces": ["g1:axle/side", "base:bore1/side"]},
             {"kind": "hinge", "faces": ["g2:axle/side", "base:bore2/side"]},
             {"kind": "gear", "faces": ["g2:axle/side", "g1:axle/side"],
              "ratio": 1.5, "offset": 20}]
    return parts, mates


def test_gears_turn_each_other_in_the_ratio():
    parts, mates = gear_train()
    rest = solve(parts, *mates)
    assert rest["dof"] == 1 and not rest["conflicts"]
    driven = placement.drive(parts, mates, [{"part": "g1", "turn": 30}], ground="base",
                             rest=rest)
    assert turned_about_z(driven["poses"]["g1"]) == pytest.approx(30, abs=1e-4)
    assert turned_about_z(driven["poses"]["g2"]) == pytest.approx(-45, abs=1e-4)


def link(fid, length):
    """A bar with a bore at each end, ``length`` apart, 6 thick."""
    bar = box(fid, (length + 20, 16, 6), at=(-10, -8, 0), centred=False)
    bar = cut(fid + "a", bar, cylinder("b1", 3, 20, at=(0, 0, -5), centred=False))
    return cut(fid + "b", bar, cylinder("b2", 3, 20, at=(length, 0, -5), centred=False))


def four_bar():
    parts = {"ground": prefixed(link("g", 120), "ground"),
             "crank": prefixed(link("c", 40), "crank"),
             "coupler": prefixed(link("k", 100), "coupler"),
             "rocker": prefixed(link("r", 80), "rocker")}
    hinge = lambda a, b, offset: {"kind": "hinge", "faces": [a, b],        # noqa: E731
                                  "offset": offset, "flip": False}
    mates = [hinge("crank:b1/side", "ground:b1/side", 6),
             hinge("coupler:b1/side", "crank:b2/side", 6),
             hinge("rocker:b1/side", "coupler:b2/side", 6),
             hinge("rocker:b2/side", "ground:b2/side", 18)]
    return parts, mates


def test_a_loop_of_hinges_closes_and_leaves_one_freedom():
    """Guards: the ordered seed leaves the last hinge of a four-bar wide open,
    and the polish alone walked into a twisted compromise with every mate off."""
    parts, mates = four_bar()
    out = solve(parts, *mates)
    assert not out["conflicts"]
    assert out["dof"] == 1
    for part in ("crank", "coupler", "rocker"):
        assert out["freedom"][part]["turns_about"][0]["direction"] == [0.0, 0.0, 1.0]


def test_driving_the_crank_round_swings_the_rocker():
    parts, mates = four_bar()
    driven = placement.drive(parts, mates, [{"part": "crank", "turn": 360}],
                             ground="ground", frames=12)
    assert len(driven["frames"]) == 12
    rocker = [turned_about_z(frame["rocker"]) for frame in driven["frames"]]
    assert max(rocker) - min(rocker) > 40           # it rocks
    assert max(rocker) < 120 and min(rocker) > 0    # and does not go round
    # every frame is a closed loop: the rocker's far end stays on the ground pin
    for frame in driven["frames"]:
        placed = placement.moved(parts["rocker"], (np.asarray(frame["rocker"][0]),
                                                   np.asarray(frame["rocker"][1])))
        assert mate_geometry(placed, "rocker:b2/side")["mid"][:2] == pytest.approx((120.0, 0.0), abs=1e-4)


def test_a_drive_picks_the_freedom_and_refuses_one_the_part_lacks(plate, pin):
    parts = {"base": plate, "pin": pin}
    mates = [{"kind": "hinge", "faces": ["pin:pin/side", "base:bore/side"]}]
    with pytest.raises(CadError) as exc:
        placement.drive(parts, mates, [{"part": "pin", "slide": 5}], ground="base")
    assert exc.value.kind == "no_freedom"
    with pytest.raises(CadError) as exc:
        placement.drive(parts, mates, [{"part": "base", "turn": 5}], ground="base")
    assert exc.value.kind == "no_freedom"
    turned = placement.drive(parts, mates, [{"part": "pin", "turn": 90, "about": [0, 0, -1]}],
                             ground="base")
    assert turned_about_z(turned["poses"]["pin"]) == pytest.approx(-90, abs=1e-4)


def test_a_drive_holds_the_part_so_nothing_is_free(plate, pin):
    parts = {"base": plate, "pin": pin}
    mates = [{"kind": "concentric", "faces": ["pin:pin/side", "base:bore/side"]}]
    slid = placement.drive(parts, mates, [{"part": "pin", "slide": 7}], ground="base")
    assert slid["poses"]["pin"][1][2] == pytest.approx(-3.0, abs=1e-6)     # from 4 of them
    assert slid["dof"] == 1                                # the turn is still free
    assert slid["freedom"]["pin"]["slides_along"] == []


def test_the_ordered_mate_places_the_new_kinds_too(plate, pin, block):
    for kind in ("hinge", "slider"):
        trsf = mate_transform(kind, pin, plate, ["pin:pin/side", "base:bore/side"])
        placed = transformed(pin, trsf)
        assert mate_geometry(placed, "pin:pin/side")["mid"][:2] == pytest.approx((30.0, 20.0), abs=1e-6)
    trsf = mate_transform("tangent", pin, plate, ["pin:pin/side", "base:plate/+z"])
    g = mate_geometry(transformed(pin, trsf), "pin:pin/side")
    assert g["mid"][2] == pytest.approx(15.0, abs=1e-6)
    trsf = mate_transform("perpendicular", block, plate, ["blk:blk/+z", "base:plate/+z"])
    from cadcore.geometry.core.measure import face_frame
    up = face_frame(transformed(block, trsf), "blk:blk/+z")["normal"]
    assert abs(up[2]) < 1e-6


def test_an_unknown_kind_is_refused_with_the_list(plate, pin):
    with pytest.raises(CadError) as exc:
        solve({"base": plate, "pin": pin},
              {"kind": "welded", "faces": ["pin:pin/side", "base:bore/side"]})
    assert exc.value.kind == "unknown_mate"
    assert exc.value.detail["available"] == list(MATES)
