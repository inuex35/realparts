"""Arcs, holes, revolves, chamfers, patterns and imports, all named."""
import math
import os

import pytest

from cadcore import Document, describe, export_step
from cadcore.geometry import kernel
from cadcore.model.document import Feature
from cadcore.errors import CadError
from cadcore.evaluation.graph import Evaluator
from cadcore.geometry.kernel import CadError
from cadcore.service.server import Session


def build(doc):
    ev = Evaluator(doc)
    return ev.build(), ev


ARC_PLATE = {
    "parameters": {"L": 50, "W": 20, "R": 6, "hole_r": 4, "t": 5},
    "features": [
        {"id": "profile", "type": "sketch",
         "points": {"p0": [0, 0], "p1": [50, 0], "p2": [50, 14], "p3": [44, 20], "p4": [0, 20],
                    "m": [44, 14], "c": [15, 10]},
         "lines": {"south": ["p0", "p1"], "east": ["p1", "p2"], "north": ["p3", "p4"],
                   "west": ["p4", "p0"]},
         "arcs": {"corner": {"centre": "m", "from": "p2", "to": "p3", "radius": "R"}},
         "circles": {"hole": {"centre": "c", "radius": "hole_r"}},
         "constraints": [
             {"type": "fix", "point": "p0", "at": [0, 0]},
             {"type": "horizontal", "line": "south"}, {"type": "vertical", "line": "east"},
             {"type": "horizontal", "line": "north"}, {"type": "vertical", "line": "west"},
             {"type": "distance", "points": ["p0", "p1"], "value": "L"},
             {"type": "distance", "points": ["p0", "p4"], "value": "W"},
             {"type": "tangent", "line": "east", "arc": "corner"},
             {"type": "tangent", "line": "north", "arc": "corner"},
             {"type": "fix", "point": "c", "at": [15, 10]}]},
        {"id": "plate", "type": "extrude", "sketch": "profile", "distance": "t"}],
    "result": "plate",
}


def doc_from(raw: dict) -> Document:
    feats = [Feature(f["id"], f["type"], {k: v for k, v in f.items() if k not in ("id", "type")})
             for f in raw["features"]]
    return Document(raw.get("parameters", {}), feats, raw.get("result"))


def test_a_sketch_can_hold_arcs_and_holes():
    body, _ = build(doc_from(ARC_PLATE))
    info = describe(body)
    assert "plate/corner" in info["face_names"]      # the arc kept its own name
    assert "plate/hole" in info["face_names"]        # the circle became the hole
    area = 50 * 20 - (36 - math.pi * 36 / 4) - math.pi * 16
    assert info["volume_mm3"] == pytest.approx(area * 5, abs=0.5)


def test_a_tangency_that_holds_exactly_is_not_underconstrained():
    """PlaneGCS reports phantom degrees of freedom when a tangency is exact;
    the jittered re-solve tells that rank artefact from a loose sketch."""
    _, ev = build(doc_from(ARC_PLATE))
    assert ev.sketches["profile"].diagnosis.get("determinate_despite_dof") is True


def test_a_genuinely_loose_sketch_is_still_refused():
    loose = dict(ARC_PLATE["features"][0])
    loose["constraints"] = [c for c in loose["constraints"] if c["type"] != "distance"]
    raw = {"parameters": ARC_PLATE["parameters"], "features": [loose], "result": "profile"}
    with pytest.raises(CadError) as exc:
        build(doc_from(raw))
    assert exc.value.kind == "sketch_underconstrained"
    assert exc.value.detail["moved"]                # it names the points that moved


def test_revolve_names_faces_after_the_section():
    body, _ = build(Document.load("examples/flange.json"))
    names = describe(body)["face_names"]
    for segment in ("rim", "hub", "bore", "base", "shoulder", "collar"):
        assert f"body/{segment}" in names


def test_the_bolt_circle_is_one_feature_with_named_instances():
    body, _ = build(Document.load("examples/flange.json"))
    holes = [n for n in describe(body)["face_names"] if n.startswith("bolt/side")]
    assert holes == ["bolt/side"] + [f"bolt/side~{k}" for k in range(1, 6)]


def test_chamfer_takes_material_off_and_is_refused_when_too_big():
    blk = kernel.box("blk", [40, 30, 10])
    edges = ["blk/+x|blk/+z"]
    assert kernel.volume(kernel.chamfer("c", blk, edges, 3)) == \
        pytest.approx(40 * 30 * 10 - 0.5 * 9 * 30, abs=0.5)
    with pytest.raises(CadError) as exc:
        kernel.chamfer("c", blk, edges, 400)
    assert exc.value.kind in ("chamfer_failed", "empty_result")
    with pytest.raises(CadError) as exc:
        kernel.chamfer("c", blk, ["blk/nope|blk/+z"], 2)
    assert exc.value.kind == "unresolved_reference"


