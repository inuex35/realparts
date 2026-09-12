"""Regression tests from a code review: behaviour that passed the suite while
being wrong."""
import json
import math
import os
import sys
import tempfile

import pytest

from cadcore import describe
from cadcore.model.document import Document
from cadcore.errors import CadError
from cadcore.evaluation.graph import Evaluator
from ..geometry.io.exchange import export_step
from ..geometry.solids.primitives import box
from ..geometry.io.tessellate import tessellate
from ..geometry.solids.sheet import flat_pattern


def test_two_documents_that_name_the_same_file_get_their_own_geometry(tmp_path):
    """Guards: the cache key of a file-reading feature includes the file, not
    only its arguments, so two `in.step` in different folders are two bodies."""
    for name, size in (("a", 10.0), ("b", 40.0)):
        folder = tmp_path / name
        folder.mkdir()
        export_step(box("c", [size, size, size], [0, 0, 0], centred=False),
                    str(folder / "in.step"))
        (folder / "part.json").write_text(json.dumps({
            "parameters": {}, "result": "p",
            "features": [{"id": "p", "type": "import_step", "path": "in.step"}]}), encoding="utf-8")
    (tmp_path / "asm.json").write_text(json.dumps({
        "parameters": {}, "result": "asm", "features": [
            {"id": "small", "type": "part", "document": "a/part.json"},
            {"id": "big", "type": "part", "document": "b/part.json"},
            {"id": "asm", "type": "assemble", "bodies": ["small", "big"]}]}), encoding="utf-8")

    body = Evaluator(Document.load(str(tmp_path / "asm.json"))).build()
    assert describe(body)["volume_mm3"] == pytest.approx(10 ** 3 + 40 ** 3, abs=1e-3)


def test_a_file_a_feature_reads_is_part_of_its_identity(tmp_path):
    """Guards: editing the file a feature reads rebuilds instead of serving the
    cached solid."""
    step = tmp_path / "in.step"
    export_step(box("c", [10, 10, 10], [0, 0, 0], centred=False), str(step))
    (tmp_path / "part.json").write_text(json.dumps({
        "parameters": {}, "result": "p",
        "features": [{"id": "p", "type": "import_step", "path": "in.step"}]}), encoding="utf-8")

    doc = Document.load(str(tmp_path / "part.json"))
    evaluator = Evaluator(doc)
    assert describe(evaluator.build())["volume_mm3"] == pytest.approx(1000.0, abs=1e-3)

    os.utime(step, (0, 0))               # a different file, same name
    export_step(box("c", [20, 20, 20], [0, 0, 0], centred=False), str(step))
    assert describe(Evaluator(doc, evaluator.cache).build())["volume_mm3"] == \
        pytest.approx(8000.0, abs=1e-3)


@pytest.mark.parametrize("through", ["translate", "pattern", "mirror", "compound",
                                     "prefixed", "transformed"])
def test_provenance_survives_every_way_a_body_is_rebuilt(through):
    """Guards: `notes` (bends, thickness) survives every path that rebuilds a
    Body, not only booleans.

    The `mirror` case is merge=False; the merged case is refused (next test),
    since it would count the original's bends twice.
    """
    from OCP.gp import gp_Trsf, gp_Vec

    from ..geometry.assembly.assembly import prefixed, transformed
    from ..geometry.solids.transform import compound, mirror, pattern, translate

    body = Evaluator(Document.load("examples/sheet_bracket.json")).build()
    assert flat_pattern(body)["bends"], "the fixture stopped being sheet metal"

    move = gp_Trsf()
    move.SetTranslation(gp_Vec(50, 0, 0))
    made = {
        "translate": lambda b: translate("t", b, [10, 0, 0]),
        "pattern": lambda b: pattern("p", b, [1, 0, 0], 200.0, 2),
        "mirror": lambda b: mirror("m", b, [0, 0, 0], [0, 1, 0], merge=False),
        "compound": lambda b: compound("c", [b]),
        "prefixed": lambda b: prefixed(b, "asm"),
        "transformed": lambda b: transformed(b, move),
    }[through]

    assert flat_pattern(made(body))["bends"], f"{through} dropped the bends"


