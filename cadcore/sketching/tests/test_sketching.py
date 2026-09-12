"""Drawing on a face: rough input in, dimensioned parametric sketch out."""
import math
import pytest

from cadcore.geometry.kernel import CadError
from cadcore.service.server import Session
from conftest import plate
from cadcore.geometry.core.occ import bounds

ROUGH = [[-12.3, -8.1], [17.6, -7.9], [17.4, 6.2], [-12.1, 6.0]]
RECT = [[0, 1], [1, 2], [2, 3], [3, 0]]


@pytest.fixture()
def session():
    s = Session()
    s.op_open("examples/sketch_plate.json")
    s.op_build()
    return s


def test_a_rough_rectangle_becomes_two_dimensions(session):
    """Four sides, but only two independent lengths -- and the solver decides
    that, not a rule about rectangles."""
    base = session.op_build()["volume_mm3"]
    out = session.op_add_sketch("plate/top", ROUGH, RECT, operation="pocket", depth=3)
    assert sorted(out["dimensions"]) == ["pocket1_east", "pocket1_north"]
    assert out["dimensions"]["pocket1_east"] == 30.0        # snapped to 0.5 mm
    assert out["dimensions"]["pocket1_north"] == 14.0
    assert out["volume_mm3"] == pytest.approx(base - 30 * 14 * 3, abs=0.5)


def test_the_drawn_segments_are_named_and_axis_aligned(session):
    session.op_add_sketch("plate/top", ROUGH, RECT, operation="pocket", depth=3)
    names = session.op_tessellate()["face_table"]
    assert {"pocket1/east", "pocket1/north", "pocket1/west", "pocket1/south",
            "pocket1/floor"} <= set(names)
    sketch = next(f for f in session.op_describe_document()["features"]
                  if f["id"] == "pocket1_profile")
    kinds = [c["type"] for c in sketch["args"]["constraints"]]
    assert kinds.count("horizontal") == 2 and kinds.count("vertical") == 2
    # the sketch is placed on the face by name, so it follows it
    assert sketch["args"]["on"]["face"] == "plate/top"


def test_the_dimensions_drive_the_model_afterwards(session):
    out = session.op_add_sketch("plate/top", ROUGH, RECT, operation="pocket", depth=3)
    before = out["volume_mm3"]
    after = session.op_set_parameter("pocket1_east", 40)["volume_mm3"]
    assert before - after == pytest.approx(10 * 14 * 3, abs=0.5)


def test_a_circle_becomes_a_radius_dimension(session):
    out = session.op_add_sketch("plate/top", ROUGH + [[2.0, -1.0]], RECT,
                                circles=[{"centre": 4, "radius": 3.0}],
                                operation="pocket", depth=3)
    assert out["dimensions"]["pocket1_hole_r"] == 3.0
    assert "pocket1/hole" in session.op_tessellate()["face_table"]


def test_a_boss_can_be_drawn_too(session):
    base = session.op_build()["volume_mm3"]
    out = session.op_add_sketch("plate/top", ROUGH, RECT, operation="boss", depth=4)
    assert out["volume_mm3"] == pytest.approx(base + 30 * 14 * 4, abs=0.5)


def test_nonsense_is_refused_before_it_reaches_the_document(session):
    before = session.op_describe_document()
    with pytest.raises(CadError) as exc:
        session.op_add_sketch("plate/top", [[0, 0]], [], operation="pocket", depth=3)
    assert exc.value.kind == "empty_sketch"
    with pytest.raises(CadError) as exc:
        session.op_add_sketch("rounded/side", ROUGH, RECT, operation="pocket", depth=3)
    assert exc.value.kind == "non_planar_face"
    with pytest.raises(CadError) as exc:
        session.op_add_sketch("plate/top", ROUGH, RECT, operation="carve", depth=3)
    assert exc.value.kind == "bad_arguments"
    assert session.op_describe_document() == before


# --- work planes ---------------------------------------------------------------

def rectangle(name, on, half, size):
    from cadcore.model.document import Feature
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


def lid_document(gap=14):
    from cadcore.model.document import Document, Feature

    return Document(
        parameters={"gap": gap},
        features=[
            Feature("base", "box", {"size": [60, 60, 10], "at": [0, 0, 0],
                                    "centred": False}),
            Feature("up", "plane", {"from": {"body": "base", "face": "base/+z"},
                                    "offset": "gap"}),
            rectangle("lid_profile", {"plane": "up"}, 15, 30),
            Feature("lid", "extrude", {"sketch": "lid_profile", "distance": 6})],
        result="lid")


