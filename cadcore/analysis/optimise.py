"""Vary the parameters a document declares in `parameters_bounds`, rebuild,
keep the best.

The document's `asserts` and studies decide feasibility. Two objectives:
`volume` needs only the kernel; `mass` requires every declared study to pass
and costs a solve per trial.
"""
from __future__ import annotations

import random

from .. import progress
from ..errors import CadError


def place_inside(trial, numeric: dict, draw: dict) -> tuple:
    """Move the trial's parameters toward ``draw``, keeping the sample inside
    the declared envelope.

    A sample the asserts reject is bisected back toward the base design until
    it lands inside, so nearly every sample counts. Returns (inside, blend);
    a blend of 0.02 or less is reported as not inside.
    """
    def apply(blend: float) -> bool:
        for key, target in draw.items():
            base = numeric[key]
            value = base + (target - base) * blend
            trial.parameters[key] = int(round(value)) if isinstance(base, int) \
                else round(value, 4)
        return trial.in_envelope()[0]

    if apply(1.0):
        return True, 1.0
    low, high = 0.0, 1.0
    for _ in range(8):                       # bisect toward the base design
        middle = (low + high) / 2
        if apply(middle):
            low = middle
        else:
            high = middle
    # the last apply may have been the one that failed, and it is the trial's
    # parameters it wrote: put the blend that fits back before handing it over
    apply(low)
    return (low > 0.02), low


def _weigh(doc, cache, objective: str) -> tuple:
    """Return (cost, report) for a design, or (None, {}) when it is not
    feasible: it did not build, or a declared study did not pass."""
    if objective == "volume":
        from ..geometry import kernel
        from ..evaluation.api import build

        try:
            body, _ = build(doc, cache)      # build hands back the evaluator too
            volume = kernel.volume(body)
        except CadError:
            return None, {}
        return volume, {"volume_mm3": round(volume, 3)}

    from ..simulation.study import require_solver, run_studies

    require_solver()          # not a bad design: nothing to search with, said by kind
    try:
        outcomes = run_studies(doc, cache)
    except CadError:
        return None, {}
    if not outcomes or not all(o.ok for o in outcomes):
        return None, {}
    # the first study that reports a mass, not the first study: a modal result
    # has no `mass_g` (checked by tests/test_optimise.py)
    weighed = next((o.result for o in outcomes
                    if hasattr(o.result, "mass_g")), None)
    if weighed is None:
        raise CadError(
            "nothing_to_optimise",
            "no study here weighs the part, so there is nothing to make lighter",
            {"studies": [o.kind for o in outcomes],
             "hint": "a static_structural study reports a mass and a safety "
                     "factor; a modal or thermal one does not"})
    return weighed.mass_g, {"mass_g": round(weighed.mass_g, 3),
                            "safety_factor": round(weighed.safety_factor, 3)}


def search(doc, cache=None, params=None, trials: int = 30, step: float = 0.15,
           seed: int = 0, objective: str = "mass") -> dict:
    """Look for the lightest design the document still allows.

    Random search, mostly stepping locally around the best so far: the space
    is small and an assert is a wall rather than a slope.
    """
    if objective not in ("mass", "volume"):
        raise CadError("bad_parameter", "objective is 'mass' or 'volume'",
                       {"given": objective})
    free = [p for p in (params or doc.bounds) if p in doc.bounds]
    if not free:
        raise CadError("nothing_to_optimise",
                       "no parameter has a declared range to move inside",
                       {"hint": "add parameters_bounds to the document",
                        "asked_for": list(params or []),
                        "declared": sorted(doc.bounds)})

    from copy import deepcopy

    rng = random.Random(seed)
    base = {p: float(doc.evaluate(doc.parameters[p])) for p in free}
    best, history, feasible = None, [], 0
    progress.total(trials)
    for index in range(trials):
        # report progress in `finally`: a trial the envelope refuses or that
        # would not build `continue`s, and still counts as a trial
        try:
            trial = deepcopy(doc)
            if index == 0:
                inside, why = trial.in_envelope()
                if not inside:               # the design as it stands is not a feasible trial
                    continue
            else:
                draw = {}
                for name in free:
                    lo, hi = (float(trial.evaluate(b)) for b in trial.bounds[name])
                    if best and rng.random() < 0.7:      # near the best so far
                        span = (hi - lo) * step
                        value = best["parameters"][name] + rng.uniform(-span, span)
                    else:
                        value = rng.uniform(lo, hi)
                    draw[name] = min(hi, max(lo, value))
                if not place_inside(trial, base, draw)[0]:
                    continue
            cost, said = _weigh(trial, cache, objective)
            if cost is None:
                continue
            feasible += 1
            values = {p: float(trial.evaluate(trial.parameters[p])) for p in free}   # numbers, not expressions
            line = dict(said, trial=index, parameters=values)
            history.append(line)
            if best is None or cost < best["cost"]:
                best = dict(line, cost=cost, parameters=dict(values))
        finally:
            progress.step(
                "trial %d of %d, %d feasible%s"
                % (index + 1, trials, feasible,
                   "" if best is None
                   else ", best %s %.4g" % (objective, best["cost"])))

    if best is None:
        raise CadError("no_feasible_design",
                       "no trial both built and stayed inside the envelope",
                       {"trials": trials, "parameters": free})
    start = next((h for h in history if h["trial"] == 0), None)
    return {"objective": objective, "parameters": best["parameters"],
            "best": {k: v for k, v in best.items()
                     if k not in ("parameters", "cost", "trial")},
            "was": {k: v for k, v in (start or {}).items()
                    if k not in ("parameters", "trial")},
            "trials": trials, "feasible": feasible, "moved": free,
            "history": history}
