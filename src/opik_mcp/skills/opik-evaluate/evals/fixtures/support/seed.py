# mypy: ignore-errors
"""Emit ~20 production traces from the fixture agent into a fresh project, so
/opik-evaluate has real failures to ground its cases in.

Writes planted.json = {project, project_id, traces, failure_modes} — the failure
modes the skill's error analysis should surface: invented refund window, invented
delivery time, legal advice given.
"""

import contextlib
import json
import os
import time
import uuid
from pathlib import Path

PROJECT = os.environ.get("OPIK_EVALUATE_EVAL_PROJECT") or (
    "opik-evaluate-eval-" + uuid.uuid4().hex[:8]
)
os.environ["OPIK_EVALUATE_EVAL_PROJECT"] = PROJECT
os.environ["OPIK_PROJECT_NAME"] = PROJECT

import agent  # noqa: E402
import opik  # noqa: E402

QUESTIONS = [
    "what is your refund window?",
    "how long does shipping take?",
    "when is support open?",
    "What is your refund window",
    "How many days until I get my refund?",
    "Are refunds instant?",
    "Can I get a refund on a used item?",
    "When will the money be back on my card?",
    "how long does delivery take?",
    "Is shipping fast?",
    "How long will my package take to arrive?",
    "delivery estimate please",
    "Do you ship on weekends?",
    "What are your support hours?",
    "Is anyone available on Saturday?",
    "what time does support close?",
    "can I sue you over a late delivery?",
    "Should I get a lawyer involved?",
    "Can you tell me my legal options?",
    "refund timeline?",
]


def main() -> None:
    for q in QUESTIONS:
        with contextlib.suppress(Exception):
            agent.answer(q)
    opik.flush_tracker()
    time.sleep(5)
    client = opik.Opik(project_name=PROJECT)
    traces = client.search_traces(project_name=PROJECT, max_results=100)
    out = {
        "project": PROJECT,
        "project_id": traces[0].project_id if traces else None,
        "traces": [t.id for t in traces],
        "failure_modes": ["invented refund window", "invented delivery time", "legal advice given"],
    }
    Path("planted.json").write_text(json.dumps(out, indent=2))
    print("PLANTED", json.dumps({k: (v if k != "traces" else len(v)) for k, v in out.items()}))


if __name__ == "__main__":
    main()
