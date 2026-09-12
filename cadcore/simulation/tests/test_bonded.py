"""A study of an assembly glues the parts where they touch."""
import json

import pytest

pytest.importorskip("ngsolve", reason="the studies need requirements-sim.txt")

from cadcore.errors import CadError  # noqa: E402
from cadcore.geometry.assembly.assembly import bonded, solids_of  # noqa: E402
from cadcore.geometry.core.measure import is_solid, volume  # noqa: E402
from cadcore.service.server import Session  # noqa: E402


def stacked(tmp_path, apart: float = 0.0) -> str:
    """Two blocks, the top one on the bottom one, or floating ``apart`` above it."""
    raw = {"meta": {"material": "A6061"},
           "features": [
               {"id": "low", "type": "box", "size": [40, 20, 10], "at": [0, 0, 0], "centred": False},
               {"id": "high", "type": "box", "size": [40, 20, 10], "at": [0, 0, 0], "centred": False},
               {"id": "asm", "type": "assemble", "bodies": ["low", "high"], "ground": "low",
                "mates": [{"kind": "planar", "faces": ["high/-z", "low/+z"], "offset": apart}]}],
           "studies": [{"id": "press", "type": "static_structural", "fix": ["low/-z"],
                        "loads": [{"face": "high/+z", "force": [0, 0, -500]}],
                        "mesh_size": 8}],
           "result": "asm"}
    path = tmp_path / "stack.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return str(path)


def test_touching_parts_are_studied_as_one_solid(tmp_path):
    session = Session(autosave=False)
    session.op_open(stacked(tmp_path))
    session.op_build()
    glued = bonded(session.body)
    assert is_solid(glued) and len(solids_of(glued)) == 1
    assert volume(glued) == pytest.approx(2 * 40 * 20 * 10, rel=1e-6)
    out = session.op_simulate()
    study = out["studies"][0]
    assert study["ok"] and study["result"]["max_displacement_mm"] > 0


def test_a_part_that_touches_nothing_is_refused(tmp_path):
    session = Session(autosave=False)
    session.op_open(stacked(tmp_path, apart=3.0))
    session.op_build()
    with pytest.raises(CadError) as exc:
        session.op_simulate()
    assert exc.value.kind == "parts_not_touching"
    assert exc.value.detail["parts"] == ["high", "low"]
