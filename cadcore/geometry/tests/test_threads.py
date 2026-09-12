"""Threads cut as geometry, with the note alongside."""
import math

import pytest

from cadcore.geometry import kernel
from cadcore.errors import CadError


def tapped_document(threaded=True):
    return {
        "parameters": {"wall": 12},
        "features": [
            {"id": "block", "type": "box", "size": [20, 20, "wall"],
             "at": [0, 0, 0], "centred": False},
            {"id": "boss", "type": "hole", "body": "block", "face": "block/+z",
             "standard": "M6", "fit": "tapped", "at": [0, 0],
             "threaded": threaded, "clearance": 0.15}],
        "result": "boss"}


def test_a_thread_cuts_a_helix_into_a_shaft():
    """A threaded bar's volume lies between its minor-diameter core and the
    plain bar, whatever the profile constants are."""
    shaft = kernel.cylinder("shaft", 3.0, 20.0, (0, 0, 0), (0, 0, 1), centred=False)
    threaded = kernel.thread("th", shaft, "shaft/side", standard="M6")

    depth = 0.5413 * 1.0                          # M6 is 1 mm pitch
    minor = math.pi * (3.0 - depth) ** 2 * 20.0
    assert minor < kernel.volume(threaded) < kernel.volume(shaft)
    assert threaded.notes["thread"]["designation"] == "M6x1"
    assert threaded.notes["thread"]["internal"] is False


def test_a_thread_in_a_hole_goes_the_other_way():
    """The face says which side its material is on, so nobody has to."""
    block = kernel.box("b", [20, 20, 12], (0, 0, 0), centred=False)
    bore = kernel.cylinder("bore", 2.5, 40.0, (10, 10, -10), (0, 0, 1), centred=False)
    drilled = kernel.cut("d", block, bore)
    tapped = kernel.thread("tap", drilled, "bore/side", standard="M6", clearance=0.15)

    assert tapped.notes["thread"]["internal"] is True
    # material comes *out* of the wall: a tapped hole holds more than a drilled one
    assert kernel.volume(tapped) < kernel.volume(drilled)


def test_clearance_is_what_makes_a_printed_pair_fit():
    block = kernel.box("b", [20, 20, 12], (0, 0, 0), centred=False)
    bore = kernel.cylinder("bore", 2.5, 40.0, (10, 10, -10), (0, 0, 1), centred=False)
    drilled = kernel.cut("d", block, bore)
    tight = kernel.thread("t", drilled, "bore/side", standard="M6")
    loose = kernel.thread("t", drilled, "bore/side", standard="M6", clearance=0.2)
    assert kernel.volume(loose) > kernel.volume(tight)


def test_a_thread_needs_a_cylinder_and_a_pitch():
    box = kernel.box("b", [10, 10, 10], (0, 0, 0))
    with pytest.raises(CadError) as exc:
        kernel.thread("t", box, "b/+z", standard="M6")
    assert exc.value.kind == "not_a_cylinder"

    shaft = kernel.cylinder("s", 3.0, 20.0, (0, 0, 0), (0, 0, 1), centred=False)
    with pytest.raises(CadError) as exc:
        kernel.thread("t", shaft, "s/side")
    assert exc.value.kind == "bad_parameter"
    with pytest.raises(CadError) as exc:
        kernel.thread("t", shaft, "s/side", standard="M99")
    assert exc.value.kind == "unknown_fastener"


def test_a_thread_shorter_than_a_pitch_is_refused():
    washer = kernel.cylinder("w", 3.0, 0.6, (0, 0, 0), (0, 0, 1), centred=False)
    with pytest.raises(CadError) as exc:
        kernel.thread("t", washer, "w/side", standard="M6")
    assert exc.value.kind == "bad_parameter"


def test_a_hole_can_be_asked_for_threaded(open_document):
    """The document says `threaded`, and the note and the helix both appear."""
    out_plain = open_document(tapped_document(threaded=False), "plain.json").op_build()
    out_threaded = open_document(tapped_document(True), "tapped.json").op_build()

    assert out_threaded["volume_mm3"] < out_plain["volume_mm3"]
    assert out_threaded["notes"]["thread"]["designation"] == "M6x1"
    assert out_threaded["faces"] > out_plain["faces"]


def test_the_thread_survives_an_edit(open_document):
    session = open_document(tapped_document(True))
    first = session.op_build()
    deeper = session.op_set_parameter("wall", 18)
    assert deeper["volume_mm3"] > first["volume_mm3"]
    assert deeper["notes"]["thread"]["length_mm"] == pytest.approx(18.0)


def test_the_same_thread_code_works_at_very_different_scales():
    """An M6 on a 6 mm bar and a 3 mm pitch on a 24 mm bottle neck. The thread
    is made by turning the depth off and fusing the ridge back (two booleans),
    which holds at both scales where a helical groove cut did not."""
    from cadcore.model.document import Document
    from cadcore.evaluation.graph import Evaluator

    evaluator = Evaluator(Document.load("examples/bottle.json"))
    plain = evaluator.build("hollow")
    threaded = evaluator.build("screw")

    # the neck lost its relief and got its thread back: less than the blank,
    # and not by more than the ring that was turned off it
    turned = math.pi * (12.0 ** 2 - (12.0 - 0.5413 * 3 + 0.15) ** 2) * 22
    assert 0 < kernel.volume(plain) - kernel.volume(threaded) < turned
    assert threaded.notes["thread"]["designation"] == "24x3"


def test_a_thread_deeper_than_the_wall_is_the_envelope_s_job(open_example):
    """A groove deeper than the wall still adds about a thread's volume, so
    the kernel's size check cannot catch it; the bottle's envelope rule
    ``0.5413 * pitch < wall - 0.6`` reports it against the parameter."""
    session = open_example("bottle.json")

    out = session.op_set_parameter("pitch", 4.0)
    assert out["in_envelope"] is False
    assert "pitch" in out["envelope_note"]

    assert session.op_set_parameter("pitch", 3.0)["in_envelope"] is True
