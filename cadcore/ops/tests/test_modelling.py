"""The modelling vocabulary: end conditions, datums, variable fillets, slots."""
import math

import pytest

from cadcore import describe
from cadcore.geometry import kernel
from cadcore.model.document import Document, Feature
from cadcore.evaluation.graph import Evaluator
from cadcore.geometry.kernel import CadError
from cadcore.geometry.core.occ import bounds


def rectangle(name, on, half, size):
    return Feature(name, "sketch", {
        "on": on,
        "points": {"a": [-half, -half], "b": [half, -half], "c": [half, half],
                   "d": [-half, half]},
        "lines": {"s": ["a", "b"], "e": ["b", "c"], "n": ["c", "d"], "w": ["d", "a"]},
        "constraints": [{"type": "fix", "point": "a", "at": [-half, -half]},
                        {"type": "horizontal", "line": "s"},
                        {"type": "vertical", "line": "e"},
                        {"type": "horizontal", "line": "n"},
                        {"type": "vertical", "line": "w"},
                        {"type": "distance", "points": ["a", "b"], "value": size},
                        {"type": "distance", "points": ["b", "c"], "value": size}]})


def plate_with_pocket(**pocket_args):
    doc = Document(features=[
        Feature("base", "box", {"size": [60, 40, 12], "at": [0, 0, 0], "centred": False}),
        rectangle("p", {"body": "base", "face": "base/+z"}, 5, 10),
        Feature("cut", "pocket", {"body": "base", "sketch": "p", **pocket_args})],
        result="cut")
    return Evaluator(doc).build()


def test_a_pocket_can_go_through_everything():
    body = plate_with_pocket(until="through_all")
    assert kernel.volume(body) == pytest.approx(60 * 40 * 12 - 10 * 10 * 12, abs=0.5)
    # nothing left to be a floor: it went through
    assert "cut/floor" not in describe(body)["face_names"]


def test_a_symmetric_pocket_straddles_its_sketch():
    body = plate_with_pocket(depth=6, symmetric=True)
    assert kernel.volume(body) == pytest.approx(60 * 40 * 12 - 10 * 10 * 3, abs=0.5)


def test_to_next_stops_at_the_face_it_finds():
    """A window through the wall of a shelled box stops at the inside."""
    doc = Document(features=[
        Feature("blk", "box", {"size": [60, 40, 30], "at": [0, 0, 0], "centred": False}),
        Feature("hollow", "shell", {"body": "blk", "open": ["blk/+z"], "thickness": 3}),
        rectangle("win", {"body": "hollow", "face": "blk/+y"}, 6, 12),
        Feature("window", "pocket", {"body": "hollow", "sketch": "win", "until": "next"})],
        result="window")
    body = Evaluator(doc).build()
    hollow = 60 * 40 * 30 - 54 * 34 * 27
    assert kernel.volume(body) == pytest.approx(hollow - 12 * 12 * 3, abs=1.0)


def test_an_end_condition_that_cannot_be_met_is_refused():
    doc = Document(features=[
        rectangle("p", None, 5, 10),
        Feature("solid", "extrude", {"sketch": "p", "until": "through_all"})],
        result="solid")
    with pytest.raises(CadError) as exc:
        Evaluator(doc).build()
    assert exc.value.kind == "bad_arguments"

    doc = Document(features=[
        Feature("base", "box", {"size": [60, 40, 12], "at": [0, 0, 0], "centred": False}),
        rectangle("p", {"body": "base", "face": "base/+z"}, 5, 10),
        Feature("cut", "pocket", {"body": "base", "sketch": "p", "until": "sideways"})],
        result="cut")
    with pytest.raises(CadError) as exc:
        Evaluator(doc).build()
    assert exc.value.kind == "unknown_end_condition"


def test_a_variable_fillet_removes_more_than_its_smaller_radius():
    blk = kernel.box("b", [40, 30, 20])
    edge = ["b/+x|b/+z"]
    small = kernel.volume(kernel.fillet("f", blk, edge, 2))
    varied = kernel.volume(kernel.fillet("f", blk, edge, [2, 8]))
    assert varied < small
    with pytest.raises(CadError) as exc:
        kernel.fillet("f", blk, edge, [2, 8, 4])
    assert exc.value.kind == "bad_parameter"


