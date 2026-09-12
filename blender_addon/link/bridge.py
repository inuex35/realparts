"""A loopback socket that lets an assistant drive the session Blender is showing.

One JSON operation per line in, its answer per line out, the viewport
refreshed on the way. A connection's first line is the token this Blender
wrote to a file only this user can read. Nothing touches bpy from the socket
thread: requests are queued and performed on a main-thread timer.
"""
from __future__ import annotations

import hmac
import json
import os
import queue
import secrets
import socket
import threading

import bpy

HOST = "127.0.0.1"
PORT = 8765
#: the file the token is written to, under Blender's per-user config; the
#: assistant's command carries this path in its environment
TOKEN_FILE = "bridge-token"
#: names a request may not use for arguments: they are the client's own
#: parameters, and "timeout": 0 through **args hung the main thread
RESERVED = frozenset({"timeout", "op", "id"})
#: ops that empty the document without replying with a build; the scene must
#: still be cleared, or the last document's mesh is left drawn and pickable.
DOC_RESET_OPS = frozenset({"new_document"})
#: the argument names that carry a path, at any depth: `open`, `save`, the
#: exports and `render` take `path`; an import feature and a sketch's `file`
#: carry one under `path` too, inside `add_feature`, `apply` and `load_json`
PATH_KEYS = frozenset({"path", "filepath", "out"})
#: what a path looks like when it is under some other name
FILE_ENDINGS = (".json", ".step", ".stp", ".iges", ".igs", ".stl", ".3mf",
                ".svg", ".dxf", ".png", ".obj", ".ply", ".brep", ".gltf", ".glb")
#: a request line longer than this is refused rather than parsed
MAX_LINE = 8 * 1024 * 1024

#: requests from socket threads that the main thread has not yet answered
_asked: queue.Queue = queue.Queue()
_server: socket.socket | None = None
_thread: threading.Thread | None = None
_clients = 0
#: the admitted connections, so `stop` can end them
_connections: set = set()
_token: str | None = None
#: True while an Ask is the reason the bridge is on, so the end of the Ask
#: turns it off again rather than leaving a listener nobody switched on
_borrowed = False
_lock = threading.Lock()


def listening() -> bool:
    return _server is not None


def clients() -> int:
    return _clients


def token_path() -> str:
    """Where this Blender keeps the token; only this user can read it."""
    return os.path.join(bpy.utils.user_resource('CONFIG', path="cadcore", create=True),
                        TOKEN_FILE)


def _write_token() -> str:
    """A fresh token, on disk for the assistant's command to read."""
    token = secrets.token_urlsafe(32)
    path = token_path()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(token)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def start(port: int = PORT, for_ask: bool = False) -> str:
    """Start listening. Returns the address.

    `for_ask`: an Ask needs the bridge and switched it on; `release` turns
    it off again when that Ask is over, unless the sidebar had it on already.
    """
    global _server, _thread, _token, _borrowed
    if _server is not None:
        return "%s:%d" % (HOST, _server.getsockname()[1])
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((HOST, port))
        server.listen(4)
    except OSError:
        server.close()               # nothing is listening, so nothing says it is
        raise
    _borrowed = for_ask
    _token = _write_token()
    _server = server
    _thread = threading.Thread(target=_accept, args=(_server,), daemon=True)
    _thread.start()
    if not bpy.app.timers.is_registered(_serve):
        bpy.app.timers.register(_serve, first_interval=0.1)
    return "%s:%d" % (HOST, server.getsockname()[1])


def stop() -> None:
    global _server, _token, _borrowed
    if _server is not None:
        try:
            _server.close()
        except OSError:
            pass
        _server = None
    with _lock:
        admitted = list(_connections)
    for connection in admitted:      # a client still talking is cut off, not left on
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            connection.close()
        except OSError:
            pass
    _token, _borrowed = None, False
    try:
        os.unlink(token_path())
    except OSError:
        pass


def release() -> None:
    """The Ask that switched the bridge on is over: switch it off again."""
    if _borrowed:
        stop()