def test_a_bracket_mirrored_onto_itself_is_not_laid_out_from_half_a_record():
    """Guards: `mirror(merge=True)` on sheet metal is refused. The part has
    four bends and a record of two, and no allowance would be right."""
    from ..geometry.solids.transform import mirror

    body = Evaluator(Document.load("examples/sheet_bracket.json")).build()
    with pytest.raises(CadError) as caught:
        flat_pattern(mirror("m", body, [0, 0, 0], [0, 1, 0], merge=True))
    assert caught.value.kind == "not_sheet_metal"
    assert "copy of itself" in caught.value.message


def test_a_result_that_is_not_a_solid_is_refused_by_kind():
    """Guards: a result naming a sketch or datum is refused as `no_solid`, not
    a KeyError from the cache."""
    doc = Document.from_dict({"parameters": {}, "result": "mid", "features": [
        {"id": "mid", "type": "plane", "origin": [0, 0, 0], "normal": [0, 0, 1]}]})
    with pytest.raises(CadError) as exc:
        Evaluator(doc).build()
    assert exc.value.kind == "no_solid"


def test_an_expression_that_cannot_finish_is_refused_like_one_that_cannot_parse():
    """Guards: an expression that cannot finish (`9**9**9`) is refused as
    `expression_too_big`; the sandbox guards exhaustion as well as escape."""
    doc = Document.from_dict({"parameters": {"w": 12.0}, "features": [],
                              "result": None})
    for expression in ("9**9**9", "10**400", "2**(10**5)"):
        with pytest.raises(CadError) as exc:
            doc.evaluate(expression)
        assert exc.value.kind == "expression_too_big", expression

    # and the sizes a document actually writes still work
    assert doc.evaluate("w**2") == pytest.approx(144.0)
    assert doc.evaluate("w**0.5") == pytest.approx(math.sqrt(12.0))
    assert doc.evaluate("2**-3") == pytest.approx(0.125)


def test_the_add_on_reopens_its_document_when_the_kernel_is_restarted():
    """Guards: after the kernel dies the client reopens its document in the
    fresh kernel instead of answering `no_document`."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "blender_addon", "link"))
    from client import Client

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    # `sys.executable`, not a virtualenv path: the interpreter running this
    # test can import cadcore, and CI builds no virtualenv (checked by
    # test_architecture.py)
    client = Client(sys.executable, repo)
    try:
        client.call("open", path="examples/mechanism/link.json")
        before = client.call("build")["volume_mm3"]
        assert client.opened == "examples/mechanism/link.json"

        client.proc.kill()               # the kernel dies mid-session
        client.proc = None
        assert client.call("build")["volume_mm3"] == pytest.approx(before)
    finally:
        client.stop()


def test_a_study_on_a_mesh_that_did_not_settle_does_not_pass():
    """Guards: `StudyOutcome.ok` is False when the mesh did not converge, even
    with every requirement met. A study with no convergence check is not
    judged on it."""
    from cadcore.simulation.convergence import Convergence
    from cadcore.simulation.study import StudyOutcome

    met = [(object(), True, 3.0)]
    settled = Convergence(converged=True)
    drifting = Convergence(converged=False)

    assert StudyOutcome("s", None, met, settled).ok is True
    assert StudyOutcome("s", None, met, drifting).ok is False
    # a study that never claimed to have converged is not judged on it
    assert StudyOutcome("s", None, met, None).ok is True
    # and a failed requirement still fails, converged or not
    assert StudyOutcome("s", None, [(object(), False, 1.0)], settled).ok is False


def test_two_faces_meeting_along_several_edges_are_numbered_by_where_they_are():
    """Guards: edges shared by the same two faces are numbered `#k` by
    position, not by OCCT's explorer order, so a reference survives a
    dimension change."""
    from cadcore import names
    from ..geometry.core.naming import _edge_key

    doc = Document.from_dict({"parameters": {"gap": 30.0}, "result": "slot",
                              "features": [
        {"id": "plate", "type": "box", "size": [90, 40, 10], "at": [0, 0, 0],
         "centred": True},
        {"id": "notch", "type": "box", "size": ["gap", 60, 4], "at": [0, 0, 5],
         "centred": True},
        {"id": "slot", "type": "cut", "target": "plate", "tool": "notch"}]})
    body = Evaluator(doc).build()

    shared = {}
    for key in body.edge_table():
        parts = names.parse(key)
        shared.setdefault(names.base(key), []).append(key)
    repeated = [k for k, v in shared.items() if len(v) > 1]
    assert repeated, "the fixture stopped producing faces that meet twice"

    for base in repeated:
        table = body.edge_table()
        keys = sorted(shared[base], key=lambda k: names.parse(k).duplicate or 1)
        positions = [_edge_key(table[k]) for k in keys]
        assert positions == sorted(positions), \
            f"{base}: #k is not in position order -- {positions}"


# --- units ---------------------------------------------------------------------

def test_a_document_in_inches_builds_the_same_solid_as_one_in_millimetres():
    """The kernel is millimetres: a unit is a property of the document,
    converted on the way in, because OCCT's tolerances are absolute."""
    metric = Document.from_dict({"parameters": {}, "result": "b", "features": [
        {"id": "b", "type": "box", "size": [25.4, 50.8, 12.7], "at": [0, 0, 0],
         "centred": False}]})
    imperial = Document.from_dict({"unit": "in", "parameters": {}, "result": "b",
                                   "features": [
        {"id": "b", "type": "box", "size": [1, 2, 0.5], "at": [0, 0, 0],
         "centred": False}]})
    assert describe(Evaluator(metric).build())["volume_mm3"] == pytest.approx(
        describe(Evaluator(imperial).build())["volume_mm3"], abs=1e-6)


