"""Study spec validation: unknown keys (e.g. `load` for `loads`), missing
fields and studies with nothing driving them are refused."""
import pytest

from cadcore.errors import CadError
from cadcore.simulation.spec import KINDS, check

LOADED = {"id": "s", "type": "static_structural", "fix": ["base/-z"],
          "loads": [{"face": "top/+z", "force": [0, 0, -500]}]}


def test_a_study_that_says_what_it_means_is_accepted():
    assert check(LOADED) == "static_structural"


def test_a_key_nothing_reads_is_refused_by_name():
    spec = dict(LOADED)
    spec.pop("loads")
    spec["load"] = [{"face": "top/+z", "force": [0, 0, -500]}]
    with pytest.raises(CadError) as caught:
        check(spec)
    assert caught.value.kind == "unknown_argument"
    assert "load" in caught.value.detail["unknown"]
    assert "loads" in caught.value.detail["understood"]


def test_a_static_study_with_nothing_pushing_on_it_is_refused():
    """Checks that an unloaded static study is refused rather than given infinite safety factor."""
    spec = dict(LOADED)
    spec.pop("loads")
    with pytest.raises(CadError) as caught:
        check(spec)
    assert caught.value.kind == "nothing_to_solve"
    assert caught.value.detail["expected_one_of"] == ["loads", "gravity"]


def test_gravity_alone_is_something_pushing():
    spec = dict(LOADED)
    spec.pop("loads")
    spec["gravity"] = True
    assert check(spec) == "static_structural"


def test_a_modal_study_needs_nothing_pushing_on_it():
    """Checks that a modal study needs no load."""
    assert check({"type": "modal", "fix": ["base/-z"], "modes": 4}) == "modal"


def test_a_thermal_study_with_no_temperature_anywhere_is_refused():
    with pytest.raises(CadError) as caught:
        check({"type": "thermal", "fix": ["base/-z"]})
    assert caught.value.kind == "nothing_to_solve"


def test_a_load_without_a_force_is_the_same_class_of_mistake():
    spec = dict(LOADED, loads=[{"face": "top/+z"}])
    with pytest.raises(CadError) as caught:
        check(spec)
    assert caught.value.kind == "missing_argument"
    assert "force" in caught.value.message


def test_an_unknown_type_lists_the_ones_there_are():
    with pytest.raises(CadError) as caught:
        check({"type": "fatigue"})
    assert caught.value.kind == "unknown_study_type"
    assert set(caught.value.detail["available"]) == set(KINDS)


def test_every_study_the_runners_implement_is_declared():
    from cadcore.simulation.study import RUNNERS

    assert set(RUNNERS) == set(KINDS), \
        "a study type that runs and is not declared is one nothing checks"


def test_every_shipped_study_still_says_what_it_means():
    import json
    import pathlib
    import subprocess

    root = pathlib.Path(__file__).resolve().parents[3]
    listed = subprocess.run(["git", "ls-files", "--", "examples"], cwd=root,
                            capture_output=True, text=True, encoding="utf-8").stdout.split()
    for name in listed:
        if not name.endswith(".json"):
            continue
        doc = json.loads((root / name).read_text(encoding="utf-8"))
        studies = list(doc.get("studies") or [])
        studies += list((doc.get("meta") or {}).get("studies") or [])
        for spec in studies:
            check(spec)


def test_a_convergence_block_on_a_study_that_ignores_it_is_refused():
    """Checks that `convergence` is refused on study types that do not read it."""
    with pytest.raises(CadError) as caught:
        check({"type": "modal", "fix": ["base/-z"],
               "convergence": {"levels": 3}})
    assert caught.value.kind == "unknown_argument"
    assert "convergence" in caught.value.detail["unknown"]


def test_and_the_static_study_still_takes_one():
    assert check(dict(LOADED, convergence={"levels": 3})) == "static_structural"
