"""Files in and out beyond STEP: healing, colours and materials, meshes, BREP."""
import pathlib

import pytest

from cadcore.model.document import Document
from cadcore.evaluation.graph import Evaluator
from cadcore.service.server import Session
from ..io import exchange, mesh
from ..core.measure import is_solid, volume

FOREIGN = pathlib.Path(__file__).resolve().parent / "data" / "foreign.step"


def box_session(tmp_path) -> Session:
    session = Session(autosave=False)
    session.op_new_document()
    session.op_add_feature(type="box", args={"size": [30, 20, 10]}, feature_id="blk")
    session.op_build()
    return session


def test_healing_a_foreign_part_keeps_it_a_valid_solid():
    if not FOREIGN.exists():
        pytest.skip("no foreign STEP fixture")
    body = exchange.import_step("part", str(FOREIGN), healed=True, tolerance=0.01)
    assert is_solid(body) and volume(body) > 0
    assert body.notes["import"]["healed"] and body.notes["import"]["valid"]


def test_healing_sews_loose_faces_into_a_solid():
    """A box written as six loose faces comes back as one solid when healed."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.TopoDS import TopoDS_Compound
    from ..core.naming import faces_of

    loose = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(loose)
    for face in faces_of(BRepPrimAPI_MakeBox(10, 20, 30).Shape()):
        builder.Add(loose, face)
    fixed, report = exchange.heal(loose, 1e-3)
    assert report["made_solid"] and report["valid"]
    from ..core.naming import Body
    assert volume(Body(fixed, [])) == pytest.approx(6000, abs=1e-3)


def test_a_colour_and_a_material_go_out_to_step_and_come_back(tmp_path):
    session = box_session(tmp_path)
    session.doc.meta = {"name": "blk", "colour": "#e63946", "material": "A6061"}
    out = tmp_path / "blk.step"
    session.op_export_step(path=str(out))
    back = exchange.import_step("in", str(out))
    assert back.notes["colours"] == {"in": "#e63946"}
    assert back.notes["materials"] == {"in": "A6061"}


def test_every_part_of_an_assembly_keeps_its_own_colour(tmp_path):
    part = tmp_path / "pin.json"
    part.write_text('{"features": [{"id": "pin", "type": "cylinder", "radius": 3, "height": 20}],'
                    ' "result": "pin"}', encoding="utf-8")
    doc = Document.from_dict({"features": [
        {"id": "left", "type": "part", "document": "pin.json", "colour": "#0000ff", "material": "brass"},
        {"id": "right", "type": "part", "document": "pin.json", "colour": "#00ff00"},
        {"id": "asm", "type": "assemble", "bodies": ["left", "right"],
         "mates": [{"kind": "distance", "faces": ["right:pin/side", "left:pin/side"], "offset": 10}]}],
        "result": "asm"})
    doc.source = str(tmp_path / "asm.json")
    body = Evaluator(doc).build("asm")
    assert body.notes["colours"] == {"left": "#0000ff", "right": "#00ff00"}
    assert body.notes["materials"] == {"left": "brass"}
    out = tmp_path / "asm.step"
    exchange.export_step(body, str(out), name="asm")
    back = exchange.import_step("in", str(out))
    assert back.notes["colours"] == {"left": "#0000ff", "right": "#00ff00"}
    assert back.notes["materials"] == {"left": "brass"}


@pytest.mark.parametrize("suffix", [".stl", ".obj", ".3mf", ".glb", ".gltf"])
def test_a_mesh_goes_out_and_comes_back_as_a_solid(tmp_path, suffix):
    session = box_session(tmp_path)
    out = tmp_path / ("blk" + suffix)
    assert session.op_export_mesh(path=str(out))["bytes"] > 0
    body = mesh.import_mesh("scan", str(out))
    assert is_solid(body) and body.notes["import"]["closed"]
    assert volume(body) == pytest.approx(6000, rel=1e-6)
    assert len(body.names) == 6, "coplanar facets merged into the six faces"
    assert "scan/+z" in {n for n, _ in body.names}


def test_a_mesh_file_opens_as_a_document(tmp_path):
    session = box_session(tmp_path)
    out = tmp_path / "blk.stl"
    session.op_export_mesh(path=str(out))
    other = Session(autosave=False)
    other.op_open(str(out))
    built = other.op_build()
    assert built["volume_mm3"] == pytest.approx(6000, rel=1e-6)
    assert other.doc.features[0].type == "import_mesh"


def test_an_open_mesh_is_a_shell_not_a_solid(tmp_path):
    path = tmp_path / "lid.obj"
    path.write_text("v 0 0 0\nv 10 0 0\nv 10 10 0\nv 0 10 0\nf 1 2 3 4\n", encoding="utf-8")
    body = mesh.import_mesh("lid", str(path))
    assert not is_solid(body) and not body.notes["import"]["closed"]
    assert len(body.names) == 1


def test_brep_goes_out_and_comes_back_exactly(tmp_path):
    session = box_session(tmp_path)
    session.op_add_fillet(edges=session.op_select_edges(query={"of_face": "blk/+z"})["edges"][:1],
                          radius=2.0)
    out = tmp_path / "blk.brep"
    session.op_export_step(path=str(out))
    body = exchange.import_brep("in", str(out))
    assert volume(body) == pytest.approx(session.op_build()["volume_mm3"], abs=1e-3)
    assert "in/side" in {n for n, _ in body.names}, "the fillet is still a cylinder face"


def test_a_bad_colour_is_refused():
    from cadcore.errors import CadError
    with pytest.raises(CadError) as caught:
        exchange.rgb_of("blue")
    assert caught.value.kind == "bad_parameter"