def test_an_axis_can_be_taken_from_a_bore_and_patterned_about():
    doc = Document(parameters={"n": 6}, features=[
        Feature("plate", "cylinder", {"radius": 40, "height": 8, "at": [0, 0, 0],
                                      "centred": False}),
        Feature("bore", "cylinder", {"radius": 8, "height": 40, "at": [0, 0, -10],
                                     "centred": False}),
        Feature("hub", "cut", {"target": "plate", "tool": "bore"}),
        Feature("spin", "axis", {"body": "hub", "face": "bore/side"}),
        Feature("pin", "cylinder", {"radius": 3, "height": 40, "at": [25, 0, -10],
                                    "centred": False}),
        Feature("pins", "pattern", {"body": "pin", "count": "n", "axis": "spin"}),
        Feature("drilled", "cut", {"target": "hub", "tool": "pins"})],
        result="drilled")
    body = Evaluator(doc).build()
    expected = math.pi * (1600 - 64) * 8 - 6 * math.pi * 9 * 8
    assert kernel.volume(body) == pytest.approx(expected, abs=1.0)
    assert "pin/side~5" in describe(body)["face_names"]

    doc.features[3].args = {"body": "hub", "face": "plate/+z"}      # not round
    with pytest.raises(CadError) as exc:
        Evaluator(doc).build()
    assert exc.value.kind == "not_a_cylinder"


def test_a_slot_is_a_centreline_and_a_width():
    doc = Document(parameters={"L": 40, "W": 12, "t": 6}, features=[
        Feature("profile", "sketch", {
            "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
            "points": {"a": [0, 0], "b": [40, 0]},
            "slots": {"slot": {"from": "a", "to": "b", "width": "W"}},
            "constraints": [{"type": "fix", "point": "a", "at": [0, 0]},
                            {"type": "fix", "point": "b", "at": ["L", 0]}]}),
        Feature("bar", "extrude", {"sketch": "profile", "distance": "t"})],
        result="bar")
    body = Evaluator(doc).build()
    # a rectangle plus two half discs -- the ends bulge out, which is the whole
    # question a slot asks of the arc direction
    assert kernel.volume(body) == pytest.approx((40 * 12 + math.pi * 36) * 6, abs=0.5)
    names = describe(body)["face_names"]
    assert {"bar/slot/left", "bar/slot/right", "bar/slot/start", "bar/slot/end"} <= set(names)


def test_symmetry_and_midpoint_constraints():
    doc = Document(parameters={"W": 30, "H": 20, "t": 4}, features=[
        Feature("profile", "sketch", {
            "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
            "points": {"a": [-15, 0], "b": [15, 0], "c": [15, 20], "d": [-15, 20],
                       "o": [0, 0]},
            "lines": {"s": ["a", "b"], "e": ["b", "c"], "n": ["c", "d"], "w": ["d", "a"]},
            "constraints": [
                {"type": "fix", "point": "o", "at": [0, 0]},
                {"type": "horizontal", "line": "s"}, {"type": "vertical", "line": "e"},
                {"type": "horizontal", "line": "n"}, {"type": "vertical", "line": "w"},
                {"type": "symmetric", "points": ["a", "b"], "about": "o"},
                {"type": "distance", "points": ["a", "b"], "value": "W"},
                {"type": "distance", "points": ["b", "c"], "value": "H"}]}),
        Feature("plate", "extrude", {"sketch": "profile", "distance": "t"})],
        result="plate")
    body = Evaluator(doc).build()
    assert kernel.volume(body) == pytest.approx(30 * 20 * 4, abs=0.5)
    # symmetric about the fixed origin: the profile is centred on it
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box = Bnd_Box()
    BRepBndLib.Add_s(body.shape, box)
    assert bounds(box)[0] == pytest.approx(-15, abs=0.01)
    assert bounds(box)[3] == pytest.approx(15, abs=0.01)


# --- the rest of the vocabulary --------------------------------------------------

