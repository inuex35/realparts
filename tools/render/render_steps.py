"""Every feature of a document, one frame each: how the thing was built.

    blender -b -noaudio --factory-startup -P tools/render/render_steps.py \
        -- examples/spot/spot.json build/steps

The same document asked for a different `result` each time; the camera is
framed on the finished part and left alone, so the part is seen to grow.
"""
import math
import os
import sys
import time

import bpy
import mathutils

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
DOC = argv[0] if argv else "examples/spot/spot.json"
OUT = argv[1] if len(argv) > 1 else os.path.join(REPO, "build", "steps")
TILT = float(os.environ.get("STEP_TILT", "0.30"))
TURN = float(os.environ.get("STEP_TURN", "236"))
#: how many identical frames each step gets. One, because holding a step on
#: screen is the encoder's job -- `ffmpeg -framerate 2 ... -r 24` shows each
#: image for half a second, and rendering the same picture six times costs six
#: times as much for exactly nothing.
HOLD = int(os.environ.get("STEP_HOLD", "1"))
OCP_PATH = os.environ.get("CAD_OCP_PATH", "/tmp/bpy313")

sys.path.insert(0, REPO)
if os.path.isdir(OCP_PATH):
    sys.path.insert(0, OCP_PATH)

from cadcore.service.server import Session  # noqa: E402
from cadcore.geometry.io.tessellate import tessellate  # noqa: E402
from cadcore.errors import CadError  # noqa: E402


def polyline(segment, plane, per_arc=24):
    """A sketch segment as points in space, through the sketch's own plane.

    Splines already carry the points they were solved through, arcs are walked
    round, and a line is its two ends. `plane.to_3d` is what makes this the
    sketch's geometry rather than a drawing of it: the profile is shown where
    the sketch actually is, which for a wing is a section standing at the tip.
    """
    if segment.kind == "circle" and segment.centre is not None:
        # a circle's start and end are both its centre, so the fallback below
        # would draw it as a single point
        cx, cy = segment.centre
        flat = [(cx + segment.radius * math.cos(2 * math.pi * i / (per_arc * 2)),
                 cy + segment.radius * math.sin(2 * math.pi * i / (per_arc * 2)))
                for i in range(per_arc * 2 + 1)]
    elif segment.kind == "spline" and segment.through:
        flat = list(segment.through)
    elif segment.kind == "arc" and segment.centre is not None:
        cx, cy = segment.centre
        a0 = math.atan2(segment.start[1] - cy, segment.start[0] - cx)
        a1 = math.atan2(segment.end[1] - cy, segment.end[0] - cx)
        if segment.ccw and a1 <= a0:
            a1 += 2 * math.pi
        if not segment.ccw and a1 >= a0:
            a1 -= 2 * math.pi
        flat = [(cx + segment.radius * math.cos(a0 + (a1 - a0) * i / per_arc),
                 cy + segment.radius * math.sin(a0 + (a1 - a0) * i / per_arc))
                for i in range(per_arc + 1)]
    else:
        flat = [segment.start, segment.end]
    return [tuple(plane.to_3d(*point)) for point in flat]

if not os.path.isabs(DOC):
    DOC = os.path.join(REPO, DOC)
session = Session(autosave=False)
session.op_open(DOC)
session.op_build()
final = tessellate(session.body, deflection=0.4, angular=0.35)
whole = final["vertices"]
low = mathutils.Vector([min(v[i] for v in whole) for i in range(3)])
high = mathutils.Vector([max(v[i] for v in whole) for i in range(3)])
centre = (low + high) / 2
diagonal = (high - low).length
finished = session.doc.result

# every sketch is taken from the *finished* build, before the loop below
# starts rebuilding intermediate results. The evaluator only holds the sketches
# the last build actually needed, so asking for `plan` right after building
# `slab` -- which is drawn from `side` alone -- returns nothing, and the plan
# view went missing from the sequence with no error at all
solved_sketches = {}
for feature in session.doc.features:
    if feature.type != "sketch":
        continue
    try:
        solved_sketches[feature.id] = session.evaluator.sketch_of(feature.id)
    except Exception:                                            # noqa: BLE001
        pass

