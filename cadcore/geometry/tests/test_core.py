"""What has to keep working: names survive rebuilds, errors are typed."""
import math

import pytest

from cadcore import Document, describe
from cadcore.geometry import kernel
from cadcore.evaluation.graph import Evaluator
from cadcore.geometry.kernel import CadError

DOC = "examples/bracket.json"


def build(**params):
    doc = Document.load(DOC)
    doc.parameters.update(params)
    return Evaluator(doc).build()


def test_builds_and_names_faces():
    info = describe(build())
    assert info["volume_mm3"] > 0
    assert "plate/+z" in info["face_names"]


def test_a_name_whose_face_is_gone_stays_reported_as_gone():
    """Guards: `dropped` accumulates across operations; the end faces of the
    cutting cylinders are consumed and must still be reported gone."""
    info = describe(build())
    assert "h1/+z" in info["dropped"]
    assert "bore_tool/+y" in info["dropped"]
    assert not set(info["dropped"]) & set(info["face_names"]), \
        "a name cannot be both gone and there"


def test_edge_is_named_after_its_two_faces():
    body = build()
    assert any("|" in name for name in body.edge_table())


def test_reference_survives_a_parameter_change():
    """The fillet is selected by a face pair; changing the plate must not move it."""
    a = describe(build())
    b = describe(build(width=95, thickness=6, corner_r=5))
    assert set(a["face_names"]) - {"round_corners/side"} <= set(b["face_names"]) | set(b["aliases"])
    assert "round_corners/side" in b["face_names"]


def test_welded_face_keeps_an_alias():
    body = build()
    # the wall's side faces are welded into the plate's by the fuse
    assert body.face("wall/-x") is not None


def test_errors_are_typed_not_silent():
    doc = Document.load(DOC)
    doc.parameters["corner_r"] = 400        # far larger than the part
    with pytest.raises(CadError) as e:
        Evaluator(doc).build()
    assert e.value.kind in {"fillet_failed", "empty_result", "invalid_shape"}


def test_cache_only_reruns_what_changed():
    doc = Document.load(DOC)
    cache = {}
    Evaluator(doc, cache).build()
    doc2 = Document.load(DOC)
    doc2.parameters["corner_r"] = 5.5       # only the fillet should re-evaluate
    ev = Evaluator(doc2, cache)
    ev.build()
    assert ev.stats["reused"] >= len(doc2.features) - 2


# ---------------------------------------------------------------- sketches --
SKETCH_DOC = "examples/sketch_plate.json"


def sketch_build(**params):
    doc = Document.load(SKETCH_DOC)
    doc.parameters.update(params)
    return Evaluator(doc).build()


def test_extruded_faces_are_named_after_sketch_segments():
    names = describe(sketch_build())["face_names"]
    for seg in ("plate/front", "plate/right", "plate/chamfer", "plate/back", "plate/left"):
        assert seg in names
    assert "plate/top" in names and "plate/bottom" in names


def test_sketch_dimension_change_keeps_the_fillet_where_it_was():
    a = describe(sketch_build())
    b = describe(sketch_build(width=120, depth=80, notch=25))
    assert set(a["face_names"]) == set(b["face_names"])


def test_underconstrained_sketch_is_refused():
    import json
    doc = Document.load(SKETCH_DOC)
    spec = doc.feature("profile").args
    spec["constraints"] = [c for c in spec["constraints"] if c["type"] != "distance"]
    with pytest.raises(CadError) as e:
        Evaluator(doc).build()
    assert e.value.kind == "sketch_underconstrained"
    assert e.value.detail["diagnosis"]["dof"] > 0


def test_open_profile_is_refused():
    doc = Document.load(SKETCH_DOC)
    spec = doc.feature("profile").args
    spec["lines"].pop("back")
    spec["constraints"] = [c for c in spec["constraints"] if c.get("line") != "back"]
    spec["allow_underconstrained"] = True       # the point here is the open loop
    with pytest.raises(CadError) as e:
        Evaluator(doc).build()
    assert e.value.kind == "open_profile"