def test_a_sketch_can_sit_on_a_work_plane():
    from cadcore import describe
    from cadcore.evaluation.graph import Evaluator
    from OCP.BRepBndLib import BRepBndLib
    from OCP.Bnd import Bnd_Box

    body = Evaluator(lid_document()).build()
    assert describe(body)["volume_mm3"] == pytest.approx(30 * 30 * 6, abs=0.5)
    box = Bnd_Box()
    BRepBndLib.Add_s(body.shape, box)
    zmin, zmax = bounds(box)[2], bounds(box)[5]
    assert (round(zmin), round(zmax)) == (24, 30)      # 10 mm plate + a 14 mm gap


def test_the_plane_follows_the_face_it_was_offset_from():
    from cadcore.evaluation.graph import Evaluator
    from OCP.BRepBndLib import BRepBndLib
    from OCP.Bnd import Bnd_Box

    doc = lid_document(gap=25)
    body = Evaluator(doc).build()
    box = Bnd_Box()
    BRepBndLib.Add_s(body.shape, box)
    assert round(bounds(box)[2]) == 35                   # the gap drove it


def test_a_sketch_on_a_plane_that_does_not_exist_is_refused():
    from cadcore.evaluation.graph import Evaluator
    from cadcore.geometry.kernel import CadError

    doc = lid_document()
    doc.features[2].args["on"] = {"plane": "nowhere"}
    with pytest.raises(CadError) as exc:
        Evaluator(doc).build()
    assert exc.value.kind in ("unknown_plane", "unknown_feature")


def test_a_plane_can_be_added_to_an_open_document(session):
    out = session.op_add_plane(face="plate/top", offset=12)
    assert out["frame"]["normal"] == [0.0, 0.0, 1.0]
    assert out["frame"]["origin"][2] == pytest.approx(8 + 12)
    middle = session.op_add_plane(between=["plate/top", "plate/bottom"])
    assert middle["frame"]["origin"][2] == pytest.approx(4.0)
    with pytest.raises(CadError) as exc:
        session.op_add_plane()
    assert exc.value.kind == "bad_arguments"


# --- references into the sketch: offsets and projections -----------------------

def strip_document(wall=4.0):
    from cadcore.model.document import Document, Feature

    return Document(parameters={"L": 60, "wall": wall, "t": 5}, features=[
        Feature("profile", "sketch", {
            "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
            "points": {"a": [0, 0], "b": [60, 0]},
            "lines": {"south": ["a", "b"], "east": ["b", "north_b"],
                      "west": ["north_a", "a"]},
            "offsets": {"north": {"of": "south", "distance": "wall", "side": "left"}},
            "constraints": [{"type": "fix", "point": "a", "at": [0, 0]},
                            {"type": "horizontal", "line": "south"},
                            {"type": "distance", "points": ["a", "b"], "value": "L"}]}),
        Feature("strip", "extrude", {"sketch": "profile", "distance": "t"})],
        result="strip")


def test_an_offset_is_a_relationship_not_a_copy():
    from cadcore import describe
    from cadcore.geometry import kernel
    from cadcore.evaluation.graph import Evaluator

    body = Evaluator(strip_document(4)).build()
    assert kernel.volume(body) == pytest.approx(60 * 4 * 5, abs=0.5)
    assert "strip/north" in describe(body)["face_names"]      # the offset is a face
    # change the distance and the line follows: it was never a copy
    assert kernel.volume(Evaluator(strip_document(7)).build()) == \
        pytest.approx(60 * 7 * 5, abs=0.5)


def counterbore_document(bore=8.0, at_x=40.0):
    from cadcore.model.document import Document, Feature

    return Document(parameters={"bore": bore, "seat": 3}, features=[
        Feature("plate", "box", {"size": [80, 60, 10], "at": [0, 0, 0], "centred": False}),
        Feature("tool", "cylinder", {"radius": "bore", "height": 40,
                                     "at": [at_x, 30, -5], "centred": False}),
        Feature("bored", "cut", {"target": "plate", "tool": "tool"}),
        Feature("ring", "sketch", {
            "on": {"body": "bored", "face": "plate/+z"},
            "projections": {"rim": {"edge": "plate/+z|tool/side"}},
            "circles": {"seat_circle": {"centre": "rim_c", "radius": "bore + seat"}},
            "points": {}, "constraints": []}),
        Feature("seat", "pocket", {"body": "bored", "sketch": "ring", "depth": "seat"})],
        result="seat")


