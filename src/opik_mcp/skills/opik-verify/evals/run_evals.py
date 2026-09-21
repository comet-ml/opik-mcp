#!/usr/bin/env python3
# mypy: ignore-errors
"""Test-automation harness for the `/opik-verify` skill.

Flows:

  1. Manual (default):
        uv run --with pyyaml python run_evals.py prepare   # seed one suite + 4 runs, stage workdirs
        # ...run the /opik-verify skill in each _work/<case>/ (cwd = the workdir) with PROMPT.txt;
        #    it should write result.json = {status, policy, criteria, regressions, ...}...
        uv run --with pyyaml python run_evals.py grade     # score vs planted.json, offline

  2. Triggering (selection_accuracy):
        uv run --with pyyaml python run_evals.py trigger-prepare
        uv run --with pyyaml python run_evals.py trigger-grade

`prepare` runs the seeder once (fixtures/gate/seed.py): a 12-item suite, a baseline and
three candidates with known verdicts. Every case shares that seed; what differs per case is
the candidate id substituted into the prompt and the policy file staged in its workdir.
Requires Opik configured, network, and a judge model (OPIK_EVAL_JUDGE_MODEL, default
gpt-4o-mini, plus its key). Grading is offline.
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
SEED_DIR = WORK / "_seed"
TRIG = WORK / "triggering"
POLICY_FILE = "opik-release-policy.yaml"

DECOY_SKILLS = [
    {
        "name": "opik-compare",
        "description": "Run a candidate against the baseline over a test suite and read the "
        "numbers back — deltas, regressions, fixes. The NUMBERS, no ship/hold verdict.",
    },
    {
        "name": "opik-evaluate",
        "description": "Build an LLM evaluation and run it, returning an experiment with scores.",
    },
    {
        "name": "opik-test",
        "description": "Turn a failing trace into a regression check in a test suite.",
    },
    {
        "name": "opik-diagnose",
        "description": "Surface the live production traces worth attention, ranked by signal.",
    },
    {
        "name": "opik-online-eval",
        "description": "Take a judge live on production traffic as an online evaluation rule.",
    },
]


def load_cases() -> dict:
    import yaml

    return yaml.safe_load((HERE / "cases.yaml").read_text())


def _all_cases(cases: dict) -> list[tuple[str, dict]]:
    return [("functional", c) for c in cases.get("functional", [])] + [
        ("edge", c) for c in cases.get("edge", [])
    ]


def _seed() -> dict | None:
    if SEED_DIR.exists():
        shutil.rmtree(SEED_DIR)
    shutil.copytree(FIXTURES / "gate", SEED_DIR)
    try:
        out = subprocess.run(
            ["uv", "run", "--quiet", "python", "seed.py"],
            cwd=SEED_DIR,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except Exception as e:
        print(f"  ! seed failed: {e}")
        return None
    planted = SEED_DIR / "planted.json"
    if planted.exists():
        return json.loads(planted.read_text())
    print(f"  ! no planted.json:\n{out.stdout[-400:]}\n{out.stderr[-400:]}")
    return None


def _write_policy(wd: Path, overrides: dict) -> dict:
    import yaml

    policy = yaml.safe_load((FIXTURES / "gate" / POLICY_FILE).read_text()) or {}
    policy.update(overrides or {})
    (wd / POLICY_FILE).write_text(yaml.safe_dump(policy, sort_keys=False))
    return policy


def prepare() -> None:
    cases = load_cases()
    WORK.mkdir(exist_ok=True)
    planted = _seed()
    if not planted:
        print("seed failed; nothing staged")
        return
    lines = [f"skill: {SKILL}", f"suite: {planted['suite']}", ""]
    for area, c in _all_cases(cases):
        wd = WORK / c["id"]
        if wd.exists():
            shutil.rmtree(wd)
        shutil.copytree(FIXTURES / c["fixture"], wd)
        (wd / "seed.py").unlink(missing_ok=True)  # not part of what the skill may touch
        (wd / "planted.json").write_text(json.dumps(planted, indent=2))
        policy = _write_policy(wd, c.get("policy") or {})
        (wd / "policy_effective.json").write_text(json.dumps(policy))
        cand = planted[f"{c['candidate']}_id"]
        prompt = (
            c["prompt"]
            .replace("<CANDIDATE>", cand)
            .replace("<BASELINE>", planted["baseline_id"])
            .replace("<SUITE>", planted["suite"])
        )
        (wd / "PROMPT.txt").write_text(prompt)
        lines.append(
            f"## {c['id']}  ({area})\n- workdir: {wd}\n- candidate: {cand}\n- prompt: {prompt}\n"
        )
    (WORK / "PROMPTS.md").write_text("\n".join(lines))
    print(f"Prepared {len(_all_cases(cases))} workdir(s) under {WORK}")
    print("Run /opik-verify in each workdir (cwd = the workdir), write result.json, then `grade`.")


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
        policy = _read_json(wd, "policy_effective.json") or {}
        results.append(
            grader.grade_case(c, FIXTURES / c["fixture"], wd, result, planted, policy, area=area)
        )
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
    menu = [{"name": "opik-verify", "description": _skill_description()}, *DECOY_SKILLS]
    phrases = [{"phrase": p, "expect": "opik-verify"} for p in trig.get("should_trigger", [])] + [
        {"phrase": p, "expect": "not-opik-verify"} for p in trig.get("should_not_trigger", [])
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
    st = {p: (verdicts.get(p) == "opik-verify") for p in trig.get("should_trigger", [])}
    sn = {p: (verdicts.get(p) == "opik-verify") for p in trig.get("should_not_trigger", [])}
    m = metrics.compute([], triggering={"should_trigger": st, "should_not_trigger": sn})
    for p, did in st.items():
        print(f"[{'PASS' if did else 'FAIL'}] should_trigger:     {p!r} -> {verdicts.get(p)}")
    for p, did in sn.items():
        print(f"[{'PASS' if not did else 'FAIL'}] should_not_trigger: {p!r} -> {verdicts.get(p)}")
    print(f"\nselection_accuracy: {m.get('selection_accuracy')}")
    return 0 if m.get("selection_accuracy") == 1.0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Eval harness for the /opik-verify skill")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", help="seed a suite + 3 experiments; stage a workdir per case")
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
