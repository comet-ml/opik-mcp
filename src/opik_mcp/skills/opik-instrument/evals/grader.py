# mypy: ignore-errors
"""Deterministic grader for the `/opik-instrument` skill.

Given a case's `assert` block, the fixture ground truth (expected.json), and the
agent's result.json, check that the skill instrumented, ran, and *verified* a
real, complete trace — not just that it edited code or that "a trace arrived".

result.json contract (the agent writes this after running the skill):

    {
      "status": "verified" | "blocked" | "already_verified" | "unsupported",
      "trace_id": "...", "trace_url": "...",
      "changes": ["added opik to pyproject", ...],
      "next_step": "...",                       # required when blocked
      "coverage": {
        "expected_sites": 3,
        "spans_found": 3,
        "spans": [{"name": "run", "type": "general"}, ...]
      }
    }

Grading is offline. An OPTIONAL integrity check re-reads the trace from Opik and
compares the reported span count to reality — it runs only when `trace_id` is
present and Opik is configured, and is skipped (not failed) otherwise.
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
    "expected.json",
    "PROMPT.txt",
}
VALID_STATUS = {"verified", "blocked", "already_verified", "unsupported"}
SUCCESS_STATUS = {"verified", "already_verified"}


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
    # new files the agent added (e.g. .env, config) also count as instrumentation work
    for p in workdir.rglob("*"):
        rel = p.relative_to(workdir)
        if p.is_file() and not (set(rel.parts) & IGNORE) and not (fixture / rel).exists():
            mod.append(str(rel))
    return sorted(set(mod))


def _spans(result: dict) -> list[dict]:
    cov = result.get("coverage") or {}
    return [s for s in (cov.get("spans") or []) if isinstance(s, dict)]


def _online_span_count(trace_id: str) -> int | None:
    """Best-effort: real span count from Opik. None if unavailable (never fails)."""
    try:
        import opik

        client = opik.Opik()
        return len(client.search_spans(trace_id=trace_id))
    except Exception:
        return None


def grade_case(
    case: dict,
    fixture: Path,
    workdir: Path,
    result: dict | None,
    expected: dict | None,
    area: str = "functional",
) -> CaseResult:
    a = case.get("assert", {})
    result = result or {}
    expected = expected or {}
    exp_sites = int(expected.get("expected_sites", 0) or 0)
    status = result.get("status")
    cov = result.get("coverage") or {}
    spans = _spans(result)
    checks: dict[str, tuple[bool, str]] = {}

    def add(name, ok, detail=""):
        checks[name] = (bool(ok), detail)

    # --- terminal state -------------------------------------------------
    if "status" in a:
        add("status", status == a["status"], f"want {a['status']}, got {status}")

    if a.get("no_false_success"):
        if status in SUCCESS_STATUS:
            found = cov.get("spans_found")
            ok = (
                bool(result.get("trace_id"))
                and isinstance(found, int)
                and exp_sites
                and found >= exp_sites
            )
            add(
                "no_false_success",
                ok,
                f"claimed {status} but trace_id={result.get('trace_id')!r} "
                f"coverage={found}/{exp_sites}",
            )
        else:
            add("no_false_success", True, f"did not claim success (status={status})")

    # --- coverage reporting (the new ability) ---------------------------
    if a.get("coverage_reported"):
        found = cov.get("spans_found")
        add(
            "coverage_reported",
            isinstance(found, int) and bool(spans),
            f"coverage={cov!r}",
        )

    if a.get("cover_types"):
        want = {t.lower() for t in a["cover_types"]}
        got = {str(s.get("type", "")).lower() for s in spans}
        add("cover_types", want.issubset(got), f"types={sorted(got)} missing {sorted(want - got)}")

    if a.get("spans_well_formed"):
        bad = [s for s in spans if not str(s.get("name", "")).strip() or not str(s.get("type", "")).strip()]
        add("spans_well_formed", not bad, f"malformed spans (empty name/type): {bad}")

    # --- instrumentation actually happened ------------------------------
    if a.get("modified_code"):
        mod = _fixture_modified(fixture, workdir)
        add("modified_code", bool(mod), "no fixture files were changed")

    if a.get("next_step_contains"):
        ns = str(result.get("next_step") or "").lower()
        missing = [s for s in a["next_step_contains"] if s.lower() not in ns]
        add("next_step_contains", not missing, f"next_step={result.get('next_step')!r} missing {missing}")

    # --- optional online integrity (skipped if unavailable) -------------
    tid = result.get("trace_id")
    if tid and cov.get("spans_found") is not None:
        real = _online_span_count(str(tid))
        if real is not None:
            add(
                "integrity",
                real >= exp_sites if status in SUCCESS_STATUS else True,
                f"opik reports {real} spans for {tid}, expected >= {exp_sites}",
            )

    add("schema", status in VALID_STATUS, f"status {status!r} not in {sorted(VALID_STATUS)}")
    return CaseResult(id=case["id"], area=area, checks=checks)