def test_a_document_cannot_run_code():
    """Expressions are parsed and walked, not eval'd; emptying `__builtins__`
    is not a sandbox."""
    from cadcore.model.document import Document
    from cadcore.errors import CadError

    doc = Document(parameters={"w": 40, "h": 12})
    assert doc.evaluate("w/2") == 20
    assert doc.evaluate("max(w, h) - 3") == 37
    assert doc.evaluate("w > h + 10") is True

    for attack in ("().__class__.__mro__[1].__subclasses__()",
                   "__import__('os').system('touch /tmp/pwned')",
                   "open('/etc/passwd').read()",
                   "[x for x in (1, 2)]",
                   "(lambda: 1)()"):
        with pytest.raises(CadError) as exc:
            doc.evaluate(attack)
        assert exc.value.kind in ("unsafe_expression", "bad_expression")


def test_a_load_on_a_face_the_mesh_does_not_have_is_refused():
    """Silently integrating over an empty region gives safety factor infinity."""
    pytest.importorskip("ngsolve", reason="the studies need requirements-sim.txt")

    from cadcore.geometry import kernel
    from cadcore.errors import CadError
    from ...simulation.studies.structural import StaticStructural

    bar = kernel.box("bar", [100, 20, 20], (50, 0, 0))
    study = StaticStructural(bar, "A6061", 10.0, 2)
    study.fix("bar/-x")
    study.loads.append(("bar/nowhere", (0, 0, -100)))
    with pytest.raises(CadError) as exc:
        study.solve()
    assert exc.value.kind == "boundary_not_in_mesh"
    assert exc.value.detail["missing"] == ["bar/nowhere"]


def test_a_mistyped_edge_query_is_refused_not_ignored():
    """Guards: an unknown query key is refused rather than silently applying
    no filter."""
    from ..core.query import select_edges

    box = kernel.box("b", [20, 20, 20], (0, 0, 0))
    with pytest.raises(CadError) as exc:
        select_edges(box, {"betwen": ["b/+z", "b/+x"]})
    assert exc.value.kind == "bad_query" and exc.value.detail["unknown"] == ["betwen"]

    with pytest.raises(CadError) as exc:
        select_edges(box, {"parallel": "w"})
    assert exc.value.kind == "bad_query"

    assert select_edges(box, {"between": ["b/+z", "b/+x"]}) == ["b/+x|b/+z"]


def test_an_edge_name_is_never_taken_apart_as_text():
    """``a|b#2`` is ambiguous as text because a face may itself be called
    ``b#2``, so the face pair is stored when the edge table is built rather
    than parsed from the name."""
    box = kernel.box("b", [20, 20, 20], (0, 0, 0))
    for name in box.edge_table():
        owners = box.edge_owners(name)
        assert owners and all(box.face(o) is not None for o in owners)


def test_a_face_named_for_an_axis_faces_that_way():
    """The axis role in a face name matches the face's real normal. A cut
    turns its tool inside out: the tool's ``+y`` bounds the cavity and faces
    ``-y``, so the name follows the face."""
    from cadcore import names
    from ..core.measure import face_frame

    block = kernel.box("b", [40, 30, 20], (0, 0, 0), centred=False)
    tool = kernel.box("t", [10, 10, 40], (5, 5, 12), centred=False)   # blind
    pocket = kernel.cut("p", block, tool)

    for name in pocket.face_names():
        axis = names.axis_of(names.parse(name).role)
        if axis is None:
            continue
        normal = face_frame(pocket, name)["normal"]
        assert sum(normal[i] * axis[i] for i in range(3)) > 0.9, \
            f"{name} faces {tuple(round(c, 2) for c in normal)}"
    # the pocket floor was the tool's -z face; it faces +z once the material
    # above it is gone
    assert "t/+z" in pocket.face_names() and "t/-z" not in pocket.face_names()