def test_a_rib_is_a_wall_grown_from_a_line():
    doc = Document(parameters={"t": 3, "drop": 25}, features=[
        Feature("base", "box", {"size": [60, 40, 8], "at": [0, 0, 0], "centred": False}),
        Feature("wall", "box", {"size": [8, 40, 40], "at": [0, 0, 0], "centred": False}),
        Feature("angle", "fuse", {"target": "base", "tool": "wall"}),
        Feature("line", "sketch", {
            "open": True,
            "plane": {"origin": [0, 20, 0], "normal": [0, 1, 0], "x_axis": [1, 0, 0]},
            "points": {"a": [8, 40], "b": [40, 8]},
            "lines": {"run": ["a", "b"]},
            "constraints": [{"type": "fix", "point": "a", "at": [8, 40]},
                            {"type": "fix", "point": "b", "at": [40, 8]}]}),
        Feature("stiffener", "rib", {"body": "angle", "sketch": "line",
                                     "thickness": "t", "depth": "drop"})],
        result="stiffener")
    body = Evaluator(doc).build()
    plain = 60 * 40 * 8 + 8 * 40 * 32
    assert kernel.volume(body) > plain              # it added a wall
    assert kernel.volume(body) < plain + 3 * 32 * 45  # and only a wall

    doc.parameters["t"] = 6
    assert kernel.volume(Evaluator(doc).build()) > kernel.volume(body)


def test_a_spline_runs_through_points_that_are_dimensions():
    doc = Document(parameters={"t": 5, "rise": 25}, features=[
        Feature("profile", "sketch", {
            "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
            "points": {"a": [0, 0], "b": [80, 0], "c": [80, 20], "m1": [55, 32],
                       "m2": [25, 14], "d": [0, 20]},
            "lines": {"south": ["a", "b"], "east": ["b", "c"], "west": ["d", "a"]},
            "splines": {"crest": {"through": ["c", "m1", "m2", "d"]}},
            "constraints": [{"type": "fix", "point": "a", "at": [0, 0]},
                            {"type": "fix", "point": "b", "at": [80, 0]},
                            {"type": "fix", "point": "c", "at": [80, 20]},
                            {"type": "fix", "point": "d", "at": [0, 20]},
                            {"type": "fix", "point": "m1", "at": [55, "rise"]},
                            {"type": "fix", "point": "m2", "at": [25, 14]}]}),
        Feature("cam", "extrude", {"sketch": "profile", "distance": "t"})],
        result="cam")
    body = Evaluator(doc).build()
    assert "cam/crest" in describe(body)["face_names"]
    low = kernel.volume(body)
    doc.parameters["rise"] = 40
    assert kernel.volume(Evaluator(doc).build()) > low     # the point is a dimension


