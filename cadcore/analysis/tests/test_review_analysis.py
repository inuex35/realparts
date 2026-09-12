"""The analyses answer what they say: a bottom view the way third-angle
projection stands it, a dashboard that trusts no unconverged study, a search
that reads expressions, a mechanism's refusal that is a refusal like any other."""
import pytest

from cadcore.analysis import drawing
from cadcore.errors import CadError, KINDS
from cadcore.mechanism import MechanismError
from cadcore.model.document import Document


def test_the_bottom_view_keeps_plus_x_on_the_right():
    (eye, up) = drawing.VIEWS["bottom"]
    right = (up[1] * eye[2] - up[2] * eye[1], up[2] * eye[0] - up[0] * eye[2], up[0] * eye[1] - up[1] * eye[0])
    front_eye, front_up = drawing.VIEWS["front"]
    front_right = (front_up[1] * front_eye[2] - front_up[2] * front_eye[1],
                   front_up[2] * front_eye[0] - front_up[0] * front_eye[2],
                   front_up[0] * front_eye[1] - front_up[1] * front_eye[0])
    assert right[0] * front_right[0] > 0, "a boss at +x lands on the same side under the front view"


def test_a_mechanism_refusal_is_a_refusal_by_kind():
    err = MechanismError("jammed", "stuck", {"at": 12})
    assert isinstance(err, CadError) and err.kind in KINDS and err.detail == {"at": 12}
    for kind in ("not_a_mechanism", "jammed", "planets_will_not_space", "planets_touch",
                 "cannot_assemble", "too_few_slots"):
        assert kind in KINDS


def test_the_search_reads_bounds_and_values_written_as_expressions():
    from cadcore.analysis.optimise import search

    doc = Document.from_dict({
        "parameters": {"w": 40.0, "t": "w/4"},
        "parameters_bounds": {"t": [4.0, "w/2"]},
        "result": "bar",
        "features": [{"id": "bar", "type": "box", "size": [200, 20, "t"]}]})
    out = search(doc, trials=4, objective="volume", seed=1)
    assert out["feasible"] >= 1 and 4.0 <= out["parameters"]["t"] <= 20.0


def test_a_design_outside_its_own_envelope_is_not_the_first_feasible_trial():
    from cadcore.analysis.optimise import search

    doc = Document.from_dict({
        "parameters": {"t": 30.0},
        "parameters_bounds": {"t": [4.0, 12.0]},
        "result": "bar",
        "features": [{"id": "bar", "type": "box", "size": [200, 20, "t"]}]})
    out = search(doc, trials=3, objective="volume", seed=1)
    assert all(h["trial"] > 0 for h in out["history"])
