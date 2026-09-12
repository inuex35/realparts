"""What `pyproject.toml` claims, held to what the repository is.

A packaging file fails in one direction only: quietly. A subpackage left off
the list imports perfectly in the checkout every test runs in and is simply
absent from the wheel, and the first person to hear about it is whoever ran
`uvx`. So the three facts it states that could drift are checked here against
the tree itself.
"""
from __future__ import annotations

import os
import re
import tomllib

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

with open(os.path.join(HERE, "pyproject.toml"), "rb") as _file:
    PROJECT = tomllib.load(_file)


def _requirements(name: str) -> list:
    """The real requirements in a pip file: no comments, no `-r` lines."""
    with open(os.path.join(HERE, name), encoding="utf-8") as file:
        return [line.strip() for line in file
                if line.strip() and not line.lstrip().startswith(("#", "-"))]


def test_every_package_in_the_tree_is_in_the_wheel():
    shipped = set(PROJECT["tool"]["setuptools"]["packages"])
    for top in ("cadcore",):
        for root, dirs, files in os.walk(os.path.join(HERE, top)):
            dirs[:] = [d for d in dirs if d != "tests"]         # a layer's tests are not shipped
            if "__init__.py" not in files:
                continue
            name = os.path.relpath(root, HERE).replace(os.sep, ".")
            assert name in shipped, (
                "%s is a package in the tree and not in pyproject.toml, so it "
                "would import here and be missing from the wheel" % name)


def test_the_studies_extra_is_the_studies_file():
    """`[sim]` and requirements-sim.txt are two copies of one fact."""
    declared = set(PROJECT["project"]["optional-dependencies"]["sim"])
    assert declared == set(_requirements("requirements-sim.txt"))


def test_the_base_dependencies_are_not_copied_at_all():
    """They are read from requirements.txt, which is the only place they live."""
    assert "dependencies" in PROJECT["project"]["dynamic"]
    assert PROJECT["tool"]["setuptools"]["dynamic"]["dependencies"] == {
        "file": ["requirements.txt"]}
    assert _requirements("requirements.txt")     # and the file is not empty


def test_the_console_scripts_point_at_functions_that_exist():
    for target in PROJECT["project"]["scripts"].values():
        module, _, function = target.partition(":")
        loaded = __import__(module, fromlist=[function])
        assert callable(getattr(loaded, function)), target


def test_the_shelves_ship_where_the_resources_look_for_them():
    """`cadcore.service.resources` searches two layouts; the wheel builds the second."""
    from cadcore.service import resources

    mapped = PROJECT["tool"]["setuptools"]["package-dir"]
    for _, folder, pattern, _, _ in resources.SHELVES:
        assert mapped.get("cadcore." + folder) == folder
        assert pattern in PROJECT["tool"]["setuptools"]["package-data"][
            "cadcore." + folder]
    # and the installed layout is the one *inside* the package
    assert resources.HOMES[1].name == "cadcore"


def test_the_version_is_the_one_the_library_reports():
    import cadcore

    assert PROJECT["project"]["version"] == cadcore.__version__


def test_the_readme_shows_each_script_being_run():
    """Not merely mentioned: `cadcore` appears in this README a hundred times
    as part of a module path, so a search for the word proves nothing. A
    command is a line that starts with it."""
    with open(os.path.join(HERE, "README.md"), encoding="utf-8") as file:
        lines = [line.strip() for line in file]
    for name in PROJECT["project"]["scripts"]:
        assert any(line == name or line.startswith(name + " ")
                   for line in lines), name


# -- the add-on zip -------------------------------------------------------------

def _bridge():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bridge_package", os.path.join(HERE, "tools", "package.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_addon_zip_carries_every_package_the_kernel_is():
    """The zip's kernel/ folder is the repository's code, not a copy of it
    with a life of its own: every package pyproject ships is in it."""
    bridge = _bridge()
    bundled = set(bridge.BUNDLED_TREES) | set(bridge.BUNDLED_FLAT)
    for name in PROJECT["tool"]["setuptools"]["packages"]:
        top = name.split(".")[0]
        if top in ("cadcore",):
            assert top in bundled, name
    for tree in bundled:
        assert os.path.isdir(os.path.join(HERE, tree)), tree


def test_the_addon_zip_builds_and_needs_no_repository(tmp_path):
    bridge = _bridge()
    import zipfile

    target = bridge.build(str(tmp_path))
    names = set(zipfile.ZipFile(target).namelist())
    module = bridge.MODULE
    assert f"{module}/__init__.py" in names
    assert f"{module}/kernel/cadcore/__init__.py" in names
    assert f"{module}/kernel/requirements.txt" in names
    assert f"{module}/kernel/requirements-sim.txt" in names
    assert f"{module}/kernel/examples/bracket.json" in names
    assert f"{module}/kernel/docs/features.md" in names
    # and nothing that is a creation, a build product or a cache
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
    assert not [n for n in names if "/examples/v8/" in n or "/build/" in n]


# -- the extension manifest -------------------------------------------------------

def _manifest():
    import tomllib

    with open(os.path.join(HERE, "blender_addon", "blender_manifest.toml"), "rb") as file:
        return tomllib.load(file)