def test_a_pattern_can_follow_a_path():
    doc = Document(parameters={"n": 5}, features=[
        Feature("plate", "box", {"size": [120, 40, 8], "at": [0, 0, 0], "centred": False}),
        Feature("route", "sketch", {
            "open": True,
            "plane": {"origin": [0, 0, 8], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
            "points": {"p0": [15, 10], "p1": [60, 30], "p2": [105, 10]},
            "lines": {"in": ["p0", "p1"], "out": ["p1", "p2"]},
            "constraints": [{"type": "fix", "point": "p0", "at": [15, 10]},
                            {"type": "fix", "point": "p1", "at": [60, 30]},
                            {"type": "fix", "point": "p2", "at": [105, 10]}]}),
        Feature("pin", "cylinder", {"radius": 3, "height": 40, "at": [15, 10, -16],
                                    "centred": False}),
        Feature("pins", "pattern", {"body": "pin", "count": "n", "path": "route"}),
        Feature("drilled", "cut", {"target": "plate", "tool": "pins"})],
        result="drilled")
    body = Evaluator(doc).build()
    assert kernel.volume(body) == pytest.approx(120 * 40 * 8 - 5 * math.pi * 9 * 8, abs=1.0)
    assert "pin/side~4" in describe(body)["face_names"]


def test_a_shell_can_have_a_heavier_wall():
    """One floor thicker than the sides -- and the uniform case still agrees
    with the builder that only knows one thickness."""
    box = kernel.box("b", [40, 30, 20], (0, 0, 0), centred=False)
    uniform = kernel.volume(kernel.shell("s", box, ["b/+z"], 2))
    assert kernel.volume(kernel.shell("s", box, ["b/+z"],
                                      {"default": 2, "faces": {}})) == \
        pytest.approx(uniform, abs=0.5)
    heavier = kernel.shell("s", box, ["b/+z"], {"default": 2, "faces": {"b/-x": 4}})
    assert kernel.volume(heavier) == pytest.approx(8088, abs=1.0)
    assert "b/-x" in heavier.face_names()


def test_iges_goes_out_and_comes_back(tmp_path):
    from cadcore import Document as Doc

    body = Evaluator(Doc.load("examples/flange.json")).build()
    path = str(tmp_path / "flange.igs")
    kernel.export_iges(body, path)
    back = kernel.import_iges("back", path)
    assert len(back.names) == len(body.names)
    assert kernel.volume(back) == pytest.approx(kernel.volume(body), rel=1e-3)


def test_a_drawing_can_be_dxf():
    from cadcore.analysis.drawing import dxf
    from cadcore import Document as Doc

    doc = Doc.load("examples/flange.json")
    text = dxf(Evaluator(doc).build(), doc.drawing)
    assert text.startswith("999\nRealParts") and text.rstrip().endswith("EOF")
    assert "AC1015" in text and "LWPOLYLINE" in text
    assert text.count("SECTION") == text.count("ENDSEC")
    for layer in ("VISIBLE", "HIDDEN"):
        assert layer in text


def test_a_rib_straddles_its_line_whichever_way_the_line_was_drawn():
    """`MakeThickSolidBySimple` offsets along the surface's own normal, and a
    surface swept from a chain drawn right to left has the opposite one. The
    step back to centre it was a fixed half thickness along the plane's normal,
    so half the ribs in the world sat beside their line by a whole thickness.
    """
    import copy

    from cadcore.geometry import kernel
    from cadcore.model.document import Document
    from cadcore.evaluation.graph import Evaluator
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    base = {"meta": {"name": "a sketched line"}, "parameters": {},
            "features": [
                {"id": "line", "type": "sketch",
                 "plane": {"origin": [0, 0, 0], "normal": [0, 1, 0],
                           "x_axis": [1, 0, 0]},
                 "points": {"a": [0, 0], "b": [40, 0]},
                 "lines": {"run": ["a", "b"]},
                 "open": True, "allow_underconstrained": True}],
            "result": "line"}

    spans = {}
    for label, drawn in (("forwards", ["a", "b"]), ("backwards", ["b", "a"])):
        raw = copy.deepcopy(base)
        raw["features"][0]["lines"] = {"run": drawn}
        evaluator = Evaluator(Document.from_dict(raw))
        evaluator._eval(evaluator.doc.feature("line"))
        body = kernel.rib("r", evaluator.sketches["line"], 4.0, 20.0, (0.0, -1.0))
        box = Bnd_Box()
        BRepBndLib.AddOptimal_s(body.shape, box)
        spans[label] = (round(bounds(box)[1], 6), round(bounds(box)[4], 6))

    assert spans["forwards"] == spans["backwards"], spans
    assert spans["forwards"] == (-2.0, 2.0), \
        "a 4 mm rib on y=0 reaches 2 mm each side of it"


def test_a_loft_will_not_quietly_drop_the_hole_in_its_section():
    """`AddWire` takes one wire per section, so a profile with a hole in it was
    lofted as though the hole were not there. A 20x20 square with an 8x8 hole
    extrudes to 3360 mm3 and lofted to 4000, silently."""
    import copy

    import pytest

    from cadcore.evaluation.api import build, describe
    from cadcore.model.document import Document
    from cadcore.errors import CadError

    ring = {"points": {"a": [-10, -10], "b": [10, -10], "c": [10, 10],
                       "d": [-10, 10], "p": [-4, -4], "q": [4, -4],
                       "r": [4, 4], "s": [-4, 4]},
            "lines": {"s1": ["a", "b"], "s2": ["b", "c"], "s3": ["c", "d"],
                      "s4": ["d", "a"], "h1": ["p", "q"], "h2": ["q", "r"],
                      "h3": ["r", "s"], "h4": ["s", "p"]},
            "allow_underconstrained": True}
    plane = {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]}
    top = {"origin": [0, 0, 10], "normal": [0, 0, 1], "x_axis": [1, 0, 0]}

    extruded = {"parameters": {}, "result": "out", "features": [
        dict({"id": "sk", "type": "sketch", "plane": plane}, **ring),
        {"id": "out", "type": "extrude", "sketch": "sk", "distance": 10.0}]}
    assert describe(build(Document.from_dict(copy.deepcopy(extruded)))[0])[
        "volume_mm3"] == pytest.approx(20 * 20 * 10 - 8 * 8 * 10)

    lofted = {"parameters": {}, "result": "out", "features": [
        dict({"id": "sk", "type": "sketch", "plane": plane}, **ring),
        dict({"id": "sk2", "type": "sketch", "plane": top}, **ring),
        {"id": "out", "type": "loft", "sketches": ["sk", "sk2"]}]}
    with pytest.raises(CadError) as caught:
        build(Document.from_dict(lofted))
    assert caught.value.kind == "bad_profile"
    assert "hole" in caught.value.message



