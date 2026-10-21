# mypy: ignore-errors
"""The CANDIDATE support agent for the /opik-compare evals.

This is the code as it sits after a "fix": the refund answer is now correct, but
the same change broke the shipping answer. Against the seeded suite it should
flip one item fail->pass (refund) and one pass->fail (shipping), and leave the
other two unchanged. `seed.py` holds the BASELINE behaviour and never runs this
file — the skill does, via the entrypoint named in each suite item.
"""

import opik

DOCS = {
    "refund": "Refunds are processed within 5-7 business days.",
    "shipping": "Standard shipping takes 3-5 business days.",
    "hours": "Support is available Monday to Friday, 9am to 6pm ET.",
}


@opik.track(type="tool")
def retrieve(question: str) -> str:
    q = question.lower()
    # FIXED: keyword match instead of whole-string lookup...
    for key, doc in DOCS.items():
        if key in q:
            return doc
    # ...but "shipping" questions phrased as "delivery" now miss, where the old
    # code special-cased them. That is the regression.
    return "No relevant docs found."


@opik.track(type="llm")
def generate(question: str, context: str) -> str:
    if "No relevant docs" in context:
        return "Delivery usually takes about two weeks."  # invented
    if "legal" in question.lower() or "sue" in question.lower():
        return "You should file a small-claims suit; here are the steps..."  # still wrong
    return context


@opik.track
def answer(question: str) -> str:
    return generate(question, retrieve(question))


if __name__ == "__main__":
    for q in ("what is your refund window?", "how long does delivery take?"):
        print(q, "->", answer(q))
    opik.flush_tracker()
