"""Stable face and edge names.

A face is named when a feature creates it (``base/+z``) and keeps the name
through OCCT's Modified/Generated history; an edge is named after the two
faces that meet there (``base/+z|base/+x``). Nothing relies on OCCT's ordering.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_SurfaceType
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape
from .occ import IndexedDataMapOfShapeListOfShape

from ... import names

AXES = {
    (1, 0, 0): "+x", (-1, 0, 0): "-x",
    (0, 1, 0): "+y", (0, -1, 0): "-y",
    (0, 0, 1): "+z", (0, 0, -1): "-z",
}


def outward_normal(face) -> tuple:
    """The solid's outward normal at the middle of the face.

    Not the surface axis with the orientation flag applied: a mirror reverses
    the parametrisation without moving the axis, so that is wrong on every
    mirrored face. ``BRepGProp_Face.Normal`` evaluates the real surface with
    the real orientation.
    """
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Pnt, gp_Vec

    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    point, normal = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face).Normal(0.5 * (u0 + u1), 0.5 * (v0 + v1), point, normal)
    length = normal.Magnitude() or 1.0
    return (normal.X() / length, normal.Y() / length, normal.Z() / length)


def faces_of(shape: TopoDS_Shape) -> list[TopoDS_Face]:
    out, exp = [], TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        out.append(TopoDS.Face_s(exp.Current()))
        exp.Next()
    return out


def edges_of(shape: TopoDS_Shape) -> list:
    out, exp = [], TopExp_Explorer(shape, TopAbs_EDGE)
    while exp.More():
        out.append(TopoDS.Edge_s(exp.Current()))
        exp.Next()
    return out


# Face properties are asked for repeatedly and each answer costs an OCCT
# surface integration, so they are cached; faces are immutable. The key is
# OCCT's hash plus the orientation (which flips the normal); the face is kept
# alongside so a hash collision is detected.
_INFO_CACHE: dict = {}
_INFO_CACHE_LIMIT = 50_000


def face_info(face: TopoDS_Face) -> dict:
    """Surface type, outward normal (for planes) and area, used for roles."""
    key = (hash(face), face.Orientation())
    hit = _INFO_CACHE.get(key)
    if hit is not None and hit[0].IsSame(face):
        return hit[1]
    info = _face_info(face)
    if len(_INFO_CACHE) >= _INFO_CACHE_LIMIT:
        _INFO_CACHE.clear()
    _INFO_CACHE[key] = (face, info)
    return info


#: how far a surface may be from the canonical one it is recognised as, in mm.
#: Tight, so a spline that is a plane is named as one without rounding off a
#: shape that was meant to curve.
RECOGNISE_MM = 1e-4


#: the role a face gets from its surface type, for the surfaces that have one.
SHAPE_ROLES = {"plane": None,             # a plane is named for its axis
               "cylinder": "side", "cone": "taper",
               "sphere": "ball", "torus": "ring"}


def _recognised(face: TopoDS_Face, kind):
    """The canonical surface (plane, cylinder, cone, sphere) this face lies on, or None.

    STEP files from other kernels write planes and cylinders as splines, so the
    declared type alone gives such faces no role. The gap is checked too, since
    with a loose enough tolerance anything is a plane.
    """
    from OCP.ShapeAnalysis import ShapeAnalysis_CanonicalRecognition
    from OCP.gp import gp_Cylinder, gp_Pln

    try:
        rec = ShapeAnalysis_CanonicalRecognition(face)
    except Exception:                                            # noqa: BLE001
        return None
    from OCP.gp import gp_Cone, gp_Sphere

    asked = {"plane": (rec.IsPlane, gp_Pln), "cylinder": (rec.IsCylinder, gp_Cylinder),
             "cone": (rec.IsCone, gp_Cone), "sphere": (rec.IsSphere, gp_Sphere)}
    if kind not in asked:
        return None                        # a torus is not one it recognises
    test, make = asked[kind]
    answer = make()
    if test(RECOGNISE_MM, answer) and rec.GetGap() <= RECOGNISE_MM:
        return answer
    return None


def _face_info(face: TopoDS_Face) -> dict:
    ad = BRepAdaptor_Surface(face)
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    info = {"type": ad.GetType(), "area": props.Mass(),
            "centre": (props.CentreOfMass().X(), props.CentreOfMass().Y(), props.CentreOfMass().Z())}
    declared = {GeomAbs_SurfaceType.GeomAbs_Cone: "cone",
                GeomAbs_SurfaceType.GeomAbs_Sphere: "sphere",
                GeomAbs_SurfaceType.GeomAbs_Torus: "torus"}.get(ad.GetType())
    if declared:
        # cones, spheres and tori take their role from the declared type
        info["shape"] = declared
        return info
    if ad.GetType() not in (GeomAbs_SurfaceType.GeomAbs_Plane,
                            GeomAbs_SurfaceType.GeomAbs_Cylinder):
        plane = _recognised(face, "plane")
        if plane is not None:
            info["normal"] = outward_normal(face)
            info["recognised"] = info["shape"] = "plane"
            return info
        cylinder = _recognised(face, "cylinder")
        if cylinder is not None:
            a = cylinder.Axis().Direction()
            location = cylinder.Position().Location()
            info["axis"] = (a.X(), a.Y(), a.Z())
            info["radius"] = cylinder.Radius()
            info["axis_origin"] = (location.X(), location.Y(), location.Z())
            info["recognised"] = info["shape"] = "cylinder"
            return info
        for kind in ("cone", "sphere"):
            found = _recognised(face, kind)
            if found is not None:
                info["recognised"] = info["shape"] = kind
                # a cone has no one radius: OCCT gives the radius at its
                # reference plane, and the half angle
                info["radius"] = found.RefRadius() if kind == "cone" else found.Radius()
                if kind == "cone":
                    info["half_angle"] = found.SemiAngle()
                    a = found.Axis().Direction()
                    info["axis"] = (a.X(), a.Y(), a.Z())
                return info
        return info
    info["shape"] = ("plane" if ad.GetType() == GeomAbs_SurfaceType.GeomAbs_Plane
                     else "cylinder")
    if ad.GetType() == GeomAbs_SurfaceType.GeomAbs_Plane:
        info["normal"] = outward_normal(face)
    elif ad.GetType() == GeomAbs_SurfaceType.GeomAbs_Cylinder:
        cylinder = ad.Cylinder()
        a = cylinder.Axis().Direction()
        info["axis"] = (a.X(), a.Y(), a.Z())
        info["radius"] = cylinder.Radius()
        # a point on the axis, which the centre of mass is not: half a
        # cylinder's centroid is off to one side
        location = cylinder.Position().Location()
        info["axis_origin"] = (location.X(), location.Y(), location.Z())
    return info


def axis_role(normal) -> str | None:
    """Snap a normal to a named axis, if it is one."""
    for axis, name in AXES.items():
        if all(abs(normal[i] - axis[i]) < 1e-6 for i in range(3)):
            return name
    return None


def reroled(body: Body) -> Body:
    """After a rotation, a face named ``+x`` that now faces ``+y`` is renamed
    to say so; a face that faces no axis keeps its name. The old spelling
    stays as an alias when no other face has taken it."""
    renamed: dict = {}
    for index, (name, face) in enumerate(list(body.names)):
        parts = names.parse(name)
        role, sep, duplicate = parts.role.partition(names.DUPLICATE)
        if names.axis_of(role) is None:
            continue
        normal = face_info(face).get("normal")
        now = axis_role(normal) if normal is not None else None
        if now is None or now == role:
            continue
        new = names.spell(names.Parts(parts.feature, now + sep + duplicate, parts.scope, parts.suffixes))
        body.rename(index, new)
        renamed[name] = new
    if renamed:
        body.aliases = {a: renamed.get(c, c) for a, c in body.aliases.items()}
        taken = {n for n, _ in body.names}
        for old, new in renamed.items():
            if old not in taken:
                body.aliases[old] = new
    return body


def neighbours(body) -> dict:
    """Each face name to the base names of the faces it shares an edge with.

    Pieces of one split face count as their parent, so a piece appearing or
    moving inside a face is not a change of neighbourhood.
    """
    beside: dict = {}
    for key in body.edge_table():
        owners = [names.base(n) for n in body.edge_owners(key)]
        if len(owners) != 2 or owners[0] == owners[1]:
            continue
        a, b = owners
        beside.setdefault(a, set()).add(b)
        beside.setdefault(b, set()).add(a)
    return beside


@dataclass
class Body:
    """A shape plus the names of its faces. Edge names are derived on demand.

    A face carries exactly one canonical name. When a boolean welds two faces
    into one, the second parent's name becomes an alias of the survivor, so
    references written against either parent keep working.
    """

    shape: TopoDS_Shape
    names: list[tuple[str, TopoDS_Face]] = field(default_factory=list)
    aliases: dict = field(default_factory=dict)      # alias -> canonical
    dropped: list = field(default_factory=list)      # names whose face is gone
    notes: dict = field(default_factory=dict)        # what a feature wants to report
    _index: dict = field(default_factory=dict, repr=False, compare=False)
    _indexed: tuple = field(default=(), repr=False, compare=False)
    _edges: dict | None = field(default=None, repr=False, compare=False)
    _edges_at: tuple = field(default=(), repr=False, compare=False)
    _owners: dict = field(default_factory=dict, repr=False, compare=False)
    _volume: float | None = field(default=None, repr=False, compare=False)
    #: how many names `rename` has replaced in place, for the face index
    _renamed: int = field(default=0, repr=False, compare=False)
    #: set by the façade when the operation that made this body promised a
    #: valid shape; the evaluator checks it once per build (core/promises.py)
    promised_valid: bool = field(default=False, repr=False, compare=False)

    def rebuilt(self, shape=None, names=None, aliases=None, dropped=None,
                notes=None) -> "Body":
        """The same body with some fields replaced and the rest copied.

        ``notes`` (provenance, e.g. the bends of a sheet metal part) is carried
        over unless replaced, so dropping it is a decision, not an omission.
        """
        from dataclasses import replace
        return replace(
            self,
            shape=self.shape if shape is None else shape,
            names=list(self.names) if names is None else names,
            aliases=dict(self.aliases) if aliases is None else aliases,
            dropped=list(self.dropped) if dropped is None else dropped,
            notes=dict(self.notes) if notes is None else notes,
            _index={}, _indexed=(), _edges=None, _edges_at=(), _owners={},
            _volume=self._volume if shape is None else None,
            promised_valid=self.promised_valid if shape is None else False)

    def canonical(self, name: str) -> str:
        seen = set()
        while name in self.aliases and name not in seen:
            seen.add(name)
            name = self.aliases[name]
        return name

    # -- faces --------------------------------------------------------------
    def rename(self, index: int, name: str) -> None:
        """Give the face at `index` another name -- the one door, so the face
        index knows to rebuild."""
        self.names[index] = (name, self.names[index][1])
        self._renamed += 1

    def _token(self) -> tuple:
        """Identifies the current name list: its identity, length and how
        many names have been replaced through `rename`."""
        return (id(self.names), len(self.names), self._renamed)

    def _refresh(self) -> None:
        """Index faces by OCCT's hash; lookups are otherwise quadratic."""
        token = self._token()
        if token == self._indexed:
            return
        if (self._indexed and self._indexed[0] == token[0]
                and self._indexed[1] <= token[1] and self._indexed[2] == token[2]):
            start = self._indexed[1]                 # grown by appends: extend
        else:
            self._index, start = {}, 0
        for name, f in self.names[start:]:
            self._index.setdefault(hash(f), []).append((f, name))
        self._indexed = token

    def face(self, name: str) -> TopoDS_Face | None:
        name = self.canonical(name)
        for n, f in self.names:
            if n == name:
                return f
        return None

    def name_of(self, face: TopoDS_Face) -> str | None:
        self._refresh()
        for f, name in self._index.get(hash(face), ()):
            if f.IsSame(face):
                return name
        return None

    def face_names(self) -> list[str]:
        return [n for n, _ in self.names]

    # -- edges --------------------------------------------------------------
    def edge_table(self) -> dict[str, object]:
        """``{"faceA|faceB": edge}`` for every edge whose two faces are named.

        Cached until the name list changes.
        """
        if self._edges is not None and self._edges_at == self._token():
            return self._edges
        self._edges = self._build_edge_table()
        self._edges_at = self._token()
        return self._edges

    def edge_owners(self, key: str) -> tuple:
        """The face names an edge key was built from.

        The key cannot be parsed back: a face may be ``fillet1/side#2`` and the
        key appends its own ``#2`` for a second edge between the same faces, so
        the pair is recorded when the table is built.
        """
        self.edge_table()
        return self._owners.get(key, ())

    def _build_edge_table(self) -> dict[str, object]:
        mapping = IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(self.shape, TopAbs_EDGE, TopAbs_FACE, mapping)
        table: dict[str, object] = {}
        free: dict[str, list] = {}
        shared: dict[tuple, list] = {}
        self._owners = {}
        for i in range(1, mapping.Extent() + 1):
            edge = TopoDS.Edge_s(mapping.FindKey(i))
            owners, sides = [], []
            it = mapping.FindFromIndex(i)
            for face in it:
                nm = self.name_of(TopoDS.Face_s(face))
                if nm:
                    owners.append(nm)
                    sides.append(TopoDS.Face_s(face))
            if len(owners) == 1:
                # an open shell's boundary edges have one face; they are named
                # after it so surface work can refer to them
                free.setdefault(owners[0], []).append(edge)
                continue
            if len(owners) != 2 or sides[0].IsSame(sides[1]):
                # an unnamed neighbour, or a seam. Compared by face, not by
                # name: a cylinder's seam has the same face on both sides,
                # while two different faces can carry one name (the pieces of
                # a split face) and meet along a real edge
                continue
            shared.setdefault(tuple(sorted(owners)), []).append(edge)
        for owners, edges in shared.items():
            # two faces can meet along more than one edge; `#2` is assigned by
            # position, not explorer order, so it survives a dimension moving
            for k, edge in enumerate(sorted(edges, key=_edge_key)):
                key = names.edge(*owners) if k == 0 else \
                    names.edge(*owners, duplicate=k + 1)
                table[key] = edge
                self._owners[key] = tuple(sorted(owners))
        for owner, edges in free.items():
            # ordered by position, so `open#2` means the same edge after a
            # dimension moves
            for k, edge in enumerate(sorted(edges, key=_edge_key)):
                key = names.boundary(owner, k + 1)
                table[key] = edge
                self._owners[key] = (owner,)
        return table


