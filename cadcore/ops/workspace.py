"""Where a session is allowed to read and write, and what each operation does.

A served session has a root and every path is resolved inside it, before
comparing, so `..` and a planted symlink come out where they really point.
A library session has no root and is not confined.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..errors import CadError

#: What an operation does outside the document. Anything not named here does
#: nothing outside it -- and a test asserts that every `op_` either appears
#: here or takes no path, so a new one cannot quietly get a file to itself.
TOUCHES: dict[str, str] = {
    "open": "read",
    "save": "write",
    "export_step": "write",
    "export_mesh": "write",
    "drawing": "write",
    "flat_dxf": "write",
    "render": "write",
}


def touches(op: str) -> str | None:
    """``"read"``, ``"write"`` or None -- what this operation does to files."""
    return TOUCHES.get(op)


def inside(root, path, writing: bool) -> str:
    """`path` resolved under `root`, or a refusal.

    A relative path is relative to the root, which is what makes "save it as
    bracket.step" mean something. An absolute one has to already be under it.

    Resolved before comparing, so `../../etc/passwd` and a symlink planted in
    the root both come out where they really point. For a write the parent is
    resolved rather than the file, because the file is not there yet.
    """
    if root is None:
        return str(path)
    base = Path(root).resolve()
    asked = Path(path)
    full = asked if asked.is_absolute() else base / asked
    try:
        # for a write the file may not exist yet, so its parent is resolved and
        # its name kept -- unless something *is* there already, in which case
        # that thing is resolved whole: a symlink planted in the root pointing
        # at a file outside it was a door, because the parent was inside and
        # the name was innocent
        if writing and not (full.is_symlink() or full.exists()):
            settled = full.parent.resolve() / full.name
        else:
            settled = full.resolve()
    except OSError as exc:
        raise CadError("bad_path", f"cannot resolve {str(path)!r}",
                       {"path": str(path), "error": str(exc)}) from exc
    if base != settled and base not in settled.parents:
        raise CadError(
            "bad_path",
            f"{str(path)!r} is outside this session's workspace",
            {"path": str(path), "workspace": str(base),
             "hint": "a served session may only read and write under its root; "
                     "pass a path inside it, or start the session elsewhere"})
    return str(settled)


def default_root() -> str:
    """Where a served session works when nobody said: the current directory."""
    return os.environ.get("CAD_WORKSPACE") or os.getcwd()
