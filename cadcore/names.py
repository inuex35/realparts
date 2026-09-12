"""How a face or edge name is spelled and read, in one place."""
from __future__ import annotations

from .errors import CadError

import re
from dataclasses import dataclass, field

SCOPE = ":"          # a part inside an assembly
ROLE = "/"           # feature from role
DUPLICATE = "#"      # two faces in the same role, or two edges between the same faces
PIECE = "@"          # a face split into pieces by a boolean
INSTANCE = "~"       # one copy of a pattern
PAIR = "|"           # the two faces an edge lies between
OPEN = "open"        # ... or the one face, when the shell is open

_SUFFIX = re.compile(r"([%s%s%s])(\d+|m)$" % (DUPLICATE, PIECE, INSTANCE))

AXES = {"+x": (1.0, 0.0, 0.0), "-x": (-1.0, 0.0, 0.0),
        "+y": (0.0, 1.0, 0.0), "-y": (0.0, -1.0, 0.0),
        "+z": (0.0, 0.0, 1.0), "-z": (0.0, 0.0, -1.0)}


# --- spelling ------------------------------------------------------------------

def face(feature: str, role: str, duplicate: int = 1) -> str:
    """``plate/+z``, or ``fillet1/side#2`` for the second of the same role."""
    suffix = "" if duplicate <= 1 else f"{DUPLICATE}{duplicate}"
    return f"{feature}{ROLE}{role}{suffix}"


def unused(candidate: str, taken) -> str:
    """``candidate``, or the same name with the next free ``#k``.

    Two faces cannot answer to one name. A sketch segment called ``top`` and
    the cap of the prism swept from it both ask for ``plate/top``, and so do a
    body's own ``blk/+y~m`` and the ``blk/+y`` of a second mirror.
    """
    if candidate not in taken:
        return candidate
    parts = parse(candidate)
    rest = tuple(s for s in parts.suffixes if s[0] != DUPLICATE)
    k = 2
    while True:
        spelled = spell(Parts(parts.feature, parts.role, parts.scope,
                              rest + ((DUPLICATE, k),)))
        if spelled not in taken:
            return spelled
        k += 1


def piece(name: str, index: int) -> str:
    """One piece of a face a boolean split, numbered by where it is."""
    return f"{name}{PIECE}{index}"


def instance(name: str, index) -> str:
    """One copy of a pattern; the original keeps its own name.

    ``index`` is the copy's number, or ``"m"`` for the single mirror image.
    """
    return f"{name}{INSTANCE}{index}"


#: what a feature id may not contain: the separators every name is read by,
#: and whitespace. `a/b` as an id made a face `a/b/+z` that `feature_of` read
#: as belonging to `a`, and every reference to it then meant something else
FORBIDDEN_IN_ID = SCOPE + ROLE + DUPLICATE + PIECE + INSTANCE + PAIR


def check_id(feature_id, what: str = "feature id") -> str:
    """The id, or a `bad_arguments` refusal naming what is wrong with it.

    ``what`` is the thing being named: a sketch segment's name becomes part
    of a face name and is read by the same separators.
    """
    if not isinstance(feature_id, str) or not feature_id:
        raise CadError("bad_arguments", f"a {what} is a non-empty string",
                       {"given": feature_id})
    bad = sorted({c for c in feature_id if c in FORBIDDEN_IN_ID or c.isspace()})
    if bad:
        raise CadError("bad_arguments",
                       f"a {what} may not contain {' '.join(repr(c) for c in bad)}: "
                       f"{feature_id!r}",
                       {"feature": feature_id, "forbidden": list(FORBIDDEN_IN_ID),
                        "hint": "those are how a face name is read; use letters, "
                                "digits and underscores"})
    return feature_id


def scoped(scope: str, name: str) -> str:
    """A part's name inside an assembly."""
    return f"{scope}{SCOPE}{name}"


def edge(first: str, second: str, duplicate: int = 1) -> str:
    """The edge between two faces, in a fixed order so it is the same either way."""
    a, b = sorted((first, second))
    suffix = "" if duplicate <= 1 else f"{DUPLICATE}{duplicate}"
    return f"{a}{PAIR}{b}{suffix}"


