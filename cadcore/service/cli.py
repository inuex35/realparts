"""Command line: build, export, inspect, and the reference-survival fuzz test."""
from __future__ import annotations

import argparse
import json
import random
import sys
from contextlib import contextmanager
from pathlib import Path

from ..evaluation.api import build, describe
from ..model.document import Document
from .. import names, progress
from ..errors import CadError
from ..analysis.optimise import place_inside


def _load(path: str) -> Document:
    return Document.load(path)


def cmd_build(args) -> int:
    doc = _load(args.document)
    body, ev = build(doc)
    info = describe(body)
    measure = (f"volume {info['volume_mm3']:.1f} mm^3" if info["kind"] == "solid"
               else f"{info['kind']}, area {info['area_mm2']:.1f} mm^2, "
                    f"{info['open_boundaries']} open boundar"
                    f"{'y' if info['open_boundaries'] == 1 else 'ies'}")
    print(f"built {args.document}: {info['faces']} faces, {info['edges']} edges, "
          f"{measure} "
          f"({ev.stats['evaluated']} nodes evaluated, {ev.stats['reused']} reused)")
    if args.names:
        for n in info["face_names"]:
            print("  face", n)
        for n in info["edge_names"]:
            print("  edge", n)
    return 0


MESH_FORMATS = (".stl", ".3mf", ".obj", ".gltf", ".glb")


def cmd_export(args) -> int:
    """Write the format the file extension asks for; an unknown extension is
    refused rather than guessed at."""
    from .server import Session

    suffix = Path(args.out).suffix.lower()
    session = Session()
    session.op_open(args.document)
    session.op_build()
    if suffix in MESH_FORMATS:
        out = session.op_export_mesh(args.out, args.deflection)
        print(f"wrote {out['path']} ({out['bytes']} bytes, "
              f"{out['triangles']} triangles)")
        return 0
    if suffix not in (".step", ".stp", ".igs", ".iges", ".brep"):
        raise CadError("unknown_format", f"no writer for {suffix!r}",
                       {"available": [".step", ".stp", ".igs", ".iges", ".brep",
                                      *sorted(MESH_FORMATS)]})
    out = session.op_export_step(args.out)
    print(f"wrote {out['path']} ({out['bytes']} bytes)")
    return 0


NAMING_KINDS = {"unresolved_reference", "empty_selection", "lost_face_names",
                "face_count_changed", "unknown_feature"}


def cmd_mesh(args) -> int:
    from .server import Session

    session = Session()
    session.op_open(args.document)
    session.op_build()
    out = session.op_export_mesh(args.out, args.deflection)
    print("mesh: %d triangles -> %s (%d bytes)" % (out["triangles"], out["path"],
                                                   out["bytes"]))
    return 0


AXES = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1),
        "-x": (-1, 0, 0), "-y": (0, -1, 0), "-z": (0, 0, -1)}


def cmd_bom(args) -> int:
    """The assembly's parts, counted by what each part is."""
    from .server import Session

    session = Session()
    session.op_open(args.document)
    session.op_build()
    out = session.op_bill_of_materials()
    print("bill of materials: %d part(s)" % sum(p["quantity"] for p in out["flat"]))
    print("  qty  part            material   volume mm^3      mass g")

    def show(lines, depth=0):
        for line in lines:
            print("  %3d  %-15s %-10s %11.1f  %10s"
                  % (line["quantity"], "  " * depth + (line.get("standard") or line["part"]),
                     line["material"] or "-", line["volume_mm3"],
                     "-" if line["mass_g"] is None else "%.2f" % line["mass_g"]))
            show(line.get("parts") or [], depth + 1)     # a sub-assembly's own parts

    show(out["parts"])
    if out["total_mass_g"]:
        print("  total %.2f g" % out["total_mass_g"])
    return 0


