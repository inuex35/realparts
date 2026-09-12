"""Turn a cadcore tessellation into Blender mesh data, and read it back.

:func:`apply` writes the kernel's triangles into the scene and keeps the CAD
face name on every polygon (an int attribute plus a name table on the object;
Blender has no per-polygon string attribute). :func:`selected_edge_names`
maps a viewport selection back to CAD names such as ``plate/+z|plate/+x``.
"""
from __future__ import annotations

import json

import contextlib

import bpy
from . import names

#: the whole document, when the document is one part
BODY = "cad_body"
#: one part of an assembly: ``cad_body:<part>``
PART = "cad_body:"
EDGES = "cad_edges"
#: one part's wires: ``cad_edges:<part>``
EDGE_PART = "cad_edges:"
#: the collection a part's mesh and wires share, so they hide together
GROUP = "cad:"


def _is_body_name(name: str) -> bool:
    return name == BODY or name.startswith(PART)


def _is_edges_name(name: str) -> bool:
    return name == EDGES or name.startswith(EDGE_PART)


def _group(part: str | None):
    """The collection a part's objects live in.

    The wires are a second object, and a part that can be switched off has to
    take its wires with it: one collection holds both, and the eye beside the
    collection is the switch. A document that is one part has no group -- it is
    the scene, as it was before there were parts.
    """
    if part is None:
        return bpy.context.scene.collection
    name = GROUP + part
    group = bpy.data.collections.get(name)
    if group is None:
        group = bpy.data.collections.new(name)
    if name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(group)
    return group


def bodies() -> list:
    """Every object the document is drawn as, in the order the document names them.

    One for a part, one per part for an assembly. `bake` renames what it cuts
    loose, so a baked mesh drops out of this list by having stopped being one.
    """
    found = [o for o in bpy.data.objects if _is_body_name(o.name)]
    found.sort(key=lambda o: (o.get("cad_part_order", 0), o.name))
    return found


def body():
    """One of them, for the callers that only need to know a model is on screen."""
    found = bodies()
    return found[0] if found else None


def editing():
    """The body Blender is editing, if any: an assembly can have several open."""
    return next((ob for ob in bodies() if ob.mode == 'EDIT'), None)


def is_body(ob) -> bool:
    """Whether a ray hit the model rather than something else in the scene."""
    return ob is not None and _is_body_name(ob.name)


def part_of(name: str) -> str | None:
    """The part a face or edge name belongs to.

    Everything before the last colon, because scopes nest: an assembly whose
    parts are themselves assemblies gives ``wide:cone/face0``, and ``wide:cone``
    is a thing a person can switch off on its own while ``wide:body`` stays.

    An edge is named for the two faces it lies between, and both are on the one
    part, so the first of them answers for the edge too.
    """
    return names.part_of(name)


@contextlib.contextmanager
def out_of_edit_mode(ob):
    """Leave edit mode around a mesh write, then restore it.

    Mesh data cannot be written in edit mode: the geometry is in a BMesh and
    `clear_geometry` raises RuntimeError. A pick is made in edit mode, so a
    rebuild after a pick needs this.
    """
    was = ob.mode if ob is not None else 'OBJECT'
    if was != 'EDIT':
        yield
        return
    previous = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='OBJECT')
    try:
        yield
    finally:
        bpy.context.view_layer.objects.active = ob
        bpy.ops.object.mode_set(mode='EDIT')
        if previous is not None:
            bpy.context.view_layer.objects.active = previous


def _replace_mesh(name: str, build, into=None) -> bpy.types.Object:
    """Refill the object's existing mesh, or create the object the first time.

    The mesh datablock is reused, not swapped: replacing it invalidates every
    reference to it (materials, modifiers, Python handles) and crashes Blender.
    """
    ob = bpy.data.objects.get(name)
    if ob is None:
        mesh = bpy.data.meshes.new(name + "_mesh")
        ob = bpy.data.objects.new(name, mesh)
        (into or bpy.context.scene.collection).objects.link(ob)
        build(mesh)
        _deselect(mesh)
        return ob
    with out_of_edit_mode(ob):
        mesh = ob.data
        mesh.clear_geometry()
        for attr in [a for a in mesh.attributes if a.name.startswith("cad_")]:
            mesh.attributes.remove(attr)
        build(mesh)
        _deselect(mesh)
    return ob


