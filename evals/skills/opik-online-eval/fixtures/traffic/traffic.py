# mypy: ignore-errors
"""Send three more traced requests to the seeded project (for the skill's verify step).

OPIK_PROJECT_NAME=<project> uv run python traffic.py
"""

import os
import sys

import opik

ANSWERS = {
    "What is your refund window": "Your refund will be processed within 24 hours.",  # wrong
    "when is support open?": "Support is available Monday to Friday, 9am to 6pm ET.",
    "Can I get a refund on a used item?": "Refunds are processed within 5-7 business days.",
}


@opik.track
def answer(question: str) -> str:
    return ANSWERS.get(question, "I'm not sure, but it should be fine.")


if __name__ == "__main__":
    if not os.environ.get("OPIK_PROJECT_NAME"):
        sys.exit("set OPIK_PROJECT_NAME to the seeded project (see planted.json)")
    for q in ANSWERS:
        answer(q)
    opik.flush_tracker()
    print("sent", len(ANSWERS), "traces to", os.environ["OPIK_PROJECT_NAME"])
