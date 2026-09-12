# How it is put together

[← docs](README.md)

The layout, what happens to one edit, and how to extend it. Why it is
built this way -- the reasons and the bugs that shaped them -- is
[decisions.md](decisions.md). The layer picture alone is in
[cadcore/README.md](../cadcore/README.md).

## Layout

The layers go one way, and `cadcore/tests/test_architecture.py` checks it
from the import graph: a document is data and never reaches into geometry,
the kernel never reaches up into the evaluator, the add-on never imports
the kernel, and there are no import cycles. Lowest first:

    cadcore/errors.py       the one error type: every refusal carries a kind
    cadcore/names.py        how a name is spelled, and read back
    cadcore/progress.py     saying where a long operation has got to

    cadcore/model/          the document as data -- reads no geometry
      document.py           parameters, features, the envelope, JSON in and out
      requirements.py       what the part must be; authoring.py, fasteners.py
    cadcore/sketching/      PlaneGCS: named segments, arcs, constraints, freedom
      autodim.py            a drawn sketch -> constraints and named dimensions
      files.py              DXF and SVG read into the sketch language

    cadcore/geometry/       the OCCT operations, names carried through every one
      core/                 naming (stable face names, edge = face pair), measure,
                            query, promises, provenance -- what every operation stands on
      solids/               primitives, sweeps, booleans, split, emboss, modify, transform,
                            surfaces, threads, sheet, sentry -- the operations
      assembly/             parts, mates by face name, placement, interference
      io/                   exchange (STEP, IGES, BREP), mesh (STL, OBJ, 3MF, glTF), tessellate
      kernel.py             the façade: gathers the above, implements nothing

    cadcore/features/       declare/ (registry, argument kinds); families/ (one file per
                            family of feature types -- each registers itself)
    cadcore/evaluation/     graph.py: build order + content-hash cache; api.py

    cadcore/simulation/     mesh, materials, convergence; studies/ static, modal, thermal, buckling
    cadcore/mechanism/      planar rigs solved by the sketch solver; gears
    cadcore/analysis/       drawing, printability, meshio, preview, optimise, requirements

    cadcore/ops/            the editing API: session (the guard), workspace (the fence),
                            subjects/ (the operations, grouped by what they are about)
    cadcore/service/        protocol adapters and session hosting
      server.py             line-delimited JSON; mcp.py; cli.py; resources.py; soak.py
      web.py                HTTP server, shared Hub and assistant bridge for Web and Native

    blender_addon/          the Blender UI: link/ (the kernel across the pipe), ui/,
                            operators/, viewport/ (the pick, the drags, the marks)
    native_app/             the desktop app (PySide6, Qt Quick 3D): the same words and drags
    web_ui/                 the page (React, three.js), built into cadcore/service/web_ui/
    bench/                  the assistant benchmark: a task, a run, a check
    tools/                  package.py (add-on); package_release.py (product archives);
                            verify/ headless proof; render/ pictures

## Who talks to whom

The Session owns the document, built geometry and undo history. Its host
depends on the entry point:

| Entry point | Session host | Calls from the UI |
| --- | --- | --- |
| Blender | Child Python process running `cadcore.service.server` | JSON lines over stdin/stdout |
| Web | Python HTTP server, inside `cadcore.service.web.Hub` | HTTP requests from the browser |
| Native | Qt application process, inside the same `Hub` class | Direct `Hub.call()` calls |
| Standalone MCP | `cadcore-mcp` process | MCP tools on stdio |

OpenCASCADE runs inside the process that owns the Session. Blender's
kernel process is isolated from Blender. Native shares its process with
the kernel, and Web shares the server process with it.

For Blender, standalone and attached assistants take these paths:

    Without Blender                    With Blender open

    Claude Code / Codex                Claude Code / Codex     Blender, with blender_addon/
          │ MCP on stdio                     │ MCP on stdio         │ viewport, sidebar
          ▼                                  ▼                      │
    cadcore-mcp                        cadcore-mcp --attach         │
    owns a Session,                    owns nothing: each tool      │
    reads and writes files             call is forwarded to ──┐     │
    under its workspace root                                  │     │
          │                                                   ▼     ▼
          ▼                                        link/bridge.py: a loopback socket,
    OpenCASCADE, in the                            admitting a connection by token,
    same process                                   on while the sidebar says so or an
                                                   Ask needs it; requests are queued
                                                   onto Blender's main thread
                                                              │
                                                              ▼
                                                   link/client.py
                                                              │ stdin/stdout,
                                                              │ one JSON object per line
                                                              ▼
                                                   the kernel process
                                                   (cadcore.service.server)
                                                   one Session, one undo stack,
                                                   shared by the person and the assistant

