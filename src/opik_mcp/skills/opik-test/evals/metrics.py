# mypy: ignore-errors
"""Success metrics for the `/opik-test` skill (OPIK-7650).

selection_accuracy   - triggers only on appropriate requests
captured_rate        - reached the expected terminal status
trace_link_rate      - the item points at the emitted trace (source_trace_id)
assertion_bound_rate - one or two assertions, and they name the real failure
handoff_rate         - exactly one next step, pointing at compare
read_only_rate       - modified no code (target 1.0)
schema_compliance    - status is a valid state and carries item + assertions
"""

from __future__ import annotations


def _group_rate(results, pred) -> float | None:
    vals = [ok for r in results for n, (ok, _) in r.checks.items() if pred(n)]
    return round(sum(vals) / len(vals), 3) if vals else None


def compute(results: list, triggering: dict | None = None) -> dict:
    m: dict = {}

    if triggering:
        st = triggering.get("should_trigger", {})
        sn = triggering.get("should_not_trigger", {})
        correct = sum(1 for v in st.values() if v) + sum(1 for v in sn.values() if not v)
        total = len(st) + len(sn)
        m["selection_accuracy"] = round(correct / total, 3) if total else 0.0

    m["captured_rate"] = _group_rate(results, lambda n: n == "status")
    m["trace_link_rate"] = _group_rate(results, lambda n: n == "source_trace")
    m["assertion_bound_rate"] = _group_rate(
        results, lambda n: n in {"assertion_count", "assertion_names_failure"}
    )
    m["handoff_rate"] = _group_rate(results, lambda n: n in {"one_next_step", "next_step_handoff"})
    m["read_only_rate"] = _group_rate(results, lambda n: n == "no_modifications")
    m["schema_compliance"] = _group_rate(results, lambda n: n.startswith("schema"))
    m["cases_passed"] = f"{sum(1 for r in results if r.passed)}/{len(results)}"
    return m


def report(results: list, metrics: dict) -> str:
    lines = ["# /opik-test eval report", ""]
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"[{mark}] {r.id} ({r.area})")
        for name, (ok, detail) in r.checks.items():
            if not ok:
                lines.append(f"        - FAILED {name}: {detail}")
    lines += ["", "## Metrics"]
    for k, v in metrics.items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)
