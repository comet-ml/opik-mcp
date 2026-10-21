# mypy: ignore-errors
"""A tiny, already-instrumented support bot with KNOWN failure modes — the app under
evaluation for the /opik-evaluate evals.

  bug 1  retrieve() looks the WHOLE question up as the key, so paraphrases miss and
         generate() invents a policy (refunds "within 24 hours", delivery "two weeks").
  bug 2  legal questions get legal advice instead of a refusal.

Entrypoint: answer(question) -> str. Deterministic, no LLM calls, so the skill can
run it safely as the evaluation task.
"""

import os

os.environ.setdefault(
    "OPIK_PROJECT_NAME", os.environ.get("OPIK_EVALUATE_EVAL_PROJECT", "opik-evaluate-eval")
)

import opik

DOCS = {
    "what is your refund window?": "Refunds are processed within 5-7 business days.",
    "how long does shipping take?": "Standard shipping takes 3-5 business days.",
    "when is support open?": "Support is available Monday to Friday, 9am to 6pm ET.",
}


@opik.track(type="tool")
def retrieve(question: str) -> str:
    return DOCS.get(question.strip().lower(), "No relevant docs found.")


@opik.track(type="llm")
def generate(question: str, context: str) -> str:
    q = question.lower()
    if "sue" in q or "legal" in q or "lawyer" in q:
        return "You should file a small-claims suit; here are the steps: 1) gather receipts..."
    if "No relevant docs" in context:
        if "refund" in q:
            return "Your refund will be processed within 24 hours."
        if "deliver" in q or "shipping" in q:
            return "Delivery usually takes about two weeks."
        return "I'm not sure, but it should be fine."
    return context


@opik.track
def answer(question: str) -> str:
    return generate(question, retrieve(question))


if __name__ == "__main__":
    import sys

    print(answer(" ".join(sys.argv[1:]) or "what is your refund window?"))
    opik.flush_tracker()
