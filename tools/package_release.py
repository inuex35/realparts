"""Build separate Web, Native and Blender release archives."""
import argparse
from pathlib import Path
import re
import zipfile

from package import REPO, build, bundled_paths


def package(target, version, out_dir):
    """Build one product with its shared kernel and install instructions."""
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", version):
        raise ValueError("Use a version such as 0.2.4 or 0.2.4-rc.1")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"realparts-{target}-{version}"
    archive = out_dir / f"{name}.zip"
    if target == "blender":
        Path(build(str(out_dir))).replace(archive)
        return archive
    root = Path(REPO)
    paths = {p for p in bundled_paths() if not p.startswith("cadcore/service/web_ui/")}
    if target == "web":
        ui = root / "cadcore/service/web_ui"
        if not (ui / "index.html").is_file() or not list((ui / "assets").glob("*.js")):
            raise ValueError("Build the Web UI with npm ci && npm run build first")
        paths.update(p.relative_to(root).as_posix() for p in ui.rglob("*") if p.is_file())
        requirements = "requirements.txt"
        command = "python -m cadcore.service.web --open"
    elif target == "native":
        paths.update(p.relative_to(root).as_posix() for p in (root / "native_app").rglob("*")
                     if p.is_file() and p.suffix in {".py", ".qml"}
                     and "tests" not in p.relative_to(root).parts)
        paths.add("requirements-native.txt")
        requirements = "requirements-native.txt"
        command = "python -m native_app"
    else:
        raise ValueError(f"Unknown product: {target}")
    instructions = (
        f"RealParts {target} {version}\n\n"
        "Install Python 3.12 or 3.13. Extract this archive and open a terminal in this folder.\n"
        "Create a virtual environment: python -m venv .venv\n"
        "Activate on Windows: .venv\\Scripts\\activate\n"
        "Activate on Linux/macOS: source .venv/bin/activate\n"
        f"Install dependencies (internet required): python -m pip install -r {requirements}\n"
        f"Start: {command}\n\n"
        "Linux and Windows are supported by the kernel dependency wheels.\n"
        "macOS requires a separately built planegcs wheel; see .github/workflows/wheels.yml in the repository.\n"
    )
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(paths):
            zipped.write(root / path, f"{name}/{path}")
        zipped.writestr(f"{name}/START.txt", instructions)
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("web", "native", "blender"))
    parser.add_argument("version")
    parser.add_argument("--out-dir", default="build/releases")
    args = parser.parse_args()
    print(package(args.target, args.version, args.out_dir))
