"""Requirements: what the part must be, as rows in the document."""
from __future__ import annotations

from ...model import requirements
from ...errors import CadError


class RequirementOps:
    """Mixed into :class:`cadcore.ops.session.Session`."""

    def op_requirement_kinds(self) -> dict:
        """Every quantity a requirement may name, and every comparison.

        Read from the table that refuses a bad one, so a menu built from this
        and the refusal cannot disagree.
        """
        return {"quantities": {name: {"unit": q.unit, "about": q.about,
                                      "takes": list(q.takes), "costly": q.costly}
                               for name, q in requirements.QUANTITIES.items()},
                "compare": sorted(requirements.OPS)}

    def op_add_requirement(self, quantity: str, compare: str, value,
                           name: str | None = None, material: str | None = None,
                           min_wall: float | None = None,
                           overhang: float | None = None,
                           note: str | None = None) -> dict:
        """Say what the part must be, and be told on every build whether it is.

        ``quantity`` -- one of requirement_kinds: mass_g, bbox_max, printable ...
        ``compare`` -- a comparison: <=, <, >=, >, ==, !=
        ``value`` -- a number, or an expression over the parameters
        ``name`` -- what to call the row (its `id` in the document); invented
        when not given. `name` and not `id`, because `id` is the line
        protocol's request counter and an argument by that name never arrives

        This is status, not a gate: an edit that breaks a requirement is
        reported, not refused. `asserts` are the gates.
        """
        doc = self._require_doc()
        spec = {"quantity": quantity, "compare": compare, "value": value}
        for key, given in (("id", name), ("material", material), ("min_wall", min_wall),
                           ("overhang", overhang), ("note", note)):
            if given is not None:
                spec[key] = given
        requirements.check(spec)
        if name is not None and any(r.get("id") == name for r in doc.requirements):
            raise CadError("duplicate_id", f"a requirement called {name!r} already exists",
                           {"id": name})
        with self._edit() as document:
            document.requirements.append(spec)
            out = self._rebuild()
        return out

    def op_remove_requirement(self, name: str) -> dict:
        """Take a requirement out of the document, by its name (its `id`)."""
        doc = self._require_doc()
        rows = [r for r in doc.requirements if r.get("id") == name]
        if not rows:
            raise CadError("unknown_requirement", f"no requirement called {name!r}",
                           {"available": [r.get("id") for r in doc.requirements]})
        with self._edit() as document:
            document.requirements[:] = [r for r in document.requirements
                                        if r.get("id") != name]
            out = self._rebuild()
        return out

    def op_requirements(self) -> dict:
        """Every requirement and whether the part as built meets it now.

        Alongside the studies' own `require` rows from the last time
        `simulate` ran -- marked stale when the document has changed since,
        because a safety factor computed for a different part is not a fact
        about this one.
        """
        self._require_doc()
        self._ensure_body()
        return {"requirements": self._requirements_status(),
                "studies": self._studies_status()}
