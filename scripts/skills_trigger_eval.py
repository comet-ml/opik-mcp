#!/usr/bin/env python3
# mypy: ignore-errors
"""Fleet-wide triggering eval: every bundled skill's description vs every skill's phrases.

Each skill's `evals/cases.yaml` tests its own triggering against a hand-picked menu of
decoys. That answers "does MY description win MY phrases against a few neighbours".
It does not answer the question that matters once ten skills ship together: given the
whole menu the agent actually sees, does each phrase route to the skill that owns it?
This script asks that question with an independent LLM judge.

    OPENAI_API_KEY=... uv run --with pyyaml python scripts/skills_trigger_eval.py \
        [--model gpt-4o-mini] [--out report.md]

The judge sees only the skill names and their frontmatter descriptions — the same
information a host gives its agent — and classifies each phrase into one skill or
`none`. Expected labels come from the `should_trigger` lists (owner = that skill) and
the `should_not_trigger` lists (owner = the skill named in the trailing `# comment`,
or `none`). A phrase two skills both claim is reported as a conflict, not scored.

Development tooling: not shipped in the pack, not run in CI (it spends a few cents
of judge calls). Run it whenever a description changes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "src" / "opik_mcp" / "skills"
# The trailing comment on a should_not_trigger line names the owner: "# compare" -> opik-compare.
OWNER_RE = re.compile(r"#\s*([a-z][a-z-]*)\s*$")


def _frontmatter_description(skill_dir: Path) -> str:
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    fm = yaml.safe_load(text.split("---")[1])
    return str(fm["description"]).strip()


def _phrases(skill: str, cases_path: Path) -> list[tuple[str, str]]:
    """(phrase, expected owner) pairs from one skill's cases.yaml."""
    data = yaml.safe_load(cases_path.read_text(encoding="utf-8")) or {}
    trig = data.get("triggering") or {}
    out = [(p, skill) for p in trig.get("should_trigger", [])]
    # Owners of negatives live in comments, which yaml drops — read the raw lines.
    raw = cases_path.read_text(encoding="utf-8").splitlines()
    in_neg = False
    for line in raw:
        if line.strip().startswith("should_not_trigger:"):
            in_neg = True
            continue
        if in_neg and (not line.startswith(" ") or re.match(r"^\s{2}[a-z_]+:", line)):
            in_neg = False
        if in_neg and line.strip().startswith("- "):
            phrase = yaml.safe_load(line.strip()[2:].split("#")[0])
            m = OWNER_RE.search(line)
            owner = m.group(1) if m else "none"
            if owner in {"none", "not a skill", "greenfield"}:
                owner = "none"
            elif not owner.startswith("opik"):
                owner = f"opik-{owner}"
            out.append((str(phrase), owner))
    return out


def _judge(menu: list[dict], phrases: list[str], model: str) -> dict[str, str]:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("OPENAI_API_KEY is required for the judge")
    menu_text = "\n".join(f"- {s['name']}: {s['description']}" for s in menu)
    numbered = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(phrases))
    prompt = (
        "You are the routing layer of a coding agent. For EACH user phrase below, pick the ONE "
        "skill whose description best fits, or `none` if no skill fits. Judge only from the "
        "descriptions.\n\n## Skill menu\n"
        + menu_text
        + "\n\n## Phrases\n"
        + numbered
        + '\n\nReturn STRICT JSON only: {"verdicts": {"<exact phrase>": "<skill-name-or-none>"}}'
    )
    body = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        content = json.load(r)["choices"][0]["message"]["content"]
    return json.loads(content).get("verdicts", {})


def main() -> int:
    ap = argparse.ArgumentParser(description="Fleet-wide skill triggering eval")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--out", default=None, help="write the Markdown report here too")
    args = ap.parse_args()

    skills = sorted(d for d in SKILLS.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())
    menu = [{"name": d.name, "description": _frontmatter_description(d)} for d in skills]

    expected: dict[str, set[str]] = defaultdict(set)
    for d in skills:
        cases = d / "evals" / "cases.yaml"
        if cases.is_file():
            for phrase, owner in _phrases(d.name, cases):
                expected[phrase].add(owner)

    conflicts = {p: sorted(o) for p, o in expected.items() if len(o) > 1}
    scored = {p: next(iter(o)) for p, o in expected.items() if len(o) == 1}
    verdicts = _judge(menu, sorted(scored), args.model)

    per_skill: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for phrase, want in sorted(scored.items()):
        got = verdicts.get(phrase, "<missing>")
        per_skill[want].append((phrase, want, got))

    lines = [
        f"# Fleet triggering eval — {len(menu)} skills, {len(scored)} phrases, judge {args.model}",
        "",
    ]
    total_ok = 0
    for owner in sorted(per_skill):
        rows = per_skill[owner]
        ok = sum(1 for _, w, g in rows if w == g)
        total_ok += ok
        lines.append(f"## {owner}: {ok}/{len(rows)}")
        for phrase, want, got in rows:
            mark = "PASS" if want == got else "FAIL"
            lines.append(
                f"- [{mark}] {phrase!r} -> {got}" + ("" if want == got else f"  (expected {want})")
            )
        lines.append("")
    lines.append(
        f"## selection_accuracy: {total_ok}/{len(scored)} = {total_ok / max(1, len(scored)):.3f}"
    )
    if conflicts:
        lines += [
            "",
            "## Conflicts (a phrase two skills both claim — fix the cases.yaml, not the judge)",
        ]
        lines += [f"- {p!r}: {o}" for p, o in sorted(conflicts.items())]
    report = "\n".join(lines)
    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
    return 0 if total_ok == len(scored) and not conflicts else 1


if __name__ == "__main__":
    sys.exit(main())
