#!/usr/bin/env python3
"""Test-automation harness for the `/instrument` skill.

Two supported flows (per the guide's "scripted testing" level):

  1. Manual (default, always works):
        uv run --with pyyaml python run_evals.py prepare   # stage fixture workdirs
        # ...run the /instrument skill on each workdir (Claude Code / an agent)...
        uv run --with pyyaml python run_evals.py grade     # score + metrics

  2. Automatic (best-effort, needs `claude` on PATH):
        uv run --with pyyaml python run_evals.py run --runner claude-code

The grader is deterministic (static analysis + file diff) so `grade` is
repeatable. Live-trace `verified` status is provided by the runner/agent, which
writes a `result.json` = {"status": "...", ...} into each workdir (the skill's
"small internal state model"). Absent that, cases are graded on static checks
and status is left unknown.
"""

from __future__ import annotations

import argparse
import json
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


def load_cases() -> dict:
    import yaml  # provided via `uv run --with pyyaml`
    return yaml.safe_load((HERE / "cases.yaml").read_text())


def app_cases(cases: dict) -> list[tuple[str, dict]]:
    return ([("functional", c) for c in cases.get("functional", [])]
            + [("edge", c) for c in cases.get("edge", [])])


def read_status(wd: Path) -> str | None:
    f = wd / "result.json"
    if f.exists():
        try:
            return json.loads(f.read_text()).get("status")
        except Exception:
            return None
    return None


def prepare() -> list[tuple[str, dict, Path]]:
    cases = load_cases()
    WORK.mkdir(exist_ok=True)
    staged, lines = [], [f"skill: {SKILL}", ""]
    for area, c in app_cases(cases):
        wd = WORK / c["id"]
        if wd.exists():
            shutil.rmtree(wd)
        shutil.copytree(FIXTURES / c["fixture"], wd)
        staged.append((area, c, wd))
        lines.append(f"## {c['id']}  ({area})\n- workdir: {wd}\n- prompt: {c['prompt']}\n")
    (WORK / "PROMPTS.md").write_text("\n".join(lines))
    print(f"Prepared {len(staged)} workdirs under {WORK}")
    print(f"Next: run the /instrument skill on each (see {WORK}/PROMPTS.md), then `grade`.")
    return staged


def grade() -> int:
    cases = load_cases()
    results = []
    for area, c in app_cases(cases):
        wd = WORK / c["id"]
        if not wd.exists():
            print(f"  ! skip {c['id']}: no workdir (run `prepare` + the skill first)")
            continue
        status = read_status(wd)
        r = grader.grade_case(c, FIXTURES / c["fixture"], wd, reported_status=status, area=area)
        r.reported_status = status  # for metrics
        results.append(r)
    m = metrics.compute(results)
    rep = metrics.report(results, m)
    (WORK / "report.md").write_text(rep)
    (WORK / "report.json").write_text(json.dumps(
        {"metrics": m,
         "cases": [{"id": r.id, "passed": r.passed, "status_ok": r.status_ok,
                    "checks": {n: {"ok": ok, "detail": d} for n, (ok, d) in r.checks.items()}}
                   for r in results]},
        indent=2))
    print(rep)
    return 0 if all(r.passed for r in results) else 1


# ---------- triggering (selection_accuracy) ----------
#
# Triggering can't be graded from files — it asks "given a phrase, does the
# model pick this skill?". We test it faithfully with a judge that sees ONLY
# skill *descriptions* (the real `instrument` one + realistic decoys, incl. a
# better home for each should_not_trigger phrase) and classifies each phrase.

TRIG = WORK / "triggering"

DECOY_SKILLS = [
    {"name": "evaluate", "description": "Run an Opik evaluation: score a dataset "
     "or experiment with LLM/heuristic metrics and compare results. Use to measure "
     "output quality, run experiments, or build and score test suites — not to add "
     "tracing to app code."},
    {"name": "opik", "description": "Reference for how Opik works — tracing "
     "concepts, SDK/REST/integration options, and best practices. Use to look up or "
     "explain Opik, not to modify code."},
    {"name": "scaffold-app", "description": "Create a brand-new application from "
     "scratch (for example a new FastAPI or Express service) with project layout "
     "and boilerplate."},
    {"name": "code-review", "description": "Review existing code and report findings "
     "without changing it — a read-only audit pass."},
    {"name": "stdlib-logging", "description": "Add standard-library logging (Python "
     "logging module, console logs) to an app — plain log statements, not a "
     "distributed-tracing or observability platform."},
]


def _instrument_description() -> str:
    import re
    fm = SKILL.read_text().split("---")[1]
    m = re.search(r"^description:\s*(.+)$", fm, re.M)
    return m.group(1).strip() if m else ""


def _menu() -> list[dict]:
    return [{"name": "instrument", "description": _instrument_description()}] + DECOY_SKILLS


