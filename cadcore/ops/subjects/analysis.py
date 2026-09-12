"""Analysis and output: studies, interference, printability, drawings, files.

These are the operations that leave the model alone and produce an answer about
it -- which is why they are gathered apart from the ones that edit.
"""
from __future__ import annotations

from pathlib import Path

import json

from ...geometry.assembly.assembly import interference
from ... import names
from ...analysis.drawing import dxf, sheet
from ...errors import CadError
from ...geometry.io.exchange import assembly_tree, export_brep, export_iges
from ...geometry.io.mesh import write_gltf
from ...evaluation.api import export_step
from ...analysis.meshio import write_3mf, write_obj, write_stl
from ...analysis.printability import check as printability_check
from ...geometry.solids.sheet import flat_pattern as sheet_flat_pattern
from ...geometry.core.measure import require_solid
from ...geometry.core.measure import volume as kernel_volume
from ...geometry.io.tessellate import compact, refine, tessellate


def _leaves(tree: dict, scope: str = "") -> list:
    """Every part in an assembly tree, by its full scoped name."""
    out = []
    for name, node in tree.items():
        here = names.scoped(scope, name) if scope else name
        out += _leaves(node, here) if isinstance(node, dict) else [here]
    return out


def placed_parts(doc, evaluator, body) -> dict:
    """Each part as finally placed: the mate that moved it, or the part."""
    assemble = next((f for f in doc.features if f.type == "assemble"), None)
    solved = (body.notes.get("assembly") or {}) if body else {}
    if assemble and solved.get("poses"):
        # a solved assembly places its own parts, and where it put them is
        # not something a feature id holds: `body_of("bush")` is still the
        # bush where it was modelled, which is nowhere near the bore. The
        # poses come off the body, so this works on a cached rebuild too
        import numpy as np

        from ...geometry.assembly.placement import moved

        out = {}
        for name, (rotation, offset) in solved["poses"].items():
            try:
                part = evaluator.body_of(name)
            except CadError:
                continue
            out[name] = moved(part, (np.asarray(rotation), np.asarray(offset)))
        return out
    if assemble:
        wanted = assemble.args.get("bodies", [])
    else:
        # a mated part is placed by its mate; listing the part as well
        # would check a phantom copy sitting where it was modelled
        mates = [f for f in doc.features if f.type == "mate"]
        was_moved = {f.args.get("move") for f in mates}
        wanted = [f.id for f in mates] + [f.id for f in doc.features
                                          if f.type == "part" and f.id not in was_moved]
    out = {}
    for fid in wanted:
        try:
            out[fid] = evaluator.body_of(fid)
        except CadError:
            continue                     # a part nothing built: not placed
    return out


def part_behind(doc, fid: str) -> tuple:
    """Which `part` feature a placed body came from, through its mates and
    patterns, and how many copies of it the chain made."""
    seen, copies = set(), 1
    while fid and fid not in seen:
        seen.add(fid)
        feature = next((f for f in doc.features if f.id == fid), None)
        if feature is None:
            return None, copies
        if feature.type in ("part", "fastener"):
            return fid, copies
        if feature.type == "mate":
            fid = feature.args.get("move")
        elif feature.type == "translate":
            fid = feature.args.get("body")
        elif feature.type == "pattern":
            copies *= int(doc.evaluate(feature.args.get("count") or 1))
            fid = feature.args.get("body")
        elif feature.type == "mirror" and not feature.args.get("merge", True):
            copies *= 2
            fid = feature.args.get("body")
        else:
            return None, copies
    return None, copies


def material_of(spec: dict, doc) -> str | None:
    """What a part is made of: what it says, what its document says, or what
    it was analysed as -- in that order, because each is a weaker claim."""
    if spec.get("material"):
        return spec["material"]
    path = spec.get("document")
    if not path:
        return doc.meta.get("material") if isinstance(doc.meta, dict) else None
    try:
        from ...model.document import Document

        part = Document.load(doc.resolve(path))
    except Exception:                      # noqa: BLE001 -- not our problem here
        return None
    if isinstance(part.meta, dict) and part.meta.get("material"):
        return part.meta["material"]
    for study in getattr(part, "studies", []) or []:
        if study.get("material"):
            return study["material"]
    return None


