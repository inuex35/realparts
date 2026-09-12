"""Gear tests: meshing is checked by placing gears at their centre distance
and measuring the common volume, not by inspecting the tooth shape.
"""
import math

import pytest

from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

from ...geometry.assembly.assembly import transformed
from ...geometry.solids.booleans import common
from cadcore.model.document import Document
from cadcore.evaluation.graph import Evaluator
from ...geometry.core.measure import volume
from cadcore.mechanism import MechanismError, gears
from cadcore.mechanism.library import Planetary

MODULE, SUN, PLANET = 2.0, 18, 15


def build(spec) -> object:
    return Evaluator(Document.from_dict(spec)).build()


def put(body, x=0.0, y=0.0, turn=0.0):
    spin = gp_Trsf()
    spin.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), turn)
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(x, y, 0.0))
    return transformed(body, move.Multiplied(spin))


def overlap(a, b) -> float:
    try:
        return volume(common("x", a, b))
    except Exception:                                            # noqa: BLE001
        return 0.0                       # no common volume


@pytest.fixture(scope="module")
def pair():
    return (build(gears.spur(MODULE, SUN, 8.0, 8.0)),
            build(gears.spur(MODULE, PLANET, 8.0, 6.0)))


def test_the_involute_is_the_curve_it_claims_to_be():
    """Checks `_involute` against the closed form and the pitch-circle roll angle."""
    geometry = gears.geometry(MODULE, SUN)
    base = geometry["base_r"]
    for roll in (0.2, 0.5, 1.0):
        radius, wound = gears._involute(base, roll)
        assert radius == pytest.approx(base * math.sqrt(1 + roll ** 2))
        assert wound == pytest.approx(roll - math.atan(roll))
    # at the pitch circle the roll angle equals tan(pressure angle)
    roll_at_pitch = math.tan(math.radians(gears.PRESSURE))
    radius, _ = gears._involute(base, roll_at_pitch)
    assert radius == pytest.approx(geometry["pitch_r"], abs=1e-9)


def test_two_gears_mesh_at_their_own_centre_distance(pair):
    """Checks that two gears at their centre distance do not overlap when in
    phase and overlap by a whole tooth half a pitch out."""
    sun, planet = pair
    centres = MODULE * (SUN + PLANET) / 2
    assert centres == 33.0

    meshed = overlap(sun, put(planet, x=centres))
    jammed = overlap(sun, put(planet, x=centres, turn=math.pi / PLANET))
    tooth_volume = math.pi * MODULE / 2 * (
        gears.geometry(MODULE, PLANET)["tip_r"]
        - gears.geometry(MODULE, PLANET)["root_r"]) * 8.0

    assert meshed < 0.05, f"meshed but overlapping by {meshed:.3f} mm3"
    assert jammed > 0.5 * tooth_volume, \
        f"half a pitch out and only {jammed:.1f} mm3 of {tooth_volume:.0f} collides"


def test_a_gear_is_the_size_its_module_says(pair):
    """Checks pitch, tip and root radii against module and tooth count."""
    for teeth in (SUN, PLANET):
        geometry = gears.geometry(MODULE, teeth)
        assert geometry["pitch_r"] == MODULE * teeth / 2
        assert geometry["tip_r"] - geometry["pitch_r"] == pytest.approx(MODULE)
        assert geometry["pitch_r"] - geometry["root_r"] == pytest.approx(1.25 * MODULE)


def test_a_planetary_train_that_cannot_be_built_is_refused():
    """Checks the spacing rule and `ring = sun + 2 * planet`."""
    with pytest.raises(MechanismError) as exc:
        gears.planetary(sun_teeth=16, planet_teeth=16, planets=3)
    assert exc.value.kind == "planets_will_not_space"
    assert exc.value.detail["sun_plus_ring"] == 64        # not divisible by 3

    train = gears.planetary(sun_teeth=18, planet_teeth=15, planets=3)
    assert train["ring_teeth"] == 18 + 2 * 15
    assert train["ratio"] == pytest.approx(1 + 48 / 18)


