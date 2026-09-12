# mechanism

Planar mechanisms -- four-bar, slider-crank, Scotch yoke, Geneva wheel,
planetary gears -- as 2D constraint systems solved by `sketching/`. Joints
are points, links are distances, a fixed pivot is a `fix`, the input is an
angle.

## Where to start

`__init__.py`: `Rig`, `Placement`, solving and continuation. `library.py`
holds the mechanisms, `gears.py` the involute and the planetary rules,
`scene.py` a mechanism as drawable geometry.

    FOUR_BAR = Rig(
        joints={"a": (0, 0), "b": (40, 0), "c": (105, 89), "d": (120, 0)},
        loop=["a", "b", "c", "d"],
        anchors={"a": (0, 0), "d": ("ground", 0)},
        links={"crank": ("a", "b"), "coupler": ("b", "c"), "rocker": ("c", "d")},
        driver=("a", "b"))

## What it keeps

* **A rig that does not close, or still has freedom with the input held, is
  refused** with a typed reason, like an under-constrained sketch.
* **Continuation**: each frame is seeded from the previous solution, so a
  sweep stays on one branch of a closed chain.
* **Engagement is declared**, not solved.
* **Gear assembly rules are refused by name** with the failing numbers.

Sits above `evaluation/`: the bodies a rig names are documents it builds.
Nothing here imports Blender or OCCT.
