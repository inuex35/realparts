"""What an assistant can do with this kernel through the session and its tools.

Guards: a part can be made from nothing; tool schemas carry unions and enums
read from the code; the faces of a part come back in one answer; a batch of
edits is one transaction.
"""
import json

import pytest

from cadcore.errors import CadError
from cadcore.service.server import Session

FOUR_HOLES = [
    {"op": "add_feature", "type": "box",
     "args": {"size": [60, 40, 10], "at": [0, 0, 0], "centred": False}},
]
for _i, (_x, _y) in enumerate(((10, 10), (50, 10), (10, 30), (50, 30))):
    FOUR_HOLES.append({"op": "add_feature", "type": "cylinder",
                       "feature_id": "d%d" % _i,
                       "args": {"radius": 3, "height": 30,
                                "at": [_x, _y, -5], "centred": False}})
    FOUR_HOLES.append({"op": "add_feature", "type": "cut",
                       "feature_id": "c%d" % _i,
                       "args": {"target": "box1" if _i == 0 else "c%d" % (_i - 1),
                                "tool": "d%d" % _i}})


def test_a_part_can_be_made_from_nothing():
    session = Session(autosave=False)
    session.op_new_document(unit="mm", name="from nothing")
    session.op_add_feature(type="box", args={"size": [60, 40, 10],
                                             "at": [0, 0, 0], "centred": False})
    session.op_add_feature(type="cylinder", args={"radius": 5, "height": 30,
                                                  "at": [30, 20, -5],
                                                  "centred": False})
    out = session.op_add_feature(type="cut", args={"target": "box1",
                                                   "tool": "cylinder1"})
    assert out["faces"] == 7
    assert "cylinder1/side" in out["face_names"], "the bore has a name"


def test_every_feature_type_in_the_catalogue_can_be_added():
    """Guards: the catalogue and what `add_feature` reaches are the same list."""
    from cadcore import features

    features.load()
    session = Session(autosave=False)
    session.op_new_document()
    reachable = {name[4:] for name in dir(session) if name.startswith("op_add_")}
    assert set(features.types()) - reachable, "otherwise this test proves nothing"
    # the generic operation refuses an unknown argument by name
    with pytest.raises(CadError) as caught:
        session.op_add_feature(type="box", args={"size": [1, 1, 1], "nonsense": 3})
    assert caught.value.kind == "unknown_argument"
    assert "nonsense" in caught.value.message


def test_a_document_is_data_that_goes_out_and_comes_back():
    here = Session(autosave=False)
    here.op_new_document()
    made = here.op_apply(ops=FOUR_HOLES)
    document = here.op_document_json()["document"]

    there = Session(autosave=False)
    back = there.op_load_json(document=document)
    assert (back["faces"], round(back["volume_mm3"], 3)) == \
           (made["faces"], round(made["volume_mm3"], 3))


def test_a_batch_is_one_edit():
    session = Session(autosave=False)
    session.op_new_document()
    out = session.op_apply(ops=FOUR_HOLES)
    assert out["applied"] == ["add_feature"] * len(FOUR_HOLES)
    assert len(session.undone) == 1, "nine steps, one thing to undo"


def test_a_batch_that_is_refused_leaves_nothing_behind():
    session = Session(autosave=False)
    session.op_new_document()
    with pytest.raises(CadError) as caught:
        session.op_apply(ops=FOUR_HOLES + [
            {"op": "add_feature", "type": "cut",
             "args": {"target": "c3", "tool": "no_such_thing"}}])
    assert caught.value.detail["step"] == len(FOUR_HOLES)
    assert session.doc.features == [], "the first nine were applied and kept"
    assert len(session.undone) == 0


def test_the_faces_of_a_part_come_back_in_one_answer():
    session = Session(autosave=False)
    session.op_new_document()
    session.op_apply(ops=FOUR_HOLES)
    faces = session.op_describe_faces()["faces"]
    assert len(faces) == 10
    top = [f for f in faces if f.get("normal") == [0.0, 0.0, 1.0]]
    assert top and top[0]["area_mm2"] > 0 and "centre" in top[0]