def test_a_projected_edge_stays_tied_to_what_it_came_from():
    """A counterbore concentric with a bore -- because its centre *is* the bore."""
    import math

    from cadcore.geometry import kernel
    from cadcore.evaluation.graph import Evaluator

    def removed(bore, at_x=40.0):
        body = Evaluator(counterbore_document(bore, at_x)).build()
        return 80 * 60 * 10 - math.pi * bore ** 2 * 10 - kernel.volume(body)

    for bore, at_x in ((8, 40), (11, 40), (8, 25)):
        assert removed(bore, at_x) == pytest.approx(
            math.pi * ((bore + 3) ** 2 - bore ** 2) * 3, abs=1.0)


def test_projecting_an_edge_that_is_gone_is_refused():
    from cadcore.evaluation.graph import Evaluator
    from cadcore.geometry.kernel import CadError

    doc = counterbore_document()
    doc.features[3].args["projections"]["rim"]["edge"] = "plate/+z|nothing"
    with pytest.raises(CadError) as exc:
        Evaluator(doc).build()
    assert exc.value.kind == "unresolved_reference"


# --- dragging ------------------------------------------------------------------

def test_dragging_moves_what_is_free(open_document):
    document = plate()
    document["features"][0]["constraints"].append({"type": "horizontal", "line": "s"})
    session = open_document(document)
    assert session.op_build()["volume_mm3"] == pytest.approx(4000)

    out = session.op_drag_point("profile", "c", [52.0, 26.0])
    assert out["held"] is False and out["moved"]["c"] == pytest.approx([52.0, 26.0])
    assert session.op_build()["volume_mm3"] == pytest.approx(5200)
    assert session.op_undo()["volume_mm3"] == pytest.approx(4000)


def test_geometry_says_which_points_a_drag_could_move(open_document):
    """The freedom map has to agree with what dragging actually does."""
    document = plate()
    document["features"][0]["constraints"].append(
        {"type": "fix", "point": "b", "at": [40, 0]})
    session = open_document(document)
    free = session.op_sketch_geometry("profile")["free"]
    assert free == {"a": False, "b": False, "c": True, "d": True}

    assert session.op_drag_point("profile", "a", [3.0, 3.0])["held"] is True
    assert session.op_drag_point("profile", "c", [44.0, 24.0])["held"] is False


def test_geometry_calls_a_fully_constrained_sketch_immovable(session):
    """Every point of a dimensioned sketch is held -- the panel can say so first."""
    geometry = session.op_sketch_geometry("profile")
    assert geometry["editable"] is False
    assert set(geometry["free"].values()) == {False}


def test_dragging_a_held_point_changes_nothing_and_says_why(session):
    """A fully constrained sketch does not move, and the reply is useful anyway."""
    before = session.op_build()["volume_mm3"]
    out = session.op_drag_point("profile", "b", [95.0, 4.0])
    assert out["held"] is True and out["moved"] == {}
    # both of these hold b: the dimension names it, and the vertical names the
    # line it is an end of. Reporting only the first said "nothing else is
    # stopping this" about a point that a second constraint was also stopping.
    assert [c["type"] for c in out["held_by"]] == ["vertical", "distance"]
    # the parameters offered are the ones in the dimensions holding *this*
    # point -- b is fixed by "depth - notch", and width holds a different point.
    # Offering every parameter named anywhere in the sketch (which a substring
    # search over the whole constraint list did) points at the wrong number.
    assert out["parameters"] == ["depth", "notch"]
    assert session.op_build()["volume_mm3"] == pytest.approx(before)


def wedge_document(qy=40.0):
    from cadcore.model.document import Document, Feature

    return Document(parameters={"qy": qy, "t": 4}, features=[
        Feature("profile", "sketch", {
            "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
            "points": {"a": [0, 0], "b": [80, 0], "p": [50, -10], "q": [20, 40]},
            "lines": {"south": ["a", "b"], "slant": ["p", "q"], "west": ["q", "a"]},
            "trims": {"t1": {"of": "south", "at": "slant", "keep": "start"},
                      "t2": {"of": "slant", "at": "south", "keep": "end"}},
            "constraints": [{"type": "fix", "point": "a", "at": [0, 0]},
                            {"type": "fix", "point": "b", "at": [80, 0]},
                            {"type": "fix", "point": "p", "at": [50, -10]},
                            {"type": "fix", "point": "q", "at": [20, "qy"]}]}),
        Feature("wedge", "extrude", {"sketch": "profile", "distance": "t"})],
        result="wedge")