Web and Native use `web.Hub` instead of Blender's bridge and child process:

    Web browser ── HTTP ──► Handler ──┐
                                    ├──► Hub.call() ──► Session ──► OpenCASCADE
    Native Window ── direct call ────┘

These are alternative hosts, each with its own Hub and Session. Native
does not start an HTTP server. A Hub's lock serializes GUI calls and calls
from an attached assistant. The Hub's authenticated loopback bridge sends
assistant requests directly through `Hub.call()`; it does not queue them
onto Blender's main thread.

Plain `cadcore-mcp` creates an independent Session. `--attach HOST:PORT`
forwards calls to the chosen GUI's bridge and shares that GUI's document
and undo history. Opening the same file elsewhere does not share a Session.
The built-in Ask flow starts the bridge and passes its token-file path to
the attached MCP process. Web and Native's current Ask launcher uses Claude
Code command-line arguments; changing the executable name alone does not
make it a Codex launcher.

After a recorded edit, autosave attempts to write a sidecar such as
`bracket.autosave.json`. It requires a document path and autosave enabled.
Write errors do not reject the edit, and the sidecar is written directly,
so a crash during writing can leave it incomplete. Recovery is therefore
best-effort; it does not guarantee that only the last operation can be lost.

## What happens to one request

A request travels down the layers and its result comes back up. Here an
assistant changes a parameter:

    {"op": "set_parameter", "name": "width", "value": 90}
       │
       ▼
    service     finds op_set_parameter on the Session and checks its arguments
       │
       ▼
    ops         _edit(): snapshot the document and the built body ──┐
       │                                                            │ on any refusal:
       ▼                                                            │ put both back,
    model       the parameter changes in the document               │ reply with the
       │                                                            │ CadError's kind
       ▼                                                            │ and a hint
    evaluation  which features depend on width? rebuild only those  │
       │                                                            │
       ▼                                                            │
    features    each rebuilt feature calls the geometry façade      │
       │                                                            │
       ▼                                                            │
    geometry    OpenCASCADE does the work; every new face is named ─┘
       │
       ▼
    {"ok": true, "result": {"face_names": [...], "requirements": [...], ...}}

The mesh handed back afterwards carries a face name on every triangle, which
is what lets a viewport click become a name, and a name become an edit.

## An edit is all-or-nothing

Every operation that changes the document runs inside `Session._edit()`
(`cadcore/ops/session.py`); a test reads every op's source to check its
rebuild is inside the guard.

    op_add_fillet(edges, radius)              any op that changes the document
          │
    ┌─── _edit() ─────────────────────────────────────────────────────────┐
    │     │                                                               │
    │     ▼                                                               │
    │  keep: doc.snapshot(), the body, the evaluator, the rollback view   │
    │  give this state a revision number                                  │
    │     │                                                               │
    │     ▼                                                               │
    │  change the document          append the fillet feature             │
    │     │                                                               │
    │     ▼                                                               │
    │  _rebuild()                   evaluation rebuilds what depends on it │
    │     │                                                               │
    │     ▼                                                               │
    │  doc.check_units()            would the loader accept this document? │
    │     │                                                               │
    │     ├── anything raised ──►  put everything kept back, re-raise;    │
    │     │                        the reply is the CadError's kind + hint │
    │     ▼                                                               │
    │  push the snapshot on the undo stack, clear redo, autosave          │
    └─────────────────────────────────────────────────────────────────────┘

* A guard opened inside another is part of the outer one, so `apply` (a
  list of operations) is one undo step. `apply` runs only the edits the
  guard can take back (`EDITS_IN_A_BATCH`).
* Whatever a reply is computed from is computed inside the guard.
* Every recorded state has a **revision**, a number handed out once and
  never again; a snapshot carries the number of its state. Blender's undo
  restores a scene that remembers the revision on screen and asks the kernel
  for that state (`goto_revision`).
* A **drag** is a run of trial edits between `begin_drag` and `end_drag`:
  no preview is an undo step, and the end records one, or none.
* A **checkpoint** belongs to the document that made it; a restore copies
  the document's content by name (`Document.CONTENT`) and keeps the
  session's own fields.

## What a rebuild costs

