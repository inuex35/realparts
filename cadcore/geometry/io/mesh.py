"""Meshes in and out: STL, OBJ, 3MF and glTF as triangles, and a shape made of them.

A mesh has no surfaces, so a face is made per triangle and coplanar ones are
merged; what comes out is a real solid that booleans and fillets accept.
"""
from __future__ import annotations

import json
import os
import struct
import zipfile
from xml.etree import ElementTree

from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakeSolid, BRepBuilderAPI_MakeVertex,
                                BRepBuilderAPI_MakeWire)
from OCP.BRepLib import BRepLib
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shell
from OCP.gp import gp_Pnt

from ... import names
from ...errors import CadError
from ..core.naming import Body, faces_of, role_names

__all__ = ["read_triangles", "import_mesh", "write_gltf", "MESH_SUFFIXES"]

MESH_SUFFIXES = (".stl", ".obj", ".3mf", ".gltf", ".glb")


def read_triangles(path: str) -> tuple:
    """``(vertices, triangles)`` of a mesh file, by its suffix; vertices in mm."""
    if not os.path.exists(path):
        raise CadError("file_not_found", f"no mesh file at {path!r}")
    suffix = os.path.splitext(path)[1].lower()
    readers = {".stl": _read_stl, ".obj": _read_obj, ".3mf": _read_3mf,
               ".gltf": _read_gltf, ".glb": _read_gltf}
    if suffix not in readers:
        raise CadError("unknown_format", f"no mesh reader for {suffix!r}",
                       {"available": list(MESH_SUFFIXES)})
    vertices, triangles = readers[suffix](path)
    if not triangles:
        raise CadError("empty_mesh", f"{path!r} holds no triangles")
    return vertices, triangles


def _read_stl(path: str) -> tuple:
    from OCP.RWStl import RWStl

    poly = RWStl.ReadFile_s(path)
    if poly is None:
        raise CadError("mesh_read_failed", f"OCCT could not read {path!r} as STL")
    return _from_poly(poly, TopLoc_Location())


def _from_poly(poly, location) -> tuple:
    trsf = location.Transformation()
    vertices = []
    for i in range(1, poly.NbNodes() + 1):
        p = poly.Node(i).Transformed(trsf)
        vertices.append((p.X(), p.Y(), p.Z()))
    triangles = []
    for i in range(1, poly.NbTriangles() + 1):
        a, b, c = poly.Triangle(i).Get()
        triangles.append((a - 1, b - 1, c - 1))
    return vertices, triangles


def _read_obj(path: str) -> tuple:
    vertices, triangles = [], []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "v" and len(parts) >= 4:
                vertices.append(tuple(float(v) for v in parts[1:4]))
            elif parts[0] == "f" and len(parts) >= 4:
                corners = []
                for token in parts[1:]:
                    index = int(token.split("/")[0])
                    corners.append(index - 1 if index > 0 else len(vertices) + index)
                for k in range(1, len(corners) - 1):        # a polygon as a fan
                    triangles.append((corners[0], corners[k], corners[k + 1]))
    if not vertices:
        raise CadError("mesh_read_failed", f"{path!r} has no vertices")
    return vertices, triangles


