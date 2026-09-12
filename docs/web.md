# The page

[← README](../README.md)

    ./.venv/bin/python -m cadcore.service.web --open

serves the kernel over HTTP at `http://127.0.0.1:8080/` with a page beside
it. The page is a thin client like the Blender add-on: `POST /op` takes the
one JSON object the line server takes (`{"op": "add_hole", "face": ...}`)
and answers the same reply, so anything that can make an HTTP request can
drive a session -- a browser, another app, a script.

What the page does, in the order a person meets it:

* **Open** an example from the list, or a path; New; Save; Undo and Redo
  (Ctrl+Z, Ctrl+Shift+Z).
* **The picture**: the kernel's triangles in WebGL, one colour per part,
  edges drawn. Drag to orbit, right-drag or Shift-drag to pan, wheel to zoom.
* **The marks on the part**: a picked flat face grows a yellow arrow (drag it
  to push or pull the face along its normal, snapping flush with a parallel
  face), a blue plane mark (drag it for a work plane off the face), and two
  grips, a green disc that rounds the face's edges and an orange corner that
  chamfers them. Click an edge on the wire to pick it; the grips move to it.
  A drag is the kernel rebuilding the real document with the value under the
  cursor (`begin_drag` … `end_drag`), the badge beside the cursor says the
  value, in red when the shape would not take it, and letting go is one
  undoable step. Typing a number during a drag states the value; Enter keeps
  it, Esc drops it. Hole here puts a ring under the cursor on the face; the
  wheel steps M3 to M12, a click drills, Esc finishes.
* **Numbers on the part**: the picked feature's numbers float beside the
  faces it made (or named); click one to type over it. A number that is a
  parameter changes the parameter.
* **Drop a file**: a `.json` document or a `.step` / `.iges` file dropped
  anywhere on the page is sent up (`POST /upload`, under `dropped/` in the
  workspace, or the temp folder when unfenced) and opened.
* **Pick**: click a face, Shift adds. The Picked tab lists the faces and
  offers only what fits them -- a hole, push/pull, a work plane, rounding or
  chamfering the face's edges, a mate when two faces of two parts are picked,
  Drive when the part is free to move. Right-click gives the same list at
  the mouse.
* **Parameters**: type over a number and the part rebuilds.
* **Features**: switch one off, remove one.
* **Assembly**: Take Apart (a slider; only the picture moves), Drive (a
  part and an angle; Keep writes it into the document), collisions, the bill
  of materials, STEP, a drawing in a new tab.
* **Ask**: the picked faces and the document go with the question. The
  assistant CLI (`claude`, or `CADCORE_ASSISTANT`) runs against this same
  session through the loopback bridge the add-on uses, and the picture
  follows each tool call.

Paths are fenced to the directory the server was started in unless
`--unfenced` is given, as for the line server.

## How the page is built

The page is a React app on Three.js: React Three Fiber draws the scene,
Drei supplies the orbit, the view cube, the grid, the contact shadow and the
thin CAD edges. Its source is `web_ui/` at the top of the repository and
the build lands in `cadcore/service/web_ui/`, which the server serves and
the wheel ships, so a checkout runs without Node. To change the page:

    cd web_ui && npm install && npm run build      # Node 18 or later

`npm run dev` serves it with hot reload, proxying `/op` and the rest to a
kernel started with `python -m cadcore.service.web`.

The pick is Three's own raycast: the kernel's tessellation says which CAD
face every triangle belongs to, so the triangle the ray hit is a face name,
and only that face takes the accent colour. The edges are the kernel's, not
a threshold on the mesh, so a fillet's tangent edge is not drawn and a
sketch line is. A part moved for an exploded view or a drive is a matrix on
its group; the mesh does not change.

The right-click menu follows the pick. A flat face offers Push / Pull, Hole,
Plane Off It, Split Here (a cut dragged into the part; what is under it
stays), Section Here (a plane slides in as the mouse moves and the cut is
drawn on the part; a click leaves it), Emboss Text (the words asked for,
then a drag out to raise or in to cut), Draft From Here (the walls lean
away from the face's plane, degrees by a drag) and the edge rounds; a round
face offers Wrap Text Round It and Coil Round It (the pitch by a drag); two
faces offer Distance Between, Mate and Mate As with every kind the kernel
has. Checks and Output has Draft Check (pulled along the picked face's
normal, or up) and Mass; Parameters has the material and colour the bill
of materials, the mass and STEP use.

`cadcore/service/tests/test_web.py` covers the transport; `test_web_page.py`
drives the built page in a headless browser and is skipped without one
(`pip install playwright`, then `playwright install chromium`, or point
`CADCORE_CHROMIUM` at one).
