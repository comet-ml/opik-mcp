# mypy: ignore-errors
"""Success metrics for the `/opik-verify` skill.

selection_accuracy   - triggers only on appropriate requests (the decision, not the numbers)
verdict_accuracy     - returned the planted verdict
criteria_completeness - every policy criterion appears in the table
regression_recall    - every planted regression named by item id
regression_precision - no stable item named as a regression
arithmetic_shown     - subgroups / safety report the per-item arithmetic, not just "passed"
gate_integrity       - ship needs every gate passed + a validated judge; hold needs a failed gate
read_only_rate       - modified nothing in the workdir (target 1.0)
schema_compliance    - status is a valid state
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

    m["verdict_accuracy"] = _group_rate(results, lambda n: n == "status")
    m["criteria_completeness"] = _group_rate(results, lambda n: n == "criteria_complete")
    m["regression_recall"] = _group_rate(results, lambda n: n.startswith("regression:"))
    m["arithmetic_shown"] = _group_rate(
        results, lambda n: n.startswith("subgroup:") or n.startswith("safety_flag:")
    )
    m["regression_precision"] = _group_rate(
        results, lambda n: n.startswith("stable:") or n == "no_regressions"
    )
    m["gate_integrity"] = _group_rate(results, lambda n: n in {"schema_ship", "schema_hold"})
    m["read_only_rate"] = _group_rate(results, lambda n: n == "no_modifications")
    m["schema_compliance"] = _group_rate(results, lambda n: n == "schema")
    m["cases_passed"] = f"{sum(1 for r in results if r.passed)}/{len(results)}"
    return m


def report(results: list, metrics: dict) -> str:
    lines = ["# /opik-verify eval report", ""]
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