def test_mirror_and_linear_pattern_keep_every_instance_selectable():
    blk = kernel.box("blk", [20, 20, 10], (30, 0, 0))
    both = kernel.mirror("both", blk, [0, 0, 0], [1, 0, 0])
    assert kernel.volume(both) == pytest.approx(2 * 20 * 20 * 10, abs=0.5)
    assert "blk/+x~m" in both.face_names()

    plate = kernel.box("plate", [100, 30, 8], (0, 0, 0), centred=False)
    pins = kernel.pattern("pins", kernel.cylinder("pin", 3, 40, (20, 15, 4)), [1, 0, 0], 20, 4)
    drilled = kernel.cut("drilled", plate, pins)
    assert kernel.volume(drilled) == pytest.approx(100 * 30 * 8 - 4 * math.pi * 9 * 8, abs=0.5)
    assert [n for n in drilled.face_names() if n.startswith("pin/side")] == \
        ["pin/side", "pin/side~1", "pin/side~2", "pin/side~3"]


def test_a_step_file_comes_back_with_usable_names(tmp_path):
    source, _ = build(Document.load("examples/bracket.json"))
    step = str(tmp_path / "part.step")
    export_step(source, step)

    session = Session()
    opened = session.op_open(step)
    assert [f["type"] for f in opened["features"]] == ["import_step"]
    info = session.op_build()
    assert info["volume_mm3"] == pytest.approx(describe(source)["volume_mm3"], rel=1e-6)
    # imported faces are named from their own geometry; select one and fillet it
    edges = session.op_select_edges({"between": ["part/+z", "part/+y"], "limit": 1})["edges"]
    assert session.op_add_fillet(edges, radius=1.5)["faces"] > info["faces"]

    with pytest.raises(CadError) as exc:
        kernel.import_step("x", str(tmp_path / "missing.step"))
    assert exc.value.kind == "file_not_found"


def test_a_pocket_can_repeat_itself():
    session = Session()
    session.op_open("examples/sketch_plate.json")
    base = session.op_build()["volume_mm3"]
    out = session.op_add_pocket("plate/top", depth=2, width=6, height=6,
                                count=3, spacing=12, direction=[1, 0, 0])
    assert out["volume_mm3"] == pytest.approx(base - 3 * 6 * 6 * 2, abs=0.5)
    names = session.op_tessellate()["face_table"]
    assert "pocket1/floor" in names and "pocket1/floor~2" in names


# --- solids that are not prisms ------------------------------------------------

def square(name, half, z, plane_normal=(0, 0, 1), x_axis=(1, 0, 0), open_path=False):
    return Feature(name, "sketch", {
        "plane": {"origin": [0, 0, z], "normal": list(plane_normal), "x_axis": list(x_axis)},
        "points": {"a": [-half, -half], "b": [half, -half], "c": [half, half],
                   "d": [-half, half]},
        "lines": {"front": ["a", "b"], "right": ["b", "c"], "back": ["c", "d"],
                  "left": ["d", "a"]},
        "constraints": [
            {"type": "fix", "point": "a", "at": [-half, -half]},
            {"type": "horizontal", "line": "front"}, {"type": "vertical", "line": "right"},
            {"type": "horizontal", "line": "back"}, {"type": "vertical", "line": "left"},
            {"type": "distance", "points": ["a", "b"], "value": 2 * half},
            {"type": "distance", "points": ["b", "c"], "value": 2 * half}]})


def test_shell_opens_at_a_named_face():
    solid = kernel.box("b", [40, 30, 20])
    hollow = kernel.shell("sh", solid, ["b/+z"], 2)
    assert kernel.volume(hollow) == pytest.approx(40 * 30 * 20 - 36 * 26 * 18, abs=0.5)
    assert hollow.face("b/+z") is not None          # the opening becomes the rim
    with pytest.raises(CadError) as exc:
        kernel.shell("sh", solid, ["b/nope"], 2)
    assert exc.value.kind == "unresolved_reference"


def test_draft_keeps_the_names_of_the_faces_it_tilts():
    """OCCT reports Modified for the faces it did not touch, so the drafted
    ones are re-attached deliberately."""
    solid = kernel.box("blk", [40, 40, 20])
    tapered = kernel.draft("t", solid, ["blk/+x", "blk/-x"], 5, [0, 0, 1], "blk/-z")
    assert {"blk/+x", "blk/-x"} <= set(tapered.face_names())
    assert kernel.volume(tapered) < 40 * 40 * 20


