"""A mirrored body keeps outward normals, true roles and correct hole detection.

A mirror is a transform with a negative determinant, so the surface axis
plus the orientation flag does not give the real normal. `face_info`,
`is_hole` and the role in the face name (`blk/+x~m` must face +x) all
depend on getting this right.
"""
from __future__ import annotations

import math

import pytest

from ..core.naming import _edge_key, is_hole
from cadcore.ops.session import Session

MIRRORED = {"features": [
    {"id": "blk", "type": "box", "size": [20, 20, 20], "at": [40, 0, 0]},
    {"id": "pin", "type": "cylinder", "radius": 3, "height": 30, "at": [40, 0, 0]},
    {"id": "b", "type": "fuse", "target": "blk", "tool": "pin"},
    {"id": "m", "type": "mirror", "body": "b",
     "plane": {"origin": [0, 0, 0], "normal": [1, 0, 0]}, "merge": False}],
    "result": "m"}


@pytest.fixture
def mirrored():
    s = Session(autosave=False)
    s.op_load_json(document=MIRRORED)
    return s


def test_every_mirrored_plane_faces_outward(mirrored):
    centre = (-40.0, 0.0, 0.0)
    for f in mirrored.op_describe_faces()["faces"]:
        if f["shape"] != "plane" or not f["name"].startswith("blk/"):
            continue
        out = [c - o for c, o in zip(f["centre"], centre)]
        assert sum(a * b for a, b in zip(out, f["normal"])) > 0, f["name"]


def test_the_role_says_where_the_face_points(mirrored):
    faces = {f["name"]: f for f in mirrored.op_describe_faces()["faces"]}
    # the mirror is in x: +x and -x swap, y and z keep theirs
    assert faces["blk/-x~m"]["centre"][0] == pytest.approx(-50)
    assert faces["blk/-x~m"]["normal"][0] == pytest.approx(-1)
    assert faces["blk/+x~m"]["centre"][0] == pytest.approx(-30)
    assert faces["blk/+x~m"]["normal"][0] == pytest.approx(1)
    assert faces["blk/+y~m"]["normal"][1] == pytest.approx(1)
    assert faces["blk/-z~m"]["normal"][2] == pytest.approx(-1)


def test_a_mirrored_pin_is_still_a_pin(mirrored):
    body = mirrored.body
    side = next(n for n in body.face_names() if n.startswith("pin/side"))
    assert is_hole(body.face(side)) is False


def test_a_hole_on_a_mirrored_face_removes_material(mirrored):
    before = mirrored.op_build()["volume_mm3"]
    after = mirrored.op_add_hole(face="blk/-y~m", diameter=4.0, depth=10.0)["volume_mm3"]
    assert before - after == pytest.approx(math.pi * 4 * 10, rel=0.02)


def test_the_two_halves_of_a_split_cylinder_keep_their_order():
    """Guards: the halves of an axially split cylinder tie on the in-plane
    key, so `@0` must be chosen by a stable rule, not by OCCT's output order."""
    doc = {"parameters": {"h": 30}, "features": [
        {"id": "rod", "type": "cylinder", "radius": 5, "height": "h"},
        {"id": "knife", "type": "box", "size": [20, 0.5, 60], "at": [0, 0, 0]},
        {"id": "split", "type": "cut", "target": "rod", "tool": "knife"}],
        "result": "split"}
    s = Session(autosave=False)
    s.op_load_json(document=doc)

    def side_of(name):
        f = next(x for x in s.op_describe_faces()["faces"] if x["name"] == name)
        return f["centre"][1] > 0

    first = side_of("rod/side@0")
    for h in (36, 42, 24, 30):
        s.op_set_parameter(name="h", value=h)
        assert side_of("rod/side@0") == first, h


def test_concentric_edges_sort_apart():
    doc = {"features": [
        {"id": "washer", "type": "cylinder", "radius": 10, "height": 2},
        {"id": "bore", "type": "cylinder", "radius": 4, "height": 4},
        {"id": "ring", "type": "cut", "target": "washer", "tool": "bore"}],
        "result": "ring"}
    s = Session(autosave=False)
    s.op_load_json(document=doc)
    from ..core.naming import edges_of
    top = s.body.face("washer/+z")
    keys = [_edge_key(e) for e in edges_of(top)]
    assert len(set(keys)) == len(keys), keys


def test_a_body_mirrored_twice_gives_every_face_its_own_name():
    """One name, one face.

    The second mirror hands the fuse both the body's own `box1/+y~m` and the
    copy of `box1/+y`, which it also calls `box1/+y~m`. They are two faces
    40 mm apart, and `body.face(name)` could only return one of them.
    """
    from collections import Counter

    s = Session(autosave=False)
    s.op_new_document()
    s.op_add_feature(type="box", args={"size": [40, 30, 20], "at": [0, 0, 10]},
                     feature_id="box1")
    s.op_add_mirror(face="box1/-x", feature_id="mirror1")
    s.op_add_mirror(face="box1/-z~m", feature_id="mirror2")
    named = [n for n, _ in s.evaluator.build("mirror2").names]
    twice = {n: k for n, k in Counter(named).items() if k > 1}
    assert not twice, twice
    assert len(named) == 16


def test_a_draft_occt_throws_out_of_is_a_refusal():
    """`BRepOffsetAPI_DraftAngle.Add` raises for some faces instead of
    answering `AddDone`, and a Standard_OutOfRange is not something a caller
    can act on."""
    from cadcore.errors import CadError

    s = Session(autosave=False)
    s.op_new_document()
    s.op_add_feature(type="box", args={"size": [40, 30, 20], "at": [0, 0, 10]},
                     feature_id="box1")
    s.op_add_mirror(face="box1/-x", feature_id="mirror1")
    s.op_add_mirror(face="box1/-z~m", feature_id="mirror2")
    with pytest.raises(CadError) as refused:
        s.op_add_draft(faces=["box1/+y"], angle=3.0)
    assert refused.value.kind == "draft_failed"
    assert refused.value.detail["reason"] == "NCollection_Sequence::Value"
