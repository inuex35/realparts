"""Photograph an assembly with a material for each part, chosen by name.

    blender -b -noaudio --factory-startup -P tools/render/render_engine.py \
        -- examples/v8/engine.json build/engine

Cycles on the CPU: EEVEE hangs in a headless Blender.
"""
import math
import os
import sys

import bpy
import mathutils

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
DOC = argv[0] if argv else os.path.join(REPO, "examples", "v8", "engine.json")
OUT = argv[1] if len(argv) > 1 else os.path.join(REPO, "build", "engine")
SAMPLES = int(os.environ.get("ENGINE_SAMPLES", "300"))
TURN = float(os.environ.get("ENGINE_TURN", "228"))
TILT = float(os.environ.get("ENGINE_TILT", "0.30"))
REACH = float(os.environ.get("ENGINE_REACH", "2.45"))
#: "studio" is a catalogue photograph -- pale ground, light everywhere, every
#: part legible. "dark" is the moody one. The moody one flatters a shape and
#: hides half of it, which is the wrong trade while the shape is what is being
#: worked on: a black casting on a black ground against a black sky is one
#: silhouette, and no amount of detail added to it shows up.
LOOK = os.environ.get("ENGINE_LOOK", "studio")
OCP_PATH = os.environ.get("CAD_OCP_PATH", "/tmp/bpy313")

sys.path.insert(0, REPO)
if os.path.isdir(OCP_PATH):
    sys.path.insert(0, OCP_PATH)


def meshes(document: str) -> dict:
    """Each part as triangles, from the kernel, in the kernel's own Python.

    Blender is on 3.13 here and the kernel's OCP is built for 3.12, so this
    does not import cadcore at all: it runs the kernel as the add-on does --
    another interpreter, a document in, geometry out -- and reads the answer.
    That is the arrangement everything else in this repository already uses,
    and a render script is not the place to make an exception to it.
    """
    import json
    import subprocess

    script = """
import json, sys
sys.path.insert(0, %r)
from cadcore.service.server import Session
from cadcore.geometry.io.tessellate import tessellate
s = Session(autosave=False)
s.op_open(%r)
s.op_build()
out = {}
for name, body in sorted(s._placed_parts().items()):
    tri = tessellate(body, deflection=0.25, angular=0.3)
    out[name] = {"vertices": tri["vertices"], "triangles": tri["triangles"]}
json.dump(out, open(%r, "w", encoding="utf-8"))
""" % (REPO, document, os.path.join(REPO, "build", "engine_parts.json"))
    os.makedirs(os.path.join(REPO, "build"), exist_ok=True)
    python = os.environ.get("CADCORE_PYTHON") or sys.executable
    subprocess.run([python, "-c", script], cwd=REPO, check=True)
    return json.load(open(os.path.join(REPO, "build", "engine_parts.json"), encoding="utf-8"))

#: colour, metallic, roughness -- by the part's name.
#:
#: These are the four surfaces an engine actually has, and telling them apart
#: is most of what makes a picture of one look like an engine: sand-cast iron
#: is dark and completely matt, cast aluminium is pale and still fairly rough,
#: a painted cover is smooth and coloured, and a header is bare steel that
#: reflects the room.
LOOKS = {
    "block":    ((0.115, 0.112, 0.106), 0.75, 0.72, 0.0),
    "heads":    ((0.300, 0.305, 0.315), 0.88, 0.53, 0.0),
    "intake":   ((0.275, 0.280, 0.290), 0.88, 0.51, 0.0),
    # a painted cover is paint over metal: coloured, and then a clear film
    # over the top. Without the film it is a red plastic lid
    "covers":   ((0.235, 0.013, 0.011), 0.20, 0.22, 1.0),
    "pan":      ((0.255, 0.258, 0.262), 0.90, 0.56, 0.0),
    # a header is bare steel that has been hot: not chrome, and not grey
    "exhaust":  ((0.500, 0.480, 0.455), 1.00, 0.30, 0.0),
    "ignition": ((0.055, 0.055, 0.058), 0.30, 0.42, 0.0),
    "dress":    ((0.520, 0.522, 0.530), 0.95, 0.27, 0.0),
    # a bolt head is turned steel, darker and duller than a chromed cover and
    # brighter than the casting it is screwed into: that difference is the
    # whole of why it reads as a separate thing
    "bolts":    ((0.240, 0.238, 0.235), 1.00, 0.30, 0.0),
}
DEFAULT = ((0.35, 0.36, 0.38), 0.8, 0.4, 0.0)


