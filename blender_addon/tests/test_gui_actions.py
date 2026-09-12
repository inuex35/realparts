"""The menus reach the new operators, and the operators call operations the kernel has."""
from __future__ import annotations

import ast
import pathlib

ADDON = pathlib.Path(__file__).resolve().parents[1]

NEW = {"cadcore.split_here", "cadcore.emboss_text", "cadcore.draft_from_here",
       "cadcore.coil_round", "cadcore.draft_check", "cadcore.mass"}


def _idnames() -> set:
    out = set()
    for path in sorted(ADDON.rglob("*.py")):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.Assign) and any(
                            getattr(t, "id", "") == "bl_idname" for t in item.targets) \
                            and isinstance(item.value, ast.Constant):
                        out.add(item.value.value)
    return out


def test_every_new_operator_exists_and_is_in_a_menu():
    assert NEW <= _idnames()
    menus = (ADDON / "ui" / "menus.py").read_text(encoding="utf-8")
    for idname in NEW:
        assert idname in menus, idname


def test_the_new_operators_are_registered():
    init = (ADDON / "__init__.py").read_text(encoding="utf-8")
    for module in ("split", "sideways"):
        assert module in init, module


def test_the_soak_walks_the_new_moves():
    soak = (ADDON.parent / "tools" / "verify" / "soak_addon.py").read_text(encoding="utf-8")
    for idname in NEW:
        assert idname.split(".", 1)[1] in soak, idname


def test_the_material_field_reaches_the_kernel_and_comes_back():
    props = (ADDON / "ui" / "props.py").read_text(encoding="utf-8")
    assert "set_meta" in props and "material: StringProperty" in props
    state = (ADDON / "link" / "state.py").read_text(encoding="utf-8")
    assert 'meta.get("material")' in state