def test_a_trim_keeps_one_side_and_names_it_like_a_split():
    from cadcore import describe
    from cadcore.geometry import kernel
    from cadcore.evaluation.graph import Evaluator

    body = Evaluator(wedge_document(40)).build()
    names = describe(body)["face_names"]
    # the same suffix a boolean split uses, because it is the same event
    assert "wedge/south@0" in names and "wedge/slant@1" in names
    assert kernel.volume(body) == pytest.approx(0.5 * 44 * 40 * 4, abs=1.0)


def test_the_cut_point_re_solves_when_the_lines_move():
    """A trim is a relationship: move a line and the crossing follows."""
    from cadcore.geometry import kernel
    from cadcore.evaluation.graph import Evaluator

    crossing_x = 50 - 30 * (10 / 70)          # where the slant meets y = 0
    assert kernel.volume(Evaluator(wedge_document(60)).build()) == pytest.approx(
        0.5 * crossing_x * 60 * 4, abs=1.0)


def test_lines_that_do_not_cross_cannot_trim_each_other():
    from cadcore.evaluation.graph import Evaluator
    from cadcore.geometry.kernel import CadError

    doc = wedge_document()
    doc.features[0].args["trims"] = {"t1": {"of": "south", "at": "west", "keep": "start"}}
    doc.features[0].args["lines"]["west"] = ["q", "b"]      # now parallel to nothing
    doc.features[0].args["constraints"].append(
        {"type": "horizontal", "line": "west"})             # parallel to south
    with pytest.raises(CadError) as exc:
        Evaluator(doc).build()
    assert exc.value.kind in ("no_intersection", "open_profile", "sketch_unsolved")


def test_a_trim_needs_lines_that_exist():
    from cadcore.evaluation.graph import Evaluator
    from cadcore.geometry.kernel import CadError

    doc = wedge_document()
    doc.features[0].args["trims"]["t1"]["at"] = "nowhere"
    with pytest.raises(CadError) as exc:
        Evaluator(doc).build()
    assert exc.value.kind == "unknown_line"


# --- constraints as things the viewport can edit --------------------------------

def test_constraints_can_be_listed_added_and_taken_off(open_document):
    """The constraints are the design intent, so they are editable like one."""
    session = open_document(plate())

    listed = session.op_sketch_constraints("profile")
    assert [c["type"] for c in listed["constraints"]] == ["fix"]
    assert listed["free"]["b"] is True

    session.op_add_constraint("profile", {"type": "distance", "points": ["a", "b"],
                                          "value": 40})
    session.op_add_constraint("profile", {"type": "horizontal", "line": "s"})
    listed = session.op_sketch_constraints("profile")
    assert [c["type"] for c in listed["constraints"]] == ["fix", "distance", "horizontal"]

    # retyping the dimension drives the model, which is the point of having it.
    # Only b moves: nothing ties c to b, so the plate becomes a quadrilateral
    # rather than a wider rectangle -- the constraints decide, not the intent
    # someone had in their head.
    wider = session.op_set_constraint_value("profile", 1, 60)
    assert session.op_sketch_geometry("profile")["points"]["b"] == pytest.approx([60, 0])
    assert session.op_sketch_geometry("profile")["points"]["c"] == pytest.approx([40, 20])
    assert wider["volume_mm3"] == pytest.approx(5000)

    # taking the dimension off frees b again, and with nothing holding it the
    # sketch solves from the coordinates it was drawn at: 40, where b started.
    # A removed dimension is not a remembered one.
    session.op_remove_constraint("profile", 1)
    assert [c["type"] for c in session.op_sketch_constraints("profile")["constraints"]] \
        == ["fix", "horizontal"]
    assert session.op_build()["volume_mm3"] == pytest.approx(4000)


def test_a_constraint_that_will_not_solve_leaves_nothing_behind(open_document):
    """A refused constraint is a refused edit: the sketch is as it was."""
    session = open_document(plate())
    before = session.op_build()["volume_mm3"]

    with pytest.raises(CadError) as exc:
        session.op_add_constraint("profile", {"type": "no_such_thing", "line": "s"})
    assert exc.value.kind == "unknown_constraint"
    assert len(session.op_sketch_constraints("profile")["constraints"]) == 1
    assert session.op_build()["volume_mm3"] == pytest.approx(before)


