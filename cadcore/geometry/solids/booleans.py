"""Booleans, and the name inheritance that makes them usable.

A boolean splits, welds and rebuilds faces; OCCT's history (Modified,
IsDeleted) records what became what. This module carries names across it,
including split faces (positional suffixes) and welded faces (aliases).
"""
from __future__ import annotations

from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.TopoDS import TopoDS, TopoDS_Face

from ...errors import CadError
from ... import names
from ..core import provenance
from ..core.naming import Body, faces_of, role_names
from ..core.occ import bounds


def _split_key(face: TopoDS_Face):
    """Position key for ordering the pieces of a split face.

    OCCT returns the pieces in algorithm order, which changes with dimensions.
    Ordering by position in a frame from the face's own normal keeps ``@0``
    the same piece across rebuilds.
    """
    from ..core.naming import face_info
    info = face_info(face)
    c = info["centre"]
    n = info.get("normal") or info.get("axis") or (0.0, 0.0, 1.0)
    ref = min(((1, 0, 0), (0, 1, 0), (0, 0, 1)), key=lambda a: abs(sum(a[i] * n[i] for i in range(3))))
    u = (n[1] * ref[2] - n[2] * ref[1], n[2] * ref[0] - n[0] * ref[2], n[0] * ref[1] - n[1] * ref[0])
    v = (n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0])
    # position along the normal and area break ties: two halves of a cylinder
    # split along its axis have equal in-plane coordinates
    return (round(sum(c[i] * u[i] for i in range(3)), 6),
            round(sum(c[i] * v[i] for i in range(3)), 6),
            round(sum(c[i] * n[i] for i in range(3)), 6),
            round(info.get("area", 0.0), 6))


def _inherit(algo, inputs: list[Body], result_shape, feature_id: str,
             turned: tuple = ()) -> Body:
    """Carry names from the inputs onto the result, then name what is new.

    ``turned`` indexes the inputs whose faces end up facing the other way (the
    tool of a cut). Their axis roles are reversed, because a role is a claim
    about direction.
    """
    # provenance (bends, patch fit) is merged per kind by `provenance`, not
    # concatenated; ``joining=True`` because one shape comes out, so an entry
    # arriving from two inputs is a copy (a body mirrored onto itself would
    # otherwise carry each bend twice). ``dropped`` carries forward so a name
    # welded away by an earlier feature still reports as dropped, not unknown.
    out = Body(result_shape, [], {},
               list(dict.fromkeys(n for b in inputs for n in b.dropped)),
               provenance.merged(inputs, joining=True))
    claimed: dict = {}                     # hash -> claimed faces
    taken: set = set()                     # names already spoken for in the result
    live: dict = {}                        # hash -> the result's faces
    for f in faces_of(result_shape):
        live.setdefault(hash(f), []).append(f)

    def as_live(face):
        """The result's own instance of a face, with the shell's orientation.

        ``Modified()`` returns faces oriented as the algorithm holds them, not
        as they sit in the result shell; the shell orientation decides the
        outward normal, and so the face's role.
        """
        return next((f for f in live.get(hash(face), ()) if f.IsSame(face)), None)
    for index, body in enumerate(inputs):
        turn = names.turned_round if index in turned else (lambda n: n)
        for name, face in body.names:
            if algo.IsDeleted(face):
                continue
            modified = algo.Modified(face)
            targets = list(modified) if modified.Extent() else [face]
            targets = [as_live(TopoDS.Face_s(t)) for t in targets]
            targets = [t for t in targets if t is not None]
            if len(targets) > 1:
                targets.sort(key=_split_key)      # positional, not OCCT order
            for k, nf in enumerate(targets):
                carried = turn(name)
                candidate = carried if len(targets) == 1 else names.piece(carried, k)
                existing = out.name_of(nf)
                if existing is None:
                    candidate = names.unused(candidate, taken)
                    out.names.append((candidate, nf))
                    taken.add(candidate)
                    claimed.setdefault(hash(nf), []).append(nf)
                elif existing != candidate:
                    # the operation welded two parent faces into one: keep the
                    # first name and let the other resolve to it
                    out.aliases[candidate] = existing
    fresh = [f for f in faces_of(result_shape)
             if not any(f.IsSame(c) for c in claimed.get(hash(f), ()))]
    out.names.extend(role_names(feature_id, fresh))

    # aliases recorded by earlier features carry forward, so a name welded
    # away two operations ago still resolves
    for body in inputs:
        for alias, target in body.aliases.items():
            if out.face(alias) is not None or alias in out.aliases:
                continue
            if out.face(target) is not None:
                out.aliases[alias] = out.canonical(target)
                continue
            # a target that was itself split since: its pieces are its heirs
            heirs = [n for n, _ in out.names
                     if names.base(n) == target or n.startswith(target + names.PIECE)]
            if heirs:
                out.aliases[alias] = sorted(heirs)[0]

    # A name whose face did not survive as itself was usually welded into a
    # coplanar neighbour: alias it onto the result face on the same surface.
    # The face is looked up under the name it has here: a turned input's roles
    # are reversed (a drill's `t/-z` bottom becomes the pocket's `t/+z` floor),
    # and a renamed face is not a lost one. The old name is not aliased, since
    # it would resolve to a face pointing the other way.
    for index, body in enumerate(inputs):
        turn = names.turned_round if index in turned else (lambda n: n)
        for name, face in body.names:
            if name in out.aliases or out.face(turn(name)) is not None:
                continue
            survivor = _same_surface(face, out)
            if survivor:
                out.aliases[name] = survivor
            elif out.face(name) is None and name not in out.dropped:
                # only when nothing here answers to the name: a cut's turn can
                # make a consumed `t/+z` and a surviving `t/-z` both spell
                # `t/+z`, and a name that resolves is not dropped
                out.dropped.append(name)
    return out


