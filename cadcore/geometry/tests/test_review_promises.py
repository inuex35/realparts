"""Name promises re-applied to the operations added later: rotations, moved and
deleted faces, and files that carry units and orientation."""
import json

import pytest

from ...errors import CadError
from ..core.measure import face_frame, mass_properties, volume
from ..core.naming import Body, faces_of, role_names
from ..solids import primitives, surfaces, transform
from ..assembly import assembly
from ..io import mesh


def test_a_rotated_copy_is_named_for_the_way_it_faces_now():
    blk = primitives.box("blk", (10, 10, 10), at=(30, 0, 0))
    out = transform.circular_pattern("pat", blk, (0, 0, 0), (0, 0, 1), 4)
    for name in ("blk/+x~1", "blk/+y~1", "blk/-x~1", "blk/-y~1", "blk/+x~2", "blk/+z~3"):
        normal = face_frame(out, name)["normal"]
        role = name.split("/")[1].split("~")[0]
        axis = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1)}[role]
        assert sum(normal[i] * axis[i] for i in range(3)) == pytest.approx(1.0, abs=1e-6), name
    # a turn that lands on no axis keeps the names it had
    six = transform.circular_pattern("pat", blk, (0, 0, 0), (0, 0, 1), 6)
    assert "blk/+x~1" in {n for n, _ in six.names}


def test_a_placed_part_is_named_for_the_way_it_faces_now():
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    blk = primitives.box("blk", (10, 20, 30))
    trsf = gp_Trsf()
    trsf.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 3.141592653589793)
    turned = assembly.transformed(blk, trsf, rename=True)
    assert face_frame(turned, "blk/+x")["normal"][0] == pytest.approx(1.0)
    assert face_frame(turned, "blk/-y")["normal"][1] == pytest.approx(-1.0)
    # a placed part keeps its own names: its mates spell them
    placed = assembly.transformed(blk, trsf)
    assert face_frame(placed, "blk/+x")["normal"][0] == pytest.approx(-1.0)


def test_a_moved_face_is_not_reported_dead():
    blk = primitives.box("blk", (10, 10, 10))
    out = surfaces.move_face("mv", blk, "blk/+z", 2.0)
    assert out.face("blk/+z") is not None
    assert "blk/+z" not in out.dropped and not any(n.startswith("mv_step") for n in out.dropped)
    assert volume(out) == pytest.approx(1200.0)


def test_deleting_a_face_by_its_alias_deletes_it():
    blk = primitives.box("blk", (10, 10, 10))
    blk.aliases["blk/top"] = "blk/+z"
    out = surfaces.delete_face("d", blk, ["blk/top"], heal=False)
    assert len(out.names) == 5 and out.face("blk/+z") is None


def test_a_cylinder_held_as_a_spline_is_still_round_to_a_mate():
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert

    pin = primitives.cylinder("pin", 4.0, 20.0)
    shape = BRepBuilderAPI_NurbsConvert(pin.shape, True).Shape()
    body = Body(shape, role_names("pin", faces_of(shape)))
    assert "pin/side" in {n for n, _ in body.names}
    origin, direction, radius = assembly.axis_of(body, "pin/side")
    assert radius == pytest.approx(4.0, abs=1e-4)
    assert abs(direction[2]) == pytest.approx(1.0, abs=1e-6)
    assert assembly.mate_geometry(body, "pin/side")["shape"] == "cylinder"


def test_the_inertia_is_about_the_centre_of_mass():
    blk = primitives.box("blk", (10, 10, 10), at=(50, 0, 0))
    inertia = mass_properties(blk)["inertia_mm5"]
    assert inertia[1][1] == pytest.approx(1000 * 200 / 12, rel=1e-6)     # a cube's own I, not shifted by 50


def test_gltf_is_written_in_metres_y_up_and_read_back_in_millimetres(tmp_path):
    blk = primitives.box("blk", (10, 20, 30))
    path = tmp_path / "blk.gltf"
    mesh.write_gltf(blk, str(path))
    said = json.loads(path.read_text(encoding="utf-8"))
    tops = [a["max"] for a in said["accessors"] if a.get("type") == "VEC3" and "max" in a]
    top = max(tops, key=lambda m: max(m))
    assert max(top) == pytest.approx(0.015, abs=1e-6), "metres, not millimetres"
    assert top[1] == pytest.approx(0.015, abs=1e-6), "the 30 mm height is along Y"
    back = mesh.import_mesh("in", str(path))
    assert volume(back) == pytest.approx(6000.0, rel=1e-6)
    assert "in/+z" in {n for n, _ in back.names}, "Z up again on the way in"


def test_a_3mf_build_places_its_items(tmp_path):
    import zipfile

    model = """<?xml version="1.0"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
 <resources><object id="1" type="model"><mesh>
  <vertices>
   <vertex x="0" y="0" z="0"/><vertex x="10" y="0" z="0"/><vertex x="10" y="10" z="0"/><vertex x="0" y="10" z="0"/>
   <vertex x="0" y="0" z="10"/><vertex x="10" y="0" z="10"/><vertex x="10" y="10" z="10"/><vertex x="0" y="10" z="10"/>
  </vertices>
  <triangles>
   <triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="3" v3="2"/>
   <triangle v1="4" v2="5" v3="6"/><triangle v1="4" v2="6" v3="7"/>
   <triangle v1="0" v2="1" v3="5"/><triangle v1="0" v2="5" v3="4"/>
   <triangle v1="1" v2="2" v3="6"/><triangle v1="1" v2="6" v3="5"/>
   <triangle v1="2" v2="3" v3="7"/><triangle v1="2" v2="7" v3="6"/>
   <triangle v1="3" v2="0" v3="4"/><triangle v1="3" v2="4" v3="7"/>
  </triangles></mesh></object></resources>
 <build><item objectid="1"/><item objectid="1" transform="1 0 0 0 1 0 0 0 1 30 0 0"/></build>
</model>"""
    path = tmp_path / "two.3mf"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", model)
    vertices, triangles = mesh.read_triangles(str(path))
    assert len(vertices) == 16 and len(triangles) == 24
    assert max(v[0] for v in vertices) == pytest.approx(40.0)


def test_a_round_sheet_has_an_outline_round_its_edge():
    from ...sketching import solve
    from ..solids import sheet

    solved = solve({"points": {"o": (0.0, 0.0)},
                    "circles": {"round": {"centre": "o", "radius": 20}},
                    "allow_underconstrained": True}, lambda v: v)
    blank = sheet.sheet("sh", solved, 2.0)
    outline = blank.notes["sheet"]["outline"]
    assert len(outline) > 8
    assert all(abs((x * x + y * y) ** 0.5 - 20) < 1e-6 for x, y in outline)
