# features

The feature types a document may use -- box, extrude, fillet, hole, pattern,
mate and the rest, listed by `feature_types`. Each is one function with one
declaration:

```python
@feature("groove", category="modify", args={
    "body": Ref(), "face": Name(), "depth": Number(),
    "at": Number(required=False, default=0.0)})
def groove(graph, f, a, ev) -> Body:
    return kernel.cut(f.id, graph.body_of(a["body"]), ring(f.id, a, ev))
```

## Where to start

`declare/registry.py` and `declare/schema.py` say what a feature type is and
what argument kinds exist. The types are in `families/`, one file per
family: solids, booleans, modify, patterns, datums, surfaces, assembly,
exchange; `extents.py` is extents given as a relationship (`until: next`).

## What it keeps

* **The declaration is the one source** for validation, the dependency
  graph (`Ref()` arguments are edges), the catalogue an assistant reads and
  the fields the sidebar draws. A test checks every type declares its
  arguments.
* **A feature calls the geometry façade and nothing below it**, so names
  are always carried; OpenCASCADE is not imported here.
* **An argument the declaration lets a document leave out, read anyway**,
  is a `missing_argument` naming it.
* **Each result has a stated kind** (solid, surface, datum, assembly), and
  the document's result is the last feature that produces a body.

Why: [docs/decisions.md](../../docs/decisions.md).
