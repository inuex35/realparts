"""Declared keys for each study type, checked like feature arguments
(`cadcore.features.declare.schema.check`).

An unknown key (e.g. `load` for `loads`) is refused with the known keys listed,
a missing required key is refused before meshing, and a study with nothing
driving it is refused: an unloaded part has infinite safety factor and would
pass any requirement.
"""
from __future__ import annotations

from ..errors import CadError

#: Keys every study understands, whatever its type.
COMMON = {"id", "type", "body", "material", "mesh_size", "order", "require",
          "note", "fix"}

#: Per type: extra keys it accepts, keys it requires, and keys that must drive it.
KINDS: dict[str, dict] = {
    "static_structural": {
        "takes": {"loads", "gravity", "convergence"},
        "needs": set(),
        "driven_by": ("loads", "gravity"),
    },
    "modal": {
        # no `convergence`: only static_structural reads it, and accepting
        # it here would silently ignore it
        "takes": {"masses", "modes"},
        "needs": set(),
        "driven_by": (),
    },
    "thermal": {
        "takes": {"temperatures", "fluxes", "convection", "reference"},
        "needs": set(),
        "driven_by": ("temperatures", "fluxes", "convection"),
    },
    "buckling": {
        "takes": {"loads", "modes"},
        "needs": set(),
        "driven_by": ("loads",),
    },
}

#: Required fields of each entry of a list-valued key.
ENTRIES: dict[str, tuple] = {
    "loads": ("face", "force"),
    "masses": ("face", "kg"),
    "temperatures": ("face", "value"),
    "fluxes": ("face", "value"),
    "convection": ("face", "film", "ambient"),
}


def check(spec: dict) -> str:
    """Validate a study spec against its declared keys; returns the study type."""
    if not isinstance(spec, dict):
        raise CadError("bad_arguments", "a study is an object",
                       {"got": type(spec).__name__})
    kind = spec.get("type", "static_structural")
    known = KINDS.get(kind)
    if known is None:
        raise CadError("unknown_study_type",
                       f"study type {kind!r} is not implemented",
                       {"available": sorted(KINDS)})

    understood = COMMON | known["takes"]
    unknown = sorted(set(spec) - understood)
    if unknown:
        raise CadError(
            "unknown_argument",
            "a %s study does not understand: %s" % (kind, ", ".join(unknown)),
            {"study": spec.get("id", kind), "unknown": unknown,
             "understood": sorted(understood),
             "hint": "a key nothing reads is a load nothing applies"})

    missing = sorted(known["needs"] - set(spec))
    if missing:
        raise CadError("missing_argument",
                       "a %s study needs: %s" % (kind, ", ".join(missing)),
                       {"study": spec.get("id", kind), "missing": missing})

    for key, fields in ENTRIES.items():
        for index, entry in enumerate(spec.get(key) or []):
            if not isinstance(entry, dict):
                raise CadError("bad_arguments",
                               f"{key}[{index}] is not an object",
                               {"got": type(entry).__name__})
            short = sorted(set(fields) - set(entry))
            if short:
                raise CadError("missing_argument",
                               "%s[%d] needs: %s" % (key, index, ", ".join(short)),
                               {"needs": list(fields), "given": sorted(entry)})

    driven = known["driven_by"]
    if driven and not any(spec.get(key) for key in driven):
        raise CadError(
            "nothing_to_solve",
            "this %s study has nothing driving it: no %s"
            % (kind, ", no ".join(driven)),
            {"study": spec.get("id", kind), "expected_one_of": list(driven),
             "hint": "with nothing applied every result is zero and the safety "
                     "factor is infinite, which passes any requirement written "
                     "against it"})
    return kind
