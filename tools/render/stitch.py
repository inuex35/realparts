"""Turn a folder of rendered frames into one animation.

    python tools/render/stitch.py build/four_bar_frames build/four_bar.gif [fps]

Blender here is built without FFMPEG, so the render writes PNGs and this puts
them together. A GIF because it plays anywhere without a codec, which is what
an animation of a mechanism is for: showing somebody that it goes round.
"""
from __future__ import annotations

import os
import sys

from PIL import Image


def stitch(folder: str, out: str, fps: int = 25, width: int = 900) -> dict:
    frames = sorted(f for f in os.listdir(folder) if f.lower().endswith(".png"))
    if not frames:
        raise SystemExit(f"no frames in {folder}")
    images = []
    for name in frames:
        image = Image.open(os.path.join(folder, name)).convert("RGB")
        if width and image.width != width:
            image = image.resize((width, round(image.height * width / image.width)),
                                 Image.LANCZOS)
        # a GIF holds 256 colours, and a shaded render dithered to them crawls
        # between frames; one palette for the whole clip keeps it still
        images.append(image)
    palette = images[0].quantize(colors=192, method=Image.MEDIANCUT)
    flat = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in images]
    # the last frame is the first one again -- a closed cycle -- so it is dropped
    if len(flat) > 1:
        flat = flat[:-1]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    flat[0].save(out, save_all=True, append_images=flat[1:],
                 duration=round(1000 / fps), loop=0, optimize=True)
    return {"frames": len(flat), "path": out, "bytes": os.path.getsize(out),
            "size": flat[0].size}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    folder = argv[0] if argv else "build/four_bar_frames"
    out = argv[1] if len(argv) > 1 else folder.rstrip("/") + ".gif"
    fps = int(argv[2]) if len(argv) > 2 else 25
    info = stitch(folder, out, fps)
    print(f"{info['frames']} frames at {info['size'][0]}x{info['size'][1]} "
          f"-> {info['path']} ({info['bytes'] / 1024:.0f} kB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
