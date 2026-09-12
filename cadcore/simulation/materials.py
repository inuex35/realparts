"""A small material library. Units are mm / N / MPa throughout."""
from __future__ import annotations

from dataclasses import dataclass

from ..errors import CadError


@dataclass(frozen=True)
class Material:
    name: str
    E: float          # Young's modulus  [MPa]
    nu: float         # Poisson ratio    [-]
    rho: float        # density          [t/mm^3] (so mass comes out in tonnes)
    yield_strength: float   # [MPa]
    # thermal properties in the mm/N/t/s system: conductivity in W/(mm.K)
    # (SI value / 1000), expansion in 1/K
    conductivity: float = 0.0
    expansion: float = 0.0

    @property
    def lame(self) -> tuple[float, float]:
        mu = self.E / (2 * (1 + self.nu))
        lam = self.E * self.nu / ((1 + self.nu) * (1 - 2 * self.nu))
        return mu, lam

    def elasticity(self, ng):
        """Small-strain and Hooke's-law forms as `(eps, sigma)`. ``ng`` is the
        ngsolve module, passed in so this file imports without it."""
        mu, lam = self.lame

        def eps(w):
            return 0.5 * (ng.Grad(w) + ng.Grad(w).trans)

        def sigma(w):
            return 2 * mu * eps(w) + lam * ng.Trace(eps(w)) * ng.Id(3)

        return eps, sigma


LIBRARY = {
    "A6061": Material("A6061-T6 aluminium", 68900, 0.33, 2.70e-9, 276, 0.167, 23.6e-6),
    "A5052": Material("A5052-H32 aluminium", 70300, 0.33, 2.68e-9, 193, 0.138, 23.8e-6),
    "S45C": Material("S45C carbon steel", 205000, 0.29, 7.85e-9, 490, 0.0451, 11.7e-6),
    "SUS304": Material("SUS304 stainless", 193000, 0.29, 8.00e-9, 215, 0.0162, 17.3e-6),
    "ABS": Material("ABS", 2200, 0.35, 1.04e-9, 40, 0.00017, 90e-6),
    "PLA": Material("PLA", 3500, 0.36, 1.24e-9, 50, 0.00013, 68e-6),
    "PA12-SLS": Material("PA12 (SLS)", 1700, 0.40, 1.01e-9, 48, 0.00024, 110e-6),
}


def get(name: str) -> Material:
    """A material by name; unknown names raise `CadError("unknown_material")`."""
    if name in LIBRARY:
        return LIBRARY[name]
    raise CadError("unknown_material", f"no material called {name!r}",
                   {"known": sorted(LIBRARY)})
