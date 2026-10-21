#!/usr/bin/env python3
# mypy: ignore-errors
"""Test-automation harness for the `/opik-test` skill.

Flows:

  1. Manual (default):
        uv run --with pyyaml python run_evals.py prepare   # emit a real failing trace per case
        # ...run the /opik-test skill on each _work/<case>/ with the prompt in PROMPT.txt;
        #    it should write result.json = {status, suite, item, source, next_step}.
        #    Run `capture` BEFORE `duplicate` — the edge case reuses its trace...
        uv run --with pyyaml python run_evals.py grade     # deterministic scoring, offline
        uv run --with pyyaml python run_evals.py verify    # (network) the item exists in Opik

  2. Triggering (selection_accuracy):
        uv run --with pyyaml python run_evals.py trigger-prepare
        uv run --with pyyaml python run_evals.py trigger-grade

`prepare` runs the toolbug fixture to emit a REAL trace and substitutes its id
into <TRACE_ID>. Requires Opik configured (`~/.opik.config` or OPIK_API_KEY)
and network. `grade` is offline; `verify` reads the suite back over the SDK.
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
        "name": "opik-compare",
        "description": "Run a candidate against the baseline over a test suite and read "
        "the numbers back. Use to RUN the suite and see what changed, not to add a case to it.",
    },
    {
        "name": "opik-evaluate",
        "description": "Build an LLM evaluation from scratch and run it, returning an "
        "experiment with scores. A whole eval, not one regression case.",
    },
    {
        "name": "opik-explain",
        "description": "Root-cause a specific Opik trace and return a grounded "
        "explanation. Use to understand WHY it failed, not to capture it as a test.",
    },
    {
        "name": "opik-instrument",
        "description": "Add Opik tracing to an existing app and verify a real trace lands.",
    },
    {
        "name": "code-review",
        "description": "Review code and report findings without changing it — a general "
        "audit, unrelated to traces.",
    },
]


def load_cases() -> dict:
    import yaml

    return yaml.safe_load((HERE / "cases.yaml").read_text())


def _all_cases(cases: dict) -> list[tuple[str, dict]]:
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
        print(f"  ! fixture run failed for {wd.name}: {e}")
        return None
    for line in (out.stdout or "").splitlines():
        m = re.search(r"TRACE_ID:\s*(\S+)", line)
        if m and m.group(1) != "<none>":
            return m.group(1)
    print(f"  ! no TRACE_ID from {wd.name} (Opik configured?)\n{out.stderr[-400:]}")
    return None


def prepare() -> None:
    cases = load_cases()
    WORK.mkdir(exist_ok=True)
    lines = [f"skill: {SKILL}", ""]
    emitted: dict[str, str | None] = {}
    for area, c in _all_cases(cases):
        wd = WORK / c["id"]
        if wd.exists():
            shutil.rmtree(wd)
        shutil.copytree(FIXTURES / c["fixture"], wd)
        if c.get("emit_trace"):
            tid = _emit_trace(wd)
        elif c.get("reuse_trace_from"):
            tid = emitted.get(c["reuse_trace_from"])
        else:
            tid = None
        emitted[c["id"]] = tid
        (wd / "trace_id.txt").write_text(tid or "")
        prompt = c["prompt"].replace("<TRACE_ID>", tid or "<no-trace-emitted>")
        (wd / "PROMPT.txt").write_text(prompt)
        lines.append(
            f"## {c['id']}  ({area})\n- workdir: {wd}\n- trace_id: {tid}\n- prompt: {prompt}\n"
        )
    (WORK / "PROMPTS.md").write_text("\n".join(lines))
    print(f"Prepared {len(_all_cases(cases))} workdir(s) under {WORK}")
    print(
        "Run /opik-test in each (capture first, then duplicate), write result.json, then `grade`."
    )


def _read_json(wd: Path, name: str) -> dict | None:
    f = wd / name
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            return None
    return None


def _trace_id(wd: Path) -> str | None:
    f = wd / "trace_id.txt"
    return f.read_text().strip() or None if f.exists() else None


def grade() -> int:
    cases = load_cases()
    results = []
    for area, c in _all_cases(cases):
        wd = WORK / c["id"]
        if not wd.exists():
            print(f"  ! skip {c['id']}: no workdir (run `prepare` + the skill first)")
            continue
        result = _read_json(wd, "result.json")
        results.append(
            grader.grade_case(c, FIXTURES / c["fixture"], wd, result, _trace_id(wd), area=area)
        )
    m = metrics.compute(results)
    rep = metrics.report(results, m)
    (WORK / "report.md").write_text(rep)
    print(rep)
    return 0 if all(r.passed for r in results) else 1


def verify() -> int:
    """Networked: the captured item is really in the suite, with its assertions."""
    import opik

    client = opik.Opik()
    rc = 0
    for _area, c in _all_cases(load_cases()):
        wd = WORK / c["id"]
        result = _read_json(wd, "result.json") or {}
        if result.get("status") not in {"captured", "exists"}:
            continue
        suite = (result.get("suite") or {}).get("name")
        project = (result.get("suite") or {}).get("project")
        tid = _trace_id(wd)
        try:
            s = client.get_test_suite(name=suite, project_name=project)
            items = s.get_items(filter_string=f'data.source_trace_id = "{tid}"')
        except Exception as e:
            print(f"[FAIL] {c['id']}: cannot read suite {suite!r}: {e}")
            rc = 1
            continue
        ok = len(items) == 1 and bool(items[0].get("assertions"))
        print(
            f"[{'PASS' if ok else 'FAIL'}] {c['id']}: {len(items)} item(s) for trace {tid} "
            f"in {suite!r} (expect exactly 1 with assertions)"
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
    menu = [{"name": "opik-test", "description": _skill_description()}, *DECOY_SKILLS]
    phrases = [{"phrase": p, "expect": "opik-test"} for p in trig.get("should_trigger", [])] + [
        {"phrase": p, "expect": "not-opik-test"} for p in trig.get("should_not_trigger", [])
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
    st = {p: (verdicts.get(p) == "opik-test") for p in trig.get("should_trigger", [])}
    sn = {p: (verdicts.get(p) == "opik-test") for p in trig.get("should_not_trigger", [])}
    m = metrics.compute([], triggering={"should_trigger": st, "should_not_trigger": sn})
    for p, did in st.items():
        print(f"[{'PASS' if did else 'FAIL'}] should_trigger:     {p!r} -> {verdicts.get(p)}")
    for p, did in sn.items():
        print(f"[{'PASS' if not did else 'FAIL'}] should_not_trigger: {p!r} -> {verdicts.get(p)}")
    print(f"\nselection_accuracy: {m.get('selection_accuracy')}")
    return 0 if m.get("selection_accuracy") == 1.0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Eval harness for the /opik-test skill")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", help="emit a real failing trace per case")
    sub.add_parser("grade", help="grade result.json against the emitted trace (offline)")
    sub.add_parser("verify", help="(network) confirm the captured item exists in the suite")
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