def _read_3mf(path: str) -> tuple:
    """The model's meshes, placed as its <build> items say; every object when there is no build."""
    try:
        with zipfile.ZipFile(path) as archive:
            model = next((n for n in archive.namelist() if n.lower().endswith(".model")), None)
            if model is None:
                raise CadError("mesh_read_failed", f"{path!r} has no 3D model inside")
            root = ElementTree.fromstring(archive.read(model))
    except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise CadError("mesh_read_failed", f"{path!r} is not a 3MF package",
                       {"reason": str(exc)}) from exc
    scale = {"millimeter": 1.0, "centimeter": 10.0, "meter": 1000.0, "inch": 25.4,
             "foot": 304.8, "micron": 0.001}.get(root.get("unit", "millimeter"), 1.0)
    tag = lambda node: node.tag.rsplit("}", 1)[-1]                       # noqa: E731
    objects: dict = {}                    # id -> (vertices, triangles, [(child id, transform)])
    for obj in (n for n in root.iter() if tag(n) == "object"):
        vertices, triangles, parts = [], [], []
        for node in obj.iter():
            kind = tag(node)
            if kind == "vertex":
                vertices.append(tuple(float(node.get(k)) * scale for k in ("x", "y", "z")))
            elif kind == "triangle":
                triangles.append(tuple(int(node.get(k)) for k in ("v1", "v2", "v3")))
            elif kind == "component":
                parts.append((node.get("objectid"), _3mf_transform(node.get("transform"), scale)))
        objects[obj.get("id")] = (vertices, triangles, parts)
    items = [(n.get("objectid"), _3mf_transform(n.get("transform"), scale))
             for n in root.iter() if tag(n) == "item"]
    if not items:
        items = [(key, None) for key in objects]
    vertices, triangles = [], []

    def place(key, transform, depth=0):
        if key not in objects or depth > 16:
            return
        v, t, parts = objects[key]
        base = len(vertices)
        vertices.extend(_3mf_apply(transform, p) for p in v)
        triangles.extend((base + a, base + b, base + c) for a, b, c in t)
        for child, inner in parts:
            place(child, _3mf_compose(transform, inner), depth + 1)

    for key, transform in items:
        place(key, transform)
    return vertices, triangles


def _3mf_transform(text: str | None, scale: float):
    """A 3MF transform: 12 numbers, a 3x3 matrix by rows then a translation."""
    if not text:
        return None
    m = [float(v) for v in text.split()]
    if len(m) != 12:
        return None
    return [m[0:3], m[3:6], m[6:9], [v * scale for v in m[9:12]]]


def _3mf_apply(transform, p):
    if transform is None:
        return p
    r0, r1, r2, t = transform                # row-vector convention: p' = p . M + t
    return tuple(p[0] * r0[i] + p[1] * r1[i] + p[2] * r2[i] + t[i] for i in range(3))


def _3mf_compose(outer, inner):
    """The transform that applies ``inner`` first, then ``outer``."""
    if outer is None:
        return inner
    if inner is None:
        return outer
    rows = [_3mf_apply([outer[0], outer[1], outer[2], [0.0, 0.0, 0.0]], row) for row in inner[:3]]
    return [list(rows[0]), list(rows[1]), list(rows[2]), list(_3mf_apply(outer, inner[3]))]


def _read_gltf(path: str) -> tuple:
    from OCP.RWGltf import RWGltf_CafReader
    from OCP.TCollection import TCollection_AsciiString
    from OCP.Message import Message_ProgressRange
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.collections import Sequence_TDF_Label
    from .exchange import _xcaf_document

    from OCP.RWMesh import RWMesh_CoordinateSystem

    doc = _xcaf_document()
    reader = RWGltf_CafReader()
    reader.SetDocument(doc)
    reader.SetSystemLengthUnit(0.001)           # the kernel is mm; the file is metres
    reader.SetSystemCoordinateSystem(RWMesh_CoordinateSystem.RWMesh_CoordinateSystem_Zup)
    if not reader.Perform(TCollection_AsciiString(path), Message_ProgressRange()):
        raise CadError("mesh_read_failed", f"OCCT could not read {path!r} as glTF")
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    free = Sequence_TDF_Label()
    tool.GetFreeShapes(free)
    vertices, triangles = [], []
    for i in range(1, free.Length() + 1):
        for face in faces_of(tool.GetShape_s(free.Value(i))):
            location = TopLoc_Location()
            poly = BRep_Tool.Triangulation_s(face, location)
            if poly is None:
                continue
            v, t = _from_poly(poly, location)
            base = len(vertices)
            vertices.extend(v)
            triangles.extend((a + base, b + base, c + base) for a, b, c in t)
    return vertices, triangles


