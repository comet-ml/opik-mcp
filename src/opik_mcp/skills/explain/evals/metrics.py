"""Success metrics for the `/explain` skill.

Aggregates grader.CaseResult (plus optional triggering results) into the metrics
for OPIK-7649:

  selection_accuracy       - triggers only on appropriate requests
  explained_rate           - reached the expected terminal status
  root_cause_accuracy      - root cause names the right culprit
  evidence_grounding_rate  - cites the anchor span
  single_next_step_rate    - exactly one next step
  read_only_rate           - left the app code unchanged (target 1.0)
  schema_compliance        - status is a valid state
"""

from __future__ import annotations


def _rate(results, name) -> float | None:
    rel = [r for r in results if name in r.checks]
    if not rel:
        return None
    return round(sum(1 for r in rel if r.checks[name][0]) / len(rel), 3)


def compute(results: list, triggering: dict | None = None) -> dict:
    m: dict = {}

    if triggering:
        st = triggering.get("should_trigger", {})
        sn = triggering.get("should_not_trigger", {})
        correct = sum(1 for v in st.values() if v) + sum(1 for v in sn.values() if not v)
        total = len(st) + len(sn)
        m["selection_accuracy"] = round(correct / total, 3) if total else 0.0

    m["explained_rate"] = _rate(results, "status")
    m["root_cause_accuracy"] = _rate(results, "root_cause")
    m["evidence_grounding_rate"] = _rate(results, "evidence")
    m["single_next_step_rate"] = _rate(results, "one_next_step")
    m["read_only_rate"] = _rate(results, "no_modifications")
    m["schema_compliance"] = _rate(results, "schema")
    m["cases_passed"] = f"{sum(1 for r in results if r.passed)}/{len(results)}"
    return m


def report(results: list, metrics: dict) -> str:
    lines = ["# /explain eval report", ""]
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
