"""Structural rules (layering, cycles, naming, encoding of boundaries), read
off the import graph and syntax trees so a violation fails on the line that
introduced it."""
import ast
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGES = ("cadcore", "blender_addon", "native_app", "tools")


def modules(package: str) -> dict:
    """``{module name: parsed source}`` for one package, sub-packages included.

    A module inside a sub-package is named ``features.solids``, as import
    statements name it.
    """
    out = {}
    for path in sorted((ROOT / package).rglob("*.py")):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue                          # a layer's tests are beside it, not in it
        relative = path.relative_to(ROOT / package)
        name = ".".join(relative.parts[:-1] + (relative.stem,))
        out[name.removesuffix(".__init__") if name.endswith("__init__")
            else name] = ast.parse(path.read_text(encoding="utf-8"))
    return out


def imports_of(tree: ast.AST, package: str, name: str = "",
               packages: set | None = None) -> set:
    """Which modules of its own package this one imports.

    Relative imports are resolved against the importing module's location, so
    ``from .extents import ...`` inside ``features/solids.py`` gives
    ``features.extents``; otherwise a cycle inside a sub-package goes unnoticed.
    """
    # inside a package's __init__, "." is the package itself; inside a module,
    # it is the package the module sits in
    here = name if name in (packages or set()) else (
        name.rsplit(".", 1)[0] if "." in name else "")

    def resolve(level: int, module: str) -> str:
        # one dot is this package; each further dot goes up one. It used to
        # take any `..` for the root, so `from ..declare.registry` inside
        # features/families resolved to declare.registry, a module that does
        # not exist, and the edge was lost
        base = here
        for _ in range(level - 1):
            base = base.rsplit(".", 1)[0] if "." in base else ""
        return f"{base}.{module}".lstrip(".") if module else base

    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                target = resolve(node.level, node.module or "")
                if node.module:
                    found.add(target)
                # `from .families import booleans` names the module
                # families.booleans as much as the package families; both go
                # in, and a name that is not a module is dropped by the caller
                for alias in node.names:
                    found.add(f"{target}.{alias.name}".lstrip("."))
            elif (node.module or "").split(".")[0] == package:
                parts = node.module.split(".")[1:]
                if parts:
                    found.add(".".join(parts))
                    found.add(parts[0])
                else:                             # from cadcore import ops
                    for alias in node.names:
                        found.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == package and len(parts) > 1:
                    found.add(".".join(parts[1:]))
                    found.add(parts[1])
        elif isinstance(node, ast.Call):
            # __import__("cadcore.x") and importlib.import_module("cadcore.x")
            callee = node.func
            named = (callee.id if isinstance(callee, ast.Name) else
                     callee.attr if isinstance(callee, ast.Attribute) else "")
            if named in ("__import__", "import_module") and node.args \
                    and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                parts = node.args[0].value.split(".")
                if parts[0] == package and len(parts) > 1:
                    found.add(".".join(parts[1:]))
                    found.add(parts[1])
    return found - {""}


#: The layers of cadcore/, lowest first. A module may import its own layer and
#: any layer below it at module level. `simulation` and `mechanism` sit above
#: evaluation because a study builds the body it meshes.
LAYERS = ("model", "sketching", "geometry", "features", "evaluation",
          "simulation", "mechanism", "analysis", "ops", "service")

#: The modules that sit beside the layers rather than in one: every layer may
#: import them, and they import nothing above `model`.
FOUNDATIONS = {"errors", "names", "progress", "__main__"}


def layer_of(name: str) -> str | None:
    head = name.split(".")[0]
    return head if head in LAYERS else None


def module_level_imports(tree: ast.AST, package: str, name: str, packages: set) -> set:
    """Imports at the top of the module -- the ones that run on `import`.

    A `try:` at module level runs on import too, so what it imports counts;
    it did not, and an optional import that pulled a whole layer in went
    unseen."""
    def top_level(body):
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                yield node
            elif isinstance(node, ast.Try):
                yield from top_level(node.body)
                for handler in node.handlers:
                    yield from top_level(handler.body)
                yield from top_level(node.orelse)
                yield from top_level(node.finalbody)
            elif isinstance(node, (ast.If, ast.With)):
                yield from top_level(node.body)
                yield from top_level(getattr(node, "orelse", []))

    top = ast.Module(body=list(top_level(tree.body)), type_ignores=[])
    return imports_of(top, package, name, packages)


