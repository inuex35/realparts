"""Mechanism tests: solver output is checked against closed-form kinematics."""
import math

import pytest

from cadcore.mechanism import MechanismError
from cadcore.mechanism.library import Geneva, four_bar, scotch_yoke, slider_crank


def test_a_four_bar_holds_its_link_lengths_all_the_way_round():
    """Checks link lengths at every frame of a sweep."""
    rig = four_bar(ground=120.0, crank=40.0, coupler=110.0, rocker=90.0)
    for joints in rig.sweep(frames=48):
        assert math.dist(joints["a"], joints["b"]) == pytest.approx(40.0, abs=1e-6)
        assert math.dist(joints["b"], joints["c"]) == pytest.approx(110.0, abs=1e-6)
        assert math.dist(joints["c"], joints["d"]) == pytest.approx(90.0, abs=1e-6)
        assert math.dist(joints["a"], joints["d"]) == pytest.approx(120.0, abs=1e-6)


def test_a_cycle_comes_back_to_where_it_started():
    """Guards: a chain of seeded solves must return to its start after a full turn."""
    rig = four_bar()
    poses = rig.sweep(frames=72)
    for joint in poses[0]:
        assert math.dist(poses[0][joint], poses[-1][joint]) == pytest.approx(0.0, abs=1e-6)


def test_the_solver_stays_on_one_assembly():
    """Guards: continuation seeding keeps the four-bar on one assembly branch
    (no elbow-up/elbow-down flips between frames)."""
    rig = four_bar()
    poses = rig.sweep(frames=180)
    steps = [math.dist(poses[i]["c"], poses[i + 1]["c"])
             for i in range(len(poses) - 1)]
    # two degrees of crank moves the coupler pin by a few mm at most
    assert max(steps) < 4.0, f"largest step {max(steps):.2f} mm"


def test_a_slider_crank_matches_the_closed_form():
    """Checks the piston position against r cos t + sqrt(l^2 - r^2 sin^2 t)."""
    crank, rod = 35.0, 110.0
    rig = slider_crank(crank=crank, rod=rod)
    seed = None
    for index in range(72):
        degrees = index * 5.0
        joints = rig.solve(degrees, seed)
        seed = {k: list(v) for k, v in joints.items()}
        t = math.radians(degrees)
        want = crank * math.cos(t) + math.sqrt(rod ** 2 - (crank * math.sin(t)) ** 2)
        assert joints["c"][0] == pytest.approx(want, abs=1e-6)
        assert joints["c"][1] == pytest.approx(0.0, abs=1e-9)


def test_a_scotch_yoke_is_a_cosine_to_the_micron():
    """Checks the yoke position against r cos t."""
    crank = 40.0
    rig = scotch_yoke(crank=crank)
    seed = None
    for index in range(73):
        degrees = index * 5.0
        joints = rig.solve(degrees, seed)
        seed = {k: list(v) for k, v in joints.items()}
        assert joints["y"][0] == pytest.approx(crank * math.cos(math.radians(degrees)),
                                               abs=1e-6)
        assert joints["y"][1] == pytest.approx(0.0, abs=1e-9)


def test_a_four_bar_that_cannot_close_says_so():
    """Checks that impossible lengths are refused with the lengths in the detail."""
    with pytest.raises(MechanismError) as exc:
        four_bar(ground=300.0, crank=20.0, coupler=40.0, rocker=40.0)
    assert exc.value.kind == "cannot_assemble"
    assert exc.value.detail["ground"] == 300.0


