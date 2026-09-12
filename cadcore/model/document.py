"""The design document: parameters and a DAG of features, stored as JSON."""
from __future__ import annotations

import ast
import json
from copy import deepcopy
import math
import operator
import os
from dataclasses import dataclass, field
from pathlib import Path

from .. import names
from ..errors import CadError, short

SAFE = {k: getattr(math, k) for k in ("pi", "sqrt", "sin", "cos", "tan", "asin", "acos",
                                      "atan", "atan2", "hypot", "radians", "degrees")}
SAFE.update({"abs": abs, "min": min, "max": max, "round": round, "e": math.e})

# A document is data and must not run code: `eval` with empty builtins is no
# sandbox. An expression is parsed and walked; anything not listed is refused.
_ALLOWED = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.BinOp, ast.UnaryOp,
            ast.Compare, ast.BoolOp, ast.IfExp, ast.Call, ast.Tuple, ast.List,
            ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
            ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
            ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)

#: how many digits a number in a document may have: `9**9**9` never comes back
_DIGITS = 30

#: how long a list an expression may make: `[1, 2] * 10**7` is a gigabyte
_ITEMS = 1000


def _power(base, exponent):
    """`a ** b`, refused when the answer could not be a size.

    Judged before it is computed, from how many digits it would have, because
    computing it is the thing being avoided.
    """
    try:                # digits of the answer, either side of the point: 0.5**-200 is as big as 2**200
        size = abs(float(exponent) * math.log10(abs(float(base)))) if base else 0.0
    except (TypeError, ValueError, OverflowError):
        size = float("inf")
    if not isinstance(base, (int, float)) or not isinstance(exponent, (int, float)):
        raise CadError("bad_expression",
                       "a power takes two numbers",
                       {"base": _short(base), "exponent": _short(exponent)})
    if size > _DIGITS:
        raise CadError("expression_too_big",
                       f"{base!r} ** {exponent!r} would have about "
                       f"{size:.0f} digits, which is not a size",
                       {"digits": _DIGITS,
                        "hint": "documents are data, and an expression that "
                                "cannot finish is as bad as one that cannot parse"})
    answer = operator.pow(base, exponent)
    if isinstance(answer, complex):
        # `(-2) ** 0.5`: a complex number cannot be a length
        raise CadError("bad_expression",
                       f"{base!r} ** {exponent!r} is not a real number",
                       {"hint": "a root of a negative number is not a size"})
    return answer


def _short(value) -> object:
    """A value small enough to put in a refusal."""
    if isinstance(value, (list, tuple)):
        return "a list of %d" % len(value)
    return value


_OPERATORS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: _power, ast.USub: operator.neg, ast.UAdd: operator.pos,
    ast.Not: operator.not_, ast.Eq: operator.eq, ast.NotEq: operator.ne,
    ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}


