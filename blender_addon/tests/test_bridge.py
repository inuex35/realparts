"""The bridge's fence and its lifetime, run without Blender: `bpy` is a stub."""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import socket
import sys
import types

import pytest


def _load(tmp_path):
    bpy = types.ModuleType("bpy")
    bpy.utils = types.SimpleNamespace(user_resource=lambda kind, path="", create=False: str(tmp_path))
    bpy.app = types.SimpleNamespace(timers=types.SimpleNamespace(is_registered=lambda fn: True,
                                                                 register=lambda *a, **k: None))
    bpy.path = types.SimpleNamespace(abspath=lambda p: p)
    bpy.context = types.SimpleNamespace(scene=None)
    sys.modules["bpy"] = bpy
    path = pathlib.Path(__file__).resolve().parents[1] / "link" / "bridge.py"
    spec = importlib.util.spec_from_file_location("cad_bridge_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("CAD_WORKSPACE", str(tmp_path))
    module = _load(tmp_path)
    yield module
    module.stop()
    sys.modules.pop("bpy", None)


CONTEXT = types.SimpleNamespace(scene=types.SimpleNamespace(cadcore=None))


def test_a_path_argument_is_fenced_whatever_it_ends_in(bridge, tmp_path):
    outside = bridge._outside_the_root
    assert outside(CONTEXT, {"path": "/etc/passwd"}) == "/etc/passwd"
    assert outside(CONTEXT, {"path": "../secret"}) == "../secret"
    assert outside(CONTEXT, {"path": "part.step"}) is None
    # nested: a feature's own path inside add_feature, apply and load_json
    assert outside(CONTEXT, {"type": "import_step", "args": {"path": "../../x"}}) == "../../x"
    assert outside(CONTEXT, {"ops": [{"op": "save", "path": str(tmp_path / "a.json")},
                                     {"op": "export_step", "path": "/tmp/out.step"}]}) == "/tmp/out.step"
    assert outside(CONTEXT, {"document": {"features": [
        {"id": "s", "type": "sketch", "file": {"path": "/somewhere/plan.dxf"}}]}}) == "/somewhere/plan.dxf"
    # a sketch id under `path` (a sweep's rail) is a name, and a name is inside
    assert outside(CONTEXT, {"type": "sweep", "args": {"profile": "p", "path": "rail"}}) is None
    # a file named under some other key is still caught by what it ends in
    assert outside(CONTEXT, {"picture": "/tmp/shot.png"}) == "/tmp/shot.png"


def test_a_bind_that_fails_leaves_nothing_listening(bridge, tmp_path):
    taken = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    taken.bind(("127.0.0.1", 0))
    taken.listen(1)
    try:
        with pytest.raises(OSError):
            bridge.start(port=taken.getsockname()[1])
    finally:
        taken.close()
    assert not bridge.listening()
    assert not os.path.exists(bridge.token_path())


def test_stop_ends_the_connections_it_admitted(bridge):
    address = bridge.start(port=0)
    port = int(address.rsplit(":", 1)[1])
    assert port > 0 and bridge.listening()
    token = open(bridge.token_path(), encoding="utf-8").read()
    client = socket.create_connection(("127.0.0.1", port), timeout=10)
    stream = client.makefile("rw", encoding="utf-8")
    stream.write(json.dumps({"token": token}) + "\n")
    stream.flush()
    assert json.loads(stream.readline())["admitted"] is True
    bridge.stop()
    assert stream.readline() == ""            # cut off, not left waiting on a dead bridge
    client.close()
    assert not bridge.listening() and not os.path.exists(bridge.token_path())