def test_a_geneva_wheel_moves_for_a_quarter_of_the_turn():
    """Checks a four-slot Geneva: driven a quarter turn, 90 degrees per index,
    continuous at engagement."""
    geneva = Geneva(slots=4, crank=45.0)
    assert geneva.centres == pytest.approx(45.0 / math.sin(math.pi / 4))
    assert geneva.wheel_r == pytest.approx(geneva.centres * math.cos(math.pi / 4))

    steps = 720
    angles = [i * 2 * math.pi / steps for i in range(steps + 1)]
    turned = [geneva.wheel_angle(a) for a in angles]
    moving = sum(1 for i in range(steps) if abs(turned[i + 1] - turned[i]) > 1e-9)
    assert moving / steps == pytest.approx(0.25, abs=0.01)
    assert turned[-1] - turned[0] == pytest.approx(-math.pi / 2, abs=1e-9)
    # continuity, not smoothness: the wheel peaks at a few times the driver
    # rate as the pin enters; a discontinuity would be a step of pi/2
    biggest = max(abs(turned[i + 1] - turned[i]) for i in range(steps))
    assert biggest < 0.05, f"step of {biggest:.4f} rad looks like a jump"
    assert biggest > 2 * (2 * math.pi / steps), "the wheel never speeds up at entry"


def test_changing_a_length_rebuilds_the_bar_as_well_as_moving_it():
    """Checks that a longer rocker is built as a longer part, not just moved."""
    short = four_bar(rocker=90.0)
    long = four_bar(rocker=130.0)
    assert [p.parameters["centres"] for p in short.parts] == [120.0, 40.0, 110.0, 90.0]
    assert [p.parameters["centres"] for p in long.parts] == [120.0, 40.0, 110.0, 130.0]
    assert short.link_lengths()["rocker"] == 90.0
    assert long.link_lengths()["rocker"] == 130.0
    for rig in (short, long):
        assert len(rig.sweep(frames=24)) == 25


def test_a_linkage_that_jams_says_how_far_it_got():
    """Checks that a non-Grashof four-bar jams and reports how far it turned."""
    with pytest.raises(MechanismError) as exc:
        four_bar(ground=220.0).sweep(frames=360)
    assert exc.value.kind == "jammed"
    reached = exc.value.detail["turned_through_deg"]
    assert 50.0 < reached < 60.0, reached
    assert exc.value.detail["stopped_at_deg"] > reached

    # a longer frame jams sooner
    with pytest.raises(MechanismError) as longer:
        four_bar(ground=180.0).sweep(frames=360)
    assert longer.value.detail["turned_through_deg"] > reached


def test_the_same_linkage_at_a_range_of_lengths_still_turns():
    """Checks several rocker lengths each rebuild the bar and complete a turn."""
    for rocker in (60.0, 90.0, 130.0, 150.0):
        rig = four_bar(rocker=rocker)
        poses = rig.sweep(frames=72)
        worst = max(abs(math.dist(p["c"], p["d"]) - rocker) for p in poses)
        assert worst < 1e-6, f"rocker {rocker}: out by {worst}"
        assert [p.parameters["centres"] for p in rig.parts
                if p.name == "rocker"] == [rocker]
    # an impossible length is refused before any sweep
    with pytest.raises(MechanismError):
        four_bar(rocker=200.0)


def test_a_mechanism_can_be_rebuilt_at_every_frame_not_just_moved():
    """Checks the morph sweep used by `render_morph.py`: the rocker grows from
    90 to 150 mm over two crank turns, and the rebuilt bar matches the solved
    length at every frame."""
    from cadcore import describe
    from cadcore.model.document import Document
    from cadcore.evaluation.graph import Evaluator

    link = Document.load("examples/mechanism/link.json").as_dict()
    seed, seen = None, []
    for index in range(25):                       # 25 so the ramp returns to 90
        length = 90.0 + 60.0 * (1 - abs(2 * index / 24 - 1))
        rig = four_bar(rocker=length)
        joints = rig.solve(720.0 * index / 24, seed)
        seed = {k: list(v) for k, v in joints.items()}
        assert math.dist(joints["c"], joints["d"]) == pytest.approx(length, abs=1e-6)

        spec = dict(link)
        spec["parameters"] = {**link["parameters"], "centres": length}
        body = Evaluator(Document.from_dict(spec)).build()
        seen.append((length, describe(body)["volume_mm3"]))

    # volume rises monotonically with length; both ends have the same length
    assert seen[0][1] == pytest.approx(seen[-1][1], abs=1e-3)
    assert seen[12][0] == pytest.approx(150.0)    # peak at the middle
    rising = sorted(seen[:13], key=lambda pair: pair[0])
    assert [v for _, v in rising] == sorted(v for _, v in rising)
