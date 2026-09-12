# blender_addon

The Blender side. It closes the loop that plain CAD import leaves open:

    viewport selection -> CAD face name -> feature edit -> rebuild -> mesh

The add-on never imports `cadcore`. It starts the kernel as a separate
process in Blender's own Python and talks to it over the line protocol, so
a crash in OpenCASCADE kills the kernel, not Blender; a test reads the
add-on's imports to keep it that way.

## Where to start

* `viewport/pick.py` is the tool in the person's hand: a click picks a face,
  an edge, a corner, a sketch point or a number, and a press on what the
  overlay draws (the arrow, the grips, the pencil, the plane mark) starts a
  drag.
* `ui/overlay.py` draws those marks; `viewport/marks.py` is where both
  record and read them.
* `link/` is the plumbing:
  * `client.py` starts the kernel;
  * `sync.py` turns its tessellation into a mesh with a face name on every
    triangle;
  * `state.py` refreshes the sidebar and follows the kernel on undo;
  * `bridge.py` lets an assistant join the session.

    Blender                                              the kernel process
    click two faces in the viewport
         │
         ▼
    ui/selection.py     the face name each picked triangle carries
         │  ["plate/+z", "plate/+x"]
         ▼
    operators/edits.py  EditFromSelection: is the pick the right size
                        for this tool? then one call
         │  {"op": "add_fillet", "edges": [...], "radius": 2}
         ▼
    link/client.py  ────────────── stdin/stdout ──────────────►  Session.op_add_fillet
    link/sync.py    ◄──────────── {"ok": true, ...} ────────────  tessellation, a face
         │                                                       name on each triangle
         ▼
    a new mesh, the selection carried across on the names;
    link/state.py refreshes the sidebar and pushes one Blender undo step

## What it keeps

* **Every triangle knows its face**, so a selection is a list of names and
  every operator is "these names, this operation".
* **The kernel owns the truth.** Blender's undo restores the scene, which
  remembers the kernel's revision; the kernel is then asked for that state,
  and its parameters are made to match the scene's.
* **A drag is a real rebuild** on every mouse move, coalesced, inside a
  kernel drag, so letting go is one undo step and Esc is none. The value
  follows the cursor; past what the shape can take, the last shape that
  built stays on screen and letting go keeps it.
* **Nobody manages modes.** The toolbar is the pick and the two tools that
  place many things in a row; everything else is reached from what is drawn
  on the part or by right-clicking it ([docs/experience.md](../docs/experience.md)).
* **The bridge admits a connection by token**, strips the client's own
  parameters from a request, and refuses a file outside the open document's
  folder. It is on while the sidebar says so or an Ask needs it.

`tests/` load the one file they test by path, without `bpy`. The add-on as
a whole is verified headless by `tools/verify/`. The user-facing manual is
[docs/addon.md](../docs/addon.md).
