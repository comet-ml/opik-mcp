# mypy: ignore-errors
"""Deterministic grader for the `/find` skill.

Given a case's `assert` block, the planted trace ids (planted.json, written by
the seeder), and the agent's result.json (status / shortlist / source /
next_step), check that the shortlist surfaces the attention-worthy traces,
excludes the normal ones, covers the expected signals, and modified no code.

No agent, no network at grade time.
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
}
VALID_STATUS = {"found", "empty", "blocked"}


@dataclass
class CaseResult:
    id: str
    area: str
    checks: dict[str, tuple[bool, str]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(ok for ok, _ in self.checks.values())


def _fixture_modified(fixture: Path, workdir: Path) -> list[str]:
    mod = []
    for p in fixture.rglob("*"):
        rel = p.relative_to(fixture)
        if p.is_file() and not (set(rel.parts) & IGNORE):
            w = workdir / rel
            if not w.exists() or w.read_bytes() != p.read_bytes():
                mod.append(str(rel))
    return mod


def _shortlist(result: dict) -> tuple[set[str], set[str]]:
    ids, signals = set(), set()
    for it in result.get("shortlist") or []:
        if isinstance(it, dict):
            if it.get("trace_id"):
                ids.add(str(it["trace_id"]))
            if it.get("signal"):
                signals.add(str(it["signal"]).lower())
        elif isinstance(it, str):
            ids.add(it)
    return ids, signals


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
    roles = (planted or {}).get("roles", {})
    ids_in, signals = _shortlist(result)
    checks: dict[str, tuple[bool, str]] = {}

    def add(name, ok, detail=""):
        checks[name] = (bool(ok), detail)

    status = result.get("status")
    if "status" in a:
        add("status", status == a["status"], f"want {a['status']}, got {status}")

    for role in a.get("include_roles", []):
        rid = roles.get(role)
        add(
            f"include:{role}", bool(rid) and rid in ids_in, f"planted {role}={rid} not in shortlist"
        )

    for role in a.get("exclude_roles", []):
        rids = roles.get(role) or []
        if isinstance(rids, str):
            rids = [rids]
        leaked = [r for r in rids if r in ids_in]
        add(f"exclude:{role}", not leaked, f"{role} ids leaked into shortlist: {leaked}")

    if a.get("signals_superset"):
        want = {s.lower() for s in a["signals_superset"]}
        add(
            "signals",
            want.issubset(signals),
            f"signals={sorted(signals)} missing {sorted(want - signals)}",
        )

    if a.get("source_valid"):
        add(
            "source_valid", result.get("source") in {"sdk", "mcp"}, f"source={result.get('source')}"
        )

    if a.get("one_next_step"):
        ns = result.get("next_step")
        ok = (isinstance(ns, str) and ns.strip() != "") or (isinstance(ns, list) and len(ns) == 1)
        add("one_next_step", ok, f"next_step={ns!r}")

    if a.get("read_only"):
        mod = _fixture_modified(fixture, workdir)
        add("read_only", not mod, f"fixture files modified: {mod}")

    add("schema", status in VALID_STATUS, f"status {status!r} not in {sorted(VALID_STATUS)}")
    return CaseResult(id=case["id"], area=area, checks=checks)
