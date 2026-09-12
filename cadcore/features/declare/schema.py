"""What a feature's arguments are, declared once and used four ways.

The one declaration is the validator, the dependency walk, the catalogue and
the viewport's fields. It describes shape, not meaning: whether a distance
must be positive is the handler's business.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from ...errors import CadError, short


@dataclass(frozen=True)
class Arg:
    """One argument: what kind of thing it is, and whether it must be there."""

    kind: str
    required: bool = False
    default: object = None
    choices: tuple = ()
    fields: dict = field(default_factory=dict)     # for "spec"
    options: tuple = ()                            # for "one_of"
    measures: str = "length"                       # what a number counts
    note: str = ""
    #: (sibling field, {its value: measure}): what this number counts depends on
    #: another field of the same spec, e.g. a mate's limit is degrees for a hinge
    measures_by: tuple = ()

    def as_dict(self) -> dict:
        out = {"kind": self.kind, "required": self.required}
        if self.default is not None:
            out["default"] = self.default
        if self.choices:
            out["choices"] = list(self.choices)
        if self.note:
            out["note"] = self.note
        if self.kind in ("number", "point") and self.measures != "length":
            out["measures"] = self.measures
        if self.fields:
            out["fields"] = {k: v.as_dict() for k, v in self.fields.items()}
        if self.options:
            out["options"] = [o.as_dict() for o in self.options]
        return out


def Ref(required: bool = True, note: str = "") -> Arg:
    """Names another feature in the same document."""
    return Arg("ref", required, note=note)


def Refs(required: bool = True, note: str = "") -> Arg:
    """Names several features."""
    return Arg("refs", required, note=note)


#: what a number counts. Unit conversion scales only lengths: an angle is
#: degrees whatever the document's unit and a count is a count. Length is the
#: default because nearly every number in a CAD document is one.
LENGTH, ANGLE, COUNT, RATIO = "length", "angle", "count", "ratio"


def Number(required: bool = True, default=None, note: str = "",
           measures: str = LENGTH, measures_by: tuple = ()) -> Arg:
    """A number, or an expression that evaluates to one."""
    return Arg("number", required, default, note=note, measures=measures,
               measures_by=measures_by)


def Angle(required: bool = True, default=None, note: str = "") -> Arg:
    """Degrees, unaffected by the document's length unit."""
    return Number(required, default, note, measures=ANGLE)


def Count(required: bool = True, default=None, note: str = "") -> Arg:
    """A count; never unit-converted."""
    return Number(required, default, note, measures=COUNT)


def Ratio(required: bool = True, default=None, note: str = "") -> Arg:
    """A fraction or a factor: dimensionless, so never converted."""
    return Number(required, default, note, measures=RATIO)


def Point(required: bool = True, default=None, note: str = "",
          measures: str = LENGTH) -> Arg:
    """Two or three numbers or expressions; lengths unless ``measures`` says otherwise."""
    return Arg("point", required, default, note=note, measures=measures)


def Direction(required: bool = True, default=None, note: str = "") -> Arg:
    """A direction; dimensionless, so never unit-converted."""
    return Point(required, default, note, measures=RATIO)


def Flag(default: bool = False, note: str = "") -> Arg:
    return Arg("flag", False, default, note=note)


def Text(choices=(), required: bool = False, default=None, note: str = "") -> Arg:
    return Arg("text", required, default, tuple(choices), note=note)


def Path(required: bool = True, note: str = "") -> Arg:
    """Names a file on disk, relative to the document.

    Its own kind because the cache must stat the file: a result read from a
    file is not determined by the arguments alone, and two documents in
    different folders may both say `in.step`.
    """
    return Arg("path", required, note=note)


def Name(required: bool = True, note: str = "") -> Arg:
    """A face or edge name: a reference into the geometry, not to a feature."""
    return Arg("name", required, note=note)


def Names(required: bool = True, note: str = "") -> Arg:
    return Arg("names", required, note=note)


