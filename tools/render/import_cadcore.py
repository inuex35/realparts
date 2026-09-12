"""Load a cadcore tessellation into Blender, keeping the CAD names.

    blender -b -P tools/render/import_cadcore.py -- mesh.json

Blender cannot store a string per polygon, so the face name arrives as an int
attribute (``cad_face``) plus a lookup table on the object. That pairing is what
lets a click in the viewport come back as ``plate/+z`` and drive the feature
graph. Vertices are not welded across CAD faces, so the analytic normals from
the kernel keep the edges sharp without any smoothing tricks.
"""
import json
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
path = argv[0] if argv else "mesh.json"
data = json.loads(open(path, encoding="utf-8").read())

table = data.get("face_table") or sorted(set(data["triangle_face"]))
index = {n: i for i, n in enumerate(table)}
tri_face = data["triangle_face"]
if tri_face and isinstance(tri_face[0], str):
    tri_face = [index[n] for n in tri_face]

mesh = bpy.data.meshes.new("cad_body")
mesh.from_pydata([tuple(v) for v in data["vertices"]], [], [tuple(t) for t in data["triangles"]])
mesh.update()

attr = mesh.attributes.new("cad_face", 'INT', 'FACE')
attr.data.foreach_set("value", tri_face)

normals = data.get("normals")
if normals and any(any(n) for n in normals):
    mesh.normals_split_custom_set_from_vertices([tuple(n) for n in normals])

obj = bpy.data.objects.new("cad_body", mesh)
bpy.context.scene.collection.objects.link(obj)
obj["cad_face_table"] = json.dumps(table)
obj["cad_source"] = path

# the named edges, as a second object for edge picking
if data.get("edges"):
    verts, edges, edge_id, names = [], [], [], []
    for name, pts in data["edges"].items():
        start = len(verts)
        verts.extend(tuple(p) for p in pts)
        for k in range(len(pts) - 1):
            edges.append((start + k, start + k + 1))
            edge_id.append(len(names))
        names.append(name)
    em = bpy.data.meshes.new("cad_edges")
    em.from_pydata(verts, edges, [])
    em.update()
    ea = em.attributes.new("cad_edge", 'INT', 'EDGE')
    ea.data.foreach_set("value", edge_id)
    eo = bpy.data.objects.new("cad_edges", em)
    bpy.context.scene.collection.objects.link(eo)
    eo["cad_edge_table"] = json.dumps(names)


def name_of_polygon(ob, poly_index: int) -> str:
    """What the viewport needs: polygon -> CAD face name."""
    tbl = json.loads(ob["cad_face_table"])
    return tbl[ob.data.attributes["cad_face"].data[poly_index].value]


print(f"BRIDGE imported {len(mesh.polygons)} polygons, {len(table)} named faces")
for i in (0, len(mesh.polygons) // 2, len(mesh.polygons) - 1):
    print(f"BRIDGE polygon {i} -> {name_of_polygon(obj, i)}")
if data.get("edges"):
    print(f"BRIDGE {len(names)} named edges, e.g. {names[0]}")
