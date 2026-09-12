"""Run one spec through one arm of the gauntlet, and score what came out.

    python bench/run.py --arm cadcore|texttocad --spec l_bracket [--budget 3] [--turns 60]

Both arms get the same prompt, turns and dollars, and are scored by `check.py`
from the file they leave; results land under `bench/results/<arm>/<spec>/`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from check import score  # noqa: E402

TEXTTOCAD_REPO = os.environ.get("TEXTTOCAD_REPO", os.path.expanduser("~/text-to-cad"))
TEXTTOCAD_PYTHON = os.environ.get("TEXTTOCAD_PYTHON", "")   # a python with cadgen in it
CADCORE_PYTHON = os.environ.get("CADCORE_PYTHON") or sys.executable   # the interpreter running this

COMMON = ("\n\nWork in the current directory. When you are done, the finished part "
          "must exist as a file named part.step in that directory (a STEP file). "
          "Do not ask questions; make reasonable assumptions and state them at the end. "
          "Finish by reporting the part's volume in mm^3.")


def run(arm: str, spec_id: str, budget: float, turns: int, model: str | None) -> dict:
    spec = json.load(open(os.path.join(HERE, "specs", spec_id + ".json"), encoding="utf-8"))
    out_dir = os.path.join(HERE, "results", arm, spec_id)
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)
    work = os.path.join(out_dir, "work")
    os.makedirs(work)

    prompt = spec["prompt"] + COMMON
    cmd = ["claude", "-p", "--output-format", "json", "--no-session-persistence",
           "--max-turns", str(turns), "--max-budget-usd", str(budget),
           "--dangerously-skip-permissions"]
    if model:
        cmd += ["--model", model]
    if arm == "cadcore":
        mcp = {"mcpServers": {"cadcore": {
            "command": CADCORE_PYTHON, "args": ["-m", "cadcore.service.mcp"],
            "env": {"CAD_WORKSPACE": work, "PYTHONPATH": REPO}}}}
        cfg = os.path.join(out_dir, "mcp.json")
        json.dump(mcp, open(cfg, "w", encoding="utf-8"))
        cmd += ["--strict-mcp-config", "--mcp-config", cfg,
                "--allowedTools", "mcp__cadcore",
                "--disallowedTools", "Bash", "Edit", "Write", "Read", "Glob", "Grep",
                "Agent", "WebFetch", "WebSearch", "NotebookEdit"]
        prompt += (" Use the cadcore tools for everything: build the part as a cadcore "
                   "document, save it as part.json, and export_step it to part.step.")
    elif arm == "texttocad":
        cmd += ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--plugin-dir", TEXTTOCAD_REPO,
                "--allowedTools", "Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill"]
        prompt += (" Use the cad skill (text-to-cad). Python with cadgen installed is at %s; "
                   "use that interpreter for every command and script. Do not install anything."
                   % TEXTTOCAD_PYTHON)
    else:
        raise SystemExit("arm is cadcore or texttocad")
    # the prompt on stdin, not argv: after a variadic flag such as
    # --disallowedTools, a trailing positional is read as one more tool name,
    # and claude then reports that no prompt was given
    started = time.time()
    proc = subprocess.run(cmd, cwd=work, input=prompt, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=3600,
                          env=dict(os.environ, CAD_WORKSPACE=work))
    elapsed = time.time() - started
    open(os.path.join(out_dir, "claude.stdout.json"), "w", encoding="utf-8").write(proc.stdout)
    open(os.path.join(out_dir, "claude.stderr.txt"), "w", encoding="utf-8").write(proc.stderr)
    try:
        reply = json.loads(proc.stdout)
    except ValueError:
        reply = {}

    produced = os.path.join(work, "part.step")
    if not os.path.exists(produced):
        # a STEP left under any name still counts as a part; a missing one is a
        # zero, and the score says why
        steps = [f for f in os.listdir(work) if f.lower().endswith((".step", ".stp"))]
        produced = os.path.join(work, steps[0]) if steps else produced
    scored = score(produced, spec["checks"]) if os.path.exists(produced) else {
        "opened": False, "error": "no STEP file was produced",
        "checks": [{"check": c, "ok": False, "got": "no file"} for c in spec["checks"]],
        "passed": 0, "total": len(spec["checks"])}

    # a spec may name more files -- an earlier version the prompt asked to keep
    for extra in spec.get("also", []):
        path = os.path.join(work, extra["file"])
        more = score(path, extra["checks"]) if os.path.exists(path) else {
            "checks": [{"check": c, "ok": False, "got": "no %s" % extra["file"]}
                       for c in extra["checks"]], "passed": 0, "total": len(extra["checks"])}
        for c in more["checks"]:
            c["check"] = dict(c["check"], file=extra["file"])
        scored["checks"] += more["checks"]
        scored["passed"] += more["passed"]
        scored["total"] += more["total"]

    result = {"arm": arm, "spec": spec_id, "elapsed_s": round(elapsed, 1),
              "cost_usd": reply.get("total_cost_usd"), "turns": reply.get("num_turns"),
              "exit": proc.returncode, "is_error": reply.get("is_error"),
              "produced": os.path.basename(produced) if os.path.exists(produced) else None,
              "score": "%d/%d" % (scored["passed"], scored["total"]),
              "checks": scored["checks"], "open_error": scored.get("error"),
              "final_message": (reply.get("result") or "")[-600:]}
    json.dump(result, open(os.path.join(out_dir, "score.json"), "w", encoding="utf-8"),
              indent=1, default=str)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--budget", type=float, default=3.0)
    ap.add_argument("--turns", type=int, default=60)
    ap.add_argument("--model", default=None)
    a = ap.parse_args()
    r = run(a.arm, a.spec, a.budget, a.turns, a.model)
    print("%-10s %-16s %s  $%.2f  %ss  turns %s  %s" % (
        r["arm"], r["spec"], r["score"], r["cost_usd"] or 0, r["elapsed_s"], r["turns"],
        "" if r["produced"] else "(no part)"))
    for c in r["checks"]:
        print("   %s %-11s %s" % ("ok  " if c["ok"] else "FAIL", c["check"]["type"],
                                  str(c["got"])[:110]))


if __name__ == "__main__":
    main()
