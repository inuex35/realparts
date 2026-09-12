"""Sketching on a named face: the frame has to be right, and has to follow."""
import pytest

from cadcore import Document
from cadcore.evaluation.graph import Evaluator
from cadcore.geometry.kernel import CadError, face_frame
from ..core.naming import face_info
from cadcore.service.server import Session

from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.TopAbs import TopAbs_State
from OCP.gp import gp_Pnt


def outward(body) -> list:
    """Names of faces whose normal points into the solid; expected empty.

    OCCT does not guarantee a face's orientation matches its place in the
    shell. An inward normal flips a +z/-z role and sends a pocket the wrong way.
    """
    bad = []
    for name, face in body.names:
        info = face_info(face)
        if "normal" not in info:
            continue
        c, n = info["centre"], info["normal"]
        cls = BRepClass3d_SolidClassifier(body.shape)
        cls.Perform(gp_Pnt(*[c[i] + n[i] * 0.05 for i in range(3)]), 1e-7)
        if cls.State() == TopAbs_State.TopAbs_IN:
            bad.append(name)
    return bad


@pytest.mark.parametrize("path", ["examples/bracket.json", "examples/sketch_plate.json"])
def test_named_faces_point_outward(path):
    assert outward(Evaluator(Document.load(path)).build()) == []


def test_frame_is_derived_from_the_face_not_from_occt():
    body = Evaluator(Document.load("examples/sketch_plate.json")).build()
    frame = face_frame(body, "plate/top")
    assert frame["normal"] == [0.0, 0.0, 1.0]
    assert frame["x_axis"] == [1.0, 0.0, 0.0]
    with pytest.raises(CadError) as exc:
        face_frame(body, "rounded/side")
    assert exc.value.kind == "non_planar_face"
    with pytest.raises(CadError) as exc:
        face_frame(body, "plate/nope")
    assert exc.value.kind == "unknown_face"


@pytest.fixture()
def session():
    s = Session()
    s.op_open("examples/sketch_plate.json")
    s.op_build()
    return s


def test_pocket_cuts_and_boss_adds(session):
    base = session.op_build()["volume_mm3"]
    assert session.op_add_pocket("plate/top", depth=3, width=30, height=20)["volume_mm3"] == \
        pytest.approx(base - 1800, abs=0.5)
    assert session.op_add_pocket("plate/top", depth=4, width=10, height=10,
                                 kind="boss")["volume_mm3"] == pytest.approx(base - 1400, abs=0.5)


def test_the_pocket_follows_the_face_it_was_drawn_on(session):
    session.op_add_pocket("plate/top", depth=3, width=30, height=20)
    body = session.body
    before = face_info(body.face("pocket1/floor"))["centre"]
    session.op_set_parameter("width", 118)
    after = face_info(session.body.face("pocket1/floor"))["centre"]
    # the top face's centroid moves when the plate widens, and the pocket with it
    assert after[0] > before[0] + 5
    assert after[2] == pytest.approx(before[2])


def test_a_pocket_needs_a_planar_face(session):
    with pytest.raises(CadError) as exc:
        session.op_add_pocket("rounded/side", depth=2, width=5, height=5)
    assert exc.value.kind == "non_planar_face"
    assert session.op_build()["faces"] == 8              # the document is untouched


def test_removing_a_pocket_takes_its_sketch(session):
    session.op_add_pocket("plate/top", depth=3, width=30, height=20)
    out = session.op_remove_feature("pocket1")
    assert set(out["removed"]) == {"pocket1", "pocket1_profile"}
    assert [f["id"] for f in session.op_describe_document()["features"]] == \
        ["profile", "plate", "rounded"]