The features form a graph: every `Ref()` argument is an edge.
`evaluation/graph.py` builds it in dependency order, and each node's cache
key is a hash of its own resolved arguments plus its parents' keys, filed
under the argument each parent sits in:

    parameters: width, hole_d, corner_r

      plate    box      size = [width, depth, thickness]
        │
        ▼
      bolt     hole     body = plate, face = plate/+z, diameter = hole_d
        │
        ▼
      rounded  fillet   body = bolt, edges = {parallel: z}, radius = corner_r

      key(plate)   = hash(plate's arguments)
      key(bolt)    = hash(bolt's arguments, {"body": key(plate)})
      key(rounded) = hash(rounded's arguments, {"body": key(bolt)})

      change corner_r   only key(rounded) moves        1 feature rebuilt
      change hole_d     bolt and rounded move          2 rebuilt
      change width      every key moves                a cold build

An edit near the end of a long history costs milliseconds; a change to the
first parameter costs the whole build. Rolling the history back to a
feature is "build its ancestors", which the same cache makes free. A file a
feature imports is part of its key (path, size, modification time).

Measured on a plate with a grid of holes drilled one after another:

| features | cold build | rebuild after an edit at the tip | mesh |
|---|---|---|---|
| 50 | 0.41 s | 7 ms | 0.07 s |
| 100 | 1.3 s | 14 ms | 0.41 s |
| 200 | 5.8 s | 31 ms | 0.55 s |
| 400 | 20.1 s | 49 ms | 0.94 s |

The cold build grows faster than the feature count because each boolean
works on a body with more faces than the last. A few hundred features is
comfortable; the number to improve is the boolean, not the graph.

## Extending it

**A feature type is one function with one declaration.** The declaration
is the one source for validation, the dependency graph, the catalogue an
assistant reads, and the fields the sidebar draws; a test refuses a type
that does not declare its arguments.

```python
@feature("groove", category="modify", args={
    "body": Ref(), "face": Name(), "depth": Number(),
    "at": Number(required=False, default=0.0)})
def groove(graph, f, a, ev) -> Body:
    """A groove turned into a cylindrical face."""
    return kernel.cut(f.id, graph.body_of(a["body"]), ring(f.id, a, ev))
```

A feature calls the geometry façade (`geometry/kernel.py`) and nothing
below it. An operation added to the façade declares what its result must
be (`core/promises.py`); a test holds the façade's export list to that.

**A sketch constraint is one registered function**; the refusal for an
unknown constraint lists the registry.

```python
@constraint("horizontal")
def horizontal(gcs, con, refs, value):
    """This line runs along the sketch's x axis."""
    return gcs.horizontal(refs.line())
```

**An operation is one `op_*` method on the `Session`**, inside `_edit()`
if it changes the document. It becomes a line-protocol operation and an
MCP tool by reflection, its docstring the description and its signature
the schema; the plain types the signature declares are checked before the
call. A new `CadError` kind goes on the list in `errors.py`, which a test
holds to the raises in both directions.

**A viewport tool that edits from a pick is three lines of intent** on
`EditFromSelection`; the panel greys the button and the operator refuses
from the same `wants`/`picks` declaration.

```python
class CADCORE_OT_groove(EditFromSelection, bpy.types.Operator):
    bl_idname, operation, wants = "cadcore.groove", "add_groove", 1

    def arguments(self, props, picked):
        return {"face": picked[0], "depth": props.groove_depth}
```

A change to the add-on is checked headless with
`tools/verify/verify_addon.py` and walked at random with
`tools/verify/soak_addon.py`; a geometry change that moves a golden number
is accepted with `cadcore/evaluation/tests/golden.py --write` and said in
the commit.

## A document is data, not a program

Expressions (`"width/2"`, `"hub_d > bore + 12"`) are parsed and walked --
arithmetic, comparisons, a fixed list of maths functions -- never `eval`'d;
anything else is refused as `unsafe_expression`. A document names files to
read (a STEP to import, a part to bring in); in a served session those go
through the workspace fence like every other path.

## A refusal has a kind

Every refusal is a `CadError` with a machine-readable kind from the list in
`cadcore/errors.py`, usually with a hint, because the caller is a program.
A test walks the syntax tree in both directions: every raised kind is
listed, every listed kind is raised. Kinds that look like duplicates and
are not: `no_solid` / `not_a_solid`, `unknown_feature` /
`unknown_feature_type`, `sketch_underconstrained` /
`study_underconstrained`.

## The file is a deliverable

* Every document says its format (`"format": 1`); one from a future
  version is refused by name.
* A top-level key this version does not know is kept (`Document.extra`)
  and written out again.
* A document is read and written in its own unit; the kernel works in
  millimetres. `model/tests/test_format.py` loads, saves and loads every
  shipped example and requires it back byte for byte.
* A save is written beside the target and renamed over it, so it lands
  whole or not at all.
* A document whose fields have the wrong shape, or whose feature ids carry
  a name separator, is refused before any feature is read.