def boundary(owner: str, duplicate: int = 1) -> str:
    """An edge with one face rather than two: the border of an open shell."""
    suffix = "" if duplicate <= 1 else f"{DUPLICATE}{duplicate}"
    return f"{owner}{PAIR}{OPEN}{suffix}"


# --- reading -------------------------------------------------------------------

@dataclass(frozen=True)
class Parts:
    """A face name taken apart. ``spell`` puts it back exactly as it was."""

    feature: str
    role: str
    scope: str = ""
    suffixes: tuple = field(default_factory=tuple)   # ("@", 0), ("~", 2) ...

    @property
    def piece(self) -> int | None:
        return next((n for kind, n in self.suffixes if kind == PIECE), None)

    @property
    def instance(self) -> int | None:
        return next((n for kind, n in self.suffixes if kind == INSTANCE), None)

    @property
    def duplicate(self) -> int | None:
        return next((n for kind, n in self.suffixes if kind == DUPLICATE), None)


def parse(name: str) -> Parts:
    """Take a face name apart.

    Suffixes are stripped from the right in the order they were applied, so a
    ``#`` that belongs to the role stays with the role.
    """
    scope, _, rest = name.rpartition(SCOPE)
    suffixes: list = []
    while True:
        found = _SUFFIX.search(rest)
        if not found:
            break
        mark = found.group(2)
        suffixes.insert(0, (found.group(1), int(mark) if mark.isdigit() else mark))
        rest = rest[:found.start()]
    feature, _, role = rest.partition(ROLE)
    return Parts(feature, role, scope, tuple(suffixes))


def spell(parts: Parts) -> str:
    """The inverse of :func:`parse`."""
    name = f"{parts.feature}{ROLE}{parts.role}" if parts.role else parts.feature
    for kind, index in parts.suffixes:
        name += f"{kind}{index}"
    return scoped(parts.scope, name) if parts.scope else name


def base(name: str) -> str:
    """The name before a boolean split it: the ``@k`` suffix removed, the
    rest kept, so a selector written before the split still finds the pieces."""
    parts = parse(name)
    kept = tuple((kind, n) for kind, n in parts.suffixes if kind != PIECE)
    return spell(Parts(parts.feature, parts.role, parts.scope, kept))


def is_role(name: str, feature: str, role: str) -> bool:
    """Whether ``name`` is that role of that feature, any copy or piece included.

    Unlike ``base`` this also ignores the ``~k`` of a pattern: when a feature
    asks which faces it made for a role, every copy counts.
    """
    parts = parse(name)
    return parts.feature == feature and parts.role == role


def feature_of(name: str) -> str:
    """Which feature made this face, ignoring the assembly scope."""
    return parse(name).feature


def is_boundary(edge_name: str) -> bool:
    return edge_name.endswith(PAIR + OPEN) or f"{PAIR}{OPEN}{DUPLICATE}" in edge_name


def turned_round(name: str) -> str:
    """The same name with its axis role reversed: ``+y`` becomes ``-y``.

    A cut tool's face bounds the cavity facing the other way, and anything that
    decides "into this face" from the name trusts the role.
    """
    parts = parse(name)
    if parts.role[:1] not in "+-" or parts.role[:2] not in AXES:
        return name
    flipped = ("-" if parts.role[0] == "+" else "+") + parts.role[1:]
    return spell(Parts(parts.feature, flipped, parts.scope, parts.suffixes))


def mirrored(name: str, normal) -> str:
    """The same name after a mirror about a plane with this normal.

    A role along the normal is flipped so it still says which way the face
    points; a role across the mirror (``+y`` mirrored in x) is kept.
    """
    parts = parse(name)
    axis = AXES.get(parts.role.split(DUPLICATE)[0])
    if axis is None:
        return name
    along = abs(sum(axis[i] * normal[i] for i in range(3)))
    if along < 0.5:
        return name
    flipped = ("-" if parts.role[0] == "+" else "+") + parts.role[1:]
    return spell(Parts(parts.feature, flipped, parts.scope, parts.suffixes))


def axis_of(role: str) -> tuple | None:
    """The direction a role claims, for the roles that claim one. The claim
    is checked by :func:`cadcore.service.soak.run`."""
    return AXES.get(role.split(DUPLICATE)[0])
