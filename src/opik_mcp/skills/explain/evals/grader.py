# mypy: ignore-errors
"""Deterministic grader for the `/explain` skill.

Given a case's `assert` block, the original fixture, the post-run workdir, and
the agent's `result.json` (status / root_cause / evidence / next_step /
reasoner), check the explanation is grounded to the right culprit, cites the
right span, gives exactly one next step, uses a valid reasoner, and left the app
code unchanged.

Root-cause text is matched by keyword (the explanation is natural language);
everything else is exact. No agent, no network — fast and repeatable.
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
    "trace_id.txt",
}
VALID_STATUS = {"explained", "blocked", "not_found"}


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


def _evidence_text(result: dict) -> str:
    ev = result.get("evidence")
    if isinstance(ev, dict):
        ev = ev.get("spans", [])
    names: list[str] = []
    if isinstance(ev, list):
        for e in ev:
            if isinstance(e, dict):
                names.append(f"{e.get('name', '')} {e.get('id', '')} {e.get('type', '')}")
            else:
                names.append(str(e))
    elif isinstance(ev, str):
        names.append(ev)
    return " ".join(names).lower()


def grade_case(
    case: dict, fixture: Path, workdir: Path, result: dict | None, area: str = "functional"
) -> CaseResult:
    a = case.get("assert", {})
    result = result or {}
    checks: dict[str, tuple[bool, str]] = {}

    def add(name, ok, detail=""):
        checks[name] = (bool(ok), detail)

    status = result.get("status")

    if "status" in a:
        add("status", status == a["status"], f"want {a['status']}, got {status}")

    if a.get("root_cause_any"):
        rc = str(result.get("root_cause", "")).lower()
        hit = [k for k in a["root_cause_any"] if k.lower() in rc]
        add(
            "root_cause",
            bool(hit),
            f"matched {hit}" if hit else f"none of {a['root_cause_any']} in root_cause",
        )

    if a.get("evidence_span_any"):
        text = _evidence_text(result)
        hit = [k for k in a["evidence_span_any"] if k.lower() in text]
        add(
            "evidence",
            bool(hit),
            f"matched {hit}" if hit else f"none of {a['evidence_span_any']} in evidence",
        )

    if a.get("one_next_step"):
        ns = result.get("next_step")
        ok = (isinstance(ns, str) and ns.strip() != "") or (isinstance(ns, list) and len(ns) == 1)
        add("one_next_step", ok, f"next_step={ns!r}")

    if a.get("reasoner_valid"):
        add(
            "reasoner_valid",
            result.get("reasoner") in {"agent", "ollie"},
            f"reasoner={result.get('reasoner')}",
        )

    if a.get("no_modifications"):
        ch = _changed(fixture, workdir)
        add("no_modifications", not ch, f"changed: {sorted(ch)}")

    add("schema", status in VALID_STATUS, f"status {status!r} not in {sorted(VALID_STATUS)}")
    return CaseResult(id=case["id"], area=area, checks=checks)
