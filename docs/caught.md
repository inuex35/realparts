# What the checks caught

[← docs](README.md)

The project keeps three checks that try to break its central claim and runs
them in CI: fuzz and soak ([service/README.md](../cadcore/service/README.md))
and golden ([evaluation/README.md](../cadcore/evaluation/README.md)).
This page is the argument for having them: one line per bug, because the list
is not the point. None of these were visible until the right kind of pressure
was applied, and most of them passed a full test suite while being wrong.

**Names that stopped meaning the same thing**

- a lost reference resolved to a **different hole of the same diameter** — cylinders were matched on radius alone
- a hole's cap was named for the direction it faced *in the world*: `floor` when drilled down, `-x` when drilled sideways
- `a|b#2` is ambiguous as text, because a face can be called `b#2` itself

**Failures that reported success**

- a thread left the part as **two solids**, and every boolean after it returned a negative volume and said it had worked
- `{"betwen": [...]}` in an edge query applied *no filter at all*: it rounded every edge on the body and reported it as a success
- two documents naming the same `in.step` got each other's geometry — a 10 mm cube and a 40 mm cube assembled to 2000 mm³ instead of 65000
- a study whose mesh had not converged still passed its requirements

**Work that reported success**

- a `shell` that **hollowed nothing and said `IsDone`** -- fillet a face, ask to open that face, and OCCT hands back the solid it was given. A 95 x 64 x 24 box came out solid, at five times the weight, and passed every check that only asks whether the build threw

**Arithmetic that was quietly wrong**

- the thread helix read the length of a *normalised* direction, which is 1.0 by construction, so every thread stopped short of its own end
- every flange bulged one thickness proud of the face it was bent from — and a test asserted the bulge
- the top view was the part seen from *below*, so a boss on the top face came out in dashed hidden lines
- **saving a document written in inches multiplied it by 25.4**, and again on every save after that

**What an outside review caught, reading the code against its own promises**

- the HTTP server answered any page a browser had open: no Origin or Host check, and `text/plain` needed no preflight
- the add-on's bridge fenced paths by their extension, so a save to a path without one wrote anywhere
- a refused edit put the document back and kept the evaluator built on the edited one, so the next read answered for a state nobody had
- a line with a 5,000-digit integer killed the kernel: a `ValueError` that was not a `JSONDecodeError`
- a circular pattern copied a face called `+x` facing `+y`; `move_face` reported the moved face dead; `delete_face` by an alias deleted nothing
- glTF went out in millimetres, Z up, into viewers that read metres, Y up
- an `until` to a tilted plane built a flat post to the plane's origin; a rib measured its `until` along the wrong axis
- a hinge's `min` and `max` are degrees and were multiplied by 25.4 in an inch document
- a ring drawn as two arcs chose its hole as the outline: the area was that of the arc ends
- the bottom view was the third-angle bottom view turned through 180°
- from every GUI, Draft From Here always failed: the parting plane lay in the picked face itself

**Motion that was wrong in a way a still cannot show**

- the walking foot **left the ground for half its own stance**: hip swung, knee held straight, so the foot travelled a circle and rose 9 mm at each end of the step
- the document's note had the knee backwards -- a mate turns about the *target* axis, so `knee` is the shin's angle from vertical, not from the thigh, and the gait had believed the note

**Parts sharing space**

- the walker's front and back feet **swung through each other**: the assembly never passed `hip_dx`, so the chassis kept its own default and bunched four hips 110 mm apart in the middle of a 266 mm plate
- every knee had the femur and the tibia in the *same plane* -- a concentric mate makes the bores coaxial and nothing else, and a lap joint is offset by a thickness. 2.6 cm3 of shared metal, in every pose, which is why it never looked wrong

**Rendering**

- the ground stopped a few centimetres behind the robot and everything past it was flat grey -- the camera's far clip plane, in a model measured in millimetres, at Blender's default
- EEVEE does not fail without a display under `-b`; it *hangs*, and an empty 160x160 frame never came back

**The add-on**

- it could not save. Thirty-four operators, `open` among them, and no `save` — while `op_save` sat in the kernel with nothing calling it
- the panel looked the same whatever was selected, so every tool's precondition was discoverable only by pressing the button and reading the refusal
- `9**9**9` in a parameter never returns, which through the add-on is the interface frozen until somebody kills the kernel

The reasoning behind each fix is in the commit that made it; this is the
argument for having the checks, not a changelog.