def test_every_module_of_the_core_sits_in_a_layer():
    """A file dropped in cadcore/ at the top level is a layer nobody chose."""
    parsed = modules("cadcore")
    stray = sorted(n for n in parsed if "." not in n and n not in FOUNDATIONS
                   and n not in LAYERS and n != "__init__")
    assert stray == [], f"these modules are in no layer: {stray}"


def test_the_layers_only_import_downwards():
    """Module-level imports point down the layers only (docs/architecture.md
    draws the same order).

    Imports inside functions may reach up: that is how `simulation` stays
    optional and how `model.document` asks `features` what a feature takes
    without loading OpenCASCADE.
    """
    parsed = modules("cadcore")
    names = set(parsed)
    packages = {n for n in names if any(o.startswith(n + ".") for o in names)}
    rank = {layer: i for i, layer in enumerate(LAYERS)}
    offenders = []
    for name, tree in parsed.items():
        mine = layer_of(name)
        if mine is None:
            continue
        for target in module_level_imports(tree, "cadcore", name, packages):
            theirs = layer_of(target)
            if theirs is None:
                continue                      # a foundation, or the package root
            if rank[theirs] > rank[mine]:
                offenders.append(f"cadcore/{name.replace('.', '/')}.py imports {target} "
                                 f"({mine} -> {theirs})")
    assert offenders == [], "imports pointing up the layers:\n  " + "\n  ".join(offenders)


def test_the_foundations_stand_on_nothing_above_them():
    parsed = modules("cadcore")
    names = set(parsed)
    packages = {n for n in names if any(o.startswith(n + ".") for o in names)}
    for name in sorted(FOUNDATIONS - {"__main__"}):
        reached = {t.split(".")[0] for t in imports_of(parsed[name], "cadcore", name, packages)}
        assert reached <= FOUNDATIONS, f"{name}.py imports {sorted(reached - FOUNDATIONS)}"