def test_a_face_can_be_looked_for_rather_than_looked_through():
    session = Session(autosave=False)
    session.op_new_document()
    session.op_apply(ops=FOUR_HOLES)
    bores = session.op_find_faces(query={"shape": "cylinder"})["faces"]
    assert len(bores) == 4, bores
    big = session.op_find_faces(query={"parallel": "z", "larger_than": 1000})["faces"]
    assert big, "the top and the bottom"


def test_a_face_query_nobody_understands_is_refused_by_name():
    session = Session(autosave=False)
    session.op_new_document()
    session.op_apply(ops=FOUR_HOLES[:1])
    with pytest.raises(CadError) as caught:
        session.op_find_faces(query={"of_fce": "box1/+z"})
    assert caught.value.kind == "bad_query"
    assert "of_face" in caught.value.detail["understood"]


def test_a_reply_can_carry_what_changed_instead_of_everything():
    session = Session(autosave=False)
    session.op_open("examples/quadruped/robot.json")
    whole = len(json.dumps(session.op_build(), default=str))

    session.op_reply_style(names="changed")
    session.op_build()
    changed = len(json.dumps(session.op_build(), default=str))
    assert changed * 5 < whole, "%d against %d" % (changed, whole)


def test_and_says_which_names_appeared_and_went():
    session = Session(autosave=False)
    session.op_new_document()
    session.op_apply(ops=FOUR_HOLES[:1])
    session.op_reply_style(names="changed")
    session.op_build()
    out = session.op_add_feature(type="cylinder",
                                 args={"radius": 3, "height": 30,
                                       "at": [30, 20, -5], "centred": False})
    assert out["face_names_added"], out
    assert all(n.startswith("cylinder") for n in out["face_names_added"])


def test_the_tool_schema_says_what_a_value_may_be():
    from cadcore.service.mcp import tools

    by_name = {t["name"]: t for t in tools(Session())}
    assert by_name["render"]["inputSchema"]["properties"]["view"]["enum"] == \
        ["back", "bottom", "front", "iso", "left", "right", "top"]
    assert by_name["new_document"]["inputSchema"]["properties"]["unit"]["enum"] == \
        ["cm", "ft", "in", "m", "mm"]
    # a union is a union, not its first word
    assert by_name["add_pocket"]["inputSchema"]["properties"]["until"]["type"] == \
        ["string", "object"]


def test_every_enum_in_the_schema_comes_from_what_refuses_a_bad_value():
    """Guards: every enum is read from what refuses a bad value, not typed out."""
    from cadcore import features
    from cadcore.model.document import UNITS
    from cadcore.service.mcp import tools
    from cadcore.analysis.preview import VIEWS

    by_name = {t["name"]: t for t in tools(Session())}
    assert by_name["render"]["inputSchema"]["properties"]["view"]["enum"] == sorted(VIEWS)
    assert by_name["new_document"]["inputSchema"]["properties"]["unit"]["enum"] == sorted(UNITS)
    features.load()
    assert by_name["add_feature"]["inputSchema"]["properties"]["type"]["enum"] == \
        features.types()


def test_an_assistant_is_told_the_rules_before_its_first_call():
    from cadcore.service.mcp import INSTRUCTIONS

    for said in ("plate/+z", "millimetre", "kind", "new_document", "feature_types"):
        assert said.lower() in INSTRUCTIONS.lower(), said


# --- the shapes anybody draws --------------------------------------------------

@pytest.mark.parametrize("shape,extra", [
    ("rect", {"width": 24, "height": 14}),
    ("circle", {"radius": 6}),
    ("slot", {"length": 20, "radius": 4}),
    ("polygon", {"radius": 7, "sides": 6}),
])
def test_a_profile_anybody_draws_is_one_call(shape, extra):
    """Guards: a common profile is one call, not points, segments and constraints."""
    session = Session(autosave=False)
    session.op_new_document()
    session.op_add_feature(type="box", args={"size": [80, 50, 12],
                                             "at": [0, 0, 0], "centred": False})
    made = session.op_add_profile(shape=shape, face="box1/+z", at=(0, 0), **extra)
    cut = session.op_add_feature(type="pocket",
                                 args={"sketch": made["feature"], "body": "box1",
                                       "depth": 4.0})
    assert cut["volume_mm3"] < 80 * 50 * 12, "the pocket removed nothing"


