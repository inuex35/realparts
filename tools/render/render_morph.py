"""A mechanism running while one of its links changes length: rebuilt every frame.

    blender -b -noaudio --factory-startup -P tools/render/render_morph.py \
        -- build/morph_frames [frames] [ocp_path]

Runs the kernel inside Blender's own Python (3.13 has the wheels); a crash
takes the session, which is why ordinary modelling talks to a child process.
"""
import os
import sys
import time

import bpy
import mathutils

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else os.path.join(REPO, "build", "morph_frames")
FRAMES = int(argv[1]) if len(argv) > 1 else 72
OCP_PATH = argv[2] if len(argv) > 2 else os.environ.get("CAD_OCP_PATH", "/tmp/bpy313")

sys.path.insert(0, REPO)
if os.path.isdir(OCP_PATH):
    sys.path.insert(0, OCP_PATH)

started = time.perf_counter()
from cadcore.model.document import Document  # noqa: E402
from cadcore.evaluation.graph import Evaluator  # noqa: E402
from cadcore.geometry.io.tessellate import tessellate  # noqa: E402
from cadcore.mechanism.library import four_bar  # noqa: E402
print("MORPH kernel imported into Blender in %.2fs" % (time.perf_counter() - started))

LINK = Document.load(os.path.join(REPO, "examples/mechanism/link.json")).as_dict()
SHORT, LONG = 90.0, 150.0
os.makedirs(OUT, exist_ok=True)


def rocker_length(index: int) -> float:
    """Out and back over the cycle, so the animation loops."""
    phase = index / FRAMES
    return SHORT + (LONG - SHORT) * (1 - abs(2 * phase - 1))


def mesh_for(name: str, centres: float) -> dict:
    spec = dict(LINK)
    spec["parameters"] = {**LINK["parameters"], "centres": centres}
    body = Evaluator(Document.from_dict(spec)).build()
    return tessellate(body, deflection=0.08, angular=0.35)


for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)

COLOURS = {"frame": (0.42, 0.40, 0.36), "crank": (0.86, 0.44, 0.18),
           "coupler": (0.58, 0.62, 0.70), "rocker": (0.26, 0.54, 0.74)}
scene = bpy.context.scene
objects = {}
for name, colour in COLOURS.items():
    mesh = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(ob)
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    bsdf = material.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*colour, 1.0)
    bsdf.inputs["Metallic"].default_value = 0.35
    bsdf.inputs["Roughness"].default_value = 0.38
    mesh.materials.append(material)
    objects[name] = ob

# the three bars that never change are built once; only the rocker is rebuilt
fixed = {name: mesh_for(name, length) for name, length in
         (("frame", 120.0), ("crank", 40.0), ("coupler", 110.0))}


def put(ob, tri, place):
    mesh = ob.data
    mesh.clear_geometry()
    mesh.from_pydata([tuple(v) for v in tri["vertices"]], [],
                     [tuple(t) for t in tri["triangles"]])
    for polygon in mesh.polygons:
        polygon.use_smooth = False
    mesh.update()
    ob.location = (place.x, place.y, place.z)
    ob.rotation_euler = (0.0, 0.0, place.turn)


# -- the sweep -----------------------------------------------------------------
# solved and rebuilt frame by frame rather than keyframed, because the shape is
# different in every one: there is nothing to interpolate between
poses, meshes, rebuilds, reach = [], [], [], []
seed = None
for index in range(FRAMES):
    length = rocker_length(index)
    rig = four_bar(rocker=length)
    angle = 720.0 * index / FRAMES               # two turns of the crank
    joints = rig.solve(angle, seed)
    seed = {k: list(v) for k, v in joints.items()}
    started = time.perf_counter()
    tri = mesh_for("rocker", length)
    rebuilds.append((time.perf_counter() - started) * 1000)
    poses.append(rig.place(joints))
    meshes.append(tri)
    reach.extend(joints.values())
