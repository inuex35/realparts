"""Score a produced part against a spec, with the kernel and nothing else.

The point of a gauntlet is that nobody reads the picture. A spec says what the
part must be -- a solid, this big, this heavy, these holes, printable, strong
enough -- and each of those is a question the kernel answers the same way for
a document it built and for a STEP another tool wrote. So the two arms of the
comparison are scored by one function, and the function cannot tell them apart.
"""
from __future__ import annotations

import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def _bbox(body) -> dict:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.Add_s(body.shape, box, False)
    x0, y0, z0, x1, y1, z1 = box.Get()
    return {"x": x1 - x0, "y": y1 - y0, "z": z1 - z0}


def _within(value, lo, hi) -> bool:
    return lo <= value <= hi


def score(path: str, checks: list) -> dict:
    """Every check, and whether the part passed it -- never an exception.

    A part that will not even open scores zero on everything, which is the
    right score for it.
    """
    from cadcore.ops.session import Session

    out = {"opened": False, "checks": [], "passed": 0, "total": len(checks)}
    session = Session(autosave=False)
    try:
        session.op_open(path=path)
        built = session.op_build()
    except Exception as exc:                                     # noqa: BLE001
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
        out["checks"] = [{"check": c, "ok": False, "got": "did not open"} for c in checks]
        return out
    out["opened"] = True
    body = session.body
    faces = session.op_describe_faces()["faces"]
    for check in checks:
        kind = check["type"]
        ok, got = False, None
        try:
            if kind == "solid":
                got = {"kind": built["kind"], "open_boundaries": built["open_boundaries"]}
                ok = built["kind"] == "solid" and built["open_boundaries"] == 0
            elif kind == "volume":
                got = round(built["volume_mm3"], 1)
                ok = _within(got, *check["between"])
            elif kind == "bbox":
                got = {k: round(v, 2) for k, v in _bbox(body).items()}
                # sorted extents, so that a part built along another axis is
                # not failed for the choice: a shaft is a shaft lying down
                want = sorted(check["extents"])
                have = sorted(got.values())
                tol = check.get("tol", 0.5)
                ok = all(abs(a - b) <= tol for a, b in zip(want, have))
            elif kind == "bbox_range":
                got = {k: round(v, 2) for k, v in _bbox(body).items()}
                have = sorted(got.values())
                ok = all(lo <= v <= hi for v, (lo, hi) in zip(have, check["extents"]))
            elif kind == "cylinders":
                r, tol = check["radius"], check.get("tol", 0.1)
                found = [f for f in faces if f.get("shape") == "cylinder"
                         and f.get("radius_mm") is not None
                         and abs(f["radius_mm"] - r) <= tol]
                got = len(found)
                want = check.get("count")
                ok = got == want if want is not None else got >= check.get("at_least", 1)
            elif kind == "printable":
                p = session.op_printability(min_wall=check.get("min_wall", 1.5))
                got = {k: p[k] for k in ("ok", "unsupported_mm2", "thin_walls")}
                ok = bool(p["ok"])
            elif kind == "fem":
                # stand it on its lowest flat face, push on its highest one:
                # the sanity check a designer does first, by name of no face
                # "cantilever": the two end faces along the part's longest
                # axis -- fixed at one end, pushed at the other
                if check.get("mode") == "cantilever":
                    ext = _bbox(body)
                    axis = max(range(3), key=lambda i: ext["xyz"[i]])
                else:
                    axis = 2
                planes = []
                for f in faces:
                    n = f.get("normal")
                    if f.get("shape") == "plane" and n and abs(n[axis]) > 0.99:
                        c = f.get("centre") or session.op_face_frame(face=f["name"])["origin"]
                        planes.append((c[axis], f["area_mm2"], f["name"]))
                planes.sort()
                fix, load = planes[0][2], planes[-1][2]
                doc = session.op_document_json()["document"]
                doc["studies"] = [{"id": "gauntlet", "type": "static_structural",
                                   "material": check.get("material", "A6061"),
                                   "fix": [fix],
                                   "loads": [{"face": load, "force": check["force"]}]}]
                session.op_load_json(document=doc)
                r = session.op_simulate()["studies"][0]["result"]
                got = {"fix": fix, "load": load, "safety_factor": r["safety_factor"],
                       "p95_MPa": r["p95_von_mises_MPa"],
                       "max_displacement_mm": r["max_displacement_mm"]}
                ok = r["safety_factor"] >= check["sf_min"]
                if "max_displacement" in check:
                    ok = ok and r["max_displacement_mm"] <= check["max_displacement"]
            else:
                got = "unknown check type"
        except Exception as exc:                                 # noqa: BLE001
            got = "%s: %s" % (type(exc).__name__, str(exc)[:160])
        out["checks"].append({"check": check, "ok": bool(ok), "got": got})
        out["passed"] += int(bool(ok))
    return out


if __name__ == "__main__":
    import json

    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    print(json.dumps(score(sys.argv[2], spec["checks"]), indent=1, default=str))
