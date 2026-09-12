"""Measuring a body: frames, sizes, and what a ray hits.

Questions asked of geometry rather than operations on it, kept apart from the
operations so that measuring a body does not import everything that could have
built it.
"""
from __future__ import annotations

import math

from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Shape

from ... import names
from ...errors import CadError
from .naming import Body, face_info
from .occ import bounds

__all__ = ["face_frame", "first_hit", "edge_geometry", "bounding_span", "volume",
           "area", "crossing_angle", "kind_of", "is_solid", "require_solid"]


# --- what a shape is -----------------------------------------------------------
def kind_of(shape: TopoDS_Shape) -> str:
    """What a shape is, as a plain word: solid, shell, face, compound or empty.

    A compound is read by what it contains at any depth, so an assembly of
    solids is a solid.
    """
    direct = {TopAbs_ShapeEnum.TopAbs_SOLID: "solid",
              TopAbs_ShapeEnum.TopAbs_COMPSOLID: "solid",
              TopAbs_ShapeEnum.TopAbs_SHELL: "shell",
              TopAbs_ShapeEnum.TopAbs_FACE: "face"}
    if shape.ShapeType() in direct:
        return direct[shape.ShapeType()]
    for level, word in ((TopAbs_ShapeEnum.TopAbs_SOLID, "solid"),
                        (TopAbs_ShapeEnum.TopAbs_SHELL, "shell"),
                        (TopAbs_ShapeEnum.TopAbs_FACE, "face")):
        if TopExp_Explorer(shape, level).More():
            return word
    return "compound" if not shape.IsNull() else "empty"


def is_solid(body: Body) -> bool:
    return kind_of(body.shape) == "solid"


def require_solid(body: Body, what: str) -> None:
    """Refuse an operation that needs a closed solid, and say what would fix it."""
    if is_solid(body):
        return
    raise CadError("not_a_solid", f"{what} needs a closed solid, not a {kind_of(body.shape)}",
                   {"kind": kind_of(body.shape),
                    "hint": "sew the faces into a shell and thicken it, or cap it"})



def face_frame(body: Body, face_name: str) -> dict:
    """The sketch plane of a named planar face: origin, normal, x axis.

    The x axis is global X projected onto the face (global Z when the face
    faces X), not OCCT's surface parametrisation, which can flip between
    rebuilds.
    """
    face = body.face(face_name)
    if face is None:
        raise CadError("unknown_face", f"no face named {face_name!r}",
                       {"known": sorted(body.face_names())})

    # a name aliased to one piece of a split face would give that piece's
    # centroid, not the original face's, so it is refused rather than defaulted
    resolved = body.canonical(face_name)
    if resolved != face_name and names.parse(resolved).piece is not None \
            and names.parse(face_name).piece is None:
        siblings = sorted(n for n in body.face_names()
                          if names.base(n) == names.base(resolved))
        raise CadError(
            "face_was_split",
            f"{face_name!r} is no longer one face: a boolean cut it into "
            f"{len(siblings)} pieces, so there is no one place to measure from",
            {"pieces": siblings[:8], "count": len(siblings),
             "hint": "name the piece you mean, or position from a datum plane "
                     "instead of from this face"})

    info = face_info(face)
    if "normal" not in info:
        raise CadError("non_planar_face", f"face {face_name!r} is not planar",
                       {"surface": str(info["type"])})
    n = info["normal"]
    ref = (0.0, 0.0, 1.0) if abs(n[0]) > 0.9 else (1.0, 0.0, 0.0)
    x = [ref[i] - n[i] * sum(ref[k] * n[k] for k in range(3)) for i in range(3)]
    length = math.sqrt(sum(c * c for c in x))
    if length < 1e-9:                                  # unreachable by choice of ref
        raise CadError("degenerate_frame", f"cannot orient a frame on {face_name!r}")
    x = [c / length for c in x]
    return {"origin": [round(c, 9) for c in info["centre"]],
            "normal": [round(c, 9) for c in n],
            "x_axis": [round(c, 9) for c in x],
            "area": round(info["area"], 6)}


KINDS = ("linear", "angle", "diameter", "radius", "centres", "area", "distance")


