"""The document format, treated as a deliverable.

A file format is a contract with files -- including files this code did not
write and has never seen. Three things were missing from that contract and the
first one was quietly destroying data:

* saving an inch document **multiplied it by 25.4**. Reading converted the
  numbers to millimetres and writing said `unit: "in"` over them, so a 2 inch
  box saved and reopened was a 50.8 inch box, and 25.4 times bigger again on
  the save after that. Nothing in the suite compared a document to itself
  through a save.
* a key this version does not know was **dropped on the first save**. A
  document written by a newer build, or by a person with a field of their own,
  came back missing whatever could not be read.
* there was no version stamp at all, so a document from a future format would
  be read as this one and half-understood rather than refused.
"""
import json

import pytest
from conftest import EXAMPLES

from cadcore.model.document import FORMAT, Document
from cadcore.errors import CadError


def roundtrip(raw: dict) -> tuple:
    """Load, save, load again -- the shape of every question here."""
    first = Document.from_dict(json.loads(json.dumps(raw)), None)
    written = first.as_dict()
    return first, written, Document.from_dict(json.loads(json.dumps(written)), None)


INCHES = {"unit": "in", "parameters": {"w": 2.0},
          "features": [{"id": "b", "type": "box", "size": ["w", "w", 1.5]}],
          "result": "b"}


def test_a_document_in_inches_is_still_in_inches_after_a_save():
    """The one that was destroying data.

    Two inches on the way in, two inches on the way out -- and the millimetres
    in between are the kernel's business, not the file's.
    """
    first, written, again = roundtrip(INCHES)

    assert first.parameters["w"] == 50.8              # millimetres, inside
    assert written["unit"] == "in"
    assert written["parameters"]["w"] == 2.0          # inches, in the file
    assert written["features"][0]["size"] == ["w", "w", 1.5]
    assert again.parameters == first.parameters
    assert again.features[0].args == first.features[0].args


def test_saving_twice_writes_the_same_file():
    """Which is what "the conversions are inverses" means, checked as bytes.

    Twelve significant figures on each multiplication, because `2 * 25.4 /
    25.4` is 1.9999999999999998 in binary and nobody wants to read that in the
    diff of a file they wrote `2` in.
    """
    _, written, again = roundtrip(INCHES)
    assert json.dumps(again.as_dict(), sort_keys=True) == \
        json.dumps(written, sort_keys=True)


def test_reading_a_document_does_not_change_the_dictionary_it_was_given():
    """The conversion writes into the argument dictionaries, so it copies them.

    Reading one parsed dict twice used to convert it twice, and reading it
    once scaled the caller's data behind its back.
    """
    raw = json.loads(json.dumps(INCHES))
    Document.from_dict(raw, None)

    assert raw["features"][0]["size"] == ["w", "w", 1.5]
    assert Document.from_dict(raw, None).parameters["w"] == 50.8


def test_a_key_this_version_does_not_know_survives_a_save():
    """Dropping it was a quiet deletion of somebody's data."""
    raw = dict(INCHES, revision={"by": "somebody", "n": 3}, tags=["draft"])
    _, written, again = roundtrip(raw)

    assert written["revision"] == {"by": "somebody", "n": 3}
    assert written["tags"] == ["draft"]
    assert again.extra["revision"] == {"by": "somebody", "n": 3}


def test_a_document_says_what_format_it_is():
    _, written, _ = roundtrip(INCHES)
    assert written["format"] == FORMAT


def test_a_document_with_no_stamp_is_this_format():
    """Every document written before the stamp existed, which is all of them."""
    assert Document.from_dict({"features": [], "result": None}, None).extra == {}


def test_a_format_from_the_future_is_refused_rather_than_half_understood():
    with pytest.raises(CadError) as exc:
        Document.from_dict(dict(INCHES, format=FORMAT + 1), None)
    assert exc.value.kind == "unknown_format"
    assert str(FORMAT) in exc.value.message


@pytest.mark.parametrize("name", sorted(
    p.name for p in EXAMPLES.glob("*.json")
    # a sidecar is unsaved work, not a shipped example -- and it may be
    # swept away between collection and the test that would open it
    if not p.name.endswith(".autosave.json")))
def test_every_shipped_example_survives_a_round_trip(name):
    """Saved and reopened, each one is the document it was.

    The examples are the format's own documentation, so this is the claim that
    what they demonstrate can be written back out again.
    """
    raw = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    first, written, again = roundtrip(raw)

    assert again.parameters == first.parameters
    assert again.result == first.result
    assert [f.id for f in again.features] == [f.id for f in first.features]
    assert [f.args for f in again.features] == [f.args for f in first.features]
    assert json.dumps(again.as_dict(), sort_keys=True) == \
        json.dumps(written, sort_keys=True)
