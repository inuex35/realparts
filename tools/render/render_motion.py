"""A mechanism moving: one parameter swept, the kernel rebuilt every frame.

    blender -b -noaudio --factory-startup -P tools/render/render_motion.py \
        -- examples/workshop/ratchet/ratchet.json build/rt_move 'pawl_deg=-9:24' 48

Tracks are `name=from:to` or `name=a,b,c`, several separated by commas;
`--pingpong` walks the range and comes back.
"""
import math
import os
import re
import sys
import time

import bpy
import mathutils

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
DOC = argv[0] if argv else "examples/workshop/ratchet/ratchet.json"
OUT = argv[1] if len(argv) > 1 else os.path.join(REPO, "build", "motion")
TRACKS = argv[2] if len(argv) > 2 else ""
FRAMES = int(argv[3]) if len(argv) > 3 else 36
PING = os.environ.get("MOTION_PINGPONG", "0") not in ("0", "", "no")
TILT = float(os.environ.get("MOTION_TILT", "0.45"))
TURN = float(os.environ.get("MOTION_TURN", "232"))
DEFL = float(os.environ.get("MOTION_DEFLECTION", "0.35"))
ACCENT = os.environ.get("MOTION_ACCENT", "")
OCP_PATH = os.environ.get("CAD_OCP_PATH", "/tmp/bpy313")

sys.path.insert(0, REPO)
if os.path.isdir(OCP_PATH):
    sys.path.insert(0, OCP_PATH)

from cadcore.service.server import Session  # noqa: E402
from cadcore.geometry.io.tessellate import tessellate  # noqa: E402


def merge(parts):
    """One mesh out of several: triangle indices are per-part and have to move."""
    vertices, triangles = [], []
    for tri in parts:
        base = len(vertices)
        vertices.extend(tri["vertices"])
        triangles.extend([tuple(base + i for i in t) for t in tri["triangles"]])
    return {"vertices": vertices, "triangles": triangles}


def parse(spec: str) -> dict:
    """`a=0:90,b=1,2,3` -> {"a": ("range", 0, 90), "b": ("list", [1,2,3])}."""
    out = {}
    for piece in [p for p in spec.split(";") if p.strip()]:
        name, _, rest = piece.partition("=")
        name = name.strip()
        if ":" in rest:
            lo, hi = rest.split(":")
            out[name] = ("range", float(lo), float(hi))
        else:
            out[name] = ("list", [float(v) for v in rest.split(",")])
    return out


def at(track, t: float):
    kind = track[0]
    if kind == "range":
        return track[1] + (track[2] - track[1]) * t
    values = track[1]
    return values[min(int(t * len(values)), len(values) - 1)]


if not os.path.isabs(DOC):
    DOC = os.path.join(REPO, DOC)
tracks = parse(TRACKS)
session = Session(autosave=False)
session.op_open(DOC)
session.op_build()

clock = time.perf_counter()
meshes, reach, spent = [], [], []
for index in range(FRAMES):
    t = index / FRAMES
    if PING:
        t = 2 * t if t < 0.5 else 2 * (1 - t)
    tick = time.perf_counter()
    if tracks:
        session.op_set_parameters({k: at(v, t) for k, v in tracks.items()})
    if ACCENT:
        # two meshes, so a part can be told from the frame it moves in. One
        # material over the whole assembly is fine for a gear train and no use
        # at all for a display, where the reading *is* which part faces you
        split = [[], []]
        for name, body in sorted(session._placed_parts().items()):
            split[bool(re.search(ACCENT, name))].append(
                tessellate(body, deflection=DEFL, angular=0.4))
        tri = [merge(split[0]), merge(split[1])]
        reach.extend(tri[0]["vertices"])
        reach.extend(tri[1]["vertices"])
    else:
        tri = [tessellate(session.body, deflection=DEFL, angular=0.4)]
        reach.extend(tri[0]["vertices"])
    meshes.append(tri)
    spent.append((time.perf_counter() - tick) * 1000)
