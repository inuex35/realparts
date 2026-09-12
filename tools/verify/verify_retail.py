"""Install the add-on from the zip the way a buyer does, and use it the way a buyer would.

    BLENDER_USER_SCRIPTS=<empty dir>/scripts BLENDER_USER_CONFIG=<empty dir>/config \\
        blender -b -noaudio --factory-startup -P tools/verify/verify_retail.py

`CADCORE_KERNEL_PREFIX` names an already-filled kernel prefix: the button
that fetches the wheels is modal and needs a window.
"""
import json
import os
import shutil
import sys
import tempfile

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
ZIP = os.environ.get("CADCORE_ZIP") or os.path.join(REPO, "build", "cadcore_bridge.zip")
OUT = tempfile.mkdtemp(prefix="cadcore_retail_")
fails = []


def check(label, ok, extra=""):
    print("RETAIL %-44s %s %s" % (label, "ok" if ok else "FAIL", extra))
    if not ok:
        fails.append(label)


def _died(kind, value, tb):
    import traceback
    traceback.print_exception(kind, value, tb)
    print("RETAIL RESULT died: %s: %s" % (kind.__name__, value))


sys.excepthook = _died

# -- a Blender that has never seen this add-on --------------------------------
scripts = os.environ.get("BLENDER_USER_SCRIPTS", "")
config = os.environ.get("BLENDER_USER_CONFIG", "")
check("run against a fresh user scripts dir", bool(scripts), scripts)
check("and a fresh user config dir", bool(config), config)
addons_before = set(bpy.context.preferences.addons.keys())
check("the add-on is not there yet", "cadcore_bridge" not in addons_before)

# -- install from the zip, as Preferences > Add-ons > Install does --------------
check("the zip exists", os.path.exists(ZIP), ZIP)
r = bpy.ops.preferences.addon_install(filepath=ZIP, overwrite=True)
check("installed from the zip", r == {'FINISHED'}, str(r))
r = bpy.ops.preferences.addon_enable(module="cadcore_bridge")
check("enabled", r == {'FINISHED'} and "cadcore_bridge" in bpy.context.preferences.addons)
import cadcore_bridge as addon                                     # noqa: E402

installed_at = os.path.dirname(os.path.realpath(addon.__file__))
check("it lives under the user scripts dir",
      os.path.realpath(scripts) in os.path.realpath(installed_at), installed_at)

# -- nothing set, everything found -------------------------------------------------
prefs = bpy.context.preferences.addons["cadcore_bridge"].preferences
check("no repository path was set", prefs.repo_path == "", prefs.repo_path)
check("no interpreter path was set", prefs.python_path == "", prefs.python_path)
check("the kernel's code is the bundled copy", addon.state.is_bundled(),
      addon.state.repo_root())
check("and it is inside the installed add-on",
      addon.state.repo_root() == os.path.join(installed_at, "kernel"),
      addon.state.repo_root())
home = addon.state.kernel_home()
check("downloaded packages go under the user config, not the add-on",
      home is not None and os.path.realpath(config) in os.path.realpath(home), home)

# the kernel's packages: linked in from a prefix that was filled the way the
# button fills one, because the button itself needs a window
prefix = addon.client.kernel_prefix(addon.state.repo_root(), home)
given = os.environ.get("CADCORE_KERNEL_PREFIX")
check("a filled kernel prefix was named", bool(given) and os.path.isdir(given or ""), given)
os.makedirs(os.path.dirname(prefix), exist_ok=True)
if os.path.lexists(prefix):
    os.rmdir(prefix) if os.path.isdir(prefix) and not os.path.islink(prefix) else os.remove(prefix)
try:
    os.symlink(given, prefix, target_is_directory=True)
except OSError:
    # a symlink on Windows needs a privilege a buyer's account may not have;
    # a junction needs none, and Python resolves it like a link
    import _winapi
    _winapi.CreateJunction(given, prefix)
python, site = addon.client.kernel_for(addon.state.repo_root(), "", home)
check("the interpreter is Blender's own", python == sys.executable, python)
# compared as places, not as spellings: on Windows the same directory can
# arrive written with either separator, and did
check("the packages are looked for where they were put",
      site is not None and os.path.isdir(site)
      and os.path.normcase(os.path.realpath(site)).startswith(
          os.path.normcase(os.path.realpath(prefix))), "%s under %s" % (site, prefix))

# -- what the store page promises ------------------------------------------------------
example = os.path.join(addon.state.repo_root(), "examples", "bracket.json")
check("the bundled example exists", os.path.exists(example), example)
doc = os.path.join(OUT, "bracket.json")
shutil.copy(example, doc)
props = bpy.context.scene.cadcore
r = bpy.ops.cadcore.open(filepath=doc)
check("open a bundled example", r == {'FINISHED'} and props.volume > 0,
      "%s %.0f mm3" % (props.status, props.volume))
volume = props.volume

step = os.path.join(OUT, "bracket.step")
r = bpy.ops.cadcore.export_step(filepath=step)
check("export STEP", r == {'FINISHED'} and os.path.getsize(step) > 1000,
      "%s bytes" % (os.path.getsize(step) if os.path.exists(step) else 0))

