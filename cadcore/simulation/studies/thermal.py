"""Steady-state heat conduction with optional thermal stress.

Boundary conditions attach to CAD face names: fixed temperatures, heat fluxes
and convection films. The stress solve reuses the static elasticity with the
thermal expansion as a prestress.

Units: mm, N, W, kelvin. Conductivity W/(mm.K), flux W/mm^2, film coefficient
W/(mm^2.K).
"""
from __future__ import annotations

from dataclasses import dataclass

from ...errors import CadError
from ...geometry.core.naming import Body

from ..field import Field, by_volume
from ..materials import Material, get as get_material
from ..mesh import (MeshedBody, build_mesh, mesh_name, require_anchored, require_boundaries,
                    require_face)


@dataclass
class ThermalResult:
    max_temperature: float
    min_temperature: float
    max_von_mises: float = 0.0        # MPa, from constrained expansion
    p95_von_mises: float = 0.0        # MPa, 95th percentile
    max_displacement: float = 0.0     # mm
    dofs: int = 0
    elements: int = 0
    reference: float = 20.0

    def as_dict(self) -> dict:
        return {"max_temperature_C": round(self.max_temperature, 3),
                "min_temperature_C": round(self.min_temperature, 3),
                "max_von_mises_MPa": round(self.max_von_mises, 3),
                "p95_von_mises_MPa": round(self.p95_von_mises, 3),
                "max_displacement_mm": round(self.max_displacement, 5),
                "reference_C": self.reference, "dofs": self.dofs,
                "elements": self.elements}


class Thermal:
    def __init__(self, body: Body, material: str | Material = "A6061",
                 mesh_size: float = 6.0, order: int = 2, reference: float = 20.0):
        self.body = body
        self.material = material if isinstance(material, Material) else get_material(material)
        if self.material.conductivity <= 0:
            raise CadError("no_material_property",
                           f"{self.material.name} has no conductivity in the library")
        self.mesh_size = mesh_size
        self.order = order
        self.reference = reference
        self.temperatures: list[tuple[str, float]] = []
        self.fluxes: list[tuple[str, float]] = []
        self.films: list[tuple[str, float, float]] = []
        self.fixed: list[str] = []

    def temperature(self, face_name: str, value: float) -> "Thermal":
        self.temperatures.append((require_face(self.body, face_name), float(value)))
        return self

    def flux(self, face_name: str, watts_per_mm2: float) -> "Thermal":
        self.fluxes.append((require_face(self.body, face_name), float(watts_per_mm2)))
        return self

    def convection(self, face_name: str, film: float, ambient: float) -> "Thermal":
        self.films.append((require_face(self.body, face_name), float(film), float(ambient)))
        return self

    def fix(self, face_name: str) -> "Thermal":
        """Clamp a face for the thermal stress solve."""
        self.fixed.append(require_face(self.body, face_name))
        return self

    def solve(self, meshed: MeshedBody | None = None):
        import ngsolve as ng

        if not (self.temperatures or self.films):
            raise CadError("study_underconstrained",
                           "a thermal study needs a temperature or a convection face",
                           {"hint": "heat flux alone has no equilibrium"})
        meshed = meshed or build_mesh(self.body, self.mesh_size, self.order)
        mesh = meshed.mesh
        require_boundaries(meshed, [f for f, _ in self.temperatures], "temperature faces")
        require_boundaries(meshed, [f for f, _ in self.fluxes], "flux faces")
        require_boundaries(meshed, [f for f, _, _ in self.films], "convection faces")
        require_boundaries(meshed, self.fixed, "fixed faces")
        # every piece needs a temperature or convection face to set its level;
        # flux alone has no equilibrium
        require_anchored(meshed, [f for f, _ in self.temperatures] + [f for f, _, _ in self.films],
                         "temperature or convection face")
        if self.fixed:
            require_anchored(meshed, self.fixed)
        dirichlet = "|".join(mesh_name(n) for n, _ in self.temperatures)
        fes = ng.H1(mesh, order=self.order, dirichlet=dirichlet or "")
        u, v = fes.TnT()
        k = self.material.conductivity

        a = ng.BilinearForm(k * ng.grad(u) * ng.grad(v) * ng.dx)
        f = ng.LinearForm(fes)
        for face, film, ambient in self.films:
            region = mesh.Boundaries(mesh_name(face))
            a += film * u * v * ng.ds(definedon=region)
            f += film * ambient * v * ng.ds(definedon=region)
        for face, watts in self.fluxes:
            f += watts * v * ng.ds(definedon=mesh.Boundaries(mesh_name(face)))
        a.Assemble()
        f.Assemble()

        temperature = ng.GridFunction(fes)
        if self.temperatures:
            # one Set over all faces: Set zeroes the dofs it does not touch
            fixed = {mesh_name(face): value for face, value in self.temperatures}
            temperature.Set(mesh.BoundaryCF(fixed, default=0.0),
                            definedon=mesh.Boundaries("|".join(fixed)))
        residual = f.vec.CreateVector()
        residual.data = f.vec - a.mat * temperature.vec
        temperature.vec.data += a.mat.Inverse(fes.FreeDofs(),
                                              inverse="sparsecholesky") * residual

        # read min/max off a first-order field: for order >= 2 the coefficient
        # vector holds hierarchical bubbles, not nodal values
        shown = ng.GridFunction(ng.H1(mesh, order=1))
        shown.Set(temperature)
        values = [float(x) for x in shown.vec]
        result = ThermalResult(max(values), min(values), reference=self.reference,
                               dofs=fes.ndof, elements=mesh.ne)

        stress_field = None
        if self.fixed:
            (result.max_von_mises, result.p95_von_mises, result.max_displacement,
             stress_field) = self._thermal_stress(mesh, temperature)
        # floor=None: a temperature may be below 0 and must not be clipped
        return result, (stress_field
                        or Field(mesh, shown, shown, temperature, floor=None,
                                 quantity="temperature_C"))

    def _thermal_stress(self, mesh, temperature):
        """Elasticity with the expansion of the heated material as a prestress."""
        import ngsolve as ng

        fes = ng.VectorH1(mesh, order=self.order,
                          dirichlet="|".join(mesh_name(n) for n in self.fixed))
        u, v = fes.TnT()
        mu, lam = self.material.lame
        alpha = self.material.expansion
        eps, sigma = self.material.elasticity(ng)

        thermal = (3 * lam + 2 * mu) * alpha * (temperature - self.reference)
        a = ng.BilinearForm(ng.InnerProduct(sigma(u), eps(v)) * ng.dx)
        f = ng.LinearForm(thermal * ng.Trace(eps(v)) * ng.dx)
        a.Assemble()
        f.Assemble()
        gfu = ng.GridFunction(fes)
        gfu.vec.data = a.mat.Inverse(fes.FreeDofs(), inverse="sparsecholesky") * f.vec

        s = sigma(gfu) - thermal * ng.Id(3)
        dev = s - (ng.Trace(s) / 3) * ng.Id(3)
        vm_expr = ng.sqrt(1.5 * ng.InnerProduct(dev, dev))
        vm = ng.GridFunction(ng.H1(mesh, order=1))
        vm.Set(vm_expr)
        disp = ng.GridFunction(ng.H1(mesh, order=1))
        disp.Set(ng.sqrt(ng.InnerProduct(gfu, gfu)))
        return (max(abs(x) for x in vm.vec), by_volume(mesh, vm_expr),
                max(abs(x) for x in disp.vec),
                Field(mesh, vm, disp, gfu, quantity="von_mises_MPa"))
