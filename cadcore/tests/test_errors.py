"""Every kind a refusal carries is declared in `errors.KINDS`, and every
declared kind is raised somewhere.

Read from the syntax tree rather than by running code, so refusals no test
reaches are covered. A raised kind not in the registry is a typo that reads
fine to a human; a declared kind nothing raises is a leftover.
"""
import ast
import collections
import pathlib

import pytest

from cadcore.errors import KINDS

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGES = ("cadcore",)


def raised() -> dict:
    """Every kind the tree can hand back, with where it does so.

    Two forms count: `CadError("kind", ...)` (its subclass `MechanismError` too), and a refusal reply
    `{"ok": False, "kind": ...}`, which is how the transport answers without
    raising.
    """
    found = collections.defaultdict(list)
    for package in PACKAGES:
        for path in sorted(p for p in (ROOT / package).rglob("*.py") if "tests" not in p.parts):
            if "__pycache__" in str(path):
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                where = "%s:%d" % (path.relative_to(ROOT),
                                   getattr(node, "lineno", 0))
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in ("CadError", "MechanismError")):   # a CadError by another name
                    first = node.args[0] if node.args else None
                    key = first.value if isinstance(first, ast.Constant) else None
                    found[key].append(where)
                elif isinstance(node, ast.Dict):
                    # only a refusal reply, `{"ok": False, "kind": ...}`: any
                    # other dict with a "kind" key is not a refusal
                    fields = {k.value: v for k, v in zip(node.keys, node.values)
                              if isinstance(k, ast.Constant)}
                    ok, kind = fields.get("ok"), fields.get("kind")
                    if (isinstance(ok, ast.Constant) and ok.value is False
                            and isinstance(kind, ast.Constant)
                            and isinstance(kind.value, str)):
                        found[kind.value].append(where)
    return found


RAISED = raised()


def test_every_kind_raised_is_a_declared_one():
    """A kind that is not in the registry is a typo, and reads fine to a human."""
    stray = {k: v for k, v in RAISED.items() if k is not None and k not in KINDS}
    assert not stray, "\n".join(
        "%s raised at %s is not in errors.KINDS" % (k, ", ".join(v[:3]))
        for k, v in sorted(stray.items()))


def test_every_declared_kind_is_actually_raised():
    """A kind nothing raises is a promise the code has stopped keeping."""
    dead = sorted(KINDS - set(RAISED))
    assert not dead, "declared and never raised: " + ", ".join(dead)


def test_a_kind_is_always_a_literal():
    """A computed kind is outside every check: the registry, grep and the reader."""
    assert not RAISED.get(None), \
        "CadError with a non-literal kind: " + ", ".join(RAISED.get(None, [])[:5])


@pytest.mark.parametrize("pair", [
    ("bad_argument", "bad_arguments"),
    ("not_cylindrical", "not_a_cylinder"),
    ("underconstrained", "study_underconstrained"),
])
def test_the_spellings_that_were_merged_stay_merged(pair):
    """Each pair was two spellings of one refusal; the removed one stays removed."""
    gone, kept = pair
    assert gone not in KINDS and gone not in RAISED, gone
    assert kept in KINDS


def test_the_near_misses_are_still_two_things():
    """The pairs that look like duplicates and are not (see errors.py) are
    both still raised."""
    for a, b in (("no_solid", "not_a_solid"),
                 ("unknown_feature", "unknown_feature_type"),
                 ("sketch_underconstrained", "study_underconstrained")):
        assert a in RAISED and b in RAISED, (a, b)
