"""Distance between faces, mass properties, and the curvature of a face."""
import math

import pytest

from cadcore.errors import CadError
from cadcore.service.server import Session


def block() -> Session:
    session = Session(autosave=False)
    session.op_new_document()
    session.op_add_feature(type="box", args={"size": [40, 20, 10]}, feature_id="blk")
    session.op_build()
    return session


def test_the_distance_between_two_faces_is_the_shortest_one():
    session = block()
    assert session.op_measure(kind="distance", faces=["blk/+x", "blk/-x"])["value"] == pytest.approx(40)
    session.op_add_hole(face="blk/+z", diameter=8, at=[10, 0])
    out = session.op_measure(kind="distance", faces=["hole1/bore", "blk/+x"])
    assert out["value"] == pytest.approx(20 - 10 - 4) and out["unit"] == "mm"
    assert "distance" in session.op_measure_kinds()["kinds"]


def test_mass_properties_read_the_density_from_the_material():
    session = block()
    out = session.op_mass_properties()
    assert out["volume_mm3"] == pytest.approx(8000) and out["centre_of_mass"] == [0, 0, 0]
    assert out["material"] == "A6061" and out["mass_g"] == pytest.approx(8000 * 2.7e-3, rel=1e-6)
    # a box's inertia about its centre: m (b^2 + c^2) / 12
    assert out["inertia_g_mm2"][0][0] == pytest.approx(out["mass_g"] * (20 ** 2 + 10 ** 2) / 12, rel=1e-6)
    steel = session.op_mass_properties(material="S45C")
    assert steel["mass_g"] > out["mass_g"]


def test_a_flat_face_has_no_curvature_and_a_bore_bends_inwards():
    session = block()
    flat = session.op_curvature(face="blk/+z")
    assert flat["tightest"]["curvature"] == 0 and flat["tightest"]["radius_mm"] is None
    assert flat["normal"] == [0, 0, 1]
    session.op_add_hole(face="blk/+z", diameter=8, at=[0, 0])
    bore = session.op_curvature(face="hole1/bore", at=[4, 0, 0])
    assert bore["tightest"]["radius_mm"] == pytest.approx(-4), "concave: bends towards the axis"
    assert bore["flattest"]["curvature"] == 0
    assert math.hypot(*bore["at"][:2]) == pytest.approx(4)
    assert bore["normal"][0] == pytest.approx(-1), "the outward normal of a bore points in"


def test_curvature_of_a_face_that_is_not_there_is_refused():
    session = block()
    with pytest.raises(CadError) as caught:
        session.op_curvature(face="blk/nowhere")
    assert caught.value.kind == "unresolved_reference"