def _covers(face: TopoDS_Face, point) -> bool:
    """Whether ``point`` lies within this face's bounding box, a little enlarged."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.gp import gp_Pnt

    box = Bnd_Box()
    BRepBndLib.Add_s(face, box, False)
    corners = bounds(box)
    size = max(corners[i + 3] - corners[i] for i in range(3))
    box.Enlarge(1e-3 * size + 1e-6)
    return not box.IsOut(gp_Pnt(*point))


def _same_surface(old_face: TopoDS_Face, out: Body) -> str | None:
    """A result face on the same surface as ``old_face`` and covering where it was.

    Same plane is not enough: a stepped part has two faces at one height, and
    a name welded away on one side must not come back on the other.
    """
    from ..core.naming import face_info
    a = face_info(old_face)
    for name, face in out.names:
        b = face_info(face)
        if a["type"] != b["type"] or not _covers(face, a["centre"]):
            continue
        if "normal" in a and "normal" in b:
            if any(abs(a["normal"][i] - b["normal"][i]) > 1e-6 for i in range(3)):
                continue
            n = a["normal"]
            if abs(sum((a["centre"][i] - b["centre"][i]) * n[i] for i in range(3))) > 1e-6:
                continue
            return name
        if "axis" in a and "axis" in b:
            # same radius is not enough: the axes must be the same line, or a
            # lost reference would resolve to a neighbouring hole
            if abs(a.get("radius", 0) - b.get("radius", -1)) > 1e-6:
                continue
            axis = a["axis"]
            if any(abs(axis[i] - b["axis"][i]) > 1e-6 for i in range(3)):
                continue
            delta = [b["centre"][i] - a["centre"][i] for i in range(3)]
            along = sum(delta[i] * axis[i] for i in range(3))
            perpendicular = sum((delta[i] - along * axis[i]) ** 2 for i in range(3)) ** 0.5
            if perpendicular > 1e-6:
                continue
            return name
    return None


def _one(shape):
    """A one-shape argument list, which is how the algorithm takes its input."""
    from ..core.occ import ListOfShape
    args = ListOfShape()
    args.Append(shape)
    return args


def _boolean(kind: str, feature_id: str, target: Body, tool: Body,
             fuzzy: float = 0.0, glue: bool = False) -> Body:
    """One boolean, with the names carried across.

    ``fuzzy`` widens the coincidence tolerance. Zero for ordinary shapes;
    raised only where the surfaces approximate each other (a swept helix
    against its cylinder), where the default returns an empty intersection.
    ``glue`` says the two shapes only touch or overlap along whole faces
    (a stack of blocks), which skips the intersection search.

    Arguments go through ``SetArguments``/``SetTools``: the two-shape
    constructor performs the operation itself, so ``Build`` would run it twice.
    """
    algo = {"cut": BRepAlgoAPI_Cut, "fuse": BRepAlgoAPI_Fuse,
            "common": BRepAlgoAPI_Common}[kind]()
    algo.SetArguments(_one(target.shape))
    algo.SetTools(_one(tool.shape))
    if fuzzy > 0:
        algo.SetFuzzyValue(float(fuzzy))
    if glue:
        from OCP.BOPAlgo import BOPAlgo_GlueEnum
        algo.SetGlue(BOPAlgo_GlueEnum.BOPAlgo_GlueShift)
    algo.Build()
    if not algo.IsDone():
        raise CadError("boolean_failed", f"{kind} did not complete")
    shape = algo.Shape()
    if not faces_of(shape):
        raise CadError("empty_result", f"{kind} produced no geometry",
                       {"hint": "the tool may not intersect the target"})
    # a cut turns the tool inside out, so its axis roles are reversed
    return _inherit(algo, [target, tool], shape, feature_id,
                    turned=(1,) if kind == "cut" else ())


def cut(feature_id, target, tool, fuzzy: float = 0.0, glue: bool = False):
    return _boolean("cut", feature_id, target, tool, fuzzy, glue)


def fuse(feature_id, target, tool, fuzzy: float = 0.0, glue: bool = False):
    return _boolean("fuse", feature_id, target, tool, fuzzy, glue)


def common(feature_id, target, tool, fuzzy: float = 0.0, glue: bool = False):
    return _boolean("common", feature_id, target, tool, fuzzy, glue)


