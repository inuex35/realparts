"""Tests for how `blender_addon.link.client` chooses the kernel interpreter.

Blender's own Python (`sys.executable`) runs the kernel, with the packages
installed into a versioned site directory; a `.venv` beside the repository
or an explicitly named interpreter wins over it. `client` imports no `bpy`,
so these run outside Blender.
"""
from __future__ import annotations

import os
import sys

import importlib.util

import pytest

# Loaded by path: importing the package would import bpy.
_spec = importlib.util.spec_from_file_location(
    "cadcore_client", os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")), "blender_addon", "link", "client.py"))
client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(client)


def test_blenders_own_python_is_the_running_one():
    """Outside Blender, `blender_python()` is the running interpreter."""
    assert client.blender_python() == sys.executable


def test_a_kernel_directory_is_versioned_by_the_interpreter(tmp_path):
    prefix = client.kernel_prefix(str(tmp_path))
    assert prefix.endswith("cp%d%d" % sys.version_info[:2])
    assert prefix.startswith(str(tmp_path))


def test_the_site_is_where_this_platform_puts_one(tmp_path):
    """The site directory is the one pip `--prefix` fills:
    `lib/pythonX.Y/site-packages`, or `Lib\\site-packages` on Windows."""
    import sysconfig

    prefix = client.kernel_prefix(str(tmp_path))
    site = client.kernel_site(str(tmp_path))
    assert site.startswith(prefix) and site != prefix
    assert site == sysconfig.get_path("purelib",
                                      vars={"base": prefix, "platbase": prefix})


def test_a_named_interpreter_is_taken_as_given(tmp_path):
    python, site = client.kernel_for(str(tmp_path), preferred="/opt/py/bin/python")
    assert python == "/opt/py/bin/python"
    assert site is None


def test_a_developers_venv_wins_over_the_installed_kernel(tmp_path):
    venv = client.kernel_python(str(tmp_path))
    os.makedirs(os.path.dirname(venv))
    open(venv, "w", encoding="utf-8").close()
    python, site = client.kernel_for(str(tmp_path))
    assert python == venv and site is None


def test_with_nothing_installed_it_is_blenders_python_and_a_site_to_fill(tmp_path):
    """A fresh install: Blender's Python and a site directory that does not
    exist yet. Not an error."""
    python, site = client.kernel_for(str(tmp_path))
    assert python == sys.executable
    assert site == client.kernel_site(str(tmp_path))
    assert not os.path.exists(site)


def test_a_missing_kernel_is_refused_in_the_buyers_words(tmp_path):
    """Guards: the refusal is `no_kernel` with an install hint, not a
    missing-interpreter error."""
    python, site = client.kernel_for(str(tmp_path))
    with pytest.raises(client.ServerError) as raised:
        client.Client(python, str(tmp_path), site).start()
    assert raised.value.kind == "no_kernel"
    assert "Install the CAD kernel" in raised.value.detail["hint"]


def test_an_installed_kernel_is_put_first_on_the_path(tmp_path, monkeypatch):
    """PYTHONPATH order is site, repository, inherited; PYTHONNOUSERSITE keeps
    a stray OCP in ~/.local from shadowing the installed one."""
    import subprocess

    site = client.kernel_site(str(tmp_path))
    os.makedirs(site)
    seen = {}

    class Fake:
        stdout = None
        stdin = None

        def poll(self):
            return None

    def popen(cmd, cwd, env, **kw):
        seen["cmd"], seen["env"] = cmd, env
        return Fake()

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    client.Client(sys.executable, str(tmp_path), site).start()
    path = seen["env"]["PYTHONPATH"].split(os.pathsep)
    assert path == [site, str(tmp_path), "/somewhere/else"]
    assert seen["env"]["PYTHONNOUSERSITE"] == "1"
    assert seen["cmd"][:1] == [sys.executable]


def test_a_venv_kernel_gets_no_path_of_ours(tmp_path, monkeypatch):
    import subprocess

    seen = {}

    class Fake:
        stdout = stdin = None

        def poll(self):
            return None

    monkeypatch.setattr(subprocess, "Popen",
                        lambda cmd, cwd, env, **kw: seen.update(env=env) or Fake())
    monkeypatch.delenv("PYTHONPATH", raising=False)
    client.Client(sys.executable, str(tmp_path), None).start()
    assert "PYTHONPATH" not in seen["env"]
    assert "PYTHONNOUSERSITE" not in seen["env"]


# -- what the assistant is handed ------------------------------------------------

def test_the_assistants_command_is_the_kernels_own_decision(tmp_path):
    """The assistant runs the kernel with the same interpreter and packages."""
    site = client.kernel_site(str(tmp_path))
    spec = client.assistant_command(sys.executable, site, str(tmp_path))
    assert spec["command"] == sys.executable
    assert spec["args"][:3] == ["-m", "cadcore.service.mcp", "--attach"]
    assert spec["env"]["PYTHONPATH"].split(os.pathsep) == [site, str(tmp_path)]
    assert spec["env"]["PYTHONNOUSERSITE"] == "1"


def test_a_venv_kernel_hands_the_assistant_no_path(tmp_path):
    spec = client.assistant_command("/v/bin/python", None, str(tmp_path))
    assert spec["env"]["PYTHONPATH"] == str(tmp_path)


def test_both_config_formats_parse_back_to_the_same_entry(tmp_path):
    """Guards: backslashes in a Windows path survive both config formats."""
    import json
    import tomllib

    spec = client.assistant_command(r"C:\\Blender\\5.2\\python\\bin\\python.exe",
                                    r"C:\\Users\\me\\.kernel\\cp313\\Lib\\site-packages",
                                    r"C:\\Users\\me\\kernel")
    claude = json.loads(client.assistant_config("claude", spec))
    assert claude["mcpServers"]["cadcore"] == spec
    codex = tomllib.loads(client.assistant_config("codex", spec))
    assert codex["mcp_servers"]["cadcore"] == spec


def test_an_unknown_assistant_is_refused_by_name():
    with pytest.raises(ValueError) as raised:
        client.assistant_config("gemini", {"command": "x", "args": [], "env": {}})
    assert "claude" in str(raised.value) and "codex" in str(raised.value)


def test_wheels_a_zip_carried_are_only_counted_when_blender_installed_them():
    """`bundled_kernel` answers with the directory Blender's extension system
    filled from a manifest. Outside Blender there is no such directory, and an
    OCP that happens to be importable here is somebody else's."""
    assert client.bundled_kernel() is None