def solid_count(shape: TopoDS_Shape) -> int:
    """How many separate solids a shape holds.

    Two means a boolean left its arguments unjoined in one compound; OCCT does
    not call that an error and the volumes add up, but later booleans misbehave.
    """
    explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SOLID)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def dimension(body: Body, kind: str, references: list) -> float:
    """Measure something on the model, by the names of the faces involved.

    Always measured from the geometry, never stored. ``linear`` is the distance
    between two planar faces along their common normal, ``angle`` the angle
    between two planar faces, ``centres`` the distance between two cylinders'
    axes, and ``diameter``/``radius``/``area`` read one face.
    """
    infos = []
    for name in references:
        face = body.face(name)
        if face is None:
            raise CadError("unresolved_reference",
                           f"no face named {name!r} on this body",
                           {"available": body.face_names()})
        infos.append((name, face_info(face)))

    def need(count: int):
        if len(infos) != count:
            raise CadError("bad_arguments",
                           f"a {kind} measurement needs {count} face"
                           f"{'' if count == 1 else 's'}",
                           {"given": list(references)})

    def planar(name, info):
        if "normal" not in info:
            raise CadError("not_planar", f"face {name!r} is not planar",
                           {"surface": str(info.get("type"))})
        return info["normal"]

    def round_face(name, info):
        radius = info.get("radius")
        if radius is None:
            raise CadError("not_a_cylinder", f"{name!r} is not cylindrical")
        return radius

    if kind == "linear":
        need(2)
        (_, a), (second, b) = infos
        # one normal is enough, and taking it from whichever face has one lets
        # a plane be measured against the flat of something turned
        normal = a.get("normal") or b.get("normal")
        if normal is None:
            raise CadError("not_planar", "a linear dimension needs a planar face",
                           {"faces": list(references)})
        delta = [b["centre"][i] - a["centre"][i] for i in range(3)]
        return abs(sum(delta[i] * normal[i] for i in range(3)))

    if kind == "angle":
        need(2)
        (first, a), (second, b) = infos
        u, v = planar(first, a), planar(second, b)
        # outward normals of a 30 degree wedge are 150 degrees apart; the
        # drawing angle is the supplement
        cosine = max(-1.0, min(1.0, sum(u[i] * v[i] for i in range(3))))
        return 180.0 - math.degrees(math.acos(cosine))

    if kind == "centres":
        need(2)
        (first, a), (second, b) = infos
        round_face(first, a), round_face(second, b)
        axis = a.get("axis")
        if axis is None:
            raise CadError("not_a_cylinder", f"{first!r} has no axis")
        delta = [b["axis_origin"][i] - a["axis_origin"][i] for i in range(3)]
        along = sum(delta[i] * axis[i] for i in range(3))
        across = [delta[i] - along * axis[i] for i in range(3)]
        return math.sqrt(sum(c * c for c in across))

    if kind in ("diameter", "radius"):
        need(1)
        radius = round_face(*infos[0])
        return radius * (2 if kind == "diameter" else 1)

    if kind == "area":
        need(1)
        return infos[0][1]["area"]

    if kind == "distance":
        need(2)
        return nearest(body.face(references[0]), body.face(references[1]))["distance"]

    raise CadError("unknown_dimension", f"dimension type {kind!r} is not implemented",
                   {"available": list(KINDS)})