def test_a_part_can_be_started_from_nothing_on_a_ground_plane():
    """New document, ground plane, profile, extrude: the way a part begins.

    Every placement of a sketch needed a face, and a new document has none;
    and a document whose only features were a plane and a sketch was refused
    with `no_solid` on every add. So the way to start a part was to already
    have one. A document with no body yet is under construction, not wrong.
    """
    from cadcore.service.server import Session

    s = Session(autosave=False)
    s.op_new_document()
    with pytest.raises(CadError) as refused:
        s.op_add_plane()
    assert refused.value.kind == "bad_arguments"

    plane = s.op_add_plane(on="xy")
    assert plane["feature"] == "plane1"
    assert plane["under_construction"] and plane["faces"] == 0
    assert plane["frame"]["normal"] == [0.0, 0.0, 1.0]
    assert s.doc.result is None

    sketch = s.op_add_profile(shape="rect", plane="plane1", width=40, height=20)
    assert sketch["under_construction"]
    # an operation that needs a body says what is missing, by kind
    with pytest.raises(CadError) as refused:
        s.op_face_frame(face="rect1/top")
    assert refused.value.kind == "no_solid"

    built = s.op_add_feature("extrude", {"sketch": sketch["feature"], "distance": 10})
    assert built["volume_mm3"] == pytest.approx(8000.0)
    assert s.doc.result == built["feature"]

    # and back: undo returns to under construction, redo to the part
    assert s.op_undo()["under_construction"]
    assert s.op_redo()["volume_mm3"] == pytest.approx(8000.0)


def test_the_other_ground_planes_face_the_way_a_drawing_does():
    """xz faces -y so its x is x and its y is up; yz faces +x."""
    from ..subjects.modelling import GROUND_PLANES
    from cadcore.service.server import Session

    assert set(GROUND_PLANES) == {"xy", "xz", "yz"}
    s = Session(autosave=False)
    s.op_new_document()
    plane = s.op_add_plane(on="xz", offset=5)
    assert plane["frame"]["origin"] == [0.0, -5.0, 0.0]
    assert plane["frame"]["normal"] == [0.0, -1.0, 0.0]
    disc = s.op_add_profile(shape="circle", plane=plane["feature"], radius=10)
    built = s.op_add_feature("extrude", {"sketch": disc["feature"], "distance": 30})
    assert built["volume_mm3"] == pytest.approx(math.pi * 100 * 30, rel=1e-3)
    with pytest.raises(CadError) as refused:
        s.op_add_plane(on="uv")
    assert refused.value.kind == "bad_arguments"