steps = []
for feature in session.doc.features:
    if feature.type == "sketch":
        # a sketch is not a body, and skipping it skipped the most interesting
        # frame in the sequence: the profile everything downstream is lofted or
        # extruded from
        solved = solved_sketches.get(feature.id)
        if solved is None:
            continue
        lines = [polyline(seg, solved.plane)
                 for loop in solved.loops for seg in loop]
        if not lines:
            continue
        steps.append((feature.id, "sketch", {"lines": lines}))
        print("STEP %2d  %-14s %-10s %2d curves, %d points"
              % (len(steps), feature.id, "sketch", len(lines),
                 sum(len(p) for p in lines)))
        continue
    session.doc.result = feature.id
    try:
        session._rebuild()
        tri = tessellate(session.body, deflection=0.4, angular=0.35)
    except (CadError, Exception):                                # noqa: BLE001
        # a feature can also be a tool, only meaningful once something has
        # been cut with it
        continue
    if not tri["triangles"]:
        continue
    steps.append((feature.id, feature.type, tri))
    print("STEP %2d  %-14s %-10s %7d triangles"
          % (len(steps), feature.id, feature.type, len(tri["triangles"])))
session.doc.result = finished
print("STEP %d buildable features out of %d" % (len(steps), len(session.doc.features)))

for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)
scene = bpy.context.scene

material = bpy.data.materials.new("part")
material.use_nodes = True
bsdf = material.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (0.86, 0.68, 0.10, 1.0)
bsdf.inputs["Metallic"].default_value = 0.35
bsdf.inputs["Roughness"].default_value = 0.35

mesh = bpy.data.meshes.new("step")
mesh.materials.append(material)
part = bpy.data.objects.new("step", mesh)
scene.collection.objects.link(part)

# sketches are drawn as tubes rather than as edges: a one-pixel line vanishes
# at any distance, and the whole point of showing the profile is that it can be
# seen against the part it becomes
ink = bpy.data.materials.new("ink")
ink.use_nodes = True
ib = ink.node_tree.nodes["Principled BSDF"]
ib.inputs["Base Color"].default_value = (0.05, 0.06, 0.08, 1.0)
ib.inputs["Roughness"].default_value = 0.5
curve = bpy.data.curves.new("sketch", 'CURVE')
curve.dimensions = '3D'
curve.bevel_depth = diagonal * 0.0035
curve.materials.append(ink)
pen = bpy.data.objects.new("sketch", curve)
scene.collection.objects.link(pen)

# No ghost of the finished part. One was tried at 6% alpha and the machine has
# eight overlapping limbs, so six per cent eight times over is most of the way
# to opaque: the step being rendered disappeared behind the thing it was
# building. The camera is fixed instead, which shows the growth without
# painting over it.
bpy.ops.mesh.primitive_plane_add(size=diagonal * 60,
                                 location=(centre.x, centre.y, low.z - diagonal * 0.02))
floor = bpy.context.object
fm = bpy.data.materials.new("floor")
fm.use_nodes = True
fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = \
    (0.16, 0.18, 0.21, 1.0)
fm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.85
floor.data.materials.append(fm)

scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 48
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = 1280, 720
scene.view_settings.view_transform = 'AgX'
scene.view_settings.exposure = -0.2
scene.world.node_tree.nodes["Background"].inputs[0].default_value = \
    (0.30, 0.36, 0.45, 1)

sky = bpy.data.lights.new("Sun", 'SUN')
sky.energy = 3.0
sky.angle = math.radians(3.0)
sun = bpy.data.objects.new("Sun", sky)
sun.rotation_euler = mathutils.Vector((math.radians(50), 0.0, math.radians(-40)))
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
camera.location = centre + direction * diagonal * 2.35
camera.rotation_euler = (centre - camera.location).to_track_quat('-Z', 'Y').to_euler()

os.makedirs(OUT, exist_ok=True)
scene.render.image_settings.file_format = 'PNG'
clock = time.perf_counter()
frame = 0
for fid, kind, tri in steps:
    mesh.clear_geometry()
    # sketches accumulate rather than replace: a loft between two sections is
    # a thing you have to see both of, and the ten rings of a fuselage are the
    # shape of it before there is any surface to look at. They stay up under
    # the solid too, which is where they belong -- the profile is not scaffolding
    if kind == "sketch":
        for points in tri["lines"]:
            spline = curve.splines.new('POLY')
            spline.points.add(len(points) - 1)
            for i, point in enumerate(points):
                spline.points[i].co = (point[0], point[1], point[2], 1.0)
    else:
        mesh.from_pydata([tuple(v) for v in tri["vertices"]], [],
                         [tuple(t) for t in tri["triangles"]])
    mesh.update()
    for _ in range(HOLD):
        frame += 1
        scene.render.filepath = os.path.join(OUT, "f%04d" % frame)
        bpy.ops.render.render(write_still=True)
open(os.path.join(OUT, "steps.txt"), "w", encoding="utf-8").write(
    "\n".join("%2d  %-16s %s" % (i + 1, fid, kind)
              for i, (fid, kind, _) in enumerate(steps)) + "\n")
print("STEPS wrote %d frames in %.0fs into %s"
      % (frame, time.perf_counter() - clock, OUT))
