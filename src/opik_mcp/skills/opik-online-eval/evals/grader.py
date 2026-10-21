# mypy: ignore-errors
"""Deterministic grader for the `/opik-online-eval` skill.

Given a case's `assert` block, the fixture, the post-run workdir, planted.json
(from the seeder) and the agent's result.json (status / rule / score_name /
variables / verification / next_step), check that one rule was created with a
cost cap and a sampling rate, that variables are field paths, that `live` carries
a real verification trace, that `exists` points at the pre-created rule, and that
the workdir is unchanged.

No agent, no network at grade time — `verify` in run_evals.py is the one
networked step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

IGNORE = {
    ".venv",
    "__pycache__",
    "uv.lock",
    ".python-version",
    ".git",
    "result.json",
    "planted.json",
    "PROMPT.txt",
    "seed.py",
}
VALID_STATUS = {"live", "live_unverified", "exists", "blocked"}


@dataclass
class CaseResult:
    id: str
    area: str
    checks: dict[str, tuple[bool, str]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(ok for ok, _ in self.checks.values())


def _files(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if p.is_file() and not (set(rel.parts) & IGNORE):
            out[str(rel)] = p.read_bytes()
    return out


def _changed(fixture: Path, workdir: Path) -> set[str]:
    fo, wo = _files(fixture), _files(workdir)
    return {r for r in set(fo) | set(wo) if fo.get(r) != wo.get(r)}


def _rule(result: dict) -> dict:
    r = result.get("rule")
    return r if isinstance(r, dict) else {}


def grade_case(
    case: dict,
    fixture: Path,
    workdir: Path,
    result: dict | None,
    planted: dict | None,
    area: str = "functional",
) -> CaseResult:
    a = case.get("assert", {})
    result = result or {}
    planted = planted or {}
    rule = _rule(result)
    checks: dict[str, tuple[bool, str]] = {}

    def add(name, ok, detail=""):
        checks[name] = (bool(ok), detail)

    status = result.get("status")
    if "status" in a:
        add("status", status == a["status"], f"want {a['status']}, got {status}")

    if a.get("rule_named"):
        add(
            "rule_named",
            isinstance(rule.get("name"), str) and rule["name"].strip() != "",
            f"rule={rule!r}",
        )

    if a.get("rule_has_cost_cap"):
        cap = rule.get("max_cost_usd")
        add("cost_cap", isinstance(cap, (int, float)) and cap > 0, f"max_cost_usd={cap!r}")

    if a.get("rule_has_sampling_rate"):
        sr = rule.get("sampling_rate")
        add("sampling_rate", isinstance(sr, (int, float)) and 0 < sr <= 1, f"sampling_rate={sr!r}")

    if a.get("variables_are_field_paths"):
        vals = result.get("variables") or rule.get("variables") or {}
        bad = [v for v in (vals.values() if isinstance(vals, dict) else []) if "{{" in str(v)]
        add(
            "variables_field_paths",
            isinstance(vals, dict) and bool(vals) and not bad,
            f"variables={vals!r}",
        )

    if a.get("verification_trace_present"):
        ver = result.get("verification") or {}
        tid = ver.get("trace_id") if isinstance(ver, dict) else None
        planted_traces = set(planted.get("traces") or [])
        add(
            "verification_trace",
            isinstance(tid, str) and len(tid) >= 8,
            f"verification={ver!r}"
            + (" (a seeded trace, not fresh traffic)" if tid in planted_traces else ""),
        )

    if a.get("rule_matches_planted"):
        want = planted.get("existing_rule_id")
        add(
            "rule_matches_planted",
            bool(want) and str(rule.get("id")) == str(want),
            f"rule.id={rule.get('id')!r} planted={want!r}",
        )

    if a.get("one_next_step"):
        ns = result.get("next_step")
        ok = (isinstance(ns, str) and ns.strip() != "") or (isinstance(ns, list) and len(ns) == 1)
        add("one_next_step", ok, f"next_step={ns!r}")

    if a.get("no_modifications"):
        ch = _changed(fixture, workdir)
        add("no_modifications", not ch, f"changed: {sorted(ch)}")

    add("schema", status in VALID_STATUS, f"status {status!r} not in {sorted(VALID_STATUS)}")
    if status in {"live", "live_unverified"}:
        add(
            "schema_created",
            bool(rule.get("name")) and bool(result.get("score_name")),
            "created without rule + score_name",
        )
    return CaseResult(id=case["id"], area=area, checks=checks)
