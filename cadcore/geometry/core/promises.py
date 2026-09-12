"""What an operation promises about its result, and the check that holds it to it.

A refusal says what did not build. This says what a *success* means -- a
valid shape, a closed solid, fewer faces than before -- so the checks live in
one place instead of growing wherever somebody was bitten last.
"""
from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass

from ...errors import CadError

#: A tolerance on "the same size". Volumes are in cubic millimetres and the
#: smallest deliberate cut in the examples is a few of them, so a part in a
#: million of what went in is far below anything meant and far above the noise.
SAME = 1e-6


@dataclass(frozen=True)
class Promise:
    """What must be true of a result for the operation to have succeeded.

    ``against`` names the argument the result is measured against, and
    ``effect`` what must have happened to it:

    ``less``      strictly smaller -- a cut that removed nothing removed nothing
    ``at_least``  no smaller -- a fuse cannot lose material
    ``at_most``   no larger
    ``any``       nothing to say, which is a real answer and has to be written

    ``valid`` asks OpenCASCADE whether the result is a shape it accepts. It is
    on unless the operation genuinely does not produce one -- a compound of
    separate parts, a flat blank, somebody else's imported file.
    """

    against: str | None = None
    effect: str = "any"
    valid: bool = True
    why: str = ""


def _size(value):
    from . import measure

    try:
        return measure.volume(value)
    except Exception:                                            # noqa: BLE001
        return None                       # not a thing with a volume: say nothing


def broken(name: str, promise: Promise, bound, out) -> CadError | None:
    """The refusal this result has earned, or None."""
    from .naming import Body

    if not isinstance(out, Body):
        return None
    if promise.valid:
        # BRepCheck costs more than the boolean it checks, so the promise is
        # recorded here and checked once per build by `assure`
        out.promised_valid = True
    if promise.against is None or promise.effect == "any":
        return None
    before = _size(bound.arguments.get(promise.against))
    after = _size(out)
    if before is None or after is None:
        return None
    slack = max(abs(before), 1.0) * SAME
    if promise.effect == "less" and after > before - slack:
        return CadError("no_intersection",
                        f"{name} changed nothing: {promise.why}",
                        {"operation": name, "volume_mm3": round(after, 6),
                         "was_mm3": round(before, 6),
                         "hint": "the two solids do not overlap where this "
                                 "expected them to"})
    if promise.effect == "at_least" and after < before - slack:
        return CadError("empty_result",
                        f"{name} lost material: {promise.why}",
                        {"operation": name, "volume_mm3": round(after, 6),
                         "was_mm3": round(before, 6)})
    if promise.effect == "at_most" and after > before + slack:
        return CadError("empty_result",
                        f"{name} gained material: {promise.why}",
                        {"operation": name, "volume_mm3": round(after, 6),
                         "was_mm3": round(before, 6)})
    return None


def sound(shape) -> bool:
    """Whether OpenCASCADE accepts this shape; True when it cannot tell."""
    from OCP.BRepCheck import BRepCheck_Analyzer

    try:
        return BRepCheck_Analyzer(shape).IsValid()
    except Exception:                                            # noqa: BLE001
        return True                       # cannot tell: do not invent a refusal


def assure(body, where: str) -> None:
    """Refuse a body that was promised valid and is not."""
    if getattr(body, "promised_valid", False) and not sound(body.shape):
        raise CadError(
            "invalid_shape",
            f"{where} produced a shape OpenCASCADE will not accept",
            {"feature": where,
             "hint": "it can have the right size and the wrong topology, "
                     "which is the kind of result that survives every check "
                     "made of the numbers"})


def guarded(fn, name: str, promise: Promise):
    """The same operation, held to what it says it does."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def checked(*args, **kwargs):
        out = fn(*args, **kwargs)
        try:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
        except TypeError:
            return out                    # it will refuse the arguments itself
        trouble = broken(name, promise, bound, out)
        if trouble is not None:
            raise trouble
        return out

    checked.promise = promise
    return checked


def keep_promises(exports: dict, promises: dict) -> None:
    """Wrap a façade's exports with their promises, once, at import.

    Only what the façade hands out. Inside the kernel a module imports its
    neighbour directly -- that is the rule that keeps the dependencies one way
    -- and those calls are the operation's own working, not a claim made to a
    feature. `thread` cuts and fuses half a dozen times to build one ridge, and
    it measures the result itself, in its own terms.
    """
    for name, promise in promises.items():
        fn = exports.get(name)
        if callable(fn) and not hasattr(fn, "promise"):
            exports[name] = guarded(fn, name, promise)