def trigger_prepare() -> None:
    trig = load_cases().get("triggering", {})
    TRIG.mkdir(parents=True, exist_ok=True)
    menu = _menu()
    phrases = ([{"phrase": p, "expect": "instrument"} for p in trig.get("should_trigger", [])]
               + [{"phrase": p, "expect": "not-instrument"} for p in trig.get("should_not_trigger", [])])
    (TRIG / "phrases.json").write_text(json.dumps(phrases, indent=2))
    lines = ["# Triggering judge input", "",
             "For EACH user phrase below, pick the ONE skill from the menu whose",
             "description best fits the request, or `none` if no skill fits. Judge",
             "only from the descriptions — do not assume anything not written there.",
             "", "## Skill menu", ""]
    lines += [f"- **{s['name']}**: {s['description']}" for s in menu]
    lines += ["", "## Phrases", ""]
    lines += [f"{i + 1}. {p['phrase']}" for i, p in enumerate(phrases)]
    lines += ["", "## Output", "",
              'Return STRICT JSON only: {"verdicts": {"<exact phrase>": "<skill-name-or-none>"}}',
              "one entry per phrase, key = the exact phrase text."]
    (TRIG / "judge_input.md").write_text("\n".join(lines))
    print(f"Wrote {TRIG / 'judge_input.md'} ({len(phrases)} phrases, {len(menu)} skills).")
    print("Have one or more judges classify each phrase (majority-vote a panel),")
    print(f"write {TRIG / 'verdicts.json'} = {{phrase: skill}}, then: trigger-grade")


def trigger_grade() -> int:
    f = TRIG / "verdicts.json"
    if not f.exists():
        print(f"  ! no {f}: run trigger-prepare + a judge first")
        return 2
    verdicts = json.loads(f.read_text())
    trig = load_cases().get("triggering", {})
    st = {p: (verdicts.get(p) == "instrument") for p in trig.get("should_trigger", [])}
    sn = {p: (verdicts.get(p) == "instrument") for p in trig.get("should_not_trigger", [])}
    m = metrics.compute([], triggering={"should_trigger": st, "should_not_trigger": sn})
    lines = ["# /instrument triggering report", ""]
    for p, did in st.items():
        lines.append(f"[{'PASS' if did else 'FAIL'}] should_trigger:     {p!r} -> {verdicts.get(p)}")
    for p, did in sn.items():
        lines.append(f"[{'PASS' if not did else 'FAIL'}] should_not_trigger: {p!r} -> {verdicts.get(p)}")
    lines += ["", f"selection_accuracy: {m.get('selection_accuracy')}"]
    rep = "\n".join(lines)
    (TRIG / "report.md").write_text(rep)
    print(rep)
    return 0 if m.get("selection_accuracy") == 1.0 else 1


# ---------- optional automatic runner ----------

def claude_code_run(prompt: str, workdir: Path) -> None:
    """Best-effort: drive the skill headlessly with `claude`. Flags/skill
    discovery vary by install — adjust as needed. Failures are non-fatal; the
    grader still scores whatever the run produced."""
    inst = (f"{prompt}\n\nUse the Opik `instrument` skill at {SKILL}. Work only in "
            f"this directory. When done, write result.json = "
            f'{{"status": "<verified|blocked|already_verified|unsupported>"}}.')
    try:
        subprocess.run(["claude", "-p", inst], cwd=workdir, timeout=600,
                       check=False, capture_output=True, text=True)
    except FileNotFoundError:
        print("  ! `claude` not found on PATH; use the manual flow instead.")
    except Exception as e:  # noqa: BLE001
        print(f"  ! runner error for {workdir.name}: {e}")


def run_auto(runner: str) -> int:
    staged = prepare()
    if runner == "claude-code":
        for _area, c, wd in staged:
            print(f"  running {c['id']} ...")
            claude_code_run(c["prompt"], wd)
    else:
        print(f"Unknown runner '{runner}'. Use the manual flow (prepare/grade).")
        return 2
    return grade()


def main() -> int:
    ap = argparse.ArgumentParser(description="Eval harness for the /instrument skill")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", help="stage fixture workdirs")
    sub.add_parser("grade", help="grade the staged workdirs + emit metrics")
    sub.add_parser("trigger-prepare", help="emit the triggering judge input")
    sub.add_parser("trigger-grade", help="score verdicts.json -> selection_accuracy")
    r = sub.add_parser("run", help="prepare + run an agent + grade (best-effort)")
    r.add_argument("--runner", default="claude-code", choices=["claude-code"])
    args = ap.parse_args()
    if args.cmd == "prepare":
        prepare(); return 0
    if args.cmd == "grade":
        return grade()
    if args.cmd == "trigger-prepare":
        trigger_prepare(); return 0
    if args.cmd == "trigger-grade":
        return trigger_grade()
    if args.cmd == "run":
        return run_auto(args.runner)
    return 2


if __name__ == "__main__":
    sys.exit(main())
