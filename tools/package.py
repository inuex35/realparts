"""Build the installable add-on zip: the UI, and inside it `kernel/` with the kernel's code.

    python3 tools/package.py [out_dir]

The native wheels are not in it (85-160 MB per platform); the add-on's
Install button fetches them. `tools/tests/test_packaging.py` holds the list.
"""
import os
import re
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
MODULE = "cadcore_bridge"

#: what the kernel needs at run time, and a buyer needs to read: the packages
#: (walked), the examples the sidebar opens and the manual the MCP server
#: serves (top level only -- a subdirectory under examples/ is somebody's own
#: work in progress, never the product), the requirements the installer reads,
#: and the licence
BUNDLED_TREES = ("cadcore",)
BUNDLED_FLAT = {"examples": ".json", "docs": ".md",
                # the parts the shipped assemblies are made of: assembly.json
                # says `parts/bush.json`, and a zip without it shipped an
                # example that could not open
                "examples/parts": ".json", "examples/previews": ".png"}
BUNDLED_FILES = ("requirements.txt", "requirements-sim.txt", "README.md",
                 "LICENSE", "pyproject.toml")
SKIP = {"__pycache__", ".pytest_cache", "tests"}

#: Blender's name for a platform, and the wheel tags pip must be asked for to
#: fill it. More than one because the kernel's wheels are not all built against
#: the same manylinux baseline. macOS is here but is not built by
#: `--all-platforms`; see the note on its entries.
PLATFORMS = {
    "linux-x64": ("manylinux_2_17_x86_64", "manylinux_2_28_x86_64", "manylinux_2_31_x86_64"),
    "windows-x64": ("win_amd64",),
    # macOS needs `--extra-wheels`: the sketch solver publishes no wheel for it
    # and one has to be built on a Mac first. Without it the build stops rather
    # than shipping a zip that installs and then cannot solve a sketch.
    "macos-arm64": ("macosx_11_0_arm64",),
    "macos-x64": ("macosx_11_0_x86_64",),
}
#: the interpreter the wheels are for -- Blender's own since 5.1
WHEEL_PYTHON = "3.13"
SKIP_SUFFIXES = (".pyc", ".autosave.json")


def bundled_paths():
    """Every file that goes into kernel/, relative to the repository."""
    for tree in BUNDLED_TREES:
        for root, dirs, files in os.walk(os.path.join(REPO, tree)):
            dirs[:] = sorted(d for d in dirs if d not in SKIP
                             and not d.startswith("."))
            for name in sorted(files):
                if name.endswith(SKIP_SUFFIXES):
                    continue
                yield os.path.relpath(os.path.join(root, name), REPO).replace(os.sep, "/")
    for folder, suffix in BUNDLED_FLAT.items():
        for name in sorted(os.listdir(os.path.join(REPO, folder))):
            if name.endswith(suffix) and not name.endswith(SKIP_SUFFIXES):
                yield os.path.join(folder, name).replace(os.sep, "/")
    for name in BUNDLED_FILES:
        if os.path.exists(os.path.join(REPO, name)):
            yield name


def documents_referenced(paths) -> set:
    """Every `document:` another bundled example points at, relative to the repo."""
    import json

    out = set()
    for rel in paths:
        if not rel.endswith(".json"):
            continue
        try:
            with open(os.path.join(REPO, rel), encoding="utf-8") as file:
                doc = json.load(file)
        except (OSError, ValueError):
            continue
        for feature in doc.get("features", []):
            if isinstance(feature, dict) and isinstance(feature.get("document"), str):
                out.add(os.path.normpath(os.path.join(os.path.dirname(rel), feature["document"])).replace(os.sep, "/"))
    return out


def requirements(studies: bool) -> list:
    """The pinned packages, as pip arguments."""
    path = os.path.join(REPO, "requirements-sim.txt" if studies else "requirements.txt")
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if line and not line.startswith("-r"):
            out.append(line)
        elif line.startswith("-r"):
            out += requirements(False)
    return out


def all_platforms() -> list:
    """What `--all-platforms` builds: not macOS, which needs `--extra-wheels`."""
    return [p for p in sorted(PLATFORMS) if not p.startswith("macos")]


def supplied_wheels(extra: str | None, platform: str) -> dict:
    """Package name -> wheel filename, for the wheels in `extra` that fit `platform`."""
    out = {}
    for name in sorted(os.listdir(extra) if extra and os.path.isdir(extra) else []):
        if not name.endswith(".whl"):
            continue
        if not any(tag in name for tag in PLATFORMS[platform]) and "py3-none-any" not in name:
            continue                    # a wheel for another platform
        out[name.split("-")[0].replace("_", "-").lower()] = name
    return out


