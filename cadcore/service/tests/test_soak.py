"""Random editing sessions, with the invariants checked after every step."""
import pytest

from cadcore.service.soak import run


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_editing_sketch_plate_at_random_holds_its_invariants(seed):
    out = run("examples/sketch_plate.json", steps=40, seed=seed)
    assert out.violations == []
    assert out.succeeded > 10           # a run that refuses everything proves nothing


def test_editing_the_bracket_at_random_holds_its_invariants():
    out = run("examples/bracket.json", steps=25, seed=7)
    assert out.violations == []


def test_a_refused_edit_is_the_common_case_and_costs_nothing():
    """Most random edits are refused; they must be refused by kind and leave
    nothing behind."""
    out = run("examples/sketch_plate.json", steps=40, seed=5)
    assert out.violations == []
    assert set(out.refused) <= {
        "unresolved_reference", "cyclic_graph", "feature_in_use", "no_solid",
        "fillet_failed", "chamfer_failed", "shell_failed", "empty_result",
        "boolean_failed", "invalid_shape", "empty_selection", "sketch_underconstrained",
        "sketch_unsolved", "not_a_chain_feature", "bad_parameter", "unknown_parameter",
        "outside_envelope", "degenerate_segment", "bad_profile", "open_profile",
        "extrude_failed", "no_drawing", "unknown_feature", "bad_expression",
        "non_planar_face", "unknown_face", "empty_sketch", "draft_failed",
    }, "an unexpected refusal kind means an error escaped its type"
