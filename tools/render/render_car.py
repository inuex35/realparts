"""The rally car, turned once: a shell and four wheels that are one document four times.

    blender -b -noaudio --factory-startup -P tools/render/render_car.py \
        -- build/car [frames]
"""
import math
import os
import sys
import time

import bpy
import mathutils

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else os.path.join(REPO, "build", "car")
FRAMES = int(argv[1]) if len(argv) > 1 else 1
TILT = float(os.environ.get("CAR_TILT", "0.16"))
START = float(os.environ.get("CAR_TURN", "212"))
OCP_PATH = os.environ.get("CAD_OCP_PATH", "/tmp/bpy313")

sys.path.insert(0, REPO)
if os.path.isdir(OCP_PATH):
    sys.path.insert(0, OCP_PATH)

from cadcore.service.server import Session  # noqa: E402
from cadcore.geometry.io.tessellate import tessellate  # noqa: E402

clock = time.perf_counter()
session = Session(autosave=False)
session.op_open(os.path.join(REPO, "examples", "rally", "rallycar.json"))
session.op_build()
# a body drawn with splines and intersected needs a fine mesh or the roofline
# shows its own control points
tri = tessellate(session.body, deflection=1.2, angular=0.25)
print("PLANE %d triangles in %.1fs" % (len(tri["triangles"]),
                                       time.perf_counter() - clock))

for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)
scene = bpy.context.scene

mesh = bpy.data.meshes.new("spot")
mesh.from_pydata([tuple(v) for v in tri["vertices"]], [],
                 [tuple(t) for t in tri["triangles"]])
# smooth, and let the angle decide: a lofted surface is smooth and its wing
# leading edge is not, and `shade_auto_smooth` is the difference
for polygon in mesh.polygons:
    polygon.use_smooth = True
mesh.update()
material = bpy.data.materials.new("skin")
material.use_nodes = True
bsdf = material.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (0.72, 0.11, 0.09, 1.0)
bsdf.inputs["Metallic"].default_value = 0.25
bsdf.inputs["Roughness"].default_value = 0.28
mesh.materials.append(material)
plane = bpy.data.objects.new("spot", mesh)
scene.collection.objects.link(plane)

reach = tri["vertices"]
low = mathutils.Vector([min(v[i] for v in reach) for i in range(3)])
high = mathutils.Vector([max(v[i] for v in reach) for i in range(3)])
centre = (low + high) / 2
diagonal = (high - low).length
print("PLANE %.0f x %.0f x %.0f mm" % (high.x - low.x, high.y - low.y,
                                       high.z - low.z))

bpy.ops.mesh.primitive_plane_add(size=diagonal * 60,
                                 location=(centre.x, centre.y, low.z - diagonal * 0.30))
floor = bpy.context.object
floor_material = bpy.data.materials.new("floor")
floor_material.use_nodes = True
floor_material.node_tree.nodes["Principled BSDF"].inputs["Base Color"]\
    .default_value = (0.16, 0.18, 0.21, 1.0)
floor_material.node_tree.nodes["Principled BSDF"].inputs["Roughness"]\
    .default_value = 0.85
floor.data.materials.append(floor_material)

scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 64
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = 1280, 720
scene.view_settings.view_transform = 'AgX'
scene.view_settings.exposure = -0.2
scene.world.node_tree.nodes["Background"].inputs[0].default_value = \
    (0.33, 0.41, 0.52, 1)

sky = bpy.data.lights.new("Sun", 'SUN')
sky.energy = 3.2
sky.angle = math.radians(2.5)
sun = bpy.data.objects.new("Sun", sky)
sun.rotation_euler = mathutils.Vector((math.radians(48), 0.0, math.radians(-42)))
scene.collection.objects.link(sun)
for name, offset, energy in (("Key", (0.8, -1.0, 0.8), 1.0),
                             ("Fill", (-1.2, -0.4, 0.5), 0.4),
                             ("Rim", (-0.3, 1.2, 0.9), 0.9)):
    light = bpy.data.lights.new(name, 'AREA')
    light.energy = energy * 3.0 * diagonal * diagonal
    light.size = diagonal
    lamp = bpy.data.objects.new(name, light)
    lamp.location = centre + mathutils.Vector(offset) * diagonal
    lamp.rotation_euler = (centre - lamp.location).to_track_quat('-Z', 'Y').to_euler()
    scene.collection.objects.link(lamp)

camera_data = bpy.data.cameras.new("Cam")
camera_data.lens = 55.0
# the model is in millimetres and so is the camera
camera_data.clip_start = diagonal * 0.01
camera_data.clip_end = diagonal * 500.0
camera = bpy.data.objects.new("Cam", camera_data)
scene.collection.objects.link(camera)
scene.camera = camera

os.makedirs(OUT, exist_ok=True)
scene.render.image_settings.file_format = 'PNG'
for index in range(FRAMES):
    turn = 2 * math.pi * index / max(FRAMES, 1) + math.radians(START)
    direction = mathutils.Vector((math.cos(turn), math.sin(turn), TILT)).normalized()
    camera.location = centre + direction * diagonal * 2.35
    camera.rotation_euler = (centre - camera.location).to_track_quat('-Z', 'Y').to_euler()
    scene.render.filepath = os.path.join(OUT, "f%04d" % (index + 1))
    bpy.ops.render.render(write_still=True)
print("PLANE wrote %d frames into %s" % (FRAMES, OUT))
