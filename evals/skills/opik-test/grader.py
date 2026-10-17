# mypy: ignore-errors
"""Deterministic grader for the `/opik-test` skill.

Given a case's `assert` block, the original fixture, the post-run workdir, the
emitted trace id, and the agent's result.json (status / suite / item / source /
next_step), check that the captured item points at the right trace, carries a
bounded number of assertions that name the real failure, stores the input
verbatim, hands off to compare, and left the app code unchanged.

No agent, no network at grade time — `verify` in run_evals.py is the one
networked step, and it is separate.
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
    "PROMPT.txt",
}
VALID_STATUS = {"captured", "exists", "blocked"}
#: The question the toolbug fixture asks — the item's input must carry it verbatim.
FIXTURE_INPUT = "what is your refund window?"


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


def _item(result: dict) -> dict:
    item = result.get("item")
    return item if isinstance(item, dict) else {}


def _assertions(result: dict) -> list[str]:
    a = _item(result).get("assertions")
    if isinstance(a, list):
        return [str(x) for x in a]
    if isinstance(a, str) and a.strip():
        return [a]
    return []


def grade_case(
    case: dict,
    fixture: Path,
    workdir: Path,
    result: dict | None,
    trace_id: str | None,
    area: str = "functional",
) -> CaseResult:
    a = case.get("assert", {})
    result = result or {}
    item = _item(result)
    assertions = _assertions(result)
    checks: dict[str, tuple[bool, str]] = {}

    def add(name, ok, detail=""):
        checks[name] = (bool(ok), detail)

    status = result.get("status")
    if "status" in a:
        add("status", status == a["status"], f"want {a['status']}, got {status}")

    if a.get("source_trace_matches"):
        got = str(item.get("source_trace_id") or "")
        add(
            "source_trace",
            bool(trace_id) and got == trace_id,
            f"item.source_trace_id={got!r} emitted={trace_id!r}",
        )

    if a.get("assertions_between"):
        lo, hi = a["assertions_between"]
        n = len(assertions)
        add("assertion_count", lo <= n <= hi, f"{n} assertions, want {lo}..{hi}")

    if a.get("assertion_any"):
        text = " ".join(assertions).lower()
        hit = [k for k in a["assertion_any"] if str(k).lower() in text]
        add(
            "assertion_names_failure",
            bool(hit),
            f"matched {hit}" if hit else f"none of {a['assertion_any']} in {assertions}",
        )

    if a.get("suite_named"):
        suite = result.get("suite")
        name = suite.get("name") if isinstance(suite, dict) else None
        add("suite_named", isinstance(name, str) and name.strip() != "", f"suite={suite!r}")

    if a.get("input_verbatim"):
        stored = item.get("input")
        stored_text = stored if isinstance(stored, str) else str(stored)
        add(
            "input_verbatim",
            FIXTURE_INPUT in stored_text.lower(),
            f"item.input={stored_text[:120]!r} lacks {FIXTURE_INPUT!r}",
        )

    if a.get("one_next_step"):
        ns = result.get("next_step")
        ok = (isinstance(ns, str) and ns.strip() != "") or (isinstance(ns, list) and len(ns) == 1)
        add("one_next_step", ok, f"next_step={ns!r}")

    if a.get("next_step_mentions"):
        ns = result.get("next_step")
        text = (ns if isinstance(ns, str) else " ".join(map(str, ns or []))).lower()
        hit = [k for k in a["next_step_mentions"] if str(k).lower() in text]
        add("next_step_handoff", bool(hit), f"next_step={text!r} lacks {a['next_step_mentions']}")

    if a.get("no_modifications"):
        ch = _changed(fixture, workdir)
        add("no_modifications", not ch, f"changed: {sorted(ch)}")

    add("schema", status in VALID_STATUS, f"status {status!r} not in {sorted(VALID_STATUS)}")
    if status in {"captured", "exists"}:
        add(
            "schema_item",
            bool(item) and bool(assertions),
            "captured/exists without item+assertions",
        )
    return CaseResult(id=case["id"], area=area, checks=checks)
