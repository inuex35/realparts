# Simulation

[← README](../README.md)

Studies are declared in the same document and attach to **CAD face names**, so
they keep pointing at the right face after a parameter change. Four kinds:

```json
"studies": [
  {"id": "mount_load", "type": "static_structural", "material": "A6061",
   "fix": ["h1/side", "h2/side"], "loads": [{"face": "wall/+y", "force": [0, -500, 0]}],
   "convergence": {"levels": 3, "ratio": 1.5, "tolerance": 0.05},
   "require": {"safety_factor": ">= 2", "max_displacement": "<= 0.5"}},
  {"id": "shake", "type": "modal", "fix": ["h1/side"], "modes": 4,
   "require": {"fundamental_Hz": ">= 400"}},
  {"id": "warm", "type": "thermal", "temperatures": [{"face": "wall/+y", "value": 85}],
   "convection": [{"face": "plate/-z@0", "film": 2.5e-5, "ambient": 20}],
   "fix": ["h1/side"], "require": {"max_temperature": "<= 90"}},
  {"id": "column", "type": "buckling", "fix": ["bar/-x"],
   "loads": [{"face": "bar/+x", "force": [-1000, 0, 0]}]}
]
```

Mesher and solver are Netgen/NGSolve (LGPL, in-process). Each of the four is
checked against a closed-form answer in the test suite: a cantilever's tip
deflection within 2% of Euler–Bernoulli and its root stress within 10% of
M c / I, the same beam's first two modes within 3%, conduction end to end along
a bar (and its free expansion within 10%), a column's buckling factor within 5%
of Euler. Contact, plasticity and explicit dynamics are *not* here; CalculiX can
be added later as a subprocess without changing the interface.

Two stresses come back. The **peak** is the largest von Mises value anywhere,
and at a clamped edge it is a singularity of the model that grows with every
refinement. **p95** is the stress that 95% of the material, by volume, lies
below: each element counts by its volume, so the number does not move when the
mesh is denser in one place than another. It is a property of the part, not of
the one node at the clamp.

p95 is far below the peak -- on the bracket's thermal study, 5.6 MPa against a
peak of 260 at the bolted hole. A `p95_von_mises` limit is therefore a check on
the bulk of the part, and its number has to be set from what the part actually
reads, not from the material's yield. Set it where a design you would reject
would cross it.

### Mesh convergence

A stress from a single mesh is not a result. Declaring a `convergence` block on
a **static** study re-solves on a refined sequence and reports whether the peak
has settled. Only the static study reads it; a modal, thermal or buckling study
that is given one is refused by name, rather than accepting the block, ignoring
it, and reporting that its answer settled.

    mesh converged (tol 5%)
      h= 5.00    15654 el  peak   50.828  p95   11.182 MPa
      h= 3.33    18700 el  peak   50.385  p95   11.425 MPa  change +0.9%  not a refinement
      h= 2.22    35300 el  peak   49.870  p95   11.763 MPa  change +1.0%

The judgement is made on the **peak** on purpose: when the peak refuses to
settle, the model has a singularity, and that is what the engineer needs to hear.
A level counts only if its unknowns grew by at least 30% over the one before:
Netgen's own size limits can hand back nearly the same mesh for a smaller
`mesh_size`, and a small change between two copies of one mesh says nothing.
The second level above is such a copy, so the verdict rests on the third.
Clamping the bracket's whole underside does exactly that (+74% per refinement);
constraining the four bolt holes instead — what the part really does — converges.
