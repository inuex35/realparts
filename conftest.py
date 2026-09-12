"""Shared fixtures: a document on disk and a session with it open.

A test that wants a particular document passes it to the fixture.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[0]
# put the repository on the path before importing from it, so the suite can be
# started from any directory
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cadcore.service.server import Session  # noqa: E402

EXAMPLES = ROOT / "examples"


@pytest.fixture(scope="session", autouse=True)
def _run_from_the_repository_root():
    """Run the suite from the repository root: many tests use paths written
    `examples/...` relative to it."""
    import os

    was = os.getcwd()
    os.chdir(EXAMPLES.parent)
    yield
    os.chdir(was)


@pytest.fixture(autouse=True)
def _leave_the_examples_alone():
    """Remove autosave sidecars after every test.

    Tests edit the shipped examples, and an edit writes its unsaved state
    beside the document; a sidecar left behind is offered for recovery by the
    next test or run. Per test, not per session, for that reason. This also
    covers tests that build their own session with autosave on.
    """
    yield
    for stale in EXAMPLES.rglob("*.autosave.json"):
        # missing_ok: with -n 8 two workers see the same sidecar and both
        # remove it; the loser used to fail the test it had just passed
        stale.unlink(missing_ok=True)


@pytest.fixture()
def write_document(tmp_path):
    """Write a document dict to a file and hand back the path."""
    def write(document: dict, name: str = "document.json") -> str:
        path = tmp_path / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return str(path)
    return write


@pytest.fixture()
def open_document(write_document):
    """A session with a written document open and built."""
    def open_it(document: dict, name: str = "document.json") -> Session:
        session = Session()
        session.op_open(write_document(document, name))
        session.op_build()
        return session
    return open_it


@pytest.fixture()
def open_example():
    """A session with one of the shipped examples open and built."""
    def open_it(name: str) -> Session:
        # no sidecar: the shipped examples are edited, and unsaved work
        # written beside one would be found and offered by a later run
        session = Session(autosave=False)
        session.op_open(str(EXAMPLES / name))
        session.op_build()
        return session
    return open_it


def plate(**overrides) -> dict:
    """A rectangular plate with one fixed corner: the standard test document.

    ``allow_underconstrained`` is set because most sketching tests add the
    constraints themselves.
    """
    document = {
        "parameters": {"t": 5},
        "features": [
            {"id": "profile", "type": "sketch", "allow_underconstrained": True,
             "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
             "points": {"a": [0, 0], "b": [40, 0], "c": [40, 20], "d": [0, 20]},
             "lines": {"s": ["a", "b"], "e": ["b", "c"], "n": ["c", "d"],
                       "w": ["d", "a"]},
             "constraints": [{"type": "fix", "point": "a", "at": [0, 0]}]},
            {"id": "plate", "type": "extrude", "sketch": "profile", "distance": "t"}],
        "result": "plate"}
    document.update(overrides)
    return document


@pytest.hookimpl(tryfirst=True)
def pytest_collect_directory(path, parent):
    """Collect `blender_addon/` as a plain directory, not a package.

    Its `__init__.py` imports bpy, which pytest would import before running the
    tests inside it. The add-on's tests load the file they test by path.
    """
    if path.name == "blender_addon" and path.parent == ROOT:
        return pytest.Dir.from_parent(parent, path=path)
    return None
