"""The kernel's triangles in Qt Quick 3D: one model per face, so a pick is a face name."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import Property, QObject, QPoint, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QMatrix3x3, QMatrix4x4, QQuaternion, QVector3D
from PySide6.QtQuick import QQuickView
from PySide6.QtQuick3D import QQuick3DGeometry
from PySide6.QtWidgets import QVBoxLayout, QWidget

SHADES = ["#cfd4dc", "#b9c3d3", "#d8cdbf", "#bfd3c6", "#cdc4d8", "#d6d0b8", "#b8cfd4", "#d4c0c6"]


def part_of(name: str) -> str:
    """The scope a face name carries, empty for a face of the document's own body."""
    return name[:name.rfind(":")] if ":" in name else ""


class Triangles(QQuick3DGeometry):
    """One face's triangles: position and normal, interleaved."""

    def __init__(self, position: np.ndarray, normal: np.ndarray):
        super().__init__()
        data = np.hstack([position, normal]).astype(np.float32)
        self.setVertexData(data.tobytes())
        self.setStride(24)
        self.addAttribute(QQuick3DGeometry.Attribute.Semantic.PositionSemantic, 0,
                          QQuick3DGeometry.Attribute.ComponentType.F32Type)
        self.addAttribute(QQuick3DGeometry.Attribute.Semantic.NormalSemantic, 12,
                          QQuick3DGeometry.Attribute.ComponentType.F32Type)
        self.setPrimitiveType(QQuick3DGeometry.PrimitiveType.Triangles)
        lo, hi = position.min(axis=0), position.max(axis=0)
        self.setBounds(QVector3D(*lo.tolist()), QVector3D(*hi.tolist()))
        self.centre, self.corners = position.mean(axis=0), len(position)     # for the labels' anchor


class Lines(QQuick3DGeometry):
    """A part's edges as line segments."""

    def __init__(self, points: np.ndarray):
        super().__init__()
        self.setVertexData(points.astype(np.float32).tobytes())
        self.setStride(12)
        self.addAttribute(QQuick3DGeometry.Attribute.Semantic.PositionSemantic, 0,
                          QQuick3DGeometry.Attribute.ComponentType.F32Type)
        self.setPrimitiveType(QQuick3DGeometry.PrimitiveType.Lines)
        if len(points):
            lo, hi = points.min(axis=0), points.max(axis=0)
            self.setBounds(QVector3D(*lo.tolist()), QVector3D(*hi.tolist()))


class Face(QObject):
    picked_changed = Signal()

    def __init__(self, name: str, geometry: Triangles, shade: str, parent=None):
        super().__init__(parent)
        self._name, self._geometry, self._shade, self._picked = name, geometry, shade, False
        self.centre, self.corners = geometry.centre, geometry.corners

    @Property(str, constant=True)
    def name(self) -> str:
        return self._name

    @Property(QObject, constant=True)
    def geometry(self):
        return self._geometry

    @Property(QColor, constant=True)
    def shade(self) -> QColor:
        return QColor(self._shade)

    @Property(bool, notify=picked_changed)
    def picked(self) -> bool:
        return self._picked

    def set_picked(self, value: bool) -> None:
        if value != self._picked:
            self._picked = value
            self.picked_changed.emit()


class Part(QObject):
    moved = Signal()

    def __init__(self, name: str, faces: list, lines: Lines, parent=None):
        super().__init__(parent)
        self._name, self._faces, self._lines = name, faces, lines
        self._position, self._rotation = QVector3D(0, 0, 0), QQuaternion()

    @Property(str, constant=True)
    def name(self) -> str:
        return self._name

    @Property(list, constant=True)
    def faces(self) -> list:
        return self._faces

    @Property(QObject, constant=True)
    def lines(self):
        return self._lines

    @Property(QVector3D, notify=moved)
    def position(self) -> QVector3D:
        return self._position

    @Property(QQuaternion, notify=moved)
    def rotation(self) -> QQuaternion:
        return self._rotation

    def place(self, matrix: QMatrix4x4) -> None:
        values = [matrix(r, c) for r in range(3) for c in range(3)]
        self._rotation = QQuaternion.fromRotationMatrix(QMatrix3x3(values))
        self._position = QVector3D(matrix(0, 3), matrix(1, 3), matrix(2, 3))
        self.moved.emit()


