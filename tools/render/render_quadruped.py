"""The quadruped walking, rebuilt from the document on every frame.

    blender -b -noaudio --factory-startup -P tools/render/render_quadruped.py \
        -- build/walk_frames [frames] [ocp_path]

Every joint is a mate angle and a parameter: a frame is eight numbers fed to
the kernel, about 350 ms each, so the walk is baked to PNGs (`stitch.py`).
"""
import math
import os
import sys
import time

import bpy
import mathutils

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else os.path.join(REPO, "build", "walk_frames")
FRAMES = int(argv[1]) if len(argv) > 1 else 48
OCP_PATH = argv[2] if len(argv) > 2 else os.environ.get("CAD_OCP_PATH", "/tmp/bpy313")
DOC = os.path.join(REPO, "examples", "quadruped", "robot.json")

sys.path.insert(0, REPO)
if os.path.isdir(OCP_PATH):
    sys.path.insert(0, OCP_PATH)

started = time.perf_counter()
from cadcore.service.server import Session  # noqa: E402
from cadcore.geometry.io.tessellate import tessellate  # noqa: E402
print("WALK kernel imported into Blender in %.2fs" % (time.perf_counter() - started))

from tools.render import gait                                   # noqa: E402

# -- the poses, solved by the kernel -------------------------------------------
session = Session(autosave=False)
session.op_open(DOC)
session.op_build()

meshes, spent = [], []
for index in range(FRAMES):
    clock = time.perf_counter()
    # all eight at once: a pose is one thing, and setting the angles in turn
    # builds eight robots, seven of which are half in the last pose
    session.op_set_parameters(gait.pose(index / FRAMES))
    meshes.append(tessellate(session.body, deflection=0.35, angular=0.5))
    spent.append((time.perf_counter() - clock) * 1000)
