# examples/quadruped

A four-legged walker sized to a Sony aibo ERS-1000. `robot.json` is the
assembly: the chassis plus four legs, each a thigh and a shin held by
concentric mates whose angles are parameters. A gait is a sequence of those
eight angles, and the assembly is rebuilt at every frame.

| file | what it is |
|---|---|
| `robot.json` | the assembly: hips, knees, feet, and the mates that pose them |
| `parts/body.json` | the chassis, with a hip pin at each corner and the head |

The walk itself (foot path to joint angles) is computed by
`tools/render/gait.py`, and `tools/render/render_quadruped.py` renders it.
The full write-up is [docs/quadruped.md](../../docs/quadruped.md).
