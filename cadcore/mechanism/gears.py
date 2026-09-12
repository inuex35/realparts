"""Involute gear tooth profiles and the documents that carry them.

Involute flanks mesh at a constant ratio regardless of small centre-distance
errors. The profile points are computed here (in mm) and written into an
ordinary document as fixed sketch points; tooth count and module remain the
document's parameters. A kernel-level `gear` feature would be the eventual
home for this.
"""
from __future__ import annotations

import math

PRESSURE = 20.0                      # pressure angle, degrees
FLANK_POINTS = 7


def geometry(module: float, teeth: int, pressure: float = PRESSURE) -> dict:
    """Pitch, base, tip and root radii for a gear (mm)."""
    pitch = module * teeth / 2
    return {"module": module, "teeth": int(teeth), "pressure": pressure,
            "pitch_r": pitch,
            "base_r": pitch * math.cos(math.radians(pressure)),
            "tip_r": pitch + module,
            "root_r": pitch - 1.25 * module}


def _involute(base_r: float, roll: float) -> tuple:
    """A point on the involute of a circle at ``roll``, as (radius, polar angle)."""
    radius = base_r * math.hypot(1.0, roll)
    return radius, roll - math.atan(roll)


def tooth(module: float, teeth: int, pressure: float = PRESSURE,
          backlash: float = 0.0, inner: float | None = None,
          outer: float | None = None) -> list:
    """One tooth as a closed outline: two mirrored involute flanks, a flat
    tip and a chord across the root (inside the root disc, so never a face).

    ``inner`` and ``outer`` override the radii the flank runs between; an
    internal gear's tooth space uses the same flanks from its tip circle
    outwards to its root.
    """
    g = geometry(module, teeth, pressure)
    base = g["base_r"]
    tip = g["tip_r"] if outer is None else outer
    root = g["root_r"] if inner is None else inner
    inv_pressure = (math.tan(math.radians(pressure)) - math.radians(pressure))
    # half the tooth's angular thickness at the pitch circle, less backlash
    half = math.pi / (2 * teeth) - backlash / (module * teeth)

    roll_tip = math.sqrt(max(0.0, (tip / base) ** 2 - 1.0))
    roll_root = math.sqrt(max(0.0, (root / base) ** 2 - 1.0)) if root > base else 0.0

    flank = []
    for index in range(FLANK_POINTS):
        roll = roll_root + (roll_tip - roll_root) * index / (FLANK_POINTS - 1)
        radius, wound = _involute(base, roll)
        flank.append((radius, half + inv_pressure - wound))

    # no involute exists below the base circle; the flank runs radially to the root
    if root < base:
        flank.insert(0, (root, flank[0][1]))

    points = [(r * math.cos(a), r * math.sin(a)) for r, a in flank]
    points += [(r * math.cos(-a), r * math.sin(-a)) for r, a in reversed(flank)]
    return points


def spur(module: float, teeth: int, thick: float, bore_d: float,
         pressure: float = PRESSURE, backlash: float = 0.0) -> dict:
    """A document for an external gear: root disc, one tooth patterned, bore cut."""
    g = geometry(module, teeth, pressure)
    outline = tooth(module, teeth, pressure, backlash)
    return {
        "meta": {"name": f"spur gear m{module:g} z{teeth}", "material": "A6061",
                 "note": "involute flanks computed by mechanism.gears; the "
                         "tooth is drawn once and patterned about the axis"},
        "parameters": {"module": module, "teeth": teeth, "thick": thick,
                       "bore_d": bore_d, "root_r": g["root_r"],
                       "pitch_r": g["pitch_r"], "tip_r": g["tip_r"]},
        "features": [
            {"id": "disc", "type": "cylinder", "radius": "root_r",
             "height": "thick", "at": [0, 0, 0], "axis": [0, 0, 1],
             "centred": False},
            {"id": "tooth_sk", "type": "sketch",
             "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1],
                       "x_axis": [1, 0, 0]},
             **_closed(outline)},
            {"id": "one", "type": "extrude", "sketch": "tooth_sk",
             "distance": "thick"},
            {"id": "ring", "type": "pattern", "body": "one", "count": teeth,
             "angle": 360.0 / teeth,
             "axis": {"origin": [0, 0, 0], "direction": [0, 0, 1]},
             "merge": True},
            {"id": "gear", "type": "fuse", "target": "disc", "tool": "ring"},
            # the bore is a cylinder on the axis, not a hole on "disc/+z": after
            # the fuse that face is split into one sliver per tooth gap and a
            # frame taken from it lands off-centre
            {"id": "shaft", "type": "cylinder", "radius": "bore_d/2",
             "height": "thick*3", "at": [0, 0, "-thick"], "axis": [0, 0, 1],
             "centred": False},
            {"id": "bored", "type": "cut", "target": "gear", "tool": "shaft"},
        ],
        "result": "bored"}


