"""The page in a real browser: open, see, pick, change a number, take apart."""
import os
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api", reason="pip install playwright")

from cadcore.service import web  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))


@pytest.fixture(scope="module")
def served():
    server = web.serve(port=0, root=None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


@pytest.fixture(scope="module")
def page(served):
    server = served
    url = "http://%s:%d/" % server.server_address[:2]
    with playwright.sync_playwright() as pw:
        flags = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                 "--ignore-gpu-blocklist"]
        browser = None
        # the package's own browser, or one already on the machine
        for where in (None, os.environ.get("CADCORE_CHROMIUM"), "/opt/pw-browsers/chromium"):
            if where is not None and not os.path.exists(where):
                continue
            try:
                browser = pw.chromium.launch(args=flags, executable_path=where)
                break
            except Exception as exc:                               # noqa: BLE001
                why = str(exc).splitlines()[0]
        if browser is None:
            pytest.skip("no browser to drive: %s" % why)
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.goto(url)
        yield page
        browser.close()


SLIDE = """([id, value]) => {
  const s = document.getElementById(id);
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(s, value);
  s.dispatchEvent(new Event('input', { bubbles: true }));
  s.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
}"""   # a React-controlled slider: set through the native setter, then the events React reads


def opened(page, label: str = "four-bar linkage") -> None:
    """The example on screen; each test stands on its own, whichever runs first."""
    if page.evaluate("document.getElementById('examples').selectedOptions[0].textContent") == label:
        return
    page.select_option("#examples", label=label)
    page.wait_for_function("document.getElementById('status').textContent.includes('faces')", timeout=60000)
    page.wait_for_timeout(500)                                  # the scene has drawn


def test_an_example_opens_and_is_drawn(page):
    opened(page)
    assert "free" in page.text_content("#status")            # the linkage reports its freedom
    assert page.locator("#features li").count() == 5
    assert page.evaluate("document.querySelector('#params input').value") == "120"


def test_a_click_picks_a_face_and_the_menu_offers_what_fits_it(page):
    opened(page)
    box = page.locator("canvas").first.bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_function("window.__realparts.state().picked.length === 1", timeout=10000)
    name = page.evaluate("window.__realparts.state().picked[0]")
    assert ":" in name and "/" in name                          # a scoped face name
    assert name in page.text_content("#pick")
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, button="right")
    page.wait_for_selector("#menu")
    labels = page.locator("#menu .item").all_text_contents()
    assert any(l.startswith("Round Its Edges") for l in labels) and any(l.startswith("Drive This Part") for l in labels)
    page.keyboard.press("Escape")                               # the menu goes
    page.wait_for_function("!document.getElementById('menu')", timeout=10000)
    page.mouse.click(box["x"] + box["width"] - 80, box["y"] + 110)     # empty space: the pick clears
    page.wait_for_function("window.__realparts.state().picked.length === 0", timeout=10000)


def test_a_number_typed_over_rebuilds(page):
    opened(page)
    before = page.text_content("#status")
    page.wait_for_selector("#params tr:nth-child(2) input")
    page.fill("#params tr:nth-child(2) input", "50")           # crank 40 -> 50
    page.press("#params tr:nth-child(2) input", "Enter")
    page.wait_for_function("(was) => document.getElementById('status').textContent !== was", arg=before, timeout=60000)
    assert "faces" in page.text_content("#status")


def test_the_assembly_tab_takes_it_apart_and_drives_it(page):
    opened(page)
    page.click("#sec-parts h4")
    page.wait_for_selector("#explode")
    page.evaluate(SLIDE, ["explode", 2])
    page.wait_for_function("document.getElementById('explode-v').textContent === '2'")
    page.click("#interference")
    page.wait_for_function("document.getElementById('assembly-out').textContent.includes('interference')", timeout=60000)
    page.evaluate(SLIDE, ["drive-turn", 45])
    page.wait_for_function("document.getElementById('status').textContent.includes('turned 45')", timeout=60000)


def _screen(page, point):
    return page.evaluate("(pt) => window.__realparts.project(pt)", point)


def _extent(session):
    vs = session.op_tessellate()["vertices"]
    lo = [min(v[i] for v in vs) for i in range(3)]
    hi = [max(v[i] for v in vs) for i in range(3)]
    return sum((hi[i] - lo[i]) ** 2 for i in range(3)) ** 0.5


