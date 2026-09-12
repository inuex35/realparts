"""Mesh output and the checks that decide whether a part will print."""
import struct
import zipfile

import pytest

from cadcore import Document
from cadcore.geometry import kernel
from cadcore.errors import CadError
from cadcore.evaluation.graph import Evaluator
from cadcore.analysis.meshio import write_3mf, write_stl
from cadcore.analysis.printability import check
from cadcore.service.server import Session


@pytest.fixture(scope="module")
def drilled():
    """The bracket with two M3 blind holes: 2.5 mm wide, 6 deep in an 8 mm
    plate, so each leaves a 2 mm floor. Nothing shipped is drilled that fine,
    and the checks below are about what is too small and too thin."""
    session = Session(autosave=False)
    session.op_open("examples/bracket.json")
    session.op_build()
    session.op_add_hole(face="plate/+z", diameter=2.5, depth=6.0, at=(0.0, 20.0),
                        count=2, spacing=20.0, feature_id="tapped")
    return Evaluator(session.doc).build()


def test_stl_is_a_binary_stl(tmp_path, drilled):
    path = str(tmp_path / "part.stl")
    out = write_stl(drilled, path, deflection=0.2)
    data = open(path, "rb").read()
    assert struct.unpack("<I", data[80:84])[0] == out["triangles"]
    assert len(data) == 84 + 50 * out["triangles"]      # the format, exactly

    text = str(tmp_path / "part_ascii.stl")
    write_stl(drilled, text, deflection=0.5, binary=False)
    body = open(text, encoding="utf-8").read()
    assert body.startswith("solid ") and body.rstrip().endswith("endsolid RealParts")
    assert body.count("facet normal") == body.count("endfacet")


def test_3mf_is_a_valid_package(tmp_path, drilled):
    path = str(tmp_path / "part.3mf")
    out = write_3mf(drilled, path, deflection=0.2, name="bracket")
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == {"[Content_Types].xml", "_rels/.rels",
                                           "3D/3dmodel.model"}
        model = archive.read("3D/3dmodel.model").decode()
    assert 'unit="millimeter"' in model
    assert model.count("<vertex ") == out["vertices"]
    assert model.count("<triangle ") == out["triangles"]

    import xml.etree.ElementTree as ET
    ET.fromstring(model)                                 # it has to parse


def test_a_wall_is_measured_not_guessed():
    """A 10 mm plate measures 10 mm, and its sides do not."""
    box = kernel.box("b", [40, 30, 10], (0, 0, 0), centred=False)
    thin = check(box, min_wall=12.0, min_hole=1.0, deflection=0.5)["thin_walls"]
    assert {t["face"] for t in thin} == {"b/+z", "b/-z"}
    assert all(t["thinnest_mm"] == pytest.approx(10.0, abs=0.05) for t in thin)
    assert check(box, min_wall=5.0, min_hole=1.0, deflection=0.5)["thin_walls"] == []


def test_which_face_is_on_the_bed_follows_the_build_direction():
    box = kernel.box("b", [40, 30, 10], (0, 0, 0), centred=False)
    out = check(box, min_wall=1.0, min_hole=1.0, deflection=0.5)
    assert out["on_the_bed_mm2"] == pytest.approx(40 * 30, abs=1.0)
    assert out["overhangs"] == []                  # a flat bottom is the first layer
    # stand it on its side and a different face meets the plate
    sideways = check(box, up=(1, 0, 0), min_wall=1.0, min_hole=1.0, deflection=0.5)
    assert sideways["on_the_bed_mm2"] == pytest.approx(30 * 10, abs=1.0)
    assert sideways["overhangs"] == []


