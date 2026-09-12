"""Record what every shipped example measures, and compare on every run.

This is not a set of assertions about what is right: it is the answer the
repository gives today, committed, so geometry cannot change silently. When
a measurement changes, the diff says which document and by how much, and
someone decides whether that was the point of the change.

Regenerate after a deliberate change and read the diff before committing:

    python cadcore/evaluation/tests/golden.py --write
    git diff cadcore/evaluation/tests/golden.json

Per document: volume and area (size), bounding box (position and
orientation), face and edge counts (how it is divided), every face name (the
naming claim), and for a document with a drawing, each view's box and its
visible, hidden and hatch line counts (which side the eye is on).
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
GOLDEN = pathlib.Path(__file__).resolve().parent / "golden.json"

# Sub-documents an assembly imports are measured through the assembly.
SKIP = {"examples/parts/bush.json", "examples/parts/link.json"}


def _shipped() -> list[str] | None:
    """The examples git tracks under `examples/`, or None if git cannot say.

    Asked of git rather than the filesystem so untracked drafts in `examples/`
    do not fail the covering check. A new example is tracked once `git add`ed.
    """
    import subprocess

    try:
        listed = subprocess.run(["git", "ls-files", "-z", "--", "examples"],
                                cwd=ROOT, capture_output=True, text=True,
                                timeout=30, check=True, encoding="utf-8").stdout
    except (OSError, subprocess.SubprocessError):
        return None                       # no git, or not a checkout: glob instead
    found = [p for p in listed.split("\0")
             if p.endswith(".json") and not p.endswith(".autosave.json")]
    return sorted(found) or None


def documents() -> list[str]:
    """Every example, in a fixed order so the file's diff is readable."""
    found = _shipped()
    if found is None:
        found = sorted(str(p.relative_to(ROOT)).replace("\\", "/")
                       for p in (ROOT / "examples").rglob("*.json")
                       if not p.name.endswith(".autosave.json"))
    return [p for p in found if p not in SKIP]


def _bounds(box):
    from cadcore.geometry.core.occ import bounds

    return bounds(box)


def measure(path: str) -> dict:
    """One document's answer, rounded to what a rebuild can reproduce."""
    from cadcore.evaluation.api import build, describe

    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from cadcore.model.document import Document

    doc = Document.load(str(ROOT / path))
    body, _ = build(doc)
    info = describe(body)
    box = Bnd_Box()
    # AddOptimal, not Add: `Add_s` returns the tolerance-inflated box, which
    # moves when a boolean changes the tolerance rather than the geometry.
    BRepBndLib.AddOptimal_s(body.shape, box)
    out = {
        "kind": info["kind"],
        "volume_mm3": round(info["volume_mm3"], 3),
        "area_mm2": round(info["area_mm2"], 3),
        "bounds_mm": [round(v, 3) for v in _bounds(box)],
        "faces": info["faces"],
        "edges": info["edges"],
        "face_names": sorted(info["face_names"]),
    }
    if doc.drawing:
        out["drawing"] = _views(body, doc.drawing)
    return out


def _views(body, spec: dict) -> dict:
    """Each declared view: its bounds and visible, hidden and hatch line counts.

    Counts rather than the lines themselves: the lines would swamp the file,
    and a view taken from the wrong side shows up as a changed split.
    """
    from cadcore.analysis.drawing import project, section

    sections = spec.get("sections") or {}
    out = {}
    for name in spec.get("views", []):
        view = (section(body, name, float(sections[name])) if name in sections
                else project(body, name, hidden=True))
        out[name] = {"bounds": [round(v, 3) for v in view.bounds],
                     "visible": len(view.visible), "hidden": len(view.hidden),
                     "hatch": len(view.hatch)}
    return out


def collect() -> dict:
    return {path: measure(path) for path in documents()}


def load() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def main() -> int:
    if "--write" not in sys.argv:
        print(json.dumps(collect(), indent=2))
        return 0
    GOLDEN.write_text(json.dumps(collect(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote %s for %d documents" % (GOLDEN.name, len(documents())))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
