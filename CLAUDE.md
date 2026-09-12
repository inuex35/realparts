# Working in this repository

Read `cadcore/README.md` first: it says what this CAD promises (a reference to a
face or an edge keeps meaning the same thing after the model changes) and how
the code is laid out. `docs/architecture.md` is the layout and what happens to
one edit; `docs/decisions.md` is why it is built that way, with the bugs that
shaped each decision. Each layer's `README.md` says what the layer does, where
to start reading it, and what it keeps.

## Run things through the project venv

```bash
./.venv/bin/python -m pytest -q -n 8            # the whole suite, ~2.5 min
./.venv/bin/python -m pytest -q cadcore/ops/tests  # one layer
./.venv/bin/python -m cadcore build examples/bracket.json
./.venv/bin/python -m cadcore.service.server    # the line protocol
./.venv/bin/python -m cadcore.service.web --open # the same over HTTP, with the page
./.venv/bin/python -m native_app examples/bracket.json   # the desktop app (requirements-native.txt)
```

Never Blender's Python for the kernel tests. The add-on is verified headless:

```bash
~/build_linux/bin/blender -b -noaudio --factory-startup -P tools/verify/verify_addon.py
./.venv/bin/python tools/package.py build         # the add-on zip
```

`verify_addon` prints `VERIFY RESULT all ok` or the failing check. Run it
after any change to `blender_addon/` -- the unit tests cannot import bpy.
Then walk it at random, with a seed you can replay:

```bash
~/build_linux/bin/blender -b -noaudio --factory-startup -P tools/verify/soak_addon.py -- 60 1
```

A GUI bug that a person hits by clicking around is a bug the soak finds by
clicking around; add the missing move to `soak_addon.py` when one gets past.

## The rules the tests enforce (so do not fight them)

* **Layers import downwards only**, at module level. The order is
  `LAYERS` in `cadcore/tests/test_architecture.py`: model, sketching, geometry,
  features, evaluation, simulation, mechanism, analysis, ops, service. A new
  module goes in a layer directory, never in `cadcore/` itself (only
  `errors.py`, `names.py`, `progress.py` live there).
* `simulation/` is reached for **inside functions only** from other layers, so
  `requirements-sim.txt` stays optional.
* `model/` and `sketching/` **never import geometry**. Reading a document must
  not load OpenCASCADE.
* **Names are spelled in `cadcore/names.py`.** Do not build a face or edge
  name with an f-string anywhere else (`names.face(feature, role)`,
  `names.piece`, `names.instance`, `names.scoped`).
* **Every `CadError` kind is in `errors.KINDS`**, and every kind is raised
  somewhere. Add the kind to the list when you add a raise.
* **A feature declares its arguments** (`@feature(..., args={...})`); a study
  declares its keys (`simulation/spec.py`); a requirement quantity is in
  `model/requirements.QUANTITIES`. The declaration is the validator, the
  graph edge, the catalogue and the UI field -- there is no second list.
* **Every rebuild happens inside `_edit()`** in `ops/`; an op that needs a
  body calls `self._ensure_body()`. A document with a plane and a sketch and
  no solid is *under construction*, not an error.
* `open()` / `read_text()` / `write_text()` on text always pass
  `encoding="utf-8"`.
* No test spells a virtualenv path; `sys.executable` is the interpreter.
* **Tests live beside their layer** in `<layer>/tests/`; repo-wide ones in
  `cadcore/tests/`; the add-on's in `blender_addon/tests/` (they load the file
  they test by path, without bpy); packaging in `tools/tests/`.

## The golden record

`cadcore/evaluation/tests/golden.json` holds what every example measures. If a
geometry change moves a number or a face name, check the new value is right
(e.g. probe along the new normal), then `./.venv/bin/python
cadcore/evaluation/tests/golden.py --write` and say in the commit what changed
and why.


## How it should feel: target -> action

`docs/experience.md` is the rule for every change to the add-on. Short
form: the person picks the thing, then acts on it; the selection is the
context and is never asked for twice; the assistant gets what is on screen
with the question; hands and assistant go through the same operations; the
result shows on the part at once; nobody manages modes. Before adding a
panel, a button or a menu, measure the road from wanting to having and try
to remove a step from what exists instead.

## Comments and docstrings: short, plain, and only where they carry a fact

Claude's habit here is to narrate: a paragraph above every function about
what used to be wrong, why the old way was tempting, and how the new way is
better. That is commit-message material. In the code it doubles the file and
hides the one line that matters. PR #4 removed 5,000 lines of it.

Rules, with numbers:

* A **module docstring is at most 5 lines**: what the module is for, and the
  one thing a reader must know before using it.
* A **function docstring is one line by default.** Go past that only for
  arguments a caller cannot guess, or a decision a caller has to know and
  cannot read off the signature: which of two answers comes back, which side
  of a plane is kept, what makes a refusal a refusal. Never to narrate.
* An **inline comment is at most 2 lines**, and says something the code does
  not. Never restate the code.