def test_the_kernel_is_a_facade_and_nothing_else():
    """geometry/kernel.py gathers names. Anything implemented there would hide a layer."""
    tree = modules("cadcore")["geometry.kernel"]
    defined = [n.name for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    assert defined == [], f"kernel.py should only re-export, but defines {defined}"


def test_nothing_inside_the_kernel_imports_the_facade():
    """The façade imports the operations; an operation importing it is a cycle."""
    parsed = modules("cadcore")
    for name in sorted(n for n in parsed if n.startswith("geometry.") and n != "geometry.kernel"):
        assert "geometry.kernel" not in imports_of(parsed[name], "cadcore", name, {"geometry"}), \
            f"{name}.py imports the kernel façade it is part of"


def test_reading_a_document_really_does_not_load_the_geometry_kernel():
    """`import cadcore.model.document` must not load OCP, checked by running it
    rather than reading imports (`cadcore/__init__` loads geometry lazily).

    In a subprocess, because the suite has already imported OCP by the time
    this runs.
    """
    import subprocess
    import sys
    proof = ("import sys; import cadcore.model.document; "
             "print('OCP' in sys.modules)")
    answer = subprocess.run([sys.executable, "-c", proof], cwd=str(ROOT),
                            capture_output=True, text=True, timeout=300, encoding="utf-8")
    assert answer.stdout.strip() == "False", \
        "reading a document loaded OpenCASCADE:\n" + answer.stdout + answer.stderr


def test_a_document_is_data_and_does_not_need_a_geometry_kernel():
    """Reading a document must not load OCCT: documents are exchanged as files.

    `model` and `sketching` sit below `geometry` in LAYERS, so the layer test
    already says this of their module-level imports; this says it of every
    import they make, inside functions included.
    """
    parsed = modules("cadcore")
    names = set(parsed)
    packages = {n for n in names if any(o.startswith(n + ".") for o in names)}
    for name in sorted(n for n in parsed if layer_of(n) in ("model", "sketching")):
        reached = {layer_of(t) for t in imports_of(parsed[name], "cadcore", name, packages)}
        assert not reached & {"geometry", "evaluation"}, \
            f"{name}.py reaches into geometry"


def test_no_import_cycles_anywhere():
    """No import cycles in any package: a cycle forces deferred imports inside
    functions, which hide coupling."""
    for package in PACKAGES:
        parsed = modules(package)
        names = set(parsed)
        packages = {n for n in names if any(o.startswith(n + ".") for o in names)}
        edges = {name: (imports_of(tree, package, name, packages) & names) - {name}
                 for name, tree in parsed.items()}
        colour, cycles = {}, []

        def visit(node, path):
            if colour.get(node) == "done":
                return
            if colour.get(node) == "open":
                cycles.append(" -> ".join(path[path.index(node):] + [node]))
                return
            colour[node] = "open"
            for nxt in sorted(edges.get(node, ())):
                visit(nxt, path + [node])
            colour[node] = "done"

        for name in sorted(edges):
            visit(name, [])
        assert not cycles, "import cycles in %s:\n  " % package + "\n  ".join(sorted(set(cycles)))


def test_the_add_on_never_imports_the_kernel():
    """The add-on talks to the kernel over a pipe, and that is the licence
    boundary as well as the crash boundary."""
    for name, tree in modules("blender_addon").items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("cadcore"):
                pytest.fail(f"blender_addon/{name}.py imports cadcore directly")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("cadcore"), \
                        f"blender_addon/{name}.py imports cadcore directly"


def test_no_module_reaches_for_a_global_that_is_not_there():
    """Every ``LOAD_GLOBAL`` in a module's compiled code names something the
    module defines or imports.

    The add-on cannot be imported without Blender, so the check reads compiled
    code. Only global loads are looked at, so postponed annotations such as
    `name: StringProperty(name="Kind")` do not trip it.
    """
    import builtins
    import dis

    def defined(tree) -> set:
        """Every name the module binds at its top level."""
        out = set()
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                out.update(a.asname or a.name.split(".")[0] for a in node.names)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(node.name)
                if isinstance(node, ast.ClassDef):
                    # a class body loads its own earlier names (`column = row`)
                    # with LOAD_NAME, the same opcode as a module global
                    for inner in node.body:
                        if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            out.add(inner.name)
                        elif isinstance(inner, (ast.Assign, ast.AnnAssign)):
                            targets = inner.targets if isinstance(inner, ast.Assign) else [inner.target]
                            out.update(n.id for target in targets for n in ast.walk(target)
                                       if isinstance(n, ast.Name))
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    out.update(n.id for n in ast.walk(target)
                               if isinstance(n, ast.Name))
            elif isinstance(node, (ast.Try, ast.If, ast.For, ast.While, ast.With)):
                for inner in ast.walk(node):        # conditional imports count
                    if isinstance(inner, (ast.Import, ast.ImportFrom)):
                        out.update(a.asname or a.name.split(".")[0]
                                   for a in inner.names)
                    elif isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Store):
                        out.add(inner.id)
                    elif isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        out.add(inner.name)         # a def inside a module-level `with`
                    elif isinstance(inner, ast.ExceptHandler) and inner.name:
                        out.add(inner.name)         # `except E as exc` at module level
        return out

    def loads(code) -> set:
        out = {i.argval for i in dis.get_instructions(code)
               if i.opname in ("LOAD_GLOBAL", "LOAD_NAME")}
        for const in code.co_consts:
            if hasattr(const, "co_names"):
                out |= loads(const)
        return out

    missing = []
    for package in ("blender_addon", "tools", "cadcore"):
        root = ROOT / package
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "tests" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            # a module's own dunders are put there by the interpreter, and
            # `from __future__ import annotations` makes every annotated
            # assignment touch __annotations__
            known = defined(tree) | set(dir(builtins)) | {
                "__name__", "__file__", "__doc__", "__spec__", "__package__",
                "__builtins__", "__annotations__", "__debug__", "__loader__"}
            for name in sorted(loads(compile(source, str(path), "exec")) - known):
                missing.append(f"{path.relative_to(ROOT)}: {name}")
    assert missing == [], ("these names are used but never defined or imported:\n  "
                           + "\n  ".join(missing))


