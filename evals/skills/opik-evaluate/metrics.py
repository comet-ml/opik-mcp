# mypy: ignore-errors
"""Success metrics for the `/opik-evaluate` skill (OPIK-7646).

selection_accuracy  - triggers only on appropriate requests
evaluated_rate      - reached the expected terminal status (an experiment with scores)
grounding_rate      - cases came from the production traces, enough of them
actionable_rate     - worst items named with reasons that hit the planted failure modes
judge_discipline    - every judge / assertion names exactly one failure mode
read_only_rate      - app code unchanged (target 1.0)
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

    m["evaluated_rate"] = _group_rate(
        results, lambda n: n in {"status", "experiment_url", "scores"}
    )
    m["grounding_rate"] = _group_rate(results, lambda n: n in {"cases_source", "cases_count"})
    m["actionable_rate"] = _group_rate(results, lambda n: n in {"worst_named", "hits_planted"})
    m["judge_discipline"] = _group_rate(results, lambda n: n == "judge_failure_modes")
    m["read_only_rate"] = _group_rate(results, lambda n: n == "no_modifications")
    m["schema_compliance"] = _group_rate(results, lambda n: n.startswith("schema"))
    m["cases_passed"] = f"{sum(1 for r in results if r.passed)}/{len(results)}"
    return m


def report(results: list, metrics: dict) -> str:
    lines = ["# /opik-evaluate eval report", ""]
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