* **No history in the code.** How it used to be wrong belongs in the commit
  message, not above the function. `git log`, `docs/decisions.md` and
  `docs/caught.md` are where that story lives. No test can police this: "used to" and "no longer" are
  ordinary English for the state a thing is in -- "the surface the cylinder
  used to have" is this operation, not last year -- and a check on the words
  alone was wrong eight times in thirteen. It is a rule for the reader.
* **Plain words, everywhere: code, comments, docstrings, commit messages,
  READMEs, UI text.** The reader may not be a native English speaker. If a
  shorter or more common word means the same, use it. Sentences short.

  | do not write | write |
  |---|---|
  | invariant, guarantee | rule, promise |
  | topological entity | face, edge |
  | resolve a reference | find the face the name points at |
  | instantiate, materialise | make |
  | deterministic | the same every time |
  | idempotent | safe to run twice |
  | orthogonal | separate, unrelated |
  | leverage, utilise | use |
  | semantics | meaning |
  | canonical | the one true form |
  | ephemeral, transient | short-lived |
  | heuristic | a rule of thumb |
  | "the caller is deciding what to do next, not reading a stack trace" | "a program reads this, so it has to be a code, not a sentence" |

  Names in code too: `changed_by_hand` over `mutation_detected`, `guard_mesh`
  over `enforce_mesh_integrity`, `picked` over `selection_context`.

Do not write this:

```python
# Re-entrant: an edit made *inside* another edit is part of it, not an
# edit of its own. `apply` runs a list of operations under one guard so
# that ten steps are one undo step and a refusal at the eighth leaves
# nothing behind -- and every one of those operations opens its own
# guard, which without this would snapshot ten times, push ten undo
# steps, and roll back only the last.
if self._editing:
    yield doc
    return
```

Write this:

```python
if self._editing:            # an edit inside an edit is part of the outer one
    yield doc
    return
```

**Exceptions -- these comments are required, not optional.** Getting them
wrong breaks parts, so write them even when the code looks obvious:

* **Units.** Any number or argument that is not millimetres, newtons, MPa or
  degrees says what it is (`# tonnes per mm^3`, `# radians`, `# g, not kg`).
* **Coordinate frames.** Anything that mixes world, face-local (u, v), sketch,
  or Blender coordinates says which is which and which way the normal points.
* **Orientation conventions.** Sign of a normal, direction of a positive
  angle, which side of a plane is "above", inward vs outward.
* **Non-obvious formulas.** A line of maths gets the name of the rule or a
  one-line derivation (`# Willis: carrier turns ring/(sun+ring) per sun turn`).
* **Kernel quirks that are load-bearing.** `# OCCT's constructor already
  performs the boolean; Build() would run it twice` stays.

How to check before committing: `git diff --stat` and then
`git diff | grep -c '^+\s*#'` against `git diff | grep -c '^-\s*#'`. If the
diff adds more comment lines than it adds code lines, cut comments until it
does not, unless every added comment is one of the exceptions above.

## Commit messages

Plain words. First line: what changed, as a sentence. Body: what was wrong
and how it showed, what the change guarantees now -- at most about 12 lines.
The history and the reasoning go here, not in the code.

## The Ask box and other people's subscriptions

Ask can run through `claude -p` (Claude plan) or `codex exec` (ChatGPT plan)
as a child process, with this Blender as the MCP server. Only the official
CLI is started; no login token is read or reused. Anthropic's rule on this
changed three times in 2026 (banned in February, a separate credit in May,
paused on June 15 so it counts against the plan again). Before selling, read
https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan
and OpenAI's terms again, and change the preference text if they changed.

## What not to commit

`examples/aircraft/ examples/evtol/ examples/rally/ examples/spot/
examples/v8/ examples/workshop/ tools/render/render_evtol.py` are the owner's
creations and stay untracked. `build/` and `.kernel/` are ignored. Do not edit
anything under `.kernel/cp313` -- it is a pip install, not source.

Commit and push only when asked. Commit messages say what was wrong, how it
showed, and what the fix guarantees -- read `git log` for the voice.

## The Blender used for GUI checks

A Windows Blender 5.2.1 at `C:\blender-cad-test\blender-5.2.1-windows-x64`
has the add-on junctioned from `C:\blender-cad-test\oss-cad\blender_addon`.
That checkout is a copy, not a clone: sync it with rsync from `git ls-files`
(plus changed files) and restart Blender. Its kernel wheels are in its own
`.kernel/cp313`; leave them. Screenshots: `tools/render/shoot_panel.py`
(sidebar), or PowerShell `PrintWindow` on the Blender window.

## The kernel runs out of process

Blender never imports `cadcore`. It talks to `cadcore.service.server` over one
JSON object per line; `cadcore.service.mcp` is the same operations as MCP
tools (`--attach` drives the session Blender is showing). Every `op_*` method
of `ops/session.py` is automatically an operation and a tool; its docstring is
the description an assistant reads.