#: how coarse the sand is, per part. A casting is not smooth: it comes out of
#: sand and keeps the grain of it, and every surface here being perfect is the
#: strongest single reason the picture reads as a drawing rather than a
#: photograph. Machined faces and painted covers get none of it.
#: strength, and the scale of the grain. Blender counts noise cells per unit
#: and this model is in millimetres, so a scale of 320 is three hundred cells
#: to the millimetre -- sandpaper, not sand. A casting's skin has features of
#: five to fifteen millimetres, which is a scale near a twentieth.
CAST = {"block": (0.60, 0.055), "heads": (0.45, 0.075),
        "intake": (0.40, 0.075), "pan": (0.18, 0.11),
        # paint does not fill a casting: the grain comes through it, which is
        # why a painted valve cover still reads as cast and not as moulded
        "covers": (0.22, 0.085)}


def material(name):
    colour, metal, rough, coat = LOOKS.get(name, DEFAULT)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    shader = tree.nodes["Principled BSDF"]
    shader.inputs["Base Color"].default_value = (*colour, 1.0)
    shader.inputs["Metallic"].default_value = metal
    shader.inputs["Roughness"].default_value = rough
    for label, value in (("Coat Weight", coat), ("Coat Roughness", 0.04)):
        if label in shader.inputs:
            shader.inputs[label].default_value = value

    grain = CAST.get(name)
    if grain:
        strength, scale = grain
        noise = tree.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = scale
        noise.inputs["Detail"].default_value = 8.0
        noise.inputs["Roughness"].default_value = 0.62
        bump = tree.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = strength
        bump.inputs["Distance"].default_value = 0.45
        tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
        tree.links.new(bump.outputs["Normal"], shader.inputs["Normal"])
        # and the roughness varies with it: a cast surface is not one gloss,
        # which is what a single number makes it
        wide = tree.nodes.new("ShaderNodeTexNoise")
        wide.inputs["Scale"].default_value = scale / 5.0
        mix = tree.nodes.new("ShaderNodeMapRange")
        mix.inputs["From Min"].default_value = 0.35
        mix.inputs["From Max"].default_value = 0.65
        mix.inputs["To Min"].default_value = max(rough - 0.10, 0.05)
        mix.inputs["To Max"].default_value = min(rough + 0.10, 1.0)
        tree.links.new(wide.outputs["Fac"], mix.inputs["Value"])
        tree.links.new(mix.outputs["Result"], shader.inputs["Roughness"])
    return mat


