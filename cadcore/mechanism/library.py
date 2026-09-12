"""The built-in mechanisms, each declared as a `Rig` or a small class.

Every bar is `link.json` built at its own centre distance, so changing a
length rebuilds the part as well as moving it.
"""
from __future__ import annotations

import math

from . import MechanismError, Part, Rig

LINK = "examples/mechanism/link.json"

CRANK_C = (0.86, 0.44, 0.18)
COUPLER_C = (0.58, 0.62, 0.70)
ROCKER_C = (0.26, 0.54, 0.74)
FRAME_C = (0.42, 0.40, 0.36)
SLIDER_C = (0.78, 0.72, 0.36)


def bar(name: str, spans, length: float, colour, width=16.0, thick=6.0,
        z=0.0) -> Part:
    return Part(name, LINK, {"centres": length, "width": width, "thick": thick},
                colour, spans=spans, z=z)


def four_bar(ground=120.0, crank=40.0, coupler=110.0, rocker=90.0,
             width=16.0, thick=6.0) -> Rig:
    """Crank, coupler, rocker and frame.

    Grashof's rule is not enforced; a crank that cannot turn fully makes the
    solver refuse to close at some angle, and that angle is reported.
    """
    seed = _four_bar_seed(ground, crank, coupler, rocker)
    return Rig(
        joints=seed,
        loop=["a", "b", "c", "d"],
        links={"crank": ("a", "b"), "coupler": ("b", "c"), "rocker": ("c", "d"),
               "frame": ("d", "a")},
        lengths={"crank": crank, "coupler": coupler, "rocker": rocker,
                 "frame": ground},
        anchors={"a": (0.0, 0.0), "d": (ground, 0.0)},
        driver=("a", "b"),
        parts=[bar("frame", ("d", "a"), ground, FRAME_C, width, thick, z=-thick),
               bar("crank", ("a", "b"), crank, CRANK_C, width, thick, z=0.0),
               bar("coupler", ("b", "c"), coupler, COUPLER_C, width, thick,
                   z=thick + 1.0),
               bar("rocker", ("c", "d"), rocker, ROCKER_C, width, thick, z=0.0)])


def _four_bar_seed(ground, crank, coupler, rocker) -> dict:
    """A closed starting pose at input angle 0.

    PlaneGCS does not converge from a guess that cannot close, so the seed is
    computed by trigonometry and impossible lengths are refused here.
    """
    b = (crank, 0.0)
    span = math.hypot(ground - b[0], b[1])
    if span > coupler + rocker or span < abs(coupler - rocker):
        raise MechanismError(
            "cannot_assemble", "these four lengths do not make a four-bar",
            {"ground": ground, "crank": crank, "coupler": coupler,
             "rocker": rocker, "span_at_zero_mm": round(span, 3)})
    along = (span ** 2 + coupler ** 2 - rocker ** 2) / (2 * span)
    height = math.sqrt(max(0.0, coupler ** 2 - along ** 2))
    ux, uy = (ground - b[0]) / span, (0.0 - b[1]) / span
    c = (b[0] + ux * along - uy * height, b[1] + uy * along + ux * height)
    return {"a": (0.0, 0.0), "b": b, "c": c, "d": (ground, 0.0)}


def slider_crank(crank=35.0, rod=110.0, bore=34.0, width=16.0, thick=6.0) -> Rig:
    """Rotation into straight-line motion.

    The slider is the horizontal constraint on the line from the wrist pin to
    the main bearing; that closes the loop and removes the last freedom.
    """
    if rod <= crank:
        raise MechanismError("cannot_assemble",
                             "the con-rod has to be longer than the crank",
                             {"crank": crank, "rod": rod})
    return Rig(
        joints={"a": (0.0, 0.0), "b": (crank, 0.0), "c": (crank + rod, 0.0)},
        loop=["a", "b", "c"],
        links={"crank": ("a", "b"), "rod": ("b", "c")},
        lengths={"crank": crank, "rod": rod},
        anchors={"a": (0.0, 0.0)},
        frames=[("slide", "c", "a", "horizontal")],
        driver=("a", "b"),
        parts=[bar("crank", ("a", "b"), crank, CRANK_C, width, thick),
               bar("rod", ("b", "c"), rod, COUPLER_C, width, thick,
                   z=thick + 1.0),
               Part("piston", "examples/mechanism/piston.json",
                    {"bore": bore, "pin_d": 6.0}, SLIDER_C, rides="c",
                    z=thick + 1.0)])


