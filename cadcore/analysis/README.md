# analysis

Read-only questions asked of a built body: a drawing, a printability check,
a mesh file, a preview image, a parameter search, the requirements measured.
They change nothing and are called from `ops/`.

## Where to start

* `drawing.py` -- hidden-line views, dimensions by face name, SVG and DXF.
* `printability.py` -- overhangs, thin walls, small holes, by face.
* `moulding.py` -- draft per face and undercuts for a pull direction.
* `meshio.py` -- STL, 3MF and OBJ.
* `preview.py` -- a PNG without Blender.
* `optimise.py` -- a parameter search over the declared envelope.
* `requirements.py` -- each requirement row measured on the built part.

## What it keeps

* **Answers name faces**, so a drawing is regenerated after a change rather
  than redrawn and a printability finding can be picked and fixed.
* **A design whose study did not converge is not feasible** to the search.
* This layer still reaches OpenCASCADE directly for hidden-line removal and
  ray casts; the layer test lists it as an exception with that reason.
