# mypy: ignore-errors
"""Success metrics for the `/find` skill (OPIK-7648).

selection_accuracy - triggers only on appropriate requests
found_rate         - reached the expected terminal status
recall             - every attention-worthy planted trace surfaced (include:*)
precision          - no normal trace leaked into the shortlist (exclude:*)
signal_coverage    - shortlist covered the expected signals
read_only_rate     - modified no code (target 1.0)
schema_compliance  - status is a valid state
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

    m["found_rate"] = _group_rate(results, lambda n: n == "status")
    m["recall"] = _group_rate(results, lambda n: n.startswith("include:"))
    m["precision"] = _group_rate(results, lambda n: n.startswith("exclude:"))
    m["signal_coverage"] = _group_rate(results, lambda n: n == "signals")
    m["read_only_rate"] = _group_rate(results, lambda n: n == "read_only")
    m["schema_compliance"] = _group_rate(results, lambda n: n == "schema")
    m["cases_passed"] = f"{sum(1 for r in results if r.passed)}/{len(results)}"
    return m


def report(results: list, metrics: dict) -> str:
    lines = ["# /find eval report", ""]
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
