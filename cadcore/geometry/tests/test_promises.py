"""Each kernel operation declares what its result must satisfy.

`kernel.PROMISES` states the required effect of every body-returning
operation (`cut` must remove material, `fuse` must not lose any) and the
facade checks it, so a tool that misses its target is refused rather than
reported as done. Every operation in `kernel.__all__` that returns a Body
must have an entry (enforced below).
"""
import pytest

from cadcore.geometry import kernel
from cadcore.errors import CadError
from ..core.measure import volume


def _box():
    return kernel.box("a", (40.0, 30.0, 20.0))


def test_a_cut_whose_tool_misses_is_not_a_success():
    target = _box()
    miss = kernel.cylinder("miss", 3.0, 50.0, at=(500.0, 0.0, 0.0))
    with pytest.raises(CadError) as caught:
        kernel.cut("c", target, miss)
    assert caught.value.kind == "no_intersection"
    assert "changed nothing" in caught.value.message


def test_a_cut_that_does_reach_still_works():
    target = _box()
    hit = kernel.cylinder("hit", 3.0, 50.0, at=(0.0, 0.0, 0.0))
    out = kernel.cut("c", target, hit)
    assert volume(out) < volume(target)


def test_a_fuse_that_swallows_its_tool_is_allowed():
    """A tool wholly inside the target adds nothing and is valid, so `fuse`
    promises `at_least`, not `less`."""
    target = _box()
    inside = kernel.box("b", (4.0, 4.0, 4.0))
    out = kernel.fuse("f", target, inside)
    assert volume(out) == pytest.approx(volume(target))


def test_an_intersection_cannot_come_back_bigger_than_what_went_in():
    target = _box()
    tool = kernel.box("b", (10.0, 10.0, 10.0))
    out = kernel.common("i", target, tool)
    assert volume(out) <= volume(target) + 1e-6


def test_the_promise_travels_with_the_operation():
    assert kernel.cut.promise.effect == "less"
    assert kernel.fuse.promise.effect == "at_least"
    assert kernel.box.promise.effect == "any"


def test_every_operation_that_hands_back_a_body_says_what_it_promises():
    """Every operation in `kernel.__all__` that returns a Body has an entry
    in `kernel.PROMISES`."""
    from ..core.naming import Body

    import inspect

    undeclared = []
    for name in kernel.__all__:
        fn = getattr(kernel, name)
        if not callable(fn) or isinstance(fn, type):
            continue
        hints = inspect.signature(fn).return_annotation
        makes_a_body = hints is Body or (isinstance(hints, str)
                                         and "Body" in hints)
        if makes_a_body and name not in kernel.PROMISES:
            undeclared.append(name)
    assert not undeclared, (
        "an operation that hands back a body and does not say what that means: "
        + ", ".join(undeclared) + " -- add it to kernel.PROMISES, `Promise()` "
        "on its own if there is genuinely nothing to say")


def test_a_promise_names_an_argument_the_operation_actually_takes():
    """Guards: a promise measured against an argument the operation does not
    take would pass on every result."""
    import inspect

    wrong = []
    for name, promise in kernel.PROMISES.items():
        if promise.against is None:
            continue
        fn = getattr(kernel, name, None)
        if fn is None:
            continue
        if promise.against not in inspect.signature(fn).parameters:
            wrong.append("%s.%s" % (name, promise.against))
    assert not wrong, "a promise measured against an argument that is not there: " \
                      + ", ".join(wrong)


def test_an_unsound_body_is_blamed_on_the_feature_that_made_it(monkeypatch):
    """The check runs once per build; the refusal still names the first
    feature whose body OpenCASCADE would not accept."""
    from cadcore.errors import CadError
    from cadcore.evaluation.graph import Evaluator
    from cadcore.geometry.core import promises
    from cadcore.model.document import Document

    doc = Document.load("examples/bracket.json")
    body = Evaluator(doc).build()
    assert body.promised_valid, "the last operation promised a valid shape"

    monkeypatch.setattr(promises, "sound", lambda shape: False)
    with pytest.raises(CadError) as exc:
        Evaluator(doc).build()
    assert exc.value.kind == "invalid_shape"
    assert exc.value.detail["feature"] == doc.features[0].id


def test_an_invalid_result_is_refused_rather_than_returned():
    """Guards: the bottle example must pass `BRepCheck_Analyzer`; volume and
    solid count cannot detect a self-intersecting thread wire."""
    from cadcore.model.document import Document
    from cadcore.evaluation.graph import Evaluator
    from OCP.BRepCheck import BRepCheck_Analyzer

    body = Evaluator(Document.load("examples/bottle.json")).build()
    assert BRepCheck_Analyzer(body.shape).IsValid()