def ring(module: float, teeth: int, thick: float, rim: float,
         pressure: float = PRESSURE, backlash: float = 0.0,
         phase: float = 0.0) -> dict:
    """A document for an internal gear: an annulus with the tooth spaces cut out.

    Teeth point inwards, so the tip circle is inside the pitch circle and the
    root outside. Each space has an external tooth's involute flanks, so the
    same profile is cut from tip to root.

    ``phase`` rotates the spaces about the axis (radians) before cutting. A
    planetary train with even planet tooth counts turns the planets half a
    pitch and needs the ring turned by `pi/ring_teeth` to match.
    """
    g = geometry(module, teeth, pressure)
    tip_r = g["pitch_r"] - module              # innermost radius
    root_r = g["pitch_r"] + 1.25 * module      # outermost radius
    outline = tooth(module, teeth, pressure, -backlash,
                    inner=tip_r, outer=root_r + 0.5)
    if phase:
        turn, out = float(phase), []
        for x, y in outline:
            out.append((x * math.cos(turn) - y * math.sin(turn),
                        x * math.sin(turn) + y * math.cos(turn)))
        outline = out
    return {
        "meta": {"name": f"ring gear m{module:g} z{teeth}", "material": "A6061",
                 "note": "an internal gear: the annulus has the same tooth "
                         "form taken *out* of it that a spur gear has put on"},
        "parameters": {"module": module, "teeth": teeth, "thick": thick,
                       "rim": rim, "tip_r": tip_r, "root_r": root_r},
        "features": [
            {"id": "blank", "type": "cylinder", "radius": "root_r + rim",
             "height": "thick", "at": [0, 0, 0], "axis": [0, 0, 1],
             "centred": False},
            {"id": "bore", "type": "cylinder", "radius": "tip_r",
             "height": "thick*3", "at": [0, 0, "-thick"], "axis": [0, 0, 1],
             "centred": False},
            {"id": "annulus", "type": "cut", "target": "blank", "tool": "bore"},
            {"id": "space_sk", "type": "sketch",
             "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1],
                       "x_axis": [1, 0, 0]},
             **_closed(outline)},
            {"id": "one", "type": "extrude", "sketch": "space_sk",
             "distance": "thick"},
            {"id": "spaces", "type": "pattern", "body": "one",
             "count": teeth, "angle": 360.0 / teeth,
             "axis": {"origin": [0, 0, 0], "direction": [0, 0, 1]},
             "merge": True},
            {"id": "gear", "type": "cut", "target": "annulus", "tool": "spaces"},
        ],
        "result": "gear"}


def _closed(points: list) -> dict:
    """A closed polyline as a sketch with every point fixed."""
    names = {f"p{index}": [round(x, 6), round(y, 6)]
             for index, (x, y) in enumerate(points)}
    keys = list(names)
    lines = {f"s{index}": [keys[index], keys[(index + 1) % len(keys)]]
             for index in range(len(keys))}
    fixed = [{"type": "fix", "point": key, "at": names[key]} for key in keys]
    return {"points": names, "lines": lines, "constraints": fixed}


def planetary(module: float = 2.0, sun_teeth: int = 18, planet_teeth: int = 15,
              planets: int = 3, thick: float = 8.0) -> dict:
    """Tooth counts, centre distance and ratio of a planetary train.

    Assembly rules checked here: `ring = sun + 2 * planet`; the planets space
    evenly only when `(sun + ring)` divides by their number; and the chord
    between neighbouring planet centres must exceed a planet's tip diameter.
    """
    from . import MechanismError

    ring_teeth = sun_teeth + 2 * planet_teeth
    if (sun_teeth + ring_teeth) % planets:
        raise MechanismError(
            "planets_will_not_space",
            f"{planets} planets do not divide evenly into this train",
            {"sun": sun_teeth, "ring": ring_teeth, "planets": planets,
             "sun_plus_ring": sun_teeth + ring_teeth})
    centres = module * (sun_teeth + planet_teeth) / 2
    if planets > 1:
        between = 2 * centres * math.sin(math.pi / planets)
        tip_diameter = module * (planet_teeth + 2)
        if between <= tip_diameter:
            raise MechanismError(
                "planets_touch",
                f"{planets} planets of {planet_teeth} teeth overlap each other "
                f"by {tip_diameter - between:.2f} mm",
                {"planets": planets, "planet_teeth": planet_teeth,
                 "between_centres": round(between, 3),
                 "tip_diameter": tip_diameter,
                 "hint": "fewer planets, or a bigger sun"})
    return {"module": module, "sun_teeth": sun_teeth,
            "planet_teeth": planet_teeth, "ring_teeth": ring_teeth,
            "planets": planets, "thick": thick,
            "centres": centres,
            # ring held: sun turns per carrier turn
            "ratio": 1.0 + ring_teeth / sun_teeth}
