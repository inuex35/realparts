"""Mesh convergence: solve the same study on a refined mesh sequence.

Peak von Mises stress converges slowest (fillets, hole edges), and a single
coarse run can be 30% low. The result says whether the peak settled within a
tolerance and gives a Richardson extrapolation of the converged value.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Level:
    mesh_size: float
    elements: int
    dofs: int
    max_von_mises: float
    p95_von_mises: float
    max_displacement: float
    change: float | None = None       # relative to the previous, coarser level
    refined: bool | None = None       # at least REFINED times the previous dofs


#: A level counts as a refinement of the one before when its unknowns grew by
#: this factor. Netgen's own size limits (curvature, small faces) can leave a
#: smaller ``maxh`` with nearly the same mesh, and then a small change says
#: nothing about the answer.
REFINED = 1.3


@dataclass
class Convergence:
    levels: list = field(default_factory=list)
    converged: bool = False
    tolerance: float = 0.05
    extrapolated_von_mises: float | None = None
    field: object = None              # the finest level's solved field, for display

    def as_dict(self) -> dict:
        return {
            "converged": self.converged,
            "tolerance": self.tolerance,
            "extrapolated_von_mises_MPa":
                None if self.extrapolated_von_mises is None
                else round(self.extrapolated_von_mises, 3),
            "levels": [{"mesh_size": round(l.mesh_size, 3), "elements": l.elements,
                        "dofs": l.dofs, "max_von_mises_MPa": round(l.max_von_mises, 3),
                        "p95_von_mises_MPa": round(l.p95_von_mises, 3),
                        "max_displacement_mm": round(l.max_displacement, 5),
                        "change": None if l.change is None else round(l.change, 4),
                        "refined": l.refined}
                       for l in self.levels],
        }


def run(study, base_size: float, levels: int = 3, ratio: float = 1.5,
        tolerance: float = 0.05):
    """Solve at ``levels`` refinements and return the finest result.

    At least two levels are required: a single solve has nothing to compare
    with and would report `converged = False`, failing the study.
    """
    from .. import progress
    from ..errors import CadError

    if int(levels) < 2:
        raise CadError("bad_parameter",
                       "a convergence study needs at least two levels",
                       {"levels": int(levels),
                        "hint": "one mesh is a result, not a convergence: drop "
                                "the convergence block to solve once"})
    out = Convergence(tolerance=tolerance)
    last = None
    result = None
    for k in range(levels):
        size = base_size / (ratio ** k)
        study.mesh_size = size
        result, out.field = study.solve()
        lvl = Level(size, result.elements, result.dofs, result.max_von_mises,
                    result.p95_von_mises, result.max_displacement)
        if last is not None and last.max_von_mises > 1e-12:
            # judged on the peak: a peak that does not settle is a singularity
            # (re-entrant corner, clamped face), which is what to report
            lvl.change = abs(lvl.max_von_mises - last.max_von_mises) / last.max_von_mises
            lvl.refined = lvl.dofs >= REFINED * last.dofs
        out.levels.append(lvl)
        last = lvl
        progress.step("mesh level %d of %d: %d elements, peak %.4g MPa"
                      % (k + 1, levels, result.elements, result.max_von_mises))
    if len(out.levels) >= 2 and out.levels[-1].change is not None:
        out.converged = bool(out.levels[-1].refined) and out.levels[-1].change <= tolerance
    if len(out.levels) >= 3 and all(l.refined for l in out.levels[-2:]):
        # Richardson extrapolation with the observed convergence ratio; only
        # over levels that really were refinements of each other
        s1, s2, s3 = (l.max_von_mises for l in out.levels[-3:])
        denom = (s2 - s1)
        if abs(denom) > 1e-12:
            r = (s3 - s2) / denom
            if 0 < r < 1:
                out.extrapolated_von_mises = s3 + (s3 - s2) * r / (1 - r)
    # the study keeps the finest mesh size for later readings
    return result, out
