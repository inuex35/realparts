"""Linear buckling: the load factor at which the part becomes unstable.

A static solve supplies the prestress for the geometric stiffness; the
eigenproblem between elastic and geometric stiffness gives the load
multipliers. Linear buckling ignores imperfections and yielding, so the factor
is an upper bound on elastic instability, not a safety factor.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ...errors import CadError

from ..field import Field
from .structural import StaticStructural


@dataclass
class BucklingResult:
    factors: list = field(default_factory=list)     # load multipliers, ascending
    dofs: int = 0
    elements: int = 0

    def as_dict(self) -> dict:
        return {"load_factors": [round(f, 4) for f in self.factors],
                "critical_factor": round(self.factors[0], 4) if self.factors else None,
                "dofs": self.dofs, "elements": self.elements}


class Buckling(StaticStructural):
    """A static study extended with a linear buckling eigenproblem."""

    def buckle(self, modes: int = 4, meshed=None):
        import ngsolve as ng

        result, field_ = self.solve(meshed)
        gfu = field_.solution
        fes = gfu.space
        u, v = fes.TnT()
        eps, sigma = self.material.elasticity(ng)

        stiffness = ng.BilinearForm(ng.InnerProduct(sigma(u), eps(v)) * ng.dx)
        # geometric stiffness: prestress acting through element rotation
        geometric = ng.BilinearForm(
            -ng.InnerProduct(sigma(gfu) * ng.Grad(u).trans, ng.Grad(v).trans) * ng.dx)
        stiffness.Assemble()
        geometric.Assemble()

        # Arnoldi finds the eigenvalues nearest the shift; a part whose critical
        # factor is far from one fills the slots with negative ones, so the
        # shift is moved out until a positive factor is found
        found = []
        for shift in (1.0, 10.0, 100.0, 1000.0):
            vectors = [ng.GridFunction(fes) for _ in range(modes + 4)]
            lambdas = ng.ArnoldiSolver(stiffness.mat, geometric.mat, fes.FreeDofs(),
                                       [g.vec for g in vectors], shift=shift)
            # unordered; sorted with their vectors so each mode shape stays paired with its factor
            found = sorted(((l.real, vector) for l, vector in zip(lambdas, vectors)
                            if l.real > 1e-6 and abs(l.imag) < 1e-6),
                           key=lambda pair: pair[0])
            if found:
                break
        if not found:
            raise CadError("no_buckling_mode",
                           "no positive buckling factor was found",
                           {"hint": "the load may put the part in tension"})
        factors = [factor for factor, _ in found]
        out = BucklingResult(factors[:modes], result.dofs, result.elements)

        critical = found[0][1]
        shape = ng.GridFunction(ng.H1(fes.mesh, order=1))
        shape.Set(ng.sqrt(ng.InnerProduct(critical, critical)))
        peak = max(abs(x) for x in shape.vec) or 1.0
        shape.vec.data = (1.0 / peak) * shape.vec
        return out, result, Field(fes.mesh, shape, shape, critical)