def test_an_angle_and_a_count_are_not_lengths():
    """Which arguments are lengths comes from each feature's declaration, not
    from `evaluate`, so an angle and a count are not multiplied by 25.4."""
    doc = Document.from_dict({"unit": "in", "parameters": {}, "result": "h",
                              "features": [
        {"id": "plate", "type": "box", "size": [4, 2, 0.25], "at": [0, 0, 0],
         "centred": False},
        {"id": "h", "type": "hole", "body": "plate", "face": "plate/+z",
         "diameter": 0.25, "at": [0, 0],
         "pattern": {"count": 3, "spacing": 1.0, "direction": [1, 0, 0]}}]})
    args = {f.id: f.args for f in doc.features}
    assert args["plate"]["size"] == [pytest.approx(101.6), pytest.approx(50.8),
                                     pytest.approx(6.35)]
    assert args["h"]["diameter"] == pytest.approx(6.35)
    assert args["h"]["pattern"]["count"] == 3            # not 76.2
    assert args["h"]["pattern"]["spacing"] == pytest.approx(25.4)
    assert args["h"]["pattern"]["direction"] == [1, 0, 0]   # a direction, not a place


def test_a_parameter_used_as_an_angle_has_to_say_so():
    """A parameter has no kind of its own, so an inch document that uses one
    as an angle must say so in `parameter_units`; the refusal names it."""
    spec = {"unit": "in", "parameters": {"lean": 30.0, "run": 4.0}, "result": "w",
            "features": [
        {"id": "s", "type": "sketch",
         "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
         "points": {"a": [0, 0], "b": [4, 0], "c": [4, 2], "d": [0, 2]},
         "lines": {"w": ["a", "b"], "x": ["b", "c"], "y": ["c", "d"],
                   "z": ["d", "a"]},
         "constraints": [{"type": "fix", "point": "a", "at": [0, 0]},
                         {"type": "fix", "point": "b", "at": ["run", 0]},
                         {"type": "fix", "point": "c", "at": [4, 2]},
                         {"type": "fix", "point": "d", "at": [0, 2]}]},
        {"id": "w", "type": "revolve", "sketch": "s", "angle": "90 - lean",
         "axis": {"origin": [0, 0, 0], "direction": [0, 1, 0]}}]}

    with pytest.raises(CadError) as exc:
        Document.from_dict(spec)
    assert exc.value.kind == "parameter_unit_unknown"
    assert "lean" in exc.value.detail["parameters"]
    assert "run" not in exc.value.detail["parameters"]

    spec["parameter_units"] = {"lean": "angle"}
    doc = Document.from_dict(spec)
    assert doc.parameters["lean"] == pytest.approx(30.0)     # degrees, untouched
    assert doc.parameters["run"] == pytest.approx(101.6)     # four inches