def cmd_unfold(args) -> int:
    """The bend table and the developed length of a sheet metal part."""
    from .server import Session

    session = Session()
    session.op_open(args.document)
    session.op_build()
    out = session.op_flat_pattern()
    # added_mm is what the bends add to the blank beyond the base sketch
    print("flat pattern: %.2f mm thick, %d bend(s) adding %.2f mm to the blank"
          % (out["thickness_mm"], len(out["bends"]), out["added_mm"]))
    print("  bend  edge                              angle   radius  allowance")
    for bend in out["bends"]:
        print("  %-5s %-32s %5.1f  %6.2f  %8.3f"
              % (bend["feature"], bend["edge"], bend["angle_deg"],
                 bend["radius_mm"], bend["allowance_mm"]))
    if out.get("extent_mm"):
        print("  blank %.1f x %.1f mm, %.0f mm2"
              % (*out["extent_mm"], out["blank_area_mm2"]))
    if args.out:
        from ..geometry.solids.sheet import flat_dxf
        Path(args.out).write_text(flat_dxf(out), encoding="utf-8")
        print("wrote %s (%d bytes)" % (args.out, Path(args.out).stat().st_size))
    elif not out.get("blank"):
        print("  no developed outline: this part was not laid out from a sheet")
    return 0


def cmd_repair(args) -> int:
    """Report references that no longer resolve and their candidates, or
    reattach one. Works on a document whose build fails: the chain is built
    as far as it stands and the rest is checked against that."""
    from .server import Session

    session = Session()
    session.op_open(args.document)
    try:
        session.op_build()
    except CadError:
        pass                         # a failing build is the expected input
    if args.to:
        out = session.op_reattach(old=args.reference, new=args.to)
        print(out["status"])
        for hit in out["reattached"]:
            print("  %s.%s" % (hit["feature"], ".".join(map(str, hit["path"]))))
        if args.save:
            session.doc.save(args.document)
            print("saved %s" % args.document)
        else:
            print("  (not saved -- pass --save to write it back)")
        return 0

    report = session.op_broken_references()
    if not report["broken"]:
        print("every reference still points at something"
              + (" (built up to %s)" % report["built_upto"]
                 if report["built_upto"] else ""))
        return 0
    print("built as far as %s" % report["built_upto"])
    for item in report["broken"]:
        where = "%s.%s" % (item["feature"], ".".join(map(str, item["path"])))
        print("  %-28s %-22s %s" % (where, item["name"], item["why"]))
        if item["candidates"]:
            print("      could be: " + ", ".join(item["candidates"][:6]))
    return 0


def cmd_print_check(args) -> int:
    """Report overhangs, thin walls and small holes by face name."""
    from .server import Session

    session = Session()
    session.op_open(args.document)
    session.op_build()
    out = session.op_printability(AXES[args.up], args.overhang, args.wall, args.hole)
    print("printability: build %s up, %.0f deg overhang limit" % (args.up, args.overhang))
    print("  on the bed  %.0f mm2" % out["on_the_bed_mm2"])
    print("  unsupported %.0f of %.0f mm2 (%.1f%%)"
          % (out["unsupported_mm2"], out["surface_mm2"],
             out["unsupported_fraction"] * 100))
    for entry in out["overhangs"][:6]:
        print("  overhang  %-22s %8.1f mm2 at %s deg"
              % (entry["face"], entry["unsupported_mm2"], entry["shallowest_deg"]))
    for entry in out["thin_walls"][:6]:
        print("  thin wall %-22s %8.2f mm" % (entry["face"], entry["thinnest_mm"]))
    for entry in out["small_holes"][:6]:
        print("  small hole %-21s %8.2f mm" % (entry["face"], entry["diameter_mm"]))
    if out["ok"]:
        print("  nothing to fix at these limits")
    if args.report:
        Path(args.report).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        print("  report ->", args.report)
    return 0


def cmd_soak(args) -> int:
    """Random editing, with the invariants checked after every step."""
    from .soak import run

    out = run(args.document, args.n, args.seed)
    print(f"soak: {out.attempted} operations, {out.succeeded} applied, "
          f"{out.attempted - out.succeeded} refused")
    for kind, count in sorted(out.refused.items(), key=lambda kv: -kv[1]):
        print(f"  refused {count:4d}  {kind}")
    if out.violations:
        print(f"  VIOLATIONS: {len(out.violations)}")
        for v in out.violations[:5]:
            print("   -", json.dumps(v, default=str)[:220])
    else:
        print("  no invariant violated")
    if args.report:
        Path(args.report).write_text(json.dumps(
            {"document": args.document, "seed": args.seed, "steps": args.n,
             "refused": out.refused, "violations": out.violations, "log": out.log},
            indent=2, default=str) + "\n", encoding="utf-8")
        print(f"  report -> {args.report}")
    if not out.succeeded:
        print("soak: no operation was applied, so no invariant was exercised")
        return 1
    return 1 if out.violations else 0