class Mark(QObject):
    """A mark drawn on the part: the arrow's tip, a grip, the plane mark."""

    def __init__(self, name: str, kind: str, at, colour: str, radius: float, parent=None):
        super().__init__(parent)
        self._name, self._kind, self._at, self._colour, self._radius = name, kind, QVector3D(*at), colour, radius

    @Property(str, constant=True)
    def name(self) -> str:
        return self._name

    @Property(str, constant=True)
    def kind(self) -> str:
        return self._kind

    @Property(QVector3D, constant=True)
    def at(self) -> QVector3D:
        return self._at

    @Property(QColor, constant=True)
    def colour(self) -> QColor:
        return QColor(self._colour)

    @Property(float, constant=True)
    def radius(self) -> float:
        return self._radius


class Label(QObject):
    """One of the picked feature's numbers, floating beside the part."""

    def __init__(self, key: str, text: str, parent=None):
        super().__init__(parent)
        self._key, self._text = key, text

    @Property(str, constant=True)
    def key(self) -> str:
        return self._key

    @Property(str, constant=True)
    def text(self) -> str:
        return self._text


class Bridge(QObject):
    """What QML reads and calls: the parts, the marks, where the camera looks, the clicks and drags."""

    parts_changed = Signal()
    view_changed = Signal()
    marks_changed = Signal()
    labels_changed = Signal()
    badge_changed = Signal()
    picked_at = Signal(str, float, float, bool)
    menu_at = Signal(str, float, float)
    drag_started = Signal(str, float, float)
    drag_moved = Signal(float, float, bool)
    drag_ended = Signal()
    hovered = Signal(float, float, bool)
    wheeled = Signal(int)
    label_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parts: list = []
        self._centre, self._distance = QVector3D(0, 0, 0), 200.0
        self._marks: list = []
        self._mark_lines = None
        self._picked_lines = None
        self._ring = None
        self._labels: list = []
        self._label_at = QVector3D(0, 0, 0)
        self._badge, self._badge_refused = "", False
        self._following = ""
        self._tool = ""

    @Property(list, notify=marks_changed)
    def marks(self) -> list:
        return self._marks

    @Property(QObject, notify=marks_changed)
    def markLines(self):                                    # noqa: N802 -- QML reads it
        return self._mark_lines

    @Property(QObject, notify=marks_changed)
    def pickedLines(self):                                  # noqa: N802
        return self._picked_lines

    @Property(QObject, notify=marks_changed)
    def ring(self):
        return self._ring

    def set_marks(self, marks: list, lines, picked_lines, ring=None) -> None:
        self._marks, self._mark_lines, self._picked_lines, self._ring = marks, lines, picked_lines, ring
        self.marks_changed.emit()

    @Property(list, notify=labels_changed)
    def labels(self) -> list:
        return self._labels

    @Property(QVector3D, notify=labels_changed)
    def labelAt(self) -> QVector3D:                         # noqa: N802
        return self._label_at

    def set_labels(self, labels: list, at) -> None:
        self._labels, self._label_at = labels, QVector3D(*at) if at else QVector3D(0, 0, 0)
        self.labels_changed.emit()

    @Property(str, notify=badge_changed)
    def badge(self) -> str:
        return self._badge

    @Property(bool, notify=badge_changed)
    def badgeRefused(self) -> bool:                         # noqa: N802
        return self._badge_refused

    @Property(str, notify=badge_changed)
    def following(self) -> str:
        return self._following

    @Property(str, notify=badge_changed)
    def tool(self) -> str:
        return self._tool

    def set_badge(self, text: str, refused: bool = False) -> None:
        self._badge, self._badge_refused = text, refused
        self.badge_changed.emit()

    def set_following(self, kind: str) -> None:
        self._following = kind
        self.badge_changed.emit()

    def set_tool(self, tool: str) -> None:
        self._tool = tool
        self.badge_changed.emit()

    @Property(list, notify=parts_changed)
    def parts(self) -> list:
        return self._parts

    def set_parts(self, parts: list) -> None:
        self._parts = parts
        self.parts_changed.emit()

    @Property(QVector3D, notify=view_changed)
    def centre(self) -> QVector3D:
        return self._centre

    @centre.setter
    def centre(self, value: QVector3D) -> None:
        self._centre = value
        self.view_changed.emit()

    @Property(float, notify=view_changed)
    def distance(self) -> float:
        return self._distance

    @distance.setter
    def distance(self, value: float) -> None:
        self._distance = max(0.5, float(value))
        self.view_changed.emit()

    @Slot(str, float, float, bool)
    def pick(self, name: str, x: float, y: float, shift: bool) -> None:
        self.picked_at.emit(name, x, y, shift)

    @Slot(str, float, float)
    def menu(self, name: str, x: float, y: float) -> None:
        self.menu_at.emit(name, x, y)

    @Slot(str, float, float)
    def dragStart(self, mark: str, x: float, y: float) -> None:      # noqa: N802
        self.drag_started.emit(mark, x, y)

    @Slot(float, float, bool)
    def dragMove(self, x: float, y: float, ctrl: bool) -> None:       # noqa: N802
        self.drag_moved.emit(x, y, ctrl)

    @Slot()
    def dragEnd(self) -> None:                                        # noqa: N802
        self.drag_ended.emit()

    @Slot(float, float, bool)
    def hover(self, x: float, y: float, click: bool) -> None:
        self.hovered.emit(x, y, click)

    @Slot(int)
    def wheel(self, delta: int) -> None:
        self.wheeled.emit(delta)

    @Slot(str)
    def labelClick(self, key: str) -> None:                           # noqa: N802
        self.label_clicked.emit(key)


