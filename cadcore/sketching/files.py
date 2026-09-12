"""DXF and SVG read into the sketch language: points, lines, arcs, circles, splines.

What comes in is pinned where the file put it (every point fixed), so the
sketch solves as drawn and later constraints can move things on purpose.
"""
from __future__ import annotations

import math
import re
from xml.etree import ElementTree

from ..errors import CadError

__all__ = ["read_dxf", "read_svg", "read_file"]


def read_file(path: str, scale: float = 1.0, prefix: str = "f") -> dict:
    """The file's geometry as sketch entries, by its suffix (.dxf or .svg)."""
    lower = str(path).lower()
    if lower.endswith(".dxf"):
        return read_dxf(path, scale, prefix)
    if lower.endswith(".svg"):
        return read_svg(path, scale, prefix)
    raise CadError("unknown_format", f"no sketch reader for {path!r}",
                   {"available": [".dxf", ".svg"]})


class _Drawing:
    """Collects entities and spells them as sketch entries, points fixed."""

    def __init__(self, prefix: str, scale: float):
        self.prefix, self.scale = prefix, float(scale)
        self.points: dict = {}
        self.lines: dict = {}
        self.arcs: dict = {}
        self.circles: dict = {}
        self.splines: dict = {}
        self.count = 0

    def point(self, x: float, y: float) -> str:
        key = (round(x * self.scale, 6), round(y * self.scale, 6))
        for name, at in self.points.items():
            if (at[0], at[1]) == key:
                return name
        name = f"{self.prefix}p{len(self.points)}"
        self.points[name] = [key[0], key[1]]
        return name

    def next(self, kind: str) -> str:
        self.count += 1
        return f"{self.prefix}{kind}{self.count}"

    def line(self, a, b) -> None:
        if math.dist(a, b) < 1e-9:
            return
        self.lines[self.next("l")] = [self.point(*a), self.point(*b)]

    def circle(self, centre, radius: float) -> None:
        self.circles[self.next("c")] = {"centre": self.point(*centre),
                                        "radius": round(radius * self.scale, 6)}

    def arc(self, centre, radius: float, a0: float, a1: float, ccw: bool = True) -> None:
        """Angles in degrees, counter-clockwise from +x as DXF writes them."""
        start = (centre[0] + radius * math.cos(math.radians(a0)),
                 centre[1] + radius * math.sin(math.radians(a0)))
        end = (centre[0] + radius * math.cos(math.radians(a1)),
               centre[1] + radius * math.sin(math.radians(a1)))
        self.arcs[self.next("a")] = {"centre": self.point(*centre), "from": self.point(*start),
                                     "to": self.point(*end), "radius": round(radius * self.scale, 6),
                                     "ccw": ccw}

    def polyline(self, points: list, closed: bool) -> None:
        for a, b in zip(points, points[1:]):
            self.line(a, b)
        if closed and len(points) > 2:
            self.line(points[-1], points[0])

    def spline(self, points: list) -> None:
        points = [p for k, p in enumerate(points) if k == 0 or math.dist(p, points[k - 1]) > 1e-9]
        if len(points) < 3:
            self.polyline(points, False)
            return
        self.splines[self.next("s")] = {"through": [self.point(*p) for p in points]}

    def spec(self) -> dict:
        if not (self.lines or self.arcs or self.circles or self.splines):
            raise CadError("empty_sketch", "the file holds no lines, arcs, circles or splines")
        return {"points": self.points, "lines": self.lines, "arcs": self.arcs,
                "circles": self.circles, "splines": self.splines,
                "constraints": [{"type": "fix", "point": name, "at": at}
                                for name, at in self.points.items()]}


# --- DXF ------------------------------------------------------------------------

