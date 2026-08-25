#!/usr/bin/env python3
"""Test-automation harness for the `/find` skill.

Flows:

  1. Manual (default):
        uv run --with pyyaml python run_evals.py prepare   # seed a project with known traces
        # ...run the /find skill on the project named in _work/triage/PROMPT.txt;
        #    it should write result.json = {status, scope, shortlist, source, next_step}...
        uv run --with pyyaml python run_evals.py grade     # score shortlist vs planted ids

  2. Triggering (selection_accuracy):
        uv run --with pyyaml python run_evals.py trigger-prepare
        uv run --with pyyaml python run_evals.py trigger-grade

`prepare` runs the seeder (fixtures/seed) to plant known traces in a fresh
project and records their ids by role in planted.json. Requires Opik configured
(`~/.opik.config` or OPIK_API_KEY) + network. Grading is offline.
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
        "name": "explain",
        "description": "Root-cause a specific Opik trace and return a "
        "grounded explanation. Use to debug ONE trace you already have, not to discover which.",
    },
    {
        "name": "instrument",
        "description": "Add Opik tracing to an existing app and verify a "
        "real trace lands. Use to add observability, not to find existing traces.",
    },
    {
        "name": "evaluate",
        "description": "Build an Opik evaluation and run it, returning an "
        "experiment with scores. Offline experiment results, not live production triage.",
    },
    {
        "name": "opik",
        "description": "Reference for how Opik works — tracing concepts and SDK "
        "options. Use to look up the product, not to triage traces.",
    },
    {
        "name": "code-review",
        "description": "Review code and report findings without changing "
        "it — a general audit, unrelated to traces.",
    },
]


def load_cases() -> dict:
    import yaml

    return yaml.safe_load((HERE / "cases.yaml").read_text())


def functional(cases: dict) -> list[dict]:
    return cases.get("functional", [])


def _seed(wd: Path) -> dict | None:
    try:
        out = subprocess.run(
            ["uv", "run", "--quiet", "python", "seed.py"],
            cwd=wd,
            capture_output=True,
            text=True,
            timeout=600,
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
    for c in functional(cases):
        wd = WORK / c["id"]
        if wd.exists():
            shutil.rmtree(wd)
        shutil.copytree(FIXTURES / c["fixture"], wd)
        planted = _seed(wd)
        project = (planted or {}).get("project", "<seed-failed>")
        prompt = c["prompt"].replace("<PROJECT>", project)
        (wd / "PROMPT.txt").write_text(prompt)
        lines.append(f"## {c['id']}\n- workdir: {wd}\n- project: {project}\n- prompt: {prompt}\n")
    (WORK / "PROMPTS.md").write_text("\n".join(lines))
    print(f"Prepared {len(functional(cases))} workdir(s) under {WORK}")
    print("Run /find on the project in each PROMPT.txt, write result.json, then `grade`.")


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
    for c in functional(cases):
        wd = WORK / c["id"]
        if not wd.exists():
            print(f"  ! skip {c['id']}: no workdir (run `prepare` + the skill first)")
            continue
        result = _read_json(wd, "result.json")
        planted = _read_json(wd, "planted.json")
        results.append(grader.grade_case(c, FIXTURES / c["fixture"], wd, result, planted))
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
    menu = [{"name": "find", "description": _skill_description()}, *DECOY_SKILLS]
    phrases = [{"phrase": p, "expect": "find"} for p in trig.get("should_trigger", [])] + [
        {"phrase": p, "expect": "not-find"} for p in trig.get("should_not_trigger", [])
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
    trig = load_cases().get("triggering", {})
    st = {p: (verdicts.get(p) == "find") for p in trig.get("should_trigger", [])}
    sn = {p: (verdicts.get(p) == "find") for p in trig.get("should_not_trigger", [])}
    m = metrics.compute([], triggering={"should_trigger": st, "should_not_trigger": sn})
    for p, did in st.items():
        print(f"[{'PASS' if did else 'FAIL'}] should_trigger:     {p!r} -> {verdicts.get(p)}")
    for p, did in sn.items():
        print(f"[{'PASS' if not did else 'FAIL'}] should_not_trigger: {p!r} -> {verdicts.get(p)}")
    print(f"\nselection_accuracy: {m.get('selection_accuracy')}")
    return 0 if m.get("selection_accuracy") == 1.0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Eval harness for the /find skill")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", help="seed a project with known traces")
    sub.add_parser("grade", help="grade result.json shortlist vs planted ids")
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
