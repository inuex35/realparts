"""How a face or edge name is read, spelled once for the add-on.

The kernel spells them in ``cadcore/names.py``; Blender never imports
cadcore, so the separators are repeated here and nowhere else in the add-on.
"""
from __future__ import annotations

SCOPE = ":"          # a part inside an assembly
ROLE = "/"           # feature from role
DUPLICATE = "#"      # two faces in the same role
PIECE = "@"          # a face split into pieces by a boolean
INSTANCE = "~"       # one copy of a pattern
PAIR = "|"           # the two faces an edge lies between


def scoped(scope: str | None, name: str) -> str:
    """A name inside a part: ``base:plate/+z``."""
    return f"{scope}{SCOPE}{name}" if scope else name


def first_face(edge: str) -> str:
    """The first of the two faces an edge lies between."""
    return edge.split(PAIR, 1)[0]


def edge(first: str, second: str) -> str:
    """The edge between two faces, as the two of them."""
    return f"{first}{PAIR}{second}"


def part_of(name: str) -> str | None:
    """The part a face or edge name belongs to: everything before the last colon, or None."""
    first = first_face(name)
    return first.rsplit(SCOPE, 1)[0] if SCOPE in first else None


def unscoped(name: str) -> str:
    """The name without its part: ``plate/+z`` from ``base:plate/+z``."""
    return name.rsplit(SCOPE, 1)[-1]


def feature_of(name: str) -> str:
    """The feature that made a face: the name without scope, role or suffixes."""
    return unscoped(first_face(name)).split(ROLE)[0].split(INSTANCE)[0].split(PIECE)[0].split(DUPLICATE)[0]
