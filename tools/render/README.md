# tools/render

Scripts that run inside `blender -b` (headless) and produce the images and
animations in `docs/images/`. None of them is imported by the kernel or the
add-on. Each is run as `blender -b -P tools/render/<script>.py -- <args>`;
the script's docstring gives its arguments.

| script | produces |
|---|---|
| `render_part.py` | one document, plain or coloured by von Mises stress |
| `render_steps.py` | one frame per feature: how a part was built |
| `render_engine.py` | an assembly with a material per part |
| `render_mechanism.py`, `render_motion.py`, `render_morph.py` | a mechanism running; a parameter swept over time; a link changing length while running |
| `render_quadruped.py`, `gait.py` | the walker; the foot path turned into joint angles |
| `render_aircraft.py`, `render_car.py`, `render_spot.py` | turntables of larger demo models |
| `shoot_panel.py`, `shoot_states.py`, `show_panel.py` | screenshots of the Blender sidebar, in each of its states; a text dump of the sidebar for checking without a display |
| `import_cadcore.py` | loads a kernel tessellation into Blender keeping the face names (shared by the scripts above) |
| `legend.py`, `stitch.py`, `compose_demo.py` | the stress colour bar; frames to GIF or MP4; several screenshots into one captioned demo |

Two Blender behaviours these scripts work around: the default camera clip
end is too short for models measured in millimetres, and EEVEE hangs rather
than fails when rendering without a display. `render_part.py` and
`render_engine.py` set the clip range and use Cycles headless; the
mechanism and morph renders inherit the scene the kernel wrote.
