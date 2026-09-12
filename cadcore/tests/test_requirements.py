"""What the kernel install costs, checked against what is written down.

The size is on the store page and in the button a buyer presses, so a
dependency arriving upstream has to be noticed here rather than by the
person waiting for the download.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: packages nothing here imports, which have cost 674 MB by being pulled in
UNWANTED = {"vtk", "matplotlib"}


def _pinned(path: str) -> dict:
    """The `name==version` lines of a requirements file."""
    out = {}
    for line in (ROOT / path).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        m = re.match(r"^([A-Za-z0-9._-]+)\s*([=><].*)?$", line)
        if m and not line.startswith("-r"):
            out[m.group(1).lower()] = (m.group(2) or "").strip()
    return out


def test_the_kernel_asks_for_the_build_without_vtk():
    """`cadquery-ocp` requires vtk==9.6.2, and vtk requires matplotlib. The
    `-novtk` build is the same wrapper and the same OCCT without either."""
    named = _pinned("requirements.txt")
    assert "cadquery-ocp-novtk" in named, \
        "requirements.txt does not ask for the novtk build: " + ", ".join(named)
    assert "cadquery-ocp" not in named, "the vtk build is back in requirements.txt"


def test_nothing_here_imports_what_was_dropped():
    """If something starts importing vtk or matplotlib, the novtk build stops
    being the right one and this says so before a buyer finds out."""
    guilty = []
    for path in (ROOT / "cadcore").rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for name in UNWANTED:
            if re.search(r"^\s*(import %s|from %s)\b" % (name, name), text, re.M):
                guilty.append("%s imports %s" % (path.relative_to(ROOT), name))
    assert not guilty, "; ".join(guilty)
