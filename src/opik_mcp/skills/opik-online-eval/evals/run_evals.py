#!/usr/bin/env python3
# mypy: ignore-errors
"""Test-automation harness for the `/opik-online-eval` skill.

Flows:

  1. Manual (default):
        uv run --with pyyaml python run_evals.py prepare   # seed a fresh project per case
        # ...run the /opik-online-eval skill in each _work/<case>/ (cwd = the workdir) with
        #    PROMPT.txt; it should write result.json = {status, rule, score_name, variables,
        #    verification, source, next_step}...
        uv run --with pyyaml python run_evals.py grade     # score vs planted.json, offline
        uv run --with pyyaml python run_evals.py verify    # (network) exactly one rule of that name

  2. Triggering (selection_accuracy):
        uv run --with pyyaml python run_evals.py trigger-prepare
        uv run --with pyyaml python run_evals.py trigger-grade

`prepare` runs the seeder (fixtures/traffic/seed.py) per case: six production-shaped
traces in a fresh project, plus — for the edge case — a pre-created rule. Requires
Opik configured and network. Rules run on the workspace's free provider, so no LLM
key is needed. Grading is offline.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import grader
import metrics

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / "SKILL.md"
NAME = "opik-online-eval"
FIXTURES = HERE / "fixtures"
WORK = HERE / "_work"
TRIG = WORK / "triggering"

DECOY_SKILLS = [
    {
        "name": "opik-evaluate",
        "description": "Build an LLM evaluation and run it OFFLINE against the app, returning "
        "an experiment with scores. Not live traffic.",
    },
    {
        "name": "opik-compare",
        "description": "Run a candidate against the baseline over a test suite and read the "
        "deltas back. Offline before/after.",
    },
    {
        "name": "opik-diagnose",
        "description": "Surface the live production traces worth attention, ranked by signal. "
        "Reads scores that already exist; creates no rule.",
    },
    {
        "name": "opik-test",
        "description": "Turn a failing trace into a regression check in a test suite.",
    },
    {
        "name": "opik-optimize",
        "description": "Improve a prompt with the Agent Optimizer against a dataset and metric.",
    },
]


def load_cases() -> dict:
    import yaml

    return yaml.safe_load((HERE / "cases.yaml").read_text())


def _all_cases(cases: dict) -> list[tuple[str, dict]]:
    return [("functional", c) for c in cases.get("functional", [])] + [
        ("edge", c) for c in cases.get("edge", [])
    ]


def _seed(wd: Path, env: dict) -> dict | None:
    try:
        out = subprocess.run(
            ["uv", "run", "--quiet", "python", "seed.py"],
            cwd=wd,
            capture_output=True,
            text=True,
            timeout=900,
            env={**os.environ, **env},
        )
    except Exception as e:
        print(f"  ! seed failed for {wd.name}: {e}")
        return None
    planted = wd / "planted.json"
    if planted.exists():
        return json.loads(planted.read_text())
    print(f"  ! no planted.json from {wd.name}:\n{out.stdout[-400:]}\n{out.stderr[-400:]}")
    return None


def prepare() -> None:
    cases = load_cases()
    WORK.mkdir(exist_ok=True)
    lines = [f"skill: {SKILL}", ""]
    for area, c in _all_cases(cases):
        wd = WORK / c["id"]
        if wd.exists():
            shutil.rmtree(wd)
        shutil.copytree(FIXTURES / c["fixture"], wd)
        planted = _seed(wd, {k: str(v) for k, v in (c.get("seed_env") or {}).items()})
        project = (planted or {}).get("project", "<seed-failed>")
        prompt = c["prompt"].replace("<PROJECT>", project)
        (wd / "PROMPT.txt").write_text(prompt)
        (wd / "seed.py").unlink(missing_ok=True)  # not part of what the skill may touch
        lines.append(
            f"## {c['id']}  ({area})\n- workdir: {wd}\n- project: {project}\n- prompt: {prompt}\n"
        )
    (WORK / "PROMPTS.md").write_text("\n".join(lines))
    print(f"Prepared {len(_all_cases(cases))} workdir(s) under {WORK}")
    print(f"Run /{NAME} in each workdir (cwd = the workdir), write result.json, then `grade`.")


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
    for area, c in _all_cases(cases):
        wd = WORK / c["id"]
        if not wd.exists():
            print(f"  ! skip {c['id']}: no workdir (run `prepare` + the skill first)")
            continue
        result = _read_json(wd, "result.json")
        planted = _read_json(wd, "planted.json")
        results.append(
            grader.grade_case(c, FIXTURES / c["fixture"], wd, result, planted, area=area)
        )
    m = metrics.compute(results)
    rep = metrics.report(results, m)
    (WORK / "report.md").write_text(rep)
    print(rep)
    return 0 if all(r.passed for r in results) else 1


def verify() -> int:
    """Networked: the project holds exactly one rule with the reported name."""
    import opik

    client = opik.Opik()
    rc = 0
    for _area, c in _all_cases(load_cases()):
        wd = WORK / c["id"]
        result = _read_json(wd, "result.json") or {}
        planted = _read_json(wd, "planted.json") or {}
        name = (result.get("rule") or {}).get("name")
        if not name or result.get("status") == "blocked":
            continue
        rules = client.rest_client.automation_rule_evaluators.find_evaluators(
            project_id=planted["project_id"]
        )
        same = [r for r in (rules.content or []) if r.name == name]
        ok = len(same) == 1
        print(
            f"[{'PASS' if ok else 'FAIL'}] {c['id']}: {len(same)} rule(s) named {name!r} in "
            f"{planted['project']} (expect exactly 1)"
        )
        rc = rc or (0 if ok else 1)
    return rc


# ---------- triggering ----------


def _skill_description() -> str:
    fm = SKILL.read_text().split("---")[1]
    m = re.search(r"^description:\s*(.+)$", fm, re.M)
    return m.group(1).strip() if m else ""


def trigger_prepare() -> None:
    trig = load_cases().get("triggering", {})
    TRIG.mkdir(parents=True, exist_ok=True)
    menu = [{"name": NAME, "description": _skill_description()}, *DECOY_SKILLS]
    phrases = [{"phrase": p, "expect": NAME} for p in trig.get("should_trigger", [])] + [
        {"phrase": p, "expect": f"not-{NAME}"} for p in trig.get("should_not_trigger", [])
    ]
    (TRIG / "phrases.json").write_text(json.dumps(phrases, indent=2))
    lines = [
        "# Triggering judge input",
        "",
        "For EACH user phrase, pick the ONE skill whose description best fits, or",
        "`none`. Judge only from the descriptions.",
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
        'Return STRICT JSON: {"verdicts": {"<exact phrase>": "<skill-or-none>"}}',
    ]
    (TRIG / "judge_input.md").write_text("\n".join(lines))
    print(
        f"Wrote {TRIG / 'judge_input.md'}. Judge it, write "
        f"{TRIG / 'verdicts.json'}, then: trigger-grade"
    )


def trigger_grade() -> int:
    f = TRIG / "verdicts.json"
    if not f.exists():
        print(f"  ! no {f}: run trigger-prepare + a judge first")
        return 2
    verdicts = json.loads(f.read_text())
    verdicts = verdicts.get("verdicts", verdicts)
    trig = load_cases().get("triggering", {})
    st = {p: (verdicts.get(p) == NAME) for p in trig.get("should_trigger", [])}
    sn = {p: (verdicts.get(p) == NAME) for p in trig.get("should_not_trigger", [])}
    m = metrics.compute([], triggering={"should_trigger": st, "should_not_trigger": sn})
    for p, did in st.items():
        print(f"[{'PASS' if did else 'FAIL'}] should_trigger:     {p!r} -> {verdicts.get(p)}")
    for p, did in sn.items():
        print(f"[{'PASS' if not did else 'FAIL'}] should_not_trigger: {p!r} -> {verdicts.get(p)}")
    print(f"\nselection_accuracy: {m.get('selection_accuracy')}")
    return 0 if m.get("selection_accuracy") == 1.0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=f"Eval harness for the /{NAME} skill")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", help="seed a fresh project per case")
    sub.add_parser("grade", help="grade result.json vs planted.json (offline)")
    sub.add_parser("verify", help="(network) exactly one rule of the reported name exists")
    sub.add_parser("trigger-prepare", help="emit the triggering judge input")
    sub.add_parser("trigger-grade", help="score verdicts.json -> selection_accuracy")
    args = ap.parse_args()
    if args.cmd == "prepare":
        prepare()
        return 0
    if args.cmd == "grade":
        return grade()
    if args.cmd == "verify":
        return verify()
    if args.cmd == "trigger-prepare":
        trigger_prepare()
        return 0
    if args.cmd == "trigger-grade":
        return trigger_grade()
    return 2


if __name__ == "__main__":
    sys.exit(main())
