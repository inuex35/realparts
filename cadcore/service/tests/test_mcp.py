"""The MCP adapter: the kernel's operations, offered as tools.

Every tool is an operation and every refusal is the kernel's own; both are
checked rather than trusted.
"""
from __future__ import annotations

import inspect
import json

import pytest

from cadcore.ops import catalogue
from cadcore.service import mcp
from cadcore.ops.session import Session


@pytest.fixture()
def session():
    return Session(autosave=False)


def call(session, method, **params):
    return mcp.respond(session, {"jsonrpc": "2.0", "id": 1, "method": method,
                                 "params": params})


def tool_call(session, tool, **arguments):
    # the parameter is `tool`, not `name`: many operations take a `name` argument
    return call(session, "tools/call", name=tool, arguments=arguments)


def payload(answer):
    return json.loads(answer["content"][0]["text"])


def test_initialize_answers_in_the_version_it_was_asked_in(session):
    out = call(session, "initialize", protocolVersion="2025-06-18")
    assert out["protocolVersion"] == "2025-06-18"
    assert out["serverInfo"]["name"] == "cadcore"
    assert "tools" in out["capabilities"]


def test_every_operation_is_a_tool(session):
    offered = {t["name"] for t in mcp.tools(session)}
    ops = {name[3:] for name in dir(session) if name.startswith("op_")}
    assert offered == ops - catalogue.NOT_TOOLS


def test_every_tool_says_what_it_does(session):
    # the description is what the model reads to choose a tool
    for tool in mcp.tools(session):
        assert tool["description"], tool["name"]
        assert not tool["description"].startswith("the kernel's"), tool["name"]


def test_arguments_come_off_the_signature(session):
    schema = {t["name"]: t["inputSchema"] for t in mcp.tools(session)}
    fillet = schema["add_fillet"]
    assert fillet["required"] == ["edges", "radius"]
    assert fillet["properties"]["radius"]["type"] == "number"
    assert fillet["properties"]["edges"]["type"] == "array"
    assert fillet["properties"]["kind"]["default"] == "fillet"
    # and it is really the signature, not a copy of it
    for name, shape in schema.items():
        fn = getattr(session, "op_" + name)
        wanted = [p for p, v in inspect.signature(fn).parameters.items()
                  if p != "self" and v.default is inspect.Parameter.empty]
        assert shape["required"] == wanted, name


def test_a_document_can_be_opened_and_driven(session, tmp_path):
    out = payload(tool_call(session, "open", path="examples/bracket.json"))
    assert "width" in out["parameters"]
    built = payload(tool_call(session, "build"))
    assert built["volume_mm3"] > 0
    wider = payload(tool_call(session, "set_parameter", name="width", value=130.0))
    assert wider["volume_mm3"] > built["volume_mm3"]


def test_a_refusal_arrives_as_the_kernel_wrote_it(session):
    tool_call(session, "open", path="examples/bracket.json")
    answer = tool_call(session, "set_parameter", name="nope", value=1.0)
    assert answer["isError"] is True
    said = payload(answer)
    assert said["kind"] == "unknown_parameter"
    assert "known" in said["detail"]


def test_an_operation_that_does_not_exist_is_refused_not_crashed(session):
    answer = tool_call(session, "no_such_operation")
    assert answer["isError"] is True
    assert payload(answer)["kind"] == "unknown_op"


def test_the_undo_stack_reaches_a_tool_call(session):
    """An assistant's edit is on the same history as a person's."""
    tool_call(session, "open", path="examples/bracket.json")
    before = payload(tool_call(session, "build"))["volume_mm3"]
    tool_call(session, "set_parameter", name="width", value=130.0)
    after = payload(tool_call(session, "undo"))
    assert after["volume_mm3"] == pytest.approx(before)


def test_a_notification_is_not_answered(session):
    assert mcp.respond(session, {"jsonrpc": "2.0",
                                 "method": "notifications/initialized"}) is None