def test_measuring_the_model_is_reachable_without_writing_a_drawing():
    """`op_measure` and drawing dimensions share `cadcore.geometry.core.measure`,
    so a dimension on a sheet and a number in a panel cannot disagree."""
    from ..core.measure import KINDS
    from cadcore.service.server import Session

    session = Session()
    session.op_open("examples/sheet_collar.json")
    session.op_build()

    assert session.op_measure(kind="linear",
                              faces=["plate/top", "plate/bottom"])["value"] == \
        pytest.approx(5.0)
    assert session.op_measure(kind="diameter", faces=["mouth/mouth"])["value"] == \
        pytest.approx(20.0)
    assert session.op_measure(kind="radius", faces=["mouth/mouth"])["value"] == \
        pytest.approx(10.0)
    assert session.op_measure(kind="angle", faces=["plate/top", "plate/bottom"]
                              )["unit"] == "deg"
    # the slot's rounded end is half a cylinder whose centroid is not on its
    # axis; a centre distance must use the axis, not the centre of mass
    assert session.op_measure(kind="centres",
                              faces=["mouth/mouth", "collar/loop/end"])["value"] == \
        pytest.approx(math.hypot(15.0, 6.5), abs=1e-4)

    with pytest.raises(CadError) as exc:
        session.op_measure(kind="volume", faces=["plate/top"])
    assert exc.value.kind == "unknown_dimension"
    # the menu is the list, not a copy of it
    assert session.op_measure_kinds()["kinds"] == list(KINDS)


def test_two_faces_at_an_angle_measure_the_angle_between_them():
    """The angle between two faces is measured through the material (120
    here, the angle a drawing dimensions), not the 60-degree sketch angle
    between the line directions."""
    doc = Document.from_dict({
        "parameters": {}, "features": [
            {"id": "profile", "type": "sketch",
             "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
             "points": {"o": [0, 0], "a": [40, 0], "b": [40, 25], "c": [0, 25]},
             "lines": {"base": ["o", "a"], "slant": ["a", "b"], "top": ["b", "c"],
                       "back": ["c", "o"]},
             "constraints": [
                 {"type": "fix", "point": "o", "at": [0, 0]},
                 {"type": "horizontal", "line": "base"},
                 {"type": "horizontal", "line": "top"},
                 {"type": "vertical", "line": "back"},
                 {"type": "distance", "points": ["o", "a"], "value": 40},
                 {"type": "distance", "points": ["o", "c"], "value": 25},
                 {"type": "angle", "lines": ["base", "slant"], "value": 60}]},
            {"id": "wedge", "type": "extrude", "sketch": "profile", "distance": 6}],
        "result": "wedge"})
    from ..core.measure import dimension

    body = Evaluator(doc).build()
    assert dimension(body, "angle", ["wedge/base", "wedge/slant"]) == \
        pytest.approx(120.0, abs=1e-6)
    # and a square corner reads as square from either pair
    assert dimension(body, "angle", ["wedge/base", "wedge/back"]) == \
        pytest.approx(90.0, abs=1e-6)


def test_a_face_that_a_boolean_split_is_not_a_place_to_measure_from():
    """An alias records identity, not position: a frame taken off a split
    face would use a piece's centroid, so `face_frame` refuses with
    `face_was_split` and asks for a specific piece."""
    from cadcore.geometry.kernel import face_frame

    doc = Document.from_dict({"parameters": {}, "features": [
        {"id": "plate", "type": "box", "size": [60, 60, 10], "at": [0, 0, 0],
         "centred": True},
        # two bars laid across the top, splitting it into three
        {"id": "barA", "type": "box", "size": [80, 8, 4], "at": [0, -18, 5],
         "centred": True},
        {"id": "one", "type": "fuse", "target": "plate", "tool": "barA"},
        {"id": "barB", "type": "box", "size": [80, 8, 4], "at": [0, 18, 5],
         "centred": True},
        {"id": "two", "type": "fuse", "target": "one", "tool": "barB"}],
        "result": "two"})
    body = Evaluator(doc).build()

    pieces = [n for n in describe(body)["face_names"] if n.startswith("plate/+z")]
    assert len(pieces) > 1, pieces

    with pytest.raises(CadError) as exc:
        face_frame(body, "plate/+z")
    assert exc.value.kind == "face_was_split"
    assert exc.value.detail["count"] == len(pieces)

    # naming a specific piece is still allowed
    assert face_frame(body, pieces[0])["area"] > 0