def test_findings_carry_the_face_name(drilled):
    """Guards: a full-cylinder bore is found as a hole (its centroid lies on
    the axis, so the face orientation decides, not the centroid)."""
    out = check(drilled, min_wall=2.0, min_hole=3.0)
    assert {h["face"] for h in out["small_holes"]} == {"tapped/bore", "tapped/bore~1"}
    # the floor under an M3 tapped hole in an 8 mm plate is 2 mm. The face is
    # `tapped/floor` whichever way the hole was drilled; no world axis in the name
    floors = {w["face"]: w["thinnest_mm"] for w in out["thin_walls"]}
    assert floors["tapped/floor"] == pytest.approx(2.0, abs=0.05)
    assert floors["tapped/floor~1"] == pytest.approx(2.0, abs=0.05)
    assert out["ok"] is False


def test_a_pin_is_not_a_hole():
    """Only a face that curves toward its own axis is a hole."""
    pin = kernel.cylinder("pin", 0.6, 20)
    assert check(pin, min_wall=0.1, min_hole=5.0, deflection=0.3)["small_holes"] == []


def test_the_format_comes_from_the_file_name(tmp_path):
    session = Session()
    session.op_open("examples/sketch_plate.json")
    session.op_build()
    assert session.op_export_mesh(str(tmp_path / "a.stl"))["triangles"] > 0
    assert session.op_export_mesh(str(tmp_path / "a.3mf"))["triangles"] > 0
    assert session.op_export_mesh(str(tmp_path / "a.obj"))["triangles"] > 0
    with pytest.raises(CadError) as exc:
        session.op_export_mesh(str(tmp_path / "a.ply"))
    assert exc.value.kind == "unknown_format"


def test_the_command_line_writes_the_format_the_name_asks_for(tmp_path):
    """Guards: `export --out part.stl` writes STL, not STEP under an .stl name."""
    from cadcore.service.cli import main

    stl = tmp_path / "part.stl"
    assert main(["export", "examples/sketch_plate.json", "--out", str(stl)]) == 0
    with stl.open("rb") as handle:
        assert not handle.read(13).startswith(b"ISO-10303-21")

    step = tmp_path / "part.step"
    assert main(["export", "examples/sketch_plate.json", "--out", str(step)]) == 0
    assert step.read_bytes().startswith(b"ISO-10303-21")

    assert main(["export", "examples/sketch_plate.json",
                 "--out", str(tmp_path / "part.ply")]) == 2
    assert not (tmp_path / "part.ply").exists()


def test_the_bed_is_not_an_overhang():
    """Guards: a flat bottom on the plate is the first layer, not an overhang;
    lifted off the plate it is one again."""
    from cadcore.geometry import kernel

    box = kernel.box("b", [40, 30, 10], (0, 0, 0), centred=False)
    out = check(box, min_wall=1.0, min_hole=1.0, deflection=0.5)
    assert out["on_the_bed_mm2"] == pytest.approx(40 * 30, abs=1.0)
    assert out["overhangs"] == []                      # nothing else faces down

    # tilt the same box off the bed and it is an overhang again
    lifted = kernel.translate("t", box, [0, 0, 5])
    tilted = check(lifted, up=(0.0, 0.2, 0.98), min_wall=1.0, min_hole=1.0,
                   deflection=0.5)
    assert tilted["overhangs"], "a face off the plate should still be reported"


def test_the_cup_is_printable_and_holds_what_it_should():
    from cadcore import Document
    from cadcore.geometry import kernel
    from cadcore.evaluation.graph import Evaluator

    evaluator = Evaluator(Document.load("examples/cup.json"))
    cup = evaluator.build()
    capacity = (kernel.volume(evaluator.body_of("blank"))
                - kernel.volume(evaluator.body_of("body")))
    assert capacity / 1000 == pytest.approx(353, abs=5)          # millilitres
    assert kernel.volume(cup) * 1.24e-3 == pytest.approx(93, abs=3)   # grams of PLA

    out = check(cup, min_wall=1.5, min_hole=2.0)
    assert out["on_the_bed_mm2"] > 3000                          # it stands on its base
    assert out["unsupported_fraction"] < 0.01                    # only under the handle
    assert all(o["face"].startswith("handle/") for o in out["overhangs"])