def bill_of_materials(doc, evaluator, body, cache: dict | None = None) -> dict:
    """The bill for one document, nesting into any part that is an assembly."""
    lines: dict = {}
    for fid, placed in placed_parts(doc, evaluator, body).items():
        source, copies = part_behind(doc, fid)
        spec = doc.feature(source).args if source else {}
        bought = placed.notes.get("fastener") or {}
        standard = bought.get("name")
        key = (standard or spec.get("document", source or fid),
               json.dumps(spec.get("parameters") or {}, sort_keys=True))
        line = lines.setdefault(key, {
            "part": source or fid, "document": spec.get("document"),
            "parameters": spec.get("parameters") or {},
            "material": bought.get("material") or material_of(spec, doc),
            "quantity": 0, "volume_mm3": round(kernel_volume(placed) / copies, 3),
            "placed_as": [], **({"standard": standard} if standard else {})})
        line["quantity"] += copies
        line["placed_as"].append(fid)
        if spec.get("document") and "parts" not in line:
            inside = _sub_assembly(doc, spec, cache)
            if inside is not None:
                line["kind"] = "assembly"
                line["parts"] = inside["parts"]

    out = {"parts": sorted(lines.values(), key=lambda line: line["part"])}
    for line in out["parts"]:
        line["mass_g"] = _assembly_mass(line) if line.get("parts") else _mass_of(line)
    masses = [line["mass_g"] * line["quantity"] for line in out["parts"]
              if line["mass_g"] is not None]
    out["total_mass_g"] = round(sum(masses), 3) if masses else None
    out["flat"] = _flat(out["parts"])
    return out


def _sub_assembly(doc, spec: dict, cache: dict | None) -> dict | None:
    """The bill of a part's own document when that document is an assembly."""
    from ...evaluation.graph import Evaluator
    from ...model.document import Document

    try:
        sub = Document.load(doc.resolve(spec["document"]))
    except Exception:                                   # noqa: BLE001
        return None
    if not any(f.type in ("part", "mate", "assemble") for f in sub.features):
        return None
    sub.fence = doc.fence
    for name, value in (spec.get("parameters") or {}).items():
        if name in sub.parameters:
            sub.parameters[name] = doc.evaluate(value)
    try:
        ev = Evaluator(sub, cache if cache is not None else {})
        built = ev.build(spec.get("result") or sub.result)
    except CadError:
        return None
    return bill_of_materials(sub, ev, built, cache)


def _assembly_mass(line: dict) -> float | None:
    masses = [p["mass_g"] * p["quantity"] for p in line["parts"] if p["mass_g"] is not None]
    return round(sum(masses), 3) if masses else None


def _flat(parts: list, times: int = 1, under: str = "") -> list:
    """Every leaf part, quantities multiplied down through the sub-assemblies."""
    out = []
    for line in parts:
        where = f"{under}:{line['part']}" if under else line["part"]
        if line.get("parts"):
            out += _flat(line["parts"], times * line["quantity"], where)
        else:
            out.append({"part": where, "document": line["document"],
                        "parameters": line["parameters"], "material": line["material"],
                        "quantity": line["quantity"] * times, "mass_g": line["mass_g"]})
    return out


def _mass_of(line: dict) -> float | None:
    """A part's mass, when its material is known and the library has it."""
    if not line.get("material"):
        return None
    try:
        from ...simulation.materials import get
    except ImportError:                       # the kernel runs without the solvers
        return None
    try:
        material = get(line["material"])
    except Exception:                          # noqa: BLE001 -- an unknown material
        return None
    return round(line["volume_mm3"] * material.rho * 1e6, 3)   # t/mm3 -> grams