def test_a_millimetre_document_is_not_touched_at_all():
    """A millimetre document is loaded unchanged."""
    raw = json.loads(open("examples/bracket.json", encoding="utf-8").read())
    doc = Document.load("examples/bracket.json")
    assert doc.unit == "mm"
    assert doc.parameters == raw["parameters"]
    for feature, before in zip(doc.features, raw["features"]):
        assert feature.args == {k: v for k, v in before.items()
                                if k not in ("id", "type")}


def test_a_unit_nobody_has_heard_of_is_refused_by_name():
    with pytest.raises(CadError) as exc:
        Document.from_dict({"unit": "furlong", "parameters": {}, "features": [],
                            "result": None})
    assert exc.value.kind == "unknown_unit"
    assert "in" in exc.value.detail["known"]


def test_the_result_is_reported_in_the_unit_it_was_drawn_in(tmp_path):
    """Build results are reported in the document's unit as well as
    millimetres."""
    from cadcore.service.server import Session

    path = tmp_path / "part.json"
    path.write_text(json.dumps({"unit": "in", "parameters": {}, "result": "b",
                                "features": [
        {"id": "b", "type": "box", "size": [1, 2, 0.5], "at": [0, 0, 0],
         "centred": False}]}), encoding="utf-8")
    session = Session()
    session.op_open(str(path))
    out = session.op_build()
    assert out["unit"] == "in"
    assert out["volume_in3"] == pytest.approx(1.0)
    assert out["area_in2"] == pytest.approx(7.0)
    assert out["volume_mm3"] == pytest.approx(25.4 ** 3, rel=1e-9)


# --- suppress ------------------------------------------------------------------

def suppressed(spec: dict, *ids: str) -> dict:
    out = json.loads(json.dumps(spec))
    for feature in out["features"]:
        if feature["id"] in ids:
            feature["suppressed"] = True
    return out


PLATE = {"parameters": {"r": 3.0}, "result": "f", "features": [
    {"id": "b", "type": "box", "size": [40, 40, 10], "at": [0, 0, 0],
     "centred": False},
    {"id": "h", "type": "hole", "body": "b", "face": "b/+z", "diameter": 8,
     "at": [0, 0]},
    {"id": "f", "type": "fillet", "body": "h", "radius": "r",
     "edges": {"of_face": "b/+z"}}]}


def test_a_suppressed_feature_hands_its_input_through():
    """Switched off, not deleted: the chain still builds across it."""
    def volume(*off):
        doc = Document.from_dict(suppressed(PLATE, *off))
        return describe(Evaluator(doc).build())["volume_mm3"]

    plain, no_hole, no_fillet, neither = (
        volume(), volume("h"), volume("f"), volume("h", "f"))
    assert neither == pytest.approx(40 * 40 * 10, abs=1e-3)   # the bare box
    assert no_hole > plain and no_fillet > plain
    assert plain < no_hole < neither


def test_which_argument_a_feature_stands_for_is_declared_not_guessed():
    """Which argument a suppressed feature passes through (`target` for a cut,
    `body` for a fillet) is declared on the feature type; both are references,
    so it cannot be inferred."""
    from ..features.declare.registry import handler

    assert handler("cut").stands_for == "target"
    assert handler("cut").passthrough({"target": "plate", "tool": "punch"}) == "plate"
    assert handler("fillet").stands_for == "body"
    # and a feature that makes a body out of nothing stands for nothing
    assert handler("box").stands_for is None
    assert handler("box").passthrough({"size": [1, 1, 1]}) is None


def test_a_feature_with_nothing_to_hand_through_is_refused_by_name():
    doc = Document.from_dict(suppressed(PLATE, "b"))
    with pytest.raises(CadError) as exc:
        Evaluator(doc).build()
    assert exc.value.kind == "cannot_suppress"
    assert exc.value.detail["type"] == "box"


def test_suppressing_is_what_a_broken_reference_leaves_you_instead_of_deleting():
    """A feature that stops building can be suppressed and later restored with
    its arguments intact, instead of deleted."""
    from cadcore.service.server import Session

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({**PLATE, "parameters_bounds": {"r": [0.5, 25.0]}}, handle)
        path = handle.name

    session = Session()
    session.op_open(path)
    session.op_build()

    with pytest.raises(CadError) as exc:
        session.op_set_parameter("r", 24.0)
    assert exc.value.kind == "fillet_failed"

    session.op_suppress_feature(feature_id="f")
    assert session.op_set_parameter("r", 24.0)["volume_mm3"] > 0

    kept = session.op_describe_document()["features"][2]
    assert kept["suppressed"] is True
    assert kept["args"]["radius"] == "r"        # the work is still there

    session.op_set_parameter("r", 3.0)
    back = session.op_suppress_feature(feature_id="f", suppressed=False)
    assert back["volume_mm3"] == pytest.approx(
        describe(Evaluator(Document.from_dict(PLATE)).build())["volume_mm3"],
        abs=1e-3)


