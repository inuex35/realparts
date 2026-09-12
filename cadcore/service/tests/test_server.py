"""The protocol the add-on speaks: edits by name, typed refusals, clean stdout."""
import json
import os
import subprocess
import sys

import pytest

from cadcore.geometry.kernel import CadError
from cadcore.service.server import Session, handle

DOC = "examples/sketch_plate.json"


@pytest.fixture()
def session():
    s = Session()
    s.op_open(DOC)
    s.op_build()
    return s


def test_parameter_reaches_a_nested_expression(session):
    """Guards: a parameter used inside a sketch constraint still moves the
    solid, so the cache key must resolve nested arguments."""
    before = session.op_build()["volume_mm3"]
    wider = session.op_set_parameter("width", 118)["volume_mm3"]
    assert wider > before + 1.0
    assert session.op_set_parameter("width", 60)["volume_mm3"] < before - 1.0


def test_fillet_by_edge_name_survives_a_parameter_change(session):
    edges = session.op_select_edges({"between": ["plate/left", "plate/top"]})["edges"]
    assert edges == ["plate/left|plate/top"]
    out = session.op_add_fillet(edges, radius=2.5)
    assert out["feature"] == "fillet1"
    named = [n for n in session.op_tessellate()["face_table"] if n.startswith("fillet1/")]
    assert named
    session.op_set_parameter("width", 118)
    assert [n for n in session.op_tessellate()["face_table"] if n.startswith("fillet1/")] == named


def test_failed_fillet_leaves_the_document_buildable(session):
    with pytest.raises(CadError) as exc:
        session.op_add_fillet(["plate/left|plate/top"], radius=500.0)
    assert exc.value.kind in ("fillet_failed", "empty_result")
    assert session.op_build()["faces"] == 8
    assert [f["id"] for f in session.op_describe_document()["features"]] == \
           ["profile", "plate", "rounded"]


def test_removing_a_feature_relinks_or_refuses(session):
    with pytest.raises(CadError) as exc:
        session.op_remove_feature("profile")
    assert exc.value.kind == "feature_in_use"
    assert exc.value.detail["dependents"] == ["plate"]
    assert session.op_remove_feature("rounded")["faces"] == 7


def test_errors_arrive_as_data():
    reply = handle(Session(), {"op": "build"})
    assert reply == {"ok": False, "kind": "no_document",
                     "message": "open a document first", "detail": {}}
    assert handle(Session(), {"op": "nope"})["kind"] == "unknown_op"


def test_stdout_carries_only_protocol(tmp_path):
    """Guards: OpenCASCADE prints to fd 1 and must not derail the reply stream.

    Runs in a workspace of its own because a served session reads and writes
    only under its root.
    """
    import shutil

    shutil.copy(DOC, tmp_path / "part.json")
    requests = [{"id": 1, "op": "open", "path": "part.json"},
                {"id": 2, "op": "build"},
                {"id": 3, "op": "export_step", "path": "p.step"}]
    environment = dict(os.environ, CAD_WORKSPACE=str(tmp_path),
                       PYTHONPATH=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
    proc = subprocess.run([sys.executable, "-m", "cadcore.service.server"],
                          input="".join(json.dumps(r) + "\n" for r in requests),
                          capture_output=True, text=True, timeout=300,
                          cwd=str(tmp_path), env=environment, encoding="utf-8")
    replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert all(r["ok"] for r in replies)


def test_the_field_comes_back_on_the_cad_geometry():
    """A stress picture is only useful on the faces the designer can select."""
    s = Session()
    s.op_open("examples/bracket.json")
    pytest.importorskip("ngsolve", reason="the studies need requirements-sim.txt")
    out = s.op_simulate(deflection=0.6)
    mesh, field = out["mesh"], out["field"]
    assert field["study"] == out["studies"][0]["id"]
    assert len(field["vertex_values"]) == len(mesh["vertices"])
    assert field["unsampled_vertices"] == 0
    assert min(field["vertex_values"]) >= 0.0
    assert 0 < field["scale"] <= field["peak"]
    assert set(field["per_face"]) <= set(mesh["face_table"])
    hottest = max(field["per_face"], key=lambda n: field["per_face"][n]["max_MPa"])
    # clamped bolt holes and the loaded root, not an arbitrary corner
    assert hottest.startswith(("h1/", "h2/", "h3/", "h4/", "plate/", "root_fillet/"))


def test_the_addon_asks_for_no_fence_and_gets_none(tmp_path):
    """Guards: with `--unfenced` a document outside the root and the cwd
    opens; without it the open is refused as `bad_path`."""
    import json
    import shutil
    import subprocess
    import sys

    elsewhere = tmp_path / "documents"
    elsewhere.mkdir()
    doc = elsewhere / "bracket.json"
    shutil.copy(os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
                             "examples", "bracket.json"), doc)
    root = tmp_path / "root"
    root.mkdir()
    environment = dict(os.environ, CAD_WORKSPACE=str(root),
                       PYTHONPATH=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
    requests = [json.dumps({"op": "open", "id": 1, "path": str(doc)}),
                json.dumps({"op": "build", "id": 2})]
    for flags, expect_ok in (([], False), (["--unfenced"], True)):
        proc = subprocess.run([sys.executable, "-m", "cadcore.service.server", *flags],
                              input="\n".join(requests) + "\n",
                              capture_output=True, text=True, encoding="utf-8",
                              cwd=str(root), env=environment, timeout=600)
        replies = [json.loads(line) for line in proc.stdout.splitlines() if line]
        assert replies[0]["ok"] is expect_ok, (flags, replies[0])
        if not expect_ok:
            assert replies[0]["kind"] == "bad_path"
