# cadcore/geometry/tests

Tests for the OpenCASCADE layer. `data/` holds STEP fixtures from other kernels.

| file | covers |
|---|---|
| `test_core.py` | names survive rebuilds and booleans; refusals carry a kind |
| `test_mirror.py` | a mirrored body has outward normals, true roles, and holes that are still holes |
| `test_placement.py` | the frame of a named face for sketching on it, and that it follows the face |
| `test_promises.py` | what fillet, cut, shell and the rest promise about their result |
| `test_provenance.py` | what a body records about itself, and the one declared rule for merging two records |
| `test_sentry.py` | an OCCT crash in the child process becomes a typed refusal here |
| `test_sheet.py` | sheet metal: constant thickness, bends recorded, the flat pattern computed from them |
| `test_surfaces.py` | surface features and the ways back to a solid |
| `test_threads.py` | thread geometry is real: length, pitch, handedness |
| `test_imported.py` | STEP and IGES imports get stable names and survive the same operations |
| `test_assembly.py` | parts mated by face name, and interference between them |
| `test_assembly_solver.py` | mates solved together: placement, remaining freedom, refusals |

Run with `python -m pytest cadcore/geometry/tests`. Fixtures shared by every test (an example on disk, an open session) are in the repository's `conftest.py`.