print("MOTION %d poses, median %.0f ms each, %.0fs total"
      % (FRAMES, sorted(spent)[len(spent) // 2], time.perf_counter() - clock))

for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)
scene = bpy.context.scene

shades = [((0.62, 0.64, 0.68, 1.0), 0.7, 0.32)]
if ACCENT:
    shades = [((0.10, 0.11, 0.13, 1.0), 0.1, 0.55),
              ((0.86, 0.42, 0.09, 1.0), 0.2, 0.38)]
targets = []
for index, (colour, metal, rough) in enumerate(shades):
    material = bpy.data.materials.new("part%d" % index)
    material.use_nodes = True
    bsdf = material.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = colour
    bsdf.inputs["Metallic"].default_value = metal
    bsdf.inputs["Roughness"].default_value = rough
    mesh = bpy.data.meshes.new("mech%d" % index)
    mesh.materials.append(material)
    scene.collection.objects.link(bpy.data.objects.new("mech%d" % index, mesh))
    targets.append(mesh)

# framed from every pose, not the first: a handle that swings out of shot on
# frame 30 is a render nobody can use
low = mathutils.Vector([min(v[i] for v in reach) for i in range(3)])
high = mathutils.Vector([max(v[i] for v in reach) for i in range(3)])
centre = (low + high) / 2
diagonal = (high - low).length
print("MOTION swept %.0f x %.0f x %.0f mm"
      % (high.x - low.x, high.y - low.y, high.z - low.z))

bpy.ops.mesh.primitive_plane_add(size=diagonal * 60,
                                 location=(centre.x, centre.y, low.z - diagonal * 0.01))
floor = bpy.context.object
fm = bpy.data.materials.new("floor")
fm.use_nodes = True
fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = \
    (0.16, 0.18, 0.21, 1.0)
fm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.85
floor.data.materials.append(fm)

scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 40
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = 1000, 562
scene.view_settings.view_transform = 'AgX'
scene.view_settings.exposure = -0.2
scene.world.node_tree.nodes["Background"].inputs[0].default_value = \
    (0.30, 0.36, 0.45, 1)

sky = bpy.data.lights.new("Sun", 'SUN')
sky.energy = 3.0
sky.angle = math.radians(3.0)
sun = bpy.data.objects.new("Sun", sky)
sun.rotation_euler = mathutils.Vector((math.radians(52), 0.0, math.radians(-38)))
scene.collection.objects.link(sun)
for name, offset, energy in (("Key", (0.8, -1.0, 0.9), 1.0),
                             ("Fill", (-1.2, -0.4, 0.5), 0.4),
                             ("Rim", (-0.3, 1.2, 0.9), 0.8)):
    light = bpy.data.lights.new(name, 'AREA')
    light.energy = energy * 3.0 * diagonal * diagonal
    light.size = diagonal
    lamp = bpy.data.objects.new(name, light)
    lamp.location = centre + mathutils.Vector(offset) * diagonal
    lamp.rotation_euler = (centre - lamp.location).to_track_quat('-Z', 'Y').to_euler()
    scene.collection.objects.link(lamp)

camera_data = bpy.data.cameras.new("Cam")
camera_data.lens = 55.0
camera_data.clip_start = diagonal * 0.01
camera_data.clip_end = diagonal * 500.0
camera = bpy.data.objects.new("Cam", camera_data)
scene.collection.objects.link(camera)
scene.camera = camera
direction = mathutils.Vector((math.cos(math.radians(TURN)),
                              math.sin(math.radians(TURN)), TILT)).normalized()
camera.location = centre + direction * diagonal * 2.5
camera.rotation_euler = (centre - camera.location).to_track_quat('-Z', 'Y').to_euler()

os.makedirs(OUT, exist_ok=True)
scene.render.image_settings.file_format = 'PNG'
for index, tri in enumerate(meshes):
    for mesh, piece in zip(targets, tri):
        mesh.clear_geometry()
        mesh.from_pydata([tuple(v) for v in piece["vertices"]], [],
                         [tuple(t) for t in piece["triangles"]])
        mesh.update()
    scene.render.filepath = os.path.join(OUT, "f%04d" % (index + 1))
    bpy.ops.render.render(write_still=True)
print("MOTION wrote %d frames into %s" % (len(meshes), OUT))
