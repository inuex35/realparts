"""What a body records about itself, and the one rule for combining two."""
from __future__ import annotations

from dataclasses import dataclass

#: where a body records what a join swallowed
LOST = "provenance_lost"


@dataclass(frozen=True)
class Note:
    """One kind of thing a body records, and what happens to it.

    ``merge``  how two bodies' copies of it combine:

        ``one``   a body has one of these. The first input that has it wins,
                  which is what a boolean's target should be.
        ``join``  a list, concatenated -- but *without* repeating an entry that
                  is already there, because the commonest way two inputs carry
                  the same entry is that one of them is a copy of the other.
        ``union`` a dict keyed by part: every key kept, the first input with a
                  key winning it.

    """

    merge: str
    why: str


#: Every kind of note anything in this kernel writes. A kind that is not here is
#: refused by the test beside this, because the way this went wrong was that
#: nobody had said what combining two of them meant.
NOTES: dict[str, Note] = {
    "sheet": Note("one", "the stock a part is cut from: one part, one stock"),
    "bends": Note("join", "each records a bend line, its angle and its radius"),
    "thread": Note("one", "the thread most recently cut, kept for readers that "
                          "predate `threads`"),
    "threads": Note("join", "one entry per thread: its face, designation and "
                            "length"),
    "fill": Note("one", "how closely a patch met its neighbours"),
    "assembly": Note("one", "the poses the mates solved for"),
    "fastener": Note("one", "what standard part this solid is, for the bill of materials"),
    "colours": Note("union", "the colour of each part, by scope, as #rrggbb; "
                             "read from and written to STEP"),
    "materials": Note("union", "the material name of each part, by scope"),
    "import": Note("one", "what a file import did: healing, triangles, closure"),
    LOST: Note("join",
               "what a join swallowed, so that a reader can refuse rather "
               "than answer from half a record"),
}


def rule(kind: str) -> Note:
    """The declared rule, or the careful one for a kind nobody has declared.

    Careful means "one": a note this module has never heard of is not one it
    can promise to combine correctly, and keeping the first copy is the answer
    that cannot invent an entry. The test beside this makes sure the case does
    not arise quietly.
    """
    return NOTES.get(kind, Note("one", "undeclared"))


def merged(bodies, joining: bool = False) -> dict:
    """The provenance of several bodies, combined by the declared rules.

    ``joining`` says that what comes out is one body rather than several kept
    side by side. It matters for exactly one thing: if a list-valued note has
    the same entry in two of the inputs, then one input is a copy of another,
    and the body being built has more of whatever that note describes than the
    note records. Mirroring a bracket onto itself is the case -- two bends and
    their two reflections, of which only two are written down.

    So the entry is kept once, and the fact that it happened is kept too.
    """
    out: dict = {}
    swallowed: list = []
    for body in bodies:
        for kind, value in getattr(body, "notes", {}).items():
            if kind == LOST:
                for entry in value:
                    if entry not in out.setdefault(LOST, []):
                        out[LOST].append(entry)
                continue
            how = rule(kind)
            if how.merge == "union" and isinstance(value, dict):
                for key, entry in value.items():
                    out.setdefault(kind, {}).setdefault(key, entry)
            elif how.merge == "join" and isinstance(value, list):
                already = out.setdefault(kind, [])
                for entry in value:
                    if entry in already:
                        if joining and kind not in swallowed:
                            swallowed.append(kind)
                    else:
                        already.append(entry)
            else:
                out.setdefault(kind, value)
    for kind in swallowed:
        entry = {"note": kind, "why": "joined with a copy of itself"}
        if entry not in out.setdefault(LOST, []):
            out[LOST].append(entry)
    return out


def lost(body, kind: str) -> bool:
    """Whether this body records less of `kind` than it has."""
    return any(entry.get("note") == kind
               for entry in getattr(body, "notes", {}).get(LOST, []))