class AnalysisOps:
    """Mixed into :class:`cadcore.ops.session.Session`."""

    def op_simulate(self, deflection: float | None = None, study: str | None = None,
                    resolution: float | None = None) -> dict:
        """Run the document's studies, and optionally bring back the field.

        With a deflection the reply also carries the tessellation and a von
        Mises value per vertex, sampled just inside the surface, plus a peak and
        mean per CAD face name. The picture then lands on exactly the faces the
        designer selects from, instead of on an FEM mesh they never see.
        """
        from ...simulation.study import run_studies

        doc = self._require_doc()
        outcomes = run_studies(doc, self.cache)
        self._ensure_body()
        payload = {"studies": [{
            "id": o.id, "type": o.kind, "result": o.result.as_dict(),
            "convergence": o.convergence.as_dict() if o.convergence else None,
            "requirements": [{"quantity": r.quantity, "op": r.op, "value": r.value,
                              "ok": ok, "got": got} for r, ok, got in o.requirements],
            "ok": o.ok} for o in outcomes]}
        # for `requirements`: the rows, and the document they were true of
        self._studies_seen = (self._document_stamp(), [
            {"id": "%s:%s" % (s["id"], r["quantity"]), "study": s["id"],
             "quantity": r["quantity"], "op": r["op"], "value": r["value"],
             "got": r["got"],
             # a row holds only on a converged study: the peak moves with element size
             "ok": r["ok"] and (s["convergence"] is None or bool(s["convergence"]["converged"])),
             "converged": None if s["convergence"] is None else bool(s["convergence"]["converged"])}
            for s in payload["studies"] for r in s["requirements"]])
        if deflection is None:
            return payload

        chosen = next((o for o in outcomes if o.field is not None
                       and (study is None or o.id == study)), None)
        if chosen is None:
            payload["field"] = None
            return payload
        mesh = tessellate(self.body, deflection, 0.4)
        if resolution is None:
            # enough vertices to carry a gradient across the largest flat face,
            # without turning the reply into megabytes of triangles
            pts = mesh["vertices"]
            extent = max(max(p[i] for p in pts) - min(p[i] for p in pts) for i in range(3))
            resolution = extent / 25.0
        if resolution > 0:
            mesh = refine(mesh, float(resolution))
        mesh = compact(mesh)
        sampled = chosen.field.sample(mesh["vertices"], mesh["normals"])
        # the field says what it is where it can; the kind is the fallback
        quantity = getattr(chosen.field, "quantity", None) or {
            "modal": "mode_shape", "thermal": "temperature_C",
            "buckling": "buckling_mode"}.get(chosen.kind, "von_mises_MPa")
        unit = quantity.rsplit("_", 1)[-1]                 # MPa, C; a mode shape has none
        per_face = chosen.field.per_face(sampled["values"], mesh["triangles"],
                                         mesh["triangle_face"], mesh["face_table"],
                                         unit if unit in ("MPa", "C") else "value")
        payload["mesh"] = mesh
        payload["field"] = {
            "study": chosen.id,
            "quantity": quantity,
            "vertex_values": sampled["values"],
            "unsampled_vertices": sampled["missed"],
            "per_face": per_face,
            # the peak is where a singularity lives; the percentile is what the
            # colours are scaled to, so one hot corner cannot flatten the rest
            "scale": getattr(chosen.result, "p95_von_mises", 0.0) or
                     max(sampled["values"], default=1.0),
            "peak": getattr(chosen.result, "max_von_mises", 0.0) or
                    max(sampled["values"], default=1.0),
        }
        return payload

    def op_interference(self, tolerance: float = 1e-6) -> dict:
        """Which placed parts share space, and how much.

        The parts checked are the ones the assembly puts together, each in its
        final placement; a part that has been mated is checked where the mate
        put it, not where it was modelled.
        """
        self._require_doc()                    # refuse before touching the geometry
        if self.evaluator is None:
            self._rebuild()
        bodies = self._placed_parts()
        if not bodies:
            raise CadError("not_an_assembly", "this document has no parts to check",
                           {"hint": "add part and assemble features"})
        found = interference(bodies, float(tolerance))
        return {"parts": sorted(bodies), "interferences": found, "clear": not found}

    def op_check(self, printing: bool = True, tolerance: float = 1.0,
                 studies: bool = False) -> dict:
        """Everything that can be wrong with this document, in one answer.

        The one to run before cutting metal: it builds, asks whether the
        numbers are still inside what the document declared, whether every
        reference still points at something, whether the parts of an assembly
        share space, and whether a printer could make it.

        Each answer says what it is and whether it passed, so the reply reads
        as a report rather than as a verdict with no reasons.

        Two kinds, because a check that cries wolf is a check nobody reads.
        A **must** is a fault in the model -- it will not build, it has left
        its own envelope, a reference points at nothing, two parts share
        space. **Advice** is a fact about making it: a bracket has an
        overhang, and that is a support, not a defect. `ok` answers for the
        musts alone; the advice is beside it.
        """
        doc = self._require_doc()
        checks = []

        def note(name, ok, said, severity="must", **detail):
            checks.append({"check": name, "ok": bool(ok), "said": said,
                           "severity": severity,
                           **({"detail": detail} if detail else {})})

        try:
            built = self._rebuild()
            note("builds", True, "%d faces, %s mm3"
                 % (built["faces"], round(built["volume_mm3"], 1)))
        except CadError as exc:
            note("builds", False, "%s: %s" % (exc.kind, exc.message))
            return {"ok": False, "checks": checks, "failed": ["builds"],
                    "warned": []}

        note("a solid", built["kind"] == "solid" and not built["open_boundaries"],
             built["kind"] if built["open_boundaries"] == 0
             else "%s with %d open boundaries"
                  % (built["kind"], built["open_boundaries"]))

        inside, why = doc.in_envelope()
        note("inside its own envelope", inside,
             why or "every parameter in range, every assert holds")

        # `broken` only. A name in `dropped` is one the model no longer has,
        # which is what is supposed to happen to the end caps of a cylinder
        # used as a cutting tool -- `bush:hole/+z` in the shipped assembly is
        # exactly that. Only a *reference* to a name that is gone is a fault
        names = self.op_broken_references()
        loose = list(names.get("broken") or [])
        gone = list(names.get("dropped") or [])
        note("every reference resolves", not loose,
             "all of them" if not loose
             else "%d point at nothing: %s" % (len(loose), ", ".join(map(str, loose[:4]))),
             dropped=gone)

        parts = self._placed_parts()
        if len(parts) > 1:
            clash = interference(parts, float(tolerance))
            worst = max(clash, key=lambda c: c["volume_mm3"]) if clash else None
            note("the parts do not collide", not clash,
                 "%d parts, clear" % len(parts) if not clash
                 else "%d clash(es), worst %s and %s at %s mm3"
                      % (len(clash), worst["parts"][0], worst["parts"][1],
                         round(worst["volume_mm3"], 1)),
                 clashes=clash)

        if printing:
            how = self.op_printability()
            # each of these comes back as the list of places, not a count
            trouble = ", ".join(
                "%d %s" % (len(how[key]), word) for key, word in
                (("overhangs", "overhangs"), ("thin_walls", "thin walls"),
                 ("small_holes", "holes too small")) if how.get(key))
            note("a printer could make it", how["ok"],
                 "nothing in the way" if how["ok"] else trouble,
                 severity="advice",
                 **{k: v for k, v in how.items() if k != "ok"})

        if studies:
            outcomes = self.op_simulate()
            ran = outcomes.get("studies") or []
            failed = [s for s in ran if not s.get("ok", True)]
            note("the studies pass", not failed and bool(ran),
                 "no studies declared" if not ran
                 else "%d of %d pass" % (len(ran) - len(failed), len(ran)),
                 studies=ran)

        failed = [c["check"] for c in checks
                  if not c["ok"] and c["severity"] == "must"]
        warned = [c["check"] for c in checks
                  if not c["ok"] and c["severity"] != "must"]
        return {"ok": not failed, "checks": checks, "failed": failed,
                "warned": warned}

    def op_optimize(self, params: list | None = None, trials: int = 30,
                    step: float = 0.15, seed: int = 0,
                    objective: str = "volume", apply: bool = True) -> dict:
        """Move the parameters the document says may move, and keep the best.

        `objective` is `volume` -- take material out while every assert still
        holds -- or `mass`, which weighs the answer and requires the studies to
        pass, so it asks the real question: lighter *and* still strong enough.

        With `apply` the best design is written into the document as an edit
        like any other, which puts it on the undo stack: a search that made the
        part worse is one Ctrl+Z away.
        """
        from ...analysis.optimise import search

        doc = self._require_doc()
        found = search(doc, self.cache, params=params, trials=trials, step=step,
                       seed=seed, objective=objective)
        if apply:
            with self._edit() as document:
                document.parameters.update(found["parameters"])
                built = self._rebuild()
            found["built"] = {k: built[k] for k in ("faces", "volume_mm3")
                              if k in built}
        return found

    def _placed_parts(self) -> dict:
        """Each part as finally placed: the mate that moved it, or the part."""
        return placed_parts(self.doc, self.evaluator, self.body)

    def op_flat_dxf(self, path: str) -> dict:
        """Write the blank to a DXF the cutter can read."""
        from ...geometry.solids.sheet import flat_dxf

        path = self._path(path, writing=True)
        flat = self.op_flat_pattern()
        Path(path).write_text(flat_dxf(flat), encoding="utf-8")
        return {"path": path, "bytes": Path(path).stat().st_size,
                "extent_mm": flat.get("extent_mm"),
                "blank_area_mm2": flat.get("blank_area_mm2"),
                "bends": len(flat.get("bend_lines", []))}

    def op_bill_of_materials(self) -> dict:
        """What this assembly is made of: each part, how many, and how heavy.

        The quantity is counted by *what the part is* -- the document it came
        from, with the parameters it was driven with -- so two bushes bored to
        different sizes are two lines, and four of the same bush are one line
        with a four in it. That is the distinction a purchase order cares about
        and a face count cannot make. A pattern of a part counts its copies.
        A part that is itself an assembly is one line with its own ``parts``
        under it, so the bill nests as the assembly does; ``flat`` lists
        every leaf part with the quantities multiplied through.
        """
        doc = self._require_doc()
        if self.evaluator is None:
            self._rebuild()
        return bill_of_materials(doc, self.evaluator, self.body, self.cache)

    def op_flat_pattern(self) -> dict:
        """The blank this sheet metal part is cut from, and the bends in it.

        Read off the bends the part recorded when it was built, because the
        developed length of a bend depends on how the material stretched and
        the solid cannot be asked about that afterwards.
        """
        self._ensure_body()
        return sheet_flat_pattern(self.body)

    def op_draft_check(self, direction=(0.0, 0.0, 1.0), min_angle: float = 1.0,
                       deflection: float = 0.3) -> dict:
        """Will it come out of a mould pulled along ``direction``: draft per face and undercuts.

        A face with less than ``min_angle`` degrees of draft is listed as
        needing more; a face a straight pull would drag through the part is
        an undercut, with the area caught.
        """
        from ...analysis.moulding import check as moulding_check

        self._ensure_body()
        require_solid(self.body, "a draft check")
        return moulding_check(self.body, direction, min_angle, deflection)

    def op_mass_properties(self, material: str | None = None) -> dict:
        """Volume, centre of mass and inertia; with a material, the mass in grams.

        The material is the one given, else the document's, else A6061;
        the density comes from the material library.
        """
        self._ensure_body()
        from ...geometry.core.measure import mass_properties
        try:
            from ...simulation.materials import get as material_of
        except ImportError:                                   # the kernel runs without the solvers
            material_of = None
        meta = self.doc.meta if isinstance(self.doc.meta, dict) else {}
        name = material or meta.get("material") or "A6061"
        density = material_of(name).rho if material_of else None
        out = mass_properties(self.body, density)
        out["material"] = name if density else None
        return out

    def op_printability(self, up=(0.0, 0.0, 1.0), overhang_deg: float = 45.0,
                        min_wall: float = 1.2, min_hole: float = 2.0,
                        deflection: float = 0.3) -> dict:
        """Will it print -- answered on the CAD faces, so the fix has a name."""
        self._ensure_body()
        require_solid(self.body, "a printability check")
        return printability_check(self.body, up, overhang_deg, min_wall, min_hole,
                                  deflection)

    def op_drawing(self, path: str | None = None, spec: dict | None = None) -> dict:
        """Project a drawing sheet from the model, dimensioned by face name."""
        doc = self._require_doc()
        self._ensure_body()
        spec = spec or doc.drawing
        if not spec:
            raise CadError("no_drawing", "this document has no drawing declared",
                           {"hint": "add a \"drawing\" section, or pass one"})
        wanted = Path(path).suffix.lower() if path else ".svg"
        body, parts = self.body, None
        if assembly_tree(body):
            # an assembly's sheet: balloons and a parts list, and drawn apart
            # when the sheet says `exploded` (a factor of each part's size)
            parts = self.op_bill_of_materials()["flat"]
            if spec.get("exploded"):
                body = self._exploded_body(float(spec["exploded"]))
        text = dxf(body, spec) if wanted == ".dxf" else sheet(body, spec, doc.meta, parts)
        out = {"views": spec.get("views", []), "bytes": len(text.encode()),
               "format": "dxf" if wanted == ".dxf" else "svg"}
        if parts:
            out["parts"] = [line["part"] for line in parts]
        if path:
            path = self._path(path, writing=True)
            Path(path).write_text(text, encoding="utf-8")
            out["path"] = path
        else:
            out["svg"] = text
        return out

    def _exploded_body(self, factor: float):
        """The placed parts moved apart by the exploded view's offsets, as one body."""
        import numpy as np

        from ...geometry.assembly.placement import moved
        from ...geometry.kernel import compound

        offsets = self.op_explode(factor)["offsets"]
        parts = self._placed_parts()
        return compound("exploded", [moved(body, (np.eye(3), np.asarray(offsets[name])))
                                     for name, body in parts.items()])

    def op_export_mesh(self, path: str, deflection: float = 0.05,
                       angular: float = 0.3, name: str | None = None) -> dict:
        """STL, 3MF, OBJ, glTF or GLB, chosen by the file's own extension.

        The mesh is the same tessellation the viewport shows, so what the slicer
        opens is what was on screen.
        """
        self._ensure_body()
        path = self._path(path, writing=True)
        suffix = Path(path).suffix.lower()
        name = name or (self.doc.meta or {}).get("name", "part")
        if suffix == ".stl":
            return write_stl(self.body, path, deflection, angular)
        if suffix == ".3mf":
            return write_3mf(self.body, path, deflection, angular, name)
        if suffix == ".obj":
            return write_obj(self.body, path, deflection, angular)
        if suffix in (".gltf", ".glb"):
            return write_gltf(self.body, path, deflection, angular, name)
        raise CadError("unknown_format", f"no mesh writer for {suffix!r}",
                       {"available": [".stl", ".3mf", ".obj", ".gltf", ".glb"]})

    def op_export_step(self, path: str) -> dict:
        """STEP, or IGES or BREP if the name says so.

        An assembly is written as a STEP assembly: one product per part,
        named as the document names it, sub-assemblies nested, so another
        CAD opens it as parts and not as one lump. Each part's colour and
        material go with it; the document's own ``meta`` colour and material
        dress a single part.
        """
        self._ensure_body()
        path = self._path(path, writing=True)
        suffix = Path(path).suffix.lower()
        if suffix in (".igs", ".iges"):
            export_iges(self.body, path)
        elif suffix == ".brep":
            export_brep(self.body, path)
        else:
            meta = self.doc.meta if isinstance(self.doc.meta, dict) else {}
            own = {k: meta[k] for k in ("colour", "material") if meta.get(k)}
            export_step(self.body, path, name=meta.get("name") or Path(path).stem,
                        looks={"": own} if own else None)
        out = {"path": path, "bytes": Path(path).stat().st_size}
        tree = assembly_tree(self.body)
        if tree:
            out["parts"] = _leaves(tree)
        return out