def Query(required: bool = True, note: str = "") -> Arg:
    """A list of names, or a selector resolved against the body."""
    return Arg("query", required, note=note)


def Spec(fields: dict, required: bool = False, note: str = "") -> Arg:
    """A nested dictionary with fields of its own."""
    return Arg("spec", required, fields=dict(fields), note=note)


def OneOf(*options: Arg, required: bool = False, note: str = "") -> Arg:
    """Any one of several shapes, e.g. ``until`` is a word or a dictionary."""
    return Arg("one_of", required, options=tuple(options), note=note)


def ListOf(item: Arg, required: bool = False, note: str = "") -> Arg:
    """A list whose items all have one declared shape.

    Declared rather than ``Anything`` so the dependency walk can see a
    ``{body, face}`` reference inside the list.
    """
    return Arg("list_of", required, options=(item,), note=note)


def MapOf(item: Arg, required: bool = False, note: str = "") -> Arg:
    """Named entries that all have one declared shape."""
    return Arg("map_of", required, options=(item,), note=note)


def Anything(required: bool = False, note: str = "") -> Arg:
    """Not checked here; the consumer checks it (mostly a sketch's own geometry)."""
    return Arg("any", required, note=note)


# --- shapes several features share ---------------------------------------------

# the end-condition words are deliberately not choices: an unknown one is
# refused by extents.py, which can list the conditions that exist
END_CONDITION = OneOf(
    Text(),
    Spec({"face": Name(), "body": Ref(required=False)}),
    Spec({"plane": Ref()}),
    note="how far the feature goes when that is a relationship, not a number")

AXIS = OneOf(Ref(required=False),
             Spec({"origin": Point(required=False),
                   "direction": Direction(required=False),
                   "axis": Ref(required=False)}),
             note="a datum axis by name, or an origin and a direction")

REPEAT = Spec({"count": Count(), "spacing": Number(required=False),
               "direction": Direction(required=False), "axis": AXIS,
               "angle": Angle(required=False), "path": Ref(required=False),
               "merge": Flag(True)},
              note="repeat the feature: along a direction, about an axis, or "
                   "along a path")

SEAT = Spec({"diameter": Number(), "depth": Number(required=False),
             "angle": Angle(required=False)})


# --- checking ------------------------------------------------------------------

def check(type_name: str, fields: dict, args: dict, where: str = "") -> None:
    """Refuse arguments that do not match the feature's declaration.

    Shape only: an unknown key, a missing required key or a wrong shape is
    refused instead of being ignored and replaced by a default.
    """
    place = f"{where}: " if where else ""
    unknown = sorted(set(args) - set(fields))
    if unknown:
        raise CadError("unknown_argument",
                       f"{place}{type_name} does not take: " + ", ".join(unknown),
                       {"feature_type": type_name, "unknown": unknown,
                        "understood": sorted(fields)})
    missing = sorted(name for name, spec in fields.items()
                     if spec.required and args.get(name) is None)
    if missing:
        raise CadError("missing_argument",
                       f"{place}{type_name} needs: " + ", ".join(missing),
                       {"feature_type": type_name, "missing": missing})
    for name in [n for n, v in args.items() if v is None]:
        del args[name]                   # an explicit null is the argument left out
    for name, value in args.items():
        problem = _wrong(fields[name], value)
        if problem:
            raise CadError("bad_arguments",
                           f"{place}{type_name}'s {name!r} {problem}",
                           {"feature_type": type_name, "argument": name,
                            "given": short(value, 120)})