def _deselect(mesh) -> None:
    # from_pydata leaves everything selected.
    mesh.polygons.foreach_set("select", [False] * len(mesh.polygons))
    mesh.edges.foreach_set("select", [False] * len(mesh.edges))
    mesh.vertices.foreach_set("select", [False] * len(mesh.vertices))


def _split(table: list, tri_face: list) -> list:
    """The objects to draw: ``[(object name, triangle indices or None)]``.

    An assembly's face names are scoped -- ``wide:body/+z`` -- and the scope is
    the part. One object per scope is what lets a person switch a part off
    without the document, the kernel or the picking knowing anything about it.
    ``None`` for the triangles means all of them, which is the one-part case
    and is drawn exactly as it was before parts existed.
    """
    order: dict = {}
    for name in table:
        scope = part_of(name)
        if scope is not None and scope not in order:
            order[scope] = len(order)
    if not order:
        return [(BODY, None)]
    buckets: dict = {scope: [] for scope in order}
    loose: list = []
    for triangle, face in enumerate(tri_face):
        scope = part_of(table[face])
        (loose if scope is None else buckets[scope]).append(triangle)
    out = [(PART + scope, buckets[scope]) for scope in order]
    if loose:                      # a scoped document with unscoped faces in it
        out.append((BODY, loose))
    return out


def _drop_stale(keep: set) -> None:
    """Objects and groups left over from the document that was on screen before."""
    for ob in [o for o in bpy.data.objects
               if (_is_body_name(o.name) or _is_edges_name(o.name)) and o.name not in keep]:
        if ob.mode == 'EDIT':      # removing an object that is being edited crashes
            bpy.context.view_layer.objects.active = ob
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.data.objects.remove(ob, do_unlink=True)
    for group in [c for c in bpy.data.collections
                  if c.name.startswith(GROUP) and not c.objects and not c.children]:
        bpy.data.collections.remove(group)


def _edges_by_part(data: dict) -> dict:
    """The document's wires, grouped by the part they belong to.

    An edge lies between two faces of one solid, and an assembly's parts are
    separate solids, so the scope on either end of ``wide:body/+z|wide:body/+x``
    is the same one. ``None`` is a document that is one part.
    """
    out: dict = {}
    for name, points in (data.get("edges") or {}).items():
        out.setdefault(part_of(name), []).append((name, points))
    return out


def _draw_edges(part: str | None, wire: list) -> None:
    """One wire object, in the same collection as the part it belongs to."""
    verts, segs, edge_id, names = [], [], [], []
    for name, points in wire:
        start = len(verts)
        verts.extend(tuple(p) for p in points)
        for k in range(len(points) - 1):
            segs.append((start + k, start + k + 1))
            edge_id.append(len(names))
        names.append(name)

    def build(mesh):
        mesh.from_pydata(verts, segs, [])
        mesh.update()
        mesh.attributes.new("cad_edge", 'INT', 'EDGE').data.foreach_set("value", edge_id)

    eo = _replace_mesh(EDGES if part is None else EDGE_PART + part, build, _group(part))
    eo["cad_edge_table"] = json.dumps(names)
    eo.display_type = 'WIRE'
    eo.hide_select = True              # drawn, not picked: edges are picked on the body


