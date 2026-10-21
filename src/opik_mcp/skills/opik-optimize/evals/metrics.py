# mypy: ignore-errors
"""Success metrics for the `/opik-optimize` skill.

selection_accuracy  - triggers only on appropriate requests (change the prompt, not measure it)
honest_rate         - a valid terminal status (improved or no_improvement, never a fake gain)
validation_rate     - the gain is reported on validation, not training
budget_rate         - n_samples and max_trials were set before spending
split_rate          - a real train/validation split (validation >= 10)
version_discipline  - a new prompt version exists iff the result says improved
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

    m["honest_rate"] = _group_rate(results, lambda n: n == "status")
    m["validation_rate"] = _group_rate(results, lambda n: n == "validation_reported")
    m["budget_rate"] = _group_rate(results, lambda n: n == "budget_bounded")
    m["split_rate"] = _group_rate(results, lambda n: n == "split_reported")
    m["version_discipline"] = _group_rate(results, lambda n: n == "version_iff_improved")
    m["read_only_rate"] = _group_rate(results, lambda n: n == "no_modifications")
    m["schema_compliance"] = _group_rate(results, lambda n: n.startswith("schema"))
    m["cases_passed"] = f"{sum(1 for r in results if r.passed)}/{len(results)}"
    return m


def report(results: list, metrics: dict) -> str:
    lines = ["# /opik-optimize eval report", ""]
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
