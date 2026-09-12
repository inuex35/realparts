"""The registry itself: what a feature type is, and how one is registered.

Kept apart from the package ``__init__`` so imports run one way: handlers
import the registry, the package imports the handlers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...errors import CadError
from .schema import Text, check, references

# every feature may carry a note; it builds nothing and is for the people
# reading the document
COMMON = {"note": Text(note="a human note, ignored by the kernel")}


#: what a feature must produce to count as a result; a datum or a sketch is
#: evaluated but is not one
BODY_KINDS = frozenset({"solid", "surface", "assembly"})

@dataclass(frozen=True)
class FeatureType:
    """One kind of feature, and what a caller can be told about it."""

    name: str
    build: Callable
    category: str                 # solid, boolean, modify, surface, thread, ...
    produces: str                 # solid, surface, datum, assembly, nothing
    summary: str                  # the handler's first docstring line
    args: dict                    # the argument declaration, from schema.py
    #: which argument this feature's result can stand in for when the feature
    #: is removed or suppressed: a fillet's body, a cut's target (not its
    #: tool). Not derivable from the argument kinds, since both are references.
    stands_for: str | None = None
    #: hooks for a feature whose arguments the declaration cannot describe;
    #: a sketch's geometry is declared `Anything()`, and without these the unit
    #: converter would leave it unscaled. They add to the declaration walk
    #: rather than replacing it, so declared arguments are still found.
    own_lengths: Callable | None = None
    own_angles: Callable | None = None

    def as_dict(self) -> dict:
        return {"name": self.name, "category": self.category,
                "produces": self.produces, "summary": self.summary,
                "stands_for": self.stands_for,
                "args": {k: v.as_dict() for k, v in self.args.items()}}

    def passthrough(self, args: dict) -> str | None:
        """What this feature's result could be replaced by, if anything."""
        if self.stands_for is None:
            return None
        value = args.get(self.stands_for)
        return value if isinstance(value, str) else None

    def check(self, args: dict, where: str = "") -> None:
        """Refuse arguments this feature did not declare."""
        check(self.name, self.args, args, where)

    def files(self, args: dict) -> list:
        """Which of this feature's arguments name a file on disk."""
        from .schema import files
        return files({**self.args, **COMMON}, args)

    def lengths(self, args: dict) -> list:
        """Which of this feature's arguments are lengths, as paths into them."""
        from .schema import lengths
        found = lengths({**self.args, **COMMON}, args)
        if self.own_lengths is not None:
            found += self.own_lengths(args)
        return found

    def angles(self, args: dict) -> list:
        """Which of this feature's arguments are angles, as paths into them."""
        from .schema import angles
        found = angles({**self.args, **COMMON}, args)
        if self.own_angles is not None:
            found += self.own_angles(args)
        return found

    def dimensionless(self, args: dict) -> list:
        """Which of them are counts or ratios -- numbers with no unit at all."""
        from .schema import dimensionless
        return dimensionless({**self.args, **COMMON}, args)

    def geometry_names(self, args: dict) -> list:
        """Which of this feature's arguments point at a face or an edge."""
        from .schema import geometry_names
        return geometry_names({**self.args, **COMMON}, args)

    def references(self, args: dict) -> list:
        """Which of these arguments name another feature."""
        return references(self.args, args)

    def reference_paths(self, args: dict) -> list:
        """Where those references are, as paths, so one can be re-pointed in place.

        `references` decides whether a removal is allowed and `reference_paths`
        performs it; both come from the same declaration walk so they agree on
        nested references (checked by tests/test_declaration_walk.py).
        """
        from .schema import reference_paths
        return reference_paths(self.args, args)


_TYPES: dict[str, FeatureType] = {}


def feature(*names: str, category: str = "solid", produces: str = "solid",
            args: dict | None = None, stands_for: str | None = None,
            lengths: Callable | None = None, angles: Callable | None = None):
    """Register a handler with its argument declaration.

    ``args`` is required in practice (checked by tests/test_features.py):
    validation, dependencies, the catalogue and the viewport all read it.
    """
    def register(fn):
        summary = (fn.__doc__ or "").strip().splitlines()
        for name in names:
            if name in _TYPES:
                raise RuntimeError(f"feature type {name!r} is registered twice")
            _TYPES[name] = FeatureType(name, fn, category, produces,
                                       summary[0] if summary else "",
                                       {**COMMON, **(args or {})}, stands_for,
                                       lengths, angles)
        return fn
    return register


def handler(name: str) -> FeatureType:
    """The feature type by name, or a refusal that lists what there is."""
    found = _TYPES.get(name)
    if found is None:
        raise CadError("unknown_feature_type",
                       f"feature type {name!r} is not implemented",
                       {"available": types()})
    return found


def types() -> list:
    return sorted(_TYPES)


def catalogue() -> list:
    """Every feature type with its category and summary, for a UI or an agent."""
    return [_TYPES[name].as_dict() for name in types()]
