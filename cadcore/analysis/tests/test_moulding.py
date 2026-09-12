"""The draft check: which faces need draft and where a straight pull would catch."""
from cadcore.service.server import Session


def block() -> Session:
    session = Session(autosave=False)
    session.op_new_document()
    session.op_add_feature(type="box", args={"size": [40, 20, 10]}, feature_id="blk")
    session.op_build()
    return session


def test_vertical_walls_need_draft_and_flat_ends_do_not():
    session = block()
    out = session.op_draft_check(direction=[0, 0, 1], min_angle=1.0)
    assert not out["ok"]
    assert set(out["needs_draft"]) == {"blk/+x", "blk/-x", "blk/+y", "blk/-y"}
    assert not out["undercuts"]
    faces = {f["face"]: f for f in out["faces"]}
    assert faces["blk/+z"]["draft_deg"] == 90 and faces["blk/+z"]["side"] == "+"
    assert faces["blk/-z"]["side"] == "-"


def test_drafted_walls_pass():
    session = block()
    session.op_add_draft(faces=["blk/+x", "blk/-x", "blk/+y", "blk/-y"], angle=3.0, neutral="blk/-z")
    out = session.op_draft_check(direction=[0, 0, 1], min_angle=1.0)
    assert out["ok"], out["needs_draft"]


def test_a_side_hole_is_an_undercut():
    session = block()
    session.op_add_hole(face="blk/+x", diameter=6, at=[0, 0])
    out = session.op_draft_check(direction=[0, 0, 1], min_angle=0.0)
    assert [u["face"] for u in out["undercuts"]] == ["hole1/bore"]
    assert out["undercuts"][0]["undercut_mm2"] > 0
    faces = {f["face"]: f for f in out["faces"]}
    assert faces["hole1/bore"]["side"] == "both"


def test_a_boss_on_top_is_not_an_undercut():
    session = block()
    session.op_add_feature(type="box", args={"size": [10, 10, 4], "at": [0, 0, 7]}, feature_id="cap")
    session.op_add_feature(type="fuse", args={"target": "blk", "tool": "cap"}, feature_id="t")
    out = session.op_draft_check(direction=[0, 0, 1], min_angle=0.0)
    assert not out["undercuts"]
