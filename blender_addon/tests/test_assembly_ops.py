"""The add-on's mate kinds are the kernel's, read off the source without bpy."""
import ast
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
ADDON = REPO / "blender_addon"


def _mate_kinds() -> list:
    tree = ast.parse((ADDON / "operators" / "assembly.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "MATE_KINDS" for t in node.targets):
            return [item.elts[0].value for item in node.value.elts]
    raise AssertionError("MATE_KINDS not found")


def test_the_mate_menu_offers_every_kind_the_kernel_has():
    sys.path.insert(0, str(REPO))
    from cadcore.geometry.assembly.assembly import MATES

    assert _mate_kinds() == list(MATES)


def test_the_assemble_menu_reaches_the_new_operators():
    text = (ADDON / "ui" / "menus.py").read_text(encoding="utf-8")
    for op in ("cadcore.mate_faces", "cadcore.drive", "cadcore.explode"):
        assert op in text, op
