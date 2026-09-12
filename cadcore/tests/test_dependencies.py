"""Every third-party module the shipped code imports is declared somewhere.

`render` imported Pillow, which no requirements file named and no pinned
dependency pulled in: a fresh install had a tool that always failed. This
reads the imports off the source and the names off the requirements files.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parents[2]
#: import name -> the distribution that provides it
PROVIDES = {"OCP": "cadquery-ocp-novtk", "PIL": "pillow", "numpy": "numpy",
            "planegcs": "planegcs", "ngsolve": "ngsolve", "netgen": "ngsolve",
            "scipy": "scipy"}


def _declared() -> set:
    names = set()
    for path in HERE.glob("requirements*.txt"):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line and not line.startswith("-"):
                names.add(re.split(r"[<>=!~\[ ]", line, 1)[0].lower())
    return names


def _imported() -> dict:
    std = set(sys.stdlib_module_names)
    seen: dict = {}
    for path in (HERE / "cadcore").rglob("*.py"):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top not in std and top != "cadcore":
                    seen.setdefault(top, set()).add(str(path.relative_to(HERE)))
    return seen


def test_every_shipped_import_is_a_declared_dependency():
    declared = _declared()
    stray = {}
    for module, files in _imported().items():
        provider = PROVIDES.get(module)
        if provider is None or provider.lower() not in declared:
            stray[module] = sorted(files)[:3]
    assert not stray, "imported but declared nowhere: %s" % stray


def test_the_python_floor_is_what_the_pins_need():
    text = (HERE / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.12"' in text, "planegcs 0.8 needs 3.12"
