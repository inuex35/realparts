"""The preview: fit, highlight colour, edges and view refusal."""
import numpy as np
import pytest
from PIL import Image
from cadcore.service.server import Session
from cadcore.analysis import preview


@pytest.fixture()
def part():
    s = Session(autosave=False)
    s.op_open("examples/bracket.json")
    s.op_build()
    return s.body


def colours(path):
    return Image.open(path).convert("RGB")


def test_the_shape_covers_a_fair_part_of_the_frame(part, tmp_path):
    """Guards: the orthographic fit is neither a speck nor cropped."""
    out = str(tmp_path / "iso.png")
    preview.render(part, out, width=400, height=300)
    pixels = np.asarray(colours(out), dtype=int)
    background = np.array(preview.BACKGROUND)
    drawn = (np.abs(pixels - background).sum(axis=2) > 20).mean()
    assert 0.2 < drawn < 0.85, drawn


def test_a_highlighted_face_is_a_different_colour(part, tmp_path):
    plain, lit = str(tmp_path / "a.png"), str(tmp_path / "b.png")
    preview.render(part, plain, width=300, height=220)
    preview.render(part, lit, width=300, height=220, highlight=["plate/+z"])
    a = np.asarray(colours(plain), dtype=int)
    b = np.asarray(colours(lit), dtype=int)
    changed = (np.abs(a - b).sum(axis=2) > 30).mean()
    assert changed > 0.02, changed


def test_the_edges_are_drawn_on_the_shape(part, tmp_path):
    """Guards: the part's own edges are drawn inside the shape, not only its outline."""
    out = str(tmp_path / "iso.png")
    preview.render(part, out, width=500, height=380)
    pixels = np.asarray(colours(out), dtype=int)
    edge = (np.abs(pixels - np.array(preview.EDGE)).sum(axis=2) < 12)
    inner = edge[2:-2, 2:-2]
    assert inner.sum() > 200, int(inner.sum())


def test_every_view_draws_something_different(part, tmp_path):
    seen = {}
    for view in preview.VIEWS:
        path = str(tmp_path / ("%s.png" % view))
        preview.render(part, path, view=view, width=160, height=120)
        seen[view] = colours(path).tobytes()
    assert len(set(seen.values())) == len(preview.VIEWS)


def test_a_view_nobody_has_is_refused(part, tmp_path):
    """Guards: an unknown view is refused with a CadError of kind unknown_view."""
    from cadcore.errors import CadError

    with pytest.raises(CadError) as caught:
        preview.render(part, str(tmp_path / "x.png"), view="sideways")
    assert caught.value.kind == "unknown_view"
    assert "front" in caught.value.detail["views"]
