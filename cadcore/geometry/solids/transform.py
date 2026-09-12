"""Moving and copying bodies, with every instance still selectable.

A copy is only useful if you can point at it afterwards, so every instance of a
pattern is suffixed (``bolt/side~3``) and a mirror marks its reflection
(``blk/+x~m``). Instances that do not touch are collected into a compound rather
than fused: a boolean between disjoint solids has nothing to compute.
"""
from __future__ import annotations

import math

from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Builder
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.TopoDS import TopoDS_Compound
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

from .booleans import _inherit, fuse
from ... import names
from ..core import provenance
from ...errors import CadError
from ..core.naming import Body, reroled


def translate(feature_id: str, body: Body, offset) -> Body:
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(*[float(v) for v in offset]))
    algo = BRepBuilderAPI_Transform(body.shape, trsf, True)
    algo.Build()
    return _inherit(algo, [body], algo.Shape(), feature_id)


MIRROR = "m"          # a mirror image is one instance, and not a numbered one


def _instanced(body: Body, index) -> Body:
    """The same body with every name marked as a copy -- how one stays referable.

    A pattern of four holes must not give four faces the same name, and calling
    them ``hole/side~1`` .. ``hole/side~3`` keeps every instance selectable by a
    name the designer can predict from the original. A mirror image is
    ``hole/side~m``, because there is only ever one of it.
    """
    def named(name: str) -> str:
        return names.instance(name, index)

    return body.rebuilt(
        names=[(named(n), f) for n, f in body.names],
        aliases={named(a): named(c) for a, c in body.aliases.items()},
        dropped=[named(n) for n in body.dropped])


def mirror(feature_id: str, body: Body, origin, normal, merge: bool = True) -> Body:
    """Mirror a body about a plane, optionally keeping the original with it."""
    trsf = gp_Trsf()
    trsf.SetMirror(gp_Ax2(gp_Pnt(*[float(v) for v in origin]),
                          gp_Dir(*[float(v) for v in normal])))
    algo = BRepBuilderAPI_Transform(body.shape, trsf, True)
    algo.Build()
    reflected = _instanced(_inherit(algo, [body], algo.Shape(), feature_id), MIRROR)
    # a face that pointed along the mirror's normal now points the other way,
    # and its role must say so: `blk/+x~m` on the far side, facing -x, was a
    # name lying about the one thing a role promises
    n = [float(v) for v in normal]
    length = sum(c * c for c in n) ** 0.5 or 1.0
    n = [c / length for c in n]
    reflected = reflected.rebuilt(
        names=[(names.mirrored(name, n), f) for name, f in reflected.names],
        aliases={names.mirrored(a, n): names.mirrored(c, n) for a, c in reflected.aliases.items()},
        dropped=[names.mirrored(name, n) for name in reflected.dropped])
    if not merge:
        return reflected
    return fuse(feature_id, body, reflected)


def _repeat(feature_id: str, body: Body, transforms: list, merge: bool) -> Body:
    """Copy a body under each transform, keeping every instance referable.

    Instances that do not touch are collected into a compound instead of being
    fused one by one. A boolean between disjoint solids has nothing to compute
    and everything to check, and a forty-hole pattern pays for forty of them.
    """
    copies = []
    for k, trsf in enumerate(transforms, start=1):
        algo = BRepBuilderAPI_Transform(body.shape, trsf, True)
        algo.Build()
        copies.append(reroled(_instanced(_inherit(algo, [body], algo.Shape(), feature_id), k)))

    # merge=False means "every instance, unfused" -- the same compound the
    # disjoint path builds, not one stray copy at the far end of the row
    instances = [body] + copies
    if not merge or _all_disjoint(instances):
        builder = BRep_Builder()
        compound = TopoDS_Compound()
        builder.MakeCompound(compound)
        names, aliases, dropped = [], {}, []
        for part in instances:
            builder.Add(compound, part.shape)
            names.extend(part.names)
            aliases.update(part.aliases)
            dropped.extend(part.dropped)
        return body.rebuilt(shape=compound, names=names, aliases=aliases,
                            dropped=dropped, notes=provenance.merged(instances))

    out = body
    for copy in copies:
        out = fuse(feature_id, out, copy)
    return out