def shape_from_triangles(vertices: list, triangles: list):
    """A shell of one flat face per triangle, edges shared; a solid if it closes."""
    builder = BRep_Builder()
    shell = TopoDS_Shell()
    builder.MakeShell(shell)
    points = {}
    for tri in triangles:
        for i in tri:
            if i not in points:
                points[i] = BRepBuilderAPI_MakeVertex(gp_Pnt(*vertices[i])).Vertex()
    edges: dict = {}

    def edge(i: int, j: int):
        key = (i, j) if i < j else (j, i)
        found = edges.get(key)
        if found is None:
            found = edges[key] = BRepBuilderAPI_MakeEdge(points[key[0]], points[key[1]]).Edge()
        return found if key == (i, j) else TopoDS.Edge_s(found.Reversed())

    made = 0
    for a, b, c in triangles:
        if len({a, b, c}) < 3:
            continue
        wire = BRepBuilderAPI_MakeWire(edge(a, b), edge(b, c), edge(c, a))
        if not wire.IsDone():
            continue
        face = BRepBuilderAPI_MakeFace(wire.Wire(), True)
        if not face.IsDone():
            continue
        builder.Add(shell, face.Face())
        made += 1
    if not made:
        raise CadError("empty_mesh", "no triangle of the mesh made a face")
    boundary = sum(1 for (i, j) in edges if _uses(triangles, i, j) == 1)
    if boundary:
        return shell, False
    solid = BRepBuilderAPI_MakeSolid(shell)
    solid.Build()
    if not solid.IsDone():
        return shell, False
    out = solid.Solid()
    BRepLib.OrientClosedSolid_s(out)
    return out, True


def _uses(triangles: list, i: int, j: int) -> int:
    pair = {i, j}
    return sum(1 for t in triangles if pair <= set(t))


def import_mesh(feature_id: str, path: str, unify: bool = True) -> Body:
    """A mesh file as a body; coplanar triangles are merged into one face by default."""
    vertices, triangles = read_triangles(path)
    shape, closed = shape_from_triangles(*_welded(vertices, triangles))
    if unify:
        from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

        unifier = ShapeUpgrade_UnifySameDomain(shape, True, True, False)
        unifier.Build()
        shape = unifier.Shape()
    faces = faces_of(shape)
    if len(faces) <= 400:
        named = role_names(feature_id, faces)
    else:                                  # a scan: thousands of facets, numbered
        named = [(names.face(feature_id, "facet", k), f) for k, f in enumerate(faces, start=1)]
    body = Body(shape, named)
    body.notes["import"] = {"path": os.path.basename(path), "triangles": len(triangles),
                            "closed": closed, "faces": len(faces)}
    return body


def _welded(vertices: list, triangles: list, digits: int = 6) -> tuple:
    """The same mesh with vertices at one place counted once."""
    index: dict = {}
    kept: list = []
    remap = []
    for v in vertices:
        key = tuple(round(c, digits) for c in v)
        if key not in index:
            index[key] = len(kept)
            kept.append(v)
        remap.append(index[key])
    return kept, [(remap[a], remap[b], remap[c]) for a, b, c in triangles]


def write_gltf(body: Body, path: str, deflection: float = 0.05, angular: float = 0.3,   # deflection mm, angular radians
               name: str = "part") -> dict:
    """Write the body as glTF (.gltf with a .bin beside it) or binary .glb."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.Message import Message_ProgressRange
    from OCP.RWGltf import RWGltf_CafWriter
    from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.collections import IndexedDataMap_TCollection_AsciiString_TCollection_AsciiString
    from .exchange import _xcaf_document

    BRepMesh_IncrementalMesh(body.shape, float(deflection), False, float(angular), True)
    doc = _xcaf_document()
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    label = tool.AddShape(body.shape, False)
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
    from OCP.RWMesh import RWMesh_CoordinateSystem

    writer = RWGltf_CafWriter(TCollection_AsciiString(path), path.lower().endswith(".glb"))
    converter = writer.ChangeCoordinateSystemConverter()
    converter.SetInputLengthUnit(0.001)         # the kernel is mm, Z up; glTF is metres, Y up
    converter.SetInputCoordinateSystem(RWMesh_CoordinateSystem.RWMesh_CoordinateSystem_Zup)
    converter.SetOutputLengthUnit(1.0)
    converter.SetOutputCoordinateSystem(RWMesh_CoordinateSystem.RWMesh_CoordinateSystem_glTF)
    # the whole document: the per-root overload writes an empty file
    ok = writer.Perform(doc, IndexedDataMap_TCollection_AsciiString_TCollection_AsciiString(),
                        Message_ProgressRange())
    if not ok or not os.path.exists(path):
        raise CadError("export_failed", "the glTF writer could not write this body", {"path": path})
    return {"path": path, "bytes": os.path.getsize(path)}