def _draw_part(name: str, order: int, wanted, data: dict, table: list, tri_face: list,
               revision: int | None, keep: set) -> bpy.types.Object:
    """One body object. `wanted` is the triangles it takes, or None for all of them.

    The whole face table and the document-wide face index go on every part, so
    a name read off a polygon is the same name whichever object the ray hit.
    """
    normals = data.get("normals")
    if normals and not any(any(n) for n in normals):
        normals = None
    if wanted is None:
        verts = [tuple(v) for v in data["vertices"]]
        tris = [tuple(t) for t in data["triangles"]]
        faces = tri_face
        norms = [tuple(n) for n in normals] if normals else None
    else:
        seen: dict = {}
        verts, tris, faces = [], [], []
        for triangle in wanted:
            corners = data["triangles"][triangle]
            for corner in corners:
                if corner not in seen:
                    seen[corner] = len(verts)
                    verts.append(tuple(data["vertices"][corner]))
            tris.append(tuple(seen[c] for c in corners))
            faces.append(tri_face[triangle])
        norms = None
        if normals:
            norms = [(0.0, 0.0, 0.0)] * len(verts)
            for corner, moved in seen.items():
                norms[moved] = tuple(normals[corner])

    def build(mesh):
        mesh.from_pydata(verts, [], tris)
        mesh.update()
        mesh.attributes.new("cad_face", 'INT', 'FACE').data.foreach_set("value", faces)
        if revision is not None and len(mesh.vertices):
            # the revision travels with the mesh: an edit-mode undo step brings
            # back the mesh and not the scene, and this is how the kernel
            # learns which state of the document that mesh was
            mesh.attributes.new("cad_revision", 'INT', 'POINT').data.foreach_set(
                "value", [int(revision)] * len(mesh.vertices))
        if norms:
            # Vertices are not welded across CAD faces, so analytic normals
            # keep every CAD edge sharp.
            mesh.normals_split_custom_set_from_vertices(norms)

    ob = _replace_mesh(name, build, _group(name.split(names.SCOPE, 1)[1] if names.SCOPE in name else None))
    ob["cad_face_table"] = json.dumps(table)
    ob["cad_part_order"] = order
    ob["cad_mesh_hash"] = mesh_hash(ob.data)
    _reselect(ob, table, faces, keep)
    # A painted field belongs to the old geometry; drop it.
    ob.data.materials.clear()
    ob.pop("cad_face_stress", None)
    return ob


def apply(data: dict, revision: int | None = None) -> bpy.types.Object:
    """Draw the kernel's mesh; `revision` is the kernel's undo revision it shows."""
    keep = _selected_names()
    table = data.get("face_table") or sorted(set(data["triangle_face"]))
    index = {n: i for i, n in enumerate(table)}
    tri_face = data["triangle_face"]
    if tri_face and isinstance(tri_face[0], str):
        tri_face = [index[n] for n in tri_face]

    wanted = _split(table, tri_face)
    wires = _edges_by_part(data)
    _drop_stale({name for name, _ in wanted}
                | {EDGES if part is None else EDGE_PART + part for part in wires})
    drawn = [_draw_part(name, order, take, data, table, tri_face, revision, keep)
             for order, (name, take) in enumerate(wanted)]
    for part, wire in wires.items():
        _draw_edges(part, wire)
    return drawn[0]


RAMP = [(0.00, (0.02, 0.10, 0.55)), (0.35, (0.00, 0.65, 0.75)),
        (0.60, (0.15, 0.80, 0.20)), (0.80, (0.95, 0.80, 0.10)), (1.00, (0.85, 0.05, 0.05))]


def _colour(t: float) -> tuple:
    t = min(max(t, 0.0), 1.0)
    for (t0, c0), (t1, c1) in zip(RAMP, RAMP[1:]):
        if t <= t1:
            k = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(c0[i] + (c1[i] - c0[i]) * k for i in range(3)) + (1.0,)
    return RAMP[-1][1] + (1.0,)


def _stress_material() -> bpy.types.Material:
    mat = bpy.data.materials.get("cad_stress")
    if mat is None:
        mat = bpy.data.materials.new("cad_stress")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    attr = next((n for n in nodes if n.type == 'ATTRIBUTE'), None) or \
        nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "stress_color"
    bsdf = nodes.get("Principled BSDF")
    if bsdf is not None:
        links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
        bsdf.inputs["Roughness"].default_value = 0.45
    return mat


def paint_faces(severity: dict, per_face: dict | None = None) -> bool:
    """Colour whole faces by a severity keyed on CAD face name.

    Keyed on the name, so every part can be painted: each one works out its
    own vertex values from the polygons it happens to hold.
    """
    done = False
    for ob in bodies():
        if not ob.get("cad_face_table"):
            continue
        table = json.loads(ob["cad_face_table"])
        attr = ob.data.attributes.get("cad_face")
        if attr is None:
            continue
        values = [0.0] * len(ob.data.vertices)
        for poly in ob.data.polygons:
            level = float(severity.get(table[attr.data[poly.index].value], 0.0))
            for vertex in poly.vertices:
                values[vertex] = max(values[vertex], level)
        done = paint(values, 1.0, per_face, ob) or done
    return done