def test_nothing_in_the_kernel_imports_bpy():
    """The kernel runs without Blender."""
    for package in ("cadcore",):
        for name, tree in modules(package).items():
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] != "bpy", f"{package}/{name}.py"
                if isinstance(node, ast.ImportFrom):
                    assert (node.module or "").split(".")[0] != "bpy", f"{package}/{name}.py"


def test_the_kernel_imports_without_blender_or_a_display():
    """It has to run headless in another interpreter, so it is checked here."""
    assert "bpy" not in sys.modules
    import cadcore.ops.session  # noqa: F401


def test_the_studies_can_be_left_out():
    """`simulation` is imported only inside functions, so it can be left out.

    NGSolve, Netgen and SciPy are a separate requirements file
    (requirements-sim.txt); one module-level import would make it mandatory.
    """
    offenders = []
    parsed = modules("cadcore")
    names = set(parsed)
    packages = {n for n in names if any(o.startswith(n + ".") for o in names)}
    for name, tree in parsed.items():
        if layer_of(name) == "simulation":
            continue
        reached = module_level_imports(tree, "cadcore", name, packages)
        if any(layer_of(t) == "simulation" for t in reached):
            offenders.append(f"cadcore/{name.replace('.', '/')}.py")
    assert offenders == [], (
        "these import simulation at module level, which makes "
        "requirements-sim.txt mandatory:\n" + "\n".join(offenders))


def test_every_requirement_is_pinned_in_one_place():
    """A dependency named in both files could drift to two versions."""
    core = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    extra = (ROOT / "requirements-sim.txt").read_text(encoding="utf-8").splitlines()

    def named(lines):
        return {line.split("==")[0].split(">=")[0].strip()
                for line in lines
                if line.strip() and not line.startswith(("#", "-"))}

    assert "-r requirements.txt" in extra, \
        "requirements-sim.txt has to include the kernel it builds on"
    both = named(core) & named(extra)
    assert both == set(), f"pinned in both requirements files: {sorted(both)}"


def test_a_name_survives_being_taken_apart_and_put_back():
    """`names.spell(names.parse(n)) == n` for every kind of name the system
    produces, including a ``#`` that belongs to the face rather than the edge."""
    from cadcore import names

    spelled = [
        names.face("plate", "+z"),
        names.face("fillet1", "side", duplicate=2),
        names.piece(names.face("plate", "+x"), 0),
        names.instance(names.face("bolt", "side"), 3),
        names.instance(names.face("bolt", "side"), "m"),
        names.scoped("base", names.face("plate", "+z")),
        names.piece(names.instance(names.face("plate", "+x"), 2), 1),
        names.face("imported", "face12"),
    ]
    for name in spelled:
        assert names.spell(names.parse(name)) == name, name

    assert names.base(names.piece(names.face("plate", "+x"), 0)) == "plate/+x"
    assert names.feature_of("base:plate/+z") == "plate"
    assert names.turned_round("hole/+y") == "hole/-y"
    assert names.turned_round("hole/side") == "hole/side"