def test_a_datum_added_first_does_not_become_the_result():
    """`add_feature` made whatever came first the result, a plane included,
    and the document then could not build. The result is the last thing that
    makes a body."""
    from cadcore.service.server import Session

    s = Session(autosave=False)
    s.op_new_document()
    s.op_add_feature("plane", {"origin": [0, 0, 0], "normal": [0, 0, 1]})
    assert s.doc.result is None
    box = s.op_add_feature("box", {"size": [10, 20, 30]})
    assert s.doc.result == box["feature"]
    assert box["volume_mm3"] == pytest.approx(6000.0)


def test_a_document_with_no_solid_yet_still_has_something_to_draw():
    """Remove the extrude and the sketch it was made from has to stay on
    screen. The tessellation of an unbuilt document is its sketches as named
    polylines and each work plane as a square about its origin."""
    from cadcore.service.server import Session

    s = Session(autosave=False)
    s.op_new_document()
    s.op_add_plane(on="xz", feature_id="plane1")
    s.op_add_profile(shape="circle", plane="plane1", radius=5, feature_id="disc")
    wire = s.op_tessellate()
    assert wire["under_construction"] and wire["triangles"] == []
    names = sorted(wire["edges"])
    assert names == ["disc/round", "plane1"], names
    # the circle is sampled round, on the plane: every point has y == 0
    assert len(wire["edges"]["disc/round"]) == 49
    assert all(abs(p[1]) < 1e-9 for p in wire["edges"]["disc/round"])
    assert wire["edges"]["plane1"][0] == wire["edges"]["plane1"][-1]      # closed
    # and once there is a solid, the tessellation is the solid's
    s.op_add_feature("extrude", {"sketch": "disc", "distance": 10})
    assert len(s.op_tessellate()["triangles"]) > 0



def test_rolling_back_to_a_plane_shows_the_plane_and_the_sketch():
    """Roll Back Here on the plane row made every rebuild fail with
    `no_solid`, and Blender's undo could not follow. The view up to a feature
    that makes no body is the same under-construction view a new document
    has."""
    from cadcore.service.server import Session

    s = Session(autosave=False)
    s.op_new_document()
    s.op_add_plane(on="xy", feature_id="plane1")
    s.op_add_profile(shape="rect", plane="plane1", width=40, height=20, feature_id="rect1")
    s.op_add_feature("extrude", {"sketch": "rect1", "distance": 10})

    back = s.op_rollback("plane1")
    assert back["under_construction"] and back["rolled_back_to"] == "plane1"
    assert sorted(s.op_tessellate()["edges"]) == ["plane1"]           # the sketch is later
    back = s.op_rollback("rect1")
    assert sorted(s.op_tessellate()["edges"])[:2] == ["plane1", "rect1/s0"]
    # a rebuild while rolled back stays there and stays calm
    assert s.op_set_parameter(name="plane1_offset", value=3)["under_construction"]
    forward = s.op_rollback(None)
    assert forward["volume_mm3"] == pytest.approx(8000.0)


def test_a_sketch_can_be_drawn_on_a_work_plane():
    """The viewport's drawn sketch could only sit on a face. On a work plane
    it sits the same way, and on an empty document it extrudes a solid."""
    from cadcore.service.server import Session

    s = Session(autosize=False) if False else Session(autosave=False)
    s.op_new_document()
    s.op_add_plane(on="xz", offset=4, feature_id="plane1")
    planes = s.op_planes()["planes"]
    assert list(planes) == ["plane1"] and planes["plane1"]["origin"] == [0.0, -4.0, 0.0]
    assert s.op_plane_frame("plane1")["normal"] == [0.0, -1.0, 0.0]
    with pytest.raises(CadError) as refused:
        s.op_add_sketch(points=[[0, 0], [10, 0]], lines=[[0, 1]])
    assert refused.value.kind == "bad_arguments"
    out = s.op_add_sketch(plane="plane1", points=[[-10, -5], [10, -5], [10, 5], [-10, 5]],
                          lines=[[0, 1], [1, 2], [2, 3], [3, 0]], operation="extrude", depth=6)
    assert out["volume_mm3"] == pytest.approx(20 * 10 * 6)
    assert out["on_plane"] == "plane1" and s.doc.feature(out["feature"] + "_profile").args["on"] == {"plane": "plane1"}