def paint(values, scale: float, per_face: dict | None = None,
          ob: bpy.types.Object | None = None) -> bool:
    """Colour the body's vertices by ``values`` / ``scale``.

    The stress field passes its 95th percentile as ``scale`` rather than the
    peak, so one singular corner does not wash the whole part blue.

    A field arrives as one value per vertex of the document it was solved on,
    which is one part. Told nothing, this paints the one body there is and
    refuses an assembly rather than painting a part with another part's
    numbers; `paint_faces` names its faces instead and can paint them all.
    """
    if ob is None:
        found = bodies()
        if len(found) != 1:
            return False
        ob = found[0]
    if len(values) != len(ob.data.vertices):
        return False
    mesh = ob.data
    # Remove first: `attributes.new()` with a taken name silently creates
    # "stress_color.001", which the material's Attribute node does not read.
    for existing in ("stress", "stress_color"):
        attribute = mesh.attributes.get(existing)
        if attribute is not None:
            mesh.attributes.remove(attribute)
    mesh.attributes.new("stress", 'FLOAT', 'POINT').data.foreach_set("value", list(values))
    colours = []
    span = scale if scale > 1e-9 else 1.0
    for v in values:
        colours.extend(_colour(v / span))
    mesh.color_attributes.new("stress_color", 'FLOAT_COLOR', 'POINT') \
        .data.foreach_set("color", colours)
    mesh.materials.clear()
    mesh.materials.append(_stress_material())
    if per_face is not None:
        ob["cad_face_stress"] = json.dumps(per_face)
    return True


def face_name(ob: bpy.types.Object, poly: int) -> str | None:
    table = ob.get("cad_face_table")
    attr = ob.data.attributes.get("cad_face")
    if not table or attr is None:
        return None
    if ob.mode == 'EDIT':
        import bmesh

        bm = bmesh.from_edit_mesh(ob.data)
        layer = bm.faces.layers.int.get("cad_face")
        bm.faces.ensure_lookup_table()
        return json.loads(table)[bm.faces[poly][layer]] if layer else None
    return json.loads(table)[attr.data[poly].value]


def _selected_names() -> set:
    """The picked CAD face names, taken before a rebuild replaces the polygons."""
    return set(selected_face_names())


def _reselect(ob: bpy.types.Object, table: list, tri_face: list, keep: set) -> None:
    """Reselect the faces named in ``keep`` after a rebuild replaced the polygons.

    A throttled rebuild can land after the click that picked the faces, so a
    tool run right after a drag would otherwise see nothing selected.
    """
    if not keep:
        return
    wanted = {i for i, name in enumerate(table) if name in keep}
    if not wanted:
        return
    if ob.mode == 'EDIT':
        # the mesh is back in a BMesh by now; polygon flags would be ignored
        import bmesh

        bm = bmesh.from_edit_mesh(ob.data)
        layer = bm.faces.layers.int.get("cad_face")
        for face in bm.faces:
            face.select = layer is not None and face[layer] in wanted
        bm.select_flush_mode()
        bmesh.update_edit_mesh(ob.data)
        return
    polygons = ob.data.polygons
    try:
        import numpy as np

        faces = np.asarray(tri_face, dtype=np.int32)
        flags = np.isin(faces, np.fromiter(wanted, dtype=np.int32, count=len(wanted)))
        polygons.foreach_set("select", flags)
    except ImportError:                                             # noqa: BLE001
        for polygon, face in zip(polygons, tri_face):
            polygon.select = face in wanted
    ob.data.update()


def pos_key(vector) -> tuple:
    """A vertex position rounded so the same corner on two faces is one key.
    Faces do not share vertices, so this is how neighbours are found."""
    return tuple(round(c, 4) for c in vector)


def edge_key(edge) -> frozenset:
    return frozenset(pos_key(v.co) for v in edge.verts)


def rim_map(bm, table: list, layer) -> dict:
    """Mesh edge (by its two ends) -> the CAD faces on either side of it."""
    sides: dict = {}
    for edge in bm.edges:
        sides.setdefault(edge_key(edge), set()).update(table[f[layer]] for f in edge.link_faces)
    return sides