def test_switching_one_off_is_an_edit_like_any_other():
    """So it undoes, and so the cache does not serve the built version."""
    from cadcore.service.server import Session

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(PLATE, handle)
        path = handle.name
    session = Session()
    session.op_open(path)
    before = session.op_build()["volume_mm3"]

    off = session.op_suppress_feature(feature_id="h")["volume_mm3"]
    assert off != pytest.approx(before)
    assert session.op_undo()["volume_mm3"] == pytest.approx(before)
    assert session.op_redo()["volume_mm3"] == pytest.approx(off)


# --- repairing a reference -----------------------------------------------------

SPLIT = {"parameters": {}, "result": "r", "features": [
    {"id": "plate", "type": "box", "size": [80, 60, 10], "at": [0, 0, 0],
     "centred": True},
    {"id": "bar", "type": "box", "size": [100, 8, 4], "at": [0, -18, 5],
     "centred": True},
    {"id": "welded", "type": "fuse", "target": "plate", "tool": "bar"},
    {"id": "r", "type": "hole", "body": "welded", "face": "plate/+z",
     "diameter": 6, "at": [0, 20]}]}


def open_session(spec: dict):
    from cadcore.service.server import Session

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(spec, handle)
        path = handle.name
    session = Session()
    session.op_open(path)
    return session


def test_what_is_broken_can_be_asked_while_the_build_is_failing():
    """`broken_references` answers while the build fails: the chain is walked
    back from the end until something builds."""
    session = open_session(SPLIT)
    with pytest.raises(CadError) as exc:
        session.op_build()
    assert exc.value.kind == "face_was_split"

    report = session.op_broken_references()
    assert report["built_upto"] == "welded"
    assert len(report["broken"]) == 1
    broken = report["broken"][0]
    assert (broken["feature"], broken["path"], broken["name"]) == \
        ("r", ["face"], "plate/+z")


def test_a_split_reference_is_told_apart_from_a_missing_one():
    """A split face (`why: split`, with the pieces as candidates) is reported
    differently from a missing one (`why: missing`); the fixes differ."""
    session = open_session(SPLIT)
    with pytest.raises(CadError):
        session.op_build()
    broken = session.op_broken_references()["broken"][0]
    assert broken["why"] == "split"
    assert broken["candidates"] == ["plate/+z@0", "plate/+z@1"]

    gone = dict(SPLIT)
    gone["features"] = [dict(f) for f in SPLIT["features"]]
    gone["features"][3]["face"] = "plate/+w"
    session = open_session(gone)
    with pytest.raises(CadError):
        session.op_build()
    assert session.op_broken_references()["broken"][0]["why"] == "missing"


def test_a_reference_can_be_pointed_somewhere_else_and_the_model_rebuilds():
    """`reattach` repoints a reference and the model rebuilds."""
    session = open_session(SPLIT)
    with pytest.raises(CadError):
        session.op_build()

    out = session.op_reattach(old="plate/+z", new="plate/+z@1")
    assert out["reattached"] == [{"feature": "r", "path": ["face"]}]
    assert out["volume_mm3"] > 0
    assert session.op_broken_references()["broken"] == []
    assert session.op_describe_document()["features"][3]["args"]["face"] == \
        "plate/+z@1"


