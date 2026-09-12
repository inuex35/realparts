"""Every question about a declaration goes through the one walk in
`features/schema.py`, so a reference is found wherever it is written.

Guards: the check that decides whether a feature may be removed and the
rewrite that performs the removal must see the same nested references
(`on: {"body": ...}`), or removal reports `unknown_feature` for the very
feature being removed.
"""
import pytest

from cadcore import features
from cadcore.errors import CadError
from ..declare import schema

NESTED = {
    "meta": {"name": "a reference that is not a top level argument"},
    "parameters": {},
    "features": [
        {"id": "blank", "type": "box", "size": [60, 40, 12],
         "at": [0, 0, 0], "centred": True},
        {"id": "round0", "type": "fillet", "body": "blank", "radius": 2.0,
         "edges": {"of_face": "blank/+z"}},
        {"id": "top", "type": "sketch", "on": {"body": "round0", "face": "blank/+z"},
         "points": {"a": [-10, -8], "b": [10, -8], "c": [10, 8], "d": [-10, 8]},
         "lines": {"s": ["a", "b"], "e": ["b", "c"],
                   "n": ["c", "d"], "w": ["d", "a"]},
         "allow_underconstrained": True},
        {"id": "boss", "type": "extrude", "sketch": "top", "distance": 8.0},
        {"id": "whole", "type": "fuse", "target": "round0", "tool": "boss"},
    ],
    "result": "whole",
}


def test_a_nested_reference_is_found_where_it_is_written():
    features.load()
    sketch = features.handler("sketch")
    args = {"on": {"body": "round0", "face": "blank/+z"}}
    assert sketch.references(args) == ["round0"]
    assert sketch.reference_paths(args) == [["on", "body"]]


def test_a_reference_inside_a_list_is_found_by_position():
    features.load()
    assemble = features.handler("assemble")
    args = {"bodies": ["a", "b", "c"], "ground": "a"}
    paths = assemble.reference_paths(args)
    assert ["bodies", 0] in paths and ["bodies", 2] in paths
    assert ["ground"] in paths


def test_what_may_be_removed_and_what_is_rewritten_are_the_same_walk(open_document):
    """Guards: the removal check and the removal rewrite see the same nested references."""
    session = open_document(NESTED)
    before = session.op_build()["faces"]
    out = session.op_remove_feature("round0")
    assert out["removed"] == ["round0"]
    assert session.doc.feature("top").args["on"]["body"] == "blank"
    assert session.doc.feature("whole").args["target"] == "blank"
    assert out["faces"] < before, "the fillet is gone, so there are fewer faces"


TWO_CHAINS = {
    "meta": {"name": "somewhere a chain feature can actually move to"},
    "parameters": {},
    "features": [
        {"id": "a", "type": "box", "size": [60, 40, 12],
         "at": [0, 0, 0], "centred": True},
        {"id": "b", "type": "box", "size": [20, 20, 20],
         "at": [200, 0, 0], "centred": True},
        {"id": "fa", "type": "fillet", "body": "a", "radius": 2.0,
         "edges": {"of_face": "a/+z"}},
        {"id": "top", "type": "sketch", "on": {"body": "fa", "face": "a/+z"},
         "points": {"p": [-10, -8], "q": [10, -8], "r": [10, 8], "s": [-10, 8]},
         "lines": {"s1": ["p", "q"], "s2": ["q", "r"],
                   "s3": ["r", "s"], "s4": ["s", "p"]},
         "allow_underconstrained": True},
        {"id": "boss", "type": "extrude", "sketch": "top", "distance": 8.0},
        {"id": "whole", "type": "fuse", "target": "fa", "tool": "boss"},
    ],
    "result": "whole",
}


def test_moving_a_feature_re_points_its_nested_references_too(open_document):
    """Guards: unlinking `fa` re-points a sketch's nested `on: {"body": ...}`
    to `a`, not only the top level arguments."""
    session = open_document(TWO_CHAINS)
    session.op_move_feature("fa", after="b")
    assert session.doc.feature("top").args["on"]["body"] == "a"
    assert session.doc.feature("whole").args["target"] == "a"
    assert session.doc.feature("fa").args["body"] == "b"


def test_removing_a_feature_still_refuses_when_there_is_nothing_to_hand_through(
        open_document):
    """Guards: a sketch an extrude still consumes cannot be removed, since
    nothing can stand in for it."""
    session = open_document(NESTED)
    with pytest.raises(CadError) as caught:
        session.op_remove_feature("top")
    assert caught.value.kind == "feature_in_use"


def test_every_question_about_a_declaration_goes_through_one_walk():
    """Guards the shape of schema.py: exactly one recursive `walk`, inside `leaves`."""
    import ast
    import inspect

    source = inspect.getsource(schema)
    tree = ast.parse(source)
    walkers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "walk":
            walkers.append(node)
    assert len(walkers) == 1, (
        "%d recursive argument walks in schema.py; there should be the one "
        "inside `leaves`" % len(walkers))


def test_a_one_of_describes_a_value_one_way():
    """Guards: a `one_of` value (`thickness` is a number or a spec) is described
    once, by the option that fits."""
    features.load()
    shell = features.handler("shell")
    plain = shell.lengths({"thickness": 2.0})
    assert plain == [["thickness"]]
    detailed = shell.lengths({"thickness": {"default": 2.0,
                                            "faces": {"cup/+z": 3.0}}})
    assert ["thickness"] not in detailed
    assert ["thickness", "default"] in detailed
    assert ["thickness", "faces", "cup/+z"] in detailed


def test_read_at_and_write_at_speak_the_paths_the_walk_hands_out():
    args = {"on": {"body": "a"}, "bodies": ["x", "y"]}
    assert schema.read_at(args, ["on", "body"]) == "a"
    assert schema.read_at(args, ["bodies", 1]) == "y"
    schema.write_at(args, ["bodies", 1], "z")
    assert args["bodies"] == ["x", "z"]
