"""Every text-mode file the shipped code reads or writes passes `encoding=`.

The locale encoding is not utf-8 everywhere (cp932 on Japanese Windows) and
dimension text carries a diameter sign, so the default breaks exports, notes
and the kernel pipe. The rule is held statically over the shipped code:
text-mode `open`, `read_text`, `write_text` and text-mode subprocess pipes
must say `encoding=`.
"""
from __future__ import annotations

import ast
import os
import pathlib

HERE = pathlib.Path(__file__).resolve().parents[2]
SHIPPED = ("cadcore", "blender_addon", "tools")

TEXT_METHODS = {"read_text", "write_text"}
BINARY = {"rb", "wb", "ab", "r+b", "w+b", "rb+", "wb+"}


def _mode_of(call: ast.Call) -> str | None:
    """The literal mode of an `open(...)`, if it has one, else 'r'."""
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        return str(call.args[1].value)
    return "r"


def _has(call: ast.Call, name: str) -> bool:
    return any(kw.arg == name for kw in call.keywords)


def _offenders(path: pathlib.Path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        # open(...) and os.fdopen(...)
        if (isinstance(fn, ast.Name) and fn.id == "open") or (
                isinstance(fn, ast.Attribute) and fn.attr == "fdopen"):
            mode = _mode_of(node)
            if "b" not in mode and not _has(node, "encoding"):
                out.append((node.lineno, "open() in text mode without encoding="))
        # Path.read_text / write_text
        elif isinstance(fn, ast.Attribute) and fn.attr in TEXT_METHODS:
            if not _has(node, "encoding"):
                out.append((node.lineno, fn.attr + "() without encoding="))
        # subprocess.Popen / run in text mode
        elif isinstance(fn, ast.Attribute) and fn.attr in ("Popen", "run"):
            text = any(kw.arg in ("text", "universal_newlines")
                       and isinstance(kw.value, ast.Constant) and kw.value.value
                       for kw in node.keywords)
            if text and not _has(node, "encoding"):
                out.append((node.lineno, "subprocess in text mode without encoding="))
    return out


def test_every_text_file_is_utf8_by_name():
    found = []
    for top in SHIPPED:
        for root, dirs, files in os.walk(HERE / top):
            # tests too: the GUI is checked on a cp932 Windows, where a test
            # that reads a file without saying utf-8 is the test that fails
            for name in files:
                if name.endswith(".py"):
                    path = pathlib.Path(root) / name
                    for line, what in _offenders(path):
                        found.append("%s:%d %s" % (path.relative_to(HERE), line, what))
    assert not found, "\n".join(found)