def _admitted(first_line: str) -> bool:
    """Whether a connection's first line carries this Blender's token."""
    try:
        said = json.loads(first_line)
    except ValueError:
        return False
    given = said.get("token") if isinstance(said, dict) else None
    return (isinstance(given, str) and _token is not None
            and hmac.compare_digest(given, _token))


def _accept(server: socket.socket) -> None:
    while True:
        try:
            connection, _ = server.accept()
        except OSError:
            return                            # the server socket was closed
        threading.Thread(target=_talk, args=(connection,), daemon=True).start()


def _talk(connection: socket.socket) -> None:
    """Serve one connection until it closes."""
    global _clients
    with _lock:
        _clients += 1
        _connections.add(connection)
    try:
        connection.settimeout(30.0)      # a caller has half a minute to say the token
        with connection, connection.makefile("rw", encoding="utf-8") as stream:
            # the first line is the token, or the connection is over
            if not _admitted(stream.readline(MAX_LINE + 1).strip()):
                stream.write(json.dumps({"ok": False, "kind": "not_admitted",
                                         "message": "the first line has to be this "
                                                    "Blender's token"}) + "\n")
                stream.flush()
                return
            stream.write(json.dumps({"ok": True, "admitted": True}) + "\n")
            stream.flush()
            connection.settimeout(None)
            while True:
                line = stream.readline(MAX_LINE + 1)     # bounded: a line without end fills no memory
                if not line:
                    break
                if len(line) > MAX_LINE:
                    while line and not line.endswith("\n"):      # the rest of it, thrown away
                        line = stream.readline(MAX_LINE + 1)
                    stream.write(json.dumps({"ok": False, "kind": "bad_json",
                                             "message": "a request line that long is not a request"}) + "\n")
                    stream.flush()
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    request = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(request, dict) or not isinstance(request.get("op"), str):
                    stream.write(json.dumps({"ok": False, "kind": "bad_json",
                                             "message": "a request is an object with an op"}) + "\n")
                    stream.flush()
                    continue
                ident = request.pop("id", None)
                answered = threading.Event()
                slot: dict = {}
                _asked.put((request, slot, answered))
                if not answered.wait(timeout=600):
                    slot = {"ok": False, "kind": "timed_out",
                            "message": "Blender did not answer in ten minutes"}
                if ident is not None:
                    slot["id"] = ident
                stream.write(json.dumps(slot) + "\n")
                stream.flush()
    except OSError:
        pass
    finally:
        with _lock:
            _clients -= 1
            _connections.discard(connection)


def _serve() -> float | None:
    """Perform one queued request per timer tick, on the main thread.

    One per tick: an operation may be a rebuild, and draining the queue in one
    callback would freeze the viewport for the whole queue.
    """
    if _server is None and _asked.empty():
        return None
    try:
        request, slot, answered = _asked.get_nowait()
    except queue.Empty:
        return 0.1
    try:
        slot.update(_perform(request))
    except Exception as exc:                                     # noqa: BLE001
        slot.update({"ok": False, "kind": "bridge_failed", "message": repr(exc)})
    finally:
        answered.set()
    return 0.05


def _selection(context, args: dict) -> dict:
    """The current viewport selection, by CAD name.

    The kernel cannot answer this: the selection lives in the viewport.
    """
    from . import sync
    from .state import get_client

    ob = sync.body()
    faces = sync.selected_face_names()
    try:
        edges = sync.selected_edge_names(get_client(context)) if ob else []
    except Exception:                                            # noqa: BLE001
        edges = []
    return {"faces": faces, "edges": edges,
            "hint": "" if faces or edges else
            "nothing is selected; ask the person to click a face, or use "
            "describe_faces and select() to point at one yourself"}


