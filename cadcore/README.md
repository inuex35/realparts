# cadcore

The CAD kernel: a Python package that turns a JSON document into a solid
with OpenCASCADE and answers questions about it (mesh, STEP, drawing,
strength, printability, interference). The Blender add-on, the command
line and the MCP server are thin clients of it.

## Where to start

`cadcore/ops/session.py` -- every operation is an `op_*` method on the
`Session`, and every edit goes through its `_edit()` guard. From there,
down the layers:

    ┌─────────────────────────────────────────────────────────────────────┐
    │ service/     server.py · mcp.py · cli.py · resources.py             │ transports, no modelling
    ├─────────────────────────────────────────────────────────────────────┤
    │ ops/         Session: one open document; every edit inside _edit()  │ atomic edits, undo, the fence
    ├─────────────────────────────────────────────────────────────────────┤
    │ analysis/    drawing, printability, mesh files, preview, optimise   │ read-only questions
    ├─────────────────────────────────────────────────────────────────────┤
    │ mechanism/   linkages and gears, solved as sketches                 │
    ├─────────────────────────────────────────────────────────────────────┤
    │ simulation/  FEM studies with Netgen/NGSolve                        │ optional install
    ├─────────────────────────────────────────────────────────────────────┤
    │ evaluation/  the feature graph: build in order, cache per node      │
    ├─────────────────────────────────────────────────────────────────────┤
    │ features/    one declared function per feature type                 │
    ├─────────────────────────────────────────────────────────────────────┤
    │ geometry/    every OpenCASCADE call; a Body = shape + face names    │ nothing below sees OCCT
    ├─────────────────────────────────────────────────────────────────────┤
    │ sketching/   2D profiles with named segments (PlaneGCS)             │
    ├─────────────────────────────────────────────────────────────────────┤
    │ model/       the JSON document: parameters, features, requirements  │ never imports geometry
    ├─────────────────────────────────────────────────────────────────────┤
    │ errors.py · names.py · progress.py                                  │ no dependencies
    └─────────────────────────────────────────────────────────────────────┘

Each directory is a layer with a README of its own:
[model](model/README.md) · [sketching](sketching/README.md) ·
[geometry](geometry/README.md) · [features](features/README.md) ·
[evaluation](evaluation/README.md) · [simulation](simulation/README.md) ·
[mechanism](mechanism/README.md) · [analysis](analysis/README.md) ·
[ops](ops/README.md) · [service](service/README.md). What happens to one
request, top to bottom, is drawn in
[docs/architecture.md](../docs/architecture.md#what-happens-to-one-request).

## What it keeps

* **A reference to a face or an edge keeps meaning the same thing after
  the model changes.** Every face is named by the feature that made it and
  its role, every edge by its two faces, and the names are carried through
  every operation. Documents refer to geometry only by these names.

      base:plate/+z@0~2
      │    │     │  │ └─ ~2   the third copy a pattern made
      │    │     │  └─── @0   the first piece, after a boolean split the face
      │    │     └────── +z   the role the feature gave the face
      │    └──────────── plate  the feature that made the face
      └───────────────── base:  the part it belongs to, inside an assembly

      plate/+z|plate/+x      an edge: the two faces it lies between
      skin/side|open         the border of an open shell (one face, not two)

  `names.py` is the only file that spells a name; a test reads the syntax
  tree of everything else for one spelled by hand. A feature id may not
  contain a separator.
* **A layer imports only what is below it**, at module level;
  `tests/test_architecture.py` checks it from the import graph. Two
  exceptions, on purpose: `simulation/` is imported inside functions so a
  checkout without Netgen runs, and `model/` asks `features/` what a
  feature takes, inside a function, when it converts units.
* **Every refusal is a `CadError` with a kind** from the list in
  `errors.py`, and every kind is raised somewhere (`tests/test_errors.py`).
* **Every edit is all-or-nothing**, inside `ops`' guard.

Each layer's tests are in its own `tests/`; `cadcore/tests/` holds the
tests about the whole package. The reasons behind these rules are in
[docs/decisions.md](../docs/decisions.md).