def scotch_yoke(crank=40.0, pin_d=12.0, run=150.0, thick=8.0) -> Rig:
    """Rotation into sinusoidal motion via a pin sliding in a slot.

    The yoke's reference point sits directly below the pin: the vertical line
    is the slot and the horizontal line is the travel.
    """
    return Rig(
        joints={"a": (0.0, 0.0), "b": (crank, 0.0), "y": (crank, 0.0)},
        loop=["a", "b", "y"],
        links={"crank": ("a", "b")},
        lengths={"crank": crank},
        anchors={"a": (0.0, 0.0)},
        frames=[("rise", "b", "y", "vertical"), ("travel", "y", "a", "horizontal")],
        driver=("a", "b"),
        # the yoke sits under the crank so the pin in its slot stays visible
        parts=[bar("crank", ("a", "b"), crank, CRANK_C, 16.0, 6.0),
               Part("pin", "examples/mechanism/pin.json",
                    {"pin_d": pin_d, "length": thick * 2.6}, CRANK_C,
                    rides="b", z=-thick / 2),
               Part("yoke", "examples/mechanism/yoke.json",
                    {"run": run, "height": run * 0.55,
                     "slot_w": pin_d + 0.4, "thick": thick},
                    SLIDER_C, rides="y", z=-thick - 6.0)])


class Geneva:
    """Continuous rotation into intermittent rotation.

    Not a single constraint system: while the pin is in a slot the wheel is
    driven, otherwise it is locked. The switch between the two is stated here
    rather than solved.

    The pin enters and leaves along the slot, so the crank arm is square to the
    wheel radius at both ends; centre distance and wheel radius follow from
    that right triangle and the slot count.
    """

    def __init__(self, slots=4, crank=45.0, pin_d=8.0, thick=8.0):
        if int(slots) < 3:
            raise MechanismError("too_few_slots",
                                 "a Geneva wheel needs at least three slots",
                                 {"slots": slots})
        self.slots = int(slots)
        self.crank, self.pin_d, self.thick = float(crank), float(pin_d), float(thick)

    @property
    def centres(self) -> float:
        return self.crank / math.sin(math.pi / self.slots)

    @property
    def wheel_r(self) -> float:
        return self.centres * math.cos(math.pi / self.slots)

    @property
    def engagement(self) -> float:
        """Half the driver angle the pin spends in a slot: pi/2 - pi/n."""
        return math.pi / 2 - math.pi / self.slots

    def parts(self) -> list:
        return [Part("driver", "examples/mechanism/geneva_driver.json",
                     {"crank": self.crank, "pin_d": self.pin_d,
                      "thick": self.thick}, CRANK_C, rides="driver"),
                Part("wheel", "examples/mechanism/geneva_wheel.json",
                     {"slots": self.slots, "crank": self.crank,
                      "slot_w": self.pin_d + 0.4, "thick": self.thick},
                     ROCKER_C, rides="wheel", z=self.thick + 2.0)]

    def wheel_angle(self, angle: float) -> float:
        """Wheel angle for a driver angle (radians).

        Engaged: the slot points at the pin. Disengaged: the driver angle is
        clamped at the engagement limit, so the wheel holds and the two phases
        meet continuously.
        """
        revolutions = math.floor((angle + math.pi) / (2 * math.pi))
        local = angle - revolutions * 2 * math.pi
        held = max(-self.engagement, min(self.engagement, local))
        pin = (self.crank * math.cos(held), self.crank * math.sin(held))
        facing = math.atan2(pin[1], pin[0] - self.centres) - math.pi
        facing = (facing + math.pi) % (2 * math.pi) - math.pi
        return facing - revolutions * (2 * math.pi / self.slots)

    def sweep(self, frames: int = 72, turns: float = 1.0) -> list:
        return [{"driver": (0.0, 0.0), "wheel": (self.centres, 0.0)}
                for _ in range(frames + 1)]

    def place_at(self, angle: float) -> dict:
        from . import Placement
        return {"driver": Placement(0.0, 0.0, angle),
                "wheel": Placement(self.centres, 0.0, self.wheel_angle(angle),
                                   self.thick + 2.0)}

    def reach(self) -> float:
        return (self.centres + self.wheel_r + self.crank) * 1.2


