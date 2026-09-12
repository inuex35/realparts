# simulation

FEM studies declared in the document and attached to CAD face names: fix
`plate/-z`, load `plate/+z` with 200 N, require `safety_factor_p95 >= 2`.
The Netgen mesh carries the face names as boundary names, so a load lands
on the intended face after every rebuild.

Uses Netgen/NGSolve and SciPy from `requirements-sim.txt`. Every import of
this package from elsewhere in `cadcore` is inside a function, so a checkout
that never runs a study never loads them; a study asked for without them is
a `missing_dependency` refusal.

## Where to start

* `study.py` -- runs the studies a document declares.
* `spec.py` -- says what a study may declare.
* `mesh.py` -- makes the Netgen mesh with boundary names.
* `studies/` -- the four types (structural, modal, thermal, buckling).
* `convergence.py` -- refines.
* `field.py` -- the solved field for display.

## What it keeps

* **Two safety factors.** The peak stress at a clamped face is a
  singularity of the model; `p95_von_mises` is the stress 95% of the
  material lies below. Both are reported.
* **A study that does not converge does not pass.**
* **Study keys are declared**; a misspelt one is refused by name.
* **A floating piece is refused** before anything is solved.
* **One boundary name is one face.**

Scope: linear elastic, clamped faces, loads spread over a face, parts
solved alone. A first-pass strength check. Why:
[docs/decisions.md](../../docs/decisions.md).
