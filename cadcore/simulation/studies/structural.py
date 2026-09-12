"""Linear static structural analysis on a CAD body, by CAD face name. Units: mm, N, MPa.

Linear elastic, small deformation, isotropic; a fixed face is clamped whole,
a load is a uniform traction; no contact, plasticity or fatigue. The peak at
a clamp is a singularity, so `p95_von_mises` is reported beside it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ...errors import CadError
from ...geometry.core.measure import volume
from ...geometry.core.naming import Body, face_info

from ..field import Field, by_volume
from ..materials import Material, get as get_material
from ..mesh import (MeshedBody, build_mesh, mesh_name, require_anchored, require_boundaries,
                    require_face)


def check_solved(matrix, solution, rhs, free) -> None:
    """Refuse a solution whose relative residual on the free dofs exceeds 1e-6.

    A direct solver given a singular matrix returns numbers without raising;
    the residual check catches that.
    """
    import math

    from ...errors import CadError

    residual = rhs.CreateVector()
    residual.data = rhs - matrix * solution
    top = bottom = 0.0
    for i, (r, b) in enumerate(zip(residual, rhs)):
        if not free[i]:
            continue
        top += float(r) ** 2
        bottom += float(b) ** 2
    worst = max((abs(float(x)) for x in solution), default=0.0)
    relative = math.sqrt(top) / math.sqrt(bottom) if bottom > 0 else math.sqrt(top)
    if not math.isfinite(worst) or not math.isfinite(relative) or relative > 1e-6:
        raise CadError("study_failed",
                       "the stiffness matrix is singular: the solution does not "
                       "satisfy the equations",
                       {"relative_residual": relative, "largest_dof": worst,
                        "hint": "a piece of the body that nothing holds, or a "
                                "degenerate element"})


@dataclass
class Result:
    max_von_mises: float          # MPa (peak; diverges at geometric/BC singularities)
    p95_von_mises: float          # MPa; 95% of the material by volume is below this
    max_displacement: float       # mm
    safety_factor: float          # yield / peak; conservative, singular at a clamp
    mass_g: float
    dofs: int
    elements: int
    #: yield / p95: a property of the material as a whole, not of the one
    #: node at a clamp edge
    safety_factor_p95: float = float("inf")
    reactions: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"max_von_mises_MPa": round(self.max_von_mises, 3),
                "p95_von_mises_MPa": round(self.p95_von_mises, 3),
                "max_displacement_mm": round(self.max_displacement, 5),
                "safety_factor": round(self.safety_factor, 3),
                "safety_factor_p95": round(self.safety_factor_p95, 3),
                "mass_g": round(self.mass_g, 2),
                "dofs": self.dofs, "elements": self.elements}


class StaticStructural:
    def __init__(self, body: Body, material: str | Material = "A6061",
                 mesh_size: float = 6.0, order: int = 2):
        self.body = body
        self.material = material if isinstance(material, Material) else get_material(material)
        self.mesh_size = mesh_size
        self.order = order
        self.fixed: list[str] = []
        self.loads: list[tuple[str, tuple[float, float, float]]] = []
        self.gravity: tuple[float, float, float] | None = None

    def fix(self, face_name: str) -> "StaticStructural":
        self.fixed.append(require_face(self.body, face_name))
        return self

    def force(self, face_name: str, vector) -> "StaticStructural":
        """Total force in N, spread uniformly over the face."""
        self.loads.append((require_face(self.body, face_name),
                           tuple(float(v) for v in vector)))
        return self

    def add_gravity(self, g=(0, 0, -9810.0)) -> "StaticStructural":
        self.gravity = tuple(float(v) for v in g)      # mm/s^2
        return self

    def solve(self, meshed: MeshedBody | None = None) -> tuple[Result, "Field"]:
        import ngsolve as ng

        if not self.fixed:
            raise CadError("study_underconstrained", "a static study needs at least one fixed face",
                           {"hint": "study.fix('plate/-z')"})
        meshed = meshed or build_mesh(self.body, self.mesh_size, self.order)
        mesh = meshed.mesh

        dirichlet = "|".join(mesh_name(n) for n in self.fixed)
        require_boundaries(meshed, self.fixed, "fixed faces")
        require_boundaries(meshed, [face for face, _ in self.loads], "loaded faces")
        require_anchored(meshed, self.fixed)

        fes = ng.VectorH1(mesh, order=self.order, dirichlet=dirichlet)
        u, v = fes.TnT()
        eps, sigma = self.material.elasticity(ng)

        a = ng.BilinearForm(ng.InnerProduct(sigma(u), eps(v)) * ng.dx)
        f = ng.LinearForm(fes)
        for face, vec in self.loads:
            area = self._face_area(face)
            traction = ng.CoefficientFunction(tuple(c / area for c in vec))   # N/mm^2
            f += ng.InnerProduct(traction, v) * ng.ds(definedon=mesh.Boundaries(mesh_name(face)))
        if self.gravity:
            bodyf = ng.CoefficientFunction(tuple(self.material.rho * g for g in self.gravity))
            f += ng.InnerProduct(bodyf, v) * ng.dx
        a.Assemble()
        f.Assemble()

        gfu = ng.GridFunction(fes)
        inv = a.mat.Inverse(fes.FreeDofs(), inverse="sparsecholesky")
        gfu.vec.data = inv * f.vec
        check_solved(a.mat, gfu.vec, f.vec, fes.FreeDofs())

        s = sigma(gfu)
        dev = s - (ng.Trace(s) / 3) * ng.Id(3)
        vm_expr = ng.sqrt(1.5 * ng.InnerProduct(dev, dev))
        vm = ng.GridFunction(ng.H1(mesh, order=1))
        vm.Set(vm_expr)
        disp = ng.GridFunction(ng.H1(mesh, order=1))
        disp.Set(ng.sqrt(ng.InnerProduct(gfu, gfu)))

        field = Field(mesh, vm, disp, gfu)
        max_vm = max(abs(x) for x in vm.vec)
        p95 = by_volume(mesh, vm_expr)
        max_disp = max(abs(x) for x in disp.vec)
        mass_g = volume(self.body) * self.material.rho * 1e6      # mm^3 * t/mm^3 -> g
        sf = float("inf") if max_vm <= 1e-9 else self.material.yield_strength / max_vm
        sf95 = float("inf") if p95 <= 1e-9 else self.material.yield_strength / p95
        return Result(max_vm, p95, max_disp, sf, mass_g, fes.ndof, mesh.ne,
                      safety_factor_p95=sf95), field

    def _face_area(self, face_name: str) -> float:
        face = self.body.face(face_name)
        area = face_info(face)["area"]
        if area <= 0:
            raise CadError("bad_face", f"face {face_name!r} has no area")
        return area
