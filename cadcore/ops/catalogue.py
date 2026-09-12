"""What the session can do, as tools a program can read: names, argument
schemas, and whether each one changes the document. Read off the session
class, so the list cannot drift from the code."""
from __future__ import annotations

import inspect

#: Handed to the client at `initialize`: what a caller needs before its first call.
INSTRUCTIONS = """\
This is a parametric CAD kernel. A document is a graph of features; geometry is
referred to by *name*, and a name keeps meaning the same thing when a parameter
moves.

Millimetres and degrees, always. A document may be written in another unit and
is converted on the way in; every number you send or receive here is mm.

Names
  plate/+z        a face: the feature that made it, then what it is
  plate/+z@1      one piece of a face a later boolean split in two
  bolt/side~3     the fourth instance of a pattern
  plate/+z|plate/-x   an edge: the two faces that meet along it
  A face is named for its shape -- a plane by the axis it faces (+x .. -z), a
  cylinder's wall `side`, a cone `taper`, a sphere `ball`, a torus `ring`.
  Anything else gets `face0`, `face1` .. numbered by where it is.

Getting started -- in as few calls as it takes
  A new part is ONE call: load_json with the whole document -- parameters,
  features, result -- written against cad://schema/document (cad://example/*
  are working ones to copy). The reply is the build: volume, face and edge
  names, requirements. Do not add features one call at a time when you know
  what the part is; a document is the thing this kernel is for.
  Several changes to an existing part are ONE call too: apply([...]) runs a
  list of operations as one edit, all of them or none, one undo step.
  One-at-a-time is for exploring: new_document, then add_feature for anything
  in the catalogue (feature_types lists them with their arguments), or the
  `add_*` shorthands; add_profile draws a rect, circle, slot or polygon fully
  constrained. open reads a .json document or a .step file.
  Every edit's reply already carries the build -- do not call build after it.
  To check the result, describe_faces once answers for every face; do not
  call face_frame or measure per face unless a number is needed.

  `at` on a face is measured from that face's own frame, whose origin is the
  centre of the face -- so it follows the face when a parameter moves it, and
  it moves when the face itself changes. Cutting a pocket off-centre moves the
  origin for whatever is placed next; measure with face_frame if it matters.

Reading a part
  describe_faces answers for every face at once -- what it is, where it points,
  how big it is -- and find_faces takes a selector (shape, parallel,
  larger_than). Ask one of those rather than face_frame per face.
  build returns the volume, the area, and every face and edge name;
  reply_style(names="changed") makes that the ones that appeared and went.
  measure, face_frame and describe_document answer about one thing at a time.
  render writes a PNG and hands it back as an image: look at the part.

What this kernel can answer that a modeller cannot
  simulate (static, modal, thermal, buckling, with requirements that pass or
  fail), drawing (a dimensioned sheet), flat_pattern (the blank a sheet metal
  part is cut from), printability (overhangs, thin walls), interference and
  bill_of_materials on an assembly, and optimize (search the parameters for a
  lighter part that still passes its studies).
  Say what simulate is when you report it: linear elastic, a fixed face
  clamped whole, a load spread over its face, parts solved alone. A first-pass
  strength check within a few percent of the textbook -- not plasticity,
  fatigue, bolted joints or certification. `safety_factor` is from the peak
  stress, which a clamp or a sharp corner inflates; `safety_factor_p95` is from
  the stress 95% of the material is below, by volume. Report both.

Refusals
  Every refusal has a `kind` -- `unresolved_reference`, `bad_parameter`,
  `no_intersection` and so on -- and usually a `hint`. Read the kind: it says
  whether to fix the reference, the number, or the intent. Nothing here fails
  silently, and a refusal leaves the document exactly as it was.

Trying something out
  checkpoint("before I thinned it") and restore(...) by name, rather than
  counting undo steps back to a fork.

When attached to Blender
  A person is looking at the same document. selection() tells you what they
  have clicked, by face and edge name -- "this face" means that. select(faces)
  makes your meaning visible on their screen before you change anything, and
  after. Their edits and yours share one undo stack.

What the part must be
  add_requirement(quantity, compare, value): mass_g <= 150, bbox_max <= 120,
  printable == 1, solid == 1 -- requirement_kinds lists the quantities. Every
  build reply then carries `requirements` with got/ok per row, so you are told
  the moment an edit breaks one. Status, not a gate: a part half-made may
  fail them. Strength goes in a study's `require` and is checked by simulate;
  requirements() shows both, and marks study rows stale once the document
  has changed since they were computed.

Reading before asking
  This server also serves resources. cad://schema/document is the document
  format, generated from the same declarations that refuse a bad argument, so
  a document written against it is a document this kernel accepts.
  cad://doc/features is the manual; cad://example/bracket and its neighbours
  are working documents to copy from. Read one before writing a document by
  hand.

Suggested loop
  build -> check -> render, and read the picture. `check` reports what is wrong
  with the part rather than with the request.
"""

#: Operations not offered as tools: `tessellate` answers with every triangle
#: (megabytes, meant for a renderer) and `drag_point` is one step of a mouse drag.
NOT_TOOLS = {"tessellate", "drag_point"}

#: signature annotation -> JSON Schema type
TYPES = {"str": "string", "float": "number", "int": "integer",
         "bool": "boolean", "list": "array", "dict": "object"}


#: Session attributes that are recomputed rather than remembered; assigning
#: one is not a change a caller can see.
DERIVED = frozenset({"body", "evaluator", "meshes", "cache", "_last_names",
                     "_studies_seen",
                     "_editing", "_undid"})


