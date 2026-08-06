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
    r = sub.add_parser("run", help="prepare + run an agent + grade (best-effort)")
    r.add_argument("--runner", default="claude-code", choices=["claude-code"])
    args = ap.parse_args()
    if args.cmd == "prepare":
        prepare(); return 0
    if args.cmd == "grade":
        return grade()
    if args.cmd == "run":
        return run_auto(args.runner)
    return 2


if __name__ == "__main__":
    sys.exit(main())
