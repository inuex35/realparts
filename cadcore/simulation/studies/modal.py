"""Free vibration (modal analysis) with the same mesh and face-named
boundary conditions as the static study.

Frequencies come out in Hz because the unit system is mm / N / tonne / second.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ...errors import CadError
from ...geometry.core.naming import Body, face_info

from ..field import Field
from ..materials import Material, get as get_material
from ..mesh import (MeshedBody, build_mesh, mesh_name, require_anchored, require_boundaries,
                    require_face)


@dataclass
class ModalResult:
    frequencies: list = field(default_factory=list)     # Hz, ascending
    dofs: int = 0
    elements: int = 0

    def as_dict(self) -> dict:
        return {"frequencies_Hz": [round(f, 4) for f in self.frequencies],
                "fundamental_Hz": round(self.frequencies[0], 4) if self.frequencies else None,
                "dofs": self.dofs, "elements": self.elements}


class Modal:
    def __init__(self, body: Body, material: str | Material = "A6061",
                 mesh_size: float = 6.0, order: int = 2):
        self.body = body
        self.material = material if isinstance(material, Material) else get_material(material)
        self.mesh_size = mesh_size
        self.order = order
        self.fixed: list[str] = []
        self.masses: list[tuple[str, float]] = []

    def add_mass(self, face_name: str, kilograms: float) -> "Modal":
        """Add a non-modelled mass (kg), spread over a face."""
        self.masses.append((require_face(self.body, face_name), float(kilograms)))
        return self

    def fix(self, face_name: str) -> "Modal":
        self.fixed.append(require_face(self.body, face_name))
        return self

    def solve(self, modes: int = 6, meshed: MeshedBody | None = None):
        import ngsolve as ng
        from ngsolve.solvers import PINVIT

        if not self.fixed:
            raise CadError("study_underconstrained",
                           "a modal study needs at least one fixed face",
                           {"hint": "an unconstrained part has six zero-frequency modes"})
        meshed = meshed or build_mesh(self.body, self.mesh_size, self.order)
        mesh = meshed.mesh
        require_boundaries(meshed, self.fixed, "fixed faces")
        require_boundaries(meshed, [face for face, _ in self.masses], "mass faces")
        require_anchored(meshed, self.fixed)
        dirichlet = "|".join(mesh_name(n) for n in self.fixed)
        fes = ng.VectorH1(mesh, order=self.order, dirichlet=dirichlet)
        u, v = fes.TnT()
        mu, lam = self.material.lame
        eps, _ = self.material.elasticity(ng)

        stiffness = ng.BilinearForm(
            (2 * mu * ng.InnerProduct(eps(u), eps(v))
             + lam * ng.Trace(eps(u)) * ng.Trace(eps(v))) * ng.dx)
        mass = ng.BilinearForm(self.material.rho * ng.InnerProduct(u, v) * ng.dx)
        for face, kilograms in self.masses:
            area = face_info(self.body.face(face))["area"]
            if area <= 0:
                raise CadError("bad_face", f"face {face!r} has no area")
            # tonnes per mm^2
            density = (kilograms / 1000.0) / area
            mass += density * ng.InnerProduct(u, v) * ng.ds(
                definedon=mesh.Boundaries(mesh_name(face)))
        stiffness.Assemble()
        mass.Assemble()

        pre = stiffness.mat.Inverse(fes.FreeDofs(), inverse="sparsecholesky")
        lambdas, vectors = PINVIT(stiffness.mat, mass.mat, pre, num=modes, maxit=60,
                                  printrates=False, GramSchmidt=True)
        # sort eigenvalues together with their vectors so the mode shape stays
        # paired with its frequency
        pairs = sorted(zip(lambdas, vectors), key=lambda lv: abs(float(lv[0])))
        # a clearly negative eigenvalue is not a vibration (free rigid body or
        # non-converged solve) and is refused; slop about zero is tolerated
        floor = -1e-6 * max((abs(float(l)) for l, _ in pairs), default=1.0)
        for value, _ in pairs:
            if float(value) < floor:
                raise CadError(
                    "study_failed",
                    "this model has a negative vibration mode, so it is not "
                    "held still enough to have a frequency",
                    {"eigenvalue": float(value),
                     "hint": "every rigid body motion has to be fixed: a part "
                             "free to slide or spin has no fundamental"})
        frequencies = [math.sqrt(max(float(l), 0.0)) / (2 * math.pi)
                       for l, _ in pairs]
        result = ModalResult(frequencies, fes.ndof, mesh.ne)

        # first mode shape as a displacement magnitude field
        mode = ng.GridFunction(fes)
        mode.vec.data = pairs[0][1]
        amplitude = ng.GridFunction(ng.H1(mesh, order=1))
        amplitude.Set(ng.sqrt(ng.InnerProduct(mode, mode)))
        peak = max(abs(x) for x in amplitude.vec) or 1.0
        amplitude.vec.data = (1.0 / peak) * amplitude.vec      # normalised, 0..1
        return result, Field(mesh, amplitude, amplitude, mode)