print("MORPH solved and rebuilt %d frames, rocker %.0f-%.0f mm, "
      "median rebuild %.1f ms"
      % (FRAMES, SHORT, LONG, sorted(rebuilds)[len(rebuilds) // 2]))

# -- ground, light, camera -----------------------------------------------------
# framed from where the joints actually went, plus the width of a bar: a
# hand-written box was wrong the moment the rocker grew past it
pad = LINK["parameters"]["width"]
low = mathutils.Vector((min(x for x, _ in reach) - pad,
                        min(y for _, y in reach) - pad, -20.0))
high = mathutils.Vector((max(x for x, _ in reach) + pad,
                         max(y for _, y in reach) + pad, 20.0))
centre = (low + high) / 2
diagonal = (high - low).length
print("MORPH swept box %.0f x %.0f mm" % (high.x - low.x, high.y - low.y))

bpy.ops.mesh.primitive_plane_add(size=diagonal * 6,
                                 location=(centre.x, centre.y, -diagonal * 0.10))
floor = bpy.context.object
floor_material = bpy.data.materials.new("floor")
floor_material.use_nodes = True
floor_material.node_tree.nodes["Principled BSDF"].inputs["Base Color"]\
    .default_value = (0.15, 0.16, 0.18, 1.0)
floor_material.node_tree.nodes["Principled BSDF"].inputs["Roughness"]\
    .default_value = 0.9
floor.data.materials.append(floor_material)

engines = {item.identifier for item in
           bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
scene.render.engine = ('BLENDER_EEVEE' if 'BLENDER_EEVEE' in engines
                       else 'BLENDER_EEVEE_NEXT')
scene.eevee.taa_render_samples = 24
scene.render.resolution_x, scene.render.resolution_y = 1280, 800
scene.view_settings.view_transform = 'AgX'
scene.view_settings.exposure = 0.7
scene.world.node_tree.nodes["Background"].inputs[0].default_value = \
    (0.09, 0.10, 0.12, 1)

for name, offset, energy in (("Key", (0.5, -0.9, 1.1), 5.0),
                             ("Fill", (-1.0, -0.5, 0.4), 1.8),
                             ("Rim", (-0.3, 1.0, 0.7), 2.6)):
    light = bpy.data.lights.new(name, 'AREA')
    light.energy = energy * 4.0 * diagonal * diagonal
    light.size = diagonal
    lo = bpy.data.objects.new(name, light)
    lo.location = centre + mathutils.Vector(offset) * diagonal
    lo.rotation_euler = (centre - lo.location).to_track_quat('-Z', 'Y').to_euler()
    scene.collection.objects.link(lo)

cam_data = bpy.data.cameras.new("Cam")
cam_data.lens = 55.0
# the model is in millimetres and Blender's camera clips in the same units, so
# the defaults put the far plane a few hundred millimetres past a part that is
# itself three hundred long. What that looks like is not a missing floor -- it
# is ground that stops at a line and flat world colour beyond it, which reads
# as a grey wall a metre behind the subject. Nothing here is far away by
# accident; the scene is sized from the model, so the clip is too.
cam_data.clip_start = diagonal * 0.01
cam_data.clip_end = diagonal * 500.0
cam = bpy.data.objects.new("Cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
direction = mathutils.Vector((0.0, -0.42, 0.91)).normalized()
cam.location = centre + direction * diagonal * 1.05
cam.rotation_euler = (centre - cam.location).to_track_quat('-Z', 'Y').to_euler()

scene.render.image_settings.file_format = 'PNG'
for index in range(FRAMES):
    pose = poses[index]
    put(objects["rocker"], meshes[index], pose["rocker"])
    for name in ("frame", "crank", "coupler"):
        put(objects[name], fixed[name], pose[name])
    scene.render.filepath = os.path.join(OUT, "f%04d" % (index + 1))
    bpy.ops.render.render(write_still=True)
print("MORPH wrote %d frames into %s" % (FRAMES, OUT))
