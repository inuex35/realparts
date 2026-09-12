"""Install the zip as a Blender extension (the store's door), and use it through it.

    BLENDER_USER_EXTENSIONS=<fresh>/extensions BLENDER_USER_SCRIPTS=<fresh>/scripts \\
    BLENDER_USER_CONFIG=<fresh>/config CADCORE_KERNEL_PREFIX=.kernel/cp313 \\
        blender -b -noaudio --factory-startup -P tools/verify/verify_extension.py
"""
import os
import shutil
import sys
import tempfile

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
ZIP = os.environ.get("CADCORE_ZIP") or os.path.join(REPO, "build", "cadcore_bridge.zip")
OUT = tempfile.mkdtemp(prefix="cadcore_ext_")
fails = []


def check(label, ok, extra=""):
    print("EXTENSION %-42s %s %s" % (label, "ok" if ok else "FAIL", extra))
    if not ok:
        fails.append(label)


def _died(kind, value, tb):
    import traceback
    traceback.print_exception(kind, value, tb)
    print("EXTENSION RESULT died: %s: %s" % (kind.__name__, value))


sys.excepthook = _died

check("a fresh extensions dir", bool(os.environ.get("BLENDER_USER_EXTENSIONS")))
check("the zip exists", os.path.exists(ZIP), ZIP)

# the user_default repository is where Install from Disk puts a zip
prefs = bpy.context.preferences
repos = [r for r in prefs.extensions.repos if r.module == "user_default"]
check("the user_default repository exists", bool(repos), [r.module for r in prefs.extensions.repos])
r = bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default",
                                             enable_on_install=True)
check("installed as an extension", r == {'FINISHED'}, str(r))
module_name = "bl_ext.user_default.cadcore_bridge"
enabled = module_name in prefs.addons
if not enabled:
    r = bpy.ops.preferences.addon_enable(module=module_name)
    enabled = r == {'FINISHED'} and module_name in prefs.addons
check("enabled under the extension's module name", enabled, module_name)
import importlib                                                   # noqa: E402
addon = importlib.import_module(module_name)
check("its package is the extension's, not the legacy name",
      addon.__package__ == module_name, addon.__package__)
check("the preferences are found by that package",
      prefs.addons[module_name].preferences is not None)
check("the kernel's code is the bundled copy", addon.state.is_bundled(), addon.state.repo_root())
installed_at = os.path.dirname(os.path.realpath(addon.__file__))
check("it lives under the extensions dir",
      os.path.realpath(os.environ["BLENDER_USER_EXTENSIONS"]) in installed_at, installed_at)
home = addon.state.kernel_home()
prefix = addon.client.kernel_prefix(addon.state.repo_root(), home)
given = os.environ.get("CADCORE_KERNEL_PREFIX")
os.makedirs(os.path.dirname(prefix), exist_ok=True)
if os.path.lexists(prefix):
    os.remove(prefix) if os.path.islink(prefix) else shutil.rmtree(prefix)
try:
    os.symlink(given, prefix, target_is_directory=True)
except OSError:
    import _winapi
    _winapi.CreateJunction(given, prefix)

# and it works: the same three things a buyer does first
example = os.path.join(addon.state.repo_root(), "examples", "bracket.json")
doc = os.path.join(OUT, "bracket.json")
shutil.copy(example, doc)
props = bpy.context.scene.cadcore
r = bpy.ops.cadcore.open(filepath=doc)
check("open a bundled example through the extension", r == {'FINISHED'} and props.volume > 0,
      "%s %.0f mm3" % (props.status, props.volume))
r = bpy.ops.cadcore.add_requirement(quantity='mass_g', compare='<=', value="150", req_id="light")
check("a requirement through the extension", r == {'FINISHED'} and len(props.requirements) == 1
      and props.requirements[0].ok is False, props.status)
step = os.path.join(OUT, "bracket.step")
r = bpy.ops.cadcore.export_step(filepath=step)
check("export STEP through the extension", r == {'FINISHED'} and os.path.getsize(step) > 1000)

shutil.rmtree(OUT, ignore_errors=True)
print("EXTENSION RESULT", "all ok" if not fails else "FAILED: " + ", ".join(fails))
# `blender -b -P` exits 0 whatever the script did; CI greps the line above, and
# a person running this by hand deserves the exit code too
if fails:
    sys.exit(1)