def _edge_key(edge) -> tuple:
    """Sort key for an edge: centre of mass, then length.

    Length breaks the tie between two concentric circles, which share a centre.
    """
    props = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, props)
    c = props.CentreOfMass()
    return (round(c.X(), 6), round(c.Y(), 6), round(c.Z(), 6), round(props.Mass(), 6))


def is_hole(face) -> bool:
    """A hole's wall faces its own axis; a pin's faces away from it.

    Decided from the outward normal at the middle of the face, not the
    orientation flag: a mirror reverses the parametrisation without moving the
    axis, so the flag alone is wrong for every mirrored cylinder.
    """
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Pnt, gp_Vec

    ad = BRepAdaptor_Surface(face)
    if ad.GetType() != GeomAbs_SurfaceType.GeomAbs_Cylinder:
        recognised = _recognised(face, "cylinder")
        if recognised is None:
            return face.Orientation() == 1
        axis_dir, axis_loc = recognised.Axis().Direction(), recognised.Position().Location()
    else:
        cyl = ad.Cylinder()
        axis_dir, axis_loc = cyl.Axis().Direction(), cyl.Position().Location()
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    point, normal = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face).Normal(0.5 * (u0 + u1), 0.5 * (v0 + v1), point, normal)
    # the radial direction: from the axis to the point, with the axial part removed
    d = gp_Vec(axis_loc, point)
    along = gp_Vec(axis_dir.X(), axis_dir.Y(), axis_dir.Z())
    radial = d - along * d.Dot(along)
    if radial.Magnitude() < 1e-9 or normal.Magnitude() < 1e-9:
        return face.Orientation() == 1
    return normal.Dot(radial) < 0