def _finite(value):
    """A number this document can hold, or a refusal: past the digit limit or infinite is not a size."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and abs(value) >= 10 ** _DIGITS:
        raise CadError("expression_too_big",
                       "that number would have more than %d digits, which is not a size"
                       % _DIGITS, {"digits": _DIGITS})
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        raise CadError("expression_too_big", "that number is not finite",
                       {"hint": "a length that overflowed a float is not a length"})
    return value


def _evaluate(text: str, parameters: dict, seen: frozenset = frozenset(), memo: dict | None = None):
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise CadError("bad_expression", f"cannot parse {text!r}",
                       {"error": str(exc)}) from exc
    except (RecursionError, MemoryError) as exc:
        raise CadError("expression_too_big", "that expression is nested deeper than anything this reads",
                       {"hint": "documents are data; an expression that cannot be parsed "
                                "in bounded space is not one"}) from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise CadError("unsafe_expression",
                           f"{type(node).__name__} is not allowed in an expression",
                           {"expression": text,
                            "hint": "documents are data: only arithmetic and the "
                                    "listed functions run"})
    try:
        return _value(tree.body, parameters, seen, {} if memo is None else memo)
    except CadError:
        raise
    except Exception as exc:                                        # noqa: BLE001
        raise CadError("bad_expression", f"cannot evaluate {text!r}",
                       {"error": str(exc), "parameters": sorted(parameters)}) from exc


def _no_bigger_than(left, right) -> None:
    """Refuse a repetition that would build a list nobody asked for."""
    for sequence, count in ((left, right), (right, left)):
        if isinstance(sequence, (list, tuple)) and isinstance(count, (int, float)):
            if len(sequence) * abs(count) > _ITEMS:
                raise CadError(
                    "expression_too_big",
                    "that repetition would make a list of about %d items"
                    % (len(sequence) * abs(count)),
                    {"limit": _ITEMS,
                     "hint": "documents are data, and an expression that eats "
                             "the memory is as bad as one that cannot parse"})


def _value(node, parameters, seen: frozenset = frozenset(), memo: dict | None = None):
    # `memo`: each parameter resolved once per evaluation, not once per mention
    if memo is None:
        memo = {}
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            if isinstance(node.value, int) and len(str(abs(node.value))) > _DIGITS:
                raise CadError("expression_too_big",
                               "that number would have more than %d digits, which is not a size"
                               % _DIGITS, {"digits": _DIGITS})
            return _finite(node.value)
        raise CadError("bad_expression", f"{node.value!r} is not a number")
    if isinstance(node, ast.Name):
        if node.id in parameters:
            value = parameters[node.id]
            if not isinstance(value, str):
                return value
            # a parameter written as an expression over the others
            if node.id in seen:
                raise CadError(
                    "bad_expression",
                    "parameter %r is defined in terms of itself" % node.id,
                    {"through": sorted(seen | {node.id})})
            if node.id not in memo:
                memo[node.id] = _evaluate(value, parameters, seen | {node.id}, memo)
            return memo[node.id]
        if node.id in SAFE:
            return SAFE[node.id]
        raise CadError("bad_expression", f"name {node.id!r} is not defined",
                       {"parameters": sorted(parameters), "functions": sorted(SAFE)})
    if isinstance(node, ast.BinOp):
        left = _value(node.left, parameters, seen, memo)
        right = _value(node.right, parameters, seen, memo)
        if isinstance(node.op, ast.Mult):
            _no_bigger_than(left, right)
        if isinstance(node.op, ast.Add) and isinstance(left, (list, tuple)) \
                and isinstance(right, (list, tuple)) and len(left) + len(right) > _ITEMS:
            raise CadError("expression_too_big",
                           "that join would make a list of %d items" % (len(left) + len(right)),
                           {"limit": _ITEMS})
        return _finite(_OPERATORS[type(node.op)](left, right))
    if isinstance(node, ast.UnaryOp):
        return _OPERATORS[type(node.op)](_value(node.operand, parameters, seen, memo))
    if isinstance(node, ast.Compare):
        left = _value(node.left, parameters, seen, memo)
        for op, right_node in zip(node.ops, node.comparators):
            right = _value(right_node, parameters, seen, memo)
            if not _OPERATORS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        values = [_value(v, parameters, seen, memo) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.IfExp):
        return (_value(node.body, parameters, seen, memo) if _value(node.test, parameters, seen, memo)
                else _value(node.orelse, parameters, seen, memo))
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_value(v, parameters, seen, memo) for v in node.elts]
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in SAFE:
            raise CadError("unsafe_expression", "only the listed functions can be called",
                           {"functions": sorted(k for k, v in SAFE.items() if callable(v))})
        if node.keywords:
            raise CadError("bad_expression", "keyword arguments are not supported")
        return _finite(SAFE[node.func.id](*[_value(a, parameters, seen, memo) for a in node.args]))
    raise CadError("unsafe_expression", f"{type(node).__name__} is not allowed")


#: the units a document may be written in, as millimetres each: only exact ones
UNITS = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}


@dataclass
class Feature:
    """One step of a document: what it is, and what it was given.

    What the arguments mean is declared with the handler that builds it
    (:mod:`cadcore.features.declare.schema`), not read off the key names.
    """

    id: str
    type: str
    args: dict = field(default_factory=dict)
    #: switched off without being deleted: keeps its arguments and its place
    #: in the history, and hands its input straight through
    suppressed: bool = False


def names_in(expression: str) -> set:
    """Which parameters an expression mentions.

    Read the way the evaluator reads it, so that what this returns is what an
    evaluation would bind: `max(w, 2*t)` mentions `w` and `t`, and not `max`.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError, MemoryError):
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id not in SAFE}


