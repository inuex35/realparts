"""Render a document -- plain, or coloured by von Mises stress.

    blender -b -noaudio --factory-startup -P tools/render/render_part.py \
        -- examples/flange.json build/flange.png [stress] [az] [el]

With ``stress`` the document's studies are run first and the field is painted on
the CAD faces; without it the part is rendered in clay.
"""
import json
import math
import os
import sys

import bpy
import mathutils

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
DOC = argv[0] if argv else os.path.join(REPO, "examples", "bracket.json")
OUT = argv[1] if len(argv) > 1 else os.path.join(REPO, "build", "part.png")
STRESS = "stress" in argv[2:]
angles = [float(a) for a in argv[2:] if a.replace("-", "").replace(".", "").isdigit()]
os.makedirs(os.path.dirname(OUT), exist_ok=True)

bpy.ops.preferences.addon_enable(module="cadcore_bridge")
props = bpy.context.scene.cadcore
bpy.ops.cadcore.open(filepath=DOC)
if STRESS:
    bpy.ops.cadcore.simulate(show_field=True)
print("RENDER", props.status)

ob = bpy.data.objects["cad_body"]
bpy.data.objects["cad_edges"].hide_render = True
mn = mathutils.Vector((1e9,) * 3)
mx = mathutils.Vector((-1e9,) * 3)
for c in ob.bound_box:
    w = ob.matrix_world @ mathutils.Vector(c)
    mn = mathutils.Vector(min(mn[i], w[i]) for i in range(3))
    mx = mathutils.Vector(max(mx[i], w[i]) for i in range(3))
centre, diag = (mn + mx) / 2, (mx - mn).length

sc = bpy.context.scene
if bpy.app.background:
    sc.render.engine = 'CYCLES'          # EEVEE hangs without a display; Cycles on the CPU does not
    sc.cycles.device = 'CPU'
    sc.cycles.samples = 64
else:
    # EEVEE was renamed in 4.2 and renamed back in 4.5, so ask what exists
    _engines = {item.identifier for item in
                bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    sc.render.engine = ('BLENDER_EEVEE' if 'BLENDER_EEVEE' in _engines
                        else 'BLENDER_EEVEE_NEXT')
    sc.eevee.taa_render_samples = 32
sc.render.resolution_x, sc.render.resolution_y = 1400, 1000
sc.render.filepath = OUT
# with a field on the mesh the colours *are* the data, so no film look on top
sc.view_settings.view_transform = 'Standard' if STRESS else 'AgX'
sc.world.node_tree.nodes["Background"].inputs[0].default_value = \
    (0.05, 0.055, 0.065, 1) if STRESS else (0.10, 0.11, 0.13, 1)

# a field carries its own contrast; clay needs the light to do that work
gain = 1.0 if STRESS else 4.0
sc.view_settings.exposure = 0.0 if STRESS else 0.8
for name, offset, energy in (("Key", (0.6, -1.0, 0.9), 6.0), ("Fill", (-0.9, -0.4, 0.3), 2.0),
                             ("Rim", (-0.2, 1.0, 0.6), 3.0)):
    light = bpy.data.lights.new(name, 'AREA')
    light.energy = energy * gain * diag * diag
    light.size = diag
    lo = bpy.data.objects.new(name, light)
    lo.location = centre + mathutils.Vector(offset) * diag
    lo.rotation_euler = (centre - lo.location).to_track_quat('-Z', 'Y').to_euler()
    sc.collection.objects.link(lo)

cam_data = bpy.data.cameras.new("Cam")
cam_data.lens = 70.0
cam_data.clip_start, cam_data.clip_end = diag * 0.01, diag * 100   # mm: the default end is too near for a part
cam = bpy.data.objects.new("Cam", cam_data)
sc.collection.objects.link(cam)
sc.camera = cam
az, el = math.radians(angles[0] if angles else -125.0), \
    math.radians(angles[1] if len(angles) > 1 else 26.0)
direction = mathutils.Vector((math.cos(az) * math.cos(el), math.sin(az) * math.cos(el),
                              math.sin(el)))
# fit the bounding box in frame rather than guessing a distance
corners = [mathutils.Vector((x, y, z)) for x in (mn.x, mx.x) for y in (mn.y, mx.y)
           for z in (mn.z, mx.z)]
hx = (cam_data.sensor_width / 2) / cam_data.lens
hy = hx / (sc.render.resolution_x / sc.render.resolution_y)
r = diag
for _ in range(8):
    cam.location = centre + direction * r
    cam.rotation_euler = (centre - cam.location).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.view_layer.update()
    inv = cam.matrix_world.inverted()
    need = 0.0
    for c in corners:
        v = inv @ c
        need = max(need, abs(v.x) / hx + (r + v.z), abs(v.y) / hy + (r + v.z))
    r = need * 1.06
cam.location = centre + direction * r
cam.rotation_euler = (centre - cam.location).to_track_quat('-Z', 'Y').to_euler()

if not STRESS:
    mat = bpy.data.materials.new("clay")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.78, 0.79, 0.82, 1.0)
    bsdf.inputs["Metallic"].default_value = 0.25
    bsdf.inputs["Roughness"].default_value = 0.36
    ob.data.materials.clear()
    ob.data.materials.append(mat)

bpy.ops.render.render(write_still=True)
if STRESS and "cad_face_stress" in ob:
    field = json.loads(ob["cad_face_stress"])
    hot = sorted(field.items(), key=lambda kv: -kv[1]["max_MPa"])[:5]
    print("RENDER hottest", json.dumps(dict(hot)))
print("RENDER wrote", OUT)