def is_round(info: dict) -> bool:
    """Whether this face is a cylinder, declared as one or recognised as one.

    A STEP from another kernel writes a cylinder's wall as spline patches, so
    the declared type alone misses round faces on imported parts.
    """
    return (info.get("type") == GeomAbs_SurfaceType.GeomAbs_Cylinder
            or info.get("shape") == "cylinder")


def role_names(feature_id: str, faces: list[TopoDS_Face]) -> list[tuple[str, TopoDS_Face]]:
    """Name freshly created faces by what they are, not by their index.

    Planes become ``+z`` / ``-x`` ..., a cylinder's wall ``side``, other
    recognised surfaces their ``SHAPE_ROLES`` entry. Faces without a role fall
    back to ``faceN``, ordered by position rather than OCCT's face order.
    """
    named: list[tuple[str, TopoDS_Face]] = []
    leftovers: list[tuple[tuple, TopoDS_Face]] = []
    by_role: dict[str, list[tuple[tuple, TopoDS_Face]]] = {}

    for f in faces:
        info = face_info(f)
        role = None
        if "normal" in info:
            role = axis_role(info["normal"])
        elif is_round(info):
            role = "side"
        else:
            role = SHAPE_ROLES.get(info.get("shape"))
        c = info["centre"]
        where = (round(c[0], 6), round(c[1], 6), round(c[2], 6))
        if role is None:
            leftovers.append(((round(math.atan2(c[1], c[0]), 4), round(c[2], 6)), f))
            continue
        by_role.setdefault(role, []).append((where, f))

    # duplicates of one role are numbered by position, not explorer order, so
    # `side#2` means the same face after a dimension moves
    for role, group in by_role.items():
        for k, (_, f) in enumerate(sorted(group, key=lambda t: t[0]), start=1):
            named.append((names.face(feature_id, role, k), f))

    for k, (_, f) in enumerate(sorted(leftovers, key=lambda t: t[0])):
        named.append((names.face(feature_id, f"face{k}"), f))
    return named