def test_reattaching_reaches_names_nested_inside_a_query():
    """`geometry_names` finds face names nested in queries, end conditions
    and lists, and does not report feature references as face names."""
    from ..features.declare.registry import handler

    assert handler("fillet").geometry_names(
        {"body": "p", "edges": {"between": ["p/+z", "p/+x"]}}) == \
        [(["edges", "between", 0], "query"), (["edges", "between", 1], "query")]
    assert handler("extrude").geometry_names(
        {"sketch": "s", "until": {"face": "p/+z", "body": "p"}}) == \
        [(["until", "face"], "name")]
    assert handler("shell").geometry_names(
        {"body": "p", "open": ["p/+z", "p/-z"]}) == \
        [(["open", 0], "names"), (["open", 1], "names")]
    # `body` and `sketch` are references to features, not names of geometry
    assert handler("hole").geometry_names({"body": "p", "face": "p/+z"}) == \
        [(["face"], "name")]
    # and `parallel` in an edge query is an axis, not a face
    assert handler("fillet").geometry_names(
        {"body": "p", "edges": {"parallel": "z", "of_face": "p/+z"}}) == \
        [(["edges", "of_face"], "query")]


def test_reattaching_to_something_that_is_not_there_is_refused():
    session = open_session(SPLIT)
    with pytest.raises(CadError):
        session.op_build()
    with pytest.raises(CadError) as exc:
        session.op_reattach(old="plate/+z", new="plate/nowhere")
    assert exc.value.kind == "unresolved_reference"

    with pytest.raises(CadError) as exc:
        session.op_reattach(old="plate/-y", new="plate/+z@0")
    assert exc.value.kind == "unknown_reference"


def test_a_working_document_reports_nothing_broken():
    """Guards: a document that builds reports nothing broken. `parallel: "z"`
    is an axis, not a name, and a reference is checked against the body it
    was written against, not the finished one."""
    for name in ("bracket", "flange", "sheet_bracket", "bottle",
                 "cup", "sketch_plate"):
        session = open_session(json.loads(
            open(f"examples/{name}.json", encoding="utf-8").read()))
        session.op_build()
        assert session.op_broken_references()["broken"] == [], name


def test_a_mate_names_a_face_on_each_of_two_parts(open_example):
    """A mate's two faces are each checked against the part they belong to,
    not both against one input."""
    for name in ("assembly.json", "sheet_clip.json"):
        session = open_example(name)
        assert session.op_broken_references()["broken"] == [], name


def test_a_split_face_is_fine_in_a_query_and_not_in_a_frame():
    """An edge query matches by base and may name a split face; a frame
    cannot, which is what `face_was_split` says."""
    session = open_session(json.loads(open("examples/bracket.json", encoding="utf-8").read()))
    session.op_build()
    assert session.op_broken_references()["broken"] == []

    session = open_session(SPLIT)                 # the same split, used as a frame
    with pytest.raises(CadError):
        session.op_build()
    assert session.op_broken_references()["broken"][0]["why"] == "split"


def test_several_parameters_move_together_in_one_rebuild(open_example):
    """`set_parameters` applies a pose in one rebuild and one undo step."""
    session = open_example("quadruped/robot.json")
    angles = {"hip_fl": 20.0, "knee_fl": -30.0, "hip_br": 20.0, "knee_br": -30.0}

    out = session.op_set_parameters(angles)

    assert out["volume_mm3"] > 0
    for name, value in angles.items():
        assert session.doc.parameters[name] == value
    # one edit, so one undo puts the whole pose back
    session.op_undo()
    assert all(session.doc.parameters[name] == 0.0 for name in angles)


def test_a_pose_with_one_bad_name_is_refused_whole(open_example):
    """A pose with one unknown parameter is refused whole; none of it lands."""
    session = open_example("quadruped/robot.json")
    session.op_set_parameters({"hip_fl": 15.0})

    with pytest.raises(CadError) as exc:
        session.op_set_parameters({"knee_fl": -20.0, "hip_nope": 1.0})

    assert exc.value.kind == "unknown_parameter"
    assert exc.value.detail["unknown"] == ["hip_nope"]
    assert session.doc.parameters["hip_fl"] == 15.0      # the earlier one stands
    assert session.doc.parameters["knee_fl"] == 0.0      # this one never landed


