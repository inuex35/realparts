"""What a solved sketch is: a plane, named segments, and loops.

Kept apart from the solving so that a caller who only wants to *read* a sketch
-- the viewport drawing it, a drawing sheet dimensioning it -- does not import
the solver.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Plane:
    origin: tuple = (0.0, 0.0, 0.0)
    normal: tuple = (0.0, 0.0, 1.0)
    x_axis: tuple = (1.0, 0.0, 0.0)

    def y_axis(self) -> tuple:
        n, x = self.normal, self.x_axis
        return (n[1] * x[2] - n[2] * x[1], n[2] * x[0] - n[0] * x[2], n[0] * x[1] - n[1] * x[0])

    def to_3d(self, u: float, v: float) -> tuple:
        y = self.y_axis()
        return tuple(self.origin[i] + self.x_axis[i] * u + y[i] * v for i in range(3))


@dataclass
class Segment:
    """One named piece of a profile, resolved to coordinates by the solver."""

    name: str
    kind: str                                  # line | arc | circle | ellipse | spline
    start: tuple = (0.0, 0.0)
    end: tuple = (0.0, 0.0)
    centre: tuple | None = None
    radius: float | None = None                # an ellipse's semi-major axis
    ccw: bool = True
    through: list = field(default_factory=list)     # a spline's points, in order
    minor: float | None = None                 # an ellipse's semi-minor axis
    angle: float = 0.0                         # radians from u to the major axis

    def sample(self, steps: int = 48) -> list:
        """The segment as points along it, in sketch coordinates.

        A line is its two ends; an arc or a circle is walked in angle; a spline
        is the points it goes through. What a viewport draws when there is no
        solid yet to draw.
        """
        if self.kind == "spline":
            return [tuple(p) for p in self.through]
        if self.kind == "line":
            return [tuple(self.start), tuple(self.end)]
        if self.kind == "ellipse":
            return [self.on_ellipse(2 * math.pi * i / steps) for i in range(steps + 1)]
        a0 = math.atan2(self.start[1] - self.centre[1], self.start[0] - self.centre[0])
        a1 = math.atan2(self.end[1] - self.centre[1], self.end[0] - self.centre[0])
        if self.kind == "circle":
            a0, a1 = 0.0, 2 * math.pi
        elif self.ccw and a1 <= a0:
            a1 += 2 * math.pi
        elif not self.ccw and a1 >= a0:
            a1 -= 2 * math.pi
        count = max(2, int(steps * abs(a1 - a0) / (2 * math.pi)) + 1)
        return [(self.centre[0] + self.radius * math.cos(a0 + (a1 - a0) * i / (count - 1)),
                 self.centre[1] + self.radius * math.sin(a0 + (a1 - a0) * i / (count - 1)))
                for i in range(count)]

    def on_ellipse(self, t: float) -> tuple:
        """The ellipse's point at parameter ``t`` (radians round the major axis)."""
        c, s = math.cos(self.angle), math.sin(self.angle)
        x, y = self.radius * math.cos(t), self.minor * math.sin(t)
        return (self.centre[0] + x * c - y * s, self.centre[1] + x * s + y * c)

    def mid(self) -> tuple:
        if self.kind == "spline":
            return tuple(self.through[len(self.through) // 2])
        if self.kind == "line":
            return tuple((self.start[i] + self.end[i]) / 2 for i in range(2))
        if self.kind == "ellipse":
            return self.on_ellipse(math.pi / 2)
        a0 = math.atan2(self.start[1] - self.centre[1], self.start[0] - self.centre[0])
        a1 = math.atan2(self.end[1] - self.centre[1], self.end[0] - self.centre[0])
        if self.kind == "circle":
            a0, a1 = 0.0, math.pi
        elif self.ccw and a1 <= a0:
            a1 += 2 * math.pi
        elif not self.ccw and a1 >= a0:
            a1 -= 2 * math.pi
        a = (a0 + a1) / 2
        return (self.centre[0] + self.radius * math.cos(a),
                self.centre[1] + self.radius * math.sin(a))


@dataclass
class SketchResult:
    plane: Plane
    points: dict = field(default_factory=dict)          # name -> (u, v)
    loops: list = field(default_factory=list)           # [[Segment, ...], ...] outline first
    dof: int = 0
    diagnosis: dict = field(default_factory=dict)
    closed: bool = True                                 # an open sketch is a path

    def polylines(self) -> dict:
        """Every segment as a named polyline in 3D, on the sketch's plane."""
        return {segment.name: [self.plane.to_3d(u, v) for u, v in segment.sample()]
                for loop in self.loops for segment in loop}