def fetch_wheels(into: str, platform: str, studies: bool = False,
                 extra: str | None = None) -> list:
    """The kernel's wheels for one platform. Returns their filenames.

    Downloaded rather than checked in: the versions are pinned in
    requirements.txt, and a wheel in git is a 67 MB file nobody reviews.
    `extra` is a directory of wheels built elsewhere -- what a Mac has to
    supply, since the sketch solver publishes none for it -- and a package
    found there is not asked of PyPI.
    """
    import shutil
    import subprocess

    os.makedirs(into, exist_ok=True)
    supplied = supplied_wheels(extra, platform)
    for name in supplied.values():
        shutil.copy(os.path.join(extra, name), os.path.join(into, name))

    wanted = [r for r in requirements(studies)
              if re.split(r"[=<>!\[]", r, maxsplit=1)[0].strip().lower() not in supplied]
    if wanted:
        args = [sys.executable, "-m", "pip", "download", "--quiet", "--dest", into,
                "--only-binary=:all:", "--python-version", WHEEL_PYTHON]
        for tag in PLATFORMS[platform]:
            args += ["--platform", tag]
        done = subprocess.run(args + wanted, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        if done.returncode:
            missing = re.findall(r"No matching distribution found for (\S+)", done.stderr)
            raise SystemExit(
                "PACKAGE cannot build the %s zip: PyPI has no %s wheel for %s.\n"
                "  Build it on that platform and pass the folder as\n"
                "  --extra-wheels=<folder>. See .github/workflows/wheels.yml.\n%s"
                % (platform, ", ".join(missing) or "one of the", platform,
                   "" if missing else done.stderr[-400:]))
    return sorted(n for n in os.listdir(into) if n.endswith(".whl"))


def manifest_with_wheels(platform: str, wheels: list) -> str:
    """The add-on's manifest, plus the platform and the wheels it carries."""
    source = os.path.join(REPO, "blender_addon", "blender_manifest.toml")
    with open(source, encoding="utf-8") as file:
        text = file.read()
    listed = "".join('  "./wheels/%s",\n' % name for name in wheels)
    return text.replace(
        'type = "add-on"\n',
        'type = "add-on"\nplatforms = ["%s"]\n' % platform, 1).replace(
        'tags = ["Modeling", "Import-Export"]\n',
        'tags = ["Modeling", "Import-Export"]\n\nwheels = [\n%s]\n' % listed, 1)


def build(out_dir: str, platform: str | None = None, studies: bool = False,
          extra: str | None = None) -> str:
    """The zip. With a platform, it carries that platform's kernel wheels and
    Blender installs them itself, so there is nothing to press afterwards."""
    os.makedirs(out_dir, exist_ok=True)
    wheels, wheel_dir = [], os.path.join(out_dir, "wheels-" + (platform or "none"))
    if platform:
        wheels = fetch_wheels(wheel_dir, platform, studies, extra)
    target = os.path.join(out_dir, MODULE + ("-" + platform if platform else "") + ".zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        addon = os.path.join(REPO, "blender_addon")
        for root, dirs, files in os.walk(addon):
            dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith("."))
            for name in sorted(files):
                if name == "blender_manifest.toml" and platform:
                    continue                    # written below, with the wheels in it
                if name.endswith(".py") or name == "blender_manifest.toml":
                    rel = os.path.relpath(os.path.join(root, name), addon)
                    zf.write(os.path.join(root, name), f"{MODULE}/{rel}".replace(os.sep, "/"))
        if platform:
            zf.writestr(f"{MODULE}/blender_manifest.toml",
                        manifest_with_wheels(platform, wheels))
            for name in wheels:
                zf.write(os.path.join(wheel_dir, name), f"{MODULE}/wheels/{name}")
        for rel in bundled_paths():
            zf.write(os.path.join(REPO, rel), f"{MODULE}/kernel/{rel}")
        zf.writestr(f"{MODULE}/README.txt", READ_ME_CARRIED if platform else READ_ME_FETCHED)
    return target


READ_ME_FETCHED = (
    "RealParts\n\n"
    "Install this zip in Blender (Preferences > Add-ons > Install),\n"
    "enable it, and press Install the CAD kernel in the add-on's\n"
    "preferences. That is one download of about 85 MB into Blender's\n"
    "own Python. No separate Python is needed and no path has to be set.\n"
    "\n"
    "The kernel's code, the examples and the manual are in the kernel/\n"
    "folder beside this file.\n")

READ_ME_CARRIED = (
    "RealParts\n\n"
    "Install this zip in Blender (Preferences > Add-ons > Install) and\n"
    "enable it. The kernel is inside this file; there is nothing to\n"
    "download and nothing to press.\n"
    "\n"
    "This zip is for one platform. Blender refuses one built for\n"
    "another, which is the point: the kernel is compiled code.\n"
    "\n"
    "The kernel's code, the examples and the manual are in the kernel/\n"
    "folder beside this file.\n")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    out_dir = args[0] if args else os.path.join(REPO, "build")
    studies = "--studies" in flags
    extra = next((a.split("=", 1)[1] for a in flags if a.startswith("--extra-wheels=")), None)
    wanted = [f[2:] for f in flags if f[2:] in PLATFORMS]
    if "--all-platforms" in flags:
        wanted = all_platforms()
    if not wanted:
        out = build(out_dir)
        print("PACKAGE wrote", out, os.path.getsize(out), "bytes")
    for platform in wanted:
        out = build(out_dir, platform, studies, extra)
        print("PACKAGE wrote", out, "%.1f MB" % (os.path.getsize(out) / 1e6))
