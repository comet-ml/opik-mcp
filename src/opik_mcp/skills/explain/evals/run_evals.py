#!/usr/bin/env python3
# mypy: ignore-errors
"""Test-automation harness for the `/explain` skill.

Flows (per the guide's "scripted testing" level):

  1. Manual (default):
        uv run --with pyyaml python run_evals.py prepare   # stage workdirs + emit real traces
        # ...run the /explain skill on each _work/<case>/ using the trace id in
        #    trace_id.txt; it should write result.json = {status, root_cause,
        #    evidence, next_step, reasoner} into the workdir...
        uv run --with pyyaml python run_evals.py grade     # score + metrics

  2. Triggering (selection_accuracy):
        uv run --with pyyaml python run_evals.py trigger-prepare
        # ...judge panel classifies each phrase from judge_input.md...
        uv run --with pyyaml python run_evals.py trigger-grade

`prepare` runs each functional fixture (an instrumented app with a known bug) to
emit a REAL trace, then substitutes its id into <TRACE_ID>. Requires Opik
configured (`~/.opik.config` or OPIK_API_KEY) and network. The grader is
deterministic over result.json — no network at grade time.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import grader
import metrics

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / "SKILL.md"
FIXTURES = HERE / "fixtures"
WORK = HERE / "_work"
TRIG = WORK / "triggering"

DECOY_SKILLS = [
    {
        "name": "instrument",
        "description": "Add Opik tracing to an existing app and "
        "verify a real trace lands. Installs the package, adds the minimum tracing, runs "
        "a safe path, confirms a trace. Use to add observability, not to debug one.",
    },
    {
        "name": "evaluate",
        "description": "Build an Opik evaluation and run it against "
        "your app, returning an experiment with scores. Use to measure quality, run "
        "experiments, or build test suites.",
    },
    {
        "name": "opik",
        "description": "Reference for how Opik works — tracing concepts, "
        "SDK/REST/integration options. Use to look up or explain Opik the product, not to "
        "debug a specific trace.",
    },
    {
        "name": "scaffold-app",
        "description": "Create a brand-new application from scratch "
        "(for example a new FastAPI or Express service) with project layout and boilerplate.",
    },
    {
        "name": "code-review",
        "description": "Review existing code and report findings "
        "without changing it — a general read-only audit pass, not tied to a trace.",
    },
]


def load_cases() -> dict:
    import yaml

    return yaml.safe_load((HERE / "cases.yaml").read_text())


def app_cases(cases: dict) -> list[tuple[str, dict]]:
    return [("functional", c) for c in cases.get("functional", [])] + [
        ("edge", c) for c in cases.get("edge", [])
    ]


def _emit_trace(wd: Path) -> str | None:
    """Run the fixture to produce a real trace; return its id (or None)."""
    try:
        out = subprocess.run(
            ["uv", "run", "--quiet", "python", "agent.py"],
            cwd=wd,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except Exception as e:
        print(f"  ! could not run {wd.name}: {e}")
        return None
    for line in (out.stdout + out.stderr).splitlines():
        m = re.search(r"TRACE_ID:\s*(\S+)", line)
        if m and m.group(1) != "<none>":
            return m.group(1)
    print(f"  ! no TRACE_ID from {wd.name} (Opik configured?)")
    return None


def prepare() -> None:
    cases = load_cases()
    WORK.mkdir(exist_ok=True)
    lines = [f"skill: {SKILL}", ""]
    for area, c in app_cases(cases):
        wd = WORK / c["id"]
        if wd.exists():
            shutil.rmtree(wd)
        shutil.copytree(FIXTURES / c["fixture"], wd)
        tid = _emit_trace(wd) if c.get("emit_trace") else None
        (wd / "trace_id.txt").write_text(tid or "")
        prompt = c["prompt"].replace("<TRACE_ID>", tid or "<no-trace-emitted>")
        lines.append(
            f"## {c['id']}  ({area})\n- workdir: {wd}\n- trace_id: {tid}\n- prompt: {prompt}\n"
        )
    (WORK / "PROMPTS.md").write_text("\n".join(lines))
    print(f"Prepared {len(app_cases(cases))} workdirs under {WORK}")
    print(f"Run /explain on each (see {WORK}/PROMPTS.md), have it write result.json, then `grade`.")


def _read_json(wd: Path, name: str) -> dict | None:
    f = wd / name
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            return None
    return None


def grade() -> int:
    cases = load_cases()
    results = []
    for area, c in app_cases(cases):
        wd = WORK / c["id"]
        if not wd.exists():
            print(f"  ! skip {c['id']}: no workdir (run `prepare` + the skill first)")
            continue
        result = _read_json(wd, "result.json")
        results.append(grader.grade_case(c, FIXTURES / c["fixture"], wd, result, area=area))
    m = metrics.compute(results)
    rep = metrics.report(results, m)
    (WORK / "report.md").write_text(rep)
    print(rep)
    return 0 if all(r.passed for r in results) else 1


# ---------- triggering (selection_accuracy) ----------


def _instrument_description() -> str:
    fm = SKILL.read_text().split("---")[1]
    m = re.search(r"^description:\s*(.+)$", fm, re.M)
    return m.group(1).strip() if m else ""


def trigger_prepare() -> None:
    trig = load_cases().get("triggering", {})
    TRIG.mkdir(parents=True, exist_ok=True)
    menu = [{"name": "explain", "description": _instrument_description()}, *DECOY_SKILLS]
    phrases = [{"phrase": p, "expect": "explain"} for p in trig.get("should_trigger", [])] + [
        {"phrase": p, "expect": "not-explain"} for p in trig.get("should_not_trigger", [])
    ]
    (TRIG / "phrases.json").write_text(json.dumps(phrases, indent=2))
    lines = [
        "# Triggering judge input",
        "",
        "For EACH user phrase below, pick the ONE skill from the menu whose",
        "description best fits the request, or `none` if no skill fits. Judge",
        "only from the descriptions.",
        "",
        "## Skill menu",
        "",
    ]
    lines += [f"- **{s['name']}**: {s['description']}" for s in menu]
    lines += ["", "## Phrases", ""]
    lines += [f"{i + 1}. {p['phrase']}" for i, p in enumerate(phrases)]
    lines += [
        "",
        "## Output",
        "",
        'Return STRICT JSON only: {"verdicts": {"<exact phrase>": "<skill-name-or-none>"}}',
    ]
    (TRIG / "judge_input.md").write_text("\n".join(lines))
    print(f"Wrote {TRIG / 'judge_input.md'} ({len(phrases)} phrases, {len(menu)} skills).")
    print(
        f"Have a judge panel classify each, write {TRIG / 'verdicts.json'} = "
        "{phrase: skill}, then: trigger-grade"
    )


def trigger_grade() -> int:
    f = TRIG / "verdicts.json"
    if not f.exists():
        print(f"  ! no {f}: run trigger-prepare + a judge first")
        return 2
    verdicts = json.loads(f.read_text())
    trig = load_cases().get("triggering", {})
    st = {p: (verdicts.get(p) == "explain") for p in trig.get("should_trigger", [])}
    sn = {p: (verdicts.get(p) == "explain") for p in trig.get("should_not_trigger", [])}
    m = metrics.compute([], triggering={"should_trigger": st, "should_not_trigger": sn})
    lines = ["# /explain triggering report", ""]
    for p, did in st.items():
        lines.append(
            f"[{'PASS' if did else 'FAIL'}] should_trigger:     {p!r} -> {verdicts.get(p)}"
        )
    for p, did in sn.items():
        lines.append(
            f"[{'PASS' if not did else 'FAIL'}] should_not_trigger: {p!r} -> {verdicts.get(p)}"
        )
    lines += ["", f"selection_accuracy: {m.get('selection_accuracy')}"]
    print("\n".join(lines))
    return 0 if m.get("selection_accuracy") == 1.0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Eval harness for the /explain skill")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", help="stage workdirs + emit real traces")
    sub.add_parser("grade", help="grade result.json in each workdir + emit metrics")
    sub.add_parser("trigger-prepare", help="emit the triggering judge input")
    sub.add_parser("trigger-grade", help="score verdicts.json -> selection_accuracy")
    args = ap.parse_args()
    if args.cmd == "prepare":
        prepare()
        return 0
    if args.cmd == "grade":
        return grade()
    if args.cmd == "trigger-prepare":
        trigger_prepare()
        return 0
    if args.cmd == "trigger-grade":
        return trigger_grade()
    return 2


if __name__ == "__main__":
    sys.exit(main())
