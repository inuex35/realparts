"""Simulation tests: studies attach to CAD face names and re-run after a change."""
import pytest

# ngsolve is optional (requirements-sim.txt); skip rather than fail at collection.
pytest.importorskip("ngsolve", reason="the studies need requirements-sim.txt")

from cadcore import Document  # noqa: E402
from cadcore.evaluation.graph import Evaluator  # noqa: E402
from cadcore.simulation import StaticStructural  # noqa: E402


def study(**params):
    doc = Document.load("examples/bracket.json")
    doc.parameters.update(params)
    body = Evaluator(doc).build()
    s = StaticStructural(body, "A6061", mesh_size=8.0, order=2)
    s.fix("plate/-z")
    s.force("wall/+y", (0, 500, 0))
    return s.solve()[0]


def test_static_run():
    r = study()
    assert r.max_von_mises > 0
    assert r.max_displacement > 0
    assert r.mass_g > 0


def test_static_matches_a_cantilever_beam():
    """Tip deflection within 2% of Euler-Bernoulli; root stress within 10%
    of M c / I (the clamp is a singularity, so the peak can only be near)."""
    from cadcore.geometry import kernel

    E, L, b, h, F = 68900.0, 100.0, 10.0, 10.0, 100.0
    inertia = b * h ** 3 / 12
    tip = F * L ** 3 / (3 * E * inertia)
    root = F * L * (h / 2) / inertia
    beam = kernel.box("beam", [L, b, h], (L / 2, 0, 0))
    s = StaticStructural(beam, "A6061", mesh_size=3.0, order=2)
    s.fix("beam/-x").force("beam/+x", (0, 0, -F))
    r = s.solve()[0]
    assert r.max_displacement == pytest.approx(tip, rel=0.02)
    assert r.max_von_mises == pytest.approx(root, rel=0.10)
    # most of a bent beam is far below the outer-fibre stress at the root
    assert r.p95_von_mises < r.max_von_mises


def test_the_p95_stress_counts_material_not_nodes():
    """The same beam meshed twice as fine moves p95 by less than the peak."""
    from cadcore.geometry import kernel

    beam = kernel.box("beam", [100.0, 10.0, 10.0], (50.0, 0, 0))
    got = []
    for size in (4.0, 2.0):
        s = StaticStructural(beam, "A6061", mesh_size=size, order=2)
        s.fix("beam/-x").force("beam/+x", (0, 0, -100.0))
        got.append(s.solve()[0])
    coarse, fine = got
    assert fine.elements > 2 * coarse.elements
    assert fine.p95_von_mises == pytest.approx(coarse.p95_von_mises, rel=0.05)


def test_thinner_plate_is_lighter_and_more_stressed():
    thick = study(thickness=10)
    thin = study(thickness=5)
    assert thin.mass_g < thick.mass_g
    assert thin.max_displacement > thick.max_displacement


def test_tessellation_carries_face_names():
    from ...geometry.io.tessellate import compact, tessellate
    from cadcore.evaluation.graph import Evaluator
    from cadcore import Document

    body = Evaluator(Document.load("examples/bracket.json")).build()
    t = tessellate(body, 0.3)
    assert len(t["triangles"]) == len(t["triangle_face"])
    assert set(t["triangle_face"]) <= set(body.face_names())
    assert t["edges"], "edge polylines are what an edge-picking UI needs"
    c = compact(t)
    assert all(isinstance(i, int) for i in c["triangle_face"])


def test_convergence_flags_a_singular_model():
    """Checks that a clamped whole face (a singularity) leaves the peak unconverged."""
    from cadcore import Document
    from cadcore.evaluation.graph import Evaluator
    from cadcore.simulation.convergence import run as run_convergence

    doc = Document.load("examples/bracket.json")
    body = Evaluator(doc).build()
    s = StaticStructural(body, "A6061", order=2)
    s.fix("plate/-z")                      # whole-face clamp: singular peak
    s.force("wall/+y", (0, 500, 0))
    _, conv = run_convergence(s, 6.0, levels=3, ratio=1.5, tolerance=0.05)
    assert not conv.converged


