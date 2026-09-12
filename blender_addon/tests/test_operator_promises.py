"""What an operator promises the panel, read from its source rather than run.

The add-on cannot be imported without `bpy`, so these read the files with
`ast`. Both checks exist because the thing they catch is silent: an operator
that forgets `wants` is offered on an empty selection and does the wrong
thing without refusing, and an operation name that no longer exists is only
found by clicking it.
"""
from __future__ import annotations

import ast
import pathlib

ADDON = pathlib.Path(__file__).resolve().parents[1]
REPO = ADDON.parent


def _classes():
    """Every class in the add-on, as (file, name, node)."""
    for path in sorted(ADDON.rglob("*.py")):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                yield path, node


def _assigned(node: ast.ClassDef, name: str):
    """The value assigned to `name` at class level, or None."""
    for item in node.body:
        if isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return item.value
    return None


def _uses_picked(node: ast.ClassDef) -> bool:
    """Whether the class reads the selection it was handed."""
    for item in node.body:
        if isinstance(item, ast.FunctionDef) and item.name in ("arguments", "summarise"):
            for inner in ast.walk(item):
                if isinstance(inner, ast.Name) and inner.id == "picked":
                    return True
    return False


def test_an_operator_that_reads_the_selection_says_what_it_needs():
    """Without `wants` the operator is never greyed out and never refuses.

    `cadcore.shell` had none: pressed with nothing picked it opened the body
    at no face at all, which is a smaller solid box, not a shell.
    """
    missing = [
        "%s.%s" % (path.relative_to(REPO), node.name)
        for path, node in _classes()
        if any(getattr(base, "id", "") == "EditFromSelection" for base in node.bases)
        and _uses_picked(node) and _assigned(node, "wants") is None
    ]
    assert not missing, "reads the selection but declares no wants: " + ", ".join(missing)


def test_every_operation_the_addon_names_is_one_the_kernel_has():
    """`operation = "..."` and `call("...")` are strings; a renamed operation
    is otherwise found by a person clicking the button."""
    import sys

    sys.path.insert(0, str(REPO))
    from cadcore.ops.session import Session

    known = {name[3:] for name in dir(Session) if name.startswith("op_")}
    # answered by the viewport rather than the kernel, when attached
    from_the_viewport = {"selection", "select"}

    named = {}
    # `operation` on the class is the kernel operation; the same name assigned
    # inside a function is a local, and one of those is an argument value
    for path, node in _classes():
        value = _assigned(node, "operation")
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            named.setdefault(value.value, "%s.%s" % (path.relative_to(REPO), node.name))
    for path in sorted(ADDON.rglob("*.py")):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "call" and node.args \
                    and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                named.setdefault(node.args[0].value, str(path.relative_to(REPO)))

    stray = {op: where for op, where in named.items()
             if op not in known and op not in from_the_viewport}
    assert not stray, "\n".join("%s named at %s is not an operation" % (op, where)
                               for op, where in sorted(stray.items()))


def test_the_picked_plane_is_read_through_the_check():
    """`PICKED_PLANE` outlives the document: a click clears it, opening another
    one does not. Reading it straight offers a plane that is not there."""
    stray = []
    for path in sorted(ADDON.rglob("*.py")):
        if "tests" in path.parts or path.name in ("pick.py", "marks.py"):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "PICKED_PLANE[" in line:
                stray.append("%s:%d" % (path.relative_to(REPO), n))
    assert not stray, "reads PICKED_PLANE straight instead of picked_plane(): " + ", ".join(stray)


def test_a_drag_leaves_edit_mode_before_it_starts():
    """Every preview frame writes the mesh, which edit mode forbids. The drag
    leaves once in `_begin`; leaving per write toggled the mode all drag."""
    text = (ADDON / "viewport" / "live.py").read_text(encoding="utf-8")
    begin = text[text.index("def _begin"):text.index("def _preview")]
    assert "mode_set(mode='OBJECT')" in begin, "_Live._begin does not leave edit mode"


def _base_name(node) -> str:
    """`_Live` or `modal.ViewportTool` -> the last name in it."""
    return getattr(node, "attr", None) or getattr(node, "id", "")


def test_a_drag_picks_the_face_it_starts_on():
    """A drag started with nothing picked takes the face under the cursor.

    Without this the person has to pick with one tool and then reach for
    another, which is the step `docs/experience.md` says to remove. The
    typed path (`execute`) has no cursor and keeps reading the pick.
    """
    checked = []
    for name in ("press_pull", "box", "hole", "plane", "move_part", "section"):
        path = ADDON / "viewport" / ("%s.py" % name)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # the drag operators only: a file may hold others that act on what is
        # already picked, and those have no cursor to read a face from
        drags = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
                 and any(_base_name(b) in ("_Live", "ViewportTool") for b in n.bases)]
        assert drags, "%s has no drag operator" % name
        for cls in drags:
            invokes = [n for n in cls.body
                       if isinstance(n, ast.FunctionDef) and n.name == "invoke"]
            assert invokes, "%s.%s has no invoke" % (name, cls.name)
            for node in invokes:
                called = {n.func.id for n in ast.walk(node)
                          if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
                # a drag that works on an edge has no face to read; the rule
                # is for the ones that do
                if not called & {"_face_to_act_on", "_one_face"}:
                    continue
                assert "_face_to_act_on" in called, \
                    "%s.invoke does not pick the face under the cursor" % cls.name
                assert "_one_face" not in called, \
                    "%s.invoke still asks only for what is already picked" % cls.name
            checked.append(cls.name)
    assert "CADCORE_OT_press_pull" in checked, "the rule stopped covering any drag"


def test_the_menu_offers_a_flat_face_tool_only_on_a_flat_face():
    """`face_frame` refuses every curved face, not only a cylinder.

    The kernel answers plane, cylinder, cone, torus or free. Testing for
    `!= "cylinder"` offered Push/Pull, Hole, Pocket, Draw on It and Plane Off
    It on a cone, a torus and the free surfaces a lofted part is made of --
    every one of which refuses. intake_cowl, which the gallery shows, is free
    surfaces from end to end.
    """
    text = (ADDON / "ui" / "menus.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    menu = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "draw_selection_actions")
    flat = [n for n in ast.walk(menu)
            if isinstance(n, ast.Assign)
            and any(getattr(t, "id", "") == "flat" for t in n.targets)]
    assert flat, "the menu no longer decides on one `flat` test"
    # and nothing in there asks the question the other way round
    for node in ast.walk(menu):
        if isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.NotEq):
            for value in node.comparators:
                assert getattr(value, "value", None) != "cylinder", \
                    "a flat-face tool is being offered by ruling out one curved shape"


def test_the_right_click_menu_knows_an_assembly_part_is_a_body():
    """An assembly draws one object per part, named `cad_body:housing`.

    Testing the name against "cad_body" left every assembly with Blender's
    plain right-click menu and none of the CAD items.
    """
    text = (ADDON / "ui" / "menus.py").read_text(encoding="utf-8")
    assert '"cad_body"' not in text, "menus.py still tests the single body name"
    assert text.count("sync.is_body(ob)") == 2, "both context menus ask sync.is_body"
