"""A line-delimited JSON server for the CAD core.

The core runs in its own process, separate from Blender's interpreter. One
JSON object per line in, one per line out; a reply carries the request's
`id`. The same protocol serves a UI, a test, or an agent.

    python -m cadcore.service.server            # speaks on stdin/stdout
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import traceback

from ..errors import CadError
from ..ops import workspace
from ..ops.session import Session

__all__ = ["Session", "handle", "main"]


def handle(session: Session, request: dict) -> dict:
    op = request.get("op")
    fn = getattr(session, f"op_{op}", None)
    if fn is None:
        return {"ok": False, "kind": "unknown_op", "message": f"no operation {op!r}",
                "detail": {"available": sorted(m[3:] for m in dir(session)
                                               if m.startswith("op_"))}}
    args = {k: v for k, v in request.items() if k not in ("op", "id")}
    # bad_arguments is decided by binding the signature before the call, so
    # a TypeError raised inside the operation is reported as a kernel bug.
    try:
        inspect.signature(fn).bind(**args)
    except TypeError as exc:
        return {"ok": False, "kind": "bad_arguments", "message": str(exc)}
    wrong = _wrong_types(fn, args)
    if wrong:
        return {"ok": False, "kind": "bad_arguments",
                "message": "; ".join(wrong), "detail": {"arguments": sorted(args)}}
    try:
        return {"ok": True, "result": fn(**args)}
    except CadError as exc:
        return {"ok": False, "kind": exc.kind, "message": exc.message, "detail": exc.detail}
    except (TypeError, ValueError) as exc:
        # an argument of the wrong shape that the signature could not describe
        return {"ok": False, "kind": "bad_arguments", "message": str(exc),
                "detail": {"arguments": sorted(args), "traceback": traceback.format_exc()[-600:]}}
    except Exception as exc:                                        # noqa: BLE001
        return {"ok": False, "kind": "internal_error", "message": str(exc),
                "detail": {"traceback": traceback.format_exc()[-600:]}}


_SIMPLE = {int: "an integer", float: "a number", str: "a string", bool: "true or false",
           list: "a list", dict: "an object"}


def _wrong_types(fn, args: dict) -> list:
    """Arguments whose value is not what the signature says, in words.

    Only the plain types are checked, which is what the published inputSchema
    says; int passes for float.
    """
    import types
    import typing

    try:
        hints = typing.get_type_hints(fn)
    except Exception:                                            # noqa: BLE001
        return []
    out = []
    for name, value in args.items():
        hint = hints.get(name)
        if hint is None or value is None:
            continue
        members = (typing.get_args(hint) if isinstance(hint, types.UnionType)
                   or typing.get_origin(hint) is typing.Union else (hint,))
        wanted = [typing.get_origin(m) or m for m in members if m is not type(None)]
        if not wanted or not all(w in _SIMPLE for w in wanted):
            continue                        # only the plain types are judged
        if float in wanted and isinstance(value, int) and not isinstance(value, bool):
            continue
        if not any(isinstance(value, w) for w in wanted):
            out.append("%s is %s, not %s" % (
                name, " or ".join(_SIMPLE[w] for w in wanted), type(value).__name__))
    return out


#: A longer line is refused as `bad_json` without parsing it. Fifty megabytes
#: is past any document and short of what would exhaust the parser.
MAX_LINE = 50 * 1024 * 1024


def _utf8(stream) -> None:
    """Read or write this stream as utf-8, replacing bad bytes rather than
    raising, so one bad line is answered as `bad_json` instead of ending the
    loop."""
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _parse(line: str):
    """The request in a line, or a `bad_json` reply saying why not."""
    if len(line) > MAX_LINE:
        return None, {"ok": False, "kind": "bad_json", "id": None,
                      "message": "a request line of %d bytes is not a request" % len(line),
                      "limit": MAX_LINE}
    try:
        return json.loads(line), None
    except ValueError as exc:            # JSONDecodeError, or an integer too long to convert
        return None, {"ok": False, "kind": "bad_json", "message": str(exc), "id": None}
    except RecursionError:
        return None, {"ok": False, "kind": "bad_json", "id": None,
                      "message": "that JSON is nested deeper than anything this reads"}


def _spelled(value):
    """What json.dumps cannot write (a Path, a set, a numpy scalar) goes out as text."""
    item = getattr(value, "item", None)      # a numpy scalar unwraps to a Python number
    if callable(item):
        try:
            return item()
        except Exception:                                       # noqa: BLE001
            pass
    if isinstance(value, (set, frozenset, tuple)):
        return sorted(value, key=str)
    return str(value)


def main() -> int:
    """Read requests from stdin, write replies to a private copy of stdout.

    OpenCASCADE writes to file descriptor 1 (a STEP export emits several
    lines), so the real stdout is duplicated for replies and fd 1 is pointed
    at stderr.
    """
    # utf-8 regardless of locale: `blender_addon.link.client` and
    # `cadcore.service.mcp` both speak utf-8.
    channel = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    # Fenced into a workspace root by default (see `cadcore.ops.workspace`).
    # `--unfenced` is for the Blender add-on, where paths come from a person's
    # file dialog (checked by tests/test_server.py).
    root = None if "--unfenced" in sys.argv else workspace.default_root()
    session = Session(root=root)
    _utf8(sys.stdin)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request, reply = _parse(line)
        if reply is not None:
            pass
        else:
            # valid JSON that is not an object (`[1]`, `42`, `null`) is
            # answered as bad_json rather than dispatched
            if not isinstance(request, dict):
                reply = {"ok": False, "kind": "bad_json",
                         "message": "a request is a JSON object",
                         "got": type(request).__name__, "id": None}
            else:
                reply = handle(session, request)
                reply["id"] = request.get("id")
        channel.write(json.dumps(reply, default=_spelled) + "\n")
        channel.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