def _select(context, args: dict) -> dict:
    """Select faces in the viewport by name, on every part; a replace clears
    the other parts too."""
    import json

    from . import sync

    bodies = sync.bodies()
    if not bodies:
        return {"ok": False, "kind": "no_document", "message": "nothing is on screen"}
    wanted = list(args.get("faces") or [])
    known = set()
    for ob in bodies:
        known.update(json.loads(ob.get("cad_face_table") or "[]"))
    unknown = [n for n in wanted if n not in known]
    if unknown:
        return {"ok": False, "kind": "unknown_face",
                "message": "no face called %s" % ", ".join(repr(n) for n in unknown),
                "detail": {"unknown": unknown, "available": sorted(known)[:40]}}
    keep, add = set(wanted), bool(args.get("add"))
    for ob in bodies:
        table = json.loads(ob.get("cad_face_table") or "[]")
        attr = ob.data.attributes.get("cad_face")
        with sync.out_of_edit_mode(ob):
            for poly in ob.data.polygons:
                name = table[attr.data[poly.index].value] if attr is not None else None
                poly.select = (name in keep) or (add and poly.select)
            ob.data.update()
    for area in (context.screen.areas if context.screen else []):
        area.tag_redraw()
    return {"ok": True, "result": {"selected": sync.selected_face_names()}}


#: operations the viewport answers itself; everything else goes to the kernel
VIEWPORT_OPS = {"selection": _selection, "select": _select}


def _root(context) -> str:
    """Where a request over the bridge may read and write: the open document's
    folder, else the workspace the kernel would take. Blender's own kernel is
    unfenced, so the fence for the bridge is here."""
    props = getattr(context.scene, "cadcore", None)
    doc = getattr(props, "doc_path", "") if props is not None else ""
    if doc:
        return os.path.dirname(os.path.realpath(bpy.path.abspath(doc)))
    return os.environ.get("CAD_WORKSPACE") or os.getcwd()


def _outside_the_root(context, args) -> str | None:
    """The first path-carrying argument that resolves outside the root."""
    root = os.path.realpath(_root(context))

    def outside(value):
        where = os.path.realpath(os.path.join(root, value))
        return os.path.commonpath([root, where]) != root

    def walk(value, named=False):
        if isinstance(value, dict):
            for key, inner in value.items():
                found = walk(inner, key in PATH_KEYS)
                if found:
                    return found
        elif isinstance(value, list):
            for inner in value:
                found = walk(inner, named)
                if found:
                    return found
        elif isinstance(value, str) and value and (named or value.lower().endswith(FILE_ENDINGS)):
            if outside(value):
                return value
        return None

    return walk(args)


def _perform(request: dict) -> dict:
    """Perform one operation on the session and refresh the view if it rebuilt."""
    from .client import ServerError
    from .state import (_apply_build, clear_scene_for_empty_document, get_client,
                        push_undo, refresh_guard)

    op = request.get("op")
    args = {k: v for k, v in request.items() if k not in RESERVED}
    context = bpy.context
    outside = _outside_the_root(context, args)
    if outside:
        return {"ok": False, "kind": "bad_path",
                "message": "%s is outside this session's folder" % outside,
                "detail": {"root": _root(context)}}
    if op in VIEWPORT_OPS:
        answer = VIEWPORT_OPS[op](context, args)
        if "ok" in answer:                    # a refusal, or a wrapped result
            return answer
        return {"ok": True, "result": answer}
    from ..ui import changes

    before = changes.picture(context)
    try:
        answer = get_client(context).call(op, **args)
    except ServerError as exc:
        return {"ok": False, "kind": exc.kind, "message": exc.message,
                "detail": exc.detail}
    # Only a build reply refreshes the viewport. A `faces` key alone is not
    # the test: `tessellate` and `measure` answer with a `faces` list of their
    # own, and `_apply_build` would write it into the integer `props.faces`.
    build_reply = (isinstance(answer, dict)
                   and isinstance(answer.get("faces"), int)
                   and "volume_mm3" in answer)
    if build_reply:
        with refresh_guard():
            _apply_build(context, answer)
        changes.settle(context, before)       # what that did, drawn on the part
        push_undo("assistant: %s" % op)
        for area in (context.screen.areas if context.screen else []):
            area.tag_redraw()
    elif op in DOC_RESET_OPS:
        # new_document empties the document but does not reply with a build, so
        # nothing above cleared the scene; drop the last document's mesh so it
        # is not left drawn and pickable over a document with no solid.
        with refresh_guard():
            clear_scene_for_empty_document(context)
        push_undo("assistant: %s" % op)
        for area in (context.screen.areas if context.screen else []):
            area.tag_redraw()
    return {"ok": True, "result": answer}