class Planetary:
    """Sun, planets and a fixed ring: rotation in, slower rotation out.

    Assembly rules (`ring = sun + 2 * planet`; planet count divides
    `sun + ring`) are checked by `gears.planetary`. Meshing is checked by
    `tests/test_gears.py`.

    Motion is Willis's equation with the ring held: the carrier turns
    `sun / (sun + ring)` as fast as the sun and each planet turns
    `1 - ring / planet` as fast as the carrier.
    """

    def __init__(self, module=2.0, sun_teeth=18, planet_teeth=15, planets=3,
                 thick=8.0, rim=6.0):
        from . import gears

        self.spec = gears.planetary(module, sun_teeth, planet_teeth, planets,
                                    thick)
        self.module, self.thick, self.rim = module, thick, rim
        self.sun_teeth, self.planet_teeth = sun_teeth, planet_teeth
        self.ring_teeth = self.spec["ring_teeth"]
        self.count = planets
        self.centres = self.spec["centres"]

    def parts(self) -> list:
        from . import gears

        out = [Part("ring", "<generated>",
                    {"teeth": self.ring_teeth, "module": self.module},
                    FRAME_C, rides="ring",
                    spec=gears.ring(self.module, self.ring_teeth, self.thick,
                                    self.rim, phase=self.ring_phase)),
               Part("sun", "<generated>",
                    {"teeth": self.sun_teeth, "module": self.module},
                    CRANK_C, rides="sun",
                    spec=gears.spur(self.module, self.sun_teeth, self.thick,
                                    self.module * 4))]
        for index in range(self.count):
            out.append(Part(f"planet{index}", "<generated>",
                            {"teeth": self.planet_teeth, "module": self.module},
                            ROCKER_C, rides=f"planet{index}",
                            spec=gears.spur(self.module, self.planet_teeth,
                                            self.thick, self.module * 3)))
        return out

    def carrier_angle(self, sun_angle: float) -> float:
        return sun_angle * self.sun_teeth / (self.sun_teeth + self.ring_teeth)

    def planet_angle(self, sun_angle: float, index: int) -> float:
        """Absolute angle of planet `index`: carried round with the sun held,
        plus the sun's own turn, plus `mesh_phase`.

        Gears are drawn with a tooth centred on angle 0, so a planet with an
        even tooth count presents a tooth (not a gap) to the sun and must be
        turned half a pitch to mesh.
        """
        carrier = self.carrier_angle(sun_angle)
        seat = carrier + index * 2 * math.pi / self.count
        return (seat * (1 + self.sun_teeth / self.planet_teeth)
                - sun_angle * self.sun_teeth / self.planet_teeth
                + self.mesh_phase)

    @property
    def mesh_phase(self) -> float:
        """Half a pitch for even planet tooth counts, else 0."""
        if self.planet_teeth % 2 == 0:
            return math.pi / self.planet_teeth
        return 0.0

    @property
    def ring_phase(self) -> float:
        """The planet's `mesh_phase` expressed at the ring: the same pitch-line
        arc (`pi * module / 2`) is `pi / ring_teeth` at the ring's radius."""
        if self.planet_teeth % 2 == 0:
            return math.pi / self.ring_teeth
        return 0.0

    def place_at(self, sun_angle: float) -> dict:
        from . import Placement

        carrier = self.carrier_angle(sun_angle)
        placed = {"ring": Placement(0.0, 0.0, 0.0),
                  "sun": Placement(0.0, 0.0, sun_angle)}
        for index in range(self.count):
            seat = carrier + index * 2 * math.pi / self.count
            placed[f"planet{index}"] = Placement(
                self.centres * math.cos(seat), self.centres * math.sin(seat),
                self.planet_angle(sun_angle, index))
        return placed

    def turns(self) -> float:
        """Sun angle for one full turn of the carrier (the output)."""
        return 2 * math.pi * self.spec["ratio"]

    def reach(self) -> float:
        return (self.module * self.ring_teeth / 2 + self.rim) * 2.4
