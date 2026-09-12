"""Assemblies in motion and apart: driving a freedom, and the exploded view."""
from __future__ import annotations

from ...errors import CadError
from ...geometry.assembly import placement


class AssemblyOps:
    """Mixed into :class:`cadcore.ops.session.Session`."""

    def _assemble_feature(self):
        """The solved assembly this document ends in, or a refusal."""
        doc = self._require_doc()
        assemble = next((f for f in doc.features if f.type == "assemble"), None)
        if assemble is None or not assemble.args.get("mates"):
            raise CadError("not_an_assembly",
                           "this document has no assemble feature with mates",
                           {"hint": "mates solved together are what leave a part "
                                    "free to move; add an assemble with mates"})
        return assemble

    def _mates_of(self, assemble) -> tuple:
        """The parts, unplaced, and the mates as numbers, ready for the solver."""
        doc = self.doc
        self._ensure_body()
        bodies = {b: self.evaluator.body_of(b) for b in assemble.args["bodies"]}
        ev = doc.evaluate
        mates = [dict(m, offset=None if m.get("offset") is None else ev(m["offset"]),
                      angle=ev(m.get("angle") or 0.0), pitch=ev(m.get("pitch") or 0.0),
                      ratio=ev(m["ratio"]) if m.get("ratio") is not None else None)
                 for m in assemble.args["mates"]]
        return bodies, mates

    def op_drive(self, part: str, turn: float | None = None, slide: float | None = None,
                 about: list | None = None, along: list | None = None,
                 frames: int = 1, collisions: bool = False) -> dict:
        """Move a part along a freedom its mates leave, the other parts following.

        ``turn`` is degrees, ``slide`` millimetres, from the assembly's rest
        pose; ``about`` or ``along`` picks the axis when the part has more
        than one. The answer is the pose of every part at ``frames`` steps
        along the way -- a rotation matrix and an offset each, in world
        millimetres -- and, with ``collisions``, which parts touch at each
        step. Nothing in the document changes: to keep a position, write it
        into the assemble feature's ``drive`` list.
        """
        assemble = self._assemble_feature()
        bodies, mates = self._mates_of(assemble)
        spec = {"part": part}
        if turn is not None:
            spec["turn"] = float(turn)
        if slide is not None:
            spec["slide"] = float(slide)
        if about is not None:
            spec["about"] = list(about)
        if along is not None:
            spec["along"] = list(along)
        rest = placement.solve(bodies, mates, ground=assemble.args.get("ground"))
        report = placement.drive(bodies, mates, [spec], ground=assemble.args.get("ground"),
                                 rest=rest, frames=max(1, int(frames)))
        out = {"part": part, "drives": report["drives"], "ground": report["ground"],
               "frames": report["frames"], "scopes": _scopes(bodies),
               "rest": {name: [pose[0].round(9).tolist(), pose[1].round(6).tolist()]
                        for name, pose in rest["poses"].items()},
               "freedom": {k: v for k, v in report.items()
                           if k in ("dof", "constrained", "equations", "freedom",
                                    "redundant", "conflicts")}}
        if collisions:
            from ...geometry.assembly.assembly import interference
            import numpy as np

            out["collisions"] = []
            for frame in report["frames"]:
                placed = {name: placement.moved(bodies[name], (np.asarray(R), np.asarray(t)))
                          for name, (R, t) in frame.items()}
                out["collisions"].append(interference(placed))
        return out

    def op_add_mate(self, faces: list, kind: str = "fastened", offset: float | None = None,
                    flip: bool = True, angle: float = 0.0, pitch: float | None = None,
                    ratio: float | None = None, note: str | None = None) -> dict:
        """Hold two faces of two parts together, solved with the assembly's other mates.

        The mate goes into the document's ``assemble`` feature; if there is
        none, one is made over every part and mated body, grounded on the
        first. The first face's part is the one that moves. ``kind`` is any
        mate kind (fastened, concentric, hinge, gear ...); see feature_types
        for what each needs.
        """
        doc = self._require_doc()
        if len(faces) != 2:
            raise CadError("bad_arguments", "a mate needs exactly two faces", {"given": faces})
        mate = {"kind": kind, "faces": list(faces), "flip": bool(flip)}
        if offset is not None:
            mate["offset"] = offset
        if angle:
            mate["angle"] = angle
        if pitch is not None:
            mate["pitch"] = pitch
        if ratio is not None:
            mate["ratio"] = ratio
        if note:
            mate["note"] = note
        assemble = next((f for f in doc.features if f.type == "assemble"), None)
        if assemble is None:
            moved = {f.args.get("move") for f in doc.features if f.type == "mate"}
            bodies = [f.id for f in doc.features
                      if f.type in ("part", "mate", "fastener") and f.id not in moved]
            if len(bodies) < 2:
                raise CadError("not_an_assembly", "a mate needs two parts to hold together",
                               {"parts": bodies, "hint": "add part features first"})
            out = self.op_add_feature("assemble", {"bodies": bodies, "ground": bodies[0],
                                                   "mates": [mate]}, feature_id="asm")
            doc.result = "asm"
            return dict(out, mate=len(mate) and 0)
        mates = list(assemble.args.get("mates") or []) + [mate]
        out = self.op_edit_feature(assemble.id, {"mates": mates})
        return dict(out, mate=len(mates) - 1)

    def op_explode(self, factor: float = 1.0) -> dict:
        """How far to move each part so the assembly is seen apart.

        Each part moves along the axis its mates hold it by -- a bush comes
        out of its bore, a lid lifts off its box -- and by ``factor`` times
        its own size; a part with no such axis moves away from the middle.
        The answer is an offset per part in world millimetres; nothing in the
        document changes.
        """
        from ...geometry.assembly.explode import offsets

        self._ensure_body()
        parts = self._placed_parts()
        if len(parts) < 2:
            raise CadError("not_an_assembly", "one part cannot be exploded",
                           {"hint": "open an assembly with two or more parts"})
        doc = self.doc
        assemble = next((f for f in doc.features if f.type == "assemble"), None)
        mates = list((assemble.args.get("mates") or []) if assemble else [])
        for f in doc.features:
            if f.type == "mate":
                mates.append(dict(f.args, moved_as=f.id))
        ground = assemble.args.get("ground") if assemble else None
        return {"factor": float(factor), "scopes": _scopes(parts),
                "offsets": offsets(parts, mates, ground=ground, factor=float(factor))}


def _scopes(bodies: dict) -> dict:
    """The face-name scope each placed body's part carries, so a viewer that
    draws parts by scope can find the object a pose belongs to."""
    from ... import names

    out = {}
    for fid, body in bodies.items():
        scopes = {names.parse(n).scope for n in body.face_names()}
        scopes.discard("")
        out[fid] = sorted(scopes)[0] if len(scopes) == 1 else fid
    return out
