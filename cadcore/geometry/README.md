# geometry

The only layer that calls OpenCASCADE. Every operation takes and returns a
`Body`: an OCCT shape plus a table of face names, aliases and dropped
names. Nothing above this layer sees a `TopoDS_Face` or a face index.

## Where to start

`kernel.py` is the façade `features/` calls; it imports the other modules
and implements nothing itself. Under it:

* `core/naming.py` -- `Body`, face info, edge tables.
* `core/measure.py` and `core/query.py` -- frames, sizes, selectors,
  distance, mass, curvature.
* `core/sketch2d.py` -- text outlines and outline offsets as sketch loops.
* `solids/` -- primitives, sweeps, booleans, split and section, emboss,
  fillet/shell/draft, transforms, surfaces, threads, sheet metal.
* `assembly/` -- mates and the solved placement.
* `io/` -- STEP/IGES/BREP, meshes, tessellation.
* `solids/sentry.py` -- the second process that tries a risky operation
  first.

## What it keeps

**Names follow history.** Each operation walks OCCT's Modified/Generated
map and carries the parent face's name to its children:

    Body in                          cut(body, tool)                 Body out
    ┌──────────────────────┐                                  ┌─────────────────────────┐
    │ shape                │   OCCT does the boolean and      │ shape                   │
    │ names:               │   hands back, for every face     │ names:                  │
    │   plate/+z  → face A │   of the input, the faces it     │   plate/+z@0  → face A' │
    │   plate/+x  → face B │   became (Modified) or made      │   plate/+z@1  → face A''│
    │   ...                │   (Generated)                    │   plate/+x    → face B' │
    │ aliases: {}          │ ───────────────────────────────► │   hole/bore   → face C  │
    │ dropped: []          │   a face that survives keeps     │ aliases:                │
    │ notes: {bends: ...}  │   its name; one split in two     │   {old/name: survivor}  │
    └──────────────────────┘   becomes @0 and @1; the tool's  │ dropped: [plate/-y]     │
                               new faces get the feature's    │ notes: carried over     │
                               own names (hole/bore)          └─────────────────────────┘

* A face welded away by a boolean becomes an alias of the survivor. A face
  split in two becomes `@0` and `@1`, ordered by position, so the same piece
  has the same name after a rebuild. A mirror turns `+x` into `-x`. A
  fillet's faces are numbered by the edge each one rounds.
* An edge has no entry: it is named on demand by its two faces.
* A face that goes says so (`dropped`), and a body with a face nobody named
  is refused rather than drawn with a hole. `Body.rename` is the one way to
  replace a name in place.
* **Direction roles are measured**, not read off the orientation flag,
  which a transform can leave pointing the wrong way.
* **Every operation states what its result must be** (`core/promises.py`):
  a cut makes the target smaller, a fuse never does. Size is checked as the
  operation returns; validity once per build.
* **A body's notes** -- bends, patch fits, what mates left free -- are
  carried through every operation, and a note declares how two of it merge
  (`core/provenance.py`).
* **Fillet, chamfer, shell and defeature are tried in a second process
  first** (`solids/sentry.py`), because OCCT can segfault rather than
  refuse. `CAD_SENTRY=0` turns it off.

Imports `model/`, `sketching/` and the top-level modules; never
`evaluation/` or `ops/`. Why each of these rules exists, and what went
wrong before it did, is in [docs/decisions.md](../../docs/decisions.md).