class Viewport(QWidget):
    """Drag orbits, right-drag or Shift-drag pans, the wheel zooms, a click picks."""

    picked_changed = Signal(list)
    edges_changed = Signal(list)
    menu_wanted = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.faces: list = []
        self.picked: list = []
        self.parts: list = []
        self.count = 0
        self.framed = False
        self._face_objects: dict = {}
        self._part_objects: dict = {}
        self.picked_edges: list = []
        self.edges: dict = {}
        self._marks = ([], [], None)
        self.bridge = Bridge(self)
        self.bridge.picked_at.connect(self._clicked)
        self.bridge.menu_at.connect(self._right_clicked)
        self.quick = QQuickView()
        self.quick.setResizeMode(QQuickView.SizeRootObjectToView)
        self.quick.rootContext().setContextProperty("bridge", self.bridge)
        self.quick.setSource(QUrl.fromLocalFile(os.path.join(os.path.dirname(__file__), "scene.qml")))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QWidget.createWindowContainer(self.quick, self))
        self.setMinimumSize(200, 200)

    # -- data ------------------------------------------------------------------------
    def set_mesh(self, tess: dict) -> None:
        """The kernel's tessellate reply: a model per face, grouped by part."""
        table = tess.get("face_table") or []
        self.faces = table
        names = sorted({part_of(n) for n in table} |
                       {part_of(n.split("|")[0]) for n in (tess.get("edges") or {})})
        self.parts = names
        verts = np.asarray(tess["vertices"], dtype=np.float32).reshape(-1, 3)
        norms = np.asarray(tess.get("normals") or np.zeros_like(verts), dtype=np.float32).reshape(-1, 3)
        tris = np.asarray(tess["triangles"], dtype=np.int64).reshape(-1, 3)
        tri_face = np.asarray(tess["triangle_face"], dtype=np.int64)
        by_face: dict = {}
        for k, face in enumerate(tri_face.tolist()):
            by_face.setdefault(face, []).append(k)
        self._let_go(getattr(self, "_mesh_objects", []))
        self._mesh_objects: list = []
        self._face_objects, self._part_objects = {}, {}
        parts = []
        for p, part in enumerate(names):
            faces = []
            for face, rows in by_face.items():
                if part_of(table[face]) != part:
                    continue
                corners = tris[rows].reshape(-1)
                geometry = Triangles(verts[corners], norms[corners])
                geometry.setParent(self)
                obj = Face(table[face], geometry, SHADES[p % len(SHADES)], self)
                self._mesh_objects += [geometry, obj]
                obj.set_picked(table[face] in self.picked)
                self._face_objects[table[face]] = obj
                faces.append(obj)
            points = []
            for edge, line in (tess.get("edges") or {}).items():
                if part_of(edge.split("|")[0]) == part:
                    for a, b in zip(line, line[1:]):
                        points += [a, b]
            lines = Lines(np.asarray(points, dtype=np.float32).reshape(-1, 3))
            lines.setParent(self)
            obj = Part(part, faces, lines, self)
            self._mesh_objects += [lines, obj]
            self._part_objects[part] = obj
            parts.append(obj)
        self.count = int(len(tri_face) * 3)
        self.edges = tess.get("edges") or {}
        self.picked_edges = [e for e in self.picked_edges if e in self.edges]
        self.bridge.set_parts(parts)
        self.show_picked_edges()
        bounds = verts if len(verts) else np.asarray([p for line in self.edges.values() for p in line])
        if not self.framed and len(bounds):
            self.frame(bounds)
            self.framed = True

    def frame(self, verts: np.ndarray) -> None:
        lo, hi = verts.min(axis=0), verts.max(axis=0)
        c = (lo + hi) / 2
        self.bridge.centre = QVector3D(float(c[0]), float(c[2]), -float(c[1]))   # z-up to y-up
        self.bridge.distance = max(1.0, float(np.linalg.norm(hi - lo))) * 1.5

    def set_picked(self, names: list) -> None:
        self.picked = [n for n in names if n in self.faces]
        for name, obj in self._face_objects.items():
            obj.set_picked(name in self.picked)

    def set_part_matrix(self, part: str, matrix: QMatrix4x4) -> None:
        """Move a part: the scope named, and every scope nested inside it."""
        for name, obj in self._part_objects.items():
            if name == part or name.startswith(part + ":"):
                obj.place(matrix)

    def reset_parts(self) -> None:
        for obj in self._part_objects.values():
            obj.place(QMatrix4x4())

    @property
    def part_matrices(self) -> dict:
        return {name: obj for name, obj in self._part_objects.items() if not obj.rotation.isIdentity()
                or obj.position != QVector3D(0, 0, 0)}

    # -- marks and labels ------------------------------------------------------------
    def set_marks(self, marks: list, polylines: list, ring: list | None = None) -> None:
        """Marks as (name, kind, at, colour, radius); polylines as lists of points, all CAD mm."""
        self._marks = (marks, polylines, ring)
        self._let_go(getattr(self, "_mark_objects", []))
        objects = [Mark(*m, parent=self) for m in marks]
        lines = Lines(_segments(polylines))
        lines.setParent(self)
        ring_lines = None
        if ring:
            ring_lines = Lines(_segments([ring]))
            ring_lines.setParent(self)
        picked = Lines(_segments([self.edges[e] for e in self.picked_edges if e in self.edges]))
        picked.setParent(self)
        self._mark_objects = objects + [lines, picked] + ([ring_lines] if ring_lines else [])
        self.bridge.set_marks(objects, lines, picked, ring_lines)

    @staticmethod
    def _let_go(objects: list) -> None:
        """Scene objects that were replaced: deleted once QML has let go of them."""
        for obj in objects:
            obj.setParent(None)
            obj.deleteLater()

    def show_picked_edges(self) -> None:
        self.set_marks(*self._marks)

    def set_labels(self, labels: list, at) -> None:
        self.bridge.set_labels([Label(k, t, self) for k, t in labels], at)

    def centre_of(self, faces: set):
        """The mean of these faces' triangle corners, CAD mm, or None."""
        total, count = np.zeros(3), 0
        for name in faces:
            obj = self._face_objects.get(name)
            if obj is None:
                continue
            total += obj.centre * obj.corners
            count += obj.corners
        return (total / count).tolist() if count else None

    # -- picking ------------------------------------------------------------------------
    def face_at(self, x: int, y: int) -> str | None:
        root = self.quick.rootObject()
        if root is None:
            return None
        name = root.pickAt(float(x), float(y))
        return name or None

    def project(self, point) -> tuple | None:
        """A CAD point as view pixels, or None before the scene is up."""
        root = self.quick.rootObject()
        if root is None:
            return None
        at = root.project(float(point[0]), float(point[1]), float(point[2]))
        return (at.x(), at.y()) if at is not None else None

    def hit_at(self, x: int, y: int) -> tuple | None:
        """The face under a view pixel and where the ray met it, CAD mm."""
        root = self.quick.rootObject()
        if root is None:
            return None
        hit = root.hitAt(float(x), float(y))
        hit = hit.toVariant() if hasattr(hit, "toVariant") else hit
        if not hit or not hit[0]:
            return None
        return hit[0], [float(hit[1]), -float(hit[3]), float(hit[2])]       # y-up back to z-up

    def mm_per_pixel(self, point) -> float:
        root = self.quick.rootObject()
        if root is None:
            return 0.1
        return float(root.mmPerPixel(float(point[0]), float(point[1]), float(point[2]))) or 0.1

    def edge_at(self, x: float, y: float, near: float = 8.0) -> str | None:
        """The edge whose drawn line passes nearest the pixel, within reach."""
        best, found = near, None
        for name, line in self.edges.items():
            points = [self.project(p) for p in line]
            for a, b in zip(points, points[1:]):
                if a is None or b is None:
                    continue
                d = _point_to_segment((x, y), a, b)
                if d < best:
                    best, found = d, name
        return found

    def look(self, pitch: float, yaw: float) -> None:
        """Turn the orbit: degrees, pitch negative looks down, yaw about the world's up."""
        root = self.quick.rootObject()
        orbit = root.findChild(QObject, "orbit") if root is not None else None
        if orbit is not None:
            orbit.setProperty("eulerRotation", QVector3D(pitch, yaw, 0))

    def grab(self):
        """The picture as an image: the 3D view is its own window, so the widget's grab is blank."""
        return self.quick.grabWindow()

    def release(self) -> None:
        """Let the scene go before the bridge does, so QML is not left reading a dead object."""
        self.quick.setSource(QUrl())

    def _clicked(self, name: str, x: float, y: float, shift: bool) -> None:
        edge = self.edge_at(x, y) if not name or not shift else None
        if edge is not None and (not name or self.edge_at(x, y, 4.0)):
            self.picked_edges = [] if self.picked_edges == [edge] and not shift else \
                ([edge] if not shift else (self.picked_edges + [edge] if edge not in self.picked_edges
                                           else [e for e in self.picked_edges if e != edge]))
            if not shift:
                self.picked = []
                self.set_picked([])
            self.edges_changed.emit(list(self.picked_edges))
            return
        if not shift:
            self.picked_edges = []
        if not name and not shift:
            self.picked = []
        elif name:
            if not shift:
                self.picked = [] if self.picked == [name] else [name]
            elif name in self.picked:
                self.picked.remove(name)
            else:
                self.picked.append(name)
        self.set_picked(self.picked)
        self.picked_changed.emit(list(self.picked))

    def _right_clicked(self, name: str, x: float, y: float) -> None:
        if name and name not in self.picked:
            self.set_picked([name])
            self.picked_changed.emit([name])
        self.menu_wanted.emit(self.mapToGlobal(QPoint(int(x), int(y))))


def _segments(polylines: list) -> np.ndarray:
    points = []
    for line in polylines:
        for a, b in zip(line, line[1:]):
            points += [a, b]
    return np.asarray(points, dtype=np.float32).reshape(-1, 3)


def _point_to_segment(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return ((p[0] - a[0] - t * dx) ** 2 + (p[1] - a[1] - t * dy) ** 2) ** 0.5
