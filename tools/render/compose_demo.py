"""Six screenshots into one GIF and one MP4, with a caption per frame.

    .venv/bin/python tools/render/compose_demo.py <shots dir> <out dir>

The shots are screenshots of a Blender with the extension installed as
bought. Nothing here is drawn: the frames are what was on screen, cropped to
the viewport and the sidebar, with the words a person would have typed.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

SHOTS = sys.argv[1] if len(sys.argv) > 1 else r"/mnt/c/blender-cad-test/shots"
OUT = sys.argv[2] if len(sys.argv) > 2 else "build/demo"
os.makedirs(OUT, exist_ok=True)

FRAMES = [
    ("01_open", 'Open a part. Blender, with the add-on installed from the zip.', 2.2),
    ("02_require", 'Require: mass <= 150 g.  The row turns red: 186.6 g.', 3.2),
    ("03_simulate", 'Run the studies. Stress on the faces, SF 5.4 -- strong, and heavy.', 3.2),
    ("04_assistant_edit", 'Claude, over MCP: apply(thickness 5, wall 8). 137 g -- the row turns green.', 3.6),
    ("05_simulate_again", 'Run again: SF 2.5, still passing. The kernel checked, not the model.', 3.6),
    ("06_point", '"This face" -- the assistant reads your selection, and can point back.', 3.2),
]
# the viewport and the sidebar, without Blender's chrome; in the 2560 x 1369 shot
CROP = (440, 70, 2100, 1240)
WIDTH = 1200
BAR = 92
FONT = next((f for f in ("/mnt/c/Windows/Fonts/segoeui.ttf", "/mnt/c/Windows/Fonts/YuGothM.ttc",
                         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf") if os.path.exists(f)), None)
font = ImageFont.truetype(FONT, 30) if FONT else ImageFont.load_default()
small = ImageFont.truetype(FONT, 20) if FONT else ImageFont.load_default()

frames, durations = [], []
for index, (name, caption, seconds) in enumerate(FRAMES):
    path = os.path.join(SHOTS, name + ".png")
    shot = Image.open(path).convert("RGB").crop(CROP)
    scale = WIDTH / shot.width
    shot = shot.resize((WIDTH, round(shot.height * scale)), Image.LANCZOS)
    canvas = Image.new("RGB", (WIDTH, shot.height + BAR), (18, 18, 20))
    canvas.paste(shot, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text((22, shot.height + 18), caption, fill=(240, 240, 240), font=font)
    draw.text((WIDTH - 200, shot.height + 58), "RealParts  %d / %d" % (index + 1, len(FRAMES)),
              fill=(140, 140, 150), font=small)
    frames.append(canvas)
    durations.append(int(seconds * 1000))

gif = os.path.join(OUT, "realparts-demo.gif")
# 256 colours per frame is what a GIF has; the viewport is mostly greys, so it survives
frames[0].save(gif, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=False)
print("DEMO wrote", gif, os.path.getsize(gif), "bytes", frames[0].size)
try:
    import imageio.v3 as iio
    import numpy as np

    mp4 = os.path.join(OUT, "realparts-demo.mp4")
    fps = 10
    clips = []
    for frame, ms in zip(frames, durations):
        arr = np.asarray(frame)
        clips += [arr] * max(1, round(ms / 1000 * fps))
    iio.imwrite(mp4, np.stack(clips), fps=fps, codec="libx264", macro_block_size=1,
                output_params=["-pix_fmt", "yuv420p"])
    print("DEMO wrote", mp4, os.path.getsize(mp4), "bytes")
except Exception as exc:                                                 # noqa: BLE001
    print("DEMO no mp4:", exc)
