"""The kernel as an MCP server, so an assistant can drive the CAD directly.

    python -m cadcore.service.mcp                 # speaks MCP on stdin/stdout
"""
from __future__ import annotations

import base64
import json
import os
import sys

from .. import progress
from . import resources
from ..ops import catalogue, workspace
from ..ops.catalogue import INSTRUCTIONS  # noqa: F401
from .server import handle
from ..ops.session import Session

#: The protocol revision this speaks. A client that names a revision is
#: answered in it; the methods used here are unchanged across revisions.
PROTOCOL = "2024-11-05"

#: Tools the viewport answers itself when the server is attached to Blender.
#: Not read off `Session`: the selection lives on screen, not in the kernel.
VIEWPORT_TOOLS = [
    {"name": "selection",
     "description": "What the person has selected in Blender, by CAD face and "
                    "edge name. Ask this when they say 'this face' or 'here'.",
     "touches": None,
     "annotations": {"readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "select",
     "description": "Select faces in Blender by name, so the person sees which "
                    "faces you mean before you change them. Replaces the "
                    "selection unless add is true.",
     "touches": None,
     "annotations": {"readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
     "inputSchema": {"type": "object",
                     "properties": {"faces": {"type": "array",
                                              "items": {"type": "string"},
                                              "description": "face names"},
                                    "add": {"type": "boolean", "default": False}},
                     "required": ["faces"]}},
]


def tools(session) -> list:
    """Every operation, as a tool; plus the viewport's own when attached to Blender."""
    out = catalogue.tools(Session)
    if isinstance(session, Attached):
        out += VIEWPORT_TOOLS
    return out


def _text(payload) -> dict:
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, indent=1, default=str)}]}


def _with_picture(payload) -> dict:
    """A render, handed back as an image beside the text, not only as a path."""
    answer = _text(payload)
    try:
        with open(payload["path"], "rb") as file:
            data = base64.b64encode(file.read()).decode("ascii")
    except (OSError, KeyError, TypeError):
        return answer                     # the path stands on its own
    answer["content"].append({"type": "image", "data": data,
                              "mimeType": "image/png"})
    return answer


def _reporting(params: dict, notify):
    """A progress sink for this call, or None if the client offered no
    `progressToken`. MCP sends progress only when the client asked for it."""
    token = ((params.get("_meta") or {}).get("progressToken"))
    if token is None or notify is None:
        return None

    def send(done, total, message):
        told = {"progressToken": token, "progress": done, "message": message}
        if total is not None:
            told["total"] = total
        notify({"jsonrpc": "2.0", "method": "notifications/progress",
                "params": told})
    return send


def respond(session: Session, request: dict, notify=None):
    """One MCP request in, one response out, or None for a notification.

    `notify` writes a JSON-RPC notification to the client while the call
    runs; only `simulate` and `optimize` use it, and only when asked.
    """
    method = request.get("method")
    ident = request.get("id")

    if method == "initialize":
        asked = (request.get("params") or {}).get("protocolVersion")
        return {"protocolVersion": asked or PROTOCOL,
                "capabilities": {"tools": {"listChanged": False},
                                 "resources": {"listChanged": False,
                                               "subscribe": False}},
                "serverInfo": {"name": "cadcore", "version": "1"},
                "instructions": INSTRUCTIONS}
    if method == "tools/list":
        return {"tools": tools(session)}
    if method == "resources/list":
        return {"resources": resources.catalogue()}
    if method == "resources/templates/list":
        # no resource is parameterised; `[]` rather than "no such method"
        # because a client asking this is checking capabilities
        return {"resourceTemplates": []}
    if method == "resources/read":
        uri = (request.get("params") or {}).get("uri")
        try:
            return {"contents": [resources.read(uri)]}
        except KeyError:
            raise LookupError("resources/read %r; this server offers %s"
                              % (uri, ", ".join(r["uri"] for r
                                                in resources.catalogue()))) from None
        except OSError as exc:
            raise LookupError("resources/read %r: %s" % (uri, exc)) from None
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        # through the server's own dispatcher, so an assistant and a panel
        # are refused by the same code
        request = dict(args, op=name)
        # no progress for an attached session: the operation runs in Blender's
        # process and answers once
        sink = None if isinstance(session, Attached) else _reporting(params, notify)
        if sink is None:
            answer = (session.send(request) if isinstance(session, Attached)
                      else handle(session, request))
        else:
            with progress.reported_to(sink):
                answer = handle(session, request)
        if answer.get("ok"):
            result = answer.get("result")
            if name == "render":
                return _with_picture(result)
            return _text(result)
        return dict(_text({k: v for k, v in answer.items() if k != "ok"}),
                    isError=True)
    if method == "ping":
        return {}
    if ident is None:
        return None                       # a notification we do not act on
    raise LookupError(method)


