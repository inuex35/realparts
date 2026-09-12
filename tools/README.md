# tools

Scripts that are not part of the kernel or the add-on. They cover two things
a unit test cannot:

- the add-on works inside a real Blender, installed the way a user installs it
- the pictures in `docs/` match what the code produces

| | |
|---|---|
| `package.py` | builds the installable add-on zip: the add-on, the kernel's source, the examples and the manual. `tests/` checks its contents against what `pyproject.toml` ships |
| `verify/` | drives the add-on headless: from a checkout, from the installed zip in a fresh Blender profile, and as a Blender extension. Each script exits non-zero on failure; CI runs them on Windows and Linux |
| `render/` | turntables, stress fields, mechanisms in motion and screenshots of the sidebar, rendered with `blender -b`. The images in `docs/images/` come from here |
