"""Every example still measures what it measured.

The record in golden.json (see golden.py) is compared, not asserted right: a
failure here means "this changed", and someone decides whether that was the
point. After a deliberate change:

    python cadcore/evaluation/tests/golden.py --write

Assertions are separate rather than one dictionary comparison so the failure
says which measurement moved.
"""
import pytest

from . import golden

RECORDED = golden.load()
DOCUMENTS = sorted(RECORDED)


def test_the_record_covers_every_example():
    """Guards: a new example must be recorded too."""
    assert golden.documents() == DOCUMENTS, (
        "an example was added or removed -- run `python cadcore/evaluation/tests/golden.py --write`")


@pytest.fixture(scope="module")
def measured():
    """Built once each, because a rebuild is the expensive part here."""
    return {path: golden.measure(path) for path in DOCUMENTS}


@pytest.mark.parametrize("path", DOCUMENTS)
def test_the_shape_is_the_shape_it_was(path, measured):
    """Size and position: kind, volume, area and bounding box."""
    was, now = RECORDED[path], measured[path]

    assert now["kind"] == was["kind"], path
    assert now["volume_mm3"] == pytest.approx(was["volume_mm3"], rel=1e-9), path
    assert now["area_mm2"] == pytest.approx(was["area_mm2"], rel=1e-9), path
    for i, (before, after) in enumerate(zip(was["bounds_mm"], now["bounds_mm"])):
        assert after == pytest.approx(before, abs=1e-3), \
            "%s: bounds[%d] moved %.3f mm" % (path, i, after - before)


@pytest.mark.parametrize("path", DOCUMENTS)
def test_the_faces_are_the_faces_they_were(path, measured):
    """Face and edge counts, and the face names compared as sets so the
    failure says which appeared and which vanished."""
    was, now = RECORDED[path], measured[path]

    appeared = sorted(set(now["face_names"]) - set(was["face_names"]))
    vanished = sorted(set(was["face_names"]) - set(now["face_names"]))
    assert not (appeared or vanished), \
        "%s: %d new %s, %d gone %s" % (path, len(appeared), appeared[:6],
                                       len(vanished), vanished[:6])
    assert now["faces"] == was["faces"], path
    assert now["edges"] == was["edges"], path


@pytest.mark.parametrize("path", [p for p in DOCUMENTS if RECORDED[p].get("drawing")])
def test_the_drawing_is_the_drawing_it_was(path, measured):
    """Each view's bounds and visible, hidden and hatch counts, which catch a
    view taken from the wrong side."""
    was, now = RECORDED[path]["drawing"], measured[path]["drawing"]

    assert sorted(now) == sorted(was), path
    for view in sorted(was):
        for count in ("visible", "hidden", "hatch"):
            assert now[view][count] == was[view][count], \
                "%s %s view: %d %s lines, was %d" % (path, view, now[view][count],
                                                     count, was[view][count])
        for i, (before, after) in enumerate(zip(was[view]["bounds"],
                                                now[view]["bounds"])):
            assert after == pytest.approx(before, abs=1e-3), \
                "%s %s view: bounds[%d] moved" % (path, view, i)