def test_loft_carries_the_section_names():
    doc = Document(features=[square("s0", 20, 0), square("s1", 10, 30),
                             Feature("taper", "loft", {"sketches": ["s0", "s1"]})],
                   result="taper")
    body, _ = build(doc)
    names = describe(body)["face_names"]
    assert {"taper/front", "taper/right", "taper/back", "taper/left"} <= set(names)
    # a frustum: (A1 + A2 + sqrt(A1*A2)) * h / 3
    assert kernel.volume(body) == pytest.approx((1600 + 400 + 800) / 3 * 30, abs=1.0)


def test_sweep_mitres_a_sharp_corner():
    path = Feature("path", "sketch", {
        "open": True,
        "plane": {"origin": [0, 0, 0], "normal": [0, 1, 0], "x_axis": [0, 0, 1]},
        "points": {"p0": [0, 0], "p1": [40, 0], "p2": [40, 30]},
        "lines": {"up": ["p0", "p1"], "over": ["p1", "p2"]},
        "constraints": [{"type": "fix", "point": "p0", "at": [0, 0]},
                        {"type": "horizontal", "line": "up"},
                        {"type": "vertical", "line": "over"},
                        {"type": "distance", "points": ["p0", "p1"], "value": 40},
                        {"type": "distance", "points": ["p1", "p2"], "value": 30}]})
    doc = Document(features=[square("prof", 5, 0), path,
                             Feature("bar", "sweep", {"profile": "prof", "path": "path"})],
                   result="bar")
    body, _ = build(doc)
    # the default transition collapses the corner; mitred, the volume is exact
    assert kernel.volume(body) == pytest.approx(100 * 70, abs=1.0)
    assert "bar/front" in describe(body)["face_names"]


def test_a_counterbored_hole_is_one_feature():
    doc = Document(parameters={"d": 8, "cb": 14},
                   features=[Feature("plate", "box", {"size": [80, 50, 12], "at": [0, 0, 0],
                                                      "centred": False}),
                             Feature("h", "hole", {"body": "plate", "face": "plate/+z",
                                                   "at": [-20, 0], "diameter": "d",
                                                   "counterbore": {"diameter": "cb",
                                                                   "depth": 4},
                                                   "pattern": {"direction": [1, 0, 0],
                                                               "spacing": 20, "count": 3}})],
                   result="h")
    body, _ = build(doc)
    names = describe(body)["face_names"]
    assert {"h/bore", "h/counterbore", "h/seat", "h/bore~2"} <= set(names)
    expected = 80 * 50 * 12 - 3 * (math.pi * 16 * 12 + math.pi * (49 - 16) * 4)
    assert kernel.volume(body) == pytest.approx(expected, abs=1.0)


def test_a_countersunk_hole_names_its_cone():
    doc = Document(features=[Feature("plate", "box", {"size": [40, 40, 10], "at": [0, 0, 0],
                                                      "centred": False}),
                             Feature("h", "hole", {"body": "plate", "face": "plate/+z",
                                                   "diameter": 6,
                                                   "countersink": {"diameter": 12,
                                                                   "angle": 90}})],
                   result="h")
    body, _ = build(doc)
    assert "h/countersink" in describe(body)["face_names"]


def test_standard_holes_come_from_the_table():
    from cadcore.model import fasteners

    tapped = fasteners.resolve("M6", "tapped")
    assert tapped["diameter"] == 5.0 and tapped["note"] == "M6x1 - 6H"
    seated = fasteners.resolve("M8", "normal", "counterbore")
    assert seated["diameter"] == 9.0
    assert seated["counterbore"] == {"diameter": 14.0, "depth": 8.6}
    assert "c'bore" in seated["note"]
    with pytest.raises(CadError) as exc:
        fasteners.resolve("M7", "normal")
    assert exc.value.kind == "unknown_fastener"


def test_a_hole_can_be_asked_for_by_standard():
    doc = Document(features=[
        Feature("plate", "box", {"size": [80, 40, 12], "at": [0, 0, 0], "centred": False}),
        Feature("tapped", "hole", {"body": "plate", "face": "plate/+z", "at": [-20, 0],
                                   "standard": "M6", "fit": "tapped", "depth": 10}),
        Feature("clear", "hole", {"body": "tapped", "face": "plate/+z", "at": [20, 0],
                                  "standard": "M8", "fit": "normal", "seat": "counterbore"})],
        result="clear")
    body, _ = build(doc)
    names = describe(body)["face_names"]
    assert {"tapped/bore", "tapped/floor", "clear/bore", "clear/counterbore"} <= set(names)
    expected = (80 * 40 * 12 - math.pi * 2.5 ** 2 * 10
                - (math.pi * 4.5 ** 2 * 12 + math.pi * (7 ** 2 - 4.5 ** 2) * 8.6))
    assert kernel.volume(body) == pytest.approx(expected, abs=1.0)