def cmd_drawing(args) -> int:
    from .server import Session

    session = Session()
    session.op_open(args.document)
    session.op_build()
    out = session.op_drawing(args.out)
    print(f"drawing: {', '.join(out['views'])} -> {out['path']} ({out['bytes']} bytes)")
    return 0


def cmd_interference(args) -> int:
    """Report the parts of an assembly that share space."""
    from .server import Session

    session = Session()
    session.op_open(args.document)
    session.op_build()
    out = session.op_interference(args.tolerance)
    print(f"parts: {', '.join(out['parts'])}")
    if out["clear"]:
        print("no interference")
        return 0
    for clash in out["interferences"]:
        print("  %s and %s share %.3f mm3" % (clash["parts"][0], clash["parts"][1],
                                              clash["volume_mm3"]))
    return 1


def cmd_freedom(args) -> int:
    """Report an assembly's remaining degrees of freedom per part, and which
    mates are redundant or in conflict."""
    from .server import Session

    session = Session()
    session.op_open(args.document)
    out = session.op_build()
    report = out.get("freedom")
    if not report:
        print("nothing solved here: no assemble feature with mates")
        return 1
    print("%d equations, %d of them independent, %d degree%s of freedom left"
          % (report["equations"], report["constrained"], report["dof"],
             "" if report["dof"] == 1 else "s"))
    print("  grounded on %s" % report["ground"])
    for part, free in sorted(report["freedom"].items()):
        if not free["dof"]:
            print("  %-22s held" % part)
            continue
        how = ["turns about %s through %s" % (_vector(t["direction"]),
                                              _vector(t["through"]))
               for t in free["turns_about"]]
        how += ["slides along %s" % _vector(d) for d in free["slides_along"]]
        print("  %-22s %d free: %s" % (part, free["dof"], "; ".join(how)))
    for mate in report["redundant"]:
        print("  redundant  %s %s" % (mate["kind"], " + ".join(mate["faces"])))
    for mate in report["conflicts"]:
        print("  CONFLICT   %s %s (off by %g)"
              % (mate["kind"], " + ".join(mate["faces"]), mate["error"]))
    return 0


def _vector(v) -> str:
    return "(%g, %g, %g)" % tuple(v)


def cmd_drive(args) -> int:
    """Move a part along a freedom its mates leave, and print where the parts end up."""
    from .server import Session

    session = Session()
    session.op_open(args.document)
    session.op_build()
    out = session.op_drive(args.part, turn=args.turn, slide=args.slide,
                           frames=args.frames, collisions=args.collisions)
    for drive in out["drives"]:
        how = "turns %g deg about" % drive["turn"] if "turn" in drive \
            else "slides %g mm along" % drive["slide"]
        print("%s %s %s" % (drive["part"], how, _vector(drive["axis"])))
    for k, frame in enumerate(out["frames"], start=1):
        print("frame %d" % k)
        for part, (rotation, offset) in sorted(frame.items()):
            print("  %-22s at %s" % (part, _vector(offset)))
        if args.collisions and out["collisions"][k - 1]:
            for clash in out["collisions"][k - 1]:
                print("  CLASH %s and %s share %.3f mm3"
                      % (clash["parts"][0], clash["parts"][1], clash["volume_mm3"]))
    return 1 if args.collisions and any(out["collisions"]) else 0


def cmd_explode(args) -> int:
    """Print how far each part moves to show the assembly apart."""
    from .server import Session

    session = Session()
    session.op_open(args.document)
    session.op_build()
    out = session.op_explode(args.factor)
    for part, offset in sorted(out["offsets"].items()):
        print("  %-22s by %s" % (part, _vector(offset)))
    return 0


