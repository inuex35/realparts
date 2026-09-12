"""Where a served session may read and write, and what it says it does.

`cadcore.service.mcp`'s opening paragraph told anyone connecting an assistant that it
"cannot write files, run commands or reach anything outside the document it
opened". Two thirds of that was true. `save`, `export_step`, `export_mesh`,
`drawing`, `flat_dxf` and `render` each took a path and wrote to it, absolute
or not, and `open` read any file on the machine -- it only failed afterwards,
on the JSON. That sentence is the first thing somebody reads before aiming a
language model at their filesystem.

A served session has a root now and every path is resolved inside it. A library
session does not: `Session()` from Python is somebody's own code in their own
process, and confining it would break every script that exports to /tmp.

It is a fence, not a sandbox: it stops an assistant asked to save a bracket
from writing to a dotfile. Code sharing the interpreter has other doors, and
these tests do not pretend otherwise.
"""
import json
import os
import subprocess
import sys

import pytest

from cadcore.ops import workspace
from cadcore.errors import CadError
from cadcore.ops.session import Session

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _bracket(root=None):
    """A session with something built in it, fenced to `root` afterwards.

    Afterwards, because the example lives in the repository and the point of
    every test here is where the *output* may go.
    """
    session = Session(autosave=False, root=ROOT)
    session.op_open(os.path.join(ROOT, "examples", "bracket.json"))
    session.op_build()
    session.root = root
    return session


def test_a_library_session_is_not_confined(tmp_path):
    """Nothing changes for somebody's own script."""
    out = _bracket(None).op_export_step(path=str(tmp_path / "anywhere.step"))
    assert os.path.exists(out["path"])


def test_a_served_session_will_not_write_outside_its_root(tmp_path):
    with pytest.raises(CadError) as caught:
        _bracket(str(tmp_path)).op_export_step(path="/tmp/escaped.step")
    assert caught.value.kind == "bad_path"
    assert "workspace" in caught.value.message


def test_dot_dot_does_not_get_out(tmp_path):
    with pytest.raises(CadError):
        _bracket(str(tmp_path)).op_export_step(path="../escaped.step")


def test_a_symlink_planted_in_the_root_does_not_get_out(tmp_path):
    """Resolved before comparing, so a link is followed to where it points."""
    outside = tmp_path / "outside"
    outside.mkdir()
    inside = tmp_path / "inside"
    inside.mkdir()
    (inside / "door").symlink_to(outside)
    with pytest.raises(CadError) as caught:
        _bracket(str(inside)).op_export_step(path="door/escaped.step")
    assert caught.value.kind == "bad_path"


def test_a_relative_path_lands_in_the_root(tmp_path):
    out = _bracket(str(tmp_path)).op_export_step(path="fine.step")
    assert out["path"] == str((tmp_path / "fine.step").resolve())


def test_reading_is_fenced_too(tmp_path):
    session = Session(autosave=False, root=str(tmp_path))
    with pytest.raises(CadError) as caught:
        session.op_open("/etc/hostname")
    assert caught.value.kind == "bad_path"


def test_the_tool_list_says_which_operations_touch_files():
    from cadcore.service.mcp import tools

    listed = {t["name"]: t["touches"] for t in tools(Session())}
    assert listed["save"] == "write"
    assert listed["open"] == "read"
    assert listed["build"] is None
    assert "workspace" in [t for t in tools(Session())
                           if t["name"] == "save"][0]["description"]


def test_every_operation_that_takes_a_path_is_declared():
    """A new one that quietly gets a file to itself is the way the paragraph
    stopped being true the first time."""
    import inspect

    missing = []
    for name in dir(Session):
        if not name.startswith("op_"):
            continue
        parameters = inspect.signature(getattr(Session, name)).parameters
        if "path" in parameters and workspace.touches(name[3:]) is None:
            missing.append(name[3:])
    assert not missing, ("an operation with a path and nothing said about it: "
                         + ", ".join(sorted(missing))
                         + " -- add it to workspace.TOUCHES and fence it")


