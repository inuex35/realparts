"""A drag is a run of trial edits between begin_drag and end_drag, which the kernel records
as one step or none. The same contract as the add-on's viewport/live.py and the page's drags.js."""
from __future__ import annotations

import math

from PySide6.QtCore import QObject, QTimer, Signal

from cadcore.service import web


def _takes_two(fn) -> bool:
    import inspect
    try:
        return len(inspect.signature(fn).parameters) >= 2
    except (TypeError, ValueError):
        return False


class LiveDrag(QObject):
    """make(value) -> (op, args) creates the feature; edit(value[, feature]) -> args edits it after,
    or (op, args) for an operation of its own."""

    changed = Signal()          # the badge: text, refused, value
    ended = Signal()

    def __init__(self, window, label: str, make, edit, fmt=None):
        super().__init__(window)
        self.window, self.label, self.make, self.edit = window, label, make, edit
        self.fmt = fmt or (lambda v: "%.2f mm" % v)
        self.feature = None
        self.value = self.good = self.refused = self.pending = None
        self.typed = ""
        self.text, self.is_refused, self.busy, self.done = "", False, False, False

    def begin(self) -> "LiveDrag":
        self.window.op("begin_drag")
        return self

    def say(self, text: str, refused: bool = False) -> None:
        self.text, self.is_refused = text, refused
        self.changed.emit()

    def preview(self, value: float) -> None:
        if self.done:
            return
        self.value = value
        self.say(self.fmt(value))
        self.pending = value
        if not self.busy:
            QTimer.singleShot(0, self._run)         # after this event: forty moves are a few rebuilds

    def _run(self) -> None:
        self.busy = True
        try:
            while self.pending is not None and not self.done:
                value, self.pending = self.pending, None
                try:
                    if self.feature is None:
                        op, args = self.make(value)
                        out = self.window.op(op, **args)
                        self.feature = out.get("feature")
                    else:
                        change = self.edit(value, self.feature) if _takes_two(self.edit) else self.edit(value)
                        if isinstance(change, tuple):            # an operation of its own
                            out = self.window.op(change[0], **change[1])
                        else:
                            out = self.window.op("edit_feature", feature_id=self.feature, args=change)
                    self.good, self.refused = value, None
                    self.window.shown(out)
                    if not self.done:
                        self.say(self.fmt(value))
                except web.CadError as exc:
                    # the part on screen is still the last one that built: say so in the badge
                    self.refused = value
                    self.say(exc.message, refused=True)
        finally:
            self.busy = False

    def key(self, text: str) -> bool:
        """A typed digit, a Backspace ("\\b"), Enter or Escape; whether it was taken."""
        if text == "escape":
            self.cancel()
            return True
        if text == "enter":
            self.finish()
            return True
        if text == "\b":
            self.typed = self.typed[:-1]
        elif text and text in "0123456789.-":
            self.typed += text
        else:
            return False
        try:
            typed = float(self.typed)
        except ValueError:
            return True
        self.preview(typed)
        self.say("%s mm (typed)" % self.typed)
        return True

    def _the_value_that_built(self):
        return self.good if self.refused is not None and self.good is not None else self.value

    def finish(self) -> None:
        if self.done:
            return
        self.done, self.pending = True, None
        value = self._the_value_that_built()
        window = self.window
        if value is None:
            self._drop()
            return
        try:
            window.op("reset_drag")
            op, args = self.make(value)
            window.op(op, **args)
            window.shown(window.op("end_drag", keep=True))
            window.say("%s: %s%s" % (self.label, self.fmt(value),
                                     " (the shape would not take any more)" if self.refused is not None else ""))
        except web.CadError as exc:
            try:
                window.shown(window.op("end_drag", keep=False))
            except web.CadError:
                pass
            window.say("%s: %s" % (exc.kind, exc.message), error=True)
        self.ended.emit()

    def cancel(self) -> None:
        if self.done:
            return
        self.done, self.pending = True, None
        self._drop()
        self.window.say("cancelled")

    def _drop(self) -> None:
        try:
            self.window.shown(self.window.op("end_drag", keep=False))
        except web.CadError:
            pass
        self.ended.emit()


class Snapper:
    """Snap targets, built once per drag from describe_faces: parallel planes, points on the face."""

    PIXELS = 9.0

    def __init__(self, faces: list, frame: dict, own: str):
        n, o = frame["normal"], frame["origin"]
        self.planes, self.points = [], [((0.0, 0.0), "centre")]
        for f in faces:
            if f.get("shape") == "plane" and f["name"] != own and f.get("normal"):
                if abs(abs(_dot(n, f["normal"])) - 1) > 1e-3:
                    continue
                d = _dot(_sub(f["centre"], o), n)
                if abs(d) > 1e-6:
                    self.planes.append((d, f["name"]))
            elif f.get("shape") == "cylinder" and f.get("axis"):
                if abs(abs(_dot(n, f["axis"])) - 1) > 1e-3:
                    continue
                self.points.append((to_uv(f["centre"], frame), f["name"]))

    def distance(self, raw: float, mm_per_pixel: float) -> tuple:
        best, label = raw, None
        for d, name in self.planes:
            if abs(d - raw) <= self.PIXELS * mm_per_pixel and (label is None or abs(d - raw) < abs(best - raw)):
                best, label = d, name
        return best, label

    def uv(self, uv: tuple, mm_per_pixel: float) -> tuple:
        reach = self.PIXELS * mm_per_pixel
        for (pu, pv), name in self.points:
            if math.dist(uv, (pu, pv)) <= reach:
                return (pu, pv), name
        for (pu, pv), name in self.points:
            if abs(uv[0] - pu) <= reach:
                return (pu, uv[1]), name + " (u)"
            if abs(uv[1] - pv) <= reach:
                return (uv[0], pv), name + " (v)"
        return uv, None


# a face frame: origin, outward normal, x_axis; (u, v) are millimetres in the face's own plane
def to_uv(point, frame: dict) -> tuple:
    d = _sub(point, frame["origin"])
    x, n = frame["x_axis"], frame["normal"]
    y = _cross(n, x)
    return (_dot(d, x), _dot(d, y))


def to_3d(uv, frame: dict) -> list:
    x, n = frame["x_axis"], frame["normal"]
    y = _cross(n, x)
    return [frame["origin"][i] + x[i] * uv[0] + y[i] * uv[1] for i in range(3)]


def snap(value: float, step: float) -> float:
    return round(value / step) * step


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b) -> list:
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _cross(a, b) -> list:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
