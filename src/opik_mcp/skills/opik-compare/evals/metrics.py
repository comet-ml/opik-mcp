# mypy: ignore-errors
"""Success metrics for the `/opik-compare` skill (OPIK-7651 / OPIK-8400).

selection_accuracy  - triggers only on appropriate requests
compared_rate       - reached the expected terminal status
flip_recall         - the planted regression and fix were both named (by item id)
flip_precision      - no stable item leaked into regressions/fixes
link_rate           - compare_url carries both experiment ids
no_verdict_rate     - never issues ship/hold (target 1.0)
read_only_rate      - modified no code (target 1.0)
schema_compliance   - status is a valid state
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

    m["compared_rate"] = _group_rate(results, lambda n: n == "status")
    m["flip_recall"] = _group_rate(
        results, lambda n: n.startswith("regression:") or n.startswith("fix:")
    )
    m["flip_precision"] = _group_rate(results, lambda n: n.startswith("stable:"))
    m["link_rate"] = _group_rate(results, lambda n: n == "compare_url")
    m["no_verdict_rate"] = _group_rate(results, lambda n: n == "no_verdict")
    m["read_only_rate"] = _group_rate(results, lambda n: n == "no_modifications")
    m["schema_compliance"] = _group_rate(results, lambda n: n.startswith("schema"))
    m["cases_passed"] = f"{sum(1 for r in results if r.passed)}/{len(results)}"
    return m


def report(results: list, metrics: dict) -> str:
    lines = ["# /opik-compare eval report", ""]
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