def test_a_cone_face_has_its_facts_read_without_a_crash():
    """A chamfer on a cylinder's rim makes a cone. Reading that face asked
    the cone for a radius it does not have and crashed with AttributeError
    instead of a refusal."""
    from cadcore.geometry.core.naming import face_info

    body = kernel.cylinder("c", 10.0, 10.0, (0, 0, 5), (0, 0, 1), True)
    cut = kernel.chamfer("ch", body, ["c/-z|c/side"], 3.0)
    cone = next(f for n, f in cut.names if n.startswith("ch/"))
    info = face_info(cone)
    assert info["shape"] == "cone"
    # the same face again, read as an unknown surface: the path that crashed
    from cadcore.geometry.core.naming import _recognised

    found = _recognised(cone, "cone")
    assert found is not None and found.RefRadius() > 0


def test_a_fillet_on_the_rim_of_an_open_shell_is_refused_by_kind():
    """OCCT throws for an edge with one face on it, and the throw came out of
    the kernel as an untyped internal error. It is a refusal with a kind."""
    body = kernel.box("b", [20, 20, 10], [0, 0, 5], True)
    opened = kernel.delete_face("open", body, ["b/+z"], heal=False)
    rim = next(e for e in opened.edge_table() if e.endswith("|open"))
    with pytest.raises(CadError) as refused:
        kernel.fillet("r", opened, [rim], 1.0)
    assert refused.value.kind == "fillet_failed"
    with pytest.raises(CadError) as refused:
        kernel.chamfer("c", opened, [rim], 1.0)
    assert refused.value.kind == "chamfer_failed"


def test_a_fillet_face_is_numbered_by_the_edge_it_rounds():
    """`round/side#2` is the second edge by name, not the second face OCCT
    handed back.

    Numbered by position, the four rounds of one fillet come back in the order
    the kernel walked them, and a dimension that changes that order makes a
    stored `#2` mean another edge. An edge name is the two faces it lies
    between: `boss/+x|plate/+z` sorts before `boss/+y|plate/+z`, and the
    numbers follow that.
    """
    from cadcore.geometry.core.naming import neighbours

    plate = kernel.box("plate", [60, 50, 8], (0, 0, 4), centred=False)
    boss = kernel.box("boss", [28, 20, 14], (16, 15, 8), centred=False)
    joined = kernel.fuse("joined", plate, boss)
    uprights = sorted(k for k in joined.edge_table()
                      if "plate/+z" in k and k.startswith("boss/"))
    beside = neighbours(kernel.fillet("round", joined, uprights, 3.0))
    rounded = {name: sorted(n for n in near if n.startswith("boss/"))
               for name, near in beside.items() if name.startswith("round/")}
    assert rounded == {"round/side": ["boss/+x"],
                       "round/side#2": ["boss/+y"],
                       "round/side#3": ["boss/-x"],
                       "round/side#4": ["boss/-y"]}, rounded


