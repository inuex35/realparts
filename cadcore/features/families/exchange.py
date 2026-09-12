"""Reading geometry someone else made."""
from __future__ import annotations

from ...geometry import kernel
from ...geometry.core.naming import Body
from ..declare.registry import feature
from ..declare.schema import Flag, Number, Path

HEAL = {"heal": Flag(note="repair the shape first: sew gaps, close open shells"),
        "tolerance": Number(required=False, note="the gap healing may close; 0.001 by default")}


@feature("import_step", category="input", args={
    "path": Path(required=True, note="a file, relative to the document"), **HEAL})
def import_step(graph, f, a, ev) -> Body:
    """Read a STEP file, naming its faces from their own geometry; colours and materials come too."""
    return _read(kernel.import_step, graph, f, a, ev)


def _read(reader, graph, f, a, ev) -> Body:
    return reader(f.id, graph.doc.resolve(a["path"]), bool(a.get("heal")),
                  ev(a["tolerance"]) if a.get("tolerance") else None)


@feature("import_iges", category="input", args={
    "path": Path(required=True, note="a file, relative to the document"), **HEAL})
def import_iges(graph, f, a, ev) -> Body:
    """Read an IGES file the same way."""
    return _read(kernel.import_iges, graph, f, a, ev)


@feature("import_brep", category="input", args={
    "path": Path(required=True, note="an OCCT .brep file, relative to the document")})
def import_brep(graph, f, a, ev) -> Body:
    """Read OCCT's own .brep format."""
    return kernel.import_brep(f.id, graph.doc.resolve(a["path"]))


@feature("import_mesh", category="input", args={
    "path": Path(required=True, note="an .stl, .obj, .3mf, .gltf or .glb file"),
    "unify": Flag(default=True, note="merge coplanar facets into one face")})
def import_mesh(graph, f, a, ev) -> Body:
    """Read a mesh as a body: a face per facet, a solid when the mesh is closed."""
    return kernel.import_mesh(f.id, graph.doc.resolve(a["path"]), bool(a.get("unify", True)))