def test_only_one_module_spells_a_name():
    """Face and edge names are spelled only in cadcore/names.py.

    Read off the syntax tree rather than with one regular expression: an
    f-string with an attribute in the braces, a `%` format, a `.format`, and
    a `+ "/"` all spell a name, and the regular expression saw only
    `f"{name}/"`. A separator followed by a digit is arithmetic (`{w}/2`),
    and `:` is left out because ids and URIs use it too.
    """
    separators = "/@~|#"
    offenders = []

    def spells(text: str) -> bool:
        return bool(text) and text[0] in separators and not text[1:2].isdigit() \
            and " " not in text and "://" not in text          # a URI is not a name

    for package in ("cadcore",):
        for path in sorted((ROOT / package).rglob("*.py")):
            if path.name == "names.py" or "sketching" in path.parts or "tests" in path.parts:
                continue                      # a sketch names its own 2D geometry
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                where = f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', '?')}"
                if isinstance(node, ast.JoinedStr):
                    parts = node.values
                    for i in range(1, len(parts)):
                        if isinstance(parts[i], ast.Constant) and isinstance(parts[i].value, str) \
                                and isinstance(parts[i - 1], ast.FormattedValue) \
                                and spells(parts[i].value):
                            offenders.append(where + ": f-string")
                            break
                elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
                    for side in (node.left, node.right):
                        if isinstance(side, ast.Constant) and isinstance(side.value, str):
                            text = side.value
                            if isinstance(node.op, ast.Mod):
                                if "://" in text:
                                    continue
                                text = text.split("%s", 1)[1] if "%s" in text else ""
                            if spells(text):
                                offenders.append(where + ": " + ("% format" if isinstance(node.op, ast.Mod) else "concatenation"))
                                break
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr == "format" and isinstance(node.func.value, ast.Constant) \
                        and isinstance(node.func.value.value, str):
                    text = node.func.value.value
                    if "}" in text and spells(text.split("}", 1)[1]):
                        offenders.append(where + ": .format")
    assert offenders == [], ("face and edge names are spelled in cadcore/names.py:\n"
                             + "\n".join(offenders))

def test_no_test_needs_one_particular_machine_s_virtualenv():
    """No test spells out a virtualenv path; `sys.executable` can import
    cadcore by definition and is portable. CI does not build a virtualenv.

    Read from the syntax tree, not the text, so a comment or docstring may
    mention the word and this file can describe itself.
    """
    marker = "." + "venv"                  # not a literal, or this test is one
    guilty = []
    for path in sorted(p for p in ROOT.rglob("tests/*.py") if marker not in p.parts):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and node.body:
                first = node.body[0]
                if (isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    docstrings.add(id(first.value))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and marker in node.value and id(node) not in docstrings):
                guilty.append("%s:%d" % (path.relative_to(ROOT), node.lineno))
    assert not guilty, ("a test that only runs where someone built a virtualenv: "
                        + ", ".join(guilty) + " -- use sys.executable")


def test_a_mutating_op_rebuilds_inside_its_own_guard():
    """Every `_rebuild()` in a mutating op sits inside its `_edit()` guard.

    `_edit()` promises that a refusal leaves nothing behind; a rebuild outside
    it that refuses leaves the edit and an undo step in place while the caller
    is told the edit did not happen.
    """
    guilty = []
    for path in sorted(p for p in ROOT.rglob("cadcore/**/*.py") if "tests" not in p.parts):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            def edits(node):
                return isinstance(node, ast.With) and any(
                    isinstance(i.context_expr, ast.Call)
                    and getattr(i.context_expr.func, "attr", "") == "_edit"
                    for i in node.items)

            guards = [n for n in ast.walk(fn) if edits(n)]
            if not guards:
                continue                  # not a mutating op: nothing to guard
            inside = {id(n) for g in guards for n in ast.walk(g)}
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call)
                        and getattr(node.func, "attr", "") == "_rebuild"
                        and id(node) not in inside):
                    guilty.append("%s:%d in %s"
                                  % (path.relative_to(ROOT), node.lineno, fn.name))
    assert not guilty, ("a rebuild outside the edit guard, so a refusal would "
                        "leave the edit behind: " + ", ".join(guilty))


#: Every free-form (`Anything()`) argument in the registry and what the unit
#: converter does with it. The converter walks the schema and cannot see inside
#: these, so each one needs a recorded decision or an inch document's numbers
#: would stay at millimetre values.
FREE_FORM = {
    # answered by the sketch itself, through the `lengths`/`angles` hooks
    "sketch.plane": "hook",
    "sketch.points": "hook",
    "sketch.arcs": "hook",
    "sketch.circles": "hook",
    "sketch.slots": "hook",
    "sketch.offsets": "hook",
    "sketch.ellipses": "hook",
    "sketch.constraints": "hook",
    # names and flags: nothing in them is a number
    "sketch.lines": "no numbers",
    "sketch.splines": "no numbers",
    "sketch.trims": "no numbers",
    "sketch.dimensions": "no numbers",
    # the sub-document says whether its own parameters are lengths, so a plain
    # number here is refused when the unit is not millimetres
    "part.parameters": "refused",
}