def test_the_whole_train_assembles_and_stays_meshed():
    """Checks every planet against sun and ring for interference over a turn."""
    train = Planetary()
    built = {part.name: build(part.spec) for part in train.parts()}
    tooth_volume = 113.0                                  # one planet tooth

    worst = 0.0
    for step in range(6):
        pose = train.place_at(2 * math.pi * step / 6)
        placed = {name: put(built[name], p.x, p.y, p.turn)
                  for name, p in pose.items()}
        for index in range(train.count):
            for other in ("sun", "ring"):
                worst = max(worst, overlap(placed[f"planet{index}"],
                                           placed[other]))
    # the residual is polyline approximation of the involute, not a clash
    assert worst < 0.01 * tooth_volume, f"{worst:.3f} mm3 of teeth interfering"


def test_the_reduction_is_the_one_the_tooth_counts_give():
    """Checks the carrier and planet rates against Willis's equation."""
    train = Planetary()
    assert train.carrier_angle(2 * math.pi) == pytest.approx(
        2 * math.pi * 18 / (18 + 48))
    assert train.spec["ratio"] == pytest.approx(1 + 48 / 18)
    carrier = train.carrier_angle(2 * math.pi)
    turned = train.planet_angle(2 * math.pi, 0) - train.planet_angle(0.0, 0)
    assert turned == pytest.approx(carrier * (1 - 48 / 15), abs=1e-9)


@pytest.mark.parametrize("sun_teeth,planet_teeth,planets", [
    (16, 16, 2), (20, 16, 3), (18, 14, 2), (24, 12, 3),
    (18, 15, 3), (16, 17, 3), (21, 15, 3),
])
def test_a_train_meshes_whether_its_planets_have_an_odd_or_even_tooth_count(
        sun_teeth, planet_teeth, planets):
    """Guards: even planet tooth counts need the half-pitch `mesh_phase` on the
    planet and the matching `ring_phase` on the ring to mesh without interference."""
    train = Planetary(sun_teeth=sun_teeth, planet_teeth=planet_teeth,
                      planets=planets)
    built = {part.name: build(part.spec) for part in train.parts()}
    tooth_volume = 113.0

    worst = 0.0
    for step in range(3):
        pose = train.place_at(2 * math.pi * step / 3)
        placed = {name: put(built[name], p.x, p.y, p.turn)
                  for name, p in pose.items()}
        for index in range(train.count):
            for other in ("sun", "ring"):
                worst = max(worst, overlap(placed[f"planet{index}"],
                                           placed[other]))
    assert worst < 0.01 * tooth_volume, \
        f"{sun_teeth}/{planet_teeth} x{planets}: {worst:.3f} mm3 of teeth interfering"


def test_the_two_halves_of_that_phase_are_the_same_arc():
    """Checks that `mesh_phase` and `ring_phase` are both zero or both set."""
    odd = Planetary(sun_teeth=18, planet_teeth=15, planets=3)
    even = Planetary(sun_teeth=20, planet_teeth=16, planets=3)
    assert (odd.mesh_phase, odd.ring_phase) == (0.0, 0.0)
    assert even.mesh_phase == pytest.approx(math.pi / 16)
    assert even.ring_phase == pytest.approx(math.pi / even.ring_teeth)


def test_planets_that_overlap_each_other_are_refused():
    """Checks that planets which space evenly but overlap at the tips are refused."""
    with pytest.raises(MechanismError) as exc:
        gears.planetary(sun_teeth=12, planet_teeth=30, planets=6)
    assert exc.value.kind == "planets_touch"
    assert exc.value.detail["between_centres"] == 42.0
    assert exc.value.detail["tip_diameter"] == 64.0
    # two planets never touch each other
    assert gears.planetary(sun_teeth=12, planet_teeth=30, planets=2)["ring_teeth"] == 72
