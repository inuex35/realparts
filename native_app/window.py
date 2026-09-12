"""The window: the picture, and beside it the sidebar that is the document, as in the add-on."""
from __future__ import annotations

import os

import json
import math

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QMatrix4x4
from PySide6.QtWidgets import (QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QPushButton, QScrollArea, QSlider,
                               QTableWidget, QTableWidgetItem, QTextBrowser, QTextEdit, QToolBar, QToolButton,
                               QVBoxLayout, QWidget)

from cadcore.service import web
from . import theme
from .drags import LiveDrag, Snapper, snap, to_3d, to_uv
from .viewport import Viewport

# operations that only answer a question: the picture and the panels stay as they are
QUERIES = {"interference", "bill_of_materials", "export_step", "drawing", "check", "explode", "drive",
           "describe_document", "describe_faces", "tessellate", "select_edges", "face_frame", "section",
           "measure", "draft_check", "mass_properties", "curvature", "project"}
HOLE_STEPS = ((3.4, "M3"), (4.5, "M4"), (5.5, "M5"), (6.6, "M6"), (9.0, "M8"), (11.0, "M10"), (13.5, "M12"))
YELLOW, GREEN, ORANGE, BLUE = "#f2c14e", "#6fd39a", "#f08a4b", "#7aa2f7"


def pose_matrix(pose) -> QMatrix4x4:
    """The kernel's pose -- a 3x3 rotation and an offset, rows -- as a Qt matrix."""
    R, t = pose
    return QMatrix4x4(R[0][0], R[0][1], R[0][2], t[0], R[1][0], R[1][1], R[1][2], t[1],
                      R[2][0], R[2][1], R[2][2], t[2], 0, 0, 0, 1)


def feature_of(name: str) -> str:
    """The feature that made a face or an edge: the name without its scope, role and suffixes."""
    head = name.split("|")[0].split("/")[0].split(":")[-1]
    for mark in "#@~":
        head = head.split(mark)[0]
    return head


def body_of(name: str) -> str:
    return name.split(":")[0] if ":" in name else name.split("/")[0]


