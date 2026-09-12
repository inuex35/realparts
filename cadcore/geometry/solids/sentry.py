"""Ask a second process whether an operation is survivable, before doing it."""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import tempfile
import threading


#: how the helper is invoked, and how long it is given to answer
_TIMEOUT = float(os.environ.get("CAD_SENTRY_TIMEOUT", "180"))
#: the helper has to import OCP before it can say hello, which is seconds
_HANDSHAKE = float(os.environ.get("CAD_SENTRY_HANDSHAKE", "90"))
_LOCK = threading.Lock()
_HELPER: subprocess.Popen | None = None
#: set once when no helper can start here (not a Python, no OCP, cannot fork):
#: stop asking, and run the operation in this process unguarded
_BROKEN = False


def _length(edge) -> float:
    """An edge's length, measured the way `query` measures one, so that the two
    sides of the pipe agree on what an edge is without sharing any code."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, props)
    return props.Mass()


def enabled() -> bool:
    return os.environ.get("CAD_SENTRY", "1") not in ("0", "no", "off", "")


# --------------------------------------------------------------- the parent --
def _start():
    """A helper that has proved it can talk, or None if there will not be one.

    The handshake matters: inside Blender ``sys.executable`` is the Blender
    binary, which prints a usage message and exits. Without a handshake that
    would read as a helper that died doing the work, and every fillet would be
    refused as a crash. So the first question has no shape in it.
    """
    global _HELPER, _BROKEN
    if _BROKEN:
        return None
    if _HELPER is not None and _HELPER.poll() is None:
        return _HELPER
    try:
        helper = subprocess.Popen(
            [sys.executable, "-c",
             "import sys; from cadcore.geometry.solids.sentry import _serve; _serve()"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
        helper.stdin.write('{"kind": "hello"}\n')
        helper.stdin.flush()
    except (OSError, ValueError, AttributeError):
        _BROKEN = True
        return None
    hello = _readline(helper, _HANDSHAKE)
    try:
        json.loads(hello or "")["verdict"]
    except (ValueError, KeyError, TypeError):
        try:
            helper.kill()
        except OSError:
            pass
        _BROKEN = True
        return None
    _HELPER = helper
    return _HELPER


def _readline(helper, timeout: float):
    """One line, or None if it did not arrive in time or at all.

    A thread rather than `select`, because the helper's stdout is a text stream
    on both platforms this has to work on, and a helper that has died is a read
    that returns nothing rather than one that blocks.
    """
    answer: list[str] = []

    def read():
        try:
            answer.append(helper.stdout.readline())
        except (OSError, ValueError):
            answer.append("")

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        return None
    return answer[0] if answer else ""


def _shut() -> None:
    global _HELPER
    if _HELPER is not None:
        try:
            _HELPER.kill()
        except OSError:
            pass
        _HELPER = None


atexit.register(_shut)


def _area(face) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return props.Mass()


def _face_walk(shape) -> list:
    """Every face of the shape once, in topology order -- see `_edge_walk`."""
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    seen, out = set(), []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = explorer.Current()
        key = face.TShape()
        if key not in seen:
            seen.add(key)
            out.append(face)
        explorer.Next()
    return out


def _index_faces(shape, faces: list):
    """Each face as (position in the walk, area), or None if one is not there."""
    walk = _face_walk(shape)
    picked = []
    for wanted in faces:
        for position, face in enumerate(walk):
            if face.IsSame(wanted):
                picked.append([position, round(_area(face), 6)])
                break
        else:
            return None
    return picked


def survives(kind: str, shape, edges: list, size, timeout: float | None = None) -> str:
    """``"ok"``, ``"refused"``, ``"crashed"``, ``"timed_out"`` or ``"unknown"``.

    ``"unknown"`` means the helper could not be asked or could not confirm it
    was looking at the same edges -- the caller should carry on as it would
    have without this module rather than refuse work on a hunch.
    """
    if not enabled():
        return "unknown"
    from OCP.BinTools import BinTools

    # `edges` are faces for a defeature and a shell: what is removed, or
    # left open, is faces
    by_face = kind in ("defeature", "shell")
    picked = _index_faces(shape, edges) if by_face else _index_edges(shape, edges)
    if picked is None:
        return "unknown"
    with _LOCK:
        handle, path = tempfile.mkstemp(suffix=".brep", prefix="cad-sentry-")
        os.close(handle)
        try:
            BinTools.Write_s(shape, path)
            ask = {"kind": kind, "shape": path, "size": size,
                   ("faces" if by_face else "edges"): picked}
            return _ask(json.dumps(ask), timeout)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


def _ask(line: str, timeout: float | None = None) -> str:
    helper = _start()
    if helper is None:
        return "unknown"
    try:
        helper.stdin.write(line + "\n")
        helper.stdin.flush()
    except (OSError, ValueError, AttributeError):
        _shut()
        return "unknown"

    answer = _readline(helper, timeout or _TIMEOUT)
    if answer is None:
        _shut()
        return "timed_out"
    if not answer.strip():
        # the helper wrote nothing and went away: it died doing what the
        # caller was about to do. A helper that never worked at all cannot
        # reach here -- the handshake in `_start` takes that case
        _shut()
        return "crashed"
    try:
        return json.loads(answer)["verdict"]
    except (ValueError, KeyError, TypeError):
        return "unknown"


def _edge_walk(shape) -> list:
    """Every edge of the shape once, in the order the topology gives them.

    Both sides of the pipe name an edge by its position in this walk, so both
    sides have to walk the same way: one edge per underlying `TShape`, however
    many times the faces that share it list it.
    """
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer

    walk, seen = [], set()
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        edge = explorer.Current()
        key = edge.TShape()
        if key not in seen:
            seen.add(key)
            walk.append(edge)
        explorer.Next()
    return walk


def _index_edges(shape, edges: list):
    """The chosen edges as positions in a walk of the shape, with a check value.

    The helper reads the shape back from a file and has to find the *same*
    edges in it. Position in a topological walk is the only handle both sides
    share, and a position is exactly the kind of reference this kernel does not
    trust on its own -- so each one is sent with the length of the edge it is
    supposed to be, and the helper refuses to answer if any of them disagrees.
    """
    walk = _edge_walk(shape)
    picked = []
    for wanted in edges:
        for position, edge in enumerate(walk):
            if edge.IsSame(wanted):
                picked.append([position, round(_length(edge), 6)])
                break
        else:
            return None                     # an edge not in the walk: do not guess
    return picked


# ---------------------------------------------------------------- the child --
def _serve() -> None:
    """One question per line on stdin, one verdict per line on stdout.

    Deliberately tiny: it reads a shape, tries the operation, and says how it
    went. It does not name anything, does not send geometry back, and is
    expected to die -- that is what it is for.
    """
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    # first: it is what makes `TopoDS.Face_s` exist on OCCT 8.0, and this
    # process deliberately imports none of the rest of the package. Without it
    # the walk below raised, the raise was read as "cannot tell", and the guard
    # let a defeature through that took three and a half minutes.
    from ..core import occ                                        # noqa: F401
    from OCP.BinTools import BinTools
    from OCP.BRepFilletAPI import (BRepFilletAPI_MakeChamfer,
                                   BRepFilletAPI_MakeFillet)
    from OCP.TopoDS import TopoDS, TopoDS_Shape

    def verdict(word):
        sys.stdout.write(json.dumps({"verdict": word}) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            ask = json.loads(line)
        except ValueError:
            verdict("unknown")
            continue
        try:
            shape = TopoDS_Shape()
            BinTools.Read_s(shape, ask["shape"])
            chosen = []
            if ask["kind"] in ("defeature", "shell"):
                walk = _face_walk(shape)
                for position, area in ask["faces"]:
                    if position >= len(walk):
                        raise LookupError("shape came back shorter")
                    face = TopoDS.Face_s(walk[position])
                    if abs(_area(face) - area) > 1e-4 * max(1.0, area):
                        raise LookupError("face %d has another area" % position)
                    chosen.append(face)
            else:
                walk = _edge_walk(shape)
                for position, length in ask["edges"]:
                    if position >= len(walk):
                        raise LookupError("shape came back shorter")
                    edge = TopoDS.Edge_s(walk[position])
                    here = _length(edge)
                    if abs(here - length) > 1e-4:
                        raise LookupError("edge %d is %.6f, not %.6f"
                                          % (position, here, length))
                    chosen.append(edge)
        except Exception:                                        # noqa: BLE001
            verdict("unknown")
            continue

        try:
            size = ask["size"]
            if ask["kind"] == "defeature":
                from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
                from ..core.occ import ListOfShape

                algo = BRepAlgoAPI_Defeaturing()
                algo.SetShape(shape)
                faces = ListOfShape()
                for face in chosen:
                    faces.Append(face)
                algo.AddFacesToRemove(faces)
            elif ask["kind"] == "shell":
                # the work is in the call, not in Build
                from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
                from ..core.occ import ListOfShape

                algo = BRepOffsetAPI_MakeThickSolid()
                faces = ListOfShape()
                for face in chosen:
                    faces.Append(face)
                algo.MakeThickSolidByJoin(shape, faces, -float(size), 1e-4)
                verdict("ok" if algo.IsDone() else "refused")
                continue
            elif ask["kind"] == "chamfer":
                algo = BRepFilletAPI_MakeChamfer(shape)
                for edge in chosen:
                    algo.Add(float(size), edge)
            else:
                algo = BRepFilletAPI_MakeFillet(shape)
                start, end = (size if isinstance(size, (list, tuple))
                              else (size, size))
                for edge in chosen:
                    if start == end:
                        algo.Add(float(start), edge)
                    else:
                        algo.Add(float(start), float(end), edge)
            algo.Build()                     # this is the line that may not return
            verdict("ok" if algo.IsDone() else "refused")
        except Exception:                                        # noqa: BLE001
            verdict("refused")