def test_dragging_the_arrow_pushes_the_face_and_is_one_undo_step(page, served):
    """The arrow on a picked flat face: drag it along the normal, the model follows,
    letting go is one undoable step with the last value that built."""
    opened(page, "camera bracket")
    session = served.hub.session
    page.evaluate("window.__realparts.pick('plate/+z')")
    page.wait_for_function("window.__realparts.state().picked.length === 1")
    frame = session.op_face_frame("plate/+z")
    tip = [frame["origin"][i] + frame["normal"][i] * _extent(session) * 0.22 for i in range(3)]
    t, o = _screen(page, tip), _screen(page, frame["origin"])
    length = ((t["x"] - o["x"]) ** 2 + (t["y"] - o["y"]) ** 2) ** 0.5
    ax, ay = (t["x"] - o["x"]) / length, (t["y"] - o["y"]) / length
    before, depth = session.op_build()["volume_mm3"], session.op_describe_document()["undo_depth"]
    page.mouse.move(t["x"], t["y"])
    page.mouse.down()
    for k in range(1, 8):
        page.mouse.move(t["x"] + ax * 8 * k, t["y"] + ay * 8 * k)
        page.wait_for_timeout(150)
    page.wait_for_function("document.getElementById('badge') && document.getElementById('badge').textContent.includes('mm')", timeout=20000)
    page.mouse.up()
    page.wait_for_function("!window.__realparts.state().drag", timeout=30000)
    doc = session.op_describe_document()
    assert session.op_build()["volume_mm3"] > before
    assert doc["undo_depth"] == depth + 1 and doc["features"][-1]["type"] == "move_face"
    # its number floats beside the face it moved, and is the feature shown in the steps
    page.click("#features li:last-child")
    page.wait_for_function("document.querySelectorAll('.dims .dim').length === 1", timeout=10000)
    assert "distance" in page.text_content(".dims .dim")


