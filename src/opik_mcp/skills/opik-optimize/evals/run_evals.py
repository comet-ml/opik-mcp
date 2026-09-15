#!/usr/bin/env python3
# mypy: ignore-errors
"""Triggering harness for the `/opik-optimize` skill (selection_accuracy).

    uv run --with pyyaml python run_evals.py trigger-prepare
    # ...a judge panel classifies each phrase (descriptions only) into verdicts.json...
    uv run --with pyyaml python run_evals.py trigger-grade

The functional contract is pinned in cases.yaml; its seeder and grader are the
next step. Same shape as the other skills' harnesses.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / "SKILL.md"
NAME = "opik-optimize"
WORK = HERE / "_work"
TRIG = WORK / "triggering"

DECOY_SKILLS = [
    {
        "name": "opik-evaluate",
        "description": "Build an LLM evaluation and run it, returning an experiment with "
        "scores. MEASURES the app; changes nothing.",
    },
    {
        "name": "opik-compare",
        "description": "Run a candidate against the baseline over a test suite and read the "
        "deltas back. Compares two runs; does not produce a new prompt.",
    },
    {
        "name": "opik-test",
        "description": "Turn a failing trace into a regression check in a test suite.",
    },
    {
        "name": "opik-explain",
        "description": "Root-cause one specific Opik trace and return a grounded explanation.",
    },
    {
        "name": "opik",
        "description": "Reference for the Opik SDK — tracing, span types, the prompt library. "
        "Look up how to version a prompt; does not optimize one.",
    },
]


def load_cases() -> dict:
    import yaml

    return yaml.safe_load((HERE / "cases.yaml").read_text())


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
    correct = sum(1 for v in st.values() if v) + sum(1 for v in sn.values() if not v)
    total = len(st) + len(sn)
    acc = round(correct / total, 3) if total else 0.0
    for p, did in st.items():
        print(f"[{'PASS' if did else 'FAIL'}] should_trigger:     {p!r} -> {verdicts.get(p)}")
    for p, did in sn.items():
        print(f"[{'PASS' if not did else 'FAIL'}] should_not_trigger: {p!r} -> {verdicts.get(p)}")
    print(f"\nselection_accuracy: {acc}")
    return 0 if acc == 1.0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=f"Triggering harness for the /{NAME} skill")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("trigger-prepare", help="emit the triggering judge input")
    sub.add_parser("trigger-grade", help="score verdicts.json -> selection_accuracy")
    args = ap.parse_args()
    if args.cmd == "trigger-prepare":
        trigger_prepare()
        return 0
    if args.cmd == "trigger-grade":
        return trigger_grade()
    return 2


if __name__ == "__main__":
    sys.exit(main())