def _writers() -> set:
    """The operations that change the session, found by parsing source.

    An operation writes if it calls `_edit()` (or `_apply`, `_restore`,
    `_forget_last_edit`), assigns a non-derived `self` attribute, or calls an
    operation that does. MCP's `readOnlyHint` is the negation; a client uses
    it to decide what to run without asking.
    """
    import ast
    import collections
    import pathlib

    here = pathlib.Path(__file__).resolve().parents[1]        # cadcore/, every layer
    writes, calls = set(), collections.defaultdict(set)
    for path in sorted(here.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):                           # noqa: PERF203
            continue
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name.startswith("op_")]:
            op = fn.name[3:]
            for node in ast.walk(fn):
                if isinstance(node, ast.Attribute):
                    if node.attr in ("_edit", "_apply", "_restore",
                                     "_forget_last_edit"):
                        writes.add(op)
                    elif node.attr.startswith("op_"):
                        calls[op].add(node.attr[3:])
                if isinstance(node, (ast.Assign, ast.AugAssign)):
                    targets = (node.targets if isinstance(node, ast.Assign)
                               else [node.target])
                    for target in targets:
                        # any remembered session state counts, not just the
                        # document: `rollback` changes what a later build
                        # answers and `reply_style` how much of it comes back
                        if (isinstance(target, ast.Attribute)
                                and isinstance(target.value, ast.Name)
                                and target.value.id == "self"
                                and target.attr not in DERIVED):
                            writes.add(op)
    for _ in range(4):                       # one op calling another, a few deep
        for op, called in calls.items():
            if called & writes:
                writes.add(op)
    return writes


def _choices() -> dict:
    """Each closed set of argument values, read from the thing that refuses a
    value outside it, so the schema and the refusal cannot disagree."""
    from ..model import fasteners
    from .. import features
    from ..model.document import UNITS
    from ..analysis.preview import VIEWS

    turning = ("fillet", "chamfer")
    return {
        ("render", "view"): sorted(VIEWS),
        ("new_document", "unit"): sorted(UNITS),
        ("add_feature", "type"): features.types(),
        ("add_fillet", "kind"): list(turning),
        ("add_chamfer", "kind"): list(turning),
        ("optimize", "objective"): ["mass", "volume"],
        ("add_hole", "standard"): sorted(fasteners.METRIC),
        ("add_hole", "fit"): sorted(fasteners.FITS),
        ("add_hole", "seat"): sorted(fasteners.SEATS),
    }


def _json_type(annotation: str):
    """A parameter's JSON Schema type from its annotation. A union such as
    `str | dict | None` becomes a list of types, not its first member."""
    parts = [p.strip() for p in annotation.split("|")]
    kinds = [TYPES[key] for part in parts for key in TYPES
             if part.startswith(key)]
    kinds = list(dict.fromkeys(kinds))
    if not kinds:
        return None
    return kinds[0] if len(kinds) == 1 else kinds


def _described(fn) -> dict:
    """Per-argument descriptions from the operation's docstring: lines of the
    form ``argname`` -- text (or ``argname``: text)."""
    out = {}
    current = None
    for line in (inspect.getdoc(fn) or "").splitlines():
        line = line.strip()
        if line.startswith("``") and "``" in line[2:]:
            name, _, rest = line[2:].partition("``")
            rest = rest.strip(" -:")
            current = name if name.isidentifier() and rest else None
            if current:
                out[current] = rest
        elif line and current is not None:
            out[current] += " " + line          # a description that ran on to the next line
        else:
            current = None
    return out


def _schema(fn, op: str = "") -> dict:
    """One operation's arguments, read off the operation and its vocabulary."""
    choices, told = _choices(), _described(fn)
    properties, required = {}, []
    for name, parameter in inspect.signature(fn).parameters.items():
        if name == "self":
            continue
        kind = _json_type(str(parameter.annotation).strip("'"))
        field = {"type": kind} if kind else {}
        allowed = choices.get((op, name))
        if allowed:
            field["enum"] = allowed
        if name in told:
            field["description"] = told[name]
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
        elif parameter.default is not None:
            field["default"] = parameter.default
        properties[name] = field
    return {"type": "object", "properties": properties, "required": required}



def tools(session_class) -> list:
    """Every operation the session has, as a tool.

    Read off the class, not an object: an attached session has no local
    session and the operations are the same.
    """
    from . import workspace

    out = []
    writers = _writers()
    for name in sorted(dir(session_class)):
        if not name.startswith("op_") or name[3:] in NOT_TOOLS:
            continue
        fn = getattr(session_class, name)
        doc = inspect.getdoc(fn) or ""
        summary = doc.split("\n\n")[0].replace("\n", " ").strip()
        # whether the operation reads or writes a file is said in the description
        does = workspace.touches(name[3:])
        note = {"read": " Reads a file, under the session's workspace only.",
                "write": " Writes a file, under the session's workspace only."}
        writes_here = name[3:] in writers
        out.append({"name": name[3:],
                    "description": (summary or "the kernel's %s operation"
                                    % name[3:]) + note.get(does, ""),
                    "touches": does,
                    "annotations": {
                        "readOnlyHint": not writes_here and does != "write",
                        # every edit is one undoable step and a refusal
                        # leaves the document untouched
                        "destructiveHint": False,
                        "idempotentHint": not writes_here,
                        "openWorldHint": False},
                    "inputSchema": _schema(fn, name[3:])})
    return out
