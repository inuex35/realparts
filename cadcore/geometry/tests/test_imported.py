"""Face naming on a solid imported from another kernel.

Face names come from the surface shape, and a STEP file from another kernel
routinely writes a flat face as a B-spline and a cylinder wall as spline
patches. Naming must recognise the underlying shape so imported faces get
roles (`+z`, `side`) rather than positional `faceN` names.

The fixture is a checked-in solid written by a different kernel, so the tests
exercise a foreign file rather than a round trip of this kernel's own output.
"""
import pathlib

import pytest

from ..io.exchange import import_step
from ..core.naming import face_info, is_round

FOREIGN = pathlib.Path(__file__).resolve().parent / "data" / "foreign.step"


@pytest.fixture(scope="module")
def foreign():
    if not FOREIGN.exists():
        pytest.skip("no foreign STEP fixture")
    return import_step("part", str(FOREIGN))


def test_the_surfaces_really_are_written_as_splines(foreign):
    """Guards: the fixture really contains B-spline surfaces; otherwise the
    rest of the file tests nothing."""
    from OCP.GeomAbs import GeomAbs_SurfaceType

    kinds = {face_info(f)["type"] for _, f in foreign.names}
    assert GeomAbs_SurfaceType.GeomAbs_BSplineSurface in kinds


def test_a_flat_face_written_as_a_spline_is_still_named_for_its_axis(foreign):
    named = {name for name, _ in foreign.names}
    assert "part/+z" in named and "part/-z" in named


def test_a_round_wall_written_as_spline_patches_is_still_a_side(foreign):
    named = {name for name, _ in foreign.names}
    assert "part/side" in named


def test_and_the_kernel_agrees_it_is_round(foreign):
    """`is_round` is the single roundness test used by thread, printability
    and bolt seat, and must hold for a spline-written cylinder."""
    side = foreign.face("part/side")
    assert side is not None
    info = face_info(side)
    assert is_round(info) and info["radius"] > 0 and "axis_origin" in info


def test_the_things_this_project_is_for_work_on_it(tmp_path):
    """Measure, drill, thread, printability, drawing and STEP export all work
    on a foreign part."""
    from cadcore.service.server import Session

    if not FOREIGN.exists():
        pytest.skip("no foreign STEP fixture")
    session = Session(autosave=False)
    session.op_open(str(FOREIGN))
    session.op_build()

    assert session.op_measure(kind="area", faces=["part/+z"])["value"] > 0
    assert session.op_add_hole(face="part/+z", diameter=4.0, depth=5.0)["faces"] > 0
    assert session.op_add_thread(face="part/side", pitch=2.0,
                                 length=8.0)["notes"]["thread"]["designation"]
    assert session.op_printability(min_wall=1.0)["unsupported_mm2"] >= 0
    assert session.op_drawing(spec={"views": ["front", "top"], "size": "A4"})["bytes"] > 0
    assert session.op_export_step(path=str(tmp_path / "back.step"))["bytes"] > 0


def test_every_face_of_the_foreign_part_can_be_referred_to(foreign):
    """All six: two planes, two patches of a cylinder wall, two of a sphere."""
    unnamed = [n for n, _ in foreign.names if "/face" in n]
    assert not unnamed, unnamed


def test_a_surface_that_is_genuinely_freeform_keeps_no_role():
    """Guards: a lofted freeform face gets no canonical shape and no role; a
    face called `+z` that curves is worse than an unnamed one."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.gp import gp_Pnt

    from ..core.naming import faces_of

    algo = BRepOffsetAPI_ThruSections(True, False)
    for z, corners in ((0, [(0, 0), (30, 0), (30, 20), (0, 20)]),
                       (15, [(4, 3), (26, 1), (22, 26), (2, 17)]),
                       (30, [(9, 8), (15, 2), (28, 19), (6, 22)])):
        poly = BRepBuilderAPI_MakePolygon()
        for x, y in corners:
            poly.Add(gp_Pnt(x, y, z))
        poly.Close()
        algo.AddWire(poly.Wire())
    algo.Build()

    shapes = [face_info(f).get("shape") for f in faces_of(algo.Shape())]
    assert shapes.count(None) == 4, "the four lofted sides are not canonical"
    assert shapes.count("plane") == 2, "and the two caps are"


@pytest.mark.parametrize("kind,role", [("cone", "taper"), ("sphere", "ball"),
                                       ("torus", "ring")])
def test_a_cone_a_sphere_and_a_torus_have_a_name_at_all(kind, role):
    """Cone, sphere and torus faces get the roles `taper`, `ball` and `ring`
    rather than positional names."""
    from OCP.BRepPrimAPI import (BRepPrimAPI_MakeCone, BRepPrimAPI_MakeSphere,
                                 BRepPrimAPI_MakeTorus)
    from ..core.naming import faces_of, role_names

    shape = {"cone": lambda: BRepPrimAPI_MakeCone(8.0, 3.0, 12.0).Shape(),
             "sphere": lambda: BRepPrimAPI_MakeSphere(8.0).Shape(),
             "torus": lambda: BRepPrimAPI_MakeTorus(8.0, 3.0).Shape()}[kind]()
    names = [n for n, _ in role_names("p", faces_of(shape))]
    assert any(n.startswith("p/" + role) for n in names), names


# --- the invariant --------------------------------------------------------------
#
# The share of faces with a role is not the measure: a hexagonal prism's
# slanted walls have no axis to be named for, and a face 0.23 degrees off
# horizontal must not call itself `-z`. The invariant is that a face whose
# shape the kernel knows gets the role for it, and any other face gets a
# positional name that stays stable.

SHAPED = pathlib.Path(__file__).resolve().parent / "data" / "many_shapes.step"


@pytest.mark.parametrize("fixture", ["foreign.step", "many_shapes.step"])
def test_every_face_with_a_shape_this_kernel_knows_has_the_role_for_it(fixture):
    from ..core.naming import SHAPE_ROLES, axis_role

    path = pathlib.Path(__file__).resolve().parent / "data" / fixture
    if not path.exists():
        pytest.skip("no fixture")
    body = import_step("part", str(path))

    wrong = []
    for name, face in body.names:
        info = face_info(face)
        shape = info.get("shape")
        if shape is None:
            continue
        role = axis_role(info["normal"]) if shape == "plane" else SHAPE_ROLES[shape]
        if role is None:
            continue                    # a plane with no axis has no role
        if "/%s" % role not in name:
            wrong.append((name, shape, role))
    assert not wrong, wrong


def test_a_part_of_four_counterbores_a_boss_a_bead_and_a_dome_is_all_named():
    """Every shape the kernel knows, in one solid rebuilt by eleven booleans,
    is named."""
    if not SHAPED.exists():
        pytest.skip("no fixture")
    body = import_step("part", str(SHAPED))
    unnamed = [n for n, _ in body.names if "/face" in n]
    assert not unnamed, unnamed
    roles = {n.split("/")[-1].split("#")[0] for n, _ in body.names}
    assert {"taper", "ball", "ring", "side"} <= roles, sorted(roles)


def test_reading_the_same_file_twice_gives_the_same_names():
    """Positional names are deterministic across imports."""
    if not SHAPED.exists():
        pytest.skip("no fixture")
    once = sorted(n for n, _ in import_step("part", str(SHAPED)).names)
    twice = sorted(n for n, _ in import_step("part", str(SHAPED)).names)
    assert once == twice