def test_an_unknown_method_is_reported_as_one(session):
    """Guards: a method this does not speak is -32601. Uses a method that does
    not exist and one this server deliberately does not implement, not a
    real method borrowed as an example."""
    for method in ("nonesuch/thing", "prompts/list"):
        with pytest.raises(LookupError):
            mcp.respond(session, {"jsonrpc": "2.0", "id": 2, "method": method})


def test_a_render_comes_back_as_a_picture(session, tmp_path):
    """Guards: a render comes back as an image beside the text, not only a path."""
    tool_call(session, "open", path="examples/bracket.json")
    shot = str(tmp_path / "iso.png")
    answer = tool_call(session, "render", path=shot, view="iso",
                       width=320, height=240)
    kinds = [c["type"] for c in answer["content"]]
    assert kinds == ["text", "image"]
    picture = answer["content"][1]
    assert picture["mimeType"] == "image/png"
    assert len(picture["data"]) > 1000
    assert payload(answer)["view"] == "iso"


def test_a_face_can_be_pointed_at(session, tmp_path):
    """A highlighted face is reported back by name."""
    tool_call(session, "open", path="examples/bracket.json")
    said = payload(tool_call(session, "render", path=str(tmp_path / "a.png"),
                             width=200, height=150, highlight=["plate/+z"]))
    assert said["highlighted"] == ["plate/+z"]


def test_a_view_that_does_not_exist_is_refused(session, tmp_path):
    tool_call(session, "open", path="examples/bracket.json")
    answer = tool_call(session, "render", path=str(tmp_path / "b.png"),
                       view="sideways")
    assert answer["isError"] is True


def test_attach_says_the_token_first_and_is_refused_without_one(monkeypatch, tmp_path):
    """The bridge in Blender admits a connection by a token this user's file
    holds; the attached server reads that file and says it first."""
    import socket

    ours, theirs = socket.socketpair()
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: ours)
    token_file = tmp_path / "bridge-token"
    token_file.write_text("s3cret", encoding="utf-8")
    monkeypatch.setenv("CADCORE_BRIDGE_TOKEN_FILE", str(token_file))
    # the bridge's side of the handshake, played by hand
    theirs.sendall(b'{"ok": true, "admitted": true}\n')
    attached = mcp.Attached("127.0.0.1:8765")
    first = theirs.makefile("r", encoding="utf-8").readline()
    assert json.loads(first) == {"token": "s3cret"}
    attached.stream.close()

    ours, theirs = socket.socketpair()
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: ours)
    theirs.sendall(b'{"ok": false, "kind": "not_admitted", "message": "no"}\n')
    with pytest.raises(SystemExit) as refused:
        mcp.Attached("127.0.0.1:8765")
    assert "did not admit" in str(refused.value)

    monkeypatch.delenv("CADCORE_BRIDGE_TOKEN_FILE")
    with pytest.raises(SystemExit) as refused:
        mcp.Attached("127.0.0.1:8765")
    assert "CADCORE_BRIDGE_TOKEN_FILE" in str(refused.value)


def test_stdout_carries_only_json_rpc(tmp_path):
    """OpenCASCADE prints to fd 1 on a STEP export; the line server ducks
    that and the MCP server did not, so thirteen lines of statistics landed
    between two replies."""
    import os
    import shutil
    import subprocess
    import sys

    shutil.copy(os.path.join("examples", "bracket.json"), tmp_path / "part.json")
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "open", "arguments": {"path": "part.json"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "export_step", "arguments": {"path": "p.step"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "build", "arguments": {}}}]
    environment = dict(os.environ, CAD_WORKSPACE=str(tmp_path),
                       PYTHONPATH=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
    proc = subprocess.run([sys.executable, "-m", "cadcore.service.mcp"],
                          input="".join(json.dumps(r) + "\n" for r in requests),
                          capture_output=True, text=True, timeout=300,
                          cwd=str(tmp_path), env=environment, encoding="utf-8")
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    replies = []
    for line in lines:
        replies.append(json.loads(line))        # every line, or the stream is broken
    assert [r.get("id") for r in replies if "id" in r] == [1, 2, 3, 4]
    assert (tmp_path / "p.step").exists()