def test_a_ground_plane_can_be_tipped_turned_or_given():
    from cadcore.service.server import Session

    s = Session(autosave=False)
    s.op_new_document()
    tipped = s.op_add_plane(on="xy", tilt=30, feature_id="p1")["frame"]
    assert [round(c, 3) for c in tipped["normal"]] == [0.0, -0.5, 0.866]
    turned = s.op_add_plane(on="xy", turn=45, feature_id="p2")["frame"]
    assert [round(c, 3) for c in turned["x_axis"]] == [0.707, 0.707, 0.0]
    given = s.op_add_plane(normal=[0, 1, 1], origin=[0, 0, 5], feature_id="p3")["frame"]
    assert given["origin"] == [0.0, 0.0, 5.0]
    # planes nothing uses yet are still listed, and can be drawn on
    s.op_add_feature("box", {"size": [10, 10, 10]})
    assert sorted(s.op_planes()["planes"]) == ["p1", "p2", "p3"]
    sketch = s.op_add_profile(shape="rect", plane="p1", width=10, height=6)
    assert s.op_add_feature("extrude", {"sketch": sketch["feature"], "distance": 3})["volume_mm3"] == pytest.approx(180)


def test_a_work_plane_hangs_on_an_edge_and_turns_about_it():
    """An angled plane keeps the edge it was hung on.

    Given as a frame it would be a set of numbers that stop meaning anything
    the moment the face moves. Hung on the edge it stays touching the part,
    and its angle is a parameter like any other.
    """
    from cadcore.service.server import Session

    s = Session(autosave=False)
    s.op_new_document()
    disc = s.op_add_profile(shape="rect", plane=s.op_add_plane(on="xy")["feature"],
                            width=40, height=20)
    s.op_add_feature("extrude", {"sketch": disc["feature"], "distance": 10})
    top = next(f["name"] for f in s.op_describe_faces(query={"shape": "plane"})["faces"]
               if f["normal"] == [0.0, 0.0, 1.0])
    edge = s.op_select_edges(query={"of_face": top})["edges"][0]

    flat = s.op_add_plane(face=top, offset=0.0)["frame"]
    turned = s.op_add_plane(face=top, about=edge, angle=30.0)
    assert sum(a * b for a, b in zip(turned["frame"]["normal"], flat["normal"])) == \
        pytest.approx(math.cos(math.radians(30.0)))
    # square to its own normal still, or a sketch on it comes out sheared
    assert sum(a * b for a, b in zip(turned["frame"]["normal"],
                                     turned["frame"]["x_axis"])) == pytest.approx(0.0, abs=1e-9)

    # the angle is a parameter, so it is retyped rather than rebuilt
    name = "%s_angle" % turned["feature"]
    assert name in s.doc.parameters
    s.op_set_parameter(name=name, value=60.0)
    again = s.op_plane_frame(plane=turned["feature"])
    assert sum(a * b for a, b in zip(again["normal"], flat["normal"])) == \
        pytest.approx(math.cos(math.radians(60.0)))

    # and it turns about *that* edge: the origin is on it, not in the middle
    assert again["origin"] != flat["origin"]


def test_a_plane_needs_a_straight_edge_to_turn_about():
    from cadcore.geometry.core.measure import edge_axis
    from cadcore.service.server import Session

    s = Session(autosave=False)
    s.op_new_document()
    disc = s.op_add_profile(shape="circle", plane=s.op_add_plane(on="xy")["feature"],
                            radius=10)
    s.op_add_feature("extrude", {"sketch": disc["feature"], "distance": 10})
    body = s.evaluator.body_of(s.doc.result)
    round_edge = next(name for name in body.edge_table())
    with pytest.raises(CadError) as refused:
        edge_axis(body, round_edge)
    assert refused.value.kind == "bad_arguments"
    assert "straight" in refused.value.message

    with pytest.raises(CadError) as refused:
        s.op_add_plane(about="anything", angle=10.0)
    assert refused.value.kind == "bad_arguments"
    assert "which face" in refused.value.message
