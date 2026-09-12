"""Studies declared inside the design document, re-run after a parameter change."""
from __future__ import annotations

import json

import operator
import re
from dataclasses import dataclass, field

from ..model.document import Document
from ..evaluation.graph import Evaluator
from ..errors import CadError
from ..geometry.assembly.assembly import bonded
from ..geometry.core.measure import solid_count
from .spec import check as spec_check
from .mesh import require_solver

from .. import progress

from .convergence import Convergence, run as run_convergence
from .studies.structural import Result, StaticStructural

OPS = {">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt, "==": operator.eq}


@dataclass
class Requirement:
    quantity: str
    op: str
    value: float

    @staticmethod
    def parse(quantity: str, spec) -> "Requirement":
        m = re.match(r"\s*(>=|<=|>|<|==)\s*([-\d.eE+]+)\s*$", str(spec))
        if not m:
            raise CadError("bad_requirement", f"cannot read requirement {spec!r}",
                           {"example": ">= 2"})
        try:
            value = float(m.group(2))
        except ValueError:
            raise CadError("bad_requirement", f"cannot read the number in {spec!r}",
                           {"example": ">= 2"}) from None
        return Requirement(quantity, m.group(1), value)

    def check(self, results: dict) -> tuple[bool, float]:
        if self.quantity not in results:
            raise CadError("unknown_quantity", f"no result called {self.quantity!r}",
                           {"available": sorted(results)})
        got = results[self.quantity]
        return OPS[self.op](got, self.value), got


@dataclass
class StudyOutcome:
    id: str
    result: Result
    requirements: list = field(default_factory=list)   # (Requirement, ok, value)
    convergence: Convergence | None = None
    field: object = None                               # the solved field, for display
    kind: str = "static_structural"

    @property
    def ok(self) -> bool:
        """True when every requirement holds and, if a convergence block was
        declared, the convergence check passed.

        A non-converged study does not pass whatever its requirements say,
        because the peak stress moves with element size. A study without a
        convergence block is not judged on one.
        """
        if not all(ok for _, ok, _ in self.requirements):
            return False
        return self.convergence is None or self.convergence.converged


def run_studies(doc: Document, cache: dict | None = None) -> list[StudyOutcome]:
    require_solver()
    ev = Evaluator(doc, cache)
    ev.build()
    out: list[StudyOutcome] = []
    # studies may be listed at the top level or under meta; a study written
    # in both places runs once
    listed = list(getattr(doc, "studies", []) or [])
    if isinstance(doc.meta, dict):
        seen = {json.dumps(spec, sort_keys=True, default=str) for spec in listed}
        listed += [spec for spec in doc.meta.get("studies", [])
                   if json.dumps(spec, sort_keys=True, default=str) not in seen]
    for index, spec in enumerate(listed):
        out.append(_run_one(doc, ev, spec))
        # no `total`: the number of solves depends on each study's
        # convergence block
        progress.step("study %d of %d done: %s"
                      % (index + 1, len(listed),
                         spec.get("id") or spec.get("type", "static_structural")))
    return out


def _run_one(doc: Document, ev: Evaluator, spec: dict) -> StudyOutcome:
    # validate the keys before meshing (see spec.py): an unread key such as
    # `load` for `loads` would otherwise solve an unloaded part
    kind = spec_check(spec)
    runner = RUNNERS[kind]
    body = ev.body_of(spec.get("body") or doc.result or doc.features[-1].id)
    if solid_count(body.shape) > 1:
        body = bonded(body)          # an assembly is studied glued at every contact
    result, values, conv, solved = runner(doc, body, spec)
    reqs = []
    for quantity, condition in (spec.get("require") or {}).items():
        requirement = Requirement.parse(quantity, condition)
        ok, got = requirement.check(values)
        reqs.append((requirement, ok, got))
    return StudyOutcome(spec.get("id", "study"), result, reqs, conv, solved, kind)


def _common(doc: Document, spec: dict) -> tuple:
    # a study without a material takes the part's own before the default, so
    # requirements are judged against the right yield strength
    material = spec.get("material") or (doc.meta or {}).get("material") or "A6061"
    return (material,
            float(doc.evaluate(spec.get("mesh_size", 6.0))),
            int(spec.get("order", 2)))


def _static(doc: Document, body, spec: dict) -> tuple:
    material, mesh_size, order = _common(doc, spec)
    study = StaticStructural(body, material, mesh_size, order)
    for face in spec.get("fix", []):
        study.fix(face)
    for load in spec.get("loads", []):
        study.force(load["face"], doc.evaluate(load["force"]))
    gravity = spec.get("gravity")
    if gravity:
        # `true` is 1 g down; a vector is the acceleration (mm/s^2); a number
        # is that many g straight down
        if isinstance(gravity, (list, tuple)):
            study.add_gravity([float(doc.evaluate(g)) for g in gravity])
        elif isinstance(gravity, bool):
            study.add_gravity()
        else:
            study.add_gravity((0.0, 0.0, -9810.0 * float(doc.evaluate(gravity))))

    conv, solved = None, None
    spec_conv = spec.get("convergence")
    if spec_conv:
        result, conv = run_convergence(
            study, mesh_size, int(spec_conv.get("levels", 3)),
            float(spec_conv.get("ratio", 1.5)), float(spec_conv.get("tolerance", 0.05)))
        solved = conv.field
    else:
        result, solved = study.solve()
    values = {"safety_factor": result.safety_factor,
              "safety_factor_p95": result.safety_factor_p95,
              "max_von_mises": result.max_von_mises,
              "p95_von_mises": result.p95_von_mises,
              "max_displacement": result.max_displacement,
              "mass_g": result.mass_g}
    return result, values, conv, solved


def _modal(doc: Document, body, spec: dict) -> tuple:
    from .studies.modal import Modal

    material, mesh_size, order = _common(doc, spec)
    study = Modal(body, material, mesh_size, order)
    for face in spec.get("fix", []):
        study.fix(face)
    for entry in spec.get("masses", []):
        study.add_mass(entry["face"], doc.evaluate(entry["kg"]))
    result, solved = study.solve(int(spec.get("modes", 6)))
    values = {"fundamental_Hz": result.frequencies[0] if result.frequencies else 0.0,
              "modes": len(result.frequencies)}
    return result, values, None, solved


def _thermal(doc: Document, body, spec: dict) -> tuple:
    from .studies.thermal import Thermal

    material, mesh_size, order = _common(doc, spec)
    study = Thermal(body, material, mesh_size, order,
                    float(doc.evaluate(spec.get("reference", 20.0))))
    for entry in spec.get("temperatures", []):
        study.temperature(entry["face"], doc.evaluate(entry["value"]))
    for entry in spec.get("fluxes", []):
        study.flux(entry["face"], doc.evaluate(entry["value"]))
    for entry in spec.get("convection", []):
        study.convection(entry["face"], doc.evaluate(entry["film"]),
                         doc.evaluate(entry["ambient"]))
    for face in spec.get("fix", []):
        study.fix(face)
    result, solved = study.solve()
    values = {"max_temperature": result.max_temperature,
              "min_temperature": result.min_temperature,
              "max_von_mises": result.max_von_mises,
              "p95_von_mises": result.p95_von_mises,
              "max_displacement": result.max_displacement}
    return result, values, None, solved


def _buckling(doc: Document, body, spec: dict) -> tuple:
    from .studies.buckling import Buckling

    material, mesh_size, order = _common(doc, spec)
    study = Buckling(body, material, mesh_size, order)
    for face in spec.get("fix", []):
        study.fix(face)
    for load in spec.get("loads", []):
        study.force(load["face"], doc.evaluate(load["force"]))
    result, static, solved = study.buckle(int(spec.get("modes", 4)))
    values = {"critical_factor": result.factors[0] if result.factors else 0.0,
              "max_von_mises": static.max_von_mises,
              "safety_factor": static.safety_factor}
    return result, values, None, solved


RUNNERS = {"static_structural": _static, "modal": _modal, "thermal": _thermal,
           "buckling": _buckling}
