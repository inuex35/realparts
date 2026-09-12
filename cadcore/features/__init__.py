"""The feature registry: what a document may say, and what builds it.

A feature type is a function ``handler(graph, f, a, ev)`` decorated with
``@feature("extrude", category=..., args={...})``; it returns a :class:`Body`,
or ``None`` for one that builds no geometry. Every menu reads this catalogue.
"""
from __future__ import annotations

from .declare.registry import FeatureType, catalogue, feature, handler, types  # noqa: F401

__all__ = ["feature", "handler", "types", "catalogue", "FeatureType", "load"]


def load() -> None:
    """Import the modules that register the handlers.

    Explicit rather than a directory scan, so a feature that fails to import
    raises a traceback naming it instead of silently vanishing.
    """
    from .families import assembly, booleans, datums, exchange, modify, patterns, solids, surfaces
