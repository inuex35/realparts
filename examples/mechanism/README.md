# examples/mechanism

The part documents behind the mechanism examples in
[docs/mechanisms.md](../../docs/mechanisms.md). The kinematics are solved by
`cadcore/mechanism/`; these files are the bodies it places at each frame.

| file | what it is |
|---|---|
| `link.json` | one bar of a linkage: a stadium with a hole at each end. Every bar of every linkage is this document with its own length |
| `pin.json` | the roller that rides in a yoke slot or a Geneva slot |
| `piston.json` | a slider that keeps its heading and moves only along the bore |
| `yoke.json` | the Scotch yoke plate, slotted square to its travel |
| `geneva_driver.json` | the driving half of a Geneva mechanism: hub, arm and pin |
| `geneva_wheel.json` | the driven wheel; every dimension follows from the slot count |

Render a mechanism in motion with `tools/render/render_mechanism.py` or
`render_motion.py`.