def read_dxf(path: str, scale: float = 1.0, prefix: str = "f") -> dict:
    """LINE, CIRCLE, ARC, LWPOLYLINE, POLYLINE and SPLINE (its fit or control points)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            codes = [line.rstrip("\r\n") for line in handle]
    except OSError as exc:
        raise CadError("bad_path", f"{path!r} cannot be read as a drawing", {"reason": str(exc)}) from exc
    pairs = [(codes[i].strip(), codes[i + 1].strip()) for i in range(0, len(codes) - 1, 2)]
    drawing = _Drawing(prefix, scale)
    entities = _dxf_entities(pairs)
    if entities is None:
        raise CadError("bad_path", f"{path!r} is not a DXF file", {"hint": "no ENTITIES section"})
    for kind, tags, ordered in entities:
        def num(code: str, default: float = 0.0) -> float:
            values = tags.get(code)
            try:
                return float(values[0]) if values else default
            except ValueError:
                return default
        if kind == "LINE":
            drawing.line((num("10"), num("20")), (num("11"), num("21")))
        elif kind == "CIRCLE":
            drawing.circle((num("10"), num("20")), num("40"))
        elif kind == "ARC":
            drawing.arc((num("10"), num("20")), num("40"), num("50"), num("51"))
        elif kind in ("LWPOLYLINE", "POLYLINE"):
            points, bulges = _dxf_vertices(ordered, after_vertex=kind == "POLYLINE")
            _bulged(drawing, points, bulges, int(num("70")) & 1 == 1)
        elif kind == "SPLINE":
            fit = list(zip([float(v) for v in tags.get("11", [])],
                           [float(v) for v in tags.get("21", [])]))
            control = list(zip([float(v) for v in tags.get("10", [])],
                               [float(v) for v in tags.get("20", [])]))
            drawing.spline(fit or control)
    return drawing.spec()


def _dxf_vertices(ordered: list, after_vertex: bool) -> tuple:
    """A polyline's vertices in order, each with its bulge (0 where the file has none).

    A vertex is a 10 then a 20, with a 42 only when it bows; the codes are
    read in sequence because a bulge belongs to the vertex it follows. A
    POLYLINE's own 10/20 is a placeholder: its real points are its VERTEX
    entities, which come after the first VERTEX marker.
    """
    points, bulges, seen_vertex = [], [], not after_vertex
    for code, value in ordered:
        if code == "0" and value == "VERTEX":
            seen_vertex = True
            continue
        if not seen_vertex:
            continue
        try:
            number = float(value)
        except ValueError:
            continue
        if code == "10":
            points.append([number, 0.0])
            bulges.append(0.0)
        elif code == "20" and points:
            points[-1][1] = number
        elif code == "42" and points:
            bulges[-1] = number
    return [tuple(p) for p in points], bulges


def _dxf_entities(pairs: list):
    """``(type, {code: [values]}, [(code, value) in order])`` per entity in the ENTITIES section, or None."""
    out, inside, current = [], False, None
    for code, value in pairs:
        if code == "0" and value == "SECTION":
            current = None
            continue
        if code == "2" and value == "ENTITIES" and not inside:
            inside = True
            continue
        if not inside:
            continue
        if code == "0":
            if value == "ENDSEC":
                break
            if value == "VERTEX" and current and current[0] == "POLYLINE":
                current[2].append((code, value))       # a marker: the points after it are real
                continue
            if value == "SEQEND":
                continue
            current = (value, {}, [])
            out.append(current)
        elif current is not None:
            current[1].setdefault(code, []).append(value)
            current[2].append((code, value))
    return out if inside else None


def _bulged(drawing: _Drawing, points: list, bulges: list, closed: bool) -> None:
    """A polyline whose segments may bow: a bulge is tan(angle/4), DXF's own spelling."""
    count = len(points)
    for k in range(count if closed else count - 1):
        a, b = points[k], points[(k + 1) % count]
        bulge = bulges[k] if bulges else 0.0
        if abs(bulge) < 1e-9:
            drawing.line(a, b)
            continue
        angle = 4 * math.atan(bulge)                      # the arc's sweep, signed
        chord = math.dist(a, b)
        if chord < 1e-9:
            continue
        radius = chord / (2 * math.sin(abs(angle) / 2))
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        # the centre sits off the chord's middle, on the left for a positive bulge
        h = math.sqrt(max(radius * radius - (chord / 2) ** 2, 0.0))
        nx, ny = -(b[1] - a[1]) / chord, (b[0] - a[0]) / chord
        side = 1.0 if angle > 0 else -1.0
        if abs(angle) > math.pi:
            side = -side
        centre = (mid[0] + nx * h * side, mid[1] + ny * h * side)
        a0 = math.degrees(math.atan2(a[1] - centre[1], a[0] - centre[0]))
        a1 = math.degrees(math.atan2(b[1] - centre[1], b[0] - centre[0]))
        drawing.arc(centre, radius, a0, a1, ccw=angle > 0)


