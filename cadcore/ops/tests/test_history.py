"""Editing the history: undo, redo, rollback, moving and editing features."""
import pytest

from cadcore.geometry.kernel import CadError
from cadcore.service.server import Session


@pytest.fixture()
def session():
    s = Session()
    s.op_open("examples/sketch_plate.json")
    s.op_build()
    return s


def test_every_edit_can_be_undone(session):
    base = session.op_build()["volume_mm3"]
    session.op_add_fillet(["plate/left|plate/top"], radius=2.5)
    filleted = session.op_build()["volume_mm3"]
    session.op_set_parameter("width", 118)
    assert session.op_undo()["volume_mm3"] == pytest.approx(filleted)
    assert session.op_undo()["volume_mm3"] == pytest.approx(base)
    with pytest.raises(CadError) as exc:
        session.op_undo()
    assert exc.value.kind == "nothing_to_undo"
    assert session.op_redo()["volume_mm3"] == pytest.approx(filleted)


def test_a_refused_edit_leaves_nothing_behind(session):
    before = session.op_describe_document()
    with pytest.raises(CadError):
        session.op_add_fillet(["plate/left|plate/top"], radius=500)
    assert session.op_describe_document() == before
    with pytest.raises(CadError) as exc:
        session.op_undo()                       # the failure was not an edit
    assert exc.value.kind == "nothing_to_undo"


def test_rollback_shows_the_model_without_the_later_features(session):
    full = session.op_build()
    rolled = session.op_rollback("plate")
    assert rolled["faces"] < full["faces"]
    assert rolled["rolled_back_to"] == "plate"
    # nothing was deleted: dropping the view brings it all back
    assert session.op_rollback(None)["faces"] == full["faces"]
    assert [f["id"] for f in session.op_describe_document()["features"]] == \
        ["profile", "plate", "rounded"]


def test_editing_a_feature_rebuilds_or_refuses(session):
    smaller = session.op_edit_feature("rounded", {"radius": 3})["volume_mm3"]
    bigger = session.op_edit_feature("rounded", {"radius": 10})["volume_mm3"]
    assert bigger < smaller                      # a larger fillet removes more
    with pytest.raises(CadError):
        session.op_edit_feature("rounded", {"radius": 500})
    assert session.op_build()["volume_mm3"] == pytest.approx(bigger)


def test_moving_a_feature_relinks_the_chain(session):
    session.op_add_pocket("plate/top", depth=3, width=20, height=12)
    session.op_add_fillet(["plate/right|plate/top"], radius=2.0)
    order = [f["id"] for f in session.op_describe_document()["features"]]
    assert order[-1] == "fillet1"

    out = session.op_move_feature("fillet1", after="plate")
    assert out["after"] == "plate"
    doc = session.op_describe_document()
    assert [f["id"] for f in doc["features"]].index("fillet1") < \
        [f["id"] for f in doc["features"]].index("pocket1")
    # the chain still ends where the document says it does
    assert doc["result"] == "pocket1"
    assert session.op_build()["faces"] > 0

    with pytest.raises(CadError) as exc:
        session.op_move_feature("profile", after="plate")
    assert exc.value.kind == "not_a_chain_feature"


def test_a_move_that_breaks_the_model_is_refused_whole(session):
    """Order is not free: an 8 mm corner fillet cannot follow a 2 mm one that
    lands on the same corner. The refusal has to leave the document alone."""
    session.op_add_fillet(["plate/left|plate/top"], radius=2.0)
    before = session.op_describe_document()
    with pytest.raises(CadError) as exc:
        session.op_move_feature("fillet1", after="plate")
    assert exc.value.kind == "fillet_failed"
    assert session.op_describe_document() == before
    assert session.op_build()["faces"] > 0


def test_a_missing_input_is_not_called_a_cycle(session):
    """A feature naming an id that does not exist is a different problem from a
    loop, and saying "cycle" sends the reader looking for the wrong thing."""
    from cadcore.geometry.kernel import CadError

    with pytest.raises(CadError) as exc:
        session.op_edit_feature("rounded", {"body": "nowhere"})
    assert exc.value.kind == "unknown_feature"
    assert exc.value.detail["missing"] == ["nowhere"]
    assert "rounded" in exc.value.detail["referenced_by"]
    assert session.op_build()["faces"] > 0          # and the document is untouched
