"""The CAD core over HTTP: the same operations as the line server, for a browser or any app.

    python -m cadcore.service.web            # http://127.0.0.1:8080/

`POST /op` takes the one JSON object the line server takes and answers the
same reply. The page in `web_ui/` is served beside it, and an Ask runs the
assistant CLI against this session over the same loopback bridge Blender uses.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from ..errors import CadError
from ..ops import workspace
from ..ops.session import Session
from .server import MAX_LINE, _spelled, handle

UI = Path(__file__).resolve().parent / "web_ui"
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".json": "application/json; charset=utf-8"}


class Hub:
    """One session, its lock, the bridge the assistant attaches to, and the Ask running."""

    def __init__(self, root: str | None, repo: str | None = None):
        self.session = Session(root=root, readable=(str(EXAMPLES),))
        self.lock = threading.RLock()
        self.repo = repo or str(Path(__file__).resolve().parents[2])
        self.ask: AskRun | None = None
        self.bridge_port: int | None = None
        self.token_file: str | None = None
        self.generation = 0            # bumped by every operation that changed the document
        #: the host names a request may be addressed to; empty means any
        self.hosts: set = set()

    def call(self, request: dict) -> dict:
        with self.lock:
            before = self._stamp()
            reply = handle(self.session, request)
            if reply.get("ok") and self._stamp() != before:
                self.generation += 1
            return reply

    def _stamp(self) -> tuple:
        doc = self.session.doc
        return (self.session.path, getattr(doc, "revision", None) if doc else None,
                id(doc), self.session.view_upto)

    def state(self) -> dict:
        doc = self.session.doc
        return {"generation": self.generation, "path": self.session.path,
                "revision": getattr(doc, "revision", None) if doc else None,
                "ask": self.ask.state() if self.ask else None}

    #: what a dropped file may be: a document, or geometry the kernel imports as one
    DROPPABLE = (".json", ".step", ".stp", ".iges", ".igs", ".brep",
                 ".stl", ".obj", ".3mf", ".gltf", ".glb")

    def upload(self, name: str, data: str) -> dict:
        """A file dropped on the page: written under the workspace, then opened."""
        import base64
        import re

        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.basename(name)) or "dropped"
        if not stem.lower().endswith(self.DROPPABLE):
            return {"ok": False, "kind": "unknown_format",
                    "message": "drop a .json document, or a STEP, IGES, BREP, STL, OBJ, 3MF or glTF file",
                    "detail": {"name": name, "accepted": list(self.DROPPABLE)}}
        # an unfenced session works anywhere, so its drops go to the temp folder
        folder = os.path.join(self.session.root, "dropped") if self.session.root \
            else os.path.join(tempfile.gettempdir(), "realparts-dropped")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, stem)
        try:
            with open(path, "wb") as file:
                file.write(base64.b64decode(data))
        except (OSError, ValueError) as exc:
            return {"ok": False, "kind": "bad_path", "message": str(exc)}
        reply = self.call({"op": "open", "path": path})
        return dict(reply, path=path)

    # -- the bridge: `cadcore.service.mcp --attach` speaks to this session --------
    def listen(self) -> None:
        folder = tempfile.mkdtemp(prefix="cadcore-web-")
        self.token_file = os.path.join(folder, "bridge-token")
        token = os.urandom(16).hex()
        with open(self.token_file, "w", encoding="utf-8") as file:
            file.write(token)
        os.chmod(self.token_file, 0o600)
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(4)
        self.bridge_port = server.getsockname()[1]
        threading.Thread(target=self._accept, args=(server, token), daemon=True).start()

    def _accept(self, server: socket.socket, token: str) -> None:
        while True:
            connection, _ = server.accept()
            threading.Thread(target=self._talk, args=(connection, token), daemon=True).start()

    def _talk(self, connection: socket.socket, token: str) -> None:
        with connection, connection.makefile("rw", encoding="utf-8") as stream:
            first = stream.readline(MAX_LINE + 1)
            try:
                said = json.loads(first) if first else {}
            except ValueError:
                said = {}
            if not isinstance(said, dict) or said.get("token") != token:
                stream.write(json.dumps({"ok": False, "kind": "not_admitted",
                                         "message": "wrong or missing token"}) + "\n")
                return
            stream.write(json.dumps({"ok": True, "admitted": True}) + "\n")
            stream.flush()
            while True:
                line = stream.readline(MAX_LINE + 1)     # bounded: a line without end fills no memory
                if not line:
                    break
                if len(line) > MAX_LINE:
                    while line and not line.endswith("\n"):
                        line = stream.readline(MAX_LINE + 1)
                    request, reply = None, {"ok": False, "kind": "bad_json", "id": None,
                                            "message": "a request line that long is not a request"}
                    stream.write(json.dumps(reply) + "\n")
                    stream.flush()
                    continue
                try:
                    request = json.loads(line)
                except (ValueError, RecursionError):
                    reply = {"ok": False, "kind": "bad_json", "message": "not JSON", "id": None}
                else:
                    reply = self.call(request) if isinstance(request, dict) else \
                        {"ok": False, "kind": "bad_json", "message": "a request is an object"}
                    reply["id"] = request.get("id") if isinstance(request, dict) else None
                stream.write(json.dumps(reply, default=_spelled) + "\n")
                stream.flush()

    # -- Ask ----------------------------------------------------------------------
    def start_ask(self, question: str, situation: str) -> dict:
        if self.ask and not self.ask.done:
            raise CadError("bad_arguments", "the assistant is still working",
                           {"hint": "wait for it, or stop it"})
        if self.bridge_port is None:
            self.listen()
        exe = shutil.which(os.environ.get("CADCORE_ASSISTANT", "claude"))
        if not exe:
            raise CadError("missing_dependency",
                           "the assistant CLI (claude) was not found on this computer",
                           {"hint": "install Claude Code and sign in, or set CADCORE_ASSISTANT"})
        spec = {"command": sys.executable,
                "args": ["-m", "cadcore.service.mcp", "--attach", "127.0.0.1:%d" % self.bridge_port],
                "env": {"CADCORE_BRIDGE_TOKEN_FILE": self.token_file, "PYTHONPATH": self.repo}}
        handle_, path = tempfile.mkstemp(suffix=".json", prefix="cadcore-mcp-")
        with os.fdopen(handle_, "w", encoding="utf-8") as file:
            json.dump({"mcpServers": {"cadcore": spec}}, file)
        # only the CAD tools: a file or a shell would ask for a permission
        # nobody can grant from a browser
        argv = [exe, "-p", "--output-format", "stream-json", "--verbose",
                "--mcp-config", path, "--allowedTools", "mcp__cadcore__*",
                "--disallowedTools", "Bash,PowerShell,Write,Edit,NotebookEdit,Task,WebFetch,WebSearch",
                "--permission-mode", "dontAsk"]
        if self.ask and self.ask.session_id:
            argv += ["--resume", self.ask.session_id]
        self.ask = AskRun(argv, situation + "\n\n" + question,
                          self.ask.session_id if self.ask else None, self.repo)
        self.ask.start()
        return self.ask.state()


class AskRun:
    """One question to the assistant CLI, its answer collected as it streams."""

    def __init__(self, argv: list, prompt: str, session_id: str | None, cwd: str):
        self.argv, self.prompt, self.cwd = argv, prompt, cwd
        self.session_id = session_id
        self.events: list = []
        self.done = False
        self.error: str | None = None
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def state(self) -> dict:
        return {"done": self.done, "error": self.error, "events": self.events[-200:],
                "session": self.session_id}

    def _run(self) -> None:
        try:
            self.proc = subprocess.Popen(self.argv, cwd=self.cwd, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         text=True, encoding="utf-8")
            self.proc.stdin.write(self.prompt)
            self.proc.stdin.close()
            said: list = []
            complaints = threading.Thread(target=lambda: said.append(self.proc.stderr.read()),
                                          daemon=True)
            complaints.start()          # read beside stdout: a full pipe would stall the child
            for line in self.proc.stdout:
                self._note(line)
            self.proc.wait()
            complaints.join(timeout=5)
            if self.proc.returncode and not self.events:
                self.error = ("".join(said) or "").strip()[-800:] \
                    or "the assistant exited with %d" % self.proc.returncode
        except OSError as exc:
            self.error = str(exc)
        finally:
            self.done = True

    def _note(self, line: str) -> None:
        """One stream-json line: keep what a person reads, and the session id."""
        try:
            said = json.loads(line)
        except ValueError:
            return
        if said.get("session_id"):
            self.session_id = said["session_id"]
        kind = said.get("type")
        if kind == "assistant":
            for piece in (said.get("message") or {}).get("content") or []:
                if piece.get("type") == "text" and piece.get("text"):
                    self.events.append({"kind": "text", "text": piece["text"]})
                elif piece.get("type") == "tool_use":
                    name = str(piece.get("name", "")).replace("mcp__cadcore__", "")
                    self.events.append({"kind": "tool", "text": name,
                                        "args": piece.get("input") or {}})
        elif kind == "result":
            if said.get("is_error"):
                self.error = str(said.get("result") or "the assistant reported an error")
            elif said.get("result") and not any(e["kind"] == "text" for e in self.events[-1:]):
                self.events.append({"kind": "text", "text": str(said["result"])})


def examples() -> list:
    """The shipped examples: path and name, for a menu."""
    out = []
    for path in sorted(EXAMPLES.glob("*.json")):
        if path.name.endswith(".autosave.json"):
            continue
        try:
            meta = json.loads(path.read_text(encoding="utf-8")).get("meta") or {}
        except (OSError, ValueError):
            meta = {}
        out.append({"path": str(path), "name": meta.get("name") or path.stem,
                    "note": meta.get("note", "")})
    return out


class Handler(BaseHTTPRequestHandler):
    hub: Hub

    def log_message(self, format, *args):                       # noqa: A002 -- stdlib name
        pass

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, default=_spelled).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        try:
            resolved = path.resolve()
            resolved.relative_to(UI.resolve())           # never outside the UI folder
            data = resolved.read_bytes()
        except (OSError, ValueError):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(resolved.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:                                   # noqa: N802 -- stdlib name
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._file(UI / "index.html")
        elif path.startswith("/static/"):
            self._file(UI / path[len("/static/"):])
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif path == "/examples":
            self._json({"examples": examples()})
        elif path == "/state":
            self._json(self.hub.state())
        elif path == "/ask":
            self._json(self.hub.ask.state() if self.hub.ask else {"done": True, "events": []})
        else:
            self.send_error(404)

    def _body(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 50 * 1024 * 1024:
            self._json({"ok": False, "kind": "bad_json", "message": "request too large"}, 413)
            return None
        try:
            said = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except ValueError as exc:
            self._json({"ok": False, "kind": "bad_json", "message": str(exc)}, 400)
            return None
        if not isinstance(said, dict):
            self._json({"ok": False, "kind": "bad_json", "message": "a request is a JSON object"}, 400)
            return None
        return said

    def _from_the_page(self) -> bool:
        """Only the page this server serves may post: JSON, addressed to this
        host, from this origin. A form on another site can send neither."""
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if kind != "application/json":
            self._json({"ok": False, "kind": "not_admitted",
                        "message": "a request is application/json"}, 415)
            return False
        host = _host_of("//%s" % (self.headers.get("Host") or ""))
        origin = self.headers.get("Origin")
        if self.hub.hosts and host not in self.hub.hosts:
            self._json({"ok": False, "kind": "not_admitted",
                        "message": "this server does not answer to %r" % host}, 403)
            return False
        if origin and origin.lower() != "null" and _host_of(origin) != host:
            self._json({"ok": False, "kind": "not_admitted",
                        "message": "a request from another site"}, 403)
            return False
        return True

    def do_POST(self) -> None:                                  # noqa: N802 -- stdlib name
        path = self.path.split("?", 1)[0]
        if not self._from_the_page():
            return
        said = self._body()
        if said is None:
            return
        if path == "/op":
            reply = self.hub.call(said)
            reply["id"] = said.get("id")
            self._json(reply)
        elif path == "/ask":
            try:
                self._json({"ok": True, "result": self.hub.start_ask(
                    str(said.get("question", "")), str(said.get("situation", "")))})
            except CadError as exc:
                self._json({"ok": False, "kind": exc.kind, "message": exc.message,
                            "detail": exc.detail})
        elif path == "/ask/stop":
            if self.hub.ask:
                self.hub.ask.stop()
            self._json({"ok": True})
        elif path == "/upload":
            self._json(self.hub.upload(str(said.get("name", "")), str(said.get("data", ""))))
        else:
            self.send_error(404)


def _host_of(url: str) -> str:
    """The host name in a URL or a Host header, lower case, without the port."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def serve(host: str = "127.0.0.1", port: int = 8080, root: str | None = None,
          open_browser: bool = False) -> ThreadingHTTPServer:
    """Start serving; returns the server (call ``serve_forever`` on it)."""
    hub = Hub(root)
    if host not in ("", "0.0.0.0", "::"):      # bound to one address: answer to that name only
        hub.hosts = LOOPBACK | {host.lower()} if host in LOOPBACK else {host.lower()}
    handler = type("BoundHandler", (Handler,), {"hub": hub})
    server = ThreadingHTTPServer((host, port), handler)
    server.hub = hub
    if open_browser:
        webbrowser.open("http://%s:%d/" % (host, server.server_address[1]))
    return server


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="the CAD core over HTTP, with its page")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--unfenced", action="store_true",
                        help="open any path, not only ones under the workspace")
    parser.add_argument("--open", action="store_true", help="open the page in a browser")
    args = parser.parse_args()
    root = None if args.unfenced else workspace.default_root()
    server = serve(args.host, args.port, root, args.open)
    print("serving on http://%s:%d/" % server.server_address[:2], file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