def _scale_at(args, path: list, factor: float) -> None:
    """Multiply one leaf, leaving an expression alone -- it is already in mm."""
    target = args
    for step in path[:-1]:
        target = target[step]
    last = path[-1]
    value = target[last]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # twelve significant figures: `2 * 25.4 / 25.4` is 1.9999999999999998
        # in binary, and nobody wants that in the diff of a file they wrote 2 in
        target[last] = float("%.12g" % (value * factor))


FORMAT = 1          # what this build writes, and the newest it can read

#: the top-level keys this version of the format is about. Anything else in a
#: document is somebody's, and is kept rather than dropped -- see `Document.extra`.
_KNOWN = frozenset({"format", "meta", "parameters", "parameters_bounds", "bounds",
                    "asserts", "studies", "requirements", "drawing", "unit", "units",
                    "parameter_units", "features", "result"})


#: the requirement quantities that are lengths in the document's unit. The
#: others say their unit in their name (`mass_g`, `volume_mm3`) or have none
LENGTH_QUANTITIES = frozenset({"bbox_x", "bbox_y", "bbox_z", "bbox_max", "bbox_min"})

#: study keys that are lengths in the document's unit
STUDY_LENGTHS = ("mesh_size",)


def _scale_lengths(parameters: dict, bounds: dict, features: list,
                   parameter_units: dict, factor: float,
                   requirements: list | None = None, studies: list | None = None) -> None:
    """Multiply every length here, and no angle, in place.

    Which is which comes from each feature's declaration. Plain-number
    parameters go too; an expression is over parameters already converted.
    One pass for both directions, so they are inverses; twelve significant
    figures, so `2 * 25.4 / 25.4` reads as 2 in the file.
    """
    def tidy(x: float) -> float:
        return float("%.12g" % x)

    from .. import features as registry

    for name, value in list(parameters.items()):
        if parameter_units.get(name, "length") != "length":
            continue                     # an angle is degrees in any document
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            parameters[name] = tidy(value * factor)
    for name, span in list(bounds.items()):
        if parameter_units.get(name, "length") == "length" and isinstance(span, (list, tuple)):
            # an expression bound is over parameters already converted
            bounds[name] = [tidy(v * factor) if isinstance(v, (int, float)) and not isinstance(v, bool)
                            else v for v in span]
    for kind_name, args in features:
        try:
            kind = registry.handler(kind_name)
        except CadError:
            continue                     # refused later, by name, when it builds
        for path in kind.lengths(args):
            _scale_at(args, path, factor)
    # a requirement on a length and a study's mesh size are in the document's unit too
    for req in requirements or []:
        if isinstance(req, dict) and req.get("quantity") in LENGTH_QUANTITIES:
            _scale_at(req, ["value"], factor)
    for study in studies or []:
        if isinstance(study, dict):
            for key in STUDY_LENGTHS:
                if key in study:
                    _scale_at(study, [key], factor)


def check_ids_once(features: list) -> None:
    """Refuse a list of features in which two have the same id."""
    seen: set = set()
    for f in features:
        if f.id in seen:
            raise CadError("duplicate_id", f"there is already a feature {f.id!r}",
                           {"feature": f.id, "hint": "every feature id names one feature"})
        seen.add(f.id)