def _free_form(spec, path: str, out: set) -> None:
    """Every `Anything` in a declaration, nested ones included
    (`assemble.mates[].flip` is not a top-level argument)."""
    if spec.kind == "any":
        out.add(path)
        return
    for name, field in (spec.fields or {}).items():
        _free_form(field, "%s.%s" % (path, name), out)
    for option in (spec.options or ()):
        _free_form(option, path + ("[]" if spec.kind in ("list_of", "map_of") else ""),
                   out)


def test_every_free_form_argument_has_been_thought_about_for_units():
    from cadcore import features as registry

    registry.load()
    seen: set = set()
    for name in registry.types():
        for arg, spec in registry.handler(name).args.items():
            _free_form(spec, "%s.%s" % (name, arg), seen)
    seen -= {k for k in seen if k.endswith(".note")}
    unaccounted = sorted(seen - set(FREE_FORM))
    assert not unaccounted, (
        "a free-form argument the unit converter cannot see, and no decision "
        "recorded about it: " + ", ".join(unaccounted))
    stale = sorted(set(FREE_FORM) - seen)
    assert not stale, "recorded here but no longer free-form: " + ", ".join(stale)


def test_the_sketch_answers_for_its_own_lengths_and_angles():
    """The sketch's `lengths`/`angles` hooks are wired up; they are what
    converts an inch document's sketch numbers."""
    from cadcore import features as registry

    registry.load()
    sketch = registry.handler("sketch")
    assert sketch.own_lengths is not None and sketch.own_angles is not None
    args = {"points": {"a": [1, 2]},
            "circles": {"c": {"centre": "a", "radius": 3}},
            "constraints": [{"type": "distance", "points": ["a", "a"], "value": 4},
                            {"type": "angle", "lines": ["x", "y"], "value": 45}]}
    assert ["points", "a", 0] in sketch.lengths(args)
    assert ["circles", "c", "radius"] in sketch.lengths(args)
    assert ["constraints", 0, "value"] in sketch.lengths(args)
    assert ["constraints", 1, "value"] in sketch.angles(args)
    assert ["constraints", 1, "value"] not in sketch.lengths(args)


def test_every_sketch_constraint_says_what_its_numbers_count():
    """A constraint that is not classified is one the converter guesses at."""
    from cadcore.sketching import constraints

    missing = [k for k in constraints.kinds() if k not in constraints.MEASURES]
    assert not missing, ("a constraint whose numbers nothing classifies: "
                         + ", ".join(missing))
    allowed = {"none", "place", "length", "angle"}
    odd = {k: v for k, v in constraints.MEASURES.items() if v not in allowed}
    assert not odd, odd


#: layers that reach OpenCASCADE directly, for now, and why: drawing does
#: hidden-line removal and printability ray-casts, and the mesher reads the
#: shape; each is a move into geometry/io that has not been made
OCP_STILL_IN = {"analysis", "simulation"}


def test_opencascade_is_reached_only_through_geometry():
    """OCP is imported under cadcore/geometry, or in the layers listed above.

    model/requirements.py measured a bounding box with OCP, features/extents
    walked a wire with it, and the layer test saw neither: it looked for
    imports of `geometry`, and OCP is not `geometry`.
    """
    stray = []
    for path in sorted((ROOT / "cadcore").rglob("*.py")):
        rel = path.relative_to(ROOT / "cadcore").parts
        if "tests" in rel or rel[0] in ("geometry",) or rel[0] in OCP_STILL_IN:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            named = []
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                named = [node.module]
            elif isinstance(node, ast.Import):
                named = [alias.name for alias in node.names]
            if any(n.split(".")[0] == "OCP" for n in named):
                stray.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert stray == [], "OpenCASCADE reached outside geometry:\n" + "\n".join(stray)