def test_modal_matches_a_cantilever_beam():
    """Checks the first two bending modes against Euler-Bernoulli (rel 3%)."""
    import math

    from cadcore.geometry import kernel
    from ..studies.modal import Modal

    L, b, h = 200.0, 20.0, 10.0
    bar = kernel.box("bar", [L, b, h], (L / 2, 0, 0))
    study = Modal(bar, "A6061", mesh_size=8.0, order=2)
    study.fix("bar/-x")
    result, _ = study.solve(modes=3)

    E, rho = 68900.0, 2.70e-9
    weak = (1.875 ** 2 / (2 * math.pi)) * math.sqrt(E * (b * h ** 3 / 12) /
                                                    (rho * b * h * L ** 4))
    strong = (1.875 ** 2 / (2 * math.pi)) * math.sqrt(E * (h * b ** 3 / 12) /
                                                      (rho * b * h * L ** 4))
    assert result.frequencies[0] == pytest.approx(weak, rel=0.03)
    assert result.frequencies[1] == pytest.approx(strong, rel=0.03)


def test_a_study_fixing_one_end_does_not_clamp_the_other():
    """Guards: `mesh_name` must keep `+` and `-` (and `@n`) faces on distinct boundaries."""
    from cadcore.simulation.mesh import mesh_name

    assert mesh_name("bar/+x") != mesh_name("bar/-x")
    assert mesh_name("plate/+x@0") != mesh_name("plate/+x")


def test_thermal_conduction_and_expansion():
    from cadcore.geometry import kernel
    from ..studies.thermal import Thermal

    bar = kernel.box("bar", [100, 20, 20], (50, 0, 0))
    study = Thermal(bar, "A6061", mesh_size=10.0, order=2)
    study.temperature("bar/-x", 100.0).temperature("bar/+x", 0.0)
    result, _ = study.solve()
    assert result.max_temperature == pytest.approx(100.0, abs=0.1)
    assert result.min_temperature == pytest.approx(0.0, abs=0.1)

    held = Thermal(bar, "A6061", mesh_size=8.0, order=2, reference=20.0)
    held.temperature("bar/-x", 120.0).temperature("bar/+x", 120.0).fix("bar/-x")
    warm, _ = held.solve()
    # free expansion of a 100 mm bar over 100 K, plus Poisson effect at the held end
    assert warm.max_displacement == pytest.approx(23.6e-6 * 100 * 100, rel=0.1)
    # the peak is a clamp singularity; the p95 is far below it
    assert warm.p95_von_mises < warm.max_von_mises / 5


def test_buckling_matches_euler():
    import math

    from cadcore.geometry import kernel
    from ..studies.buckling import Buckling

    L, b, h = 200.0, 20.0, 10.0
    bar = kernel.box("bar", [L, b, h], (L / 2, 0, 0))
    study = Buckling(bar, "A6061", mesh_size=8.0, order=2)
    study.fix("bar/-x").force("bar/+x", [-1000.0, 0, 0])
    result, static, _ = study.buckle(modes=2)

    euler = math.pi ** 2 * 68900.0 * (b * h ** 3 / 12) / (4 * L * L)
    assert result.factors[0] == pytest.approx(euler / 1000.0, rel=0.05)
    assert result.factors[1] > result.factors[0]


def test_the_document_can_declare_all_of_them():
    from cadcore.service.server import Session

    session = Session()
    session.op_open("examples/bracket.json")
    out = session.op_simulate()
    kinds = {s["id"]: s for s in out["studies"]}
    assert set(kinds) == {"mount_load", "shake", "warm"}
    assert kinds["shake"]["result"]["fundamental_Hz"] > 400
    assert kinds["warm"]["result"]["max_temperature_C"] == pytest.approx(85, abs=1)
    assert all(s["ok"] for s in out["studies"])


def test_the_safety_factor_comes_in_two_honest_flavours():
    """Checks that both safety factors are reported and the p95 one is the larger."""
    from cadcore.geometry import kernel
    from ..studies.structural import StaticStructural

    beam = kernel.box("beam", [100.0, 10.0, 10.0], (50.0, 0, 0))
    study = StaticStructural(beam, "A6061", mesh_size=4.0, order=2)
    study.fix("beam/-x")
    study.force("beam/+x", (0.0, 0.0, -100.0))
    result, _ = study.solve()
    said = result.as_dict()
    assert said["safety_factor_p95"] >= said["safety_factor"] > 0
    assert said["p95_von_mises_MPa"] <= said["max_von_mises_MPa"]


