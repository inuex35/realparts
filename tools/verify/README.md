# tools/verify

End-to-end checks that run the add-on inside a real, headless Blender. They
cover what unit tests cannot:

- the zip installs
- the kernel starts from Blender's own Python
- a selection in the viewport becomes an edit and comes back as a mesh

Each script exits non-zero on failure. CI runs them on Windows and Linux.

| script | what it does |
|---|---|
| `soak_addon.py` | walks the add-on at random: New, Start, pick in edit mode, a tool, undo, roll back, remove, bake. Any traceback is a failure; the seed replays it |
| `verify_addon.py` | runs the add-on from the checkout: open a document, select a face, fillet it, rebuild, check the reference still resolves |
| `verify_retail.py` | installs the built zip into a fresh Blender profile the way a user would, presses "Install the CAD kernel", then opens, builds, exports, draws and simulates a document. Also starts the MCP server from the config the add-on writes and builds a document through it |
| `verify_extension.py` | installs the same zip through Blender's extension system instead of the legacy add-on path |
| `verify_undo.py` | checks that Blender's undo and the kernel's undo stay in step |

Run one as `blender -b -P tools/verify/<script>.py -- <args>`; the docstring
of each script lists the arguments. `tools/package.py` builds the zip they
install.
