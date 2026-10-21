# mypy: ignore-errors
"""Success metrics for the `/opik-online-eval` skill.

selection_accuracy  - triggers only on appropriate requests (live scoring, not offline eval)
live_rate           - reached the expected terminal status
guardrail_rate      - created rules carry a cost cap and a sampling rate
mapping_rate        - variables are field paths, never template syntax
verified_rate       - `live` carries a real verification trace
dedupe_rate         - `exists` points at the pre-created rule and creates nothing
read_only_rate      - modified nothing in the workdir (target 1.0)
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

    m["live_rate"] = _group_rate(results, lambda n: n == "status")
    m["guardrail_rate"] = _group_rate(results, lambda n: n in {"cost_cap", "sampling_rate"})
    m["mapping_rate"] = _group_rate(results, lambda n: n == "variables_field_paths")
    m["verified_rate"] = _group_rate(results, lambda n: n == "verification_trace")
    m["dedupe_rate"] = _group_rate(results, lambda n: n == "rule_matches_planted")
    m["read_only_rate"] = _group_rate(results, lambda n: n == "no_modifications")
    m["schema_compliance"] = _group_rate(results, lambda n: n.startswith("schema"))
    m["cases_passed"] = f"{sum(1 for r in results if r.passed)}/{len(results)}"
    return m


def report(results: list, metrics: dict) -> str:
    lines = ["# /opik-online-eval eval report", ""]
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