def picked(ob: bpy.types.Object | None = None) -> tuple[list[str], list[tuple[str, str]]]:
    """What is picked on the body: CAD face names, and edges as pairs of faces.

    ``None`` asks every body: an assembly is several objects, and a selection
    spanning two of them is still one selection.

    Read from a BMesh in both modes, because in edit mode the mesh lives there
    and `attributes[...].data` is empty. A picked mesh edge between two CAD
    faces is the CAD edge between them; an edge inside one face (a triangle's
    diagonal) is not an edge of the part.
    """
    import bmesh

    if ob is None:
        faces: list[str] = []
        pairs: list[tuple[str, str]] = []
        for one in bodies():
            more_faces, more_pairs = picked(one)
            faces += [n for n in more_faces if n not in faces]
            pairs += [p for p in more_pairs if p not in pairs]
        return faces, pairs
    table = ob.get("cad_face_table")
    if not table or ob.type != 'MESH':
        return [], []
    table = json.loads(table)
    if ob.mode == 'EDIT':
        bm, own = bmesh.from_edit_mesh(ob.data), False
    else:
        bm, own = bmesh.new(), True
        bm.from_mesh(ob.data)
    layer = bm.faces.layers.int.get("cad_face")
    faces: list[str] = []
    pairs: list[tuple[str, str]] = []
    if layer is not None:
        for face in bm.faces:
            if face.select and table[face[layer]] not in faces:
                faces.append(table[face[layer]])
        chosen = [e for e in bm.edges if e.select]
        if chosen:
            sides = rim_map(bm, table, layer)
            for edge in chosen:
                pair = sorted(sides.get(edge_key(edge), ()))
                if len(pair) == 2 and tuple(pair) not in pairs:
                    pairs.append(tuple(pair))
    if own:
        bm.free()
    return faces, pairs


def selected_face_names(ob: bpy.types.Object | None = None) -> list[str]:
    return picked(ob)[0]


def mesh_hash(mesh: bpy.types.Mesh) -> str:
    """A short fingerprint of the mesh: vertex count, face count, positions."""
    import hashlib

    h = hashlib.blake2b(digest_size=16)
    h.update(b"%d,%d;" % (len(mesh.vertices), len(mesh.polygons)))
    try:
        import numpy as np

        flat = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", flat)
        h.update(flat.tobytes())
    except ImportError:                                             # noqa: BLE001
        for v in mesh.vertices:
            h.update(b"%.5f,%.5f,%.5f;" % tuple(v.co))
    return h.hexdigest()


def remembered_revision(ob: bpy.types.Object) -> int | None:
    """The kernel revision the body's mesh was drawn at, or None for a mesh without one."""
    if ob is None or ob.type != 'MESH':
        return None
    if ob.mode == 'EDIT':
        import bmesh

        bm = bmesh.from_edit_mesh(ob.data)
        layer = bm.verts.layers.int.get("cad_revision")
        if layer is None or not len(bm.verts):
            return None
        bm.verts.ensure_lookup_table()
        return int(bm.verts[0][layer])
    attribute = ob.data.attributes.get("cad_revision")
    if attribute is None or not len(attribute.data):
        return None
    return int(attribute.data[0].value)


def changed_by_hand(ob: bpy.types.Object | None = None) -> bool:
    """Was the body's mesh changed by a Blender tool since the kernel drew it?

    With no object, any of them having been changed counts: the document is
    drawn as one object per part and a hand on any part is a hand on the model.
    """
    if ob is None:
        return any(changed_by_hand(one) for one in bodies())
    known = ob.get("cad_mesh_hash")
    if not known or ob.mode == 'EDIT':
        return False
    return mesh_hash(ob.data) != known


def bake(ob: bpy.types.Object) -> str:
    """Cut the body loose from the CAD: a plain Blender mesh from here on.

    The face names and the fingerprint go, the wire object goes, and the
    object gets a new name so the next rebuild makes a fresh cad_body beside
    it instead of writing over it.
    """
    base = "part"
    name, n = base, 1
    while name in bpy.data.objects:
        n += 1
        name = f"{base}{n}"
    ob.name = name
    ob.data.name = name
    for key in ("cad_face_table", "cad_mesh_hash", "cad_face_stress"):
        ob.pop(key, None)
    attr = ob.data.attributes.get("cad_face")
    if attr is not None:
        ob.data.attributes.remove(attr)
    for wire in [o for o in bpy.data.objects if _is_edges_name(o.name)]:
        bpy.data.objects.remove(wire, do_unlink=True)
    return name


