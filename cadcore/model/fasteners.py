"""Standard metric screw holes, so a hole can be asked for by name: "M6 tapped, counterbored".

ISO 273 clearance, ISO 4762 counterbores, ISO 10642 countersinks, as data.
A tapped hole is a cylinder at the tapping diameter plus a note; the helix
itself is ``"threaded": true`` (see :mod:`cadcore.geometry.solids.threads`).
"""
from __future__ import annotations

from ..errors import CadError

# size: pitch, tapping drill, clearance (close/normal/loose),
#       counterbore diameter and depth, countersink diameter (90 degrees)
METRIC = {
    "M3":  {"pitch": 0.50, "tap": 2.50, "close": 3.2, "normal": 3.4, "loose": 3.6,
            "cbore_d": 6.5,  "cbore_depth": 3.4,  "csink_d": 6.7},
    "M4":  {"pitch": 0.70, "tap": 3.30, "close": 4.3, "normal": 4.5, "loose": 4.8,
            "cbore_d": 8.0,  "cbore_depth": 4.4,  "csink_d": 9.0},
    "M5":  {"pitch": 0.80, "tap": 4.20, "close": 5.3, "normal": 5.5, "loose": 5.8,
            "cbore_d": 9.5,  "cbore_depth": 5.4,  "csink_d": 11.2},
    "M6":  {"pitch": 1.00, "tap": 5.00, "close": 6.4, "normal": 6.6, "loose": 7.0,
            "cbore_d": 11.0, "cbore_depth": 6.4,  "csink_d": 13.4},
    "M8":  {"pitch": 1.25, "tap": 6.80, "close": 8.4, "normal": 9.0, "loose": 10.0,
            "cbore_d": 14.0, "cbore_depth": 8.6,  "csink_d": 17.9},
    "M10": {"pitch": 1.50, "tap": 8.50, "close": 10.5, "normal": 11.0, "loose": 12.0,
            "cbore_d": 17.5, "cbore_depth": 10.8, "csink_d": 22.4},
    "M12": {"pitch": 1.75, "tap": 10.20, "close": 13.0, "normal": 13.5, "loose": 14.5,
            "cbore_d": 20.0, "cbore_depth": 13.0, "csink_d": 26.9},
}

FITS = ("tapped", "close", "normal", "loose")
SEATS = ("none", "counterbore", "countersink")


def resolve(standard: str, fit: str = "normal", seat: str = "none") -> dict:
    """The geometry of a standard hole, plus the note that describes it."""
    size = METRIC.get(standard.upper())
    if size is None:
        raise CadError("unknown_fastener", f"no standard hole called {standard!r}",
                       {"available": sorted(METRIC)})
    if fit not in FITS:
        raise CadError("bad_arguments", f"fit must be one of {FITS}", {"given": fit})
    if seat not in SEATS:
        raise CadError("bad_arguments", f"seat must be one of {SEATS}", {"given": seat})

    diameter = size["tap"] if fit == "tapped" else size[fit]
    out = {"diameter": diameter,
           "thread": (f"{standard.upper()}x{size['pitch']:g}" if fit == "tapped"
                      else f"{standard.upper()} {fit} clearance"),
           "note": (f"{standard.upper()}x{size['pitch']:g} - 6H" if fit == "tapped"
                    else f"⌀{diameter:g} ({standard.upper()} clearance)")}
    if seat == "counterbore":
        out["counterbore"] = {"diameter": size["cbore_d"], "depth": size["cbore_depth"]}
        out["note"] += f", ⌀{size['cbore_d']:g} c'bore {size['cbore_depth']:g} deep"
    elif seat == "countersink":
        out["countersink"] = {"diameter": size["csink_d"], "angle": 90}
        out["note"] += f", ⌀{size['csink_d']:g} c'sink 90°"
    return out


# the parts that go in the holes above, by the same size key. Socket head:
# ISO 4762 (head diameter, head height); hex bolt: ISO 4017 (across flats,
# head height); nut: ISO 4032 (across flats, height); washer: ISO 7089
# (inner, outer, thickness). All mm.
PARTS = {
    "M3":  {"socket_head": (5.5, 3.0),  "hex_bolt": (5.5, 2.0), "nut": (5.5, 2.4),
            "washer": (3.2, 7.0, 0.5)},
    "M4":  {"socket_head": (7.0, 4.0),  "hex_bolt": (7.0, 2.8), "nut": (7.0, 3.2),
            "washer": (4.3, 9.0, 0.8)},
    "M5":  {"socket_head": (8.5, 5.0),  "hex_bolt": (8.0, 3.5), "nut": (8.0, 4.7),
            "washer": (5.3, 10.0, 1.0)},
    "M6":  {"socket_head": (10.0, 6.0), "hex_bolt": (10.0, 4.0), "nut": (10.0, 5.2),
            "washer": (6.4, 12.0, 1.6)},
    "M8":  {"socket_head": (13.0, 8.0), "hex_bolt": (13.0, 5.3), "nut": (13.0, 6.8),
            "washer": (8.4, 16.0, 1.6)},
    "M10": {"socket_head": (16.0, 10.0), "hex_bolt": (16.0, 6.4), "nut": (16.0, 8.4),
            "washer": (10.5, 20.0, 2.0)},
    "M12": {"socket_head": (18.0, 12.0), "hex_bolt": (18.0, 7.5), "nut": (18.0, 10.8),
            "washer": (13.0, 24.0, 2.5)},
}

KINDS = ("socket_head", "hex_bolt", "nut", "washer")


def part(standard: str, kind: str = "socket_head", length: float | None = None) -> dict:
    """The sizes of a standard part, and the words a bill of materials calls it by.

    A screw's ``length`` is the shank under the head; left out, it is four
    times the nominal diameter.
    """
    size = METRIC.get(standard.upper())
    if size is None:
        raise CadError("unknown_fastener", f"no standard part called {standard!r}",
                       {"available": sorted(METRIC)})
    if kind not in KINDS:
        raise CadError("unknown_fastener", f"no fastener kind called {kind!r}",
                       {"available": list(KINDS)})
    nominal = float(standard[1:])
    dims = PARTS[standard.upper()][kind]
    out = {"standard": standard.upper(), "kind": kind, "nominal": nominal,
           "pitch": size["pitch"]}
    if kind in ("socket_head", "hex_bolt"):
        out["length"] = float(length) if length else 4 * nominal
        out["head_width"], out["head_height"] = dims
        out["name"] = "%s x %g %s" % (out["standard"], out["length"],
                                      "socket head cap screw" if kind == "socket_head"
                                      else "hex bolt")
    elif kind == "nut":
        out["across_flats"], out["height"] = dims
        out["name"] = f"{out['standard']} hex nut"
    else:
        out["inner"], out["outer"], out["thickness"] = dims
        out["name"] = f"{out['standard']} washer"
    return out