def _wrong(spec: Arg, value) -> str | None:
    """Why this value is not what the argument declared, or None."""
    kind = spec.kind
    if kind == "any":
        return None
    if kind in ("ref", "name", "text"):
        if not isinstance(value, str):
            return f"should be a name, not {type(value).__name__}"
        if spec.choices and value not in spec.choices:
            return "should be one of: " + ", ".join(spec.choices)
        return None
    if kind in ("refs", "names"):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return "should be a list of names"
        return None
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return "should be a number or an expression"
        return None
    if kind == "point":
        if not isinstance(value, (list, tuple)) or not 2 <= len(value) <= 3:
            return "should be two or three numbers"
        if any(isinstance(v, bool) or not isinstance(v, (int, float, str)) for v in value):
            return "should be numbers or expressions"
        return None
    if kind == "flag":
        return None if isinstance(value, bool) else "should be true or false"
    if kind == "query":
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return None
        if isinstance(value, dict):
            return None
        return "should be a list of names or a selector"
    if kind == "spec":
        if not isinstance(value, dict):
            return "should be a dictionary"
        try:
            check("it", spec.fields, value)
        except CadError as exc:
            # report rather than raise, so a one_of can try its next option
            return exc.message
        return None
    if kind == "one_of":
        misses = [_wrong(option, value) for option in spec.options]
        if any(miss is None for miss in misses):
            return None
        if spec.note:
            return f"is not {spec.note}"
        return "is not any of the shapes it can take: " + "; ".join(
            m for m in misses if m)
    if kind == "list_of":
        if not isinstance(value, list):
            return "should be a list"
        for item in value:
            problem = _wrong(spec.options[0], item)
            if problem:
                return f"has an entry that {problem}"
        return None
    if kind == "map_of":
        if not isinstance(value, dict):
            return "should be a dictionary of named entries"
        for item in value.values():
            problem = _wrong(spec.options[0], item)
            if problem:
                return f"has an entry that {problem}"
        return None
    if kind == "path":
        # refuse `path: 123` here rather than later inside `open()`
        return None if isinstance(value, str) else "should be a file path"
    return None


# --- dependencies --------------------------------------------------------------

@dataclass(frozen=True)
class Found:
    """One leaf of a feature's arguments, and the declaration that describes it.

    ``path`` is what a caller needs to reach or rewrite the value: ``["at"]``,
    ``["edges", "of_face"]``, ``["mates", 2, "faces"]``.
    """

    path: tuple
    spec: Arg
    value: object


def leaves(fields: dict, args: dict):
    """Walk a declaration over some arguments, yielding every leaf it reaches.

    This is the only recursion over a declaration: `files`, `lengths`,
    `geometry_names`, `reference_paths` and the rest all use it, so every
    question about an argument gets the same answer (checked by
    tests/test_declaration_walk.py). What a leaf means stays with the caller.
    """
    def walk(spec: Arg, value, path: tuple):
        if value is None:
            return
        if spec.kind == "spec" and isinstance(value, dict):
            for name, item in value.items():
                if name in spec.fields:
                    field = spec.fields[name]
                    if field.measures_by:
                        by, table = field.measures_by
                        field = replace(field, measures=table.get(value.get(by), field.measures))
                    yield from walk(field, item, (*path, name))
        elif spec.kind == "one_of":
            # only the option that fits: walking every option would describe
            # one value two ways
            for option in spec.options:
                if _wrong(option, value) is None:
                    yield from walk(option, value, path)
                    break
        elif spec.kind == "list_of" and isinstance(value, list):
            for i, item in enumerate(value):
                yield from walk(spec.options[0], item, (*path, i))
        elif spec.kind == "map_of" and isinstance(value, dict):
            for key, item in value.items():
                yield from walk(spec.options[0], item, (*path, key))
        else:
            yield Found(path, spec, value)

    for name, value in args.items():
        if name in fields:
            yield from walk(fields[name], value, (name,))


def files(fields: dict, args: dict) -> list[str]:
    """Every file this feature's arguments name; the cache stats each one."""
    return [f.value for f in leaves(fields, args)
            if f.spec.kind == "path" and isinstance(f.value, str)]


