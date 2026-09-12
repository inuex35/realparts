# The desktop app

[← README](../README.md)

    pip install -r requirements-native.txt
    ./.venv/bin/python -m native_app examples/linkage.json

A Qt window on the kernel, and the third thin client beside the Blender
add-on and [the page](web.md). It does what the page does with the same
words -- open, pick, act on the pick, parameters, features, the assembly
tab, Ask -- and the same kernel operations, so a feature added to
`ops/session.py` is one button away in each. The picture is Qt Quick 3D
(`native_app/scene.qml`): physically based materials, a procedural sky as
the light probe, soft shadows and ambient occlusion, MSAA. Every CAD face
is its own model, so a pick is Qt's own `View3D.pick` and answers a face
name; only the picked face takes the accent colour. The kernel's edges are
drawn as lines. A part is a node, so the exploded view and a drive move it
by a position and a rotation while the meshes stay as they are. The kernel
is z-up and Qt Quick 3D y-up, which one rotated node at the root settles.

Unlike the add-on, the kernel runs in the app's own process: `native_app`
imports `cadcore` and uses `cadcore.service.web.Hub`, the same session,
lock and assistant bridge the HTTP server uses. There is no line protocol
to cross, and Ask goes through `cadcore.service.mcp --attach` on the hub's
loopback bridge exactly as it does from a browser.

`native_app/tests` drives the window on Qt's offscreen platform and is
skipped where PySide6 is not installed. The offscreen platform draws no
picture, so the one test that picks under the mouse (the hole tool) runs
only with a screen: `QT_QPA_PLATFORM=xcb xvfb-run -a ./.venv/bin/python -m
pytest native_app/tests`.

A `.json`, `.step` or `.iges` file dropped on the window opens.

The marks are the add-on's, drawn in the scene as small models of their own
(`Repeater3D` over `bridge.marks`): the yellow arrow tip on a flat face is
pushed or pulled along the normal, the blue plane mark is dragged off the
face to make a work plane, the green disc rounds and the orange cone
chamfers the picked edges or the picked face's edges. A press on a mark
starts a drag (`native_app/drags.py`, the same begin_drag / end_drag
contract as the page's `drags.js`): every move is a trial rebuild, the
badge beside the cursor says the value or, in red, why the kernel refused,
digits typed replace the mouse, Enter keeps and Escape drops. A pushed face
snaps flush with a parallel face. The hole tool (right-click, Hole) follows
the mouse with a ring of the chosen size, snaps to hole centres, drills on
a click and stops on Escape. The picked feature's numbers float beside the
faces it made; a click types over one. The right-click menu offers what
fits the pick, in the add-on's words (Push / Pull, Hole, Plane Off It,
Round Its Edges, Mate, Drive This Part, Ask About This).

A flat face also offers Split Here (a cut parallel to the face, dragged into
the part; what is under it stays), Section Here (a plane slides in as the
mouse moves and the cut is drawn on the part; a click leaves it), Emboss
Text (the words asked for, then a drag out to raise or in to cut) and Draft
From Here (the walls lean away from the face's plane, degrees by a drag). A
round face offers Wrap Text Round It and Coil Round It (a drag sets the
pitch). Two faces offer Distance Between and Mate As, with every kind the
kernel has. Checks and Output has Draft Check (pulled along the picked
face's normal, or up) and Mass; Parameters has the material and colour the
bill of materials, the mass and STEP use.

The sidebar is the document, as on the page: the pick line, History with
Roll Back Here and the picked feature's numbers, Parameters, Parts for an
assembly, Ask, Checks and Output.