class Section(QWidget):
    """A titled part of the sidebar that folds."""

    def __init__(self, title: str, open_: bool = True, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.head = QToolButton()
        self.head.setText(title)
        self.head.setObjectName("section")
        self.head.setCheckable(True)
        self.head.setChecked(open_)
        self.head.setArrowType(Qt.DownArrow if open_ else Qt.RightArrow)
        self.head.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.head.toggled.connect(self._toggled)
        layout.addWidget(self.head)
        self.body = QWidget()
        self.body.setVisible(open_)
        self.inner = QVBoxLayout(self.body)
        self.inner.setContentsMargins(12, 2, 12, 12)
        layout.addWidget(self.body)

    def _toggled(self, on: bool) -> None:
        self.body.setVisible(on)
        self.head.setArrowType(Qt.DownArrow if on else Qt.RightArrow)

    def open(self) -> None:
        self.head.setChecked(True)


class Window(QMainWindow):
    def __init__(self, hub: web.Hub | None = None):
        super().__init__()
        self.hub = hub or web.Hub(root=None)
        self.doc: dict | None = None
        self.built: dict | None = None
        self.picked: list = []
        self.picked_edges: list = []
        self.shapes: dict = {}
        self.feature: str | None = None
        self.frame: dict | None = None            # the picked flat face's frame
        self.drag: LiveDrag | None = None
        self._drag_state: dict = {}
        self.tool: dict | None = None
        self.section: dict | None = None          # the cut the section tool drew, until the next pick
        self.driven = None
        self.setWindowTitle("RealParts")
        self.setAcceptDrops(True)
        self.resize(1280, 820)
        self.setStyleSheet(theme.QSS)
        self.view = Viewport(self)
        self.setCentralWidget(self.view)
        self.view.picked_changed.connect(self._picked)
        self.view.edges_changed.connect(self._edges_picked)
        self.view.menu_wanted.connect(self._menu)
        bridge = self.view.bridge
        bridge.drag_started.connect(self._drag_started)
        bridge.drag_moved.connect(self._drag_moved)
        bridge.drag_ended.connect(self._drag_ended)
        bridge.hovered.connect(self._hovered)
        bridge.wheeled.connect(self._wheeled)
        bridge.label_clicked.connect(self._label_clicked)
        self._toolbar()
        self._dock()
        self._asking = None
        self.timer = QTimer(self)
        self.timer.setInterval(800)
        self.timer.timeout.connect(self._poll)
        self.refresh()

    def dragEnterEvent(self, event) -> None:      # noqa: N802 -- Qt's name
        urls = event.mimeData().urls()
        if urls and urls[0].toLocalFile().lower().endswith(web.Hub.DROPPABLE):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:           # noqa: N802 -- Qt's name
        self.open(event.mimeData().urls()[0].toLocalFile())

    def closeEvent(self, event) -> None:          # noqa: N802 -- Qt's name
        if self.doc and not self.doc.get("saved", True) and self.isVisible() \
                and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            answer = QMessageBox.question(self, "RealParts", "The document has unsaved changes. Close anyway?",
                                          QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self.view.release()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:       # noqa: N802 -- Qt's name
        if self.drag is not None:
            key = {Qt.Key_Escape: "escape", Qt.Key_Return: "enter", Qt.Key_Enter: "enter",
                   Qt.Key_Backspace: "\b"}.get(event.key(), event.text())
            if self.drag.key(key):
                return
        if self.tool and event.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            self._end_tool()
            return
        super().keyPressEvent(event)

    # -- talking to the kernel -------------------------------------------------
    def op(self, operation: str, **args):
        reply = self.hub.call(dict(args, op=operation))
        if not reply.get("ok"):
            raise web.CadError(reply.get("kind", "error"), reply.get("message", ""), reply.get("detail"))
        return reply.get("result")

    def try_op(self, operation: str, **args):
        try:
            out = self.op(operation, **args)
        except web.CadError as exc:
            self.say("%s: %s" % (exc.kind, exc.message), error=True)
            return None
        if operation in QUERIES:
            return out
        if isinstance(out, dict) and ("face_names" in out or "faces" in out):
            self.shown(out)
        else:
            self.refresh()
        return out

    def say(self, text: str, error: bool = False) -> None:
        self.statusBar().showMessage(("! " if error else "") + text)

    def refresh(self) -> None:
        try:
            self.doc = self.op("describe_document")
        except web.CadError:
            self.doc = None
        info = None
        if self.doc:
            try:
                info = self.op("build")
            except web.CadError as exc:
                self.say("%s: %s" % (exc.kind, exc.message), error=True)
        self.shown(info)

    def shown(self, info) -> None:
        self.built = info
        try:
            self.doc = self.op("describe_document")
        except web.CadError:
            self.doc = None
        if info and info.get("faces"):
            try:
                self.view.set_mesh(self.op("tessellate", deflection=0.15))
                self.shapes = {f["name"]: f.get("shape") for f in self.op("describe_faces")["faces"]}
            except web.CadError as exc:
                self.say("%s: %s" % (exc.kind, exc.message), error=True)
            free = info.get("freedom")
            self.say("%d faces, %d mm3%s" % (info["faces"], round(info["volume_mm3"]),
                                             ", %d free" % free["dof"] if free else ""))
        elif self.doc:
            empty = {"vertices": [], "normals": [], "triangles": [], "triangle_face": [],
                     "face_table": [], "edges": {}}
            try:                        # a sketch or a work plane is drawn as lines before there is a solid
                self.view.set_mesh(dict(empty, **self.op("tessellate", deflection=0.12)))
            except web.CadError:
                self.view.set_mesh(empty)
            self.shapes = {}
            self.say((info or {}).get("hint") or "nothing built yet")
        self.picked = [n for n in self.picked if n in self.shapes]
        self.picked_edges = [e for e in self.picked_edges if e in self.view.edges]
        self.view.picked_edges = list(self.picked_edges)
        self.view.set_picked(self.picked)
        self._choose_feature()
        self._draw_pick_line()
        self._draw_history()
        self._draw_parameters()
        self._draw_parts()
        self._draw_marks()
        self._draw_labels()
        self.docname.setText(self._name())

    def _name(self) -> str:
        if not self.doc:
            return "no document"
        path = self.doc.get("path")
        name = path.replace("\\", "/").split("/")[-1] if path else "new document"
        if not self.doc.get("saved", True):
            name += " •"
        if self.built and self.built.get("faces"):
            name += "  ·  %d faces" % self.built["faces"]
        return name

    def _choose_feature(self) -> None:
        """The picked feature: what made the picked face or edge, else what it was."""
        if self.picked:
            self.feature = feature_of(self.picked[0])
        elif self.picked_edges:
            self.feature = feature_of(self.picked_edges[0])
        elif self.doc and not any(f["id"] == self.feature for f in self.doc["features"]):
            self.feature = self.doc["features"][-1]["id"] if self.doc["features"] else None

    # -- the bar -----------------------------------------------------------------
    def _toolbar(self) -> None:
        bar = QToolBar("RealParts")
        bar.setMovable(False)
        self.addToolBar(bar)
        self.examples = QComboBox()
        self.examples.addItem("Open an example…", "")
        for example in web.examples():
            self.examples.addItem(example["name"], example["path"])
        self.examples.currentIndexChanged.connect(       # an example is a starting point: a copy
            lambda _: self.open(self.examples.currentData(), as_copy=True) if self.examples.currentData() else None)
        bar.addWidget(self.examples)
        self.docname = QLabel("")
        self.docname.setObjectName("docname")
        bar.addWidget(self.docname)
        for label, slot, keys in (("Open…", self._open_dialog, QKeySequence.Open),
                                  ("New", self.new, QKeySequence.New),
                                  ("Save", self.save, QKeySequence.Save),
                                  ("Undo", lambda: self.try_op("undo"), QKeySequence.Undo),
                                  ("Redo", lambda: self.try_op("redo"), QKeySequence.Redo)):
            action = QAction(label, self)
            action.triggered.connect(slot)
            action.setShortcut(keys)
            bar.addAction(action)

    def open(self, path: str, as_copy: bool = False) -> None:
        self.view.framed = False
        self.picked, self.picked_edges = [], []
        self.try_op("open", path=path, as_copy=as_copy)

    def _open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open", "", "Documents (*.json *.step *.stp *.iges *.igs *.brep *.stl *.obj *.3mf *.gltf *.glb)")
        if path:
            self.open(path)

    def new(self) -> None:
        self.view.framed = False
        self.picked, self.picked_edges = [], []
        self.try_op("new_document")

    def save(self) -> None:
        path = self.doc.get("path") if self.doc else None
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save as", "part.json", "Documents (*.json)")
        if path:
            out = self.try_op("save", path=path)
            if out:
                self.say("saved %s" % (out.get("path") or path))

    def create_sketch(self) -> None:
        if not self.doc:
            self.new()
        if not self.doc:
            return
        taken = {f["id"] for f in self.doc["features"]}
        number = 1
        while f"plane{number}" in taken or f"sketch{number}" in taken:
            number += 1
        plane, sketch = f"plane{number}", f"sketch{number}"
        self.view.framed = False
        self.picked, self.picked_edges = [], []
        self.feature = sketch
        self.try_op("apply", ops=[
            {"op": "add_plane", "on": self.sketch_plane.currentData(), "feature_id": plane},
            {"op": "add_profile", "plane": plane, "feature_id": sketch,
             "shape": self.sketch_shape.currentData(), "width": self.sketch_width.value(),
             "height": self.sketch_height.value(), "radius": self.sketch_radius.value()}])

    def extrude_sketch(self) -> None:
        chosen = next((f for f in (self.doc or {}).get("features", [])
                       if f["id"] == self.feature and f["type"] == "sketch"), None)
        if not chosen:
            self.say("Select a sketch in History, then extrude it.", error=True)
            return
        self.view.framed = False
        self.feature = None
        self.try_op("add_feature", type="extrude",
                    args={"sketch": chosen["id"], "distance": self.sketch_depth.value()})

    # -- the sidebar: the document -----------------------------------------------------
    def _dock(self) -> None:
        dock = QDockWidget("", self)
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        dock.setTitleBarWidget(QWidget())
        dock.setMinimumWidth(360)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        dock.setWidget(scroll)
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        scroll.setWidget(column)

        self.pick_line = QLabel("Click a face or an edge to pick it; Shift adds. Right-click for what fits it.")
        self.pick_line.setObjectName("pick")
        self.pick_line.setWordWrap(True)
        layout.addWidget(self.pick_line)

        create = Section("Create Sketch")
        self.sketch_plane = QComboBox()
        for label, plane in (("XY (top)", "xy"), ("XZ (front)", "xz"), ("YZ (side)", "yz")):
            self.sketch_plane.addItem(label, plane)
        create.inner.addWidget(self.sketch_plane)
        self.sketch_shape = QComboBox()
        self.sketch_shape.addItem("Rectangle", "rect")
        self.sketch_shape.addItem("Circle", "circle")
        create.inner.addWidget(self.sketch_shape)
        for attr, label, value in (("width", "Width", 40), ("height", "Height", 30),
                                   ("radius", "Radius", 15), ("depth", "Extrusion", 10)):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            field = QDoubleSpinBox()
            field.setRange(0.01, 100000)
            field.setValue(value)
            field.setSuffix(" mm")
            setattr(self, "sketch_" + attr, field)
            row.addWidget(field)
            create.inner.addLayout(row)
        for label, slot in (("Create Sketch", self.create_sketch), ("Extrude Sketch", self.extrude_sketch)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            create.inner.addWidget(button)
            if label == "Extrude Sketch":
                self.extrude_button = button
        self.sketch_shape.currentIndexChanged.connect(self._sketch_fields)
        self._sketch_fields()
        create.inner.addWidget(QLabel("Choose a plane → create a profile → extrude."))
        layout.addWidget(create)

        self.history = Section("History")
        self.features = QListWidget()
        self.features.itemChanged.connect(self._feature_toggled)
        self.features.itemClicked.connect(self._feature_clicked)
        self.history.inner.addWidget(self.features)
        row = QHBoxLayout()
        self.rollback = QPushButton("Roll Back Here")
        self.rollback.clicked.connect(self._roll_back)
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(lambda: self.feature and self.try_op("remove_feature", feature_id=self.feature))
        row.addWidget(self.rollback)
        row.addWidget(self.remove)
        self.history.inner.addLayout(row)
        self.numbers = QWidget()
        self.numbers.setObjectName("numbers")
        self.numbers_layout = QVBoxLayout(self.numbers)
        self.history.inner.addWidget(self.numbers)
        layout.addWidget(self.history)

        self.parameters = Section("Parameters")
        self.params = QTableWidget(0, 2)
        self.params.setHorizontalHeaderLabels(["parameter", "value"])
        self.params.horizontalHeader().setStretchLastSection(True)
        self.params.verticalHeader().setVisible(False)
        self.params.cellChanged.connect(self._parameter_typed)
        self.parameters.inner.addWidget(self.params)
        meta = QHBoxLayout()
        self.material = QLineEdit()
        self.material.setPlaceholderText("material, e.g. A6061")
        self.material.setToolTip("what it is made of: the bill of materials, its mass and STEP use it")
        self.material.editingFinished.connect(lambda: self._meta_typed("material", self.material))
        self.colour = QLineEdit()
        self.colour.setPlaceholderText("colour #rrggbb")
        self.colour.setToolTip("written to STEP")
        self.colour.editingFinished.connect(lambda: self._meta_typed("colour", self.colour))
        meta.addWidget(self.material)
        meta.addWidget(self.colour)
        self.parameters.inner.addLayout(meta)
        layout.addWidget(self.parameters)

        self.parts = Section("Parts", open_=False)
        self.parts_layout = self.parts.inner
        layout.addWidget(self.parts)

        self.ask_section = Section("Ask", open_=False)
        self.question = QTextEdit()
        self.question.setMaximumHeight(64)
        self.question.setPlaceholderText('e.g. "M4 tapped hole here, 8 deep" -- the pick goes with it')
        self.ask_section.inner.addWidget(self.question)
        row = QHBoxLayout()
        send = QPushButton("Ask")
        send.setObjectName("primary")
        send.clicked.connect(self.ask)
        stop = QPushButton("Stop")
        stop.clicked.connect(lambda: self.hub.ask and self.hub.ask.stop())
        row.addWidget(send)
        row.addWidget(stop)
        self.ask_section.inner.addLayout(row)
        self.replies = QTextBrowser()
        self.replies.setMinimumHeight(120)
        self.ask_section.inner.addWidget(self.replies)
        layout.addWidget(self.ask_section)

        self.output = Section("Checks and Output", open_=False)
        row = QHBoxLayout()
        for label, slot in (("Check the Design", self._check), ("Draft Check", self._draft_check),
                            ("Mass", self._mass)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        self.output.inner.addLayout(row)
        row = QHBoxLayout()
        for label, slot in (("STEP", self._step), ("Drawing", self._drawing)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        self.output.inner.addLayout(row)
        self.report = QTextBrowser()
        self.report.setMinimumHeight(100)
        self.output.inner.addWidget(self.report)
        layout.addWidget(self.output)
        layout.addStretch()

    def _sketch_fields(self) -> None:
        rectangle = self.sketch_shape.currentData() == "rect"
        self.sketch_width.setEnabled(rectangle)
        self.sketch_height.setEnabled(rectangle)
        self.sketch_radius.setEnabled(not rectangle)

    def _draw_pick_line(self) -> None:
        if self.picked_edges:
            head = "This edge" if len(self.picked_edges) == 1 else "These %d edges" % len(self.picked_edges)
            text = "%s -- drag the green disc to round, the orange corner to chamfer" % ", ".join(self.picked_edges)
        elif len(self.picked) == 1:
            flat = self.shapes.get(self.picked[0]) == "plane"
            text = "%s -- %s" % (self.picked[0], "drag the arrow to push or pull; right-click for more"
                                 if flat else "a round face: right-click for more")
        elif self.picked:
            text = ", ".join(self.picked)
        else:
            text = ("Choose a plane and create a sketch below."
                    if not self.doc or not self.doc["features"] else
                    "Click a face or an edge to pick it; Shift adds. Right-click for what fits it.")
        self.pick_line.setText(text)
        self.pick_line.setProperty("on", bool(self.picked or self.picked_edges))
        self.pick_line.style().unpolish(self.pick_line)
        self.pick_line.style().polish(self.pick_line)

    def _draw_history(self) -> None:
        self.features.blockSignals(True)
        self.features.clear()
        doc = self.doc or {"features": []}
        rolled = doc.get("rolled_back_to")
        later = False
        for f in doc["features"]:
            item = QListWidgetItem("%s   %s%s" % (f["id"], f["type"], "  ·  " + f["summary"] if f.get("summary") else ""))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked if f["suppressed"] else Qt.Checked)
            item.setData(Qt.UserRole, f["id"])
            item.setToolTip(json.dumps(f["args"]))
            if later:
                item.setForeground(Qt.gray)
            if f["id"] == rolled:
                later = True
            self.features.addItem(item)
            if f["id"] == self.feature:
                item.setSelected(True)
        self.features.blockSignals(False)
        self.rollback.setEnabled(bool(self.feature))
        self.rollback.setText("Build It All Again" if rolled and rolled == self.feature else "Roll Back Here")
        self.remove.setEnabled(bool(self.feature))
        while self.numbers_layout.count():
            item = self.numbers_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        chosen = next((f for f in doc["features"] if f["id"] == self.feature), None)
        self.extrude_button.setEnabled(bool(chosen and chosen["type"] == "sketch"))
        for key, label, value in self._numbers(chosen):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            field = QLineEdit(str(value))
            field.setAlignment(Qt.AlignRight)
            field.setMaximumWidth(100)
            field.editingFinished.connect(lambda field=field, key=key, chosen=chosen: self._number_typed(chosen, key, field.text()))
            row.addWidget(field)
            self.numbers_layout.addLayout(row)
        self.numbers.setVisible(bool(chosen) and self.numbers_layout.count() > 0)

    def _numbers(self, feature: dict | None) -> list:
        """(key, label, value) for the numbers a feature is made of: plain, or a parameter by name."""
        if not feature or not self.doc:
            return []
        out = []
        for key, v in feature["args"].items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out.append((key, key, v))
            elif isinstance(v, str) and v in self.doc["parameters"]:
                out.append((key, v, self.doc["parameters"][v]))
        return out

    def _number_typed(self, feature: dict, key: str, text: str) -> None:
        try:
            value = float(text)
        except ValueError:
            return
        was = feature["args"][key]
        if isinstance(was, str):
            self.try_op("set_parameter", name=was, value=value)
        else:
            self.try_op("edit_feature", feature_id=feature["id"], args={key: value})

    def _feature_clicked(self, item: QListWidgetItem) -> None:
        self.feature = item.data(Qt.UserRole)
        self._draw_history()
        self._draw_labels()

    def _feature_toggled(self, item: QListWidgetItem) -> None:
        self.try_op("suppress_feature", feature_id=item.data(Qt.UserRole),
                    suppressed=item.checkState() != Qt.Checked)

    def _roll_back(self) -> None:
        if not self.feature:
            return
        rolled = (self.doc or {}).get("rolled_back_to")
        self.try_op("rollback", **({} if rolled == self.feature else {"feature_id": self.feature}))

    def _draw_parameters(self) -> None:
        self.params.blockSignals(True)
        self.params.setRowCount(0)
        if self.doc:
            for name, value in self.doc["parameters"].items():
                row = self.params.rowCount()
                self.params.insertRow(row)
                key = QTableWidgetItem(name)
                key.setFlags(key.flags() & ~Qt.ItemIsEditable)
                key.setToolTip(" … ".join(str(b) for b in self.doc["parameters_bounds"].get(name, [])))
                self.params.setItem(row, 0, key)
                self.params.setItem(row, 1, QTableWidgetItem(str(value)))
        self.params.blockSignals(False)
        self.params.setMaximumHeight(36 + 30 * max(1, self.params.rowCount()))
        meta = (self.doc or {}).get("meta") or {}
        for key, field in (("material", self.material), ("colour", self.colour)):
            if field.text() != (meta.get(key) or ""):
                field.setText(meta.get(key) or "")

    def _meta_typed(self, key: str, field) -> None:
        meta = (self.doc or {}).get("meta") or {}
        if self.doc and field.text().strip() != (meta.get(key) or ""):
            self.try_op("set_meta", **{key: field.text().strip()})

    def _parameter_typed(self, row: int, column: int) -> None:
        if column != 1:
            return
        name = self.params.item(row, 0).text()
        text = self.params.item(row, 1).text().strip()
        try:
            value = float(text)
        except ValueError:
            value = text
        self.try_op("set_parameter", name=name, value=value)

    # -- the pick ------------------------------------------------------------------------
    def _picked(self, names: list) -> None:
        self.picked = names
        self.picked_edges = list(self.view.picked_edges)
        self._end_tool()
        self.section = None
        self._after_pick()

    def _edges_picked(self, names: list) -> None:
        self.picked_edges = names
        self.picked = list(self.view.picked)
        self._end_tool()
        self._after_pick()

    def _after_pick(self) -> None:
        self._choose_feature()
        self._draw_pick_line()
        self._draw_history()
        self._draw_marks()
        self._draw_labels()

    def _assemble(self) -> dict | None:
        return next((f for f in (self.doc or {}).get("features", [])
                     if f["type"] == "assemble" and f["args"].get("mates")), None)

    def _extent(self) -> float:
        return float(self.view.bridge.distance) / 1.5

    def _draw_marks(self) -> None:
        """The marks on the pick: the arrow and plane mark on a flat face, the grips on edges."""
        marks, lines = [], []
        self.frame = None
        size = self._extent()
        r = size * 0.024
        one = self.picked[0] if len(self.picked) == 1 and not self.picked_edges else None
        if self.tool:                     # a tool owns the face: only its ring is drawn
            one = None
        if one and self.shapes.get(one) == "plane":
            try:
                self.frame = self.op("face_frame", face=one)
            except web.CadError:
                self.frame = None
        if self.frame:
            o, n, x = self.frame["origin"], self.frame["normal"], self.frame["x_axis"]
            reach = size * 0.22
            tip = [o[i] + n[i] * reach for i in range(3)]
            marks.append(("push", "sphere", tip, YELLOW, r))
            lines.append([o, tip])
            y = [n[1] * x[2] - n[2] * x[1], n[2] * x[0] - n[0] * x[2], n[0] * x[1] - n[1] * x[0]]
            at = [o[i] + x[i] * reach * 0.55 + n[i] * size * 0.01 for i in range(3)]
            s = size * 0.05
            corner = lambda a, b: [at[i] + x[i] * a * s + y[i] * b * s for i in range(3)]   # noqa: E731
            lines.append([corner(-1, -1), corner(1, -1), corner(1, 1), corner(-1, 1), corner(-1, -1)])
            marks.append(("plane", "box", at, BLUE, r * 0.8))
        grip = self._grip_place(size) if not self.tool else None
        if grip is not None:
            at, lean = grip
            marks.append(("fillet", "disc", [at[i] + lean[i] * size * 0.09 for i in range(3)], GREEN, r))
            marks.append(("chamfer", "corner", [at[i] + lean[i] * size * 0.16 for i in range(3)], ORANGE, r))
        if self.section:
            lines.extend(curve["points"] for curve in self.section["curves"])
        ring = None
        if self.tool and self.tool.get("cursor") is not None:
            d = HOLE_STEPS[self.tool["index"]][0]
            u, v = self.tool["cursor"]
            n, lift = self.tool["frame"]["normal"], size * 0.004       # off the face, or the face hides it
            ring = [[q + n[i] * lift for i, q in enumerate(
                        to_3d((u + math.cos(a) * d / 2, v + math.sin(a) * d / 2), self.tool["frame"]))]
                    for a in [k / 48 * 2 * math.pi for k in range(49)]]
        self.view.set_marks(marks, lines, ring)

    def _grip_place(self, size: float):
        """Where the edge grips sit: off the first picked edge's middle, or beside a picked face."""
        if self.picked_edges:
            line = self.view.edges.get(self.picked_edges[0])
            if not line:
                return None
            mid = line[len(line) // 2]
            centre = self.view.bridge.centre
            c = [centre.x(), -centre.z(), centre.y()]                    # y-up back to z-up
            lean = [mid[0] - c[0], mid[1] - c[1], mid[2] - c[2] + size * 0.3]
            norm = math.sqrt(sum(q * q for q in lean)) or 1.0
            return mid, [q / norm for q in lean]
        if self.frame:
            x = self.frame["x_axis"]
            return self.frame["origin"], [-x[0], -x[1], -x[2]]
        return None

    def _draw_labels(self) -> None:
        """The picked feature's numbers, beside the faces it made or named."""
        chosen = next((f for f in (self.doc or {}).get("features", []) if f["id"] == self.feature), None)
        numbers = self._numbers(chosen)
        if not numbers or self.drag is not None:
            self.view.set_labels([], None)
            return
        wanted = {n for n in self.view.faces if feature_of(n) == chosen["id"]}
        if not wanted:
            for text in json.dumps(chosen["args"]).split('"'):
                for n in text.split("|"):
                    if n in self.view.faces:
                        wanted.add(n)
        at = self.view.centre_of(wanted)
        if at is None:
            self.view.set_labels([], None)
            return
        self.view.set_labels([(key, "%s  %s" % (label, _tidy(value))) for key, label, value in numbers], at)

    def _label_clicked(self, key: str) -> None:
        chosen = next((f for f in (self.doc or {}).get("features", []) if f["id"] == self.feature), None)
        if not chosen:
            return
        was = chosen["args"].get(key)
        current = self.doc["parameters"][was] if isinstance(was, str) else was
        value, ok = QInputDialog.getDouble(self, "Type over it", was if isinstance(was, str) else key,
                                           float(current), -1e6, 1e6, 3)
        if ok:
            self._number_typed(chosen, key, str(value))

    # -- the drags on the marks -------------------------------------------------------------
    def _drag_started(self, mark: str, x: float, y: float, text: str | None = None) -> None:
        if self.drag is not None or not self.doc:
            return
        try:
            if mark in ("push", "plane", "split") and self.frame:
                self._start_along_normal(mark, x, y)
            elif mark == "emboss" and self.frame:
                self._start_along_normal(mark, x, y, text)
            elif mark in ("fillet", "chamfer"):
                self._start_grip(mark, x, y)
            elif mark in ("draft", "coil", "emboss") and self.picked:
                self._start_sideways(mark, x, y, text)
        except web.CadError as exc:
            self.say("%s: %s" % (exc.kind, exc.message), error=True)

    def _start_sideways(self, kind: str, x: float, y: float, text: str | None) -> None:
        """A drag read along the screen's x: degrees of draft, a coil's pitch, words on a round face."""
        face = self.picked[0]
        centre = self.view.bridge.centre
        per_pixel = self.view.mm_per_pixel([centre.x(), -centre.z(), centre.y()])   # y-up back to z-up
        if kind == "draft":
            drag = LiveDrag(self, "Draft from %s" % face,
                            lambda v: ("add_draft", {"parting_face": face, "angle": round(v, 2)}),
                            lambda v: {"angle": round(v, 2)}, lambda v: "%.1f°" % v)
            self.say("Draft from %s: the walls lean away from its plane; drag right for more | type degrees | Enter keeps | Esc" % face)
            state = {"per_pixel": 1 / 12, "step": 0.5, "low": 0.0}
        elif kind == "coil":
            drag = LiveDrag(self, "Coil round %s" % face,
                            lambda v: ("add_coil", {"face": face, "pitch": round(v, 2)}),
                            lambda v, made: ("set_parameter", {"name": made + "_pitch", "value": round(v, 2)}),
                            lambda v: "pitch %.1f mm" % v)
            self.say("Coil round %s: drag right for a longer pitch | type a number | Enter keeps | Esc" % face)
            state = {"per_pixel": per_pixel, "step": 0.5, "low": 0.5}
        else:
            drag = LiveDrag(self, "%s round %s" % (text, face),
                            lambda v: ("add_emboss", {"face": face, "text": text, "depth": round(v, 3)}),
                            lambda v: {"depth": round(abs(v), 3), "cut": v < 0},
                            lambda v: "%s %.2f mm" % ("raised" if v >= 0 else "cut", abs(v)))
            self.say("%s round %s: drag right to raise, left to cut | type a number | Enter keeps | Esc" % (text, face))
            state = {"per_pixel": per_pixel, "step": 0.5, "low": None}
        self._drag_state = dict(state, kind=kind, start=(x, y))
        self._begin(drag)

    def _start_along_normal(self, mark: str, x: float, y: float, text: str | None = None) -> None:
        face, frame = self.picked[0], self.frame
        o, n = frame["origin"], frame["normal"]
        a, b = self.view.project(o), self.view.project([o[i] + n[i] * 10 for i in range(3)])
        if a is None or b is None or math.dist(a, b) < 1e-3:
            self.say("the face is edge-on; orbit a little and try again", error=True)
            return
        length = math.dist(a, b)
        axis = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
        snapper = Snapper(self.op("describe_faces")["faces"], frame, face) if mark == "push" else None
        step = 0.5
        if mark == "push":
            drag = LiveDrag(self, "Push/Pull %s" % face,
                            lambda v: ("move_face", {"face": face, "distance": round(v, 3)}),
                            lambda v: {"distance": round(v, 3)}, lambda v: "%+.2f mm" % v)
            self.say("Push/Pull %s: drag along the normal | Ctrl fine | type a number | Enter keeps | Esc" % face)
        elif mark == "plane":
            drag = LiveDrag(self, "Plane off %s" % face,
                            lambda v: ("add_plane", {"face": face, "offset": round(v, 3)}),
                            lambda v: {"offset": round(v, 3)})
            self.say("Work plane off %s: drag it away from the face | Enter keeps | Esc" % face)
            step = 1.0
        elif mark == "split":
            drag = LiveDrag(self, "Split off %s" % face,
                            lambda v: ("add_split", {"face": face, "offset": round(v, 3), "keep": "below"}),
                            lambda v, made: ("set_parameter", {"name": made + "_offset", "value": round(v, 3)}),
                            lambda v: "%+.2f mm" % v)
            self.say("Split off %s: drag the cut into the part; what is under it stays | type a number | Enter keeps | Esc" % face)
            step = 1.0
        else:
            drag = LiveDrag(self, "%s on %s" % (text, face),
                            lambda v: ("add_emboss", {"face": face, "text": text, "depth": round(v, 3)}),
                            lambda v: {"depth": round(abs(v), 3), "cut": v < 0},
                            lambda v: "%s %.2f mm" % ("raised" if v >= 0 else "cut", abs(v)))
            self.say("%s on %s: drag out to raise, in to cut | type a number | Enter keeps | Esc" % (text, face))
        self._drag_state = {"kind": mark, "start": (x, y), "axis": axis, "per_pixel": 10.0 / length,
                            "snapper": snapper, "step": step}
        self._begin(drag)

    def _start_grip(self, kind: str, x: float, y: float) -> None:
        edges = list(self.picked_edges)
        if not edges and self.picked:
            edges = self.op("select_edges", query={"of_face": self.picked[0]})["edges"]
        if not edges:
            return
        grip = self._grip_place(self._extent())
        per_pixel = self.view.mm_per_pixel(grip[0]) if grip else 0.1
        drag = LiveDrag(self, "%s %d edge(s)" % (kind, len(edges)),
                        lambda v: ("add_fillet", {"edges": edges, "radius": round(v, 3), "kind": kind}),
                        lambda v: {("radius" if kind == "fillet" else "distance"): round(v, 3)},
                        lambda v: "%.1f mm" % v)
        self.say("%s %d edge(s): drag right for more | type a number | Enter keeps | Esc"
                 % ("Round" if kind == "fillet" else "Chamfer", len(edges)))
        self._drag_state = {"kind": kind, "start": (x, y), "per_pixel": per_pixel}
        self._begin(drag)

    def _begin(self, drag: LiveDrag) -> None:
        self.drag = drag.begin()
        drag.changed.connect(lambda: self.view.bridge.set_badge(drag.text, drag.is_refused))
        drag.ended.connect(self._drag_over)
        self.view.set_labels([], None)

    def _drag_moved(self, x: float, y: float, ctrl: bool) -> None:
        drag, state = self.drag, self._drag_state
        if drag is None or drag.typed:
            return
        sx, sy = state["start"]
        if "axis" in state:
            d = ((x - sx) * state["axis"][0] + (y - sy) * state["axis"][1]) * state["per_pixel"]
            d = snap(d, 0.1 if ctrl else state["step"])
            flush = None
            if state["snapper"] and not ctrl:
                d, flush = state["snapper"].distance(d, state["per_pixel"])
            if abs(d) < 1e-6:
                return
            drag.preview(d)
            if flush:
                drag.say("%+.2f mm, flush with %s" % (d, flush))
        else:
            value = snap((x - sx) * state["per_pixel"], 0.1 if ctrl else state.get("step", 0.5))
            low = state.get("low", 0.0)
            if low is not None:
                value = max(low, value)
                if value <= 0:
                    return
            elif abs(value) < 1e-6:
                return
            drag.preview(value)

    def _drag_ended(self) -> None:
        if self.drag is not None:
            self.drag.finish()

    def _drag_over(self) -> None:
        self.drag = None
        self._drag_state = {}
        self.view.bridge.set_badge("")
        self.view.bridge.set_following("")
        self._draw_labels()

    def start_from_menu(self, kind: str, x: float, y: float, text: str | None = None) -> None:
        """A drag the menu starts: the mouse is followed until a click."""
        self._drag_started(kind, x, y, text)
        if self.drag is not None:
            self.view.bridge.set_following(kind)

    # -- the hole tool ------------------------------------------------------------------------
    def start_hole_tool(self, face: str) -> None:
        if self.explode.value():
            self.say("put the parts back together first: a hole goes where the part is, not where it is shown", error=True)
            return
        try:
            frame = self.op("face_frame", face=face)
            faces = self.op("describe_faces")["faces"]
        except web.CadError as exc:
            self.say("%s: %s" % (exc.kind, exc.message), error=True)
            return
        self.tool = {"kind": "hole", "face": face, "frame": frame, "index": 3, "cursor": None, "placed": 0,
                     "snapper": Snapper(faces, frame, face)}
        self.view.bridge.set_tool("hole")
        self._draw_marks()
        self._say_hole()

    def start_section_tool(self, face: str) -> None:
        """A plane parallel to the face follows the mouse into the part; the cut is drawn."""
        try:
            frame = self.op("face_frame", face=face)
        except web.CadError as exc:
            self.say("%s: %s" % (exc.kind, exc.message), error=True)
            return
        o, n = frame["origin"], frame["normal"]
        a, b = self.view.project(o), self.view.project([o[i] + n[i] * 10 for i in range(3)])
        if a is None or b is None or math.dist(a, b) < 1e-3:
            self.say("the face is edge-on; orbit a little and try again", error=True)
            return
        length = math.dist(a, b)
        self.tool = {"kind": "section", "face": face, "at": a, "per_pixel": 10.0 / length,
                     "axis": ((b[0] - a[0]) / length, (b[1] - a[1]) / length), "placed": 0}
        self.view.bridge.set_tool("section")
        self._draw_marks()
        self.say("Section off %s: move the mouse to slide the plane into the part | click to leave it | Esc" % face)

    def _say_hole(self) -> None:
        d, name = HOLE_STEPS[self.tool["index"]]
        self.say("Hole on %s: click to drill %s clearance (%.1f mm) | wheel: size | %d placed | Esc to finish"
                 % (self.tool["face"], name, d, self.tool["placed"]))

    def _end_tool(self) -> None:
        if self.tool:
            kind, placed = self.tool["kind"], self.tool["placed"]
            self.tool = None
            self.view.bridge.set_tool("")
            self._draw_marks()
            if kind == "hole":
                self.say("%d hole(s) placed" % placed)
            elif self.section:
                self.say("section %.1f mm², outline %.1f mm" % (self.section["area_mm2"], self.section["length_mm"]))

    def _hovered(self, x: float, y: float, click: bool) -> None:
        if not self.tool:
            return
        if self.tool["kind"] == "section":
            ax, ay = self.tool["axis"]
            d = ((x - self.tool["at"][0]) * ax + (y - self.tool["at"][1]) * ay) * self.tool["per_pixel"]
            offset = round(min(0.0, d), 1)
            try:
                self.section = self.op("section", face=self.tool["face"], offset=offset)
            except web.CadError:
                self.section = None
            self._draw_marks()
            if click:
                self._end_tool()
            return
        hit = self.view.hit_at(int(x), int(y))
        if hit is None or hit[0] != self.tool["face"]:
            self.tool["cursor"] = None
            self._draw_marks()
            return
        uv = to_uv(hit[1], self.tool["frame"])
        uv, label = self.tool["snapper"].uv(uv, self.view.mm_per_pixel(hit[1]))
        self.tool["cursor"] = uv
        self._draw_marks()
        if click:
            d, name = HOLE_STEPS[self.tool["index"]]
            out = self.try_op("add_hole", face=self.tool["face"], standard=name, fit="normal",
                              at=[round(uv[0], 2), round(uv[1], 2)])
            if out and self.tool:
                self.tool["placed"] += 1
                self._say_hole()

    def _wheeled(self, delta: int) -> None:
        if self.tool:
            self.tool["index"] = (self.tool["index"] + (1 if delta > 0 else -1)) % len(HOLE_STEPS)
            self._say_hole()
            self._draw_marks()

    # -- the right-click: what fits the pick, in the add-on's words ------------------------------
    def _menu(self, at) -> None:
        items = self.menu_items(at.x(), at.y())
        if not items:
            return
        menu = QMenu(self)
        head = menu.addAction(self._head())
        head.setEnabled(False)
        for label, run in items:
            menu.addAction(label, run)
        menu.exec(at)

    def _head(self) -> str:
        if self.picked_edges:
            return "This edge" if len(self.picked_edges) == 1 else "These %d edges" % len(self.picked_edges)
        return "This face" if len(self.picked) == 1 else "These %d faces" % len(self.picked)

    def menu_items(self, gx: float = 0, gy: float = 0) -> list:
        """(label, run) for what the pick takes, in the add-on's words."""
        out = []
        if not self.doc:
            return out
        local = self.view.mapFromGlobal(QPoint(int(gx), int(gy)))
        x, y = local.x(), local.y()
        start = lambda kind: (lambda: self.start_from_menu(kind, x, y))     # noqa: E731
        if self.picked_edges:
            one = len(self.picked_edges) == 1
            out.append(("Round It" if one else "Round Them", start("fillet")))
            out.append(("Chamfer It" if one else "Chamfer Them", start("chamfer")))
        elif len(self.picked) == 1:
            face = self.picked[0]
            words = lambda: self._words_then("emboss", x, y)                   # noqa: E731
            if self.shapes.get(face) == "plane":
                out.append(("Push / Pull", start("push")))
                out.append(("Hole", lambda: self.start_hole_tool(face)))
                out.append(("Plane Off It", start("plane")))
                out.append(("Split Here", start("split")))
                out.append(("Section Here", lambda: self.start_section_tool(face)))
                out.append(("Emboss Text…", words))
                out.append(("Draft From Here", start("draft")))
            elif self.shapes.get(face) == "cylinder":
                out.append(("Wrap Text Round It", words))
                out.append(("Coil Round It", start("coil")))
            out.append(("Round Its Edges", start("fillet")))
            out.append(("Chamfer Its Edges", start("chamfer")))
        elif len(self.picked) == 2:
            round_ = lambda n: self.shapes.get(n) in ("cylinder", "cone")         # noqa: E731
            faces = list(self.picked)
            if len({body_of(n) for n in self.picked}) == 2:
                kind = "concentric" if all(round_(n) for n in self.picked) else \
                    "fastened" if all(self.shapes.get(n) == "plane" for n in self.picked) else "tangent"
                out.append(("Mate: Put the First Against the Second (%s)" % kind,
                            lambda: self.try_op("add_mate", faces=faces, kind=kind,
                                                offset=None if kind == "concentric" else 0, flip=True)))
                out.append(("Mate As…", lambda: self._mate_as(faces, kind)))
            out.append(("Distance Between", lambda: self._distance(faces)))
        asm = self._assemble()
        first = (self.picked or self.picked_edges or [None])[0]
        if first and asm and body_of(first) != (asm["args"].get("ground") or asm["args"]["bodies"][0]):
            out.append(("Drive This Part", lambda: self.drive(body_of(first), 30.0)))
        if self.picked or self.picked_edges:
            out.append(("Ask About This…", self._ask_about))
        return out

    def _ask_about(self) -> None:
        self.ask_section.open()
        self.question.setFocus()

    def _words_then(self, kind: str, x: float, y: float) -> None:
        text, ok = QInputDialog.getText(self, "The words", "Words to press onto the face", text="TEXT")
        if ok and text.strip():
            self.start_from_menu(kind, x, y, text.strip())

    def _mate_as(self, faces: list, guess: str) -> None:
        from cadcore.geometry.assembly.assembly import MATES
        kind, ok = QInputDialog.getItem(self, "Mate As", "kind", list(MATES), list(MATES).index(guess), False)
        if ok:
            self.try_op("add_mate", faces=faces, kind=kind,
                        offset=None if kind in ("concentric", "belt") else 0, flip=True)

    def _distance(self, faces: list) -> None:
        out = self.try_op("measure", kind="distance", faces=faces)
        if out:
            self.say("%s to %s: %.3f mm" % (faces[0], faces[1], out["value"]))

    # -- assemblies -------------------------------------------------------------------
    def drive(self, part: str, turn: float) -> None:
        try:
            out = self.op("drive", part=part, turn=turn, frames=1)
        except web.CadError as exc:
            self.say("%s: %s" % (exc.kind, exc.message), error=True)
            return
        for fid, pose in out["frames"][-1].items():
            relative = pose_matrix(pose) * pose_matrix(out["rest"][fid]).inverted()[0]
            self.view.set_part_matrix(out["scopes"].get(fid, fid), relative)
        self.driven = (part, turn)
        if hasattr(self, "keep"):
            self.keep.setEnabled(True)
        self.say("%s turned %g deg; Keep in Parts writes it into the document" % (part, turn))

    def _draw_parts(self) -> None:
        layout = self.parts_layout

        def drop(item) -> None:
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                while item.layout().count():
                    drop(item.layout().takeAt(0))

        while layout.count():
            drop(layout.takeAt(0))
        assembly = bool(self.doc) and len(self.view.parts) > 1
        self.parts.setVisible(assembly)
        if not assembly:
            return
        row = QHBoxLayout()
        row.addWidget(QLabel("Take apart"))
        self.explode = QSlider(Qt.Horizontal)
        self.explode.setRange(0, 30)
        self.explode.valueChanged.connect(self._exploded)
        row.addWidget(self.explode)
        layout.addLayout(row)
        asm = self._assemble()
        row = QHBoxLayout()
        row.addWidget(QLabel("Drive"))
        self.drive_part = QComboBox()
        if asm:
            ground = asm["args"].get("ground") or asm["args"]["bodies"][0]
            self.drive_part.addItems([b for b in asm["args"]["bodies"] if b != ground])
        else:
            self.drive_part.setEnabled(False)
            self.drive_part.setToolTip("mates solved together (an assemble with mates) leave a part free to drive")
        self.drive_turn = QSlider(Qt.Horizontal)
        self.drive_turn.setRange(-180, 180)
        self.drive_turn.setEnabled(bool(asm))
        self.drive_turn.sliderReleased.connect(self._drive_slid)
        self.keep = QPushButton("Keep")
        self.keep.setEnabled(self.driven is not None)
        self.keep.clicked.connect(self._keep_drive)
        for widget in (self.drive_part, self.drive_turn, self.keep):
            row.addWidget(widget)
        layout.addLayout(row)
        row = QHBoxLayout()
        for label, slot in (("Collisions", self._interference), ("Bill of Materials", self._bom)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        self.assembly_out = QTextBrowser()
        self.assembly_out.setMinimumHeight(90)
        layout.addWidget(self.assembly_out)

    def _exploded(self, value: int) -> None:
        factor = value / 10.0
        self.view.reset_parts()
        if factor > 0:
            out = self.try_op("explode", factor=factor)
            if out:
                for fid, offset in out["offsets"].items():
                    matrix = QMatrix4x4()
                    matrix.translate(*offset)
                    self.view.set_part_matrix(out["scopes"].get(fid, fid), matrix)

    def _drive_slid(self) -> None:
        turn = self.drive_turn.value()
        if turn:
            self.drive(self.drive_part.currentText(), float(turn))
        else:
            self.view.reset_parts()

    def _keep_drive(self) -> None:
        asm = self._assemble()
        if not asm or not self.driven:
            return
        part, turn = self.driven
        drives = [d for d in (asm["args"].get("drive") or []) if d.get("part") != part] + [{"part": part, "turn": turn}]
        self.driven = None
        self.view.reset_parts()
        self.try_op("edit_feature", feature_id=asm["id"], args={"drive": drives})

    def _interference(self) -> None:
        out = self.try_op("interference")
        if out:
            self.assembly_out.setHtml("no interference between %s" % ", ".join(out["parts"]) if out["clear"] else
                                      "<br>".join("%s and %s share %s mm3" % (c["parts"][0], c["parts"][1], c["volume_mm3"])
                                                  for c in out["interferences"]))

    def _bom(self) -> None:
        out = self.try_op("bill_of_materials")
        if not out:
            return

        def rows(lines, depth):
            return "".join("<tr><td>%s%s</td><td>%s</td><td>%s</td><td>%s</td></tr>%s" % (
                "&nbsp;&nbsp;" * depth, line.get("standard") or line["part"], line["quantity"],
                line.get("material") or "", "" if line.get("mass_g") is None else "%.1f" % line["mass_g"],
                rows(line.get("parts") or [], depth + 1)) for line in lines)
        self.assembly_out.setHtml("<table><tr><td>part</td><td>qty</td><td>material</td><td>g</td></tr>%s</table>%s"
                                  % (rows(out["parts"], 0), "total %s g" % out["total_mass_g"] if out.get("total_mass_g") else ""))

    # -- checks and output -------------------------------------------------------------------
    def _check(self) -> None:
        out = self.try_op("check", printing=True)
        if not out:
            return
        self.report.setHtml("".join(
            '<div style="color:%s">%s %s: %s</div>' % (
                "#7fd97f" if c["ok"] else ("#9aa0a8" if c["severity"] == "advice" else "#ff7070"),
                "✓" if c["ok"] else "✗", c["check"], c["said"]) for c in out["checks"]))

    def _draft_check(self) -> None:
        """Pulled out along the picked flat face's normal, else straight up."""
        direction = [0, 0, 1]
        if len(self.picked) == 1 and self.shapes.get(self.picked[0]) == "plane":
            frame = self.try_op("face_frame", face=self.picked[0])
            if frame:
                direction = frame["normal"]
        out = self.try_op("draft_check", direction=direction, min_angle=1.0)
        if not out:
            return
        rows = [(not out["needs_draft"], "draft",
                 "%d face(s) need draft: %s" % (len(out["needs_draft"]), ", ".join(out["needs_draft"][:6]))
                 if out["needs_draft"] else "every wall has draft"),
                (not out["undercuts"], "undercuts",
                 ", ".join("%s (%s mm²)" % (u["face"], u["undercut_mm2"]) for u in out["undercuts"])
                 if out["undercuts"] else "none along %s" % ", ".join("%.1f" % c for c in direction))]
        self.report.setHtml("".join('<div style="color:%s">%s %s: %s</div>' % (
            "#7fd97f" if ok else "#ff7070", "✓" if ok else "✗", check, said) for ok, check, said in rows))

    def _mass(self) -> None:
        out = self.try_op("mass_properties")
        if out:
            self.say("%.1f g of %s, centre of mass at %s" % (
                out["mass_g"], out["material"], ", ".join("%.1f" % c for c in out["centre_of_mass"])))

    def _step(self) -> None:
        suggested = ((self.doc or {}).get("path") or "part.json").replace(".json", ".step")
        path, _ = QFileDialog.getSaveFileName(self, "Write STEP", suggested, "STEP (*.step *.stp)")
        if path:
            out = self.try_op("export_step", path=path)
            if out:
                self.say("wrote %s (%d bytes%s)" % (out["path"], out["bytes"],
                                                    ", %d parts" % len(out["parts"]) if out.get("parts") else ""))

    def _drawing(self) -> None:
        suggested = ((self.doc or {}).get("path") or "part.json").replace(".json", ".svg")
        path, _ = QFileDialog.getSaveFileName(self, "Write the drawing", suggested, "SVG (*.svg)")
        if path:
            out = self.try_op("drawing", path=path, spec={"views": ["front", "top", "right"], "scale": 1})
            if out:
                self.say("wrote %s" % out["path"])

    # -- Ask ------------------------------------------------------------------------------
    def situation(self) -> str:
        lines = ["[From the RealParts app]"]
        if self.picked:
            lines.append("Selected faces: " + ", ".join(self.picked))
        elif self.picked_edges:
            lines.append("Selected edges (between faces): " + ", ".join(self.picked_edges))
        else:
            lines.append("Nothing is selected.")
        if self.doc:
            lines.append("Document: %s; features in order: %s" % (
                self.doc.get("path") or "(new, unsaved)",
                ", ".join(f["id"] for f in self.doc["features"]) or "none yet"))
            if self.built and self.built.get("faces"):
                lines.append("Body: %d faces, %d mm3." % (self.built["faces"], round(self.built["volume_mm3"])))
            else:
                lines.append("No solid yet.")
        else:
            lines.append("No document is open; start with new_document.")
        lines.append('"here" or "this" means the selection above. Say what you did in one or two lines. '
                     'Use only the cadcore tools: no files, no shell, no scripts.')
        return "\n".join(lines)

    def ask(self) -> None:
        question = self.question.toPlainText().strip()
        if not question:
            return
        try:
            self.hub.start_ask(question, self.situation())
        except web.CadError as exc:
            self.say("%s: %s" % (exc.kind, exc.message), error=True)
            return
        self.replies.append('<div style="color:#f0a030">%s</div>' % question)
        self.question.clear()
        self._asking = {"shown": 0, "generation": self.hub.generation}
        self.say("the assistant is working…")
        self.timer.start()

    def _poll(self) -> None:
        state = self.hub.state()
        ask = state.get("ask") or {"done": True, "events": []}
        for event in ask["events"][self._asking["shown"]:]:
            if event["kind"] == "text":
                self.replies.append(event["text"])
            else:
                self.replies.append('<span style="color:#9aa0a8">▸ %s %s</span>'
                                    % (event["text"], json.dumps(event.get("args", {}))[:120]))
        self._asking["shown"] = len(ask["events"])
        if state["generation"] != self._asking["generation"]:
            self._asking["generation"] = state["generation"]
            self.refresh()
        if ask["done"]:
            self.timer.stop()
            self._asking = None
            self.say(ask.get("error") or "the assistant is done", error=bool(ask.get("error")))


def _tidy(value) -> str:
    """A number as a person reads it: three figures, no trailing zeros after the point."""
    text = "%.3g" % float(value)
    return text