print("WALK solved %d poses, median %.0f ms each"
      % (FRAMES, sorted(spent)[len(spent) // 2]))

# -- the scene -----------------------------------------------------------------
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)

scene = bpy.context.scene
mesh = bpy.data.meshes.new("robot")
robot = bpy.data.objects.new("robot", mesh)
scene.collection.objects.link(robot)
material = bpy.data.materials.new("robot")
material.use_nodes = True
bsdf = material.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (0.52, 0.55, 0.60, 1.0)
bsdf.inputs["Metallic"].default_value = 0.75
bsdf.inputs["Roughness"].default_value = 0.35
mesh.materials.append(material)


def put(tri) -> None:
    mesh.clear_geometry()
    mesh.from_pydata([tuple(v) for v in tri["vertices"]], [],
                     [tuple(t) for t in tri["triangles"]])
    for polygon in mesh.polygons:
        polygon.use_smooth = False
    mesh.update()


put(meshes[0])
# framed from every pose, not the first one: a leg that swings out of shot on
# frame 30 is a render nobody can use
reach = []
for tri in meshes:
    for v in tri["vertices"]:
        reach.append(v)
low = mathutils.Vector([min(v[i] for v in reach) for i in range(3)])
high = mathutils.Vector([max(v[i] for v in reach) for i in range(3)])
centre = (low + high) / 2
diagonal = (high - low).length
print("WALK swept box %.0f x %.0f x %.0f mm"
      % (high.x - low.x, high.y - low.y, high.z - low.z))

# big enough that its edge is not the horizon: at six times the robot the floor
# ran out just behind it, and what looked like a backdrop a metre away was the
# end of the ground
bpy.ops.mesh.primitive_plane_add(size=diagonal * 60,
                                 location=(centre.x, centre.y, low.z - 1.0))
floor = bpy.context.object
floor_material = bpy.data.materials.new("floor")
floor_material.use_nodes = True
floor_material.node_tree.nodes["Principled BSDF"].inputs["Base Color"]\
    .default_value = (0.17, 0.18, 0.20, 1.0)
floor_material.node_tree.nodes["Principled BSDF"].inputs["Roughness"]\
    .default_value = 0.9
floor.data.materials.append(floor_material)

# Cycles on the CPU, not EEVEE. EEVEE needs a GL context even with `-b`, and
# on a machine whose display has gone -- a headless box, a wedged WSLg session
# -- it does not fail, it *hangs*: an empty 160x160 frame never came back.
# Cycles on the CPU touches no display at all, and a robot at 24 samples is
# seconds a frame, which is the right trade for a render that finishes.
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 24
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = 960, 540
scene.view_settings.view_transform = 'AgX'
scene.view_settings.exposure = -0.25
# light enough that the horizon is a horizon rather than the edge of the lit
# world: the ground meets a sky, instead of fading into the same grey
scene.world.node_tree.nodes["Background"].inputs[0].default_value = \
    (0.29, 0.34, 0.42, 1)
scene.world.node_tree.nodes["Background"].inputs[1].default_value = 1.0

# a sun first, because the area lights below are sized to the robot and reach
# about as far: past that the ground was unlit and read as a grey wall a metre
# behind it. A sun is directional and does not fall off, so the floor stays
# floor all the way to the horizon
sky = bpy.data.lights.new("Sun", 'SUN')
sky.energy = 2.6
sky.angle = math.radians(3.0)              # a soft edge on the shadows
sun = bpy.data.objects.new("Sun", sky)
sun.rotation_euler = mathutils.Vector((math.radians(52), 0.0, math.radians(-35)))
scene.collection.objects.link(sun)

# a third of what they were: the sun above does the lifting now, and the three
# of them on top of it washed the robot out to paper
for name, offset, energy in (("Key", (0.7, -1.0, 0.9), 1.0),
                             ("Fill", (-1.1, -0.4, 0.5), 0.4),
                             ("Rim", (-0.2, 1.1, 0.8), 0.7)):
    light = bpy.data.lights.new(name, 'AREA')
    light.energy = energy * 4.0 * diagonal * diagonal
    light.size = diagonal
    lamp = bpy.data.objects.new(name, light)
    lamp.location = centre + mathutils.Vector(offset) * diagonal
    lamp.rotation_euler = (centre - lamp.location).to_track_quat('-Z', 'Y').to_euler()
    scene.collection.objects.link(lamp)

camera_data = bpy.data.cameras.new("Cam")
camera_data.lens = 50.0
# the model is in millimetres and Blender's camera clips in the same units, so
# the defaults put the far plane a few hundred millimetres past a part that is
# itself three hundred long. What that looks like is not a missing floor -- it
# is ground that stops at a line and flat world colour beyond it, which reads
# as a grey wall a metre behind the subject. Nothing here is far away by
# accident; the scene is sized from the model, so the clip is too.
camera_data.clip_start = diagonal * 0.01
camera_data.clip_end = diagonal * 500.0
camera = bpy.data.objects.new("Cam", camera_data)
scene.collection.objects.link(camera)
scene.camera = camera
# far enough back that the whole robot is in shot at every pose: a 50 mm lens
# on a 360 mm diagonal needs about twice that, and 1.15 put the camera inside
# the legs -- the first render was a close-up of two shins
# from the front quarter, on the side the head faces: the camera was behind the
# robot, which is a photograph of its back end walking away
direction = mathutils.Vector((-0.45, -0.85, 0.30)).normalized()
camera.location = centre + direction * diagonal * 2.1
camera.rotation_euler = (centre - camera.location).to_track_quat('-Z', 'Y').to_euler()

os.makedirs(OUT, exist_ok=True)
scene.render.image_settings.file_format = 'PNG'
for index, tri in enumerate(meshes):
    put(tri)
    scene.render.filepath = os.path.join(OUT, "f%04d" % (index + 1))
    bpy.ops.render.render(write_still=True)
print("WALK wrote %d frames into %s" % (FRAMES, OUT))