# --- what a feature depends on -------------------------------------------------

def test_a_reference_is_found_wherever_it_is_written(open_document):
    """Guards: an end condition naming a work plane makes that plane an
    ancestor of the extrude, so it is built first."""
    session = open_document({
        "parameters": {}, "features": [
            {"id": "base", "type": "box", "size": [40, 30, 10], "at": [0, 0, 0],
             "centred": False},
            {"id": "lid", "type": "plane", "from": {"body": "base", "face": "base/+z"},
             "offset": 12},
            {"id": "post", "type": "sketch", "on": {"body": "base", "face": "base/+z"},
             "points": {"o": [0, 0], "a": [8, 0], "b": [8, 8], "c": [0, 8]},
             "lines": {"s": ["o", "a"], "e": ["a", "b"], "n": ["b", "c"],
                       "w": ["c", "o"]},
             "constraints": [{"type": "fix", "point": "o", "at": [0, 0]},
                             {"type": "fix", "point": "a", "at": [8, 0]},
                             {"type": "fix", "point": "b", "at": [8, 8]},
                             {"type": "fix", "point": "c", "at": [0, 8]}]},
            {"id": "tower", "type": "extrude", "sketch": "post",
             "until": {"plane": "lid"}}],
        "result": "tower"})

    assert session.op_build()["volume_mm3"] == pytest.approx(8 * 8 * 12)
    listed = session.op_describe_document()["features"]
    inputs = {f["id"]: set(f["inputs"]) for f in listed}
    assert "lid" in inputs["tower"] and "post" in inputs["tower"]
    assert "base" in inputs["lid"]


def test_a_repeat_can_turn_about_a_named_axis(open_document):
    """A bolt circle: the hole's own repeat, about a datum axis."""
    session = open_document({
        "parameters": {}, "features": [
            {"id": "disc", "type": "cylinder", "radius": 30, "height": 8,
             "at": [0, 0, 0], "axis": [0, 0, 1], "centred": False},
            {"id": "spin", "type": "axis", "origin": [0, 0, 0], "direction": [0, 0, 1]},
            {"id": "bolts", "type": "hole", "body": "disc", "face": "disc/+z",
             "diameter": 5, "at": [20, 0],
             "pattern": {"axis": "spin", "count": 6}}],
        "result": "bolts"})
    out = session.op_build()

    assert out["volume_mm3"] == pytest.approx(math.pi * 30 ** 2 * 8 - 6 * math.pi * 2.5 ** 2 * 8)
    assert "bolts/bore~5" in out["face_names"]
    assert "spin" in set(session.op_describe_document()["features"][-1]["inputs"])


def test_a_file_name_is_not_a_reference(tmp_path):
    """`path` names a sketch on a sweep and a file on an import; each feature
    type's declaration says which of its arguments are references."""
    from cadcore.features import handler

    assert handler("sweep").references({"profile": "ring", "path": "route"}) \
        == ["ring", "route"]
    assert handler("import_step").references({"path": str(tmp_path / "part.step")}) == []


def test_every_feature_type_declares_its_arguments():
    """Guards: every feature type declares its arguments, since validation,
    dependencies, the catalogue and the viewport all read the declaration."""
    from cadcore import features

    from cadcore.features.declare.registry import COMMON

    features.load()
    # the registry adds the common arguments to every declaration, so "no
    # arguments" has to be measured after taking those back off: measured
    # before, this never failed for anything
    without = [name for name in features.types()
               if not set(features.handler(name).args) - set(COMMON)]
    assert without == []


def test_an_argument_a_feature_does_not_take_is_refused(open_document):
    """Guards: an unknown argument key is refused rather than ignored."""
    session = open_document({
        "parameters": {}, "features": [
            {"id": "block", "type": "box", "size": [10, 10, 10], "centred": False}],
        "result": "block"})

    with pytest.raises(CadError) as exc:
        session.op_edit_feature("block", {"centered": True})
    assert exc.value.kind == "unknown_argument"
    assert exc.value.detail["unknown"] == ["centered"]
    assert "centred" in exc.value.detail["understood"]

    with pytest.raises(CadError) as exc:
        session.op_edit_feature("block", {"size": "big"})
    assert exc.value.kind == "bad_arguments" and exc.value.detail["argument"] == "size"


