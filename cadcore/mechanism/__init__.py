"""Planar mechanisms as 2D constraint systems, solved by `cadcore.sketching`.

A `Rig` declares joints (points), links (distance constraints), anchors
(`fix`), guide lines and a driver angle:
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..errors import CadError
from ..sketching import solve


class MechanismError(CadError):
    """A mechanism that will not assemble; ``detail`` carries the numbers."""


@dataclass(frozen=True)
class Placement:
    """A body's origin and rotation about z."""

    x: float = 0.0
    y: float = 0.0
    turn: float = 0.0                    # radians, counter-clockwise
    z: float = 0.0


@dataclass
class Part:
    """One body: its document, parameters and the joints it sits on.

    A bar gives the two joints in ``spans``; any other body gives one joint in
    ``rides`` and optionally the link whose direction it follows in
    ``turns_with``.
    """

    name: str
    document: str
    parameters: dict = field(default_factory=dict)
    colour: tuple = (0.62, 0.64, 0.68)
    spans: tuple | None = None
    rides: str | None = None
    turns_with: str | None = None        # a link whose direction it takes
    z: float = 0.0
    spec: dict | None = None             # a document computed rather than read


@dataclass
class Rig:
    """A mechanism as a constraint system, plus the bodies on its joints."""

    joints: dict                          # name -> a starting guess
    links: dict                           # name -> (joint, joint), a fixed length
    driver: tuple                         # the pair whose direction is the input
    anchors: dict = field(default_factory=dict)     # joint -> where it is pinned
    loop: list = field(default_factory=list)        # joint order round the closed chain
    frames: list = field(default_factory=list)      # extra lines: ("name", a, b, "horizontal")
    parts: list = field(default_factory=list)
    lengths: dict = field(default_factory=dict)     # link -> length, if not from the seed

    def sketch(self, seed: dict, angle_deg: float) -> dict:
        """The sketch dict for this input angle, with ``seed`` as start points."""
        order = self.loop or list(self.joints)
        lines, constraints = {}, []
        for index, joint in enumerate(order):
            nxt = order[(index + 1) % len(order)]
            name = self._link_between(joint, nxt) or f"side{index}"
            lines[name] = [joint, nxt]
        for name, (a, b) in self.links.items():
            length = self.lengths.get(name)
            if length is None:
                length = math.dist(self.joints[a], self.joints[b])
            constraints.append({"type": "distance", "points": [a, b],
                                "value": float(length)})
        for joint, at in self.anchors.items():
            constraints.append({"type": "fix", "point": joint, "at": list(at)})
        for name, a, b, kind in self.frames:
            lines.setdefault(name, [a, b])
            constraints.append({"type": kind, "line": name})
        constraints.append({"type": "angle", "points": list(self.driver),
                            "value": float(angle_deg)})
        return {"plane": {"origin": [0, 0, 0], "normal": [0, 0, 1],
                          "x_axis": [1, 0, 0]},
                "points": {k: list(v) for k, v in seed.items()},
                "lines": lines, "constraints": constraints}

    def _link_between(self, a: str, b: str) -> str | None:
        for name, pair in self.links.items():
            if set(pair) == {a, b}:
                return name
        for name, first, second, _ in self.frames:
            if {first, second} == {a, b}:
                return name
        return None

    def solve(self, angle_deg: float, seed: dict | None = None) -> dict:
        """Joint positions at this input angle; raises MechanismError if unsolvable."""
        start = {k: list(v) for k, v in (seed or self.joints).items()}
        try:
            out = solve(self.sketch(start, angle_deg), lambda v: v)
        except CadError as exc:
            # the solver's own refusal, with where it happened added
            exc.message = f"the mechanism will not close at {angle_deg:.1f} deg ({exc.message})"
            exc.detail.update({"angle_deg": round(angle_deg, 2), "lengths": self.link_lengths()})
            raise
        if out.dof != 0:
            raise MechanismError(
                "not_a_mechanism",
                f"{out.dof} degrees of freedom are left with the input held",
                {"dof": out.dof, "angle_deg": round(angle_deg, 2)})
        return {k: (float(v[0]), float(v[1])) for k, v in out.points.items()}

    def link_lengths(self) -> dict:
        return {name: round(self.lengths.get(
            name, math.dist(self.joints[a], self.joints[b])), 4)
            for name, (a, b) in self.links.items()}

    def sweep(self, frames: int = 72, turns: float = 1.0) -> list:
        """Solve `frames + 1` poses from 0 to a whole turn inclusive, each seeded
        from the previous one so the assembly branch does not flip.

        The last pose repeats the first, which lets tests check for drift;
        animations drop it (`mechanism.scene` does). A jam is reported as how
        far the mechanism turned before it stopped.
        """
        seed, out = {k: list(v) for k, v in self.joints.items()}, []
        reached = None
        for index in range(frames + 1):
            angle = 360.0 * turns * index / frames
            try:
                joints = self.solve(angle, seed)
            except CadError as exc:
                raise MechanismError(
                    "jammed",
                    f"the mechanism turns as far as {reached:.1f} deg and no "
                    f"further" if reached is not None else
                    "the mechanism will not assemble at all",
                    {**exc.detail, "stopped_at_deg": round(angle, 2),
                     "turned_through_deg": reached,
                     "refused_by": exc.kind}) from None
            seed = {k: list(v) for k, v in joints.items()}
            reached = angle
            out.append(joints)
        return out

    def place(self, joints: dict) -> dict:
        """Placement of each part from the joint positions."""
        placed = {}
        for part in self.parts:
            if part.spans:
                first, second = (joints[j] for j in part.spans)
                placed[part.name] = Placement(
                    (first[0] + second[0]) / 2, (first[1] + second[1]) / 2,
                    math.atan2(second[1] - first[1], second[0] - first[0]), part.z)
            else:
                at = joints[part.rides]
                turn = 0.0
                if part.turns_with:
                    a, b = self.links[part.turns_with]
                    turn = math.atan2(joints[b][1] - joints[a][1],
                                      joints[b][0] - joints[a][0])
                placed[part.name] = Placement(at[0], at[1], turn, part.z)
        return placed

    def reach(self) -> float:
        """Approximate overall extent over a cycle, for framing a camera."""
        span = 0.0
        for joints in self.sweep(frames=36):
            for x, y in joints.values():
                span = max(span, math.hypot(x, y))
        return span * 2.2