def lengths(fields: dict, args: dict) -> list[list]:
    """Paths to the lengths in the arguments.

    ``["at", 0]`` is the first number of a point, ``["pattern", "spacing"]`` a
    field inside a spec. Angles and counts are excluded by their declaration.
    """
    out: list[list] = []
    for found in leaves(fields, args):
        if found.spec.measures != LENGTH:
            continue
        if found.spec.kind == "number":
            out.append(list(found.path))
        elif found.spec.kind == "point" and isinstance(found.value, list):
            out.extend([*found.path, i] for i in range(len(found.value)))
    return out


def angles(fields: dict, args: dict) -> list[list]:
    """Paths to the angles in the arguments; the mirror of :func:`lengths`."""
    return measured_as(fields, args, (ANGLE,))


def dimensionless(fields: dict, args: dict) -> list[list]:
    """Paths to the counts and ratios in the arguments.

    A parameter feeding a count has no declaration of its own; without this
    list it would be converted as a length.
    """
    return measured_as(fields, args, (COUNT, RATIO))


def measured_as(fields: dict, args: dict, measures: tuple) -> list[list]:
    out: list[list] = []
    for found in leaves(fields, args):
        if found.spec.measures not in measures:
            continue
        if found.spec.kind == "number":
            out.append(list(found.path))
        elif found.spec.kind == "point" and isinstance(found.value, list):
            out.extend([*found.path, i] for i in range(len(found.value)))
    return out


QUERY_NAMES = ("between", "of_face")


def geometry_names(fields: dict, args: dict) -> list[tuple]:
    """Paths to the face and edge names in the arguments, each with its declared kind.

    Used by reference repair; feature references, which look identical, are
    not included. The kind decides what counts as broken: a `Name` is where a
    frame is taken, so a split face fails there (`face_was_split`); a `Query`
    matches by base and finds the pieces of a split face, so it does not.
    """
    out: list[tuple] = []
    for found in leaves(fields, args):
        path, value = list(found.path), found.value
        if found.spec.kind == "name" and isinstance(value, str):
            out.append((path, "name"))
        elif found.spec.kind == "names" and isinstance(value, list):
            out.extend(([*path, i], "names") for i, v in enumerate(value)
                       if isinstance(v, str))
        elif found.spec.kind == "query":
            if isinstance(value, list):
                out.extend(([*path, i], "query") for i, v in enumerate(value)
                           if isinstance(v, str))
            elif isinstance(value, dict):
                for key in QUERY_NAMES:
                    item = value.get(key)
                    if isinstance(item, str):
                        out.append(([*path, key], "query"))
                    elif isinstance(item, list):
                        out.extend(([*path, key, i], "query")
                                   for i, v in enumerate(item)
                                   if isinstance(v, str))
    return out


def reference_paths(fields: dict, args: dict) -> list[list]:
    """Paths to the references to other features.

    :func:`references` says which features are named; this says where, so
    removing or moving a feature can re-point nested references
    (`on: {"body": ...}`, `until`, entries in a list) as well as top level ones.
    """
    out: list[list] = []
    for found in leaves(fields, args):
        if found.spec.kind == "ref" and isinstance(found.value, str):
            out.append(list(found.path))
        elif found.spec.kind == "refs" and isinstance(found.value, list):
            out.extend([*found.path, i] for i, v in enumerate(found.value)
                       if isinstance(v, str))
    return out


def references(fields: dict, args: dict) -> list[str]:
    """Every feature this feature's arguments name, in first-seen order.

    Read from the declaration, not guessed from key names.
    """
    out = [read_at(args, path) for path in reference_paths(fields, args)]
    return list(dict.fromkeys(out))


def read_at(args, path):
    """The value at a path, as :func:`leaves` spells one."""
    value = args
    for step in path:
        value = value[step]
    return value


def write_at(args, path, value) -> None:
    """Put a value at a path, as :func:`leaves` spells one."""
    holder = args
    for step in path[:-1]:
        holder = holder[step]
    holder[path[-1]] = value
