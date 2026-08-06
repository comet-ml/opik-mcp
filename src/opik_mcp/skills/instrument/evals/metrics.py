"""Success metrics for the `/instrument` skill.

Aggregates a list of grader.CaseResult (plus optional triggering results and
runner-supplied cost data) into the metrics from OPIK-7800:

  selection_accuracy        - triggers only on appropriate requests
  verification_rate         - reached a `verified` trace
  instrumentation_correctness - all instrumentation asserts passed
  minimal_diff_rate         - only expected files changed
  double_instrumentation_rate - 0 target
  secret_mutation_rate      - 0 target  (config_untouched)
  no_prompt_migration_rate  - no Prompt Library changes during activation
  output_schema_compliance  - status is a valid state
  avg_tool_calls / avg_tokens - efficiency (from the runner, when available)
"""

from __future__ import annotations

from dataclasses import dataclass


def _rate(num: int, den: int) -> float:
    return round(num / den, 3) if den else 0.0


def _has(res, name) -> bool | None:
    return res.checks[name][0] if name in res.checks else None


def compute(results: list, triggering: dict | None = None,
            costs: list[dict] | None = None) -> dict:
    functional = [r for r in results]
    m: dict = {}

    # Triggering (needs an agent runner that reports which skill loaded).
    if triggering:
        st = triggering.get("should_trigger", {})   # {prompt: bool}
        sn = triggering.get("should_not_trigger", {})
        correct = sum(1 for v in st.values() if v) + sum(1 for v in sn.values() if not v)
        total = len(st) + len(sn)
        m["selection_accuracy"] = _rate(correct, total)

    # Verified traces (status recorded by the runner).
    verifiable = [r for r in functional if r.status_ok is not None]
    m["verification_rate"] = _rate(sum(1 for r in verifiable if r.status_ok), len(verifiable))

    # Instrumentation correctness: every instrumentation-shaped check passed.
    instr_checks = {"opik_imported", "entrypoint", "flush", "dep_added", "integration",
                    "undecorated_llm_call", "audit_only"}
    graded = [r for r in functional if r.checks]
    m["instrumentation_correctness"] = _rate(
        sum(1 for r in graded if all(ok for n, (ok, _) in r.checks.items() if n in instr_checks)),
        len(graded),
    )

    def rate_over(name, invert=False):
        rel = [r for r in functional if _has(r, name) is not None]
        good = sum(1 for r in rel if (_has(r, name) if not invert else not _has(r, name)))
        return _rate(good, len(rel))

    m["minimal_diff_rate"] = rate_over("minimal_diff")
    # 0-target rates are reported as the *violation* fraction.
    m["double_instrumentation_rate"] = round(1 - rate_over("no_double_wrap"), 3)
    m["secret_mutation_rate"] = round(1 - rate_over("config_untouched"), 3)
    m["no_prompt_migration_rate"] = rate_over("no_prompt_migration")

    # Output-schema compliance: the reported status is a known state.
    valid = {"verified", "blocked", "already_verified", "unsupported"}
    reported = [r for r in functional if getattr(r, "reported_status", None) is not None]
    if reported:
        m["output_schema_compliance"] = _rate(
            sum(1 for r in reported if r.reported_status in valid), len(reported))

    if costs:
        tool_calls = [c["tool_calls"] for c in costs if "tool_calls" in c]
        tokens = [c["tokens"] for c in costs if "tokens" in c]
        if tool_calls:
            m["avg_tool_calls"] = round(sum(tool_calls) / len(tool_calls), 1)
        if tokens:
            m["avg_tokens"] = round(sum(tokens) / len(tokens))

    m["cases_passed"] = f"{sum(1 for r in functional if r.passed)}/{len(functional)}"
    return m


def report(results: list, metrics: dict) -> str:
    lines = ["# /instrument eval report", ""]
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"[{mark}] {r.id} ({r.area})  status_ok={r.status_ok}")
        for name, (ok, detail) in r.checks.items():
            if not ok:
                lines.append(f"        - FAILED {name}: {detail}")
    lines.append("")
    lines.append("## Metrics")
    for k, v in metrics.items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)
