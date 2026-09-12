"""Render a shipped example as the picture the sidebar shows.

    ./.venv/bin/python tools/render/gallery.py examples/cup.json
    ./.venv/bin/python tools/render/gallery.py --all

Writes ``examples/previews/<name>.png`` at 256 x 192, through the kernel's own
preview renderer. Not Blender: the pictures ship in the wheel, and one made a
different way does not sit beside the rest.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
sys.path.insert(0, REPO)

from cadcore.analysis import preview                                  # noqa: E402
from cadcore.evaluation.graph import Evaluator                        # noqa: E402
from cadcore.model.document import Document                           # noqa: E402

WIDTH, HEIGHT = 256, 192


def shoot(document: str) -> str:
    """Draw one example and return the path written."""
    name = os.path.splitext(os.path.basename(document))[0]
    out = os.path.join(REPO, "examples", "previews", name + ".png")
    body = Evaluator(Document.load(document)).build()
    preview.render(body, out, width=WIDTH, height=HEIGHT)
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--all"]:
        import glob

        # only the ones that already have a picture: the rest are not offered
        wanted = sorted(os.path.basename(p)[:-4]
                        for p in glob.glob(os.path.join(REPO, "examples", "previews", "*.png")))
        args = [os.path.join(REPO, "examples", n + ".json") for n in wanted]
    if not args:
        raise SystemExit(__doc__)
    for document in args:
        print("GALLERY wrote", shoot(document))