svg = os.path.join(OUT, "bracket.svg")
r = bpy.ops.cadcore.drawing(filepath=svg)
with open(svg, "rb") as handle:
    sheet = handle.read()
check("draw a sheet", r == {'FINISHED'} and sheet.startswith(b"<"), props.status)
check("with a diameter sign in it, written as utf-8",
      "⌀".encode("utf-8") in sheet,
      "the dimension text that killed the export on a cp932 locale")

r = bpy.ops.cadcore.simulate()
check("run the studies", r == {'FINISHED'} and props.studies and
      all(s.ok for s in props.studies),
      "%s: %s" % (props.status, [(s.name, s.ok) for s in props.studies]))

# -- an assistant drives it, with nothing more installed ---------------------------------
import subprocess
import threading
import time

bridge = addon.bridge
r = bpy.ops.cadcore.bridge()
check("the assistant's door opens", r == {'FINISHED'} and bridge.listening(), props.status)
r = bpy.ops.cadcore.assistant_config(client='claude')
# the clipboard needs a window, and there is none here; the button also
# writes the same text to a file under the user config, and that is what
# a buyer who closed the terminal before pasting is told to look for
written = os.path.join(home, "assistant-claude.json")
check("a config for Claude Code is written", r == {'FINISHED'} and os.path.exists(written),
      props.status)
with open(written, encoding="utf-8") as handle:
    pasted = handle.read()
check("and it is an MCP server entry", "cadcore.service.mcp" in pasted and "--attach" in pasted,
      pasted[:120])
spec = json.loads(pasted)["mcpServers"]["cadcore"]
check("it names Blender's own interpreter", spec["command"] == sys.executable,
      spec["command"])
check("and the installed kernel", site in spec["env"]["PYTHONPATH"], spec["env"]["PYTHONPATH"])

# run exactly that config, as the assistant's client would
mcp = subprocess.Popen([spec["command"], *spec["args"]],
                       env=dict(os.environ, **spec["env"]),
                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                       bufsize=1)
replies = []
threading.Thread(target=lambda: [replies.append(json.loads(l)) for l in mcp.stdout if l.strip()],
                 daemon=True).start()
# the person clicks a face; the assistant should be able to ask which
body_ob = addon.sync.body()
table = json.loads(body_ob["cad_face_table"])
attr = body_ob.data.attributes["cad_face"]
clicked = "plate/+z" if "plate/+z" in table else table[0]
for poly in body_ob.data.polygons:
    poly.select = table[attr.data[poly.index].value] == clicked
other = next(n for n in table if n != clicked)
for request in ({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "build", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                 "params": {"name": "selection", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                 "params": {"name": "select", "arguments": {"faces": [other]}}}):
    mcp.stdin.write(json.dumps(request) + "\n")
mcp.stdin.flush()
# the bridge answers on Blender's main thread from a timer, and without a
# window nothing ticks the timer: this loop is the tick
deadline = time.time() + 180
while len(replies) < 5 and time.time() < deadline and mcp.poll() is None:
    bridge._serve()
    time.sleep(0.05)
mcp.stdin.close()
by_id = {r.get("id"): r for r in replies}
check("the assistant's client connected", 1 in by_id and "serverInfo" in by_id[1].get("result", {}),
      str(by_id.get(1, "no reply"))[:120])
listed = by_id.get(2, {}).get("result", {}).get("tools", [])
check("and saw the tools", len(listed) >= 40, "%d tools" % len(listed))
built = by_id.get(3, {}).get("result", {})
text = built.get("content", [{}])[0].get("text", "{}") if built else "{}"
volume_seen = json.loads(text).get("volume_mm3") if text.startswith("{") else None
# the panel's number is a single-precision property, so 69115.095 reads back
# as 69115.1; the comparison is to what the panel can hold
check("and built the document on screen through Blender",
      volume_seen is not None and abs(volume_seen - props.volume) < 1e-5 * props.volume,
      "%s vs %.1f" % (volume_seen, props.volume))
seen = json.loads(by_id.get(4, {}).get("result", {}).get("content", [{}])[0].get("text", "{}"))
check("the assistant can ask what the person clicked",
      seen.get("faces") == [clicked], str(seen)[:120])
check("and the tool list said so", "selection" in {t["name"] for t in listed})
now = [table[attr.data[p.index].value] for p in body_ob.data.polygons if p.select]
check("and can point at a face on the person's screen",
      now and set(now) == {other}, "%s selected after select(%s)" % (sorted(set(now)), other))
mcp.wait(timeout=30)
bpy.ops.cadcore.bridge()
check("and the door shuts", not bridge.listening())

# -- and the sidebar's parameter edit still rebuilds -----------------------------------
for p in props.parameters:
    if p.name == "thickness":
        p.value = p.value + 2
        break
check("a parameter edit rebuilds", props.volume != volume,
      "%.0f -> %.0f mm3" % (volume, props.volume))

shutil.rmtree(OUT, ignore_errors=True)
print("RETAIL RESULT", "all ok" if not fails else "FAILED: " + ", ".join(fails))
# `blender -b -P` exits 0 whatever the script did; CI greps the line above, and
# a person running this by hand deserves the exit code too
if fails:
    sys.exit(1)
