"""Will it come out of the mould: draft angles and undercuts, per CAD face name.

Every face is sampled on its tessellation; a sample's draft is the angle
between the face and the pull direction, and a sample is an undercut when
material stands in the way of pulling it out on its own side.
"""
from __future__ import annotations

import math

from ..geometry.core.measure import first_hit
from ..geometry.io.tessellate import tessellate
from .printability import _triangle_area_and_centre


def check(body, direction=(0.0, 0.0, 1.0), min_angle: float = 1.0,
          deflection: float = 0.3, undercut_samples: int = 400) -> dict:
    """Draft per face, faces that need more, and undercuts, for a pull along ``direction``.

    A face is on the ``+`` side when it looks along the pull, ``-`` against
    it, ``both`` when it curves through; ``draft_deg`` is the least draft
    found on it. An undercut is a sample a straight pull would drag through
    the part.
    """
    d = _unit(direction)
    mesh = tessellate(body, deflection, 0.4)
    vertices, triangles, names = mesh["vertices"], mesh["triangles"], mesh["triangle_face"]
    analytic = mesh.get("normals") or []
    faces: dict = {}
    samples: list = []
    for tri, name in zip(triangles, names):
        area, centre, normal = _triangle_area_and_centre(*(vertices[i] for i in tri))
        if area <= 0:
            continue
        if analytic:
            averaged = [sum(analytic[i][k] for i in tri) / 3 for k in range(3)]
            length = math.sqrt(sum(c * c for c in averaged))
            if length > 1e-9:
                normal = [c / length for c in averaged]
        facing = sum(normal[i] * d[i] for i in range(3))
        draft = math.degrees(math.asin(max(-1.0, min(1.0, abs(facing)))))
        slot = faces.setdefault(name, {"face": name, "area": 0.0, "draft_deg": None,
                                       "sides": set(), "undercut_mm2": 0.0})
        slot["area"] += area
        slot["sides"].add("+" if facing >= 0 else "-")
        if slot["draft_deg"] is None or draft < slot["draft_deg"]:
            slot["draft_deg"] = draft
        samples.append((name, centre, normal, area, facing))

    step = max(1, len(samples) // max(1, undercut_samples))
    for name, centre, normal, area, facing in samples[::step]:
        if abs(facing) < 1e-6:
            continue
        pull = d if facing > 0 else [-c for c in d]
        start = [centre[i] + normal[i] * 1e-3 for i in range(3)]
        if first_hit(body.shape, start, pull, minimum=1e-3, crossing=0.05) is not None:
            faces[name]["undercut_mm2"] += area * step

    report = []
    for name in sorted(faces):
        f = faces[name]
        report.append({"face": name, "area_mm2": round(f["area"], 2),
                       "draft_deg": round(f["draft_deg"], 2),
                       "side": "both" if len(f["sides"]) == 2 else next(iter(f["sides"])),
                       "undercut_mm2": round(f["undercut_mm2"], 2)})
    short = [r for r in report if r["draft_deg"] < min_angle and r["side"] != "both"
             and r["draft_deg"] < 89.0]
    undercuts = [r for r in report if r["undercut_mm2"] > 1e-6]
    return {"pull_direction": [round(c, 6) for c in d], "min_draft_deg": float(min_angle),
            "faces": report,
            "needs_draft": [r["face"] for r in short],
            "undercuts": [{"face": r["face"], "undercut_mm2": r["undercut_mm2"]} for r in undercuts],
            "ok": not short and not undercuts}


def _unit(v) -> list:
    length = math.sqrt(sum(float(c) * float(c) for c in v)) or 1.0
    return [float(c) / length for c in v]