def autosave_path(path: str | Path) -> str:
    """Where a document's unsaved work waits: ``bracket.autosave.json``, beside it and not hidden."""
    where = Path(path)
    return str(where.with_name(where.stem + ".autosave" + (where.suffix or ".json")))


@dataclass
class Document:
    parameters: dict = field(default_factory=dict)
    features: list[Feature] = field(default_factory=list)
    result: str | None = None
    meta: dict = field(default_factory=dict)
    # The envelope the design is claimed to work in. Fuzzing outside it proves
    # nothing: a fillet bigger than the part *should* fail.
    bounds: dict = field(default_factory=dict)
    asserts: list = field(default_factory=list)
    studies: list = field(default_factory=list)
    #: what the part must be -- mass, size, printability -- as rows the kernel
    #: measures on every build. Status, not a gate: see `cadcore.model.requirements`
    requirements: list = field(default_factory=list)
    drawing: dict = field(default_factory=dict)   # views, dimensions, tolerances
    source: str | None = None            # where it was loaded from, for relative paths
    #: what the file's numbers are written in; the geometry is always mm, since
    #: OCCT's tolerances are absolute
    unit: str = "mm"
    #: what each parameter measures, for the ones that are not lengths
    parameter_units: dict = field(default_factory=dict)
    #: top-level keys this version does not know, kept so a save deletes nothing
    extra: dict = field(default_factory=dict)

    #: every file this document names goes through this first: the session's
    #: fence, or None. A plain function: deepcopy keeps it by reference
    fence: object = None
    #: which state this is: handed out by the session once per edit; not saved
    revision: int = 0

    #: what a copy carries: the content. `source`, `fence` and `revision` are
    #: the session's (a new field goes on one list or the other)
    CONTENT = ("parameters", "features", "result", "meta", "bounds", "asserts",
               "studies", "requirements", "drawing", "unit", "parameter_units", "extra")
    SESSIONS_OWN = ("source", "fence", "revision")

    def take_content_from(self, other: "Document") -> None:
        """Become a copy of `other`'s content, keeping what is the session's."""
        for name in self.CONTENT:
            setattr(self, name, deepcopy(getattr(other, name)))

    @property
    def per_mm(self) -> float:
        """How many millimetres one of this document's units is."""
        return UNITS[self.unit]

    def resolve(self, path: str) -> str:
        """A path in a document is relative to the document, not to the CWD --
        and, in a served session, inside the session's fence or refused."""
        if not (os.path.isabs(path) or self.source is None):
            path = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(self.source)),
                                                 path))
        return self.fence(path) if self.fence is not None else path

    def in_envelope(self) -> tuple[bool, str | None]:
        for name, bound in self.bounds.items():
            if not isinstance(bound, (list, tuple)) or len(bound) != 2:
                raise CadError("bad_parameter", f"the bound on {name!r} is not [low, high]",
                               {"parameter": name, "given": bound})
            lo, hi = (self.evaluate(b) if isinstance(b, str) else b for b in bound)
            if not all(isinstance(b, (int, float)) for b in (lo, hi)):
                raise CadError("bad_parameter", f"the bound on {name!r} is not two numbers",
                               {"parameter": name, "given": bound})
            v = self.parameters.get(name)
            if v is not None:
                v = self.evaluate(v)           # a parameter can be an expression
            if not isinstance(v, (int, float)) or not (lo <= v <= hi):
                return False, f"{name}={v} outside [{lo}, {hi}]"
        for expr in self.asserts:
            if not self.evaluate(expr):
                return False, f"assert failed: {expr}"
        return True, None

    # -- io -----------------------------------------------------------------
    @staticmethod
    def load(path: str | Path) -> "Document":
        """Read a document, refusing by kind what is not there or not JSON."""
        try:
            text = Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise CadError("file_not_found", f"no file at {str(path)!r}",
                           {"path": str(path)}) from None
        except (OSError, UnicodeDecodeError) as exc:
            raise CadError("bad_path", f"cannot read {str(path)!r}: {exc}",
                           {"path": str(path)}) from exc
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise CadError("bad_json", f"{str(path)!r} is not a JSON document: {exc}",
                           {"path": str(path)}) from exc
        if not isinstance(raw, dict):
            raise CadError("bad_json", f"{str(path)!r} holds a {type(raw).__name__}, "
                           "not a document", {"path": str(path)})
        return Document.from_dict(raw, str(path))

    def as_dict(self) -> dict:
        """The document as a file: in its own unit, and whole (`extra` keys included)."""
        out = {
            "format": FORMAT,
            **{k: v for k, v in self.extra.items() if k not in _KNOWN},
            "meta": self.meta,
            "parameters": self.parameters,
            "parameters_bounds": self.bounds,
            "asserts": self.asserts,
            "studies": self.studies,
            "requirements": self.requirements,
            "drawing": self.drawing,
            "unit": self.unit,
            "parameter_units": self.parameter_units,
            "features": [{"id": f.id, "type": f.type, **f.args,
                          **({"suppressed": True} if f.suppressed else {})}
                         for f in self.features],
            "result": self.result,
        }
        if self.unit != "mm":
            out = deepcopy(out)
            _scale_lengths(out["parameters"], out["parameters_bounds"],
                           [(f["type"], f) for f in out["features"]],
                           self.parameter_units, 1.0 / self.per_mm,
                           out["requirements"], out["studies"])
        return out

    @staticmethod
    def from_dict(raw: dict, source: str | None = None) -> "Document":
        written = raw.get("format", FORMAT)
        if not isinstance(written, int) or written > FORMAT:
            raise CadError(
                "unknown_format",
                f"this document says it is format {written!r}, "
                f"and this build understands {FORMAT}",
                {"hint": "a newer version of the tool wrote it"})
        for key, wanted, word in (("parameters", dict, "an object"), ("features", list, "a list"),
                                  ("parameters_bounds", dict, "an object"), ("bounds", dict, "an object"),
                                  ("asserts", list, "a list"), ("requirements", list, "a list"),
                                  ("studies", list, "a list"), ("meta", dict, "an object"),
                                  ("drawing", dict, "an object"), ("parameter_units", dict, "an object")):
            if raw.get(key) is not None and not isinstance(raw[key], wanted):
                raise CadError("bad_arguments", f"a document's {key} is {word}",
                               {"key": key, "got": type(raw[key]).__name__})
        for name, span in (raw.get("parameters_bounds") or raw.get("bounds") or {}).items():
            if not isinstance(span, (list, tuple)) or len(span) != 2:
                raise CadError("bad_arguments", f"the bounds of {name!r} are a [low, high] pair",
                               {"parameter": name, "got": short(span)})
        if raw.get("result") is not None and not isinstance(raw["result"], str):
            raise CadError("bad_arguments", "a document's result is a feature id",
                           {"got": type(raw["result"]).__name__})
        unit = str(raw.get("unit", raw.get("units", "mm"))).lower()
        # the document owns its lists: the caller's dictionary stays as it was
        raw = deepcopy(raw)
        feats = [Feature(names.check_id(f.get("id")), str(f.get("type", "")),
                         {k: v for k, v in f.items() if k not in ("id", "type", "suppressed")},
                         bool(f.get("suppressed", False)))
                 for f in raw.get("features", []) if isinstance(f, dict)]
        if len(feats) != len(raw.get("features", [])):
            raise CadError("bad_arguments", "every feature is an object with an id and a type",
                           {"given": [type(f).__name__ for f in raw.get("features", [])]})
        check_ids_once(feats)
        if unit not in UNITS:
            raise CadError("unknown_unit", f"this document is written in {unit!r}",
                           {"known": sorted(UNITS)})
        doc = Document(dict(raw.get("parameters") or {}), feats, raw.get("result"),
                       raw.get("meta") or {},
                       raw.get("parameters_bounds") or raw.get("bounds") or {},
                       list(raw.get("asserts") or []), list(raw.get("studies") or []),
                       list(raw.get("requirements") or []),
                       dict(raw.get("drawing") or {}), source, unit,
                       dict(raw.get("parameter_units") or {}),
                       {k: v for k, v in raw.items() if k not in _KNOWN})
        if unit != "mm":
            doc.convert_to_mm()
        return doc

    def convert_to_mm(self) -> None:
        """Turn every length into millimetres, once, on load; angles and counts stay."""
        self._check_parameter_units()
        _scale_lengths(self.parameters, self.bounds,
                       [(f.type, f.args) for f in self.features],
                       self.parameter_units, UNITS[self.unit],
                       self.requirements, self.studies)

    def _check_parameter_units(self) -> None:
        """Refuse a parameter that feeds an angle or a count while being called a length."""
        from .. import features

        # the argument's own declaration is read by the converter; the
        # parameter behind it has none, so it is the parameter that is checked
        unitless: dict[str, tuple] = {}
        for feature in self.features:
            try:
                kind = features.handler(feature.type)
            except CadError:
                continue
            for what, paths in (("an angle", kind.angles(feature.args)),
                                ("a count", kind.dimensionless(feature.args))):
                for path in paths:
                    value = feature.args
                    for step in path:
                        value = value[step]
                    if isinstance(value, str):
                        for name in names_in(value):
                            if name in self.parameters:
                                unitless.setdefault(name, (feature.id, what))
        # which of a part's parameters is a length is the sub-document's to
        # say: a plain number is refused rather than converted on a guess
        plain = []
        for feature in self.features:
            if feature.type != "part":
                continue
            for name, value in (feature.args.get("parameters") or {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    plain.append("%s.%s" % (feature.id, name))
        if plain:
            raise CadError(
                "parameter_unit_unknown",
                f"this document is in {self.unit!r}, and "
                + ", ".join(sorted(plain))
                + " is a plain number handed to another document, which says "
                  "for itself whether that is a length",
                {"parameters": sorted(plain),
                 "hint": "write it as an expression -- \"25.4\" or \"w/2\" -- "
                         "which is already in millimetres and is passed through"})

        wrong = {n: v for n, v in unitless.items()
                 if self.parameter_units.get(n, "length") == "length"}
        if wrong:
            first = sorted(wrong)[0]
            says = "angle" if wrong[first][1] == "an angle" else "count"
            raise CadError(
                "parameter_unit_unknown",
                f"this document is in {self.unit!r}, and "
                + ", ".join("%s is used as %s" % (n, wrong[n][1])
                            for n in sorted(wrong))
                + " while counting as a length",
                {"parameters": {n: "used by %s as %s" % (f, what)
                                for n, (f, what) in sorted(wrong.items())},
                 "hint": 'say what it is: "parameter_units": {"%s": "%s"}'
                         % (first, says)})

    def snapshot(self) -> "Document":
        """A copy to put back if an edit is refused: a plain copy, so putting it back cannot itself be refused."""
        return deepcopy(self)

    def check_units(self) -> None:
        """Refuse a length parameter wired into an angle; asked after every edit, not only on load.

        Only in a document that is converted: in millimetres nothing is multiplied.
        """
        if self.unit != "mm":
            self._check_parameter_units()

    def save(self, path: str | Path) -> None:
        """Write the document, whole or not at all: beside the target, then renamed over it."""
        import os

        target = Path(path)
        tmp = target.with_name(target.name + ".saving")
        tmp.write_text(json.dumps(self.as_dict(), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)

    # -- parameters ---------------------------------------------------------
    def evaluate(self, value):
        """Numbers pass through; strings are expressions over the parameters."""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        if isinstance(value, list):
            return [self.evaluate(v) for v in value]
        if isinstance(value, str):
            return _evaluate(value, self.parameters)
        return value

    def feature(self, fid: str) -> Feature:
        for f in self.features:
            if f.id == fid:
                return f
        raise CadError("unknown_feature", f"no feature with id {fid!r}",
                       {"known": [f.id for f in self.features]})
