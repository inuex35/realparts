# Mechanisms

[← README](../README.md)

The six of them, playing:
**[claude.ai/code/artifact/728179f9](https://claude.ai/code/artifact/728179f9-e356-41f6-8dce-474d3aae5e32)**

`cadcore/mechanism/` is a layer above the geometry, not part of it, and it exists
to ask the CAD a question the parts alone cannot: **does this still work when a
length changes**. Four classic linkages are built from `cadcore` documents and
swept through a cycle -- a four-bar, a slider-crank, a Scotch yoke and a Geneva
wheel.

The first version wrote the kinematics out by hand, one mechanism at a time.
That was the wrong shape for this repository, because a planar mechanism *is* a
two-dimensional constraint system and there is already a solver here for those.
So a mechanism is a **declaration** and the sketch solver does the work:

```python
four_bar = Rig(
    loop=["a", "b", "c", "d"],
    links={"crank": ("a", "b"), "coupler": ("b", "c"),
           "rocker": ("c", "d"), "frame": ("d", "a")},
    anchors={"a": (0, 0), "d": (ground, 0)},
    driver=("a", "b"))
```

Joints are points, links are distances, a fixed pivot is a `fix`, a slider is a
line something has to stay on, and the input is an `angle`. All of those are
constraints the sketcher already had, so a mechanism that will not assemble is
refused by the same code, with the same kind of error, as a sketch that will
not -- and the solver counting the degrees of freedom is what says "this is a
mechanism" rather than anyone asserting it. Every frame of every sweep here
solves at **zero** degrees of freedom.

Two things the constraint system does not do by itself, both stated rather than
hidden:

* **Which assembly it is.** A four-bar closes two ways, elbow up and elbow
  down. Solved afresh at each angle it flips between them, which looks exactly
  like the mechanism coming apart -- measured, the coupler pin jumps tens of
  millimetres between two frames five degrees apart. Each frame is therefore
  seeded from the one before, which is continuation, and it is what makes a
  swept mechanism one motion instead of a series of unrelated solutions.
* **Engagement.** A Geneva wheel is not one constraint system: the pin is in a
  slot for part of the turn and the wheel is locked for the rest, and the
  equations differ. That switch is written down.

The kinematics is not implemented, so the textbook closed forms are free to be
the *check* instead: a slider-crank's piston is `r cos t + sqrt(l^2 - r^2 sin^2
t)` and a Scotch yoke's is `r cos t`, and the tests hold the solver to those to
a micron. A four-bar's bars are checked for length at every joint of every
frame, and a full turn has to come back where it started.

Every bar is the same `examples/mechanism/link.json` at its own centre
distance, so a longer rocker is a longer *part* and not just a longer arrow.
The Geneva wheel goes further and works its own proportions out from the slot
count: the pin has to enter and leave along its slot, which makes the crank
square to the wheel's radius at both ends, so the centre distance and the wheel
radius fall out of that right-angled triangle -- three to eight slots all build.

### Gears

The fifth is a planetary train, and it is a different kind of work from the
other four: the motion is only ratios, and all the difficulty is in the tooth.

An involute is the curve a taut string traces off a circle, and gears use it
because two of them mesh at a constant ratio however far their centres drift.
That is worth generating properly rather than drawing something tooth-looking,
because **a wrong flank still turns in a picture**. So none of the gear tests
look at the shape. They put two gears at the centre distance their tooth
counts demand and measure how much of one is inside the other:

| planet phase | overlap |
|---|---|
| meshed | 0.000 mm³ |
| half a tooth pitch out | 114.8 mm³ |

and one tooth of that planet is 113 mm³ — so at the wrong phase a whole tooth
is inside the other gear, and at the right one nothing is. Anything that is
not an involute fails one of those two. The whole three-planet train is held
to the same test right round a turn, where the worst figure is 0.03 mm³:
that residue is the seven-point polyline standing in for the curve, not a
clash.

Two rules decide whether a train exists at all, and both are refused rather
than drawn wrong: the planets have to fit between sun and ring, so
`ring = sun + 2 × planet`, and they only space evenly when their number
divides `sun + ring`. The motion after that is Willis and nothing else — with
the ring held, 18/15/48 teeth give a reduction of 3.667, and one turn of the
sun moves the carrier 98.18°.

The profile is computed in `cadcore/mechanism/gears.py` rather than written as
expressions in a document. It *could* be written as expressions — the document
language has `sqrt`, `atan` and `pi`, which is all an involute needs — but
each point would be a six-hundred-character line and twenty-eight of those is
not a document anybody can edit. The right home is a `gear` feature in the
kernel, beside `hole` and `thread`, and that is not done.

```
python -m cadcore.mechanism.scene four_bar build/four_bar.json 72
blender -b -noaudio --factory-startup -P tools/render/render_mechanism.py \
    -- build/four_bar.json build/four_bar_frames
python tools/render/stitch.py build/four_bar_frames build/four_bar.gif
```

The kernel writes the meshes and the motion into one file and Blender reads
that, the same split the add-on uses. The frames are stitched outside Blender
because this build has no FFMPEG.

### Two kinds of animation, and why they are not the same

Those five build their parts once and move them, which is right: a linkage's
bars do not change shape while it runs. `render_morph.py` does the other
thing -- the rocker grows from 90 mm to 150 mm and back while the crank turns
twice, so the linkage is re-solved *and the bar is rebuilt from the document*
on every frame. What is on screen is a solid that did not exist in the frame
before.

That one runs the kernel **inside Blender**, which turns out to be possible:
Blender 5.3's Python is 3.13 and both `cadquery-ocp` and `planegcs` publish
wheels for it, so OCCT and the sketch solver import into the same interpreter
as `bpy`. Measured there, a small part costs **26 ms** to rebuild and re-mesh,
against 41.7 ms for a frame at 24 fps.

Which is the whole design in one number:

| | cost per frame | so |
|---|---|---|
| rigid motion | a transform | real time |
| shape change | 26 ms for a link, seconds for a gear | baked, not scrubbed |

Being able to run in-process does not mean everything should. An OCCT boolean
that crashes in there takes the session and the user's unsaved work with it,
which is the reason the modelling side still talks to a child process -- not
the licence, which was never the obstacle: OCCT is LGPL with an exception and
this is BSD, and both are GPL-compatible.