def test_the_quadruped_does_not_walk_through_itself(open_example):
    """Guards: the trot's worst-case frame (front-left mid-swing, back-left
    planted) is clear of interference.

    This depends on the assembly passing `hip_dx` to the chassis; without it
    the pins fall back to the part's default spacing and the tibias meet.
    """
    session = open_example("quadruped/robot.json")
    # the front-left foot swinging through where the back-left one is standing
    session.op_set_parameters({"hip_fl": -18.4, "knee_fl": -29.7,
                               "hip_bl": 18.4, "knee_bl": 0.0})

    assert session.op_interference(tolerance=1.0)["clear"]

    # without the hip_dx the assembly passes, the chassis falls back to its
    # default spacing and the legs meet
    doc = session.doc
    body = next(f for f in doc.features if f.id == "body")
    body.args["parameters"] = {k: v for k, v in body.args["parameters"].items()
                               if k != "hip_dx"}
    session._rebuild()
    hit = session.op_interference(tolerance=1.0)["interferences"]
    assert [h["parts"] for h in hit] == [["knee_bl", "knee_fl"]]


def test_a_knee_puts_the_tibia_beside_the_femur_not_through_it(open_example):
    """Knee mates carry an offset: two flat bars on one pin are a lap joint,
    and a concentric mate alone leaves them overlapping at every pose."""
    session = open_example("quadruped/robot.json")
    assert session.op_interference(tolerance=1.0)["clear"]

    for feature in session.doc.features:
        if feature.type == "mate" and feature.id.startswith("knee_"):
            feature.args.pop("offset", None)
    session._rebuild()

    hit = session.op_interference(tolerance=1.0)["interferences"]
    assert sorted(h["parts"] for h in hit) == [["hip_bl", "knee_bl"],
                                               ["hip_br", "knee_br"],
                                               ["hip_fl", "knee_fl"],
                                               ["hip_fr", "knee_fr"]]


def test_the_walking_foot_stays_on_the_ground(open_example):
    """A planted foot stays at one height through the gait, measured from the
    kernel's placement of the shin rather than from the gait arithmetic."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "render"))
    import gait

    session = open_example("quadruped/robot.json")
    planted = []
    for frame in range(24):
        session.op_set_parameters(gait.pose(frame / 24))
        for leg in ("fl", "fr", "bl", "br"):
            if (frame / 24 + gait.PHASE[leg]) % 1.0 < gait.DUTY:
                shin = session._placed_parts()["knee_" + leg]
                planted.append(min(v[2] for v in
                                   tessellate(shin, deflection=0.2)["vertices"]))

    assert len(planted) == 48                      # two feet down at all times
    # the millimetre of slack is the sole's contact point sliding round its
    # own radius as the shin leans
    assert max(planted) - min(planted) < 1.0


def test_the_knee_angle_is_measured_from_vertical_not_from_the_thigh(open_example):
    """Hip and knee angles are both absolute: a mate turns about the target
    axis (the femur's bore), whose direction does not change with the hip."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "render"))
    import gait

    session = open_example("quadruped/robot.json")
    for hip, knee in ((0.0, 0.0), (26.0, 0.0), (-20.0, 15.0)):
        session.op_set_parameters({"hip_fl": hip, "knee_fl": knee})
        shin = session._placed_parts()["knee_fl"]
        sole = min(tessellate(shin, deflection=0.2)["vertices"], key=lambda v: v[2])
        assert abs(-sole[2] - gait.foot_of(hip, knee)[1]) < 1.5


def test_a_shell_that_removes_nothing_is_a_failure():
    """Guards: a shell that removes nothing is refused as `shell_failed` with a
    hint about the order.

    Fillet a face and then open that face, and OCCT's thick-solid builder finds
    nothing to join: `IsDone` is true and `Shape()` is the input.
    """
    box = {"id": "b", "type": "box", "size": [40, 30, 20],
           "at": [0, 0, 10], "centred": True}
    plain = open_session({"features": [box,
                                       {"id": "h", "type": "shell", "body": "b",
                                        "thickness": 2.0, "open": ["b/+z"]}],
                          "result": "h"})
    hollow = plain.op_build()["volume_mm3"]
    assert hollow < 40 * 30 * 20 * 0.5          # it really is hollow

    session = open_session({"features": [
        box,
        {"id": "r", "type": "fillet", "body": "b",
         "edges": {"of_face": "b/+z"}, "radius": 2.0},
        {"id": "h", "type": "shell", "body": "r",
         "thickness": 2.0, "open": ["b/+z"]}], "result": "h"})

    with pytest.raises(CadError) as exc:
        session.op_build()
    assert exc.value.kind == "shell_failed"
    assert "removed nothing" in str(exc.value)
    # the hint says the fix is the order of the two features
    assert "afterwards" in exc.value.detail["hint"]
