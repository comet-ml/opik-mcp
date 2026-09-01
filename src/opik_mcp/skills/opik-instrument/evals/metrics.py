# mypy: ignore-errors
"""Success metrics for the `/opik-instrument` skill.

selection_accuracy      - triggers on "add tracing", not on diagnose/explain/evaluate
verify_correctness      - reached the correct terminal state for the fixture
no_false_success_rate   - never claimed success without a real, complete trace (target 1.0)
coverage_reported_rate  - reported span-level coverage, not just "a trace arrived"
type_coverage           - the expected span types (general/llm/tool) were all present
well_formed_rate        - every reported span had a non-empty name and type
instrumented_rate       - actually changed code (instrument modifies; target 1.0)
integrity_rate          - reported coverage matched the real trace in Opik (when checkable)
schema_compliance       - status is a valid terminal state
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

    m["verify_correctness"] = _group_rate(results, lambda n: n in ("status", "no_false_success"))
    m["no_false_success_rate"] = _group_rate(results, lambda n: n == "no_false_success")
    m["coverage_reported_rate"] = _group_rate(results, lambda n: n == "coverage_reported")
    m["type_coverage"] = _group_rate(results, lambda n: n == "cover_types")
    m["well_formed_rate"] = _group_rate(results, lambda n: n == "spans_well_formed")
    m["instrumented_rate"] = _group_rate(results, lambda n: n == "modified_code")
    m["integrity_rate"] = _group_rate(results, lambda n: n == "integrity")
    m["schema_compliance"] = _group_rate(results, lambda n: n == "schema")
    m["cases_passed"] = f"{sum(1 for r in results if r.passed)}/{len(results)}"
    return m


def report(results: list, metrics: dict) -> str:
    lines = ["# /opik-instrument eval report", ""]
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