def nearest(a, b) -> dict:
    """The shortest distance between two shapes, and where it is measured."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    gap = BRepExtrema_DistShapeShape(a, b)
    gap.Perform()
    if not gap.IsDone() or gap.NbSolution() < 1:
        raise CadError("no_intersection", "no shortest distance could be found")
    p, q = gap.PointOnShape1(1), gap.PointOnShape2(1)
    return {"distance": gap.Value(), "from": [p.X(), p.Y(), p.Z()], "to": [q.X(), q.Y(), q.Z()]}


def mass_properties(body: Body, density: float | None = None) -> dict:
    """Volume, centre of mass and the inertia matrix about it; with ``density`` (t/mm^3), mass in g."""
    require_solid(body, "mass properties")
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.shape, props)
    centre = props.CentreOfMass()
    matrix = props.MatrixOfInertia()
    inertia = [[matrix.Value(i, j) for j in (1, 2, 3)] for i in (1, 2, 3)]     # mm^5, about the centre of mass
    out = {"volume_mm3": round(props.Mass(), 6),
           "centre_of_mass": [round(c, 6) for c in (centre.X(), centre.Y(), centre.Z())],
           "inertia_mm5": [[round(v, 3) for v in row] for row in inertia]}
    principal = props.PrincipalProperties()
    moments = principal.Moments()
    out["principal_moments_mm5"] = [round(float(m), 3) for m in moments]
    if density:
        scale = float(density) * 1e6                          # tonnes to grams
        out["mass_g"] = round(props.Mass() * scale, 6)
        out["inertia_g_mm2"] = [[round(v * scale, 3) for v in row] for row in inertia]
    return out


def curvature(body: Body, face_name: str, at=None) -> dict:
    """The curvature of a face at a point: principal, mean and Gaussian, and the normal.

    ``at`` is a world point projected onto the face; the face's middle when
    left out. Radii are 1/curvature, ``None`` when flat that way.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.BRep import BRep_Tool
    from OCP.gp import gp_Dir as _Dir

    face = body.face(face_name)
    if face is None:
        raise CadError("unresolved_reference", f"no face named {face_name!r} on this body",
                       {"available": body.face_names()})
    adaptor = BRepAdaptor_Surface(face)
    if at is not None:
        project = GeomAPI_ProjectPointOnSurf(gp_Pnt(*[float(c) for c in at]), BRep_Tool.Surface_s(face))
        if project.NbPoints() < 1:
            raise CadError("cannot_project", "the point has no nearest point on the face")
        u, v = project.LowerDistanceParameters()
    else:
        u = (adaptor.FirstUParameter() + adaptor.LastUParameter()) / 2
        v = (adaptor.FirstVParameter() + adaptor.LastVParameter()) / 2
    props = BRepLProp_SLProps(adaptor, u, v, 2, 1e-9)
    if not props.IsCurvatureDefined():
        raise CadError("degenerate_frame", f"the curvature of {face_name!r} is not defined there")
    point = props.Value()
    smallest, largest = props.MinCurvature(), props.MaxCurvature()
    d_max, d_min = _Dir(), _Dir()
    props.CurvatureDirections(d_max, d_min)
    normal = props.Normal()
    from OCP.TopAbs import TopAbs_Orientation
    sign = -1.0 if face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED else 1.0

    def principal(k: float, d) -> dict:
        # OCCT counts bending away from the surface normal as negative; read with the
        # outward normal and turned round, a boss is positive and a bore negative
        k = -k * sign
        return {"curvature": round(k, 9), "radius_mm": None if abs(k) < 1e-9 else round(1.0 / k, 6),
                "direction": [round(c, 9) for c in (d.X(), d.Y(), d.Z())]}

    tightest, flattest = sorted([principal(largest, d_max), principal(smallest, d_min)],
                                key=lambda q: -abs(q["curvature"]))
    return {"face": face_name,
            "at": [round(c, 6) for c in (point.X(), point.Y(), point.Z())],
            "normal": [round(c * sign, 9) for c in (normal.X(), normal.Y(), normal.Z())],
            "tightest": tightest, "flattest": flattest,
            "mean_curvature": round(-props.MeanCurvature() * sign, 9),
            "gaussian_curvature": round(props.GaussianCurvature(), 12)}


def first_hit(shape, point, direction, minimum: float = 1e-3,
              crossing: float = 0.0) -> float | None:
    """Distance along a ray to the first face beyond ``minimum``, or None.

    ``crossing`` rejects grazing hits: a hit whose crossing angle is not above
    it is skipped, since a nearly tangent surface near a fillet is not a face
    to stop at.
    """
    from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter

    direction = list(direction)
    length = math.sqrt(sum(c * c for c in direction)) or 1.0
    direction = [c / length for c in direction]
    intersector = BRepIntCurveSurface_Inter()
    try:
        intersector.Init(shape, gp_Lin(gp_Pnt(*[float(c) for c in point]),
                                       gp_Dir(*direction)), 1e-6)
    except Exception:                                            # noqa: BLE001
        return None
    best = None
    while intersector.More():
        w = intersector.W()
        if w > minimum and (best is None or w < best):
            angle = crossing_angle(intersector, direction)
            if crossing <= 0 or angle is None or abs(angle) > crossing:
                best = w
        intersector.Next()
    return best


