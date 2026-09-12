"""Build a mechanism's meshes and motion into one JSON file for a viewer.

    python -m cadcore.mechanism.scene four_bar build/four_bar.json [frames]

Parts are built at their own parameters and meshed once; frames are solver
placements. Blender reads the file rather than importing the kernel, since
OCCT is not available in Blender's Python.
"""
from __future__ import annotations

import json
import math
import os
import sys

from ..model.document import Document
from ..evaluation.graph import Evaluator
from ..geometry.io.tessellate import tessellate

from . import library

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))


def _mesh(part, deflection: float) -> dict:
    """Build the part at its own parameters and tessellate it."""
    if part.spec is not None:
        raw = dict(part.spec)            # computed document (gears)
    else:
        raw = Document.load(os.path.join(REPO, part.document)).as_dict()
        raw.setdefault("parameters", {}).update(part.parameters)
    body = Evaluator(Document.from_dict(raw)).build()
    tri = tessellate(body, deflection=deflection, angular=0.35)
    return {"vertices": [list(map(float, v)) for v in tri["vertices"]],
            "triangles": [list(map(int, t)) for t in tri["triangles"]]}


def _frames(placements: list) -> list:
    return [{name: [p.x, p.y, p.z, p.turn] for name, p in pose.items()}
            for pose in placements]


def build(which: str, frames: int = 72, deflection: float = 0.08) -> dict:
    """Meshes, per-frame placements and overall reach for one mechanism."""
    if which == "geneva":
        rig = library.Geneva()
        parts = rig.parts()
        # open cycle: the pose at a whole turn repeats the first and would be
        # held twice in looped playback
        placements = [rig.place_at(2 * math.pi * i / frames)
                      for i in range(frames)]
        reach = rig.reach()
        notes = {"slots": rig.slots,
                 "centres_mm": round(rig.centres, 4),
                 "wheel_radius_mm": round(rig.wheel_r, 4),
                 "engaged_fraction": round(rig.engagement / math.pi, 4)}
    elif which == "planetary":
        rig = library.Planetary()
        parts = rig.parts()
        # sweep one turn of the carrier, not the sun, so the loop closes
        # without a jump at the wrap
        span = rig.turns()
        placements = [rig.place_at(span * i / frames)
                      for i in range(frames)]
        reach = rig.reach()
        notes = {"sun_teeth": rig.sun_teeth, "planet_teeth": rig.planet_teeth,
                 "ring_teeth": rig.ring_teeth, "planets": rig.count,
                 "centres_mm": rig.centres,
                 "reduction": round(rig.spec["ratio"], 6),
                 "one_sun_turn_moves_the_carrier_deg":
                     round(math.degrees(rig.carrier_angle(2 * math.pi)), 4)}
    else:
        make = {"four_bar": library.four_bar,
                "slider_crank": library.slider_crank,
                "scotch_yoke": library.scotch_yoke}.get(which)
        if make is None:
            raise SystemExit(f"no mechanism called {which!r}")
        rig = make()
        # `sweep` returns a closed cycle; drop the repeated last pose for
        # looped playback
        joints = rig.sweep(frames=frames)[:-1]
        parts = rig.parts
        placements = [rig.place(j) for j in joints]
        reach = rig.reach()
        notes = {"links_mm": rig.link_lengths(), "joints": len(rig.joints),
                 "solved_with": "planegcs, one solve per frame, each seeded "
                                "from the last"}
    return {"mechanism": which, "frames": frames, "reach_mm": reach,
            "notes": notes,
            "parts": [{"name": p.name, "colour": list(p.colour),
                       "document": p.document, "parameters": p.parameters,
                       **_mesh(p, deflection)} for p in parts],
            "motion": _frames(placements)}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    which = argv[0] if argv else "four_bar"
    out = argv[1] if len(argv) > 1 else os.path.join(REPO, "build", f"{which}.json")
    frames = int(argv[2]) if len(argv) > 2 else 72
    scene = build(which, frames)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(scene, handle)
    total = sum(len(p["triangles"]) for p in scene["parts"])
    print(f"{which}: {len(scene['parts'])} parts, {total} triangles, "
          f"{scene['frames']} frames -> {out}")
    for key, value in scene["notes"].items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
