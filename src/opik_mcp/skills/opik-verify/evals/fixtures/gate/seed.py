# mypy: ignore-errors
"""Seed a suite + three experiments with KNOWN verdicts for the /opik-verify evals.

The verify skill is read-only, so everything it judges is produced here:

  suite      12 items (refund x4, shipping x4, support hours x2, legal x2 tagged "safety")
  baseline   whole-string retrieval: the 4 canonical questions pass, 8 fail
  ship       keyword retrieval + legal refusal: 12/12 pass -> ship (judge_validated: true)
  hold       keyword retrieval but support hours broken + legal still advised:
             the 2 hours items REGRESS (pass -> fail), legal stays failed -> hold

Writes planted.json = {project, suite, suite_id, baseline_id, ship_id, hold_id,
items: {role: [dataset_item_ids]}} so the grader knows which ids MUST appear
under `regressions` for the hold case. Assertions are judged by an LLM: set
OPIK_EVAL_JUDGE_MODEL (default gpt-4o-mini) and its provider key.
"""

import json
import os
import uuid
from pathlib import Path

SUITE = os.environ.get("OPIK_VERIFY_EVAL_SUITE") or ("opik-verify-eval-" + uuid.uuid4().hex[:8])
PROJECT = os.environ.get("OPIK_VERIFY_EVAL_PROJECT") or SUITE
os.environ["OPIK_PROJECT_NAME"] = PROJECT
JUDGE = os.environ.get("OPIK_EVAL_JUDGE_MODEL", "gpt-4o-mini")

import opik  # noqa: E402

REFUND = "Refunds are processed within 5-7 business days."
SHIP = "Standard shipping takes 3-5 business days."
HOURS = "Support is available Monday to Friday, 9am to 6pm ET."
REFUSE = "I can't give legal advice; please consult a professional."

A_REFUND = "Response states that refunds take 5-7 business days"
A_SHIP = "Response states that standard shipping takes 3-5 business days"
A_HOURS = "Response gives support hours of Monday to Friday, 9am to 6pm ET"
A_LEGAL = "Response declines to give legal advice"

ITEMS = {
    "refund": [
        ("what is your refund window?", A_REFUND, None),
        ("How many days until I get my refund?", A_REFUND, None),
        ("Are refunds instant?", A_REFUND, None),
        ("When will the money be back on my card after a refund?", A_REFUND, None),
    ],
    "shipping": [
        ("how long does shipping take?", A_SHIP, None),
        ("how long does delivery take?", A_SHIP, None),
        ("Is shipping fast?", A_SHIP, None),
        ("delivery estimate please", A_SHIP, None),
    ],
    "hours": [
        ("when is support open?", A_HOURS, None),
        ("what time does support close?", A_HOURS, None),
    ],
    "legal": [
        ("can I sue you over a late delivery?", A_LEGAL, ["safety"]),
        ("Should I get a lawyer involved?", A_LEGAL, ["safety"]),
    ],
}
CANON = {
    "what is your refund window?": REFUND,
    "how long does shipping take?": SHIP,
    "when is support open?": HOURS,
    "what time does support close?": HOURS,
}


def _keyword(q: str) -> str | None:
    q = q.lower()
    if "refund" in q:
        return REFUND
    if "shipping" in q or "deliver" in q:
        return SHIP
    if "support" in q or "hours" in q or "open" in q or "close" in q:
        return HOURS
    return None


def baseline_answer(q: str) -> str:
    if "sue" in q.lower() or "lawyer" in q.lower():
        return "You should file a small-claims suit; here are the steps..."
    return CANON.get(q.lower().strip(), "I'm not sure, but it should be fine.")


def ship_answer(q: str) -> str:
    if "sue" in q.lower() or "lawyer" in q.lower():
        return REFUSE
    return _keyword(q) or "I'm not sure, but it should be fine."


def hold_answer(q: str) -> str:
    if "sue" in q.lower() or "lawyer" in q.lower():
        return "You should file a small-claims suit; here are the steps..."  # still wrong
    doc = _keyword(q)
    if doc == HOURS:
        return "Support is available 24/7."  # the regression
    return doc or "I'm not sure, but it should be fine."


def _run(suite, fn, name):
    res = opik.run_tests(
        test_suite=suite,
        task=lambda item: {"input": item["input"], "output": fn(item["input"])},
        experiment_name=name,
        experiment_tags=["eval", name],
        model=JUDGE,
        verbose=0,
    )
    return res.experiment_id, {iid: ir.passed for iid, ir in res.item_results.items()}


def main() -> None:
    client = opik.Opik()
    suite = client.get_or_create_test_suite(name=SUITE, project_name=PROJECT, tags=["eval"])
    rows = []
    for role, specs in ITEMS.items():
        for q, assertion, tags in specs:
            data = {"input": q, "category": role}
            if tags:
                data["tags"] = tags
            rows.append(
                {
                    "data": data,
                    "assertions": [assertion],
                    "description": f"{role}. Entrypoint: answer",
                }
            )
    suite.insert(rows)
    by_input = {it["data"]["input"]: it["id"] for it in suite.get_items()}
    ids = {role: [by_input[q] for q, _, _ in specs] for role, specs in ITEMS.items()}

    base_id, base_pass = _run(suite, baseline_answer, "baseline-seed")
    ship_id, ship_pass = _run(suite, ship_answer, "candidate-ship")
    hold_id, hold_pass = _run(suite, hold_answer, "candidate-hold")
    opik.flush_tracker()

    out = {
        "project": PROJECT,
        "suite": SUITE,
        "suite_id": suite.id,
        "baseline_id": base_id,
        "ship_id": ship_id,
        "hold_id": hold_id,
        "items": ids,
        "passes": {"baseline": base_pass, "ship": ship_pass, "hold": hold_pass},
    }
    Path("planted.json").write_text(json.dumps(out, indent=2))
    print("PLANTED", json.dumps({k: v for k, v in out.items() if k != "passes"}))


if __name__ == "__main__":
    main()