def cmd_fuzz(args) -> int:
    """Perturb the parameters and check every reference still resolves.

    Samples are drawn inside the document's declared envelope
    (``parameters_bounds`` / ``asserts``); outside it a failure means nothing.
    Failures are split into naming failures (bugs) and geometry failures (the
    shape is not buildable).
    """
    doc = _load(args.document)
    numeric = {k: v for k, v in doc.parameters.items() if isinstance(v, (int, float))}
    if not numeric:
        print("no numeric parameters to perturb")
        return 1
    rng = random.Random(args.seed)
    base_body, _ = build(doc)
    base = describe(base_body)
    base_edges = sorted(base_body.edge_table())
    naming_failures, geometry_failures, skipped, checked = [], [], 0, 0

    shrunk = 0
    for i in range(args.n):
        trial = Document.load(args.document)
        draw = {}
        for k, v in numeric.items():
            if k not in trial.bounds:
                continue          # not declared as varying: a count, a mode, a choice
            lo, hi = trial.bounds[k]
            draw[k] = rng.uniform(lo, hi)
        ok, blend = place_inside(trial, numeric, draw)
        if not ok:
            skipped += 1
            continue
        if blend < 1.0:
            shrunk += 1
        try:
            body, _ = build(trial)
            info = describe(body)
            checked += 1
            missing = [n for n in base["face_names"] if names.base(n) not in
                       {names.base(m) for m in info["face_names"]}]
            if missing:
                naming_failures.append({"trial": i, "kind": "lost_face_names",
                                        "missing": missing[:6], "parameters": trial.parameters})
            # Edges are checked separately: an edge is named for the two faces
            # that meet along it, so a surviving face does not imply a
            # surviving edge, and a fillet is an edge reference.
            gone = [n for n in base_edges if names.base(n) not in
                    {names.base(m) for m in body.edge_table()}]
            if gone:
                naming_failures.append({"trial": i, "kind": "lost_edge_names",
                                        "missing": gone[:6],
                                        "parameters": trial.parameters})
        except CadError as exc:
            rec = {"trial": i, "kind": exc.kind, "message": exc.message,
                   "detail": exc.detail, "parameters": trial.parameters}
            (naming_failures if exc.kind in NAMING_KINDS else geometry_failures).append(rec)

    varied = [k for k in numeric if k in doc.bounds]
    if shrunk:
        print(f"  {shrunk} of {checked} samples were pulled back toward the base "
              "design to land inside the envelope")
    held = [k for k in numeric if k not in doc.bounds]
    print(f"fuzz: {args.n} samples -> {checked} rebuilt, {skipped} outside the envelope")
    print(f"  varied: {', '.join(varied) or 'nothing'}")
    if held:
        # a parameter without bounds is held, and therefore untested
        print(f"  held  : {', '.join(held)} (no bounds declared)")
    print(f"  naming failures  : {len(naming_failures)}   <- architectural")
    print(f"  geometry failures: {len(geometry_failures)}   <- unbuildable shapes")
    for f in (naming_failures + geometry_failures)[: args.show]:
        print("  -", json.dumps(f, default=str)[:240])
    if args.report:
        Path(args.report).write_text(json.dumps(
            {"document": args.document, "samples": args.n, "rebuilt": checked,
             "skipped": skipped, "naming_failures": naming_failures,
             "geometry_failures": geometry_failures}, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"  report -> {args.report}")
    if not checked:
        # a run in which every sample was refused by the envelope is a
        # failure, not a pass
        print("fuzz: nothing was rebuilt, so nothing was tested")
        return 1
    return 1 if naming_failures else 0


def cmd_tessellate(args) -> int:
    from ..geometry.io.tessellate import compact, tessellate

    body, _ = build(_load(args.document))
    data = tessellate(body, args.deflection, args.angular)
    payload = compact(data) if args.compact else data
    Path(args.out).write_text(json.dumps(payload), encoding="utf-8")
    print(f"wrote {args.out}: {len(data['vertices'])} vertices, {len(data['triangles'])} triangles, "
          f"{len(data['faces'])} named faces, {len(data.get('edges', {}))} named edges")
    return 0


@contextmanager
def _saying_where_it_got_to():
    """Report a long command's stages on stderr, only when stderr is a
    terminal, so redirected output is unchanged. Uses the same
    `cadcore.progress` sink the MCP server installs."""
    if not sys.stderr.isatty():
        yield
        return

    def say(done, total, message):
        print("  [%s] %s" % ("%d/%d" % (done, total) if total else str(done),
                             message), file=sys.stderr, flush=True)
    with progress.reported_to(say):
        yield


def cmd_simulate(args) -> int:
    from ..simulation.study import run_studies

    doc = _load(args.document)
    with _saying_where_it_got_to():
        outcomes = run_studies(doc)
    if not outcomes:
        print("no studies declared in this document")
        return 1
    failed = 0
    for o in outcomes:
        print(f"[{o.id}] " + json.dumps(o.result.as_dict()))
        if o.convergence:
            cv = o.convergence.as_dict()
            mark = "converged" if cv["converged"] else "NOT CONVERGED"
            print(f"   mesh {mark} (tol {cv['tolerance']:.0%})"
                  + (f", extrapolated {cv['extrapolated_von_mises_MPa']} MPa"
                     if cv["extrapolated_von_mises_MPa"] else ""))
            for l in cv["levels"]:
                ch = "" if l["change"] is None else f"  change {l['change']:+.1%}"
                print(f"     h={l['mesh_size']:5.2f}  {l['elements']:7d} el  "
                      f"peak {l['max_von_mises_MPa']:8.3f}  p95 {l['p95_von_mises_MPa']:8.3f} MPa{ch}")
        for req, ok, got in o.requirements:
            mark = "ok  " if ok else "FAIL"
            print(f"   {mark} {req.quantity} {req.op} {req.value}   (got {got:.4g})")
        failed += 0 if o.ok else 1
    return 1 if failed else 0


def cmd_optimize(args) -> int:
    """Vary parameters, rebuild, weigh, keep the best. The search is
    `cadcore.analysis.optimise`, shared with `op_optimize`."""
    from ..analysis.optimise import search

    doc = _load(args.document)
    try:
        with _saying_where_it_got_to():
            found = search(doc, params=args.params.split(",") if args.params else None,
                           trials=args.n, step=args.step, seed=args.seed,
                           objective=args.objective)
    except CadError as exc:
        print("%s: %s" % (exc.kind, exc.message))
        return 1
    for line in found["history"]:
        said = " ".join("%s=%s" % (k, v) for k, v in line.items()
                        if k not in ("trial", "parameters"))
        print("  [%3d] %s   %s" % (line["trial"], said,
                                   " ".join("%s=%s" % (p, v) for p, v
                                            in line["parameters"].items())))
    print("was: ", json.dumps(found["was"], default=str))
    print("best:", json.dumps(found["best"], default=str))
    print("parameters:", json.dumps(found["parameters"]))
    if args.out:
        doc.parameters.update(found["parameters"])
        doc.save(args.out)
        print("wrote", args.out)
    return 0


def main(argv=None) -> int:
    # the program name is how this is invoked; there is no console script
    p = argparse.ArgumentParser("python -m cadcore",
                                description="headless parametric CAD core")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="rebuild a document")
    b.add_argument("document")
    b.add_argument("--names", action="store_true", help="list face and edge names")
    b.set_defaults(func=cmd_build)

    e = sub.add_parser("export", help="write STEP, IGES, STL or 3MF")
    e.add_argument("document")
    e.add_argument("--out", default="out.step")
    e.add_argument("--deflection", type=float, default=0.05,
                   help="mesh tolerance, for the formats that are meshes")
    e.set_defaults(func=cmd_export)

    f = sub.add_parser("fuzz", help="perturb parameters and check references survive")
    f.add_argument("document")
    f.add_argument("-n", type=int, default=100)
    f.add_argument("--seed", type=int, default=0)
    f.add_argument("--show", type=int, default=5)
    f.add_argument("--report")
    f.set_defaults(func=cmd_fuzz)

    m = sub.add_parser("mesh", help="write STL or 3MF for a slicer")
    m.add_argument("document")
    m.add_argument("--out", default="part.stl")
    m.add_argument("--deflection", type=float, default=0.05)
    m.set_defaults(func=cmd_mesh)

    pc = sub.add_parser("print-check", help="overhangs, thin walls and small holes")
    pc.add_argument("document")
    pc.add_argument("--overhang", type=float, default=45.0, help="degrees from the bed")
    pc.add_argument("--wall", type=float, default=1.2, help="minimum wall in mm")
    pc.add_argument("--hole", type=float, default=2.0, help="minimum hole diameter")
    pc.add_argument("--up", default="z", choices=["x", "y", "z", "-x", "-y", "-z"])
    pc.add_argument("--report")
    pc.set_defaults(func=cmd_print_check)

    uf = sub.add_parser("unfold", help="the flat pattern of a sheet metal part")
    uf.add_argument("document")
    uf.add_argument("--out", help="write the blank as DXF, for the cutter")
    uf.set_defaults(func=cmd_unfold)

    rp = sub.add_parser("repair", help="references that stopped pointing at anything")
    rp.add_argument("document")
    rp.add_argument("reference", nargs="?", help="the name to point somewhere else")
    rp.add_argument("--to", help="where to point it")
    rp.add_argument("--save", action="store_true",
                    help="write the repaired document back over itself")
    rp.set_defaults(func=cmd_repair)

    bom = sub.add_parser("bom", help="what an assembly is made of")
    bom.add_argument("document")
    bom.set_defaults(func=cmd_bom)

    sk = sub.add_parser("soak", help="fuzz the *editing* session: add, remove, undo, roll back")
    sk.add_argument("document")
    sk.add_argument("-n", type=int, default=200)
    sk.add_argument("--seed", type=int, default=0)
    sk.add_argument("--report")
    sk.set_defaults(func=cmd_soak)

    dr = sub.add_parser("drawing", help="project a dimensioned drawing sheet (SVG)")
    dr.add_argument("document")
    dr.add_argument("--out", default="drawing.svg")
    dr.set_defaults(func=cmd_drawing)

    fr = sub.add_parser("freedom",
                        help="what an assembly's mates hold, and what is still loose")
    fr.add_argument("document")
    fr.set_defaults(func=cmd_freedom)

    dv = sub.add_parser("drive", help="move a part along a freedom its mates leave")
    dv.add_argument("document")
    dv.add_argument("part")
    dv.add_argument("--turn", type=float, help="degrees")
    dv.add_argument("--slide", type=float, help="millimetres")
    dv.add_argument("--frames", type=int, default=1)
    dv.add_argument("--collisions", action="store_true", help="check each frame for clashes")
    dv.set_defaults(func=cmd_drive)

    ex = sub.add_parser("explode", help="how far each part moves for an exploded view")
    ex.add_argument("document")
    ex.add_argument("--factor", type=float, default=1.0)
    ex.set_defaults(func=cmd_explode)

    i = sub.add_parser("interference", help="check an assembly for parts sharing space")
    i.add_argument("document")
    i.add_argument("--tolerance", type=float, default=1e-6)
    i.set_defaults(func=cmd_interference)

    t = sub.add_parser("tessellate", help="triangles + the CAD face name on every triangle")
    t.add_argument("document")
    t.add_argument("--out", default="mesh.json")
    t.add_argument("--deflection", type=float, default=0.15)
    t.add_argument("--angular", type=float, default=0.4)
    t.add_argument("--compact", action="store_true", help="index the names instead of repeating them")
    t.set_defaults(func=cmd_tessellate)

    s_ = sub.add_parser("simulate", help="run the studies declared in the document")
    s_.add_argument("document")
    s_.set_defaults(func=cmd_simulate)

    o = sub.add_parser("optimize", help="vary parameters, rebuild, simulate, keep the lightest")
    o.add_argument("document")
    o.add_argument("-n", type=int, default=30)
    o.add_argument("--params", help="comma separated; default: everything with bounds")
    o.add_argument("--step", type=float, default=0.25, help="local step as a fraction of the range")
    o.add_argument("--seed", type=int, default=0)
    o.add_argument("--objective", default="mass", choices=("mass", "volume"),
                   help="mass needs the studies to pass; volume needs only the kernel")
    o.add_argument("--out", help="write the winning document here")
    o.set_defaults(func=cmd_optimize)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except CadError as exc:
        print(f"error [{exc.kind}] {exc.message}", file=sys.stderr)
        if exc.detail:
            print(json.dumps(exc.detail, indent=2, default=str)[:800], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