def test_only_a_dimension_can_be_retyped(open_document):
    document = plate()
    document["features"][0]["constraints"] = [{"type": "horizontal", "line": "s"}]
    session = open_document(document)
    with pytest.raises(CadError) as exc:
        session.op_set_constraint_value("profile", 0, 12)
    assert exc.value.kind == "not_a_dimension"


def test_every_constraint_refuses_by_name(open_document):
    """A constraint naming geometry that is not there says which name.

    Three of them -- parallel, perpendicular, equal_length -- indexed the
    lookup directly and raised a bare KeyError, which reaches a caller as
    ``internal_error`` with a traceback instead of something it can act on.
    These arrive straight from user input, so they are checked here.
    """
    session = open_document(plate())
    for kind in ("parallel", "perpendicular", "equal_length"):
        with pytest.raises(CadError) as exc:
            session.op_add_constraint("profile", {"type": kind, "lines": ["s", "nope"]})
        assert exc.value.kind == "unknown_line"
        assert "nope" in exc.value.message
    assert len(session.op_sketch_constraints("profile")["constraints"]) == 1


# --- angles --------------------------------------------------------------------

def wedge(constraint, **extra):
    """Two segments from a common corner, the second placed by `constraint`."""
    spec = {"plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
            "points": {"o": [0, 0], "a": [40, 0], "b": [30, 20]},
            "lines": {"base": ["o", "a"], "slant": ["o", "b"]},
            "open": True,
            "constraints": [
                {"type": "fix", "point": "o", "at": [0, 0]},
                {"type": "fix", "point": "a", "at": [40, 0]},
                {"type": "distance", "points": ["o", "b"], "value": 30},
                constraint]}
    spec.update(extra)
    from cadcore.sketching import solve
    return solve(spec, lambda v: v)


def bearing(point) -> float:
    """Which way a point lies from the origin, in degrees."""
    return math.degrees(math.atan2(point[1], point[0]))


@pytest.mark.parametrize("asked", [30.0, -30.0, 90.0, 120.0, 7.5])
def test_an_angle_is_in_degrees_and_lands_where_it_was_asked_to(asked):
    """Degrees, because a revolve's 360 and a flange's 90 are degrees too.

    A sketch that measured its one angle in radians would be a trap for
    whoever edited it next.
    """
    out = wedge({"type": "angle", "lines": ["base", "slant"], "value": asked})
    assert bearing(out.points["b"]) == pytest.approx(asked, abs=1e-6)


def test_an_angle_is_read_from_the_first_line_to_the_second():
    """Naming the two lines the other way round negates the angle.

    The sense follows the order the endpoints were written in, which is
    PlaneGCS's rule; a solve that lands on the supplement looks like a solver
    bug until you know that, so it is pinned here.
    """
    forward = wedge({"type": "angle", "lines": ["base", "slant"], "value": 30})
    backward = wedge({"type": "angle", "lines": ["slant", "base"], "value": 30})
    assert bearing(forward.points["b"]) == pytest.approx(30.0, abs=1e-6)
    assert bearing(backward.points["b"]) == pytest.approx(-30.0, abs=1e-6)


def test_an_angle_can_place_one_line_on_its_own():
    """Two points instead of two lines sets the direction of that segment."""
    from cadcore.sketching import solve
    out = solve({"plane": {"origin": [0, 0, 0], "normal": [0, 0, 1],
                           "x_axis": [1, 0, 0]},
                 "points": {"o": [0, 0], "b": [30, 20]},
                 "lines": {"ray": ["o", "b"]}, "open": True,
                 "constraints": [
                     {"type": "fix", "point": "o", "at": [0, 0]},
                     {"type": "distance", "points": ["o", "b"], "value": 25},
                     {"type": "angle", "points": ["o", "b"], "value": 35}]},
                lambda v: v)
    assert bearing(out.points["b"]) == pytest.approx(35.0, abs=1e-6)
    assert math.hypot(*out.points["b"]) == pytest.approx(25.0, abs=1e-6)


