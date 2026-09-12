"""Driving and exploding an assembly through the session."""
import pytest

from cadcore.errors import CadError
from cadcore.service.server import Session

LINKAGE = "examples/linkage.json"


@pytest.fixture
def linkage():
    session = Session(autosave=False)
    session.op_open(LINKAGE)
    session.op_build()
    return session


def test_the_document_drives_its_own_mechanism(linkage):
    """The crank angle is a parameter: building at another angle moves the links."""
    before = linkage.body.notes["assembly"]["poses"]["rocker"][1]
    linkage.op_set_parameter("crank_angle", 150)
    out = linkage.op_build()
    after = linkage.body.notes["assembly"]["poses"]["rocker"][1]
    assert before != after
    assert out["freedom"]["drives"] == [{"part": "crank", "turn": 150.0, "axis": [0.0, 0.0, 1.0]}]
    assert out["freedom"]["dof"] == 0            # held at that angle, nothing is free


def test_a_drive_answers_frames_and_leaves_the_document_alone(linkage):
    revision = linkage.doc.revision
    out = linkage.op_drive("crank", turn=180, frames=4, collisions=True)
    assert len(out["frames"]) == 4 and len(out["collisions"]) == 4
    assert set(out["frames"][0]) == {"frame", "crank", "coupler", "rocker"}
    assert out["scopes"]["crank"] == "crank"
    assert all(not clash for clash in out["collisions"])
    assert linkage.doc.revision == revision


def test_a_drive_the_mates_refuse_is_named(linkage):
    with pytest.raises(CadError) as exc:
        linkage.op_drive("crank", slide=5)
    assert exc.value.kind == "no_freedom"
    with pytest.raises(CadError) as exc:
        linkage.op_drive("frame", turn=5)
    assert exc.value.kind == "no_freedom"


def test_a_drive_needs_mates_solved_together():
    session = Session(autosave=False)
    session.op_open("examples/assembly.json")          # one ordered mate
    with pytest.raises(CadError) as exc:
        session.op_drive("bush", turn=10)
    assert exc.value.kind == "not_an_assembly"


def test_the_exploded_view_takes_a_stack_apart(linkage):
    out = linkage.op_explode(factor=2.0)
    offsets = out["offsets"]
    assert offsets["frame"] == [0.0, 0.0, 0.0]
    # every link comes off along the hinge axis, each farther than the one under it
    assert [offsets[p][2] for p in ("crank", "coupler", "rocker")] == [12.0, 24.0, 36.0]
    assert out["scopes"] == {"frame": "frame", "crank": "crank", "coupler": "coupler",
                             "rocker": "rocker"}


def test_the_exploded_view_pulls_a_bush_out_of_its_bore():
    session = Session(autosave=False)
    session.op_open("examples/assembly.json")
    session.op_build()
    out = session.op_explode()
    assert out["offsets"]["base"] == [0.0, 0.0, 0.0]
    assert out["scopes"]["fitted"] == "bush"           # placed by the mate, named by the part
    moved = out["offsets"]["fitted"]
    assert abs(moved[1]) > 10 and moved[0] == 0.0 and moved[2] == 0.0   # along the bore


def test_one_part_cannot_be_exploded():
    session = Session(autosave=False)
    session.op_open("examples/bracket.json")
    session.op_build()
    with pytest.raises(CadError) as exc:
        session.op_explode()
    assert exc.value.kind == "not_an_assembly"


def test_a_mate_can_be_added_from_two_faces():
    """Guards: the add-on's mate button goes through this; it makes an
    assemble when there is none and appends to it when there is."""
    session = Session(autosave=False)
    session.op_open("examples/assembly_solved.json")
    session.op_build()
    before = len(session.doc.feature("asm").args["mates"])
    out = session.op_add_mate(["bush:flange/+z", "base:wall/-y@1"], kind="planar", offset=0)
    assert out["mate"] == before
    assert len(session.doc.feature("asm").args["mates"]) == before + 1
    assert not out["freedom"]["conflicts"]
    session.op_undo()
    assert len(session.doc.feature("asm").args["mates"]) == before


def test_a_mate_the_others_refuse_leaves_nothing_behind():
    session = Session(autosave=False)
    session.op_open("examples/assembly_solved.json")
    session.op_build()
    before = len(session.doc.feature("asm").args["mates"])
    with pytest.raises(CadError) as exc:
        session.op_add_mate(["bush:flange/+z", "base:wall/-y@1"], kind="planar", offset=3)
    assert exc.value.kind == "over_constrained"
    assert len(session.doc.feature("asm").args["mates"]) == before