def test_a_line_that_is_not_an_object_does_not_end_the_server():
    """`[1]` reached `request.get("op")` and took the process down with an
    AttributeError -- and with it every reply the client was waiting for."""
    proc = subprocess.run(
        [sys.executable, "-m", "cadcore.service.server"],
        input='[1]\n42\n{"op": "describe_document"}\n',
        capture_output=True, text=True, cwd=ROOT, timeout=300, encoding="utf-8")
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 3, "the server stopped answering"
    assert lines[0]["kind"] == "bad_json" and lines[0]["got"] == "list"
    assert lines[1]["kind"] == "bad_json" and lines[1]["got"] == "int"
    assert lines[2]["kind"] == "no_document"


def test_a_json_rpc_batch_is_refused_rather_than_fatal():
    proc = subprocess.run(
        [sys.executable, "-m", "cadcore.service.mcp"],
        input='[{"jsonrpc": "2.0", "method": "tools/list", "id": 1}]\n',
        capture_output=True, text=True, cwd=ROOT, timeout=300, encoding="utf-8")
    reply = json.loads(proc.stdout.splitlines()[0])
    assert reply["error"]["code"] == -32600


def test_a_symlinked_file_in_the_root_is_not_a_way_out(tmp_path):
    """The directory-link case was tested; the file-link case was the door.
    For a write the parent was resolved and the name kept, so a link planted in
    the root pointing at a file outside it passed as inside."""
    import os

    import pytest

    from cadcore.ops import workspace
    from cadcore.errors import CadError

    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("secret", encoding="utf-8")
    os.symlink(victim, root / "link.txt")
    for writing in (True, False):
        with pytest.raises(CadError) as raised:
            workspace.inside(str(root), "link.txt", writing=writing)
        assert raised.value.kind == "bad_path"
    # a dangling link is refused too, and a plain new file is still fine
    os.symlink(outside / "later.txt", root / "dangling.txt")
    with pytest.raises(CadError):
        workspace.inside(str(root), "dangling.txt", writing=True)
    assert workspace.inside(str(root), "fresh.txt", writing=True).endswith("fresh.txt")


def test_a_document_cannot_name_a_file_outside_the_fence(tmp_path):
    """`import_step` and `part` opened whatever the document named.

    The fence checked arguments called `path`; a feature's own arguments are
    resolved by the document, and that route went round it. A served session
    now hands the document its fence, and every file it names goes through.
    """
    root = tmp_path / "root"
    root.mkdir()
    (root / "part.json").write_text(json.dumps({"features": [
        {"id": "b", "type": "box", "size": [10, 10, 10]}], "result": "b"}), encoding="utf-8")
    s = Session(autosave=False, root=str(root))
    s.op_open(path="part.json")
    for kind, args in (("import_step", {"path": "/etc/passwd"}),
                       ("import_step", {"path": "../outside.step"}),
                       ("part", {"document": "/etc/hostname"})):
        with pytest.raises(CadError) as refused:
            s.op_add_feature(kind, args)
        assert refused.value.kind == "bad_path", (kind, refused.value.kind)
    # a file beside the document is inside, and reaches the reader
    (root / "sub.json").write_text(json.dumps({"features": [
        {"id": "c", "type": "box", "size": [5, 5, 5]}], "result": "c"}), encoding="utf-8")
    out = s.op_add_feature("part", {"document": "sub.json"})
    assert out["faces"] > 0
    # and a library session is not confined
    free = Session(autosave=False)
    free.op_open(path=str(root / "part.json"))
    with pytest.raises(CadError) as refused:
        free.op_add_feature("part", {"document": "/etc/hostname"})
    assert refused.value.kind == "bad_json"        # refused by the reader, not the fence


def test_opening_what_is_not_a_document_is_refused_by_kind(tmp_path):
    """A missing file and a file that is not JSON came back as internal_error."""
    s = Session(autosave=False)
    with pytest.raises(CadError) as refused:
        s.op_open(path=str(tmp_path / "nowhere.json"))
    assert refused.value.kind == "file_not_found"
    (tmp_path / "not.json").write_text("this is not json", encoding="utf-8")
    with pytest.raises(CadError) as refused:
        s.op_open(path=str(tmp_path / "not.json"))
    assert refused.value.kind == "bad_json"
    (tmp_path / "list.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(CadError) as refused:
        s.op_open(path=str(tmp_path / "list.json"))
    assert refused.value.kind == "bad_json"