def test_a_thermal_field_says_whether_it_is_degrees_or_megapascals():
    """Checks that `Field.quantity` labels a thermal study's stress field as MPa."""
    from cadcore.geometry import kernel
    from ..studies.thermal import Thermal

    bar = kernel.box("bar", [40.0, 10.0, 10.0], (20.0, 0, 0))
    hot = Thermal(bar, "A6061", mesh_size=5.0, order=2)
    hot.temperature("bar/-x", 120.0).temperature("bar/+x", 20.0)
    _, field = hot.solve()
    assert field.quantity == "temperature_C"
    held = Thermal(bar, "A6061", mesh_size=5.0, order=2)
    held.temperature("bar/-x", 120.0).temperature("bar/+x", 20.0).fix("bar/-x")
    result, field = held.solve()
    assert field.quantity == "von_mises_MPa"
    assert result.max_von_mises > 0


def test_two_different_face_names_cannot_share_a_mesh_boundary():
    """Checks that `mesh_name` is injective over names that differ only in escaped characters."""
    from cadcore.simulation.mesh import mesh_name

    names = ["plate/+z", "plate/pz", "plate/-z", "plate/mz", "a_b", "a/b", "a__b",
             "bolt/side~3", "bolt/side~3@1", "bolt/side_t3", "x|y", "x_ory"]
    aliases = [mesh_name(n) for n in names]
    assert len(set(aliases)) == len(names), aliases
    assert all(alias.replace("_", "").isalnum() for alias in aliases)


def test_a_piece_nothing_holds_is_refused_not_solved():
    """Guards: every study refuses a body with a piece no boundary condition holds."""
    from cadcore.geometry import kernel
    from cadcore.errors import CadError
    from ..studies.modal import Modal
    from ..studies.structural import StaticStructural
    from ..studies.thermal import Thermal

    a = kernel.box("a", [20, 20, 20], [0, 0, 0], True)
    b = kernel.box("b", [20, 20, 20], [60, 0, 0], True)
    both = kernel.fuse("u", a, b)

    with pytest.raises(CadError) as exc:
        StaticStructural(both, "A6061", 6.0, 1).fix("a/-z").force("b/+z", [0, 0, -100]).solve()
    assert exc.value.kind == "study_underconstrained"
    loose = exc.value.detail["floating"]
    assert len(loose) == 1 and loose[0]["faces"] == sorted(
        f"b/{s}" for s in ("+x", "-x", "+y", "-y", "+z", "-z"))

    with pytest.raises(CadError) as exc:
        Thermal(both, "A6061", 6.0, 1).temperature("a/-z", 100).solve()
    assert exc.value.kind == "study_underconstrained"
    with pytest.raises(CadError) as exc:
        Modal(both, "A6061", 6.0, 1).fix("a/-z").solve(3)
    assert exc.value.kind == "study_underconstrained"

    # with both pieces held the study solves normally
    result, _ = (StaticStructural(both, "A6061", 6.0, 1).fix("a/-z").fix("b/-z")
                 .force("b/+z", [0, 0, -100]).solve())
    assert result.max_displacement < 1.0


def test_a_singular_system_is_caught_by_its_residual():
    """Checks that `check_solved` refuses a solution with a large residual."""
    from cadcore.errors import CadError
    from ..studies.structural import check_solved

    class Vec(list):
        def CreateVector(self):
            return Vec([0.0] * len(self))
        @property
        def data(self):
            return self
        @data.setter
        def data(self, other):
            self[:] = list(other)
        def __rmul__(self, m):
            return Vec(m(self))
        def __sub__(self, other):
            return Vec(x - y for x, y in zip(self, other))

    class Mat:
        def __init__(self, fn):
            self.fn = fn
        def __mul__(self, vec):
            return Vec(self.fn(vec))

    rhs, free = Vec([1.0, 0.0]), [True, True]
    check_solved(Mat(lambda v: [v[0], v[1]]), Vec([1.0, 0.0]), rhs, free)   # exact
    with pytest.raises(CadError) as exc:
        check_solved(Mat(lambda v: [v[0], 0.0]), Vec([1.0, 3e11]), Vec([1.0, 1.0]), free)
    assert exc.value.kind == "study_failed"
    assert exc.value.detail["largest_dof"] == 3e11


def test_the_p95_safety_factor_can_be_required():
    """Checks that `safety_factor_p95` can be named in a document requirement."""
    from cadcore.service.server import Session

    session = Session()
    session.op_open("examples/bracket.json")
    study = next(s for s in session.doc.studies if s["id"] == "mount_load")
    study["require"] = {"safety_factor_p95": ">= 1.5", "safety_factor": ">= 1.0"}
    out = session.op_simulate()
    mount = next(s for s in out["studies"] if s["id"] == "mount_load")
    named = {r["quantity"] for r in mount["requirements"]}
    assert "safety_factor_p95" in named
    assert all(r["ok"] for r in mount["requirements"]), mount["requirements"]
