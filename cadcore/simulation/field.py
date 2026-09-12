"""The solved field, sampled at the CAD tessellation's vertices and
summarised per CAD face name, so the display lands on the geometry the user
selected from rather than on the FEM mesh.
"""
from __future__ import annotations

from dataclasses import dataclass


def by_volume(mesh, expr, fraction: float = 0.95) -> float:
    """The value of ``expr`` that ``fraction`` of the body's volume lies below.

    Each element counts by its volume, not by its number of nodes, so the
    answer does not move when the mesh is denser in one place than another.
    """
    import ngsolve as ng

    sums = ng.Integrate(expr, mesh, element_wise=True)
    volumes = ng.Integrate(ng.CoefficientFunction(1.0), mesh, element_wise=True)
    cells = sorted((float(s) / float(v), float(v)) for s, v in zip(sums, volumes)
                   if float(v) > 0)
    total = sum(v for _, v in cells)
    seen = 0.0
    for value, v in cells:
        seen += v
        if seen >= fraction * total:
            return value
    return cells[-1][0] if cells else 0.0


@dataclass
class Field:
    mesh: object
    von_mises: object                    # H1 GridFunction, MPa
    displacement: object                 # H1 GridFunction, mm
    solution: object                     # the vector solution
    #: Lower clip for sampled values, or None for no clip. Von Mises is a
    #: magnitude, so L2-projection undershoot below 0 is clipped; a temperature
    #: field must pass None or readings below 0 degrees are lost.
    floor: float | None = 0.0
    #: What the values are: `von_mises_MPa`, `temperature_C`, `mode_shape`.
    #: Carried by the field because the study kind is not enough (a thermal
    #: study with a fixed face returns a stress field).
    quantity: str | None = None

    def sample(self, points, normals=None, inset: float = 0.05) -> dict:
        """Field value at each point, stepped inside the solid along ``normals``.

        Boundary points are unreliable to look up, so each is offset inwards
        by ``inset`` and retried further in on a miss (fillets and thin walls
        can put the first offset outside). NGSolve raises for a whole batch if
        one point misses, so batches fall back to per-point lookups.
        """
        import numpy as np

        base = np.asarray(points, dtype=float)
        dirs = (np.asarray(normals, dtype=float) if normals is not None
                else np.zeros_like(base))
        values = np.full(len(base), np.nan)

        for step in (1.0, 3.0, 8.0):
            todo = np.flatnonzero(np.isnan(values))
            if not len(todo):
                break
            pts = base[todo] - dirs[todo] * inset * step
            for start in range(0, len(todo), 1024):
                idx = todo[start:start + 1024]
                block = pts[start:start + 1024]
                try:
                    raw = np.asarray(self.von_mises(
                        self.mesh(block[:, 0], block[:, 1], block[:, 2])), dtype=float)
                    values[idx] = raw.reshape(len(block), -1)[:, 0]
                except Exception:                                   # noqa: BLE001
                    for i, p in zip(idx, block):
                        try:
                            values[i] = float(self.von_mises(self.mesh(*p)))
                        except Exception:                           # noqa: BLE001
                            pass

        missed = int(np.isnan(values).sum())
        if self.floor is not None:
            values = np.clip(values, self.floor, None)
        values = np.nan_to_num(values)
        return {"values": [round(float(v), 4) for v in values], "missed": missed}

    def per_face(self, values, triangles, triangle_face, face_table, unit: str = "MPa") -> dict:
        """Peak and mean value per CAD face from the sampled vertices, keyed ``max_<unit>``."""
        import numpy as np

        values = np.asarray(values, dtype=float)
        out: dict[str, dict] = {}
        for tri, face in zip(triangles, triangle_face):
            name = face_table[face] if isinstance(face, int) else face
            v = values[list(tri)]
            slot = out.setdefault(name, {"max": float(v.max()), "sum": 0.0, "n": 0})
            slot["max"] = max(slot["max"], float(v.max()))      # a field below zero keeps its own peak
            slot["sum"] += float(v.sum())
            slot["n"] += len(v)
        return {name: {"max_%s" % unit: round(s["max"], 3),
                       "mean_%s" % unit: round(s["sum"] / s["n"], 3)}
                for name, s in out.items()}
