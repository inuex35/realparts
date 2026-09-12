"""Selectors: how a document refers to edges without naming OCCT indices.

A query is evaluated against the *current* body, so it re-resolves on every
rebuild. What gets stored in the document is the query, not the answer.
"""
from __future__ import annotations


from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_CurveType
from OCP.GProp import GProp_GProps

from ... import names
from ...errors import CadError
from .naming import Body


def _edge_length(edge) -> float:
    props = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, props)
    return props.Mass()


def _edge_direction(edge):
    ad = BRepAdaptor_Curve(edge)
    if ad.GetType() != GeomAbs_CurveType.GeomAbs_Line:
        return None
    d = ad.Line().Direction()
    return (d.X(), d.Y(), d.Z())


def _base(name: str) -> str:
    """``plate/+x@1`` -> ``plate/+x`` -- see :mod:`cadcore.names`."""
    return names.base(name)


QUERY_KEYS = frozenset({"between", "of_face", "parallel", "longer_than",
                        "shorter_than", "limit", "allow_empty"})
AXES = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}


def select_edges(body: Body, query: dict) -> list[str]:
    """Resolve an edge query to a list of edge names on ``body``.

    Supported keys (all optional, combined with AND):

    ``between``   two face names; keeps edges shared by exactly those faces
    ``of_face``   one face name; keeps every edge of that face
    ``parallel``  axis name (``x``/``y``/``z``); keeps straight edges along it
    ``longer_than`` / ``shorter_than``   length filters in mm
    ``limit``     keep at most N (after a deterministic sort)

    A key that is not one of those is refused. Ignoring it would be the worst
    kind of quiet: ``{"betwen": [...]}`` would apply no filter at all, return
    every edge on the body, and round them all -- reported as a success. A
    reference must never silently come to mean something else, and a mistyped
    query is a reference.
    """
    unknown = sorted(set(query) - QUERY_KEYS)
    if unknown:
        raise CadError("bad_query",
                       "edge query does not understand: " + ", ".join(unknown),
                       {"unknown": unknown, "understood": sorted(QUERY_KEYS)})

    table = body.edge_table()
    # not `names`: this module imports the naming vocabulary under that name,
    # and shadowing it here works only for as long as nobody in this function
    # needs to spell an edge
    found = sorted(table)

    if "between" in query:
        want = sorted(_base(body.canonical(f)) for f in query["between"])
        found = [n for n in found
                 if sorted(_base(p) for p in body.edge_owners(n)) == want]
    if "of_face" in query:
        face = _base(body.canonical(query["of_face"]))
        found = [n for n in found
                 if face in [_base(p) for p in body.edge_owners(n)]]
    if "parallel" in query:
        axis = AXES.get(str(query["parallel"]).lower())
        if axis is None:
            raise CadError("bad_query", "parallel takes an axis: x, y or z",
                           {"given": query["parallel"]})
        keep = []
        for n in found:
            d = _edge_direction(table[n])
            if d and abs(abs(sum(d[i] * axis[i] for i in range(3))) - 1.0) < 1e-6:
                keep.append(n)
        found = keep
    if "longer_than" in query:
        found = [n for n in found if _edge_length(table[n]) > float(query["longer_than"])]
    if "shorter_than" in query:
        found = [n for n in found if _edge_length(table[n]) < float(query["shorter_than"])]
    if "limit" in query:
        found = found[: int(query["limit"])]

    if not found and not query.get("allow_empty"):
        raise CadError("empty_selection", "edge query matched nothing",
                       {"query": query, "available_faces": body.face_names()})
    return found
