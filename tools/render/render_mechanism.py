"""Draw a mechanism scene and render the cycle as a video.

    python -m cadcore.mechanism.scene four_bar build/four_bar.json 72
    blender -b -noaudio --factory-startup -P tools/render/render_mechanism.py \
        -- build/four_bar.json build/four_bar.mp4 [az] [el]

Reads the file the kernel wrote and nothing else: OCCT is not in Blender's
Python, and the split is the same one the add-on uses -- the modelling side
makes the parts, this side only draws them.
"""
import json
import math
import os
import sys

import bpy
import mathutils

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
SCENE = argv[0] if argv else "build/four_bar.json"
OUT = argv[1] if len(argv) > 1 else os.path.splitext(SCENE)[0] + "_frames"
AZ = float(argv[2]) if len(argv) > 2 else -90.0
EL = float(argv[3]) if len(argv) > 3 else 58.0

with open(SCENE, encoding="utf-8") as handle:
    scene_data = json.load(handle)
reach = float(scene_data["reach_mm"])
os.makedirs(OUT, exist_ok=True)
print("MECH", scene_data["mechanism"], len(scene_data["parts"]), "parts,",
      len(scene_data["motion"]), "frames")

for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)

scene = bpy.context.scene
objects = {}
for part in scene_data["parts"]:
    mesh = bpy.data.meshes.new(part["name"])
    mesh.from_pydata([tuple(v) for v in part["vertices"]], [],
                     [tuple(t) for t in part["triangles"]])
    mesh.update()
    for polygon in mesh.polygons:
        polygon.use_smooth = False           # a machined part has edges
    ob = bpy.data.objects.new(part["name"], mesh)
    scene.collection.objects.link(ob)
    material = bpy.data.materials.new(part["name"])
    material.use_nodes = True
    bsdf = material.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*part["colour"], 1.0)
    bsdf.inputs["Metallic"].default_value = 0.35
    bsdf.inputs["Roughness"].default_value = 0.38
    mesh.materials.append(material)
    objects[part["name"]] = ob

# -- the motion ----------------------------------------------------------------
# a cycle runs at a constant speed, so the keys are linear, set on the way in
# (where the curves live differs between Blender versions)
try:
    bpy.context.preferences.edit.keyframe_new_interpolation_type = 'LINEAR'
except (AttributeError, TypeError):                              # noqa: BLE001
    pass

motion = scene_data["motion"]
scene.frame_start, scene.frame_end = 1, len(motion)
for index, pose in enumerate(motion, start=1):
    for name, (x, y, z, turn) in pose.items():
        ob = objects[name]
        ob.location = (x, y, z)
        ob.rotation_euler = (0.0, 0.0, turn)
        ob.keyframe_insert("location", frame=index)
        ob.keyframe_insert("rotation_euler", frame=index)

# -- how much room the whole cycle needs ---------------------------------------
# framed from where the parts actually go over the whole cycle rather than from
# a radius: a mechanism is mostly off to one side of its own origin, and a
# camera aimed at the origin puts half the picture on empty floor
low = mathutils.Vector((1e9, 1e9, 1e9))
high = mathutils.Vector((-1e9, -1e9, -1e9))
for index in range(1, len(motion) + 1):
    scene.frame_set(index)
    bpy.context.view_layer.update()
    for ob in objects.values():
        for corner in ob.bound_box:
            world = ob.matrix_world @ mathutils.Vector(corner)
            low = mathutils.Vector(min(low[i], world[i]) for i in range(3))
            high = mathutils.Vector(max(high[i], world[i]) for i in range(3))
scene.frame_set(1)
centre = (low + high) / 2
diagonal = (high - low).length
print("MECH swept box %.0f x %.0f x %.0f mm" % tuple(high - low))

# -- ground, light, camera -----------------------------------------------------
bpy.ops.mesh.primitive_plane_add(size=diagonal * 6,
                                 location=(centre.x, centre.y, low.z - diagonal * 0.12))
floor = bpy.context.object
floor_material = bpy.data.materials.new("floor")
floor_material.use_nodes = True
floor_bsdf = floor_material.node_tree.nodes["Principled BSDF"]
floor_bsdf.inputs["Base Color"].default_value = (0.15, 0.16, 0.18, 1.0)
floor_bsdf.inputs["Roughness"].default_value = 0.9
floor.data.materials.append(floor_material)

engines = {item.identifier for item in
           bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
scene.render.engine = ('BLENDER_EEVEE' if 'BLENDER_EEVEE' in engines
                       else 'BLENDER_EEVEE_NEXT')
scene.eevee.taa_render_samples = 24
scene.render.resolution_x, scene.render.resolution_y = 1280, 800
scene.render.fps = 30
scene.view_settings.view_transform = 'AgX'
scene.view_settings.exposure = 0.7
scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.09, 0.10, 0.12, 1)

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
az, el = math.radians(AZ), math.radians(EL)
direction = mathutils.Vector((math.cos(az) * math.cos(el),
                              math.sin(az) * math.cos(el), math.sin(el)))
# back off until the swept box fits, rather than guessing a distance
corners = [mathutils.Vector((x, y, z)) for x in (low.x, high.x)
           for y in (low.y, high.y) for z in (low.z, high.z)]
half_x = (cam_data.sensor_width / 2) / cam_data.lens
half_y = half_x / (scene.render.resolution_x / scene.render.resolution_y)
distance = diagonal
for _ in range(8):
    cam.location = centre + direction * distance
    cam.rotation_euler = (centre - cam.location).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.view_layer.update()
    inverse = cam.matrix_world.inverted()
    need = 0.0
    for corner in corners:
        seen = inverse @ corner
        need = max(need, abs(seen.x) / half_x + (distance + seen.z),
                   abs(seen.y) / half_y + (distance + seen.z))
    distance = need * 1.08
cam.location = centre + direction * distance
cam.rotation_euler = (centre - cam.location).to_track_quat('-Z', 'Y').to_euler()

# PNG frames, and something else stitches them: this Blender is built without
# FFMPEG, so asking for a video here fails at the last step of a long render
scene.render.image_settings.file_format = 'PNG'
scene.render.filepath = os.path.join(OUT, "f")
bpy.ops.render.render(animation=True)
print("MECH wrote", len(motion), "frames into", OUT)
