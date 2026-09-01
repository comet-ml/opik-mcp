#!/usr/bin/env python3
# mypy: ignore-errors
"""Test-automation harness for the `/opik-instrument` skill.

Flows:

  1. Functional (default):
        uv run --with pyyaml python run_evals.py prepare   # stage the fixture apps
        # ...for each workdir in _work/, run /opik-instrument on it (see PROMPTS.md);
        #    the skill writes result.json = {status, trace_id, changes, coverage, ...}...
        uv run --with pyyaml python run_evals.py grade     # score result.json vs expected.json

  2. Triggering (selection_accuracy):
        uv run --with pyyaml python run_evals.py trigger-prepare
        uv run --with pyyaml python run_evals.py trigger-grade

Unlike the diagnose harness, `prepare` does NOT seed traces — the fixture apps
are what the skill instruments and runs. Grading is offline; an optional online
integrity re-read of the trace runs only if Opik is configured (see grader.py).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
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
        "name": "opik-diagnose",
        "description": "Surface the Opik traces worth attention, ranked by signal. "
        "Use to discover which traces are broken, not to add tracing.",
    },
    {
        "name": "opik-explain",
        "description": "Root-cause a specific Opik trace you already have. "
        "Use to debug ONE trace, not to add tracing.",
    },
    {
        "name": "opik-evaluate",
        "description": "Build an Opik evaluation and run it, returning scores. "
        "Offline experiment results, not adding observability.",
    },
    {
        "name": "opik",
        "description": "Reference for how Opik works — concepts and SDK options. "
        "Use to look up the product, not to instrument an app.",
    },
    {
        "name": "code-review",
        "description": "Review code and report findings without changing it — "
        "a general audit, unrelated to tracing.",
    },
]


def load_cases() -> dict:
    import yaml

    return yaml.safe_load((HERE / "cases.yaml").read_text())


def functional(cases: dict) -> list[dict]:
    return cases.get("functional", [])


def _read_json(path: Path) -> dict | None:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
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
        (wd / "PROMPT.txt").write_text(c["prompt"])
        lines.append(f"## {c['id']}\n- workdir: {wd}\n- prompt: {c['prompt']}\n")
    (WORK / "PROMPTS.md").write_text("\n".join(lines))
    print(f"Prepared {len(functional(cases))} workdir(s) under {WORK}")
    print("Run /opik-instrument in each workdir (see PROMPT.txt), have it write")
    print("result.json there, then `grade`. Running needs an LLM provider key + Opik configured.")


def grade() -> int:
    cases = load_cases()
    results = []
    for c in functional(cases):
        wd = WORK / c["id"]
        if not wd.exists():
            print(f"  ! skip {c['id']}: no workdir (run `prepare` + the skill first)")
            continue
        result = _read_json(wd / "result.json")
        expected = _read_json(wd / "expected.json")
        results.append(grader.grade_case(c, FIXTURES / c["fixture"], wd, result, expected))
    m = metrics.compute(results)
    rep = metrics.report(results, m)
    (WORK / "report.md").write_text(rep)
    print(rep)
    return 0 if results and all(r.passed for r in results) else 1


# ---------- triggering ----------


def _skill_description() -> str:
    fm = SKILL.read_text().split("---")[1]
    m = re.search(r"^description:\s*(.+)$", fm, re.M)
    return m.group(1).strip() if m else ""


def trigger_prepare() -> None:
    trig = load_cases().get("triggering", {})
    TRIG.mkdir(parents=True, exist_ok=True)
    menu = [{"name": "opik-instrument", "description": _skill_description()}, *DECOY_SKILLS]
    phrases = [{"phrase": p, "expect": "opik-instrument"} for p in trig.get("should_trigger", [])] + [
        {"phrase": p, "expect": "not-opik-instrument"} for p in trig.get("should_not_trigger", [])
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
    st = {p: (verdicts.get(p) == "opik-instrument") for p in trig.get("should_trigger", [])}
    sn = {p: (verdicts.get(p) == "opik-instrument") for p in trig.get("should_not_trigger", [])}
    m = metrics.compute([], triggering={"should_trigger": st, "should_not_trigger": sn})
    for p, did in st.items():
        print(f"[{'PASS' if did else 'FAIL'}] should_trigger:     {p!r} -> {verdicts.get(p)}")
    for p, did in sn.items():
        print(f"[{'PASS' if not did else 'FAIL'}] should_not_trigger: {p!r} -> {verdicts.get(p)}")
    print(f"\nselection_accuracy: {m.get('selection_accuracy')}")
    return 0 if m.get("selection_accuracy") == 1.0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Eval harness for the /opik-instrument skill")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", help="stage the fixture apps under _work/")
    sub.add_parser("grade", help="grade result.json vs expected.json")
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
