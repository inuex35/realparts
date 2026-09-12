# ops

The editing API. `Session` holds one open document, the body built from it
and the evaluator that built it; every `op_*` method is one operation, and
`service/` turns them into line-protocol operations and MCP tools by
reflection, docstring as description.

## Where to start

`session.py`: the `Session`, `_edit()` and `_rebuild()`. The `op_*` methods
are spread over the mixins in `subjects/`, one file per topic -- documents
and history, modelling from a selection, sketches, inspection, analysis,
requirements. They are chapters of one class, not layers. `workspace.py`
is the fence.

    Session
    ├── doc         the Document (model/)            ─┐
    ├── body        the Body built from it (geometry/) │ saved before an edit and
    ├── evaluator   the Evaluator that built it        │ put back if it refuses
    │               (evaluation/), with its cache     ─┘
    ├── undone[]    document snapshots, oldest first    ─┐ undo and redo; each carries
    ├── redone[]                                        ─┘ the revision of its state
    ├── view_upto   the feature the history is rolled back to, or None
    └── saved       whether the file on disk holds this document

## What it keeps

* **Every edit is all-or-nothing, shape included.** `_edit()` saves the
  document, the body and the evaluator, applies the edit, rebuilds inside
  the guard, and on any refusal restores all three and re-raises. Whatever
  a reply is computed from is computed inside the guard. A guard inside a
  guard is part of the outer one, so a batch is one undo step. A test reads
  every op's source and checks its rebuild is inside its guard.
* **Every recorded state has a revision**, a number handed out once and
  never again; Blender's undo asks for a state by it (`goto_revision`).
* **A drag is a run of trials** between `begin_drag` and `end_drag`: no
  preview is an undo step, and the end records one, or none.
* **A checkpoint belongs to the document that made it** and goes when
  another is opened; a restore copies the document's content by name and
  keeps the session's own fields.
* **`apply` runs only the edits the guard can take back**
  (`EDITS_IN_A_BATCH`); a test holds that list to the guard.
* **A document with no solid yet is under construction, not an error.**
* **A served session has a root** and reads and writes only under it, the
  files the document names included; a library session is not confined.

Why: [docs/decisions.md](../../docs/decisions.md).