def test_a_hole_names_its_floor_the_same_whichever_way_it_is_drilled():
    """Guards: a hole's cap is named `floor` whichever way the hole points,
    including on a slant, rather than after a world axis."""
    plate = {"id": "plate", "type": "box", "size": [80, 40, 12],
             "at": [0, 0, 0], "centred": False}

    def drilled(face, at):
        doc = Document.from_dict({"parameters": {}, "features": [
            plate, {"id": "h", "type": "hole", "body": "plate", "face": face,
                    "diameter": 6, "depth": 8, "at": at}], "result": "h"})
        return set(describe(Evaluator(doc).build())["face_names"])

    for face, at in (("plate/+z", [0, 0]), ("plate/-x", [0, 0]),
                     ("plate/+y", [0, 0])):
        names = drilled(face, at)
        assert {"h/bore", "h/floor"} <= names, f"drilled into {face}: {sorted(names)}"


def test_a_thread_leaves_one_solid_and_not_two():
    """Guards: the fuse that adds the thread ridge yields one solid.

    OCCT can return both arguments unjoined in one compound with the right
    total volume, after which every downstream boolean is wrong.
    """
    from ...geometry.core.measure import solid_count

    doc = Document.from_dict({"parameters": {}, "features": [
        {"id": "plate", "type": "box", "size": [80, 40, 12], "at": [0, 0, 0],
         "centred": False},
        {"id": "h", "type": "hole", "body": "plate", "face": "plate/+z",
         "standard": "M6", "fit": "tapped", "depth": 10, "at": [-20, 0],
         "threaded": True}], "result": "h"})
    body = Evaluator(doc).build()
    assert solid_count(body.shape) == 1


def test_a_boolean_after_a_thread_is_still_arithmetic():
    """Guards: a boolean after a thread still removes exactly its own volume
    (a 10x10 notch through a 12 mm plate removes 1200 mm3)."""
    from ...geometry.solids.booleans import cut
    from ...geometry.solids.primitives import box

    doc = Document.from_dict({"parameters": {}, "features": [
        {"id": "plate", "type": "box", "size": [80, 40, 12], "at": [0, 0, 0],
         "centred": False},
        {"id": "h", "type": "hole", "body": "plate", "face": "plate/+z",
         "standard": "M6", "fit": "tapped", "depth": 10, "at": [-20, 0],
         "threaded": True}], "result": "h"})
    threaded = Evaluator(doc).build()
    before = describe(threaded)["volume_mm3"]
    after = cut("notch", threaded, box("n", [10, 10, 20], [30, 15, 0], centred=False))
    assert describe(after)["volume_mm3"] == pytest.approx(before - 1200.0, abs=1e-3)


def test_a_patterned_tapped_hole_taps_every_one_of_them():
    """Guards: a patterned tapped hole threads `bore~1`, `bore~2` as well as
    `bore`, so a pattern and separate features come out the same."""
    plate = {"id": "plate", "type": "box", "size": [80, 40, 12], "at": [0, 0, 0],
             "centred": False}
    tapped = {"type": "hole", "standard": "M6", "fit": "tapped", "depth": 10,
              "threaded": True, "face": "plate/+z"}

    one_by_one, previous = [plate], "plate"
    for index, x in enumerate((-20.0, 0.0, 20.0)):
        one_by_one.append(dict(tapped, id=f"h{index}", body=previous, at=[x, 0]))
        previous = f"h{index}"
    separately = Evaluator(Document.from_dict(
        {"parameters": {}, "features": one_by_one, "result": previous})).build()

    patterned = Evaluator(Document.from_dict({"parameters": {}, "features": [
        plate, dict(tapped, id="h", body="plate", at=[-20, 0],
                    pattern={"count": 3, "spacing": 20, "direction": [1, 0, 0]})],
        "result": "h"})).build()

    assert describe(patterned)["volume_mm3"] == pytest.approx(
        describe(separately)["volume_mm3"], abs=1e-3)
    # and it is three holes' worth of thread, not one
    plain = Evaluator(Document.from_dict({"parameters": {}, "features": [
        plate, dict(tapped, id="h", body="plate", at=[-20, 0], threaded=False,
                    pattern={"count": 3, "spacing": 20, "direction": [1, 0, 0]})],
        "result": "h"})).build()
    per_hole = (describe(plain)["volume_mm3"]
                - describe(patterned)["volume_mm3"]) / 3
    assert per_hole > 1.0