class Attached:
    """A session that lives in Blender, reached over a socket.

    Speaks the same line-of-JSON protocol as `cadcore.service.server`, so the
    assistant works on the document on screen and the add-on refreshes the
    viewport.
    """

    def __init__(self, where: str):
        import socket

        host, _, port = where.partition(":")
        token = self._token()
        self.socket = socket.create_connection((host or "127.0.0.1",
                                                int(port or 8765)), timeout=600)
        self.stream = self.socket.makefile("rw", encoding="utf-8")
        # the first line is the token Blender wrote when it started listening;
        # without it the bridge closes the connection
        self.stream.write(json.dumps({"token": token}) + "\n")
        self.stream.flush()
        admitted = self.stream.readline()
        try:
            said = json.loads(admitted) if admitted else {}
        except ValueError:
            said = {}
        if not said.get("admitted"):
            raise SystemExit("Blender did not admit this connection: %s"
                             % (said.get("message") or "no answer"))

    @staticmethod
    def _token() -> str:
        """The bridge's token, from the file the environment names."""
        path = os.environ.get("CADCORE_BRIDGE_TOKEN_FILE")
        if not path:
            raise SystemExit("CADCORE_BRIDGE_TOKEN_FILE is not set: run this from the "
                             "command the add-on writes, which names the token")
        try:
            with open(path, encoding="utf-8") as file:
                return file.read().strip()
        except OSError as exc:
            raise SystemExit("cannot read the bridge token at %s: %s" % (path, exc))

    def send(self, request: dict) -> dict:
        # requests are numbered and the reply matched by id, so a late answer
        # to a timed-out request is not handed to the next call
        self._id = getattr(self, "_id", 0) + 1
        request = dict(request, id=self._id)
        self.stream.write(json.dumps(request) + "\n")
        self.stream.flush()
        while True:
            line = self.stream.readline()
            if not line:
                return {"ok": False, "kind": "bridge_closed",
                        "message": "Blender closed the connection"}
            try:
                reply = json.loads(line)
            except ValueError:
                continue                          # not a reply; a line of chatter
            if reply.get("id") in (None, self._id):
                return reply
            # an older request's answer arriving late: drop it, keep waiting


def main() -> None:
    # OpenCASCADE writes to fd 1, which is the JSON-RPC stream: replies go
    # through a duplicate and fd 1 is pointed at stderr, as in server.py
    channel = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = channel
    where = None
    if "--attach" in sys.argv:
        at = sys.argv.index("--attach") + 1
        where = sys.argv[at] if at < len(sys.argv) else "127.0.0.1:8765"
    # the tool list is read off the class, so attaching describes the same
    # operations while the calls go to Blender
    session = Attached(where) if where else Session(
        root=workspace.default_root())
    try:
        sys.stdin.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except (ValueError, RecursionError):
            continue                      # not ours to answer, and not ours to log
        if not isinstance(request, dict):
            # a JSON-RPC batch is an array; this speaks one request object
            # per line
            sys.stdout.write(json.dumps(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32600,
                           "message": "this server takes one request object "
                                      "per line, not a batch"}}) + "\n")
            sys.stdout.flush()
            continue
        ident = request.get("id")

        def notify(message, _out=sys.stdout) -> None:
            _out.write(json.dumps(message) + "\n")
            _out.flush()

        try:
            result = respond(session, request, notify)
        except LookupError as exc:
            reply = {"jsonrpc": "2.0", "id": ident,
                     "error": {"code": -32601, "message": "no method %s" % exc}}
        except Exception as exc:                                # noqa: BLE001
            # the kernel's refusals come back through `handle` as results, so
            # anything landing here is an adapter error
            reply = {"jsonrpc": "2.0", "id": ident,
                     "error": {"code": -32603, "message": repr(exc)}}
        else:
            if result is None:
                continue                  # a notification: nothing goes back
            reply = {"jsonrpc": "2.0", "id": ident, "result": result}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