def test_a_profile_needs_somewhere_to_be_drawn():
    session = Session(autosave=False)
    session.op_new_document()
    session.op_add_feature(type="box", args={"size": [10, 10, 10],
                                             "at": [0, 0, 0], "centred": False})
    with pytest.raises(CadError) as caught:
        session.op_add_profile(shape="rect")
    assert caught.value.kind == "bad_arguments"
    with pytest.raises(CadError):
        session.op_add_profile(shape="rect", face="box1/+z", plane="somewhere")


def test_a_shape_nobody_draws_is_refused_with_the_ones_there_are():
    session = Session(autosave=False)
    session.op_new_document()
    with pytest.raises(CadError) as caught:
        session.op_add_profile(shape="trapezium", plane="xy")
    assert "circle" in caught.value.message and "slot" in caught.value.message
    assert set(session.op_profile_shapes()["shapes"]) == {
        "rect", "circle", "slot", "polygon", "ellipse", "text"}


# --- coming back to an idea ------------------------------------------------------

def test_a_checkpoint_is_a_name_rather_than_a_number_of_undos():
    session = Session(autosave=False)
    session.op_open("examples/bracket.json")
    was = session.op_build()["volume_mm3"]
    session.op_checkpoint(name="before I thinned it")
    for step in range(6):
        session.op_set_parameter("thickness", 8.0 - step * 0.5)
    thin = session.op_build()["volume_mm3"]
    assert thin < was

    out = session.op_restore(name="before I thinned it")
    assert out["volume_mm3"] == pytest.approx(was)
    # and it is one step on the same stack, not an unwinding of six
    assert session.op_undo()["volume_mm3"] == pytest.approx(thin)


def test_restoring_a_name_nobody_kept_says_which_names_there_are():
    session = Session(autosave=False)
    session.op_open("examples/bracket.json")
    session.op_checkpoint(name="one")
    with pytest.raises(CadError) as caught:
        session.op_restore(name="two")
    assert caught.value.kind == "unknown_reference"
    assert caught.value.detail["checkpoints"] == ["one"]


# --- what a client is told before it runs anything --------------------------------

def test_an_operation_that_changes_the_document_says_so():
    """Guards: `readOnlyHint` is false for every operation that edits, since a
    client runs read-only tools without asking."""
    from cadcore.service.mcp import tools

    by_name = {t["name"]: t for t in tools(Session())}
    for reader in ("build", "measure", "describe_faces", "interference",
                   "printability", "simulate"):
        assert by_name[reader]["annotations"]["readOnlyHint"], reader
    for writer in ("add_fillet", "set_parameter", "undo", "open", "apply",
                   "restore", "add_profile", "rollback"):
        assert not by_name[writer]["annotations"]["readOnlyHint"], writer


def test_that_answer_is_read_off_the_code_rather_than_kept_in_a_list():
    """Guards: writers are read off the code, not kept in a list."""
    from cadcore.ops.catalogue import _writers

    writers = _writers()
    assert "add_fillet" in writers and "build" not in writers
    # both change what a later build answers with
    assert {"reply_style", "rollback"} <= writers


def test_no_operation_argument_is_named_after_the_protocol():
    """Guards: no operation has an argument called `op` or `id`.

    Those are the line protocol's keys and `handle` strips them before the
    call, so such an argument could never be received.
    """
    import inspect

    from cadcore.ops.session import Session

    offenders = []
    for name, fn in inspect.getmembers(Session, inspect.isfunction):
        if not name.startswith("op_"):
            continue
        for parameter in inspect.signature(fn).parameters:
            if parameter in ("op", "id"):
                offenders.append("%s(%s)" % (name, parameter))
    assert not offenders, offenders