def select_faces(names, ob: bpy.types.Object | None = None) -> int:
    """Select the body's faces with these CAD names; returns how many were found.

    With no object it answers for the whole document, which for an assembly is
    every part -- including clearing the parts the names are not on.
    """
    if ob is None:
        return sum(select_faces(names, one) for one in bodies())
    if not ob.get("cad_face_table"):
        return 0
    table = json.loads(ob["cad_face_table"])
    wanted = {i for i, n in enumerate(table) if n in set(names)}
    if ob.mode == 'EDIT':
        import bmesh

        bm = bmesh.from_edit_mesh(ob.data)
        layer = bm.faces.layers.int.get("cad_face")
        for face in bm.faces:
            face.select = layer is not None and face[layer] in wanted
        bm.select_flush_mode()
        bmesh.update_edit_mesh(ob.data)
    else:
        attr = ob.data.attributes.get("cad_face")
        if attr is None:
            return 0
        for poly in ob.data.polygons:
            poly.select = attr.data[poly.index].value in wanted
        ob.data.update()
    return len(wanted)


def select_corner(ob, position, add: bool = False) -> int:
    """Select every vertex of the body at this position (faces do not share
    vertices, so a corner is several)."""
    import bmesh

    if ob.mode != 'EDIT':
        return 0
    bm = bmesh.from_edit_mesh(ob.data)
    if not add:
        for v in bm.verts:
            v.select = False
        for e in bm.edges:
            e.select = False
        for f in bm.faces:
            f.select = False
    count = 0
    for v in bm.verts:
        if pos_key(v.co) == tuple(position):
            v.select = True
            count += 1
    bm.select_flush_mode()
    bmesh.update_edit_mesh(ob.data)
    return count


def picked_corner(ob):
    """The selected corner, if the pick is a corner: (position, faces) or None."""
    import bmesh

    if ob is None or ob.mode != 'EDIT' or not ob.get("cad_face_table"):
        return None
    table = json.loads(ob["cad_face_table"])
    bm = bmesh.from_edit_mesh(ob.data)
    layer = bm.faces.layers.int.get("cad_face")
    if layer is None or any(f.select for f in bm.faces) or any(e.select for e in bm.edges):
        return None
    chosen = [v for v in bm.verts if v.select]
    if not chosen:
        return None
    key = pos_key(chosen[0].co)
    faces = sorted({table[f[layer]] for v in chosen for f in v.link_faces})
    return key, faces


def select_edge_between(ob, a: str, b: str, add: bool = False) -> int:
    """Select every rim edge of the body that lies between CAD faces a and b."""
    import bmesh

    if ob.mode != 'EDIT' or not ob.get("cad_face_table"):
        return 0
    table = json.loads(ob["cad_face_table"])
    bm = bmesh.from_edit_mesh(ob.data)
    layer = bm.faces.layers.int.get("cad_face")
    if layer is None:
        return 0
    sides = rim_map(bm, table, layer)
    wanted = {a, b}
    count = 0
    if not add:
        for e in bm.edges:
            e.select = False
        for f in bm.faces:
            f.select = False
    for e in bm.edges:
        if sides.get(edge_key(e)) == wanted:
            e.select = True
            count += 1
    bm.select_flush_mode()
    bmesh.update_edit_mesh(ob.data)
    return count


def clear() -> None:
    """Take the model out of the scene: a new document, or one with no body yet."""
    _drop_stale(set())


def selected_edge_names(client) -> list[str]:
    """Whatever is picked on the body, answered with CAD edge names.

    Faces first: two picked faces mean the edges between them, one face means
    its whole border. With no face picked, each picked edge is the edge between
    the two faces it separates. The kernel resolves both, by name.
    """
    if body() is None:
        return []
    faces, pairs = picked()
    out: list[str] = []
    if len(faces) == 1:
        out = client.call("select_edges", query={"of_face": faces[0]})["edges"]
    elif faces:
        for i, a in enumerate(faces):
            for b in faces[i + 1:]:
                # allow_empty: two picked faces that do not touch must not
                # abort the other pairs
                out.extend(client.call("select_edges",
                                       query={"between": [a, b],
                                              "allow_empty": True})["edges"])
    else:
        for a, b in pairs:
            out.extend(client.call("select_edges",
                                   query={"between": [a, b], "allow_empty": True})["edges"])
    return list(dict.fromkeys(out))