def compound(feature_id: str, bodies: list) -> Body:
    """Several bodies as one shape, every name kept -- an assembly, or a pattern.

    No boolean: the parts of an assembly are separate solids and should stay
    that way, so that interference between them is still a question that can be
    asked.
    """
    if not bodies:
        raise CadError("empty_selection", "nothing to assemble")
    builder = BRep_Builder()
    shape = TopoDS_Compound()
    builder.MakeCompound(shape)
    names, aliases, dropped = [], {}, []
    for part in bodies:
        builder.Add(shape, part.shape)
        names.extend(part.names)
        aliases.update(part.aliases)
        dropped.extend(part.dropped)
    notes = provenance.merged(bodies)
    return bodies[0].rebuilt(shape=shape, names=names, aliases=aliases,
                             dropped=dropped, notes=notes)


def _all_disjoint(bodies: list, gap: float = 1e-7) -> bool:
    """Bounding boxes only -- a cheap sufficient test, never a claim of contact."""
    boxes = []
    for part in bodies:
        box = Bnd_Box()
        BRepBndLib.Add_s(part.shape, box)
        boxes.append(box)
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if not a.IsOut(b):
                return False
    return True


def pattern(feature_id: str, body: Body, direction, spacing: float, count: int,
            merge: bool = True) -> Body:
    """A linear pattern: ``count`` instances, ``spacing`` apart, as one body."""
    n = int(count)
    if n < 2:
        raise CadError("bad_parameter", "a pattern needs at least two instances",
                       {"count": n})
    step = [float(v) for v in direction]
    length = math.sqrt(sum(c * c for c in step))
    if length < 1e-9:
        raise CadError("bad_parameter", "the pattern direction is zero")
    step = [c / length * float(spacing) for c in step]

    transforms = []
    for k in range(1, n):
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(step[0] * k, step[1] * k, step[2] * k))
        transforms.append(trsf)
    return _repeat(feature_id, body, transforms, merge)


def path_pattern(feature_id: str, body: Body, positions: list, merge: bool = True) -> Body:
    """Instances placed at given points -- a row that follows a drawn path.

    The positions come from the path itself (equal arc length along it), so the
    row re-spaces when the path changes rather than keeping coordinates that
    were right once.
    """
    if len(positions) < 2:
        raise CadError("bad_parameter", "a pattern needs at least two instances",
                       {"count": len(positions)})
    origin = positions[0]
    transforms = []
    for point in positions[1:]:
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(point[0] - origin[0], point[1] - origin[1],
                                   point[2] - origin[2]))
        transforms.append(trsf)
    return _repeat(feature_id, body, transforms, merge)


def circular_pattern(feature_id: str, body: Body, origin, direction, count: int,
                     angle_deg: float | None = None, merge: bool = True) -> Body:
    """A circular pattern about an axis -- a bolt circle, in one feature.

    With no angle the instances are spread evenly over a full turn, which is
    what "six bolts" means; give an angle to step by that much per instance
    instead (a gear passes 360/teeth -- the pitch, not the arc to fill).
    """
    n = int(count)
    if n < 2:
        raise CadError("bad_parameter", "a pattern needs at least two instances",
                       {"count": n})
    step = math.radians(float(angle_deg)) if angle_deg is not None else 2 * math.pi / n
    axis = gp_Ax1(gp_Pnt(*[float(v) for v in origin]),
                  gp_Dir(*[float(v) for v in direction]))
    transforms = []
    for k in range(1, n):
        trsf = gp_Trsf()
        trsf.SetRotation(axis, step * k)
        transforms.append(trsf)
    return _repeat(feature_id, body, transforms, merge)


