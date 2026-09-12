"""Progress: that it is real, that it is asked for, and that it costs nothing.

`simulate` and `optimize` are the two operations here that take tens of
seconds, and over a pipe a silent minute is indistinguishable from a server
that has hung. The temptation is a timer -- a tick a second, a bar filling at a
guessed rate -- and that is worse than silence, because it is a claim about how
far along the work is and it is wrong.

So what is checked here is that every report comes from a loop that really
finished something: as many reports as there were trials, as many as there were
studies and meshes, and none at all when nothing is running.
"""
from __future__ import annotations

import json
import os

import pytest

from cadcore.service import mcp
from cadcore import progress
from cadcore.ops.session import Session

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BRACKET = os.path.join(HERE, "examples", "bracket.json")


@pytest.fixture
def heard():
    """Everything reported inside the block, as (done, total, message)."""
    said = []

    class Listening:
        def __enter__(self):
            self.scope = progress.reported_to(
                lambda done, total, message: said.append((done, total, message)))
            self.scope.__enter__()
            return said

        def __exit__(self, *exc):
            return self.scope.__exit__(*exc)

    return Listening()


def test_nothing_is_reported_when_nobody_is_listening():
    assert not progress.watched()
    progress.step("into the void")           # and does not raise


def test_a_count_only_goes_up(heard):
    with heard as said:
        assert progress.watched()
        for _ in range(5):
            progress.step("a stage")
    assert [d for d, _, _ in said] == [1, 2, 3, 4, 5]


def test_the_first_total_declared_is_the_one_that_stands(heard):
    """An inner loop declaring its own total is a bar that jumps sideways."""
    with heard as said:
        progress.total(10)
        progress.total(3)                    # an inner loop, ignored
        progress.step("one")
    assert said == [(1, 10, "one")]


def test_a_trial_that_is_refused_still_counts(heard):
    """Two of the three ways out of the search loop are a `continue`.

    A trial the envelope refuses took just as long to find out about, so a
    count that moved only on the feasible ones would sit still through a search
    that is working perfectly well.
    """
    session = Session()
    session.op_open(path=BRACKET)
    with heard as said:
        found = session.op_optimize(trials=6, apply=False, objective="volume",
                                    step=5.0, seed=7)
    assert len(said) == 6
    assert found["feasible"] <= 6            # some were refused, or none were
    assert [d for d, _, _ in said] == [1, 2, 3, 4, 5, 6]
    assert all(total == 6 for _, total, _ in said)


def test_a_study_reports_its_meshes_and_then_itself(heard):
    """Each report names a real stage: a mesh solved, or a study finished."""
    session = Session()
    session.op_open(path=BRACKET)
    with heard as said:
        pytest.importorskip("ngsolve", reason="the studies need requirements-sim.txt")
        out = session.op_simulate()
    studies = [m for _, _, m in said if m.startswith("study ")]
    meshes = [m for _, _, m in said if m.startswith("mesh level ")]
    assert len(studies) == len(out["studies"])
    assert meshes, "the bracket declares a convergence block"
    # no total: how many meshes a study solves is read per study
    assert all(total is None for _, total, _ in said)
    assert "elements" in meshes[0]


# -- over MCP ------------------------------------------------------------------

def _run(session, calls):
    """Play a list of requests at the server, collecting what comes back."""
    out = []
    for call in calls:
        answer = mcp.respond(session, call, out.append)
        if answer is not None:
            out.append({"id": call.get("id"), "result": answer})
    return out


def _call(ident, name, arguments, token=None):
    params = {"name": name, "arguments": arguments}
    if token is not None:
        params["_meta"] = {"progressToken": token}
    return {"jsonrpc": "2.0", "id": ident, "method": "tools/call",
            "params": params}


def test_progress_crosses_the_wire_only_when_a_token_is_offered():
    session = Session()
    with_token = _run(session, [_call(1, "open", {"path": BRACKET}),
                                _call(2, "optimize",
                                      {"trials": 3, "apply": False,
                                       "objective": "volume"}, token="t1")])
    told = [m for m in with_token if m.get("method") == "notifications/progress"]
    assert len(told) == 3
    assert [m["params"]["progress"] for m in told] == [1, 2, 3]
    assert all(m["params"]["progressToken"] == "t1" for m in told)
    assert all(m["params"]["total"] == 3 for m in told)

    session = Session()
    without = _run(session, [_call(3, "open", {"path": BRACKET}),
                             _call(4, "optimize", {"trials": 3, "apply": False,
                                                   "objective": "volume"})])
    assert not [m for m in without if m.get("method") == "notifications/progress"]


def test_the_answer_still_arrives_after_the_progress():
    session = Session()
    got = _run(session, [_call(1, "open", {"path": BRACKET}),
                         _call(2, "optimize", {"trials": 2, "apply": False,
                                               "objective": "volume"},
                               token=7)])
    last = got[-1]
    assert last["id"] == 2
    payload = json.loads(last["result"]["content"][0]["text"])
    assert payload["trials"] == 2 and payload["parameters"]


def test_an_attached_session_reports_nothing_it_cannot_know():
    """The work happens in Blender, on the far side of a socket that answers
    once. A count invented on this side would be a count of nothing."""

    class Pretend(mcp.Attached):
        def __init__(self):
            pass

        def send(self, request):
            return {"ok": True, "result": {"trials": 3}}

    out = _run(Pretend(), [_call(1, "optimize", {"trials": 3}, token="t")])
    assert not [m for m in out if m.get("method") == "notifications/progress"]
    assert out[-1]["id"] == 1


def test_an_attached_session_offers_what_only_the_viewport_can_answer():
    """`selection` and `select` are Blender's, not the kernel's: listed only
    when the calls go to Blender, and forwarded there as they are."""

    class Pretend(mcp.Attached):
        def __init__(self):
            self.sent = []

        def send(self, request):
            self.sent.append(request)
            return {"ok": True, "result": {"faces": ["plate/+z"], "edges": []}}

    names = {t["name"] for t in mcp.tools(Pretend())}
    assert {"selection", "select"} <= names
    assert not ({"selection", "select"} & {t["name"] for t in mcp.tools(Session())})
    out = _run(Pretend(), [_call(1, "selection", {})])
    assert json.loads(out[-1]["result"]["content"][0]["text"])["faces"] == ["plate/+z"]
