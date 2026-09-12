"""BREP -> Netgen mesh, with CAD face names carried onto the boundaries.

Boundary conditions attach to named faces rather than element numbers, so a
re-mesh after a parameter change puts loads back on the same faces.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from OCP.BRepTools import BRepTools

from ..errors import CadError
from ..geometry.core.naming import Body, face_info


def mesh_name(cad_name: str) -> str:
    """An injective, regex-safe boundary name for a CAD face name.

    NGSolve selects boundaries by regular expression, so every non-alphanumeric
    character (``_`` included) is escaped to ``_`` plus a unique code. Distinct
    CAD names (``plate/+z`` vs ``plate/-z``, ``a_b`` vs ``a/b``) therefore
    never share a boundary.
    """
    return "".join(c if c.isalnum() and c.isascii() else _ESCAPED.get(c) or "_x%04x" % ord(c)
                   for c in cad_name)


#: Escape codes for common face-name characters; no code is a prefix of
#: another, so the encoding is reversible.
_ESCAPED = {"/": "__", "+": "_p", "-": "_m", "@": "_at", "~": "_t", "|": "_or",
            "#": "_n", ".": "_d", "_": "_u", " ": "_s", ":": "_c"}


def require_face(body: Body, face_name: str) -> str:
    """Canonical name of ``face_name`` on ``body``; refuses with the available names."""
    if body.face(face_name) is None:
        raise CadError("unresolved_reference", f"no face named {face_name!r} on this body",
                       {"available": body.face_names(), "aliases": sorted(body.aliases)})
    return body.canonical(face_name)


def require_boundaries(meshed, faces, purpose: str = "boundary condition") -> None:
    """Refuse any named face that is not a mesh boundary.

    Applies to loaded faces as well as fixed ones: a load on a missing boundary
    integrates over nothing, giving near-zero stress and a huge safety factor.
    """
    from ..errors import CadError

    known = set(meshed.mesh.GetBoundaries())
    missing = [name for name in faces if mesh_name(name) not in known]
    if missing:
        raise CadError("boundary_not_in_mesh",
                       f"{purpose}: these faces are not in the mesh",
                       {"missing": missing, "mesh_boundaries": sorted(known),
                        "unmatched_by_the_mesher": list(meshed.unmatched)})


def floating(meshed, anchored) -> list:
    """Connected pieces of the mesh that no anchored face touches.

    A loose piece makes the stiffness matrix singular without the sparse
    Cholesky complaining, and the numbers read off it are noise; studies
    refuse before factorising.
    """
    import ngsolve as ng

    mesh = meshed.mesh
    parent = {}

    def root(v):
        while parent.setdefault(v, v) != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    sizes = {}
    for el in mesh.Elements(ng.VOL):
        first = root(el.vertices[0].nr)
        for v in el.vertices[1:]:
            parent[root(v.nr)] = first
    for el in mesh.Elements(ng.VOL):
        r = root(el.vertices[0].nr)
        sizes[r] = sizes.get(r, 0) + 1
    faces_of = {}
    for el in mesh.Elements(ng.BND):
        faces_of.setdefault(root(el.vertices[0].nr), set()).add(el.mat)
    anchors = {mesh_name(name) for name in anchored}
    loose = []
    for r, count in sizes.items():
        touched = faces_of.get(r, set())
        if touched & anchors:
            continue
        loose.append({"elements": count,
                      "faces": sorted(cad for cad, alias in meshed.boundary_of.items()
                                      if alias in touched)})
    return loose


def require_anchored(meshed, anchored, purpose: str = "fixed faces") -> None:
    """Refuse a mesh with a piece that nothing holds still."""
    from ..errors import CadError

    loose = floating(meshed, anchored)
    if loose:
        raise CadError("study_underconstrained",
                       "part of this body is not held by any %s" % purpose,
                       {"floating": loose, "anchored": list(anchored),
                        "hint": "a loose piece makes the system singular: its "
                                "displacement is arbitrary and every number "
                                "read off it is noise"})


@dataclass
class MeshedBody:
    ngmesh: object
    mesh: object                       # ngsolve.Mesh
    boundary_of: dict = field(default_factory=dict)   # cad name -> mesh name
    unmatched: list = field(default_factory=list)


def require_solver() -> None:
    """Refuse by kind when NGSolve is not installed, before anything is built."""
    try:
        import ngsolve  # noqa: F401
        import netgen.occ  # noqa: F401
    except ImportError as exc:
        raise CadError("missing_dependency",
                       "the studies need NGSolve, which is not installed",
                       {"module": getattr(exc, "name", None) or "ngsolve",
                        "hint": "pip install -r requirements-sim.txt"}) from exc


def build_mesh(body: Body, max_size: float = 6.0, order: int = 2,
               curvature_safety: float = 3.0) -> MeshedBody:
    require_solver()
    from netgen.occ import OCCGeometry
    from ngsolve import Mesh

    from ..geometry.core.measure import require_solid
    require_solid(body, "a finite element mesh")

    with tempfile.TemporaryDirectory() as tmp:
        brep = str(Path(tmp) / "body.brep")
        BRepTools.Write_s(body.shape, brep)
        # Face names must be set on the shape before OCCGeometry is built from
        # it; names set on an existing OCCGeometry do not reach the mesher.
        shape = OCCGeometry(brep).shape
        ng_faces = list(shape.faces)
        ours = [(name, face_info(face)) for name, face in body.names]
        if len(ng_faces) != len(ours):
            raise CadError("mesh_face_mismatch",
                           "netgen and the CAD core disagree on the face count",
                           {"netgen": len(ng_faces), "cad": len(ours)})

        # pair netgen faces with CAD faces by centroid and area; the centroid
        # alone cannot separate coaxial faces (a washer's bore and outer wall)
        boundary_of, unmatched, taken = {}, [], set()
        for i, ngf in enumerate(ng_faces):
            c = ngf.center
            area = float(ngf.mass)
            best, best_d = None, 1e30
            for name, info in ours:
                if name in taken:
                    continue
                cc = info["centre"]
                d = (cc[0] - c[0]) ** 2 + (cc[1] - c[1]) ** 2 + (cc[2] - c[2]) ** 2
                span = max(abs(info["area"]), abs(area), 1e-9)
                d += 1e-5 * ((info["area"] - area) / span) ** 2
                if d < best_d:
                    best, best_d = name, d
            if best is None or best_d > 1e-4:
                unmatched.append(i)
                continue
            taken.add(best)
            alias = mesh_name(best)
            if alias in boundary_of.values():
                raise CadError("mesh_face_mismatch",
                               "two faces would share one mesh boundary name",
                               {"boundary": alias, "faces": [best] + [
                                   n for n, m in boundary_of.items() if m == alias]})
            ngf.name = alias
            boundary_of[best] = alias

        geo = OCCGeometry(shape)
        ngmesh = geo.GenerateMesh(maxh=float(max_size), curvaturesafety=curvature_safety)
        if order > 1:
            ngmesh.SecondOrder()
        return MeshedBody(ngmesh, Mesh(ngmesh), boundary_of, unmatched)