def test_a_sketch_segment_called_top_does_not_take_the_cap_s_name():
    """On a prism, `top` is the cap. A sketch line of that name gets the `#2`.

    `yoke.json` draws a rectangle whose lines are bottom, right, top, left and
    then chamfers `of_face: plate/top`. With both the cap and the swept side
    answering to that name, which one was chamfered was OCCT's traversal
    order, and the two faces were 110 mm apart.
    """
    from collections import Counter

    doc = {"meta": {"name": "a named rectangle"}, "parameters": {},
           "features": [
               {"id": "outline", "type": "sketch",
                "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0]},
                "points": {"o": [0, 0], "a": [40, 0], "b": [40, 30], "c": [0, 30]},
                "lines": {"bottom": ["o", "a"], "right": ["a", "b"],
                          "top": ["b", "c"], "left": ["c", "o"]},
                "constraints": [{"type": "fix", "point": "o", "at": [0, 0]},
                                {"type": "horizontal", "line": "bottom"},
                                {"type": "horizontal", "line": "top"},
                                {"type": "vertical", "line": "right"},
                                {"type": "vertical", "line": "left"},
                                {"type": "distance", "points": ["o", "a"], "value": 40},
                                {"type": "distance", "points": ["a", "b"], "value": 30}]},
               {"id": "plate", "type": "extrude", "sketch": "outline", "distance": 8}],
           "result": "plate"}
    body = Evaluator(Document.from_dict(doc)).build()
    named = [n for n, _ in body.names]
    assert not {n: k for n, k in Counter(named).items() if k > 1}, named
    # the cap keeps the plain name; the side drawn as `top` is the one moved
    top = body.face("plate/top")
    assert top is not None
    from cadcore.geometry.core.naming import face_info
    assert face_info(top)["centre"][2] == pytest.approx(8.0)
    assert "plate/top#2" in named


def test_a_shell_is_tried_in_the_sentry_first():
    """OCCT's offsetter segfaults on some solids rather than refusing them,
    which took the whole process down in a soak run. The sentry tries the
    shell first, by its open faces, the way it tries a fillet by its edges."""
    from cadcore.geometry import kernel
    from cadcore.geometry.solids import sentry

    if not sentry.enabled():
        import pytest
        pytest.skip("the sentry is off")
    from cadcore.geometry.core.measure import volume
    from cadcore.geometry.solids import modify

    box = kernel.box("b", [20, 20, 20], (0, 0, 0))
    top = box.face("b/+z")
    assert sentry.survives("shell", box.shape, [top], 2.0) == "ok"
    # and a shell that goes through the sentry still builds and hollows
    hollow = modify.shell("s", box, ["b/+z"], 2.0)
    assert volume(hollow) < volume(box)


def test_extend_says_which_faces_it_dropped():
    """The result of an extend is the one grown face. The other five of a box
    were gone and `dropped` was empty, so a reference to one of them missed
    without a word."""
    from cadcore.geometry.solids import surfaces

    box = kernel.box("b", [20, 20, 20], (0, 0, 0))
    grown = surfaces.extend("e", box, "b/+z", 5.0)
    assert sorted(grown.dropped) == sorted(n for n, _ in box.names if n != "b/+z")
    assert grown.face("b/+z") is not None              # the old name still means it


def test_a_body_with_a_nameless_face_is_not_drawn_with_a_hole():
    from cadcore.errors import CadError
    from cadcore.geometry.core.naming import Body
    from cadcore.geometry.io.tessellate import tessellate

    box = kernel.box("b", [20, 20, 20], (0, 0, 0))
    short = Body(box.shape, list(box.names[:-1]), {}, [])
    with pytest.raises(CadError) as refused:
        tessellate(short)
    assert refused.value.kind == "invalid_shape"
    assert refused.value.detail["named"] == 5


def test_a_draft_about_a_curved_neutral_is_refused_by_kind():
    from cadcore.errors import CadError
    from cadcore.geometry.solids import modify

    tube = kernel.cylinder("c", 10.0, 20.0)
    with pytest.raises(CadError) as refused:
        modify.draft("d", tube, ["c/top"], 5.0, neutral="c/side")
    assert refused.value.kind == "non_planar_face"


def test_a_renamed_face_is_found_under_its_new_name():
    """`body.names[i] = (new, face)` left the face index at the old name."""
    box = kernel.box("b", [20, 20, 20], (0, 0, 0))
    assert box.face("b/+z") is not None                # builds the index
    box.rename([n for n, _ in box.names].index("b/+z"), "lid/top")
    assert box.face("lid/top") is not None
    assert box.face("b/+z") is None
