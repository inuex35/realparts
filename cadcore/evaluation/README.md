# evaluation

Builds a document. The features form a dependency graph (each `Ref()`
argument is an edge); `graph.py` evaluates it in order with a content-hash
cache per node, so an edit near the end of a long history rebuilds only
what depends on it.

## Where to start

`api.py` is the entry point: `build`, `describe`, `export_step`. `graph.py`
is the `Evaluator`: ordering, the per-node key, `body_of`, `sketch_of`,
`frame_of`.

    parameters: width, hole_d, corner_r

      plate    box      size = [width, depth, thickness]
        │
        ▼
      bolt     hole     body = plate, diameter = hole_d
        │
        ▼
      rounded  fillet   body = bolt, radius = corner_r

      key(plate)   = hash(plate's arguments)
      key(bolt)    = hash(bolt's arguments, {"body": key(plate)})
      key(rounded) = hash(rounded's arguments, {"body": key(bolt)})

      change corner_r   only key(rounded) moves      1 feature rebuilt
      change width      every key moves              a cold build

## What it keeps

* **The key tells a name from a value**, so a sketch point and a parameter
  with the same name never share an entry; **each parent's key is filed
  under the argument it sits in**, so the same parents in another role are
  another key; **a file a feature imports is keyed by its stamp**.
* Rolling the history back to a feature is "build its ancestors", so it
  costs nothing extra.
* A document with no body yet is built and reported as under construction;
  one whose last feature makes no body is refused with `no_solid`.
* A sub-document a part brings in gets the same fence as its parent.

`tests/golden.json` records what every shipped example measures;
`python cadcore/evaluation/tests/golden.py --write` accepts a deliberate
change (say what moved and why in the commit).
