"""Create a visible sketch before making a solid."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QPushButton

from native_app.window import Window


@pytest.mark.parametrize("plane", ["xy", "xz", "yz"])
@pytest.mark.parametrize("shape", ["rect", "circle"])
def test_sketch_then_extrude(plane, shape):
    app = QApplication.instance() or QApplication([])
    window = Window()
    try:
        window.sketch_plane.setCurrentIndex(window.sketch_plane.findData(plane))
        window.sketch_shape.setCurrentIndex(window.sketch_shape.findData(shape))
        buttons = {b.text(): b for b in window.findChildren(QPushButton)}
        buttons["Create Sketch"].click()
        app.processEvents()
        assert [f["type"] for f in window.doc["features"]] == ["plane", "sketch"]
        assert not window.built.get("faces")
        assert window.view.edges
        assert window.view._part_objects
        assert window.view.framed
        buttons["Extrude Sketch"].click()
        assert window.built["volume_mm3"] > 0
        assert window.doc["features"][-1]["type"] == "extrude"
        window.try_op("undo")
        assert [f["type"] for f in window.doc["features"]] == ["plane", "sketch"]
        assert window.view.edges
    finally:
        window.view.release()
        window.close()