# --- SVG ------------------------------------------------------------------------

#: a length with a unit, as millimetres per unit; a bare number or px is taken as mm
_SVG_UNITS = {"": 1.0, "px": 1.0, "mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72, "pc": 25.4 / 6}
_LENGTH = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-z%]*)\s*$")


def _svg_length(text, default: float = 0.0) -> float:
    m = _LENGTH.match(str(text)) if text is not None else None
    if m is None:
        return default
    return float(m.group(1)) * _SVG_UNITS.get(m.group(2), 1.0)


#: a 2D affine transform as SVG spells it: x' = a x + c y + e, y' = b x + d y + f
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _multiply(m, n) -> tuple:
    """m after n: the transform that applies n, then m."""
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (a * a2 + c * b2, b * a2 + d * b2, a * c2 + c * d2, b * c2 + d * d2,
            a * e2 + c * f2 + e, b * e2 + d * f2 + f)


def _svg_transform(text: str | None) -> tuple:
    """The matrix of a `transform` attribute; its functions compose left to right."""
    out = _IDENTITY
    for name, inner in re.findall(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)", text or ""):
        v = [float(t) for t in re.findall(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", inner)]
        if name == "matrix" and len(v) == 6:
            m = tuple(v)
        elif name == "translate":
            m = (1.0, 0.0, 0.0, 1.0, v[0] if v else 0.0, v[1] if len(v) > 1 else 0.0)
        elif name == "scale":
            m = (v[0] if v else 1.0, 0.0, 0.0, (v[1] if len(v) > 1 else v[0]) if v else 1.0, 0.0, 0.0)
        elif name == "rotate" and v:
            r = math.radians(v[0])
            m = (math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0.0, 0.0)
            if len(v) == 3:                       # about a point: move it to the origin and back
                m = _multiply(_multiply((1, 0, 0, 1, v[1], v[2]), m), (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "skewX" and v:
            m = (1.0, 0.0, math.tan(math.radians(v[0])), 1.0, 0.0, 0.0)
        elif name == "skewY" and v:
            m = (1.0, math.tan(math.radians(v[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        out = _multiply(out, m)
    return out


def _apply(m, p) -> tuple:
    a, b, c, d, e, f = m
    return (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f)


def _similarity(m) -> float | None:
    """The uniform scale of a transform that keeps circles round, else None."""
    a, b, c, d = m[:4]
    k1, k2 = math.hypot(a, b), math.hypot(c, d)
    if abs(k1 - k2) < 1e-9 * max(k1, 1.0) and abs(a * c + b * d) < 1e-9 * max(k1 * k1, 1.0):
        return k1
    return None


def read_svg(path: str, scale: float = 1.0, prefix: str = "f") -> dict:
    """line, rect, circle, ellipse (as a spline), polyline, polygon and path.

    SVG's y runs down the page; it is turned up so the sketch reads the way
    the drawing does. Every `transform` on the way down is applied, and a
    `viewBox` with a sized `width` maps user units to millimetres. Units are
    taken as millimetres unless ``scale`` says.
    """
    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError as exc:
        raise CadError("bad_path", f"{path!r} is not an SVG file", {"reason": str(exc)}) from exc
    except OSError as exc:
        raise CadError("bad_path", f"{path!r} cannot be read as a drawing", {"reason": str(exc)}) from exc
    drawing = _Drawing(prefix, scale)
    frame = _IDENTITY
    box = [float(t) for t in re.findall(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", root.get("viewBox") or "")]
    if len(box) == 4 and box[2] > 0 and box[3] > 0:
        width = _svg_length(root.get("width"), box[2]) or box[2]
        k = width / box[2]
        frame = (k, 0.0, 0.0, k, -box[0] * k, -box[1] * k)
    _svg_walk(drawing, root, frame)
    return drawing.spec()


def _svg_walk(drawing: _Drawing, node, frame) -> None:
    tag = node.tag.rsplit("}", 1)[-1]
    if tag in ("defs", "symbol", "clipPath", "mask", "marker", "pattern"):
        return                                # not drawn, only referred to
    m = _multiply(frame, _svg_transform(node.get("transform")))
    place = lambda p: (lambda q: (q[0], -q[1]))(_apply(m, p))          # noqa: E731
    g = lambda k, d=0.0: _svg_length(node.get(k), d)                  # noqa: E731
    k = _similarity(m)
    if tag == "line":
        drawing.line(place((g("x1"), g("y1"))), place((g("x2"), g("y2"))))
    elif tag == "rect":
        x, y, w, h = g("x"), g("y"), g("width"), g("height")
        drawing.polyline([place(p) for p in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))], True)
    elif tag == "circle":
        cx, cy, r = g("cx"), g("cy"), g("r")
        if k is not None:
            drawing.circle(place((cx, cy)), r * k)
        else:
            drawing.spline([place((cx + r * math.cos(t), cy + r * math.sin(t)))
                            for t in [2 * math.pi * i / 32 for i in range(33)]])
    elif tag == "ellipse":
        cx, cy, rx, ry = g("cx"), g("cy"), g("rx"), g("ry")
        drawing.spline([place((cx + rx * math.cos(t), cy + ry * math.sin(t)))
                        for t in [2 * math.pi * i / 32 for i in range(33)]])
    elif tag in ("polyline", "polygon"):
        numbers = [float(v) for v in _NUMBER.findall(node.get("points", ""))]
        drawing.polyline([place(p) for p in zip(numbers[::2], numbers[1::2])], tag == "polygon")
    elif tag == "path":
        _svg_path(drawing, node.get("d", ""), place, exact=m == _IDENTITY or k is not None)
    for child in node:
        _svg_walk(drawing, child, m)


_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def _svg_path(drawing: _Drawing, d: str, place, exact: bool = True) -> None:
    """The path's segments through ``place``; ``exact`` keeps a round arc an arc."""
    tokens = _TOKEN.findall(d)
    try:
        _svg_segments(drawing, tokens, place, exact)
    except (IndexError, ValueError) as exc:
        raise CadError("bad_path", "the path data ends in the middle of a segment",
                       {"path": d[:80], "reason": str(exc)}) from exc


def _svg_segments(drawing: _Drawing, tokens: list, place, exact: bool) -> None:
    i, cmd, cur, start, last_ctrl = 0, None, (0.0, 0.0), (0.0, 0.0), None

    def numbers(n: int) -> list:
        nonlocal i
        out = [float(tokens[i + k]) for k in range(n)]
        i += n
        return out

    def flag() -> bool:
        """An arc flag is one digit, and two may be written together: `01`."""
        nonlocal i
        token = tokens[i]
        if len(token) > 1 and token[0] in "01" and token[1] not in ".eE":
            tokens[i] = token[1:]                 # the rest is the next number
            return token[0] == "1"
        i += 1
        return float(token) != 0.0

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                if math.dist(cur, start) > 1e-9:
                    drawing.line(place(cur), place(start))
                cur = start
                continue
        if cmd is None:
            raise ValueError("path data starts with a number")
        rel = cmd.islower()
        c = cmd.upper()
        at = lambda x, y: (cur[0] + x, cur[1] + y) if rel else (x, y)    # noqa: E731
        if c == "M":
            x, y = numbers(2)
            cur = start = at(x, y)
            cmd = "l" if rel else "L"
        elif c == "L":
            x, y = numbers(2)
            to = at(x, y)
            drawing.line(place(cur), place(to))
            cur = to
        elif c == "H":
            x = numbers(1)[0]
            to = (cur[0] + x if rel else x, cur[1])
            drawing.line(place(cur), place(to))
            cur = to
        elif c == "V":
            y = numbers(1)[0]
            to = (cur[0], cur[1] + y if rel else y)
            drawing.line(place(cur), place(to))
            cur = to
        elif c in ("C", "S", "Q", "T"):
            need = {"C": 6, "S": 4, "Q": 4, "T": 2}[c]
            values = numbers(need)
            pts = [at(values[k], values[k + 1]) for k in range(0, need, 2)]
            if c in ("S", "T"):
                mirrored = (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1]) if last_ctrl else cur
                pts = [mirrored] + pts
            control = [cur] + pts
            drawing.spline([place(_bezier(control, t / 16)) for t in range(17)])
            last_ctrl = pts[-2] if len(pts) >= 2 else cur
            cur = pts[-1]
            continue
        elif c == "A":
            rx, ry, rot = numbers(3)
            large, sweep = flag(), flag()
            x, y = numbers(2)
            to = at(x, y)
            _svg_arc(drawing, cur, to, rx, ry, rot, large, sweep, place, exact)
            cur = to
        else:
            i += 1
        last_ctrl = None


def _bezier(control: list, t: float) -> tuple:
    points = list(control)
    while len(points) > 1:
        points = [((1 - t) * a[0] + t * b[0], (1 - t) * a[1] + t * b[1])
                  for a, b in zip(points, points[1:])]
    return points[0]


def _svg_arc(drawing: _Drawing, a, b, rx: float, ry: float, rot: float, large: bool,
             sweep: bool, place, exact: bool = True) -> None:
    """An SVG arc to centre form (the specification's own derivation); a round one is an arc,
    an elliptical one, or one under a transform that is not a similarity, a spline."""
    if rx <= 0 or ry <= 0 or math.dist(a, b) < 1e-9:
        drawing.line(place(a), place(b))
        return
    phi = math.radians(rot)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (a[0] - b[0]) / 2, (a[1] - b[1]) / 2
    x1 = cos_p * dx + sin_p * dy
    y1 = -sin_p * dx + cos_p * dy
    grow = (x1 / rx) ** 2 + (y1 / ry) ** 2
    if grow > 1:
        rx, ry = rx * math.sqrt(grow), ry * math.sqrt(grow)
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    factor = math.sqrt(max(num / den, 0.0)) * (-1 if large == sweep else 1)
    cx1, cy1 = factor * rx * y1 / ry, -factor * ry * x1 / rx
    cx = cos_p * cx1 - sin_p * cy1 + (a[0] + b[0]) / 2
    cy = sin_p * cx1 + cos_p * cy1 + (a[1] + b[1]) / 2
    t0 = math.atan2((y1 - cy1) / ry, (x1 - cx1) / rx)
    t1 = math.atan2((-y1 - cy1) / ry, (-x1 - cx1) / rx)
    sweep_angle = t1 - t0
    if sweep and sweep_angle < 0:
        sweep_angle += 2 * math.pi
    elif not sweep and sweep_angle > 0:
        sweep_angle -= 2 * math.pi
    if exact and abs(rx - ry) < 1e-9 and abs(rot) < 1e-9:
        centre, first, last = place((cx, cy)), place(a), place(b)
        radius = math.dist(centre, first)
        a0 = math.degrees(math.atan2(first[1] - centre[1], first[0] - centre[0]))
        a1 = math.degrees(math.atan2(last[1] - centre[1], last[0] - centre[0]))
        # placing turns the page over (y up), and a similarity may turn it again:
        # the sense is read from where the arc's middle landed
        middle = place((cx + rx * math.cos(t0 + sweep_angle / 2), cy + ry * math.sin(t0 + sweep_angle / 2)))
        cross = ((first[0] - centre[0]) * (middle[1] - centre[1])
                 - (first[1] - centre[1]) * (middle[0] - centre[0]))
        drawing.arc(centre, radius, a0, a1, ccw=cross > 0)
        return
    samples = []
    for k in range(17):
        t = t0 + sweep_angle * k / 16
        x, y = rx * math.cos(t), ry * math.sin(t)
        samples.append(place((cos_p * x - sin_p * y + cx, sin_p * x + cos_p * y + cy)))
    drawing.spline(samples)
