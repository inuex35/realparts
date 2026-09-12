# sketching

2D profiles with named segments, solved by PlaneGCS (FreeCAD's constraint
solver). A segment called `right` becomes a face called `profile/right`
when a feature sweeps the sketch.

## Where to start

`__init__.py`: `solve()` runs PlaneGCS (lines, arcs, circles, ellipses,
splines), reports freedom, refuses or returns a `SketchResult`. The rest:

* `constraints.py` -- the constraint registry.
* `derived.py` -- offsets, trims and slots.
* `loops.py` -- which loop is the outline.
* `autodim.py` -- a drawn sketch turned into constraints and dimensions.
* `files.py` -- a DXF or SVG read into the sketch language.

Text and a whole-outline offset need OpenCASCADE, so the `sketch` feature
adds those loops after the solve.

## What it keeps

* **An under-constrained sketch is refused** unless the document says it may
  move; remaining freedom, conflicting and redundant constraints are
  reported.
* **Constraints are a registry**: the list of supported constraints and the
  implementation are the same object, and a constraint missing what its
  kind reads is refused by index.
* **A drawn sketch is dimensioned one constraint at a time** while freedom
  remains, so it comes out fully determined.

Sits below `geometry/`: a sketch is 2D until a feature sweeps it. Why:
[docs/decisions.md](../../docs/decisions.md).
