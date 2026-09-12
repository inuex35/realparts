# bench/specs

One JSON file per benchmark task. Each has:

* `id`: the name results are filed under;
* `prompt`: the part described in plain English, exactly as both arms
  receive it;
* `checks`: what `bench/check.py` measures on the resulting STEP file, such
  as being one solid, the bounding box, mass, hole count, printability, or a
  safety factor from a study;
* `also` (optional): a second prompt sent after the first, for tasks that
  test an edit.

| spec | task |
|---|---|
| `plate_4_holes.json` | a rectangular plate with four through holes |
| `plate_then_edit.json` | the same plate, then a change to it in a second step |
| `l_bracket.json` | an L bracket from 6 mm plate |
| `flanged_bushing.json` | a bushing with a bore and a flange |
| `shaft_keyway.json` | a round shaft with a keyway open at one end |
| `enclosure_shell.json` | an open-topped box with 2 mm walls |
| `strength_driven_hole.json` | a cantilever tab in 6061 aluminium under 500 N, where the thickness has to be chosen to meet a safety factor |

Checks describe the part, never how it was built: bounding boxes are
compared as sorted extents, and the strength check finds the fixed and
loaded faces by geometry, not by name.
