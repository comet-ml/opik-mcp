#!/usr/bin/env python3
# mypy: ignore-errors
"""Test-automation harness for the `/opik-compare` skill.

Flows:

  1. Manual (default):
        uv run --with pyyaml python run_evals.py prepare   # seed a suite + baseline per case
        # ...run the /opik-compare skill in each _work/<case>/ with the prompt in PROMPT.txt;
        #    it should write result.json = {status, suite, baseline, candidate, deltas,
        #    regressions, fixes, compare_url, next_step}...
        uv run --with pyyaml python run_evals.py grade     # score vs planted.json, offline

  2. Triggering (selection_accuracy):
        uv run --with pyyaml python run_evals.py trigger-prepare
        uv run --with pyyaml python run_evals.py trigger-grade

`prepare` runs the seeder (fixtures/regress/seed.py): a unique suite with four
items and — for the functional case — a baseline experiment with known pass/fail.
Requires Opik configured (`~/.opik.config` or OPIK_API_KEY), network, and a judge
model for assertions (OPIK_EVAL_JUDGE_MODEL, default gpt-4o-mini, plus its key).
Grading is offline.
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
FIXTURES = HERE / "fixtures"
WORK = HERE / "_work"
TRIG = WORK / "triggering"

DECOY_SKILLS = [
    {
        "name": "opik-test",
        "description": "Turn a failing trace into a regression check — a test-suite item "
        "with assertions. Use to ADD a case, not to run the suite.",
    },
    {
        "name": "opik-evaluate",
        "description": "Build an LLM evaluation from scratch and run it, returning a first "
        "experiment with scores. A new eval, not a before/after on an existing suite.",
    },
    {
        "name": "opik-diagnose",
        "description": "Surface the live production traces worth attention, ranked by "
        "signal. Online traffic, not offline experiments.",
    },
    {
        "name": "opik-explain",
        "description": "Root-cause one specific Opik trace and return a grounded explanation.",
    },
    {
        "name": "opik-optimize",
        "description": "Improve a prompt with the Agent Optimizer against a dataset and "
        "metric. Changes the prompt, does not compare two runs of the app.",
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
        suite = (planted or {}).get("suite", "<seed-failed>")
        prompt = c["prompt"].replace("<SUITE>", suite)
        (wd / "PROMPT.txt").write_text(prompt)
        # The seeder is not part of the candidate under test; drop it so the
        # read-only check compares only what the skill may touch.
        (wd / "seed.py").unlink(missing_ok=True)
        lines.append(
            f"## {c['id']}  ({area})\n- workdir: {wd}\n- suite: {suite}\n- prompt: {prompt}\n"
        )
    (WORK / "PROMPTS.md").write_text("\n".join(lines))
    print(f"Prepared {len(_all_cases(cases))} workdir(s) under {WORK}")
    print("Run /opik-compare in each workdir (cwd = the workdir), write result.json, then `grade`.")


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
        fixture = FIXTURES / c["fixture"]
        results.append(grader.grade_case(c, fixture, wd, result, planted, area=area))
    m = metrics.compute(results)
    rep = metrics.report(results, m)
    (WORK / "report.md").write_text(rep)
    print(rep)
    return 0 if all(r.passed for r in results) else 1


# ---------- triggering ----------


def _skill_description() -> str:
    fm = SKILL.read_text().split("---")[1]
    m = re.search(r"^description:\s*(.+)$", fm, re.M)
    return m.group(1).strip() if m else ""


def trigger_prepare() -> None:
    trig = load_cases().get("triggering", {})
    TRIG.mkdir(parents=True, exist_ok=True)
    menu = [{"name": "opik-compare", "description": _skill_description()}, *DECOY_SKILLS]
    phrases = [{"phrase": p, "expect": "opik-compare"} for p in trig.get("should_trigger", [])] + [
        {"phrase": p, "expect": "not-opik-compare"} for p in trig.get("should_not_trigger", [])
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
    st = {p: (verdicts.get(p) == "opik-compare") for p in trig.get("should_trigger", [])}
    sn = {p: (verdicts.get(p) == "opik-compare") for p in trig.get("should_not_trigger", [])}
    m = metrics.compute([], triggering={"should_trigger": st, "should_not_trigger": sn})
    for p, did in st.items():
        print(f"[{'PASS' if did else 'FAIL'}] should_trigger:     {p!r} -> {verdicts.get(p)}")
    for p, did in sn.items():
        print(f"[{'PASS' if not did else 'FAIL'}] should_not_trigger: {p!r} -> {verdicts.get(p)}")
    print(f"\nselection_accuracy: {m.get('selection_accuracy')}")
    return 0 if m.get("selection_accuracy") == 1.0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Eval harness for the /opik-compare skill")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", help="seed a suite + baseline per case")
    sub.add_parser("grade", help="grade result.json vs planted.json (offline)")
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