def test_an_angle_is_a_parameter_like_every_other_dimension():
    """Written as an expression it re-answers itself when the parameter moves."""
    from cadcore.sketching import solve

    values = {"lean": 20.0}
    spec = {"plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
            "points": {"o": [0, 0], "a": [40, 0], "b": [30, 20]},
            "lines": {"base": ["o", "a"], "slant": ["o", "b"]}, "open": True,
            "constraints": [
                {"type": "fix", "point": "o", "at": [0, 0]},
                {"type": "fix", "point": "a", "at": [40, 0]},
                {"type": "distance", "points": ["o", "b"], "value": 30},
                {"type": "angle", "lines": ["base", "slant"], "value": "lean * 2"}]}

    def evaluate(v):
        return values[v.split(" *")[0]] * 2 if isinstance(v, str) else v

    assert bearing(solve(spec, evaluate).points["b"]) == pytest.approx(40.0, abs=1e-6)
    values["lean"] = 25.0
    assert bearing(solve(spec, evaluate).points["b"]) == pytest.approx(50.0, abs=1e-6)


def test_an_angle_that_names_one_line_is_refused_by_name():
    with pytest.raises(CadError) as exc:
        wedge({"type": "angle", "lines": ["base"], "value": 30})
    assert exc.value.kind == "bad_constraint"

    with pytest.raises(CadError) as exc:
        wedge({"type": "angle", "lines": ["base", "sloant"], "value": 30})
    assert exc.value.kind == "unknown_line"


def test_an_angle_of_ninety_is_the_perpendicular_it_replaces(session):
    """The sign convention, checked against a shape that already exists.

    `left` runs from d down to o, so it heads along -y while `front` heads
    along +x: the angle from the first to the second is -90, and getting that
    backwards would solve to a mirrored plate rather than fail. Swapping one
    for the other has to leave the solid alone, to the microlitre.
    """
    before = session.op_build()["volume_mm3"]
    constraints = session.op_sketch_constraints(sketch="profile")["constraints"]
    index = next(i for i, c in enumerate(constraints)
                 if c["type"] == "vertical" and c["line"] == "left")
    session.op_remove_constraint(sketch="profile", index=index)
    assert session.op_sketch_geometry(sketch="profile")["dof"] == 1

    session.op_add_constraint(sketch="profile", constraint={
        "type": "angle", "lines": ["front", "left"], "value": -90})
    assert session.op_sketch_geometry(sketch="profile")["dof"] == 0
    assert session.op_build()["volume_mm3"] == pytest.approx(before, abs=1e-6)


def test_an_angle_drives_a_solid_and_the_answer_is_trigonometry():
    """End to end: parameter -> angle -> sketch -> extrude -> a volume that
    can be worked out on paper."""
    from cadcore.model.document import Document
    from cadcore.evaluation.graph import Evaluator
    from cadcore import describe

    spec = {
        "meta": {"name": "leaning wedge"},
        "parameters": {"lean": 12.0, "run": 40.0, "rise": 25.0, "thick": 6.0},
        "features": [
            {"id": "profile", "type": "sketch",
             "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1],
                       "x_axis": [1, 0, 0]},
             "points": {"o": [0, 0], "a": [40, 0], "b": [45, 25], "c": [0, 25]},
             "lines": {"base": ["o", "a"], "slant": ["a", "b"],
                       "top": ["b", "c"], "back": ["c", "o"]},
             "constraints": [
                 {"type": "fix", "point": "o", "at": [0, 0]},
                 {"type": "horizontal", "line": "base"},
                 {"type": "horizontal", "line": "top"},
                 {"type": "vertical", "line": "back"},
                 {"type": "distance", "points": ["o", "a"], "value": "run"},
                 {"type": "distance", "points": ["o", "c"], "value": "rise"},
                 {"type": "angle", "lines": ["base", "slant"],
                  "value": "90 - lean"}]},
            {"id": "wedge", "type": "extrude", "sketch": "profile",
             "distance": "thick"}],
        "result": "wedge"}

    for lean in (0.0, 12.0, 25.0):
        spec["parameters"]["lean"] = lean
        body = Evaluator(Document.from_dict(spec)).build()
        run, rise, thick = 40.0, 25.0, 6.0
        top = run + rise * math.tan(math.radians(lean))
        # describe() rounds to a microlitre, so that is the tolerance -- the
        # underlying numbers agree further than this
        assert describe(body)["volume_mm3"] == pytest.approx(
            (run + top) / 2 * rise * thick, abs=1e-3)
