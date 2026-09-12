# model

The document layer: reads, writes and validates the JSON file that describes
a part. It is the only layer that knows the file's shape, and it imports
nothing that imports OpenCASCADE, so a document can be read without the
geometry kernel installed.

## Where to start

`document.py`: `Document` and `Feature`; load, save, expressions, the
envelope each parameter may take, unit conversion. `authoring.py` is an
edit before it is applied; `requirements.py` the quantities a requirement
may name; `fasteners.py` metric screw holes by designation.

## What it keeps

* **Expressions are evaluated, not executed.** `"width/2"` is parsed into a
  syntax tree and walked: arithmetic, comparisons, a fixed list of maths
  functions. Overflow and very long numbers are refused.
* **The file round-trips.** The kernel works in millimetres; a document is
  read and written in its own unit and comes back byte for byte on the
  second save. Unknown keys are kept. Saves land whole or not at all.
* **A document with the wrong shape is refused by kind** -- a missing id,
  an id with a separator in it, parameters that are a list -- before any
  feature is read.
* **What a checkpoint restores is a named list** (`Document.CONTENT`); the
  session's own fields (`source`, `fence`, `revision`) stay. A new field has
  to say which it is.
* **Requirements are status, not gates**: measured on every build, reported,
  never blocking.

Imports `features/` only inside functions, to ask what a feature takes when
it converts units. Why: [docs/decisions.md](../../docs/decisions.md).
