"""An assembly's sheet: a balloon per part, a parts list, and drawn apart when asked."""
from cadcore.service.server import Session


def built(document: str) -> Session:
    session = Session(autosave=False)
    session.op_open(document)
    session.op_build()
    return session


def test_an_assembly_sheet_carries_balloons_and_a_parts_list():
    session = built("examples/assembly_nested.json")
    out = session.op_drawing(spec={"views": ["front", "top"], "scale": 0.5})
    assert out["parts"] == ["left:base", "left:bush", "rail", "right:base", "right:bush"]
    svg = out["svg"]
    assert svg.count("<circle") == 5                     # one balloon per part
    for word in ("PART", "QTY", "MATERIAL", "left:bush", "SUS304"):
        assert word in svg


def test_the_sheet_can_show_the_assembly_apart():
    session = built("examples/assembly.json")
    together = session.op_drawing(spec={"views": ["top"]})["svg"]
    apart = session.op_drawing(spec={"views": ["top"], "exploded": 1.5})["svg"]
    assert together != apart
    assert apart.count("<circle") == 2                   # the bracket and the bush


def test_one_part_has_neither():
    session = built("examples/bracket.json")
    out = session.op_drawing(spec={"views": ["front"]})
    assert "parts" not in out and "<circle" not in out["svg"]
