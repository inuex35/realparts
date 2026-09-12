"""The semantic API. One entry point for the CLI, tests, and agents."""
from __future__ import annotations

from pathlib import Path

from ..geometry import kernel
from ..model.document import Document
from .graph import Evaluator
from ..errors import CadError
from ..geometry.core.naming import Body
from ..geometry.core.query import select_edges


def build(doc: Document, cache: dict | None = None,
          target: str | None = None) -> tuple[Body, Evaluator]:
    ev = Evaluator(doc, cache)
    return ev.build(target), ev


def build_file(path: str | Path, cache: dict | None = None):
    return build(Document.load(path), cache)


def export_step(body: Body, path: str | Path, name: str | None = None,
                looks: dict | None = None) -> None:
    kernel.export_step(body, str(path), name=name, looks=looks)


def describe(body: Body) -> dict:
    """What an agent needs to look at the result: names, counts, volume."""
    edges = body.edge_table()
    return {
        "kind": kernel.kind_of(body.shape),
        "volume_mm3": round(kernel.volume(body), 3),
        "area_mm2": round(kernel.area(body), 3),
        "open_boundaries": len(kernel.boundary_loops(body)),
        "faces": len(body.names),
        "edges": len(edges),
        "face_names": body.face_names(),
        "aliases": dict(body.aliases),
        "dropped": list(body.dropped),
        "edge_names": sorted(edges),
    }


__all__ = ["build", "build_file", "export_step", "describe", "select_edges",
           "Document", "CadError"]
