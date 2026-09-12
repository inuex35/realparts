"""BREP -> triangles, with the CAD name of the face carried on every triangle.

This is the contract with any viewer (Blender included): a click on a triangle
has to come back as ``plate/+z``, otherwise the UI cannot drive the feature
graph. Vertices are *not* welded across faces, so CAD edges stay sharp, and
normals are evaluated analytically on the surface rather than averaged from the
triangles -- a tessellated cylinder still shades like a cylinder.
"""
from __future__ import annotations

from ...errors import CadError

import math

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location
from OCP.gp import gp_Pnt

from ..core.naming import Body


def tessellate(body: Body, deflection: float = 0.15, angular: float = 0.4,   # deflection mm, angular radians
               with_edges: bool = True) -> dict:
    """Return vertices, triangles, per-triangle face names and edge polylines."""
    BRepMesh_IncrementalMesh(body.shape, float(deflection), False, float(angular), True)

    verts: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    tris: list[tuple[int, int, int]] = []
    tri_face: list[str] = []
    face_names: list[str] = []

    # the walk below is over the names: a nameless face would be a hole in the mesh
    from ..core.naming import faces_of

    unnamed = len(faces_of(body.shape)) - len(body.names)
    if unnamed > 0:
        raise CadError("invalid_shape",
                       "%d face(s) of this body have no name and would not be drawn" % unnamed,
                       {"named": len(body.names), "faces": len(body.names) + unnamed})
    for name, face in body.names:
        loc = TopLoc_Location()
        poly = BRep_Tool.Triangulation_s(face, loc)
        if poly is None:
            continue
        trsf = loc.Transformation()
        reversed_face = face.Orientation() == TopAbs_REVERSED
        surf = BRepAdaptor_Surface(face)
        has_uv = poly.HasUVNodes()
        base = len(verts)

        for i in range(1, poly.NbNodes() + 1):
            p = poly.Node(i).Transformed(trsf)
            verts.append((p.X(), p.Y(), p.Z()))
            n = None
            if has_uv:
                uv = poly.UVNode(i)
                try:
                    du, dv = surf.DN(uv.X(), uv.Y(), 1, 0), surf.DN(uv.X(), uv.Y(), 0, 1)
                    cx = du.Y() * dv.Z() - du.Z() * dv.Y()
                    cy = du.Z() * dv.X() - du.X() * dv.Z()
                    cz = du.X() * dv.Y() - du.Y() * dv.X()
                    mag = (cx * cx + cy * cy + cz * cz) ** 0.5
                    if mag > 1e-12:
                        s = -1.0 if reversed_face else 1.0
                        n = (s * cx / mag, s * cy / mag, s * cz / mag)
                except Exception:                                   # noqa: BLE001
                    n = None
            normals.append(n or (0.0, 0.0, 0.0))

        for i in range(1, poly.NbTriangles() + 1):
            a, b, c = poly.Triangle(i).Get()
            if reversed_face:
                a, c = c, a
            tris.append((base + a - 1, base + b - 1, base + c - 1))
            tri_face.append(name)
        face_names.append(name)

    out = {
        "vertices": verts,
        "normals": normals,
        "triangles": tris,
        "triangle_face": tri_face,
        "faces": face_names,
        "deflection": deflection,
    }
    if with_edges:
        out["edges"] = _edge_polylines(body, deflection)
    return out


def _edge_polylines(body: Body, deflection: float) -> dict:
    """Sampled points per named edge -- what an edge-picking UI needs."""
    lines: dict[str, list] = {}
    for name, edge in body.edge_table().items():
        ad = BRepAdaptor_Curve(edge)
        sampler = GCPnts_QuasiUniformDeflection(ad, float(deflection))
        pts = []
        if sampler.IsDone():
            for i in range(1, sampler.NbPoints() + 1):
                p: gp_Pnt = sampler.Value(i)
                pts.append((p.X(), p.Y(), p.Z()))
        if len(pts) >= 2:
            lines[name] = pts
    return lines


def refine(tess: dict, max_edge: float, limit: int = 400_000) -> dict:
    """Split triangles until no edge is longer than ``max_edge``.

    The tessellation is sized for *shape*: a flat face needs two triangles to be
    exact, however large it is. That is right for geometry and wrong for a
    field, which is then interpolated linearly across the whole face and shows
    as broad wedges of colour. Splitting the longest edge repeatedly gives the
    display enough vertices to carry the gradient, and leaves the geometry where
    it was -- new vertices sit on the segment they split, which for the planar
    faces this matters most on is exactly on the surface.
    """
    verts = [tuple(v) for v in tess["vertices"]]
    normals = [tuple(n) for n in tess["normals"]]
    tris = [tuple(t) for t in tess["triangles"]]
    faces = list(tess["triangle_face"])
    midpoints: dict[tuple, int] = {}

    def split(a: int, b: int) -> int:
        key = (a, b) if a < b else (b, a)
        if key not in midpoints:
            verts.append(tuple((verts[a][i] + verts[b][i]) / 2 for i in range(3)))
            n = tuple((normals[a][i] + normals[b][i]) / 2 for i in range(3))
            length = math.sqrt(sum(c * c for c in n)) or 1.0
            normals.append(tuple(c / length for c in n))
            midpoints[key] = len(verts) - 1
        return midpoints[key]

    queue = list(range(len(tris)))
    while queue and len(tris) < limit:
        i = queue.pop()
        tri = tris[i]
        edges = [(tri[k], tri[(k + 1) % 3], tri[(k + 2) % 3]) for k in range(3)]
        a, b, c = max(edges, key=lambda e: math.dist(verts[e[0]], verts[e[1]]))
        if math.dist(verts[a], verts[b]) <= max_edge:
            continue
        m = split(a, b)
        tris[i] = (a, m, c)
        tris.append((m, b, c))
        faces.append(faces[i])
        queue.extend([i, len(tris) - 1])

    return {**tess, "vertices": [list(v) for v in verts], "normals": [list(n) for n in normals],
            "triangles": [list(t) for t in tris], "triangle_face": faces}


def compact(tess: dict) -> dict:
    """Replace the per-triangle name with an index into a name table.

    Blender cannot store a string per polygon, so the bridge stores an integer
    attribute plus this table on the object.
    """
    table = tess["faces"]
    index = {n: i for i, n in enumerate(table)}
    return {**tess, "triangle_face": [index[n] for n in tess["triangle_face"]],
            "face_table": table}
