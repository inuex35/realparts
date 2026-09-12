"""What the part must be, written in the document and checked on every build.

The rows and the quantities they may name live here; measuring them against
the built part is `cadcore.analysis.requirements`, which may reach the
geometry. This layer does not."""
from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Callable

from ..errors import CadError

OPS: dict[str, Callable] = {"<=": operator.le, "<": operator.lt, ">=": operator.ge,
                            ">": operator.gt, "==": operator.eq, "!=": operator.ne}


@dataclass(frozen=True)
class Quantity:
    """One thing the kernel can measure about the built part."""

    unit: str
    about: str
    #: True for a quantity that costs a real computation (a printability scan),
    #: so the panel can say why a rebuild took longer
    costly: bool = False
    #: extra keys a requirement of this quantity may carry
    takes: tuple = ()


QUANTITIES: dict[str, Quantity] = {
    "volume_mm3": Quantity("mm3", "the solid's volume"),
    "area_mm2": Quantity("mm2", "the solid's surface area"),
    "mass_g": Quantity("g", "volume times the density of `material` -- the "
                            "requirement's, else the document's `meta.material`, "
                            "else A6061", takes=("material",)),
    "faces": Quantity("", "how many faces the solid has"),
    "bbox_x": Quantity("mm", "extent along x"),
    "bbox_y": Quantity("mm", "extent along y"),
    "bbox_z": Quantity("mm", "extent along z"),
    "bbox_max": Quantity("mm", "the longest extent, whichever axis"),
    "bbox_min": Quantity("mm", "the shortest extent, whichever axis"),
    "solid": Quantity("", "1 when the result is one closed solid, else 0"),
    "printable": Quantity("", "1 when the printability check passes at "
                              "`min_wall` (1.5 mm by default) and "
                              "`overhang` degrees (45), else 0",
                          costly=True, takes=("min_wall", "overhang")),
}


def check(spec: dict) -> None:
    """Refuse a requirement the kernel cannot measure, by name."""
    if not isinstance(spec, dict):
        raise CadError("bad_requirement", "a requirement is an object",
                       {"got": type(spec).__name__})
    quantity = spec.get("quantity")
    if quantity not in QUANTITIES:
        raise CadError("unknown_quantity",
                       f"no quantity called {quantity!r} to require",
                       {"available": sorted(QUANTITIES)})
    if "op" in spec and "compare" not in spec:
        raise CadError("bad_requirement",
                       "a requirement's comparison is called `compare`, not `op`",
                       {"hint": "op is the protocol's word for the operation"})
    if spec.get("compare") not in OPS:
        raise CadError("bad_requirement",
                       f"{spec.get('compare')!r} is not a comparison",
                       {"available": sorted(OPS)})
    if "value" not in spec:
        raise CadError("bad_requirement", "a requirement needs a value",
                       {"quantity": quantity})
    allowed = {"id", "quantity", "compare", "value", "note", *QUANTITIES[quantity].takes}
    unknown = sorted(set(spec) - allowed)
    if unknown:
        raise CadError("unknown_argument",
                       "a %s requirement does not understand: %s"
                       % (quantity, ", ".join(unknown)),
                       {"understood": sorted(allowed)})
