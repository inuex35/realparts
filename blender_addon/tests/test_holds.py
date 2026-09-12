"""Which holds are offered for what is picked on a sketch, and what they say.

The menu is built from `fitting`, so a wrong answer here is a button that
refuses when pressed, or a hold that is never reachable at all.
"""
import importlib.util
import pathlib

_path = (pathlib.Path(__file__).resolve().parents[2] / "blender_addon"
         / "viewport" / "holding.py")
_spec = importlib.util.spec_from_file_location("cad_holding", _path)
holding = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(holding)

# a square, drawn anticlockwise from the origin
GEOMETRY = {
    "points": {"a": [0.0, 0.0], "b": [20.0, 0.0], "c": [20.0, 12.0], "d": [0.0, 12.0]},
    "segments": [
        {"name": "south", "kind": "line", "start": [0.0, 0.0], "end": [20.0, 0.0]},
        {"name": "east", "kind": "line", "start": [20.0, 0.0], "end": [20.0, 12.0]},
    ],
}


def test_each_shape_of_pick_gets_the_holds_that_fit_it():
    assert holding.fitting([("line", "south")]) == ["horizontal", "vertical"]
    assert holding.fitting([("point", "a")]) == ["fix"]
    assert holding.fitting([("point", "a"), ("point", "b")]) == ["coincident", "distance"]
    assert holding.fitting([("line", "south"), ("line", "east")]) == [
        "parallel", "perpendicular", "equal_length", "tangent", "angle"]


def test_a_pick_nothing_fits_offers_nothing_and_says_what_to_pick():
    mixed = [("point", "a"), ("line", "south")]
    assert holding.fitting(mixed) == []
    assert "not both" in holding.what_it_needs(mixed)
    assert holding.fitting([("point", "a"), ("point", "b"), ("point", "c")]) == []
    assert holding.fitting([]) == []


def test_every_hold_that_can_be_offered_has_a_button_and_can_be_built():
    for kinds in holding.FITS.values():
        for kind in kinds:
            assert kind in holding.HOLDS, kind


def test_a_dimension_is_taken_at_the_size_the_sketch_already_is():
    """Adding one holds the shape; it does not snap the sketch to a round number."""
    pair = [("point", "a"), ("point", "b")]
    assert holding.build("distance", pair, GEOMETRY)["value"] == 20.0
    lines = [("line", "south"), ("line", "east")]
    assert holding.build("angle", lines, GEOMETRY)["value"] == 90.0
    # and the other way round is the other sign, not the supplement
    assert holding.build("angle", lines[::-1], GEOMETRY)["value"] == -90.0


def test_a_hold_that_does_not_fit_its_picks_is_not_guessed_at():
    assert holding.build("parallel", [("point", "a"), ("point", "b")], GEOMETRY) is None
    assert holding.build("distance", [("line", "south")], GEOMETRY) is None
    assert holding.build("no_such_hold", [("point", "a")], GEOMETRY) is None


def test_a_fix_pins_the_point_where_it_is_now():
    held = holding.build("fix", [("point", "c")], GEOMETRY)
    assert held == {"type": "fix", "point": "c", "at": [20.0, 12.0]}


def test_what_is_picked_reads_as_a_sentence():
    assert holding.what_is_picked([("point", "a")]) == "This point"
    assert holding.what_is_picked([("line", "south"), ("line", "east")]) == "These 2 lines"