def crossing_angle(intersector, direction) -> float | None:
    """Dot of the ray direction with the outward normal at the hit.

    Positive means leaving the solid, negative entering, near zero grazing.
    ``None`` when the surface cannot be differentiated there; callers keep the
    hit in that case.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopAbs import TopAbs_Orientation
    from OCP.gp import gp_Vec

    try:
        face = intersector.Face()
        adaptor = BRepAdaptor_Surface(face)
        point, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
        adaptor.D1(intersector.U(), intersector.V(), point, du, dv)
        normal = du.Crossed(dv)
        if normal.Magnitude() < 1e-12:
            return 0.0
        normal.Normalize()
        outward = [normal.X(), normal.Y(), normal.Z()]
        if face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED:
            outward = [-c for c in outward]
    except Exception:                                            # noqa: BLE001
        return None
    return sum(direction[i] * outward[i] for i in range(3))


def edge_geometry(edge) -> tuple:
    """An edge as a line (start, end) or a circle (centre, axis, radius).

    Any other curve type is returned by its OCCT name with an empty dict.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_CurveType

    curve = BRepAdaptor_Curve(edge)
    kind = curve.GetType()
    if kind == GeomAbs_CurveType.GeomAbs_Line:
        first, last = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
        return "line", {"start": (first.X(), first.Y(), first.Z()),
                        "end": (last.X(), last.Y(), last.Z())}
    if kind == GeomAbs_CurveType.GeomAbs_Circle:
        circle = curve.Circle()
        centre, axis = circle.Location(), circle.Axis().Direction()
        return "circle", {"centre": (centre.X(), centre.Y(), centre.Z()),
                          "axis": (axis.X(), axis.Y(), axis.Z()),
                          "radius": circle.Radius()}
    return str(kind).split("GeomAbs_")[-1].lower(), {}


def edge_points(edge, count: int = 24) -> list:
    """``count`` points at equal arc length along an edge, world mm."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformAbscissa

    curve = BRepAdaptor_Curve(edge)
    sampler = GCPnts_QuasiUniformAbscissa(curve, max(2, int(count)))
    if not sampler.IsDone():
        raise CadError("cannot_project", "the edge could not be sampled")
    return [[round(c, 9) for c in (curve.Value(sampler.Parameter(i)).X(),
                                   curve.Value(sampler.Parameter(i)).Y(),
                                   curve.Value(sampler.Parameter(i)).Z())]
            for i in range(1, sampler.NbPoints() + 1)]


def edge_axis(body: Body, edge_name: str) -> tuple:
    """A straight edge as a point on it and its direction: the hinge a work
    plane turns about. A curved edge has no one direction, and is refused."""
    edge = body.edge_table().get(edge_name)
    if edge is None:
        raise CadError("unresolved_reference", f"no edge named {edge_name!r} on this body",
                       {"asked_for": edge_name})
    kind, geometry = edge_geometry(edge)
    if kind != "line":
        raise CadError("bad_arguments",
                       f"a plane turns about a straight edge; {edge_name!r} is a {kind}",
                       {"edge": edge_name, "kind": kind})
    start, end = geometry["start"], geometry["end"]
    direction = [end[i] - start[i] for i in range(3)]
    length = math.sqrt(sum(c * c for c in direction))
    if length < 1e-9:
        raise CadError("degenerate_frame", f"the edge {edge_name!r} has no length",
                       {"edge": edge_name})
    return list(start), [c / length for c in direction]


def turned(vector, axis, radians: float) -> list:
    """`vector` turned about `axis` (a unit direction) by `radians`.

    Rodrigues: v cos t + (k x v) sin t + k (k . v)(1 - cos t).
    """
    cos, sin = math.cos(radians), math.sin(radians)
    dot = sum(axis[i] * vector[i] for i in range(3))
    cross = [axis[1] * vector[2] - axis[2] * vector[1],
             axis[2] * vector[0] - axis[0] * vector[2],
             axis[0] * vector[1] - axis[1] * vector[0]]
    return [vector[i] * cos + cross[i] * sin + axis[i] * dot * (1.0 - cos)
            for i in range(3)]


def extents(body: Body) -> tuple:
    """The bounding box's size along x, y and z, in millimetres."""
    box = Bnd_Box()
    BRepBndLib.Add_s(body.shape, box, False)
    xmin, ymin, zmin, xmax, ymax, zmax = bounds(box)
    return (xmax - xmin, ymax - ymin, zmax - zmin)


