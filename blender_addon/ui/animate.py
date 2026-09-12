"""The document's parameters, mirrored onto the CAD object as ID properties.

An ID property can be keyframed and driven and is saved in the .blend; a
scene collection's value cannot. Ranges come from `parameters_bounds`.
Reacting to a moved value belongs to :mod:`state`, which owns the connection.
"""
from __future__ import annotations

import bpy

HANDLED = "cadcore_parameters"           # ID property listing the names this module owns
SOFT = 4.0                               # slider range multiplier when no bounds are declared


def publish(ob: bpy.types.Object, parameters: dict, bounds: dict | None = None) -> int:
    """Mirror the document's numeric parameters onto the object, with their ranges.

    Expressions are skipped: a slider writing over one would turn it into a constant.
    """
    if ob is None:
        return 0
    bounds = bounds or {}
    owned = []
    for name, value in (parameters or {}).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        ob[name] = number
        low, high = bounds.get(name, (None, None)) if name in bounds else (None, None)
        if low is None:
            span = abs(number) * SOFT or SOFT
            low, high = min(0.0, number - span), number + span
            hard = False
        else:
            hard = True
        ui = ob.id_properties_ui(name)
        ui.update(min=float(low), max=float(high),
                  soft_min=float(low), soft_max=float(high),
                  description=("%s (%g to %g)" % (name, low, high) if hard
                               else "%s -- no envelope declared" % name))
        owned.append(name)
    stale = [n for n in list(ob.get(HANDLED, [])) if n not in owned]
    for name in stale:
        ob.pop(name, None)               # a parameter the document no longer has
    ob[HANDLED] = owned
    return len(owned)


def owned(ob: bpy.types.Object) -> list:
    return list(ob.get(HANDLED, [])) if ob else []


def values(ob: bpy.types.Object) -> dict:
    """The object's current slider values."""
    return {name: float(ob[name]) for name in owned(ob) if name in ob}


def differs(ob: bpy.types.Object, tolerance: float = 1e-9) -> dict:
    """Parameters whose slider value differs from what the kernel last agreed."""
    return {name: value for name, value in values(ob).items()
            if abs(value - float(_agreed.get(name, value + 1))) > tolerance}


# What the kernel last agreed to; read only by `differs`.
_agreed: dict = {}


def agreed() -> dict:
    return dict(_agreed)


def remember(parameters: dict) -> None:
    """Record what the kernel now holds so `differs` can tell what moved."""
    _agreed.clear()
    _agreed.update({k: float(v) for k, v in (parameters or {}).items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)})


def note(changed: dict) -> None:
    _agreed.update(changed)


def forget() -> None:
    _agreed.clear()
