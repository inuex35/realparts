"""Standard screws, nuts and washers as parts, and a screw in the hole of its size."""
import pytest

from cadcore.errors import CadError
from cadcore.geometry.core.measure import face_frame, is_solid, volume
from cadcore.geometry.kernel import fastener
from cadcore.model import fasteners
from cadcore.service.server import Session


@pytest.mark.parametrize("kind, faces", [
    ("socket_head", {"top", "head", "under", "shank", "tip"}),
    ("hex_bolt", {"top", "flat", "flat#2", "flat#3", "flat#4", "flat#5", "flat#6",
                  "under", "shank", "tip"}),
    ("nut", {"+z", "-z", "bore", "flat", "flat#2", "flat#3", "flat#4", "flat#5", "flat#6"}),
    ("washer", {"+z", "-z", "bore", "outer"}),
])
def test_every_kind_is_a_solid_with_its_faces_named(kind, faces):
    body = fastener("m6", fasteners.part("M6", kind, 20))
    assert is_solid(body) and volume(body) > 0
    assert {name.split("/", 1)[1] for name in body.face_names()} == faces


def test_a_screw_hangs_below_the_plane_its_head_sits_on():
    body = fastener("m6", fasteners.part("M6", "socket_head", 20))
    assert face_frame(body, "m6/under")["origin"][2] == pytest.approx(0.0)
    assert face_frame(body, "m6/top")["origin"][2] == pytest.approx(6.0)     # ISO 4762 k
    assert face_frame(body, "m6/tip")["origin"][2] == pytest.approx(-20.0)
    turned = fastener("m6", fasteners.part("M6", "socket_head", 20), at=(10, 0, 5), axis=(0, 0, -1))
    assert face_frame(turned, "m6/under")["origin"] == pytest.approx([10.0, 0.0, 5.0])
    assert face_frame(turned, "m6/tip")["origin"][2] == pytest.approx(25.0)


def test_the_part_is_named_the_way_a_purchase_order_names_it():
    assert fasteners.part("M6", "socket_head", 20)["name"] == "M6 x 20 socket head cap screw"
    assert fasteners.part("m8", "nut")["name"] == "M8 hex nut"
    assert fasteners.part("M6", "socket_head")["length"] == 24.0        # four diameters


def test_an_unknown_size_or_kind_is_refused_with_the_list():
    with pytest.raises(CadError) as exc:
        fasteners.part("M7", "nut")
    assert exc.value.kind == "unknown_fastener" and "M6" in exc.value.detail["available"]
    with pytest.raises(CadError) as exc:
        fasteners.part("M6", "rivet")
    assert exc.value.kind == "unknown_fastener" and "washer" in exc.value.detail["available"]


def test_a_screw_fits_the_hole_of_its_size_and_the_bill_names_it():
    session = Session(autosave=False)
    session.op_open("examples/fastened.json")
    out = session.op_build()
    assert out["freedom"]["dof"] == 0 and not out["freedom"]["conflicts"]
    assert session.op_interference()["clear"]
    lines = {line["part"]: line for line in session.op_bill_of_materials()["parts"]}
    assert lines["screw"]["standard"] == "M6 x 20 socket head cap screw"
    assert lines["washer"]["standard"] == "M6 washer"
    assert lines["screw"]["material"] == "S45C" and lines["screw"]["mass_g"] > 5


def test_a_fastener_is_a_feature_an_assistant_can_add():
    session = Session(autosave=False)
    session.op_new_document()
    session.op_add_feature(type="fastener", args={"standard": "M4", "kind": "nut"}, feature_id="nut")
    assert "nut/bore" in session.op_build()["face_names"]
