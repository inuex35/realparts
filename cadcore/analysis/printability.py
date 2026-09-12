"""Printability checks, reported per CAD face name so a finding points at the
feature that made it.

Three checks: overhang (downward-facing area the printer cannot bridge), wall
thickness (a ray shot from the surface into the solid) and small holes (a bore
narrower than the limit). Lengths are in mm, angles in degrees.
"""
from __future__ import annotations

import math

from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

from ..errors import CadError
from ..geometry.core.measure import crossing_angle
from ..geometry.core.naming import face_info, is_hole, is_round
from ..geometry.io.tessellate import tessellate


def _triangle_area_and_centre(a, b, c) -> tuple:
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    length = math.sqrt(sum(x * x for x in n))
    if length < 1e-12:
        return 0.0, None, None
    centre = [(a[i] + b[i] + c[i]) / 3 for i in range(3)]
    return length / 2, centre, [x / length for x in n]


def check(body, up=(0.0, 0.0, 1.0), overhang_deg: float = 45.0,
          min_wall: float = 1.2, min_hole: float = 2.0,
          deflection: float = 0.3, wall_samples: int = 400) -> dict:
    """Run the three checks and group the findings by CAD face name.

    A downward face tilted less than ``overhang_deg`` from the build plate is
    an overhang; ``min_wall`` and ``min_hole`` are in mm.
    """
    up = _unit(up)
    mesh = tessellate(body, deflection, 0.4)
    vertices, triangles, names = mesh["vertices"], mesh["triangles"], mesh["triangle_face"]
    analytic = mesh.get("normals") or []
    if not triangles:
        raise CadError("empty_mesh", "the body tessellated to nothing")

    limit = math.cos(math.radians(overhang_deg))
    # a face lying on the build plate is the first layer, not an overhang
    heights = [sum(v[i] * up[i] for i in range(3)) for v in vertices]
    bed = min(heights) if heights else 0.0
    faces: dict = {}
    samples: list = []
    bed_area = 0.0
    for tri, name in zip(triangles, names):
        area, centre, normal = _triangle_area_and_centre(*(vertices[i] for i in tri))
        if area <= 0:
            continue
        # prefer the tessellation's analytic normals: triangle winding is not a
        # reliable statement of which way is out
        if analytic:
            averaged = [sum(analytic[i][k] for i in tri) / 3 for k in range(3)]
            length = math.sqrt(sum(c * c for c in averaged))
            if length > 1e-9:
                normal = [c / length for c in averaged]
        slot = faces.setdefault(name, {"area": 0.0, "overhang_area": 0.0,
                                       "steepest_deg": None})
        slot["area"] += area
        facing = sum(normal[i] * up[i] for i in range(3))
        on_bed = (facing < -0.999 and
                  sum(centre[i] * up[i] for i in range(3)) <= bed + 0.05)
        if on_bed:
            bed_area += area
        elif facing < -limit:                   # points down, and steeply enough
            slot["overhang_area"] += area
            angle = math.degrees(math.asin(min(1.0, -facing)))
            tilt = 90.0 - angle                 # from the build plate
            if slot["steepest_deg"] is None or tilt < slot["steepest_deg"]:
                slot["steepest_deg"] = round(tilt, 1)
        samples.append((name, centre, normal, area))

    walls = _thickness(body, samples, min_wall, wall_samples)
    holes = _small_holes(body, min_hole)

    overhangs = [{"face": name, "unsupported_mm2": round(f["overhang_area"], 2),
                  "shallowest_deg": f["steepest_deg"]}
                 for name, f in sorted(faces.items()) if f["overhang_area"] > 1e-6]
    total = sum(f["area"] for f in faces.values())
    unsupported = sum(f["overhang_area"] for f in faces.values())
    return {
        "build_direction": list(up),
        "overhang_limit_deg": overhang_deg,
        "surface_mm2": round(total, 1),
        "on_the_bed_mm2": round(bed_area, 1),
        "unsupported_mm2": round(unsupported, 1),
        "unsupported_fraction": round(unsupported / total, 4) if total else 0.0,
        "overhangs": overhangs,
        "thin_walls": walls,
        "small_holes": holes,
        "ok": not overhangs and not walls and not holes,
    }


def _thickness(body, samples: list, minimum: float, wanted: int) -> list:
    """Measure the wall at sampled points by shooting a ray into the solid."""
    if not samples:
        return []
    step = max(1, len(samples) // max(1, wanted))
    intersector = BRepIntCurveSurface_Inter()
    thin: dict = {}
    for name, centre, normal, area in samples[::step]:
        # start slightly inside the wall so the surface the ray leaves is not
        # counted as a wall it crossed
        start = [centre[i] - normal[i] * 1e-3 for i in range(3)]
        line = gp_Lin(gp_Pnt(*start), gp_Dir(*[-c for c in normal]))
        try:
            intersector.Init(body.shape, line, 1e-6)
        except Exception:                                       # noqa: BLE001
            continue
        direction = [-c for c in normal]
        best = None
        while intersector.More():
            w = intersector.W()
            # count only faces the ray goes through (crossing angle > 0.2 rad):
            # a grazing hit near a fillet would read as a 0.01 mm wall
            angle = crossing_angle(intersector, direction)
            if w > 0.02 and (angle is None or angle > 0.2):
                if best is None or w < best:
                    best = w
            intersector.Next()
        if best is not None and best < minimum:
            hit = thin.setdefault(name, {"face": name, "thinnest_mm": best,
                                         "samples": 0})
            hit["thinnest_mm"] = min(hit["thinnest_mm"], best)
            hit["samples"] += 1
    return [{**v, "thinnest_mm": round(v["thinnest_mm"], 3)}
            for v in sorted(thin.values(), key=lambda h: h["thinnest_mm"])]


def _small_holes(body, minimum: float) -> list:
    """Cylindrical faces that curve inward -- holes -- narrower than the limit."""
    out = []
    for name, face in body.names:
        info = face_info(face)
        if not is_round(info):
            continue
        radius = info.get("radius", 0.0)
        if radius <= 0 or radius * 2 >= minimum:
            continue
        if not is_hole(face):
            continue                            # a thin pin is a different problem
        out.append({"face": name, "diameter_mm": round(radius * 2, 3)})
    return sorted(out, key=lambda h: h["diameter_mm"])


def _unit(v) -> list:
    length = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / length for c in v]
