"""MCP resources: the manual, the worked examples, and the document schema.

Enumerated from `docs/` and `examples/` on disk; the schema is generated from
the same `Arg` declarations that refuse arguments. The URI-to-path mapping is
a closed dictionary, never a join, so `cad://doc/../x` is a missing key.
"""
from __future__ import annotations

import json
from pathlib import Path

#: A checkout has `docs/` and `examples/` beside `cadcore/`; a wheel has them
#: inside it. Searched in that order so a checkout serves its working copy.
HOMES = (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1])

#: The two directories worth reading, and what a reader gets from each.
SHELVES = (
    ("doc", "docs", "*.md", "text/markdown",
     "the manual: %s"),
    ("example", "examples", "*.json", "application/json",
     "a worked document: %s"),
)


def _shelf() -> dict:
    """uri -> (path, mimeType, title, description), from the directories on disk."""
    out: dict[str, tuple] = {}
    for prefix, folder, pattern, mime, about in SHELVES:
        for home in HOMES:
            found = sorted((home / folder).glob(pattern))
            if not found:
                continue
            for path in found:
                uri = "cad://%s/%s" % (prefix, path.stem)
                out[uri] = (path, mime, "/".join((folder, path.name)),
                            about % path.stem.replace("_", " "))
            break
    return out


def catalogue() -> list:
    """Every resource this server offers, for `resources/list`."""
    out = [{"uri": uri, "name": name, "title": name, "mimeType": mime,
            "description": about}
           for uri, (_, mime, name, about) in sorted(_shelf().items())]
    out.append({"uri": "cad://schema/document", "name": "document.schema.json",
                "title": "document.schema.json",
                "mimeType": "application/schema+json",
                "description": "the document format, generated from the same "
                               "declarations the kernel refuses arguments with"})
    return out


def read(uri: str) -> dict:
    """One resource's contents; a URI not in the catalogue raises `KeyError`."""
    if uri == "cad://schema/document":
        return {"uri": uri, "mimeType": "application/schema+json",
                "text": json.dumps(document_schema(), indent=1)}
    shelf = _shelf()
    if uri not in shelf:
        raise KeyError(uri)
    path, mime, _, _ = shelf[uri]
    return {"uri": uri, "mimeType": mime, "text": path.read_text(encoding="utf-8")}


# -- the document format, read off the declarations ---------------------------

#: A number in a document may be an expression such as `"wall * 2"`, so every
#: place a number is accepted, a string is too.
NUMBER = {"type": ["number", "string"]}

_SIMPLE = {
    "ref": {"type": "string", "description": "the id of another feature"},
    "refs": {"type": "array", "items": {"type": "string"}},
    "flag": {"type": "boolean"},
    "text": {"type": "string"},
    "path": {"type": "string", "description": "a file, relative to the document"},
    "name": {"type": "string",
             "description": "a face or edge name, such as plate/+z or a|b"},
    "names": {"type": "array", "items": {"type": "string"}},
    # a `Query` is a list of names or a selector object; the shipped examples
    # use both forms
    "query": {"anyOf": [{"type": "array", "items": {"type": "string"}},
                        {"type": "object"}],
              "description": "edge or face names, or a selector such as "
                             "{\"of_face\": \"plate/+z\"}"},
    "number": NUMBER,
    "point": {"type": "array", "items": NUMBER, "minItems": 2, "maxItems": 3},
    "any": {},
}


def _field(arg) -> dict:
    """One declared argument as JSON Schema."""
    out = dict(_SIMPLE.get(arg.kind, {}))
    if arg.kind == "spec":
        out = {"type": "object",
               "properties": {k: _field(v) for k, v in arg.fields.items()},
               "required": [k for k, v in arg.fields.items() if v.required]}
    elif arg.kind == "one_of":
        out = {"anyOf": [_field(o) for o in arg.options]}
    elif arg.kind == "list_of":
        out = {"type": "array", "items": _field(arg.options[0])}
    elif arg.kind == "map_of":
        out = {"type": "object", "additionalProperties": _field(arg.options[0])}
    if arg.choices:
        out["enum"] = list(arg.choices)
    if arg.default is not None:
        out["default"] = arg.default
    if arg.note:
        out["description"] = arg.note
    if arg.kind in ("number", "point") and arg.measures != "length":
        out["description"] = "%s (%s)" % (out.get("description", ""),
                                          arg.measures)
    return out


def _feature_schema(kind) -> dict:
    """One feature type: its id, its type, and exactly its own arguments."""
    return {
        "title": kind.name,
        "description": kind.summary,
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "type": {"const": kind.name},
            "suppressed": {"type": "boolean",
                           "description": "kept in the document, built as a "
                                          "pass-through"},
            **{name: _field(arg) for name, arg in kind.args.items()}},
        # the kernel refuses undeclared arguments (`unknown_argument`)
        "additionalProperties": False,
        "required": ["id", "type",
                     *sorted(n for n, a in kind.args.items() if a.required)]}


def document_schema() -> dict:
    """The whole format, generated from the feature registry."""
    from .. import features
    from ..model.document import FORMAT, UNITS
    from ..features.declare.registry import _TYPES
    from ..model.requirements import OPS, QUANTITIES

    # handlers register the types and are imported lazily; without this the
    # registry may be empty and `features` would accept nothing
    features.load()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "cadcore document",
        "description": "A parametric CAD document: named parameters, an "
                       "envelope they are claimed to work in, and a graph of "
                       "features referring to each other by id.",
        "type": "object",
        "properties": {
            "format": {"const": FORMAT},
            "unit": {"enum": sorted(UNITS), "default": "mm",
                     "description": "what the numbers here are written in; the "
                                    "kernel works in millimetres and converts"},
            "meta": {"type": "object"},
            "parameters": {"type": "object", "additionalProperties": NUMBER,
                           "description": "named numbers and expressions; "
                                          "`**` is a power"},
            "parameter_units": {
                "type": "object",
                "additionalProperties": {"enum": ["length", "angle", "count",
                                                  "ratio"]},
                "description": "declared for any parameter that is not a length"},
            "parameters_bounds": {
                "type": "object",
                "additionalProperties": {"type": "array", "items": NUMBER,
                                         "minItems": 2, "maxItems": 2},
                "description": "the envelope the design is claimed to hold in; "
                               "fuzzing outside it proves nothing"},
            "asserts": {"type": "array", "items": {"type": "string"},
                        "description": "relations that must hold, as "
                                       "expressions over the parameters"},
            "studies": {"type": "array", "items": {"type": "object"}},
            "requirements": {
                "type": "array",
                "description": "what the part must be, measured on every build; "
                               "status, not a gate",
                "items": {"type": "object",
                          "properties": {
                              "id": {"type": "string"},
                              "quantity": {"enum": sorted(QUANTITIES)},
                              "compare": {"enum": sorted(OPS)},
                              "value": NUMBER,
                              "material": {"type": "string"},
                              "min_wall": NUMBER, "overhang": NUMBER,
                              "note": {"type": "string"}},
                          "required": ["quantity", "compare", "value"],
                          "additionalProperties": False}},
            "drawing": {"type": "object"},
            "features": {"type": "array",
                         "items": {"oneOf": [_feature_schema(_TYPES[name])
                                             for name in features.types()]}},
            "result": {"type": ["string", "null"],
                       "description": "the feature that is the part; the last "
                                      "one by default"},
        },
        "required": ["features"]}
