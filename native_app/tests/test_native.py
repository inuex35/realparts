"""The desktop window on the kernel, driven without a screen."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="pip install -r requirements-native.txt")

from PySide6.QtWidgets import QApplication  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))


def spin(times: int = 20) -> None:
    for _ in range(times):
        QApplication.processEvents()


@pytest.fixture(scope="module")
def window():
    app = QApplication.instance() or QApplication([])
    from native_app.app import main  # noqa: F401  -- the format the app sets up
    from native_app.window import Window

    window = Window()
    window.resize(1200, 800)
    window.show()
    spin()
    yield window
    window.view.release()
    window.close()


def pick(window, face: str) -> None:
    window.view.set_picked([face])
    window.view.picked = [face]
    window._picked([face])
    spin()


def needs_picture(window) -> None:
    """A tool picks what is under the mouse: the offscreen platform draws nothing to pick."""
    size = window.view.quick.size()
    if window.view.hit_at(size.width() // 2, size.height() // 2) is None:
        pytest.skip("no GL context to draw with; run under xvfb-run for this one")


def test_an_example_opens_into_the_panels(window):
    window.open(os.path.join(REPO, "examples", "linkage.json"))
    spin()
    assert "free" in window.statusBar().currentMessage()
    assert window.params.rowCount() == 5
    assert window.features.count() == 5
    assert window.view.parts == ["coupler", "crank", "frame", "rocker"]


def test_a_number_typed_over_rebuilds(window):
    before = window.built["volume_mm3"]
    window.params.item(1, 1).setText("50")                 # crank 40 -> 50
    spin()
    assert window.built["volume_mm3"] != before


def test_a_pick_offers_what_fits_it_in_the_addon_words(window):
    pick(window, "coupler:bar/+z")
    assert window.pick_line.property("on")
    assert "coupler:bar/+z" in window.pick_line.text()
    labels = [label for label, _ in window.menu_items()]
    assert "Push / Pull" in labels and "Round Its Edges" in labels and "Drive This Part" in labels
    assert [m.name for m in window.view.bridge.marks] == ["push", "plane", "fillet", "chamfer"]
    pick(window, "frame:base/+z")
    assert "Drive This Part" not in [label for label, _ in window.menu_items()]   # the ground stays


def test_the_assembly_tab_takes_it_apart_and_drives_it(window):
    window.explode.setValue(20)
    spin()
    assert window.view.part_matrices                       # the parts moved, the mesh did not
    window.drive_turn.setValue(45)
    window._drive_slid()
    assert "turned 45" in window.statusBar().currentMessage()
    window._interference()
    assert "no interference" in window.assembly_out.toPlainText()


def test_dragging_the_arrow_pushes_the_face(window):
    window.open(os.path.join(REPO, "examples", "bracket.json"))
    spin()
    window.view.look(-58, 35)                               # from above: the plate is not behind the wall
    spin()
    pick(window, "plate/+z")
    before = window.built["volume_mm3"]
    tip = next(m for m in window.view.bridge.marks if m.name == "push").at
    x, y = window.view.project([tip.x(), -tip.z(), tip.y()])           # y-up back to z-up
    bridge = window.view.bridge
    bridge.dragStart("push", x, y)
    spin()
    assert window.drag is not None
    ax, ay = window._drag_state["axis"]
    for k in range(1, 9):
        bridge.dragMove(x + ax * 6 * k, y + ay * 6 * k, False)
        spin(5)
    assert bridge.badge.endswith("mm") and not bridge.badgeRefused
    bridge.dragEnd()
    spin()
    assert window.drag is None and bridge.badge == ""
    assert window.built["volume_mm3"] > before
    assert window.doc["features"][-1]["type"] == "move_face"
    assert "Push/Pull plate/+z" in window.statusBar().currentMessage()


def test_a_typed_number_and_escape(window):
    pick(window, "plate/+z")
    count = len(window.doc["features"])
    window.start_from_menu("push", 500, 400)
    spin()
    assert window.view.bridge.following == "push"
    for ch in "-2":
        window.drag.key(ch)
    spin()
    distance = window.doc["features"][-1]["args"]["distance"]        # a parameter the kernel named
    assert window.doc["parameters"][distance] == -2
    window.drag.key("escape")
    spin()
    assert window.drag is None and len(window.doc["features"]) == count   # dropped, no undo step


def test_the_hole_tool_drills_where_clicked(window):
    window.open(os.path.join(REPO, "examples", "bracket.json"))
    spin()
    window.view.look(-58, 35)
    spin()
    needs_picture(window)
    pick(window, "plate/+z")
    window.start_hole_tool("plate/+z")
    spin()
    assert window.view.bridge.tool == "hole"
    assert window.view.bridge.marks == []                   # the tool owns the face
    o = window.tool["frame"]["origin"]
    x, y = window.view.project([o[0] + 5, o[1] + 5, o[2]])
    window.view.bridge.hover(x, y, False)
    spin(5)
    assert window.tool["cursor"] is not None and window.view.bridge.ring is not None
    count = len(window.doc["features"])
    window.view.bridge.hover(x, y, True)
    spin()
    assert len(window.doc["features"]) == count + 1 and window.doc["features"][-1]["type"] == "hole"
    assert "1 placed" in window.statusBar().currentMessage()
    window._end_tool()
    assert window.tool is None and window.view.bridge.tool == ""


def test_the_menu_offers_the_new_actions_and_a_split_follows_a_drag(window):
    window.open(os.path.join(REPO, "examples", "bracket.json"))
    spin()
    window.view.look(-58, 35)
    spin()
    pick(window, "plate/+z")
    labels = [label for label, _ in window.menu_items()]
    for wanted in ("Split Here", "Section Here", "Emboss Text…", "Draft From Here"):
        assert wanted in labels, labels
    before, count = window.built["volume_mm3"], len(window.doc["features"])
    window.start_from_menu("split", 500, 400)
    spin()
    assert window.drag is not None and "axis" in window._drag_state
    ax, ay = window._drag_state["axis"]
    for k in range(1, 6):
        window.view.bridge.dragMove(500 - ax * 6 * k, 400 - ay * 6 * k, False)
        spin(5)
    window.view.bridge.dragEnd()
    spin()
    assert window.drag is None
    assert window.doc["features"][-1]["type"] == "split" and len(window.doc["features"]) == count + 1
    assert window.built["volume_mm3"] < before
    window.try_op("undo")
    spin()
    assert len(window.doc["features"]) == count


def test_the_section_tool_draws_the_cut_and_the_checks_answer(window):
    pick(window, "plate/+z")
    window.start_section_tool("plate/+z")
    spin()
    assert window.view.bridge.tool == "section"
    ax, ay = window.tool["axis"]
    at = window.tool["at"]
    window.view.bridge.hover(at[0] - ax * 8, at[1] - ay * 8, False)          # a few mm into the plate
    spin()
    assert window.section and window.section["curves"]
    window.view.bridge.hover(at[0] - ax * 8, at[1] - ay * 8, True)
    spin()
    assert window.tool is None and "mm²" in window.statusBar().currentMessage()
    window._draft_check()
    assert "draft" in window.report.toPlainText()
    window._mass()
    assert " g of " in window.statusBar().currentMessage()


def test_the_material_field_writes_the_document(window):
    window.material.setText("S45C")
    window._meta_typed("material", window.material)
    spin()
    assert window.doc["meta"]["material"] == "S45C"
    assert window.material.text() == "S45C"