def test_dragging_the_green_disc_rounds_the_picked_edge(page, served):
    opened(page, "camera bracket")
    session = served.hub.session
    mesh = session.op_tessellate()
    edge = next(k for k in mesh["edges"] if "plate/+z" in k and "plate/+x" in k and "@0" in k)
    page.evaluate("(e) => window.__realparts.pickEdge(e)", edge)
    page.wait_for_function("window.__realparts.state().pickedEdges.length === 1")
    line = mesh["edges"][edge]
    mid = line[len(line) // 2]
    vs = mesh["vertices"]
    centre = [(min(v[i] for v in vs) + max(v[i] for v in vs)) / 2 for i in range(3)]
    extent = _extent(session)
    lean = [mid[0] - centre[0], mid[1] - centre[1], mid[2] - centre[2] + extent * 0.3]
    norm = sum(c * c for c in lean) ** 0.5
    disc = [mid[i] + lean[i] / norm * extent * 0.09 for i in range(3)]
    d = _screen(page, disc)
    depth = session.op_describe_document()["undo_depth"]
    page.mouse.move(d["x"], d["y"])
    page.mouse.down()
    for k in range(1, 5):
        page.mouse.move(d["x"] + 8 * k, d["y"])
        page.wait_for_timeout(150)
    page.wait_for_function("document.getElementById('badge') && document.getElementById('badge').textContent.includes('mm')", timeout=20000)
    page.mouse.up()
    page.wait_for_function("!window.__realparts.state().drag", timeout=30000)
    doc = session.op_describe_document()
    assert doc["undo_depth"] == depth + 1
    assert doc["features"][-1]["type"] == "fillet" and doc["features"][-1]["args"]["edges"] == [edge]


def test_a_dropped_document_opens(page, served):
    import base64
    import json
    raw = json.dumps({"features": [{"id": "blk", "type": "box", "size": [10, 20, 30]}], "result": "blk"})
    page.evaluate("""async (data) => {
        const reply = await (await fetch('/upload', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: 'dropped block.json', data})})).json();
        if (!reply.ok) throw new Error(reply.message);
    }""", base64.b64encode(raw.encode("utf-8")).decode("ascii"))
    assert served.hub.session.path.endswith("dropped_block.json")
    assert served.hub.session.op_build()["volume_mm3"] == 6000


def reopened(page, label: str) -> None:
    """The example fresh from disk, whatever earlier tests did to it."""
    opened(page, "four-bar linkage" if label != "four-bar linkage" else "camera bracket")
    opened(page, label)


def _meta(session, key: str, wanted: str, tries: int = 40) -> str:
    """The document's meta field once the page's edit has landed in the kernel."""
    import time
    for _ in range(tries):
        value = session.op_describe_document()["meta"].get(key)
        if value == wanted:
            return value
        time.sleep(0.25)
    return session.op_describe_document()["meta"].get(key)


def test_the_menu_offers_the_new_actions_and_a_split_follows_the_mouse(page, served):
    """A flat face's menu: Split Here starts a drag the mouse is followed on; a click keeps it."""
    reopened(page, "camera bracket")
    session = served.hub.session
    page.evaluate("window.__realparts.pick('plate/+z')")
    page.wait_for_function("window.__realparts.state().picked.length === 1")
    labels = page.evaluate("window.__realparts.actions()")
    for wanted in ("Split Here", "Section Here", "Emboss Text…", "Draft From Here", "Push / Pull"):
        assert wanted in labels, labels
    frame = session.op_face_frame("plate/+z")
    o = _screen(page, frame["origin"])
    b = _screen(page, [frame["origin"][i] + frame["normal"][i] * 10 for i in range(3)])
    length = ((b["x"] - o["x"]) ** 2 + (b["y"] - o["y"]) ** 2) ** 0.5
    ax, ay = (b["x"] - o["x"]) / length, (b["y"] - o["y"]) / length
    before, depth = session.op_build()["volume_mm3"], session.op_describe_document()["undo_depth"]
    page.mouse.move(o["x"], o["y"])
    assert page.evaluate("(p) => window.__realparts.action('Split Here', p[0], p[1])", [o["x"], o["y"]])
    for k in range(1, 6):                                       # into the part: against the normal
        page.mouse.move(o["x"] - ax * 6 * k, o["y"] - ay * 6 * k)
        page.wait_for_timeout(150)
    page.wait_for_function("document.getElementById('badge') && document.getElementById('badge').textContent.includes('mm')", timeout=20000)
    page.mouse.click(o["x"] - ax * 30, o["y"] - ay * 30)
    page.wait_for_function("!window.__realparts.state().drag", timeout=30000)
    doc = session.op_describe_document()
    assert doc["features"][-1]["type"] == "split" and doc["undo_depth"] == depth + 1
    assert session.op_build()["volume_mm3"] < before
    page.click("button[title^='Undo']")                        # the plate is whole again for the next test
    page.wait_for_function("(d) => document.getElementById('status').textContent.includes('faces')", arg=None, timeout=30000)
    page.wait_for_timeout(300)
    assert session.op_describe_document()["undo_depth"] == depth


def test_the_section_tool_draws_the_cut_and_the_checks_answer(page, served):
    reopened(page, "camera bracket")
    session = served.hub.session
    page.evaluate("window.__realparts.pick('plate/+z')")
    page.wait_for_function("window.__realparts.state().picked.length === 1")
    frame = session.op_face_frame("plate/+z")
    o = _screen(page, frame["origin"])
    b = _screen(page, [frame["origin"][i] + frame["normal"][i] * 10 for i in range(3)])
    length = ((b["x"] - o["x"]) ** 2 + (b["y"] - o["y"]) ** 2) ** 0.5
    ax, ay = (b["x"] - o["x"]) / length, (b["y"] - o["y"]) / length
    assert page.evaluate("(p) => window.__realparts.action('Section Here', p[0], p[1])", [o["x"], o["y"]])
    page.wait_for_function("window.__realparts.state().tool && window.__realparts.state().tool.kind === 'section'")
    page.mouse.move(o["x"] - ax * 20, o["y"] - ay * 20)
    page.wait_for_function("window.__realparts.state().section && window.__realparts.state().section.curves.length > 0", timeout=30000)
    page.mouse.click(o["x"] - ax * 20, o["y"] - ay * 20)
    page.wait_for_function("!window.__realparts.state().tool", timeout=10000)
    assert "mm²" in page.text_content("#status")
    page.click("#sec-output h4")
    page.click("#draft-check")
    page.wait_for_function("document.getElementById('report') && document.getElementById('report').textContent.includes('draft')", timeout=60000)
    page.click("#mass")
    page.wait_for_function("document.getElementById('status').textContent.includes(' g of ')", timeout=60000)


def test_the_material_and_colour_fields_write_the_document(page, served):
    reopened(page, "camera bracket")
    session = served.hub.session
    page.click("#sec-params h4") if not page.is_visible("#material") else None
    page.fill("#material", "S45C")
    page.press("#material", "Enter")
    assert _meta(session, "material", "S45C") == "S45C"
    page.fill("#colour", "#ff8800")
    page.press("#colour", "Enter")
    assert _meta(session, "colour", "#ff8800") == "#ff8800"