def bounds_of(body: Body) -> tuple:
    """The bounding box as its low and high corners, in mm."""
    box = Bnd_Box()
    BRepBndLib.Add_s(body.shape, box, False)
    xmin, ymin, zmin, xmax, ymax, zmax = bounds(box)
    return (xmin, ymin, zmin), (xmax, ymax, zmax)


def bounding_span(body: Body) -> float:
    """The bounding box diagonal: a length no feature can exceed."""
    box = Bnd_Box()
    BRepBndLib.Add_s(body.shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = bounds(box)
    return math.sqrt((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2)


def volume(body: Body) -> float:
    """Enclosed volume, zero for anything that is not a solid.

    OCCT integrates an open shell anyway and returns an origin-dependent
    number, so non-solids answer zero.
    """
    if body._volume is None:
        if not is_solid(body):
            return 0.0
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(body.shape, props)
        body._volume = props.Mass()
    return body._volume


def area(body: Body) -> float:
    """Total surface area."""
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(body.shape, props)
    return props.Mass()


def axis_span(body: Body, face_name: str) -> dict:
    """A round face's axis: its foot, direction, radius, and how far along it the face runs."""
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.BRep import BRep_Tool

    face = body.face(face_name)
    if face is None:
        raise CadError("unresolved_reference", f"no face named {face_name!r} on this body",
                       {"available": body.face_names()})
    info = face_info(face)
    if "axis" not in info or "radius" not in info:
        raise CadError("not_a_cylinder", f"{face_name!r} has no axis to wind round")
    origin, d = info["axis_origin"], info["axis"]
    along = []
    from OCP.TopoDS import TopoDS
    walk = TopExp_Explorer(face, TopAbs_ShapeEnum.TopAbs_VERTEX)
    while walk.More():
        p = BRep_Tool.Pnt_s(TopoDS.Vertex_s(walk.Current()))
        along.append(sum((q - origin[i]) * d[i] for i, q in enumerate((p.X(), p.Y(), p.Z()))))
        walk.Next()
    low, high = (min(along), max(along)) if along else (0.0, 0.0)
    return {"foot": [origin[i] + d[i] * low for i in range(3)], "direction": list(d),
            "radius": float(info["radius"]), "length": high - low,
            "bore": _material_outside(body, info, origin, d)}


def _material_outside(body: Body, info: dict, origin, d) -> bool:
    """Whether a point just outside the round face is inside the part: a bore, not a boss."""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_State

    c = info["centre"]
    along = sum((c[i] - origin[i]) * d[i] for i in range(3))
    on_axis = [origin[i] + d[i] * along for i in range(3)]
    radial = [c[i] - on_axis[i] for i in range(3)]        # towards the face; on the axis for a full round
    size = math.sqrt(sum(v * v for v in radial))
    if size < 1e-6:
        seed = (0.0, 0.0, 1.0) if abs(d[2]) < 0.9 else (1.0, 0.0, 0.0)
        radial = [d[1] * seed[2] - d[2] * seed[1], d[2] * seed[0] - d[0] * seed[2],
                  d[0] * seed[1] - d[1] * seed[0]]
        size = math.sqrt(sum(v * v for v in radial))
    r = float(info["radius"]) + 0.01
    probe = gp_Pnt(*[on_axis[i] + radial[i] / size * r for i in range(3)])
    classifier = BRepClass3d_SolidClassifier(body.shape)
    classifier.Perform(probe, 1e-7)
    return classifier.State() == TopAbs_State.TopAbs_IN


def helix_start(foot, direction, radius: float) -> dict:
    """Where a helix wound about this axis starts, and its tangent and a side direction there."""
    from OCP.gp import gp_Ax3

    frame = gp_Ax3(gp_Pnt(*[float(c) for c in foot]), gp_Dir(*[float(c) for c in direction]))
    x, y = frame.XDirection(), frame.YDirection()
    return {"start": [foot[i] + radius * c for i, c in enumerate((x.X(), x.Y(), x.Z()))],
            "tangent": [y.X(), y.Y(), y.Z()], "x_axis": [x.X(), x.Y(), x.Z()]}
