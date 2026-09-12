"""The guard that turns an OCCT segfault in a fillet into a refusal.

No small shape in the repository reproduces the crash, so these tests check
the machinery: the helper identifies edges by position plus length and
answers "unknown" on a mismatch rather than giving a verdict; a helper that
dies without answering reads as a crash, never as a pass; and an ordinary
OCCT refusal stays a ``fillet_failed``.
"""
import json
import os
import subprocess
import sys

import pytest

from .. import kernel
from ..solids import sentry
from cadcore.errors import CadError
from ..solids.primitives import box


def _edges(body, count=None):
    table = body.edge_table()
    names = sorted(table)[:count] if count else sorted(table)
    return names, [table[n] for n in names]


def test_a_normal_fillet_still_builds_and_is_still_named():
    body = box("b", (40.0, 30.0, 20.0))
    names, _ = _edges(body, 1)
    out = kernel.fillet("r", body, names, 2.0)
    assert out.shape is not None
    assert out.names, "the fillet came back with no face names"


def test_the_helper_is_asked_about_the_edges_that_were_chosen():
    body = box("b", (40.0, 30.0, 20.0))
    names, edges = _edges(body, 3)
    picked = sentry._index_edges(body.shape, edges)
    assert picked is not None and len(picked) == 3
    # a position and the length it is supposed to be, so the far side can check
    for position, length in picked:
        assert isinstance(position, int) and length > 0
    assert sentry.survives("fillet", body.shape, edges, 2.0) == "ok"


def test_an_edge_the_walk_does_not_contain_is_not_guessed_at():
    body = box("b", (40.0, 30.0, 20.0))
    stranger = box("other", (5.0, 5.0, 5.0))
    _, edges = _edges(stranger, 1)
    assert sentry._index_edges(body.shape, edges) is None
    # and the caller is told nothing rather than told the wrong thing
    assert sentry.survives("fillet", body.shape, edges, 1.0) == "unknown"


def test_a_length_that_disagrees_is_refused_by_the_helper():
    """The length check ties a positional edge reference to the right edge,
    so the helper must consult it."""
    body = box("b", (40.0, 30.0, 20.0))
    _, edges = _edges(body, 2)
    picked = sentry._index_edges(body.shape, edges)
    picked[0][1] += 7.0                       # claim an edge is longer than it is
    from OCP.BinTools import BinTools
    import tempfile, os

    handle, path = tempfile.mkstemp(suffix=".brep")
    os.close(handle)
    try:
        BinTools.Write_s(body.shape, path)
        answer = sentry._ask(json.dumps(
            {"kind": "fillet", "shape": path, "size": 2.0, "edges": picked}))
    finally:
        os.unlink(path)
    assert answer == "unknown"


def test_a_helper_that_dies_without_answering_reads_as_a_crash(monkeypatch):
    """Guards: a dead helper reads as `crashed`, never as a pass."""
    def dying_helper():
        return subprocess.Popen(
            [sys.executable, "-c",
             "import sys, os; sys.stdin.readline(); os._exit(-11 & 0xff)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8")

    monkeypatch.setattr(sentry, "_start", dying_helper)
    assert sentry._ask('{"kind": "fillet"}') == "crashed"
    sentry._shut()


def test_a_crash_verdict_becomes_a_refusal_rather_than_a_dead_process(monkeypatch):
    monkeypatch.setattr(sentry, "survives", lambda *a, **k: "crashed")
    body = box("b", (40.0, 30.0, 20.0))
    names, _ = _edges(body, 1)
    with pytest.raises(CadError) as caught:
        kernel.fillet("r", body, names, 2.0)
    assert caught.value.kind == "fillet_crashed"
    assert "crashes OCCT" in caught.value.message


def test_a_helper_that_will_not_answer_in_time_is_not_a_pass(monkeypatch):
    def sleeping_helper():
        return subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; sys.stdin.readline(); time.sleep(30)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8")

    monkeypatch.setattr(sentry, "_start", sleeping_helper)
    monkeypatch.setattr(sentry, "_TIMEOUT", 1.0)
    assert sentry._ask('{"kind": "fillet"}') == "timed_out"
    sentry._shut()


def test_an_ordinary_refusal_is_still_an_ordinary_refusal():
    """A radius wider than the block is a `fillet_failed`; the guard must not
    report it as a crash."""
    body = box("b", (40.0, 30.0, 20.0))
    names, _ = _edges(body, 1)
    with pytest.raises(CadError) as caught:
        kernel.fillet("r", body, names, 500.0)
    assert caught.value.kind == "fillet_failed"


def test_it_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("CAD_SENTRY", "0")
    assert not sentry.enabled()
    body = box("b", (40.0, 30.0, 20.0))
    _, edges = _edges(body, 1)
    assert sentry.survives("fillet", body.shape, edges, 2.0) == "unknown"


@pytest.mark.skipif(not os.path.exists("/bin/cat"), reason="needs a talker")
def test_an_executable_that_is_not_a_python_stops_it_asking(monkeypatch):
    """Inside Blender `sys.executable` is the Blender binary. A helper that
    cannot start reads as `unknown` (no helper), never as `crashed`, or the
    add-on would refuse every fillet."""
    monkeypatch.setattr(sentry, "_BROKEN", False)
    monkeypatch.setattr(sentry, "_HELPER", None)
    monkeypatch.setattr(sentry, "_HANDSHAKE", 10.0)
    monkeypatch.setattr(sys, "executable", "/bin/cat")   # talks, but not JSON
    assert sentry._start() is None
    assert sentry._BROKEN
    assert sentry._ask('{"kind": "fillet"}') == "unknown"


def test_once_it_has_given_up_it_does_not_keep_trying(monkeypatch):
    tries = []

    def counted(*args, **kwargs):
        tries.append(1)
        raise OSError("no such executable")

    monkeypatch.setattr(sentry, "_BROKEN", False)
    monkeypatch.setattr(sentry, "_HELPER", None)
    monkeypatch.setattr(subprocess, "Popen", counted)
    assert sentry._start() is None
    assert sentry._start() is None
    assert len(tries) == 1, "a helper that cannot start was started twice"


def test_a_defeature_that_never_returns_is_refused_in_time(monkeypatch):
    """Removing a thread flank from the bottle with healing ran for more than
    ten minutes in OCCT and had not come back; the add-on soak found it as a
    hang. The second process tries it first, and a short wait is a typed
    refusal."""
    import time

    from cadcore.geometry.solids import surfaces
    from cadcore.service.server import Session

    monkeypatch.setattr(surfaces, "DEFEATURE_PATIENCE", 5.0)
    s = Session(autosave=False)
    s.op_open("examples/bottle.json")
    s.op_build()
    started = time.monotonic()
    with pytest.raises(CadError) as refused:
        s.op_delete_faces(faces=["screw_thread/face0"])
    assert refused.value.kind == "defeature_failed"
    assert refused.value.detail["verdict"] == "timed_out"
    assert time.monotonic() - started < 60
    # and a face that can go, still goes
    assert s.op_delete_faces(faces=["blank/base"])["faces"] > 0