def main() -> None:
    parts = meshes(DOC if os.path.isabs(DOC) else os.path.join(REPO, DOC))
    print("ENGINE %d parts: %s" % (len(parts), ", ".join(sorted(parts))), flush=True)

    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    scene = bpy.context.scene
    reach = []
    for name, tri in sorted(parts.items()):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([tuple(v) for v in tri["vertices"]], [],
                         [tuple(t) for t in tri["triangles"]])
        mesh.update()
        mesh.materials.append(material(name))
        # smooth only where the surface really is curved: shading a machined
        # face smooth rounds off the corner a casting is read by
        mesh.shade_smooth()
        for polygon in mesh.polygons:
            polygon.use_smooth = True
        scene.collection.objects.link(bpy.data.objects.new(name, mesh))
        reach.extend(tri["vertices"])
        print("ENGINE   %-9s %6d triangles" % (name, len(tri["triangles"])), flush=True)

    low = mathutils.Vector([min(v[i] for v in reach) for i in range(3)])
    high = mathutils.Vector([max(v[i] for v in reach) for i in range(3)])
    centre = (low + high) / 2
    diagonal = (high - low).length
    print("ENGINE %.0f x %.0f x %.0f mm"
          % (high.x - low.x, high.y - low.y, high.z - low.z), flush=True)

    bpy.ops.mesh.primitive_plane_add(size=diagonal * 40,
                                     location=(centre.x, centre.y, low.z - 1.0))
    floor = bpy.context.object
    ground = bpy.data.materials.new("floor")
    ground.use_nodes = True
    shade = ground.node_tree.nodes["Principled BSDF"]
    if LOOK == "studio":
        # a sweep: pale, and matt enough that the engine casts a shadow on it
        # rather than a second engine upside down
        shade.inputs["Base Color"].default_value = (0.62, 0.62, 0.63, 1)
        shade.inputs["Roughness"].default_value = 0.55
    else:
        shade.inputs["Base Color"].default_value = (0.016, 0.017, 0.020, 1)
        shade.inputs["Roughness"].default_value = 0.22
        shade.inputs["Metallic"].default_value = 0.4
    floor.data.materials.append(ground)

    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = SAMPLES
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = 1400, 1000
    scene.view_settings.view_transform = 'AgX'
    scene.view_settings.look = ('AgX - Base Contrast' if LOOK == 'studio'
                                else 'AgX - Medium High Contrast')
    sky = (0.52, 0.54, 0.58, 1) if LOOK == "studio" else (0.030, 0.033, 0.040, 1)
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = sky

    # a big soft key from one side, a much weaker fill from the other, and two
    # hard rims. It is the rims that draw the edge of a casting against a dark
    # ground, and a picture of a metal object with no rim light is a grey blob
    # studio: two big soft boxes either side and one overhead, so that no face
    # of the engine is left with nothing on it, and one weak rim to keep the
    # silhouette off the ground. dark: a hard key and hard rims.
    LAMPS = {
        "studio": (("Key",  (-1.35, -1.15, 0.85), 14.0, 2.6),
                   ("Fill", (1.55, -0.95, 0.45), 7.0, 3.0),
                   ("Back", (0.2, 1.5, 0.7), 8.0, 2.4),
                   ("Top",  (0.05, 0.1, 2.1), 9.0, 3.4),
                   ("Rim",  (-0.7, 1.2, 0.9), 10.0, 0.5)),
        "dark":   (("Key",  (1.15, -0.95, 0.95), 22.0, 1.5),
                   ("Fill", (-1.5, -0.7, 0.35), 3.0, 2.0),
                   ("Rim",  (-0.55, 1.25, 0.75), 40.0, 0.35),
                   ("Rim2", (1.0, 1.1, 0.5), 26.0, 0.3),
                   ("Top",  (0.05, 0.15, 2.0), 6.0, 2.2)),
    }
    for name, offset, energy, size in LAMPS.get(LOOK, LAMPS["dark"]):
        light = bpy.data.lights.new(name, 'AREA')
        light.energy = energy * diagonal * diagonal
        light.size = diagonal * size
        lamp = bpy.data.objects.new(name, light)
        lamp.location = centre + mathutils.Vector(offset) * diagonal
        lamp.rotation_euler = (centre - lamp.location).to_track_quat('-Z', 'Y').to_euler()
        scene.collection.objects.link(lamp)

    camera_data = bpy.data.cameras.new("Cam")
    camera_data.lens = 85.0
    camera_data.clip_start = diagonal * 0.01
    camera_data.clip_end = diagonal * 500.0
    camera = bpy.data.objects.new("Cam", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    direction = mathutils.Vector((math.cos(math.radians(TURN)),
                                  math.sin(math.radians(TURN)), TILT)).normalized()
    camera.location = centre + direction * diagonal * REACH
    camera.rotation_euler = (centre - camera.location).to_track_quat('-Z', 'Y').to_euler()

    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = OUT
    bpy.ops.render.render(write_still=True)
    print("ENGINE wrote %s.png" % OUT, flush=True)


main()
