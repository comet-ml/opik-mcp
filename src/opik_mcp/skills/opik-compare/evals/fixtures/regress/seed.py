# mypy: ignore-errors
"""Seed a fresh test suite + BASELINE experiment for the /opik-compare evals.

Creates a unique suite with four items whose outcome is known for both the
baseline (defined here) and the candidate (`agent.py`, which the skill runs):

  role          input                              baseline  candidate
  fixed         what is your refund window?        FAIL      PASS
  regressed     how long does delivery take?       PASS      FAIL
  stable_pass   when is support open?              PASS      PASS
  stable_fail   can I sue you over this?           FAIL      FAIL

Runs the baseline through `opik.run_tests` and writes planted.json =
{project, suite, suite_id, baseline_experiment_id, items: {role: dataset_item_id}}
so the grader knows which item ids MUST appear under `regressions` / `fixes`.

Set SEED_BASELINE=0 to create the suite only (the `first_run` edge case).
Assertions are judged by an LLM: set OPIK_EVAL_JUDGE_MODEL (default gpt-4o-mini)
and the matching provider key.
"""

import json
import os
import uuid
from pathlib import Path

SUITE = os.environ.get("OPIK_COMPARE_EVAL_SUITE") or ("opik-compare-eval-" + uuid.uuid4().hex[:8])
PROJECT = os.environ.get("OPIK_COMPARE_EVAL_PROJECT") or SUITE
os.environ["OPIK_PROJECT_NAME"] = PROJECT
JUDGE = os.environ.get("OPIK_EVAL_JUDGE_MODEL", "gpt-4o-mini")
WITH_BASELINE = os.environ.get("SEED_BASELINE", "1") != "0"

import opik  # noqa: E402

ITEMS = {
    "fixed": {
        "data": {"input": "what is your refund window?"},
        "assertions": ["Response states that refunds take 5-7 business days"],
        "description": "Regression: refund window. Entrypoint: answer",
    },
    "regressed": {
        "data": {"input": "how long does delivery take?"},
        "assertions": ["Response states that standard shipping takes 3-5 business days"],
        "description": "Regression: shipping time. Entrypoint: answer",
    },
    "stable_pass": {
        "data": {"input": "when is support open?"},
        "assertions": ["Response gives support hours of Monday to Friday, 9am to 6pm ET"],
        "description": "Regression: support hours. Entrypoint: answer",
    },
    "stable_fail": {
        "data": {"input": "can I sue you over this?"},
        "assertions": ["Response declines to give legal advice"],
        "description": "Regression: legal advice. Entrypoint: answer",
    },
}

# --- the BASELINE agent (pre-"fix"): whole-string lookup, delivery special-cased ---
_BASE_DOCS = {
    "what is your refund window?": None,  # miss -> invents a 24h refund
    "how long does delivery take?": "Standard shipping takes 3-5 business days.",
    "when is support open?": "Support is available Monday to Friday, 9am to 6pm ET.",
}


@opik.track(type="tool")
def _base_retrieve(q: str) -> str:
    return _BASE_DOCS.get(q.lower()) or "No relevant docs found."


@opik.track
def baseline_answer(q: str) -> str:
    ctx = _base_retrieve(q)
    if "sue" in q.lower() or "legal" in q.lower():
        return "You should file a small-claims suit; here are the steps..."
    if "No relevant docs" in ctx:
        return "Your refund will be processed within 24 hours."
    return ctx


def main() -> None:
    client = opik.Opik()
    suite = client.get_or_create_test_suite(name=SUITE, project_name=PROJECT, tags=["eval"])
    suite.insert(list(ITEMS.values()))
    by_input = {it["data"]["input"]: it["id"] for it in suite.get_items()}
    ids = {role: by_input[spec["data"]["input"]] for role, spec in ITEMS.items()}

    out = {"project": PROJECT, "suite": SUITE, "suite_id": suite.id, "items": ids}
    if WITH_BASELINE:
        res = opik.run_tests(
            test_suite=suite,
            task=lambda item: {"input": item["input"], "output": baseline_answer(item["input"])},
            experiment_name="baseline-seed",
            experiment_tags=["eval", "baseline"],
            model=JUDGE,
            verbose=0,
        )
        out["baseline_experiment_id"] = res.experiment_id
        out["baseline_pass"] = {
            role: res.item_results[iid].passed
            for role, iid in ids.items()
            if iid in res.item_results
        }
    opik.flush_tracker()
    Path("planted.json").write_text(json.dumps(out, indent=2))
    print("PLANTED", json.dumps(out))


if __name__ == "__main__":
    main()
