"""Measure the document's requirements against the part as built.

The rows and the quantities are `cadcore.model.requirements`; this is where
each quantity is read off the geometry, which the model layer may not touch.
"""
from __future__ import annotations

from ..errors import CadError
from ..geometry.core.measure import extents
from ..model.requirements import OPS, QUANTITIES, check


def measure(quantity: str, spec: dict, doc, body, built: dict, printability) -> float:
    """The current value of one quantity, in the unit `QUANTITIES` says."""
    if quantity == "volume_mm3":
        return float(built["volume_mm3"])
    if quantity == "area_mm2":
        return float(built["area_mm2"])
    if quantity == "faces":
        return float(built["faces"])
    if quantity == "solid":
        return 1.0 if built["kind"] == "solid" and built["open_boundaries"] == 0 else 0.0
    if quantity.startswith("bbox_"):
        ext = extents(body)
        if quantity == "bbox_max":
            return max(ext)
        if quantity == "bbox_min":
            return min(ext)
        return ext["xyz".index(quantity[-1])]
    if quantity == "mass_g":
        # inside the function, so that a machine without the solver still
        # opens documents -- the same rule every reach into `simulation` keeps
        from ..simulation.materials import get as material

        name = spec.get("material") or (doc.meta or {}).get("material") or "A6061"
        return float(built["volume_mm3"]) * material(name).rho * 1e6     # t -> g
    if quantity == "printable":
        report = printability(min_wall=float(spec.get("min_wall", 1.5)),
                              overhang_deg=float(spec.get("overhang", 45.0)))
        return 1.0 if report.get("ok") else 0.0
    raise CadError("unknown_quantity", f"no quantity called {quantity!r}",
                   {"available": sorted(QUANTITIES)})


def status(doc, body, built: dict, printability) -> list:
    """Every requirement in the document, measured against the part as built.

    Never raises for a row that cannot be measured: the row says so, and the
    others are still answered. A requirements list is a dashboard, and a
    dashboard with one dead gauge is not a reason to switch the others off.
    """
    out = []
    for index, spec in enumerate(doc.requirements):
        row = {"id": spec.get("id") or "r%d" % (index + 1),
               "quantity": spec.get("quantity"), "compare": spec.get("compare"),
               "value": spec.get("value"), "unit": ""}
        try:
            check(spec)
            want = float(doc.evaluate(spec["value"]))
            got = measure(spec["quantity"], spec, doc, body, built, printability)
            row.update(value=want, got=round(got, 4), ok=bool(OPS[spec["compare"]](got, want)),
                       unit=QUANTITIES[spec["quantity"]].unit)
        except CadError as exc:
            row.update(got=None, ok=False, error="%s: %s" % (exc.kind, exc.message))
        out.append(row)
    return out
