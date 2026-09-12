# examples

Worked documents. Each is a complete part or assembly in the JSON format
the kernel reads, and each is built by the test suite and recorded in the
golden file (`cadcore/evaluation/tests/golden.json`), so they always
reflect what the current kernel produces. They also ship inside the Python
package and are served to assistants as `cad://example/*` resources.

Build any of them with `python -m cadcore build examples/<name>.json --names`.

## Parts

| file | what it is | exercises |
|---|---|---|
| `bracket.json` | camera bracket: plate, wall, bore, four M6 holes, fillets | the basic vocabulary; a static study; a drawing; requirements |
| `flange.json` | bolted flange | revolve, bolt circle pattern, chamfer; a drawing |
| `cup.json` | cup with a swept handle and a heavier floor | revolve, shell with per-face thickness, sweep; a study |
| `bottle.json` | threaded bottle | revolve, shell through an open face, real thread geometry |
| `fairing.json` | aerofoil skin lofted between two sections | surface modelling, thicken |
| `sketch_plate.json` | a plate from a constrained sketch | sketch, constraints, extrude, fillet |
| `sheet_bracket.json` | sheet metal L bracket | sheet, flange, holes; the flat pattern |
| `sheet_knob.json`, `sheet_collar.json`, `sheet_clip.json` | the two halves of a tarp clip, and the two assembled under load | sheet metal in an assembly |

## Assemblies

| file | what it is |
|---|---|
| `assembly.json` | bracket and bush placed by mates applied in order; the bush's diameter is driven by the bore it fits |
| `assembly_solved.json` | the same two parts, with the mates solved together, reporting the freedom left |
| `parts/bush.json` | the bush the assemblies place |
| `quadruped/` | a four-legged walker with eight mate angles; see [docs/quadruped.md](../docs/quadruped.md) |
| `mechanism/` | the parts of the linkage and Geneva examples; see [docs/mechanisms.md](../docs/mechanisms.md) |

More detail on several of these is in [docs/examples.md](../docs/examples.md).