def _bl_info():
    """`bl_info` read off the add-on's __init__ without importing bpy."""
    import ast

    tree = ast.parse(open(os.path.join(HERE, "blender_addon", "__init__.py"),
                          encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "bl_info" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("no bl_info")


def test_the_manifest_and_bl_info_tell_the_same_story():
    """Two doors, one add-on: the legacy install reads bl_info, the extension
    install reads the manifest, and a buyer must not be told two versions or
    two minimum Blenders depending on which they used."""
    manifest, info = _manifest(), _bl_info()
    assert manifest["id"] == _bridge().MODULE
    assert tuple(int(x) for x in manifest["version"].split(".")) == tuple(info["version"])
    assert tuple(int(x) for x in manifest["blender_version_min"].split(".")) == tuple(info["blender"])
    assert manifest["type"] == "add-on"


def test_the_manifest_fits_the_validators_limits():
    """Found by `blender --command extension validate`, twice: 65 characters
    where 64 are allowed. The validator is the authority; this keeps its two
    rulings from being rediscovered."""
    manifest = _manifest()
    assert len(manifest["tagline"]) <= 64
    for key, text in manifest.get("permissions", {}).items():
        assert len(text) <= 64, key
    assert manifest["license"] and all(l.startswith("SPDX:") for l in manifest["license"])


def test_the_zip_carries_the_manifest_at_the_extension_root(tmp_path):
    import zipfile

    bridge = _bridge()
    names = set(zipfile.ZipFile(bridge.build(str(tmp_path))).namelist())
    assert f"{bridge.MODULE}/blender_manifest.toml" in names


def test_every_document_a_bundled_example_refers_to_is_bundled_too():
    """assembly.json says `parts/bush.json`; a zip and a wheel without it shipped
    an example that would not open. Checked from the examples themselves, so a
    new sub-part cannot be forgotten by hand."""
    bridge = _bridge()
    bundled = set(bridge.bundled_paths())
    referenced = bridge.documents_referenced(bundled)
    assert referenced, "no example refers to another; the assembly did"
    missing = sorted(referenced - bundled)
    assert not missing, missing
    # and the wheel carries the same sub-folders the zip does
    wheel_dirs = {v for k, v in PROJECT["tool"]["setuptools"]["package-dir"].items()}
    for rel in referenced:
        assert os.path.dirname(rel) in wheel_dirs, rel


def test_a_platform_zip_carries_that_platform_and_no_other():
    """The wheels are compiled code: a zip that says linux-x64 must not have a
    win_amd64 wheel in it, and Blender has no way to notice if it does."""
    import tools.package as package

    for platform, tags in package.PLATFORMS.items():
        wheels = ["cadquery_ocp_novtk-7.9.3.1.1-cp313-cp313-%s.whl" % tags[-1],
                  "cadquery_ocp_proxy-7.9.3.1.1-py3-none-any.whl"]
        text = package.manifest_with_wheels(platform, wheels)
        assert 'platforms = ["%s"]' % platform in text, text[:200]
        for wheel in wheels:
            assert '"./wheels/%s"' % wheel in text, wheel
        # the plain zip says neither, and Blender then offers it everywhere
        assert "platforms" not in _manifest(), "the shipped manifest names a platform"


def test_the_macos_zip_is_not_built_on_its_own():
    """planegcs publishes no macOS wheel. The zip can still be built, but only
    with one handed to it, so `--all-platforms` leaves macOS out."""
    import tools.package as package

    assert [p for p in package.PLATFORMS if p.startswith("macos")]
    assert not [p for p in package.all_platforms() if p.startswith("macos")], \
        "--all-platforms is building a macOS zip; it has no wheel to put in it"


def test_a_wheel_built_elsewhere_is_used_instead_of_pypi(tmp_path):
    """What `.github/workflows/wheels.yml` hands over: a wheel in `--extra-wheels`
    is picked up by its platform tag, and wheels for other platforms are left."""
    import tools.package as package

    for name in ("planegcs-0.8.0-cp313-cp313-macosx_11_0_arm64.whl",
                 "planegcs-0.8.0-cp313-cp313-macosx_11_0_x86_64.whl",
                 "cadquery_ocp_proxy-7.9.3.1.1-py3-none-any.whl",
                 "notes.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    found = package.supplied_wheels(str(tmp_path), "macos-arm64")
    assert found["planegcs"].endswith("macosx_11_0_arm64.whl")
    assert "cadquery-ocp-proxy" in found          # any platform takes a pure wheel
    assert "notes" not in found
    assert package.supplied_wheels(str(tmp_path), "windows-x64").keys() == {"cadquery-ocp-proxy"}
    assert package.supplied_wheels(None, "macos-arm64") == {}


def test_every_pinned_package_is_asked_for():
    """`fetch_wheels` asks pip for names, not for the file, so the names have
    to be the whole of both requirement files."""
    import tools.package as package

    plain = package.requirements(False)
    assert any(r.startswith("planegcs") for r in plain)
    assert any(r.startswith("cadquery-ocp-novtk") for r in plain)
    assert not [r for r in plain if r.startswith("-r") or r.startswith("#")]
    with_studies = package.requirements(True)
    assert set(plain) <= set(with_studies), "-r requirements.txt was not followed"
    assert len(with_studies) > len(plain)
